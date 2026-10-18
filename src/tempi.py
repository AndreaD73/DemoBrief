# -*- coding: utf-8 -*-
"""Lightweight block profiler for DemoBrief.

Pipeline steps wrap their work in `blocco(name)` contexts; this module accumulates wall-clock
seconds, CPU seconds, call counts and decoded frames per block, and appends them as JSON lines
to <work folder>/tempi.jsonl when a step is flushed or closed. It can also wrap cv2 (video
reads, resize, colour conversion), RapidOCR and file writes so they are measured transparently.
"""
import atexit
import builtins
import json
import os
import threading
import time
import tracemalloc
from contextlib import ContextDecorator

BYTES_PER_MB = 1048576.0
ROUND_DIGITS = 6
COMPLETE_READ_FRACTION = 0.9

_lock = threading.RLock()
_dati = {}
_fase = None
_lavoro = None
_video = None
_radice = None
_chiuso = False
_registrato = False
_cv2_originale = None
_catture = []
_open_originale = builtins.open
_open_attivo = False
_overhead = 0.0
_psutil = None

try:
    import psutil as _psutil
except ImportError:
    tracemalloc.start()


def _aggiungi(nome, secondi=0.0, cpu_secondi=0.0, chiamate=1, fotogrammi=0):
    """Add one measurement to the accumulator of block `nome`.

    The time spent in this bookkeeping is itself added to the global `_overhead`, which is
    reported at close time as the block 'overhead_misure'.
    """
    global _overhead
    inizio = time.perf_counter()
    with _lock:
        dati_blocco = _dati.setdefault(
            nome, {'secondi': 0.0, 'cpu_secondi': 0.0, 'chiamate': 0, 'fotogrammi': 0})
        dati_blocco['secondi'] += float(secondi)
        dati_blocco['cpu_secondi'] += float(cpu_secondi)
        dati_blocco['chiamate'] += int(chiamate)
        dati_blocco['fotogrammi'] += int(fotogrammi)
    _overhead += time.perf_counter() - inizio


class _Blocco(ContextDecorator):
    """Context manager / decorator that times its body and books it under a block name."""

    def __init__(self, nome):
        """Remember the block name; the clocks are read on entering."""
        self.nome = nome
        self.inizio = None
        self.inizio_cpu = None

    def __enter__(self):
        """Start the wall-clock and CPU timers."""
        self.inizio = time.perf_counter()
        self.inizio_cpu = time.process_time()
        return self

    def __exit__(self, tipo, valore, traccia):
        """Book the elapsed wall-clock and CPU seconds; exceptions are never swallowed."""
        _aggiungi(
            self.nome,
            time.perf_counter() - self.inizio,
            cpu_secondi=time.process_time() - self.inizio_cpu,
        )
        return False


def blocco(nome):
    """Return a timing context (also usable as decorator) that books its time under `nome`."""
    return _Blocco(nome)


def conta(nome, n=1):
    """Count `n` occurrences of event `nome` without measuring any time."""
    _aggiungi(nome, chiamate=n)


def fotogrammi(nome='fotogrammi_letti', n=1):
    """Add `n` decoded video frames to the block `nome` (the call count is left untouched)."""
    _aggiungi(nome, chiamate=0, fotogrammi=n)


def ocr(richieste=0, cache=0, rapidocr=0):
    """Record the OCR budget of the current step: requests, cache hits, real RapidOCR calls."""
    conta('ocr_richieste', richieste)
    conta('ocr_risolte_cache', cache)
    conta('ocr_chiamate_rapidocr', rapidocr)


def scarica():
    """Append the blocks gathered so far to tempi.jsonl and empty the in-memory accumulator.

    Does nothing before `configura` has run or when nothing was measured.
    """
    if _fase is None or _lavoro is None:
        return
    with _lock:
        if not _dati:
            return
        dati = {
            nome: {
                'secondi': valore['secondi'],
                'cpu_secondi': valore['cpu_secondi'],
                'chiamate': valore['chiamate'],
                'fotogrammi': valore['fotogrammi'],
            }
            for nome, valore in _dati.items()
        }
        _dati.clear()
    ram = _ram_picco_mb()
    percorso = os.path.join(_lavoro, 'tempi.jsonl')
    with _open_originale(percorso, 'a', encoding='utf-8', newline='\n') as file:
        for nome in sorted(dati):
            valore = dati[nome]
            chiamate = valore['chiamate']
            per_chiamata = round(valore['secondi'] / chiamate, ROUND_DIGITS) if chiamate else 0.0
            riga = {
                'fase': _fase,
                'blocco': nome,
                'secondi_totali': round(valore['secondi'], ROUND_DIGITS),
                'cpu_secondi': round(valore['cpu_secondi'], ROUND_DIGITS),
                'numero_chiamate': chiamate,
                'secondi_per_chiamata': per_chiamata,
                'ram_picco_mb': ram,
                'fotogrammi_letti': valore['fotogrammi'],
            }
            file.write(json.dumps(riga, ensure_ascii=False, sort_keys=True) + '\n')


