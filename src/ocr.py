# -*- coding: utf-8 -*-
"""Text reading (OCR) of a screen recording, in three phases.

Usage: ocr.py <phase> <video> <outdir>   (phase: letture | azione | schermate)
Phase 'letture': (1) zone discovery: frames every 2 s, text-box DETECTION only (no recognition);
    boxes that recur at the same place with different pixel content are dynamic zones (one per
    text line); (2) reading at 1 fps of the dynamic zones only, and only when the crop changes,
    with single-line recognition. Writes ocr1.json and ocr1_stat.json.
Phase 'schermate': 1 fps; when the screen settles on a view different from the last one read
    (window, dialog, panel, new page) the changed box is read whole, once only. Writes ocr3.json
    and ocr3_stat.json.
Phase 'azione': uses mov.json (action phases from the movement step) and ocr1.json: 2 fps inside
    the phases (half seconds) plus 300x150 windows around the cursor. Writes ocr2.json and
    ocr2_stat.json.
Everything runs in multiprocessing; no coordinate or threshold is tied to a particular video.
Recognition results are cached by image fingerprint in <outdir>/ocr_cache.json.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from multiprocessing import Pool

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import comune
import tempi

# Frame width at which every phase works; the height follows the video aspect ratio.
W = 1280
# Upper bounds on the OCR worker processes (the configuration can only lower them).
MAX_PROCESSI_GPU = 2
MAX_PROCESSI_CPU = 3
# Summed per-channel difference above which a pixel counts as changed.
SOGLIA_PIXEL_SOMMA = 60
# Minimum fraction of changed pixels (or of the frame area, for a changed region) that matters.
FRAZIONE_CAMBIO_MINIMA = 0.004
# Fingerprint: grey crop shrunk to this width, quantised to this many levels.
LARGHEZZA_IMPRONTA = 32
LIVELLI_IMPRONTA = 16
# The parallel OCR phases share one cache file: on Windows opening it while another phase
# replaces it raises PermissionError, so file access is retried a few times, then given up.
CACHE_TENTATIVI = 5
CACHE_ATTESA_SECONDI = 0.1
# Zone discovery thresholds, shared by the fast and the reference implementation.
LATO_MINIMO_BOX = 6
IOU_STESSO_WIDGET = 0.5
MARGINE_ZONA = 3
ZONA_MAX_LARGHEZZA = 0.6
ZONA_MAX_ALTEZZA = 60
SOGLIA_PIXEL_DINAMICA = 40
SOGLIA_FRAZIONE_DINAMICA = 0.02
IOU_FUSIONE_ZONE = 0.4
TOLLERANZA_RIGA_X = 14
FUSIONE_MAX_LARGHEZZA = 0.5
# Fraction of the frame pairs a pixel must change in to count as always-moving ("live" area).
FRAZIONE_AREA_VIVA = 0.3


def _cuda_disponibile():
    """True when the config allows GPU OCR and onnxruntime offers the CUDA execution provider."""
    if not comune.config().get('ocr_gpu', True):
        return False
    try:
        import onnxruntime
        return 'CUDAExecutionProvider' in onnxruntime.get_available_providers()
    except Exception:
        return False


_USA_CUDA = _cuda_disponibile()
if _USA_CUDA:
    NPROC = max(1, min(int(comune.config().get('processi_ocr_gpu', 2)), MAX_PROCESSI_GPU,
                       os.cpu_count() or 1))
else:
    NPROC = max(1, min(int(comune.config().get('processi_ocr', 3)), MAX_PROCESSI_CPU,
                       os.cpu_count() or 1))


class _MotoreConFallback:
    """RapidOCR engine that tries CUDA first and silently falls back to the CPU.

    The CUDA engine is proven on a blank test image at start-up; if that or the very first real
    call fails, a CPU engine (one thread) replaces it. Attribute access is forwarded to the engine.
    """

    def __init__(self, rapidocr, usa_cuda):
        """Build the engine: `rapidocr` is the RapidOCR class, `usa_cuda` asks for GPU first."""
        self._rapidocr = rapidocr
        self._cuda = bool(usa_cuda)
        self._prima_chiamata = self._cuda
        if self._cuda:
            try:
                import onnxruntime
                preload = getattr(onnxruntime, 'preload_dlls', None)
                if preload is not None:
                    preload()
                self._motore = rapidocr(
                    det_use_cuda=True,
                    rec_use_cuda=True,
                    cls_use_cuda=True,
                )
                immagine_prova = np.full((64, 256, 3), 255, dtype=np.uint8)
                self._motore(immagine_prova)
            except Exception:
                self._attiva_processore(cuda_non_funzionante=True)
        else:
            self._attiva_processore()

    def _attiva_processore(self, cuda_non_funzionante=False):
        """Switch to a single-threaded CPU engine and report why (CUDA broken or not requested)."""
        self._cuda = False
        self._prima_chiamata = False
        self._motore = self._rapidocr(
            intra_op_num_threads=1,
            inter_op_num_threads=1,
        )
        if cuda_non_funzionante:
            print('OCR su processore (CUDA non funzionante)', flush=True)
        else:
            print('OCR su processore', flush=True)

    def __call__(self, *args, **kwargs):
        """Run the engine. The first call on CUDA is guarded: an exception falls back to the CPU."""
        if not self._prima_chiamata:
            return self._motore(*args, **kwargs)
        try:
            risultato = self._motore(*args, **kwargs)
        except Exception:
            self._attiva_processore(cuda_non_funzionante=True)
            return self._motore(*args, **kwargs)
        self._prima_chiamata = False
        print('OCR su CUDA', flush=True)
        return risultato

    def __getattr__(self, nome):
        """Forward every other attribute to the wrapped engine."""
        return getattr(self._motore, nome)


_eng = None


def _init():
    """Worker-process initialiser: create this process's OCR engine and start its timing recorder."""
    global _eng
    from rapidocr_onnxruntime import RapidOCR
    tempi.configura(comune.ambiente('TEMPI_FASE'), comune.ambiente('TEMPI_LAVORO'), calcolo=False)
    _eng = tempi.strumenta_rapidocr(
        _MotoreConFallback(RapidOCR, _cuda_disponibile())
    )


