# -*- coding: utf-8 -*-
"""Profilazione leggera a blocchi per DemoBrief."""
import atexit
import builtins
import json
import os
import threading
import time
import tracemalloc
from contextlib import ContextDecorator

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
    global _overhead
    t0 = time.perf_counter()
    with _lock:
        d = _dati.setdefault(nome, {'secondi': 0.0, 'cpu_secondi': 0.0, 'chiamate': 0, 'fotogrammi': 0})
        d['secondi'] += float(secondi)
        d['cpu_secondi'] += float(cpu_secondi)
        d['chiamate'] += int(chiamate)
        d['fotogrammi'] += int(fotogrammi)
    _overhead += time.perf_counter() - t0


class _Blocco(ContextDecorator):
    def __init__(self, nome):
        self.nome = nome
        self.inizio = None
        self.inizio_cpu = None

    def __enter__(self):
        self.inizio = time.perf_counter()
        self.inizio_cpu = time.process_time()
        return self

    def __exit__(self, tipo, valore, traccia):
        _aggiungi(
            self.nome,
            time.perf_counter() - self.inizio,
            cpu_secondi=time.process_time() - self.inizio_cpu,
        )
        return False


def blocco(nome):
    return _Blocco(nome)


def conta(nome, n=1):
    _aggiungi(nome, chiamate=n)


def fotogrammi(nome='fotogrammi_letti', n=1):
    _aggiungi(nome, chiamate=0, fotogrammi=n)


def ocr(richieste=0, cache=0, rapidocr=0):
    """Registra il bilancio OCR della fase corrente."""
    conta('ocr_richieste', richieste)
    conta('ocr_risolte_cache', cache)
    conta('ocr_chiamate_rapidocr', rapidocr)


def _ram_picco_mb():
    if _psutil is not None:
        info = _psutil.Process(os.getpid()).memory_info()
        valore = getattr(info, 'peak_wset', info.rss)
        return round(valore / 1048576.0, 3)
    return round(tracemalloc.get_traced_memory()[1] / 1048576.0, 3)


class _FileMisurato:
    def __init__(self, file_reale):
        self._file = file_reale

    def write(self, valore):
        with blocco('scrittura_file'):
            return self._file.write(valore)

    def writelines(self, valori):
        with blocco('scrittura_file'):
            return self._file.writelines(valori)

    def __enter__(self):
        self._file.__enter__()
        return self

    def __exit__(self, tipo, valore, traccia):
        return self._file.__exit__(tipo, valore, traccia)

    def __iter__(self):
        return iter(self._file)

    def __getattr__(self, nome):
        return getattr(self._file, nome)


def _open_misurato(file, mode='r', *args, **kwargs):
    reale = _open_originale(file, mode, *args, **kwargs)
    scrittura = any(x in mode for x in ('w', 'a', 'x', '+'))
    try:
        percorso = os.path.abspath(os.fspath(file))
    except TypeError:
        percorso = ''
    dentro = bool(_lavoro and percorso.startswith(os.path.abspath(_lavoro) + os.sep))
    return _FileMisurato(reale) if scrittura and dentro else reale


def strumenta_scritture():
    global _open_attivo
    if not _open_attivo:
        builtins.open = _open_misurato
        _open_attivo = True


class _Cattura:
    def __init__(self, cv2, sorgente, *args, **kwargs):
        self._cv2 = cv2
        self._letti = 0
        self._chiusa = False
        with blocco('apertura_video'):
            self._cap = _cv2_originale(sorgente, *args, **kwargs)
        self._totale = int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        _catture.append(self)

    def read(self, *args, **kwargs):
        with blocco('decodifica_video'):
            esito = self._cap.read(*args, **kwargs)
        if esito[0]:
            self._letti += 1
            fotogrammi(n=1)
        return esito

    def grab(self, *args, **kwargs):
        with blocco('decodifica_video'):
            esito = self._cap.grab(*args, **kwargs)
        if esito:
            self._letti += 1
            fotogrammi(n=1)
        return esito

    def retrieve(self, *args, **kwargs):
        with blocco('decodifica_video'):
            return self._cap.retrieve(*args, **kwargs)

    def release(self):
        self._completa()
        return self._cap.release()

    def _completa(self):
        if self._chiusa:
            return
        self._chiusa = True
        if self._totale > 0 and self._letti >= self._totale * 0.9:
            conta('video_decodifiche_complete')

    def __getattr__(self, nome):
        return getattr(self._cap, nome)


def strumenta_cv2(cv2):
    global _cv2_originale
    if _cv2_originale is not None:
        return
    _cv2_originale = cv2.VideoCapture
    cv2.VideoCapture = lambda sorgente, *a, **k: _Cattura(cv2, sorgente, *a, **k)
    originale_resize = cv2.resize
    originale_colore = cv2.cvtColor

    def resize(*args, **kwargs):
        with blocco('ridimensionamenti'):
            return originale_resize(*args, **kwargs)

    def colore(*args, **kwargs):
        with blocco('conversioni_colore'):
            return originale_colore(*args, **kwargs)

    cv2.resize = resize
    cv2.cvtColor = colore


class _RapidOCR:
    def __init__(self, motore):
        self._motore = motore

    def __call__(self, *args, **kwargs):
        with blocco('rapidocr'):
            return self._motore(*args, **kwargs)

    def __getattr__(self, nome):
        return getattr(self._motore, nome)


def strumenta_rapidocr(motore):
    return _RapidOCR(motore)


def configura(fase, lavoro, video=None, calcolo=True):
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
            d = _dati[nome]
            chiamate = d['chiamate']
            righe.append({
                'fase': _fase,
                'blocco': nome,
                    'secondi_totali': round(d['secondi'], 6),
                    'cpu_secondi': round(d['cpu_secondi'], 6),
                'numero_chiamate': chiamate,
                'secondi_per_chiamata': round(d['secondi'] / chiamate, 6) if chiamate else 0.0,
                'ram_picco_mb': ram,
                'fotogrammi_letti': d['fotogrammi'],
            })
    percorso = os.path.join(_lavoro, 'tempi.jsonl')
    with _open_originale(percorso, 'a', encoding='utf-8', newline='\n') as file:
        for riga in righe:
            file.write(json.dumps(riga, ensure_ascii=False, sort_keys=True) + '\n')