def _ram_picco_mb():
    """Return the peak memory of this process in MB (psutil peak working set, else tracemalloc)."""
    if _psutil is not None:
        info = _psutil.Process(os.getpid()).memory_info()
        valore = getattr(info, 'peak_wset', info.rss)
        return round(valore / BYTES_PER_MB, 3)
    return round(tracemalloc.get_traced_memory()[1] / BYTES_PER_MB, 3)


class _FileMisurato:
    """Proxy around a real file object that times every write under the block 'scrittura_file'."""

    def __init__(self, file_reale):
        """Wrap the already opened file object `file_reale`."""
        self._file = file_reale

    def write(self, valore):
        """Write `valore` to the wrapped file, timed; returns the wrapped file's result."""
        with blocco('scrittura_file'):
            return self._file.write(valore)

    def writelines(self, valori):
        """Write the iterable `valori` to the wrapped file, timed."""
        with blocco('scrittura_file'):
            return self._file.writelines(valori)

    def __enter__(self):
        """Enter the wrapped file's context and return this proxy."""
        self._file.__enter__()
        return self

    def __exit__(self, tipo, valore, traccia):
        """Leave the wrapped file's context (closes it)."""
        return self._file.__exit__(tipo, valore, traccia)

    def __iter__(self):
        """Iterate over the wrapped file's lines."""
        return iter(self._file)

    def __getattr__(self, nome):
        """Forward every other attribute to the wrapped file."""
        return getattr(self._file, nome)


def _open_misurato(file, mode='r', *args, **kwargs):
    """Drop-in replacement for builtins.open that wraps files opened for writing in the work folder.

    Files outside the work folder, or opened read-only, are returned unwrapped.
    """
    reale = _open_originale(file, mode, *args, **kwargs)
    scrittura = any(carattere in mode for carattere in ('w', 'a', 'x', '+'))
    try:
        percorso = os.path.abspath(os.fspath(file))
    except TypeError:
        percorso = ''
    dentro = bool(_lavoro and percorso.startswith(os.path.abspath(_lavoro) + os.sep))
    return _FileMisurato(reale) if scrittura and dentro else reale


def strumenta_scritture():
    """Replace builtins.open with the measuring version (idempotent)."""
    global _open_attivo
    if not _open_attivo:
        builtins.open = _open_misurato
        _open_attivo = True


class _Cattura:
    """Proxy for cv2.VideoCapture that times frame decoding and counts the frames read.

    When at least 90% of the frames were read it counts one complete video decode.
    """

    def __init__(self, cv2, sorgente, *args, **kwargs):
        """Open the video `sorgente` with the original VideoCapture and register this capture."""
        self._cv2 = cv2
        self._letti = 0
        self._chiusa = False
        with blocco('apertura_video'):
            self._cap = _cv2_originale(sorgente, *args, **kwargs)
        self._totale = int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        _catture.append(self)

    def read(self, *args, **kwargs):
        """Decode the next frame (timed as 'decodifica_video'); counts it when decoding succeeded."""
        with blocco('decodifica_video'):
            esito = self._cap.read(*args, **kwargs)
        if esito[0]:
            self._letti += 1
            fotogrammi(n=1)
        return esito

    def grab(self, *args, **kwargs):
        """Advance to the next frame (timed as 'decodifica_video'); counts it when that succeeded."""
        with blocco('decodifica_video'):
            esito = self._cap.grab(*args, **kwargs)
        if esito:
            self._letti += 1
            fotogrammi(n=1)
        return esito

    def retrieve(self, *args, **kwargs):
        """Decode the grabbed frame (timed as 'decodifica_video'); returns the (ok, image) pair."""
        with blocco('decodifica_video'):
            return self._cap.retrieve(*args, **kwargs)

    def release(self):
        """Close the capture after booking the complete-decode counter if it applies."""
        self._completa()
        return self._cap.release()

    def _completa(self):
        """Count one complete decode if enough frames were read; runs at most once per capture."""
        if self._chiusa:
            return
        self._chiusa = True
        if self._totale > 0 and self._letti >= self._totale * COMPLETE_READ_FRACTION:
            conta('video_decodifiche_complete')

    def __getattr__(self, nome):
        """Forward every other attribute to the wrapped VideoCapture."""
        return getattr(self._cap, nome)