def t_det(img):
    """Detect text boxes in `img` without recognising them: list of [x0, y0, x1, y1]."""
    risultato, _ = _eng(img, use_cls=False, use_rec=False)
    caselle = []
    for quadrilatero in (risultato or []):
        xs = [punto[0] for punto in quadrilatero]
        ys = [punto[1] for punto in quadrilatero]
        caselle.append([min(xs), min(ys), max(xs), max(ys)])
    return caselle


def t_line(img):
    """Recognise a single-line crop (no detection): list of [text, confidence rounded to 2 digits]."""
    risultato, _ = _eng(img, use_det=False, use_cls=False, use_rec=True)
    return [[voce[0], round(float(voce[1]), 2)] for voce in (risultato or [])]


def t_full(img):
    """Detect and recognise all text in `img`: [text, confidence, x, y] of each box's top-left corner."""
    risultato, _ = _eng(img, use_cls=False)
    return [[voce[1], round(float(voce[2]), 2), int(voce[0][0][0]), int(voce[0][0][1])]
            for voce in (risultato or [])]


def t_box(img):
    """Detect and recognise all text in `img`: [text, confidence, x0, y0, x1, y1] of each box."""
    risultato, _ = _eng(img, use_cls=False)
    righe = []
    for voce in (risultato or []):
        xs = [punto[0] for punto in voce[0]]
        ys = [punto[1] for punto in voce[0]]
        righe.append([voce[1], round(float(voce[2]), 2), int(min(xs)), int(min(ys)),
                      int(max(xs)), int(max(ys))])
    return righe


def tdisp(richiesta):
    """Pool worker entry: `richiesta` is (key, image); the key prefix before ':' picks t_det/t_line/...

    Returns (key, result) and flushes the timing records of the worker afterwards.
    """
    chiave, img = richiesta
    try:
        risultato = {'det': t_det, 'line': t_line, 'full': t_full, 'box': t_box}[chiave.split(':')[0]](img)
        return chiave, risultato
    finally:
        tempi.scarica()


def chiudi_pool(pool, attesa=20.0, intervallo=0.2):
    """Close the worker pool, terminating it if workers are still alive after `attesa` seconds."""
    pool.close()
    scadenza = time.monotonic() + attesa
    while any(processo.is_alive() for processo in pool._pool):
        restante = scadenza - time.monotonic()
        if restante <= 0:
            pool.terminate()
            print('pool terminato dopo 20 s', flush=True)
            break
        time.sleep(min(intervallo, restante))
    else:
        print('pool chiuso', flush=True)
    pool.join()


def dims(video):
    """Return (working frame height, duration in seconds): the height is W scaled, rounded to even."""
    w, h, dur = comune.info_video(video)
    H = int(round(h * W / w / 2) * 2)
    return H, dur


def frames(video, fps, H, start=0, ss=None):
    """Decode `video` with ffmpeg at `fps` pictures per second, scaled to W x H.

    Generator of (index, BGR uint8 array). `start` and `ss` are accepted for compatibility and
    ignored. Counts decoded frames and one complete decode in the timing records.
    """
    cmd = [comune.ffmpeg(), '-v', 'error', '-i', video, '-vf', 'fps=%s,scale=%d:%d' % (fps, W, H),
           '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-']
    processo = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    numero = 0
    while True:
        with tempi.blocco('decodifica_video'):
            dati = processo.stdout.read(W * H * 3)
        if len(dati) < W * H * 3:
            break
        yield numero, np.frombuffer(dati, np.uint8).reshape(H, W, 3)
        tempi.fotogrammi(n=1)
        numero += 1
    tempi.conta('video_decodifiche_complete')