def strumenta_cv2(cv2):
    """Patch the given cv2 module so video capture, resize and cvtColor are timed (idempotent)."""
    global _cv2_originale
    if _cv2_originale is not None:
        return
    _cv2_originale = cv2.VideoCapture
    cv2.VideoCapture = lambda sorgente, *args, **kwargs: _Cattura(cv2, sorgente, *args, **kwargs)
    originale_resize = cv2.resize
    originale_colore = cv2.cvtColor

    def resize(*args, **kwargs):
        """Timed replacement for the cv2 scaling function."""
        with blocco('ridimensionamenti'):
            return originale_resize(*args, **kwargs)

    def colore(*args, **kwargs):
        """Timed cv2.cvtColor."""
        with blocco('conversioni_colore'):
            return originale_colore(*args, **kwargs)

    cv2.resize = resize
    cv2.cvtColor = colore


class _RapidOCR:
    """Proxy for a RapidOCR engine that times every recognition call as 'rapidocr'."""

    def __init__(self, motore):
        """Wrap the OCR engine `motore`."""
        self._motore = motore

    def __call__(self, *args, **kwargs):
        """Run the wrapped engine on the given arguments, timed."""
        with blocco('rapidocr'):
            return self._motore(*args, **kwargs)

    def __getattr__(self, nome):
        """Forward every other attribute to the wrapped engine."""
        return getattr(self._motore, nome)


def strumenta_rapidocr(motore):
    """Return `motore` wrapped so its calls are timed."""
    return _RapidOCR(motore)


def configura(fase, lavoro, video=None, calcolo=True):
    """Start profiling a pipeline step (first call wins, later calls are ignored).

    `fase` names the step, `lavoro` is the work folder receiving tempi.jsonl, `video` the optional
    video path. With `calcolo` a root block 'calcolo_fase' covers the whole step. Registers
    `chiudi` to run at interpreter exit.
    """
    global _fase, _lavoro, _video, _radice, _chiuso, _registrato
    if _fase is not None:
        return
    _fase = str(fase)
    _lavoro = os.path.abspath(lavoro)
    _video = os.path.abspath(video) if video else None
    _chiuso = False
    os.makedirs(_lavoro, exist_ok=True)
    strumenta_scritture()
    if calcolo:
        _radice = blocco('calcolo_fase')
        _radice.__enter__()
    if not _registrato:
        atexit.register(chiudi)
        _registrato = True


def chiudi():
    """Finish profiling: close the root block and append all blocks plus the overhead to tempi.jsonl.

    Safe to call more than once; does nothing when profiling never started or is already closed.
    """
    global _chiuso, _radice, _overhead
    if _chiuso or _fase is None:
        return
    _chiuso = True
    for cattura in list(_catture):
        cattura._completa()
    if _radice is not None:
        radice = _radice
        _radice = None
        radice.__exit__(None, None, None)
    _aggiungi('overhead_misure', _overhead, chiamate=1)
    ram = _ram_picco_mb()
    righe = []
    with _lock:
        for nome in sorted(_dati):
            valore = _dati[nome]
            chiamate = valore['chiamate']
            per_chiamata = round(valore['secondi'] / chiamate, ROUND_DIGITS) if chiamate else 0.0
            righe.append({
                'fase': _fase,
                'blocco': nome,
                'secondi_totali': round(valore['secondi'], ROUND_DIGITS),
                'cpu_secondi': round(valore['cpu_secondi'], ROUND_DIGITS),
                'numero_chiamate': chiamate,
                'secondi_per_chiamata': per_chiamata,
                'ram_picco_mb': ram,
                'fotogrammi_letti': valore['fotogrammi'],
            })
    percorso = os.path.join(_lavoro, 'tempi.jsonl')
    with _open_originale(percorso, 'a', encoding='utf-8', newline='\n') as file:
        for riga in righe:
            file.write(json.dumps(riga, ensure_ascii=False, sort_keys=True) + '\n')