def iou(a, b):
    """Intersection over union of two boxes [x0, y0, x1, y1]; 0 when the union is empty."""
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    intersezione = max(0, x1 - x0) * max(0, y1 - y0)
    unione = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersezione
    return intersezione / unione if unione else 0


def crop(im, z):
    """Cut the box z = [x0, y0, x1, y1] out of image `im` (a view, not a copy)."""
    return im[z[1]:z[3], z[0]:z[2]]


def changed(a, b):
    """True when crop `b` differs enough from crop `a` (or `a` is missing or of another shape).

    A pixel differs when the summed channel difference exceeds SOGLIA_PIXEL_SOMMA; the crop has
    changed when at least FRAZIONE_CAMBIO_MINIMA of its pixels differ.
    """
    if a is None or a.shape != b.shape:
        return True
    differenza = np.abs(a.astype(np.int16) - b.astype(np.int16)).sum(2) > SOGLIA_PIXEL_SOMMA
    return differenza.mean() >= FRAZIONE_CAMBIO_MINIMA


def impronta(img):
    """Blake2b-128 fingerprint of the grey crop, 32 pixels wide and quantised to 16 levels.

    Equal fingerprints mean the OCR result can be reused. Raises ValueError for an empty crop.
    """
    if img is None or img.size == 0:
        raise ValueError('Ritaglio OCR vuoto')
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    altezza = max(1, int(round(gray.shape[0] * float(LARGHEZZA_IMPRONTA) / gray.shape[1])))
    piccolo = cv2.resize(gray, (LARGHEZZA_IMPRONTA, altezza), interpolation=cv2.INTER_AREA)
    quantizzato = (piccolo // LIVELLI_IMPRONTA).astype(np.uint8)
    forma = np.asarray([altezza, LARGHEZZA_IMPRONTA], dtype=np.uint32).tobytes()
    return hashlib.blake2b(forma + quantizzato.tobytes(), digest_size=16).hexdigest()


class CacheOCR:
    """On-disk cache of OCR results, keyed by image fingerprint and request type.

    Stored as <outdir>/ocr_cache.json; saved atomically (temporary file, then replace). The
    parallel OCR phases share the file, so reading and replacing it tolerate PermissionError:
    after a few retries the cache is simply left empty or unsaved (results stay the same).
    """

    def __init__(self, out):
        """Load the cache file of folder `out` if it exists, otherwise start empty."""
        self.percorso = os.path.join(out, 'ocr_cache.json')
        self.dati = {}
        for tentativo in range(CACHE_TENTATIVI):
            try:
                if os.path.isfile(self.percorso):
                    with open(self.percorso, encoding='utf-8') as file:
                        self.dati = json.load(file)
                break
            except (PermissionError, ValueError):
                self.dati = {}
                time.sleep(CACHE_ATTESA_SECONDI)
        self.modificata = False

    def leggi(self, chiave, tipo):
        """Cached result for fingerprint `chiave` and request type `tipo`, or None."""
        return self.dati.get(chiave, {}).get(tipo)

    def scrivi(self, chiave, tipo, valore):
        """Store `valore` for fingerprint `chiave` and request type `tipo` (kept in memory)."""
        self.dati.setdefault(chiave, {})[tipo] = valore
        self.modificata = True

    def salva(self):
        """Write the cache to disk if it changed since it was loaded or last saved."""
        if not self.modificata:
            return
        temporaneo = self.percorso + '.tmp.%d' % os.getpid()
        with open(temporaneo, 'w', encoding='utf-8', newline='\n') as file:
            json.dump(self.dati, file, ensure_ascii=False, sort_keys=True)
        for tentativo in range(CACHE_TENTATIVI):
            try:
                os.replace(temporaneo, self.percorso)
                self.modificata = False
                return
            except PermissionError:
                time.sleep(CACHE_ATTESA_SECONDI)
        try:
            os.remove(temporaneo)
        except OSError:
            pass


def esegui_ocr(pool, tasks, out, chunksize, richieste_aggiuntive=0):
    """Resolve a list of (key, image) requests through the cache; only missing fingerprints go to RapidOCR.

    Identical images of the same type are recognised once and shared. `richieste_aggiuntive`
    counts requests already satisfied elsewhere, for the statistics. Returns {key: result}.
    """
    cache = CacheOCR(out)
    risultati = {}
    mancanti = {}
    ordine = []
    for chiave, img in tasks:
        tipo = chiave.split(':', 1)[0]
        firma = impronta(img)
        valore = cache.leggi(firma, tipo)
        if valore is not None:
            risultati[chiave] = valore
            continue
        coppia = (firma, tipo)
        if coppia not in mancanti:
            mancanti[coppia] = {'img': img, 'chiavi': []}
            ordine.append(coppia)
        mancanti[coppia]['chiavi'].append(chiave)

    lavoro = []
    riferimenti = {}
    for indice, coppia in enumerate(ordine):
        firma, tipo = coppia
        chiave_lavoro = '%s:cache:%d' % (tipo, indice)
        lavoro.append((chiave_lavoro, mancanti[coppia]['img']))
        riferimenti[chiave_lavoro] = coppia

    calcolati = dict(pool.imap(tdisp, lavoro, chunksize=chunksize)) if lavoro else {}
    for chiave_lavoro, valore in calcolati.items():
        firma, tipo = riferimenti[chiave_lavoro]
        cache.scrivi(firma, tipo, valore)
        for chiave in mancanti[(firma, tipo)]['chiavi']:
            risultati[chiave] = valore

    richieste = len(tasks) + int(richieste_aggiuntive)
    vere = len(lavoro)
    tempi.ocr(richieste=richieste, cache=richieste - vere, rapidocr=vere)
    cache.salva()
    return risultati


# Strips stay disabled: splitting the image increases the number of OCR calls and the total time,
# because every RapidOCR call has a fixed cost. The whole box is read instead.
FASCE = False


def fasce_orizzontali(y0, y1, altezza=64, sovrapposizione=16):
    """Split the vertical range [y0, y1] into absolute strips at most `altezza` (64) pixels high.

    Consecutive strips overlap by `sovrapposizione` (16) pixels and the last one ends exactly at
    y1. Returns a list of (top, bottom); empty when the range is empty.
    """
    if y1 <= y0:
        return []
    if y1 - y0 <= altezza:
        return [(y0, y1)]
    passo = altezza - sovrapposizione
    risultato = []
    y = y0
    while True:
        fine = min(y1, y + altezza)
        coppia = (y, fine)
        if coppia not in risultato:
            risultato.append(coppia)
        if fine == y1:
            break
        y += passo
        if y + altezza > y1:
            y = y1 - altezza
    return risultato


def _iou_riga(a, b):
    """IoU of two recognised lines, whose boxes sit at indices 2..5 ([text, conf, x0, y0, x1, y1])."""
    x0 = max(a[2], b[2])
    y0 = max(a[3], b[3])
    x1 = min(a[4], b[4])
    y1 = min(a[5], b[5])
    intersezione = max(0, x1 - x0) * max(0, y1 - y0)
    area_a = max(0, a[4] - a[2]) * max(0, a[5] - a[3])
    area_b = max(0, b[4] - b[2]) * max(0, b[5] - b[3])
    unione = area_a + area_b - intersezione
    return intersezione / unione if unione else 0.0


def ricomponi_fasce(riferimenti, risultati):
    """Map strip results back to the original box and merge duplicates found in the overlaps.

    `riferimenti` is a list of (vertical offset, result key). Two lines are duplicates when their
    normalised text is equal and their IoU is at least 0.2; the more confident one is kept.
    Returns the lines sorted by position (y, x, text).
    """
    righe = []
    for scarto_y, chiave in riferimenti:
        for voce in risultati[chiave]:
            corrente = [voce[0], voce[1], int(voce[2]), int(voce[3] + scarto_y), int(voce[4]),
                        int(voce[5] + scarto_y)]
            testo = re.sub(r'\s+', ' ', corrente[0]).strip().casefold()
            duplicato = None
            for indice, esistente in enumerate(righe):
                altro = re.sub(r'\s+', ' ', esistente[0]).strip().casefold()
                if testo == altro and _iou_riga(corrente, esistente) >= 0.2:
                    duplicato = indice
                    break
            if duplicato is None:
                righe.append(corrente)
            elif corrente[1] > righe[duplicato][1]:
                righe[duplicato] = corrente
    righe.sort(key=lambda voce: (voce[3], voce[2], voce[0]))
    return righe


def _rileva_riquadri(video, H, pool, stat):
    """Sample the video every 2 s, run text detection on each frame and return (frames, results).

    Results map 'det:<frame index>' to the boxes found. `stat['det']` gets the frame count.
    """
    imgs = []
    for _, im in frames(video, '1/2', H):
        imgs.append(im)
    res = esegui_ocr(pool, [('det:%d' % i, im) for i, im in enumerate(imgs)],
                     comune.ambiente('TEMPI_LAVORO'), chunksize=2)
    stat['det'] = len(imgs)
    return imgs, res


def _zone_dinamiche(widgets, imgs, H):
    """Pick the widgets that really change over time and return their padded boxes.

    A widget (a box seen in at least two frames) qualifies when it is not too large and, between
    two of its observations, enough pixels of its crop change. Boxes are padded by MARGINE_ZONA
    and clipped to the frame, then ordered by number of observations, most seen first.
    """
    zone = []
    for widget in widgets:
        if len(widget['obs']) < 2:
            continue
        box = widget['box']
        riquadro = [int(max(0, box[0] - MARGINE_ZONA)), int(max(0, box[1] - MARGINE_ZONA)),
                    int(min(W, box[2] + MARGINE_ZONA)), int(min(H, box[3] + MARGINE_ZONA))]
        if riquadro[2] - riquadro[0] > ZONA_MAX_LARGHEZZA * W or riquadro[3] - riquadro[1] > ZONA_MAX_ALTEZZA:
            continue
        ritagli = [cv2.cvtColor(crop(imgs[i], riquadro), cv2.COLOR_BGR2GRAY).astype(np.int16)
                   for i in widget['obs']]
        dif = max(float((np.abs(ritagli[k] - ritagli[k - 1]) > SOGLIA_PIXEL_DINAMICA).mean())
                  for k in range(1, len(ritagli)))
        if dif >= SOGLIA_FRAZIONE_DINAMICA:
            zone.append((riquadro, len(widget['obs'])))
    return [riquadro for riquadro, _ in sorted(zone, key=lambda voce: -voce[1])]


def _scoperta_lenta(video, H, pool, stat):
    """Reference (pure Python, one comparison at a time) version of `scoperta`.

    Kept to check that the vectorised version gives byte-identical results (see the tests).
    Same arguments and return value as `scoperta`.
    """
    imgs, res = _rileva_riquadri(video, H, pool, stat)
    widgets = []
    for i in range(len(imgs)):
        for b in res['det:%d' % i]:
            if (b[2] - b[0]) < LATO_MINIMO_BOX or (b[3] - b[1]) < LATO_MINIMO_BOX:
                continue
            best = max(widgets, key=lambda widget: iou(widget['box'], b), default=None)
            if best is not None and iou(best['box'], b) > IOU_STESSO_WIDGET:
                best['obs'].append(i)
                best['box'] = [min(best['box'][0], b[0]), min(best['box'][1], b[1]),
                               max(best['box'][2], b[2]), max(best['box'][3], b[3])]
            else:
                widgets.append(dict(box=list(b), obs=[i]))
    bs = _zone_dinamiche(widgets, imgs, H)
    fuso = True
    while fuso:
        fuso = False
        for i in range(len(bs)):
            for j in range(i + 1, len(bs)):
                a, b = bs[i], bs[j]
                sovrapposizione_y = min(a[3], b[3]) - max(a[1], b[1])
                stessa_riga = (sovrapposizione_y >= 0.5 * min(a[3] - a[1], b[3] - b[1])
                               and a[0] <= b[2] + TOLLERANZA_RIGA_X and b[0] <= a[2] + TOLLERANZA_RIGA_X)
                unione = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
                if ((iou(a, b) > IOU_FUSIONE_ZONE or stessa_riga)
                        and unione[2] - unione[0] <= FUSIONE_MAX_LARGHEZZA * W):
                    bs[i] = unione
                    del bs[j]
                    fuso = True
                    break
            if fuso:
                break
    out = sorted(bs, key=lambda zona: (zona[1], zona[0]))
    stat['widgets'] = len(widgets)
    stat['zone'] = len(out)
    return out


def scoperta(video, H, pool, stat):
    """Discover the dynamic text zones of the video (one box per changing text line).

    Detects text boxes on frames every 2 s, groups boxes that overlap (IoU > 0.5) into widgets,
    keeps those whose content changes, then merges zones that overlap or sit on the same line.
    The overlap tests are vectorised with numpy. `stat` receives 'det', 'widgets' and 'zone'
    counts. Returns the zones as [x0, y0, x1, y1] sorted by position (top, then left).
    """
    imgs, res = _rileva_riquadri(video, H, pool, stat)
    widgets = []
    capacita = 16
    box_widget = np.empty((capacita, 4), dtype=np.float64)
    with tempi.blocco('scoperta_widget'):
        for i in range(len(imgs)):
            for b_originale in res['det:%d' % i]:
                if ((b_originale[2] - b_originale[0]) < LATO_MINIMO_BOX
                        or (b_originale[3] - b_originale[1]) < LATO_MINIMO_BOX):
                    continue
                b = np.asarray(b_originale, dtype=np.float64)
                if widgets:
                    attivi = box_widget[:len(widgets)]
                    x0 = np.maximum(attivi[:, 0], b[0])
                    y0 = np.maximum(attivi[:, 1], b[1])
                    x1 = np.minimum(attivi[:, 2], b[2])
                    y1 = np.minimum(attivi[:, 3], b[3])
                    intersezione = np.maximum(0.0, x1 - x0) * np.maximum(0.0, y1 - y0)
                    unione = ((attivi[:, 2] - attivi[:, 0]) * (attivi[:, 3] - attivi[:, 1])
                              + (b[2] - b[0]) * (b[3] - b[1]) - intersezione)
                    valori_iou = np.divide(intersezione, unione, out=np.zeros_like(intersezione),
                                           where=unione > 0)
                    indice_best = int(np.argmax(valori_iou))
                    valore_best = float(valori_iou[indice_best])
                else:
                    indice_best = -1
                    valore_best = 0.0
                if indice_best >= 0 and valore_best > IOU_STESSO_WIDGET:
                    best = widgets[indice_best]
                    best['obs'].append(i)
                    best['box'] = [min(best['box'][0], b_originale[0]), min(best['box'][1], b_originale[1]),
                                   max(best['box'][2], b_originale[2]), max(best['box'][3], b_originale[3])]
                    box_widget[indice_best] = best['box']
                else:
                    if len(widgets) == capacita:
                        nuova_capacita = capacita * 2
                        nuovi_box = np.empty((nuova_capacita, 4), dtype=np.float64)
                        nuovi_box[:capacita] = box_widget
                        box_widget = nuovi_box
                        capacita = nuova_capacita
                    widgets.append(dict(box=list(b_originale), obs=[i]))
                    box_widget[len(widgets) - 1] = b
    bs = _zone_dinamiche(widgets, imgs, H)
    with tempi.blocco('scoperta_fusione'):
        fuso = True
        while fuso:
            fuso = False
            numero = len(bs)
            if numero < 2:
                break
            matrice = np.asarray(bs, dtype=np.float64)
            x0 = np.maximum(matrice[:, None, 0], matrice[None, :, 0])
            y0 = np.maximum(matrice[:, None, 1], matrice[None, :, 1])
            x1 = np.minimum(matrice[:, None, 2], matrice[None, :, 2])
            y1 = np.minimum(matrice[:, None, 3], matrice[None, :, 3])
            intersezione = np.maximum(0.0, x1 - x0) * np.maximum(0.0, y1 - y0)
            aree = (matrice[:, 2] - matrice[:, 0]) * (matrice[:, 3] - matrice[:, 1])
            unione_iou = aree[:, None] + aree[None, :] - intersezione
            valori_iou = np.divide(intersezione, unione_iou, out=np.zeros_like(intersezione),
                                   where=unione_iou > 0)
            altezze = matrice[:, 3] - matrice[:, 1]
            sovrapposizione_y = (np.minimum(matrice[:, None, 3], matrice[None, :, 3])
                                 - np.maximum(matrice[:, None, 1], matrice[None, :, 1]))
            stessa_riga = ((sovrapposizione_y >= 0.5 * np.minimum(altezze[:, None], altezze[None, :]))
                           & (matrice[:, None, 0] <= matrice[None, :, 2] + TOLLERANZA_RIGA_X)
                           & (matrice[None, :, 0] <= matrice[:, None, 2] + TOLLERANZA_RIGA_X))
            unione_x0 = np.minimum(matrice[:, None, 0], matrice[None, :, 0])
            unione_x1 = np.maximum(matrice[:, None, 2], matrice[None, :, 2])
            condizione = (((valori_iou > IOU_FUSIONE_ZONE) | stessa_riga)
                          & ((unione_x1 - unione_x0) <= FUSIONE_MAX_LARGHEZZA * W))
            coppie = np.argwhere(np.triu(condizione, k=1))
            if len(coppie):
                i, j = (int(coppie[0, 0]), int(coppie[0, 1]))
                a, b = bs[i], bs[j]
                bs[i] = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
                del bs[j]
                fuso = True
    out = sorted(bs, key=lambda zona: (zona[1], zona[0]))
    stat['widgets'] = len(widgets)
    stat['zone'] = len(out)
    return out


def letture(video, out):
    """First OCR phase: discover the dynamic text zones, then read them at 1 fps.

    A zone crop is recognised only when it changed since the previous frame. Writes
    <out>/ocr1.json (zones, readings per zone as [second, result], frame height, duration, step)
    and <out>/ocr1_stat.json (counters and timings), and prints the statistics.
    """
    H, dur = dims(video)
    avvio = time.time()
    stat = {}
    with Pool(NPROC, _init) as pool:
        zone = scoperta(video, H, pool, stat)
        stat['t_scoperta'] = round(time.time() - avvio, 1)
        tasks = []
        precedenti = {}
        for n, im in frames(video, 1, H):
            for indice_zona, zona in enumerate(zone):
                ritaglio = crop(im, zona)
                if changed(precedenti.get(indice_zona), ritaglio):
                    precedenti[indice_zona] = ritaglio.copy()
                    tasks.append(('line:%d:%d' % (n, indice_zona), precedenti[indice_zona]))
        res = esegui_ocr(pool, tasks, out, chunksize=8)
        stat['rec'] = len(tasks)
        chiudi_pool(pool)
    letture_zona = {}
    for chiave, valore in res.items():
        _, n, indice_zona = chiave.split(':')
        letture_zona.setdefault(int(indice_zona), []).append([int(n), valore])
    with open(os.path.join(out, 'ocr1.json'), 'w', encoding='utf-8') as file_ocr:
        json.dump(dict(zone=zone, stato=letture_zona, H=H, dur=dur, passo=1.0), file_ocr, ensure_ascii=False)
    stat['t_tot'] = round(time.time() - avvio, 1)
    with open(os.path.join(out, 'ocr1_stat.json'), 'w') as file_stat:
        json.dump(stat, file_stat)
    print('letture', stat, flush=True)


def azione(video, out):
    """Second OCR phase: read the dynamic zones and the cursor surroundings during action phases.

    Needs ocr1.json (zones) and mov.json (phases, cursor track) in `out`. At 2 fps, inside each
    action phase (widened by 0.5 s before and 1 s after): on every half second the changed zone
    crops are recognised, and a 300x150 window around the cursor (or the phase box centre) is
    read whole. Writes <out>/ocr2.json and <out>/ocr2_stat.json and prints the statistics.
    """
    H, dur = dims(video)
    avvio = time.time()
    stat = {}
    base = json.load(open(os.path.join(out, 'ocr1.json'), encoding='utf-8'))
    zone = base['zone']
    mv = json.load(open(os.path.join(out, 'mov.json')))
    scala = W / mv['W']
    cur = np.array(mv['cur']) if mv['cur'] else np.zeros((0, 3))
    # Action times: half seconds and whole seconds inside the phases (with a margin).
    finestre_azione = [(fase['t0'] - 0.5, fase['t1'] + 1.0) for fase in mv['fasi']
                       if fase['mods'] + fase['ist'] > 0]

    def in_finestra(istante):
        """True when `istante` falls inside any action phase (with margin)."""
        return any(inizio <= istante <= fine for inizio, fine in finestre_azione)

    def posizione_cursore(istante):
        """Cursor position at `istante` in frame pixels: nearest sample within 1 s, else phase box centre."""
        if len(cur):
            k = int(np.argmin(np.abs(cur[:, 0] - istante)))
            if abs(cur[k, 0] - istante) <= 1.0:
                return cur[k, 1] * scala, cur[k, 2] * scala
        for fase in mv['fasi']:
            if fase['t0'] - 0.5 <= istante <= fase['t1'] + 1.0 and fase['bbox']:
                bbox = fase['bbox']
                return (bbox[0] + bbox[2]) / 2 * scala, (bbox[1] + bbox[3]) / 2 * scala
        return None

    with Pool(NPROC, _init) as pool:
        tasks = []
        prevframe = None
        for n, im in frames(video, 2, H):
            istante = n / 2
            # Half second: read the zones.
            if n % 2 == 1 and in_finestra(istante):
                for indice_zona, zona in enumerate(zone):
                    ritaglio = crop(im, zona)
                    precedente_zona = crop(prevframe, zona)
                    if changed(precedente_zona, ritaglio):
                        tasks.append(('line:%d:%d' % (n, indice_zona), ritaglio.copy()))
            if in_finestra(istante):
                posizione = posizione_cursore(istante)
                if posizione is not None:
                    cx, cy = int(posizione[0]), int(posizione[1])
                    x0 = min(max(0, cx - 150), W - 300)
                    y0 = min(max(0, cy - 75), H - 150)
                    tasks.append(('full:%d:%d:%d' % (n, x0, y0), im[y0:y0 + 150, x0:x0 + 300].copy()))
            prevframe = im
        res = esegui_ocr(pool, tasks, out, chunksize=4)
        chiudi_pool(pool)
    stato = {}
    finestre = []
    for chiave, valore in res.items():
        parti = chiave.split(':')
        if parti[0] == 'line':
            stato.setdefault(int(parti[2]), []).append([int(parti[1]) / 2, valore])
        else:
            finestre.append([int(parti[1]) / 2, int(parti[2]), int(parti[3]), valore])
    finestre.sort()
    with open(os.path.join(out, 'ocr2.json'), 'w', encoding='utf-8') as file_ocr:
        json.dump(dict(stato=stato, finestre=finestre), file_ocr, ensure_ascii=False)
    stat['rec_zone'] = sum(1 for chiave in res if chiave.startswith('line'))
    stat['finestre'] = len(finestre)
    stat['t_tot'] = round(time.time() - avvio, 1)
    with open(os.path.join(out, 'ocr2_stat.json'), 'w') as file_stat:
        json.dump(stat, file_stat)
    print('azione', stat, flush=True)


def regione_cambiata(a, b, H, vivo=None):
    """Box of the pixels that changed between frames `a` and `b`, plus the changed screen fraction.

    Cursor and noise are removed: pixels in the `vivo` mask are ignored, thin specks are erased
    and what is left is grown, so small islands (cursor, blinking) are dropped. Returns
    ([x0, y0, x1, y1], fraction), or (None, 0.0) when too little changed.
    """
    maschera = (np.abs(a.astype(np.int16) - b.astype(np.int16)).sum(2) > SOGLIA_PIXEL_SOMMA).astype(np.uint8)
    if vivo is not None:
        maschera[vivo] = 0
    # Removes specks and the cursor's borders.
    maschera = cv2.morphologyEx(maschera, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    maschera = cv2.dilate(maschera, np.ones((15, 15), np.uint8))
    numero, _, statistiche, _ = cv2.connectedComponentsWithStats(maschera)
    # Small islands are the cursor or blinking.
    validi = [k for k in range(1, numero)
              if statistiche[k, 2] * statistiche[k, 3] >= FRAZIONE_CAMBIO_MINIMA * W * H]
    if not validi:
        return None, 0.0
    x0 = min(statistiche[k, 0] for k in validi)
    y0 = min(statistiche[k, 1] for k in validi)
    x1 = max(statistiche[k, 0] + statistiche[k, 2] for k in validi)
    y1 = max(statistiche[k, 1] + statistiche[k, 3] for k in validi)
    return [int(x0), int(y0), int(x1), int(y1)], float(maschera.mean())


def schermate(video, out):
    """Third OCR phase: read each new full-screen view once, when the screen has settled.

    Pass 1 (1 fps) finds the always-moving "live" area. Pass 2 looks for a still frame that
    differs from the last one read; the changed box (padded by 8 px) is recognised, reusing the
    previous result when the crop fingerprint is unchanged. Writes <out>/ocr3.json
    (readings with their boxes) and <out>/ocr3_stat.json, and prints the statistics.
    """
    H, _ = dims(video)
    avvio = time.time()
    stat = {'letture': 0, 'scartate_mosse': 0, 'fasce': 0, 'fasce_riusate': 0}
    conteggio = None
    precedente = None
    numero_differenze = 0
    for _, im in frames(video, 1, H):
        if precedente is not None:
            differenza = np.abs(im.astype(np.int16) - precedente.astype(np.int16)).sum(2) > SOGLIA_PIXEL_SOMMA
            conteggio = differenza.astype(np.uint16) if conteggio is None else conteggio + differenza
            numero_differenze += 1
        precedente = im
    if conteggio is not None:
        sempre_mosso = conteggio >= FRAZIONE_AREA_VIVA * numero_differenze
    else:
        sempre_mosso = np.zeros((H, W), bool)
    vivo = cv2.dilate(sempre_mosso.astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool)
    stat['vivo_frazione'] = round(float(vivo.mean()), 3)

    tasks = []
    eventi = []
    precedenti = {}
    riusi = 0
    letto = None
    precedente = None
    for n, im in frames(video, 1, H):
        if letto is None:
            letto = im
            precedente = im
            continue
        differenza = (np.abs(im.astype(np.int16) - precedente.astype(np.int16)).sum(2) > SOGLIA_PIXEL_SOMMA)
        differenza[vivo] = False
        fermo = differenza.mean() < 0.002
        precedente = im
        if not fermo:
            continue
        box, frazione = regione_cambiata(letto, im, H, vivo)
        if box is None or frazione < 0.008:
            continue
        if (box[2] - box[0]) * (box[3] - box[1]) < 0.01 * W * H:
            continue

        zona_testo = [
            max(0, box[0] - 8),
            max(0, box[1] - 8),
            min(W, box[2] + 8),
            min(H, box[3] + 8),
        ]
        riferimenti = []
        for fy0, fy1 in (fasce_orizzontali(zona_testo[1], zona_testo[3]) if FASCE
                         else [(zona_testo[1], zona_testo[3])]):
            ritaglio = im[fy0:fy1, zona_testo[0]:zona_testo[2]].copy()
            firma = impronta(ritaglio)
            zona = (zona_testo[0], zona_testo[2], fy0, fy1)
            precedente_zona = precedenti.get(zona)
            if precedente_zona is not None and precedente_zona[0] == firma:
                chiave = precedente_zona[1]
                riusi += 1
            else:
                chiave = 'box:%d:%d:%d:%d:%d' % (n, zona_testo[0], fy0, zona_testo[2], fy1)
                tasks.append((chiave, ritaglio))
                precedenti[zona] = (firma, chiave)
            riferimenti.append((fy0 - zona_testo[1], chiave))
        eventi.append((n, zona_testo, riferimenti))
        letto = im

    with Pool(NPROC, _init) as pool:
        res = esegui_ocr(
            pool,
            tasks,
            out,
            chunksize=1,
            richieste_aggiuntive=riusi,
        )
        chiudi_pool(pool)

    elenco_letture = []
    for n, zona_testo, riferimenti in eventi:
        elenco_letture.append([n, zona_testo[0], zona_testo[1], ricomponi_fasce(riferimenti, res)])
    elenco_letture.sort()

    with open(os.path.join(out, 'ocr3.json'), 'w', encoding='utf-8') as file:
        json.dump(
            dict(letture=elenco_letture, W=W, H=H),
            file,
            ensure_ascii=False,
        )
    stat['letture'] = len(elenco_letture)
    stat['fasce'] = len(tasks) + riusi
    stat['fasce_riusate'] = riusi
    stat['t_tot'] = round(time.time() - avvio, 1)
    with open(os.path.join(out, 'ocr3_stat.json'), 'w', encoding='utf-8') as file:
        json.dump(stat, file)
    print('schermate', stat, flush=True)


if __name__ == '__main__':
    fase = 'ocr_' + sys.argv[1]
    os.environ['DEMOBRIEF_TEMPI_FASE'] = fase
    os.environ['DEMOBRIEF_TEMPI_LAVORO'] = os.path.abspath(sys.argv[3])
    tempi.configura(fase, sys.argv[3], sys.argv[2])
    fasi = {'letture': letture, 'azione': azione, 'schermate': schermate}
    fasi[sys.argv[1]](sys.argv[2], sys.argv[3])
