# -*- coding: utf-8 -*-
"""Channel D1 - mouse cursor (the CURSORE section of the narrative).

Usage:  python -I canale_cursore.py <video.mp4> <_lavoro folder>
Writes <_lavoro>\\eventi_cursore.json (the shared event schema, see eventi.py) and
<_lavoro>\\cursore.json (dense track [t, x, y, shape, conf] at 10 fps, in frame fractions).

Pipeline:
  1. fotogrammi()         reads the video at 10 fps, gray, width <= 1280 (INTER_LINEAR, never INTER_AREA)
  2. impara_modelli()     learns cursor templates automatically from moving blobs
  3. Inseguitore          tracks the cursor frame by frame (local, still, global, blob)
  4. estrai_eventi()      turns the track into dwell / click / drag / shape events
  5. assegna_nomi()       names the object under the point: Scena first, then OCR of a crop

The computation (pure functions on arrays) is kept apart from the video reading (cv2.VideoCapture,
rapidocr): the synthetic tests feed generated frames, not videos.
Coverage = share of the sampled frames in which the cursor was FOUND, meaning a position with an NCC
above threshold (source locale/globale) or a confirmed still crop (source fermo). A moving blob is
only a search hint: it is never counted as found.
"""
import argparse
import ctypes
import json
import math
import os
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eventi
from testi import pulisci_ocr, utile

VERSIONE = '0.3'

PREDEFINITI = {
    # reading
    'fps': 10.0, 'larghezza_max': 1280,
    # template learning
    'addestra_s': 60.0, 'addestra_finestre': 3, 'soglia_diff': 25, 'lato_min': 6, 'lato_max': 30,
    'quota_fissa': 0.15, 'max_macchie_coppia': 4, 'max_frazione_cambio': 0.02,
    'patch': 48, 'lato_modello': 0, 'max_candidati': 2500, 'ncc_gruppo': 0.70, 'min_membri': 6,
    'max_modelli': 4, 'estensione_min': 40, 'estensione_rel': 0.12, 'maschera': True,
    'coppie_cursore': True, 'membri_ok': 20,
    # tracking
    'finestra': 96, 'ncc_ferma': 0.60, 'ncc_locale': 0.72, 'ncc_globale': 0.72, 'cella': 32,
    'quota_occupata': 0.35, 'soglia_occ': 10, 'occ_alpha': 0.03, 'occ_online': 0.5, 'occ_forte': 0.2,
    'calibra': True, 'calibra_fotogrammi': 20, 'calibra_finestre': 12, 'calibra_quantile': 99.0,
    'calibra_margine': 0.03, 'soglia_min': 0.62, 'soglia_max': 0.90, 'scala_globale': 0.5,
    'globale': 'meta',
    'vel_max': 24, 'fermo_diff': 4.0, 'moto_conferma': True, 'moto_min': 3, 'salto_px': 3.0,
    'margine_fermo': 0.10, 'calo_ferma': 0.20, 'calo_fermo': 0.25, 'contrasto_min': 8.0,
    'ncc_fermo': 0.60, 'max_zona_cambiata': 0.05, 'max_macchie_ricerca': 8,
    # events
    'clic_entro': 2.0, 'sosta_min': 0.5, 'sosta_max': 8.0, 'raggio_sosta_px': 4.0, 'raggio_clic': 0.06,
    'stampa_al_minuto': 12, 'forme_al_minuto': 6, 'letture_max': 60, 'ocr_tipo': 'min', 'ocr_lato': 128,
}
PAR = dict(PREDEFINITI)

KERNEL_3X3 = np.ones((3, 3), np.uint8)    # 3x3 structuring element shared by every dilation
LATO_RITAGLIO = 32                        # side in px of the crop kept under the last confirmed position
LATO_SEGUE = 48                           # side in px of the patch used to test for a drag
MEZZA_BANDA = 1.5                         # half width in px of an arm band of the crosshair test
CELLA_MIN_PIXEL = 6                       # changed pixels needed to mark a cell as changed
DISTANZA_MIN_GEMELLE = 6                  # twin blobs closer than this (px) are the same spot, not a move
EPSILON_TEMPO = 1e-9                      # absorbs float error when comparing durations to a threshold
SALTO_CAMPIONE_MAX = 0.25                 # longest gap in seconds that still joins two consecutive samples
LUNGHEZZA_TESTO_MAX = 80                  # longest name kept for an event
MEZZA_ZONA_X = 0.015                      # half width of an event zone, as a fraction of the frame width
MEZZA_ZONA_Y = 0.025                      # half height of an event zone, as a fraction of the frame height


def imposta(valori=None):
    """Reset PAR to the defaults, then apply optional overrides (used by tuning runs and tests).

    `valori` maps parameter names to values, cast to the type of the default; an unknown name raises
    KeyError. Booleans accept '1', 'true' or 'si' (any case) as true.
    """
    PAR.clear()
    PAR.update(PREDEFINITI)
    for nome, valore in (valori or {}).items():
        if nome not in PREDEFINITI:
            raise KeyError('parametro sconosciuto: ' + nome)
        if isinstance(PREDEFINITI[nome], bool):
            PAR[nome] = str(valore).lower() in ('1', 'true', 'si')
        else:
            PAR[nome] = type(PREDEFINITI[nome])(valore)


# ---------------------------------------------------------------- system measurements
def rss_mb():
    """Peak memory of this process in MB (resource on POSIX, GetProcessMemoryInfo on Windows).

    Returns None when the platform gives no figure, 0.0 when the Windows call fails.
    """
    if sys.platform != "win32":
        try:
            import resource
            valore = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            divisore = 1048576.0 if sys.platform == "darwin" else 1024.0
            return valore / divisore
        except (ImportError, AttributeError, OSError):
            return None

    class ContatoriMemoria(ctypes.Structure):
        """Layout of the Win32 PROCESS_MEMORY_COUNTERS structure."""

        _fields_ = [('cb', ctypes.c_ulong), ('PageFaultCount', ctypes.c_ulong),
                    ('PeakWorkingSetSize', ctypes.c_size_t),
                    ('WorkingSetSize', ctypes.c_size_t),
                    ('QuotaPeakPagedPoolUsage', ctypes.c_size_t),
                    ('QuotaPagedPoolUsage', ctypes.c_size_t),
                    ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
                    ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                    ('PagefileUsage', ctypes.c_size_t),
                    ('PeakPagefileUsage', ctypes.c_size_t)]
    try:
        contatori = ContatoriMemoria()
        contatori.cb = ctypes.sizeof(contatori)
        kernel = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(ContatoriMemoria),
                                               ctypes.c_ulong]
        ok = psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(contatori),
                                        contatori.cb)
        return contatori.PeakWorkingSetSize / (1024.0 * 1024.0) if ok else 0.0
    except (AttributeError, OSError):
        return 0.0


# ---------------------------------------------------------------- video reading
def riduci(img):
    """Shrink a gray frame to at most PAR['larghezza_max'] px wide, keeping the aspect ratio.

    Uses INTER_LINEAR: INTER_AREA would wipe out the thin crosshair cursor. Narrower frames are
    returned untouched.
    """
    h, w = img.shape[:2]
    if w <= PAR['larghezza_max']:
        return img
    fattore = PAR['larghezza_max'] / float(w)
    nuova_altezza = max(1, int(round(h * fattore)))
    return cv2.resize(img, (PAR['larghezza_max'], nuova_altezza), interpolation=cv2.INTER_LINEAR)


def info_video(video):
    """Open the video and read its metadata.

    Returns (native width, native height, fps, duration in s); an fps outside 1-240 is taken as 25.
    Raises RuntimeError when the video cannot be opened.
    """
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        raise RuntimeError('video non apribile')
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    n_fotogrammi = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
    cap.release()
    if not (1.0 <= fps <= 240.0):
        fps = 25.0
    return w, h, fps, n_fotogrammi / fps


def fotogrammi(video, fino=None, da=0.0):
    """Yield (t, gray frame) sampled at about PAR['fps'] between `da` and `fino` seconds.

    Frames are decoded one at a time and never kept in memory; each one goes through riduci().
    Raises RuntimeError when the video cannot be opened.
    """
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        raise RuntimeError('video non apribile')
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    if not (1.0 <= fps <= 240.0):
        fps = 25.0
    passo = 1.0 / PAR['fps']
    prossimo = da
    indice = 0
    try:
        while True:
            if not cap.grab():
                break
            t = indice / fps
            indice += 1
            if fino is not None and t > fino:
                break
            if t < da - 1e-6 or t + 1e-6 < prossimo:
                continue
            ok, frame = cap.retrieve()
            if not ok:
                break
            prossimo += passo
            if prossimo < t:
                prossimo = t + passo
            yield round(t, 3), riduci(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
    finally:
        cap.release()


# ---------------------------------------------------------------- geometry and crops
def patch_a(img, cx, cy, lato):
    """Cut a `lato` x `lato` square centred on (cx, cy) out of `img`.

    Returns a view of the image, or None when the square would leave the frame.
    """
    h, w = img.shape[:2]
    x0 = int(round(cx)) - lato // 2
    y0 = int(round(cy)) - lato // 2
    if x0 < 0 or y0 < 0 or x0 + lato > w or y0 + lato > h:
        return None
    return img[y0:y0 + lato, x0:x0 + lato]


def distanza_px(a, b):
    """Euclidean distance in px between two (x, y) points."""
    return math.hypot(a[0] - b[0], a[1] - b[1])


def macchie_mobili(prec, g, con_maschere=False):
    """Find the blobs that changed between two consecutive gray frames, 6-30 px on the long side.

    The difference mask (PAR['soglia_diff']) is dilated by 3x3 so that edges merge. Returns
    ([(cx, cy, x, y, w, h)], share of changed pixels); with `con_maschere` every blob also carries
    its boolean silhouette as last item.
    """
    mask = (cv2.absdiff(prec, g) >= PAR['soglia_diff']).astype(np.uint8)
    cambiati = int(mask.sum())
    if cambiati == 0:
        return [], 0.0
    mask = cv2.dilate(mask, KERNEL_3X3)
    n_etichette, etichette, stat, centri = cv2.connectedComponentsWithStats(mask, connectivity=8)
    out = []
    for i in range(1, n_etichette):
        x, y, w, h = (int(valore) for valore in stat[i][:4])
        # -2: the dilation adds 1 px per side
        if PAR['lato_min'] <= max(w, h) - 2 <= PAR['lato_max']:
            macchia = (float(centri[i][0]), float(centri[i][1]), x, y, w, h)
            if con_maschere:
                out.append(macchia + (etichette[y:y + h, x:x + w] == i,))
            else:
                out.append(macchia)
    return out, cambiati / float(mask.size)


# ---------------------------------------------------------------- template shape
def primo_piano(img):
    """Boolean mask of the pixels that stand out from the background of a template crop.

    The background level is the median of the 2 px border; pixels differing by at least
    max(40, 35% of the peak difference) are kept, then connected pieces smaller than 15% of the
    biggest one (min 4 px) are dropped.
    """
    bordo = np.concatenate([img[:2].ravel(), img[-2:].ravel(), img[:, :2].ravel(), img[:, -2:].ravel()])
    differenza = np.abs(img.astype(np.float32) - float(np.median(bordo)))
    sagoma = (differenza >= max(40.0, 0.35 * float(differenza.max()))).astype(np.uint8)
    n_etichette, etichette, stat, _ = cv2.connectedComponentsWithStats(sagoma, connectivity=8)
    if n_etichette <= 1:
        return sagoma > 0
    aree = stat[1:, cv2.CC_STAT_AREA]
    # the detached arms of a crosshair stay
    tenute = 1 + np.nonzero(aree >= max(4, 0.15 * aree.max()))[0]
    return np.isin(etichette, tenute)


def _a_croce(sagoma):
    """Tell whether a silhouette is a crosshair: four thin arms from the centre and no mass elsewhere.

    The centre may be empty. At most 20% of the pixels may lie outside the two bands and every arm
    needs at least 35% of its nominal length (counted over the 2-px stroke).
    """
    h, w = sagoma.shape
    if w < 7 or h < 7 or not 0.6 <= w / float(h) <= 1.6:
        return False
    griglia_y, griglia_x = np.mgrid[0:h, 0:w]
    cx = (w - 1) / 2.0
    cy = (h - 1) / 2.0
    fascia_orizzontale = np.abs(griglia_y - cy) <= MEZZA_BANDA
    fascia_verticale = np.abs(griglia_x - cx) <= MEZZA_BANDA
    totale = int(sagoma.sum())
    fuori_dai_bracci = sagoma & ~fascia_orizzontale & ~fascia_verticale
    if totale == 0 or int(fuori_dai_bracci.sum()) > 0.2 * totale:
        return False
    bracci = [sagoma & fascia_orizzontale & (griglia_x < cx - MEZZA_BANDA),
              sagoma & fascia_orizzontale & (griglia_x > cx + MEZZA_BANDA),
              sagoma & fascia_verticale & (griglia_y < cy - MEZZA_BANDA),
              sagoma & fascia_verticale & (griglia_y > cy + MEZZA_BANDA)]
    lunghezza = [w / 2.0 - MEZZA_BANDA, w / 2.0 - MEZZA_BANDA,
                 h / 2.0 - MEZZA_BANDA, h / 2.0 - MEZZA_BANDA]
    # the band is 3 px wide, but the strokes are only 1-2 px thick
    return all(int(braccio.sum()) >= 0.35 * lungh * 2 for braccio, lungh in zip(bracci, lunghezza))


def descrivi_modello(img):
    """Classify a template crop and locate its hotspot.

    The shape comes from the geometry of the foreground: 'testo' (tall and narrow), 'croce',
    'freccia' (mass in the upper left) or 'mano'. Returns (shape, (hot_x, hot_y)) with the hotspot
    measured from the centre of the crop; fewer than 6 foreground pixels give 'freccia' at the centre.
    """
    lato = img.shape[0]
    ordinate, ascisse = np.nonzero(primo_piano(img))
    centro = (0.0, 0.0)
    if len(ascisse) < 6:
        return 'freccia', centro
    x0, x1 = int(ascisse.min()), int(ascisse.max())
    y0, y1 = int(ordinate.min()), int(ordinate.max())
    w = x1 - x0 + 1
    h = y1 - y0 + 1
    massa_x = (ascisse.mean() - x0) / max(1.0, w - 1.0)
    massa_y = (ordinate.mean() - y0) / max(1.0, h - 1.0)
    sagoma = np.zeros((h, w), bool)
    sagoma[ordinate - y0, ascisse - x0] = True
    if h >= 2 * w and h >= 8:
        forma = 'testo'                       # tall and narrow
    elif _a_croce(sagoma):
        forma = 'croce'
    elif massa_x < 0.46 and massa_y < 0.62:
        forma = 'freccia'                     # mass in the upper left
    else:
        forma = 'mano'
    if forma in ('testo', 'croce'):
        hx = (x0 + x1 + 1) / 2.0
        hy = (y0 + y1 + 1) / 2.0
    elif forma == 'mano':
        hx = x0 + 0.35 * w
        hy = y0 + 0.5                         # the tip of the index finger, at the top
    else:
        hx = x0 + 0.5
        hy = y0 + 0.5                         # arrow: the tip is the upper left corner
    return forma, (hx - lato / 2.0, hy - lato / 2.0)


def classifica_forma(img):
    """Name the cursor shape ('freccia', 'testo', 'croce' or 'mano') of a template crop."""
    return descrivi_modello(img)[0]


# ---------------------------------------------------------------- template learning
def _ncc_migliore(patch, modello):
    """Best TM_CCOEFF_NORMED match of `modello` inside `patch`, in both polarities.

    The negative of the template correlates with the opposite sign, so the strongest of the maximum
    and the negated minimum wins. Returns (score, x, y, negative).
    """
    risposta = cv2.matchTemplate(patch, modello, cv2.TM_CCOEFF_NORMED)
    minimo, massimo, pos_minimo, pos_massimo = cv2.minMaxLoc(risposta)
    massimo = float(massimo) if math.isfinite(massimo) else 0.0
    minimo = float(minimo) if math.isfinite(minimo) else 0.0
    if massimo >= -minimo:
        return (massimo, pos_massimo[0], pos_massimo[1], False)
    return (-minimo, pos_minimo[0], pos_minimo[1], True)


def _normalizza(righe):
    """Centre every row on zero mean and scale it to unit length, so a dot product is an NCC.

    Flat rows (norm below 1e-3) are divided by infinity and become the null vector.
    """
    vettori = righe.astype(np.float32)
    vettori -= vettori.mean(axis=1, keepdims=True)
    norme = np.linalg.norm(vettori, axis=1, keepdims=True)
    norme[norme < 1e-3] = np.inf       # flat crops: null vector
    return vettori / norme


def _recluta(patches, liberi, modello):
    """Align the free crops that resemble `modello` by sliding it inside the 48x48 patch.

    A crop joins when its best NCC reaches PAR['ncc_gruppo']; matches found in negative polarity
    are inverted. Returns [(crop index, aligned crop in the polarity of the template)].
    """
    lato = modello.shape[0]
    membri = []
    for j in liberi:
        punteggio, x, y, negativo = _ncc_migliore(patches[j], modello)
        if punteggio >= PAR['ncc_gruppo']:
            ritaglio = patches[j][y:y + lato, x:x + lato]
            membri.append((j, 255 - ritaglio if negativo else ritaglio))
    return membri


def scegli_lato(lati):
    """Pick the side of the template crops.

    PAR['lato_modello'] wins when set; otherwise the median blob side plus 8, rounded to an even
    number and clamped to 16-32 (12 px is assumed when there are no blobs).
    """
    if PAR['lato_modello']:
        return int(PAR['lato_modello'])
    mediana = float(np.median(lati)) if len(lati) else 12.0
    return int(min(32, max(16, 2 * round((mediana + 8) / 2.0))))


def celle_cambiate(prec, g, diff=None, soglia=None):
    """Boolean map of the grid cells (PAR['cella'] px square) where the frame changed.

    A cell counts as changed with at least 6 pixels above `soglia` (default PAR['soglia_occ']).
    That is low on purpose: webcam or video noise stays below 25 levels, while a digital screen
    holds still. `diff` can pass a precomputed absolute difference.
    """
    lato_cella = PAR['cella']
    if diff is None:
        differenza = cv2.absdiff(prec, g)
    else:
        differenza = diff
    if soglia is None:
        livello = PAR['soglia_occ']
    else:
        livello = soglia
    cambiata = differenza >= livello
    h = cambiata.shape[0] // lato_cella
    w = cambiata.shape[1] // lato_cella
    conteggi = cambiata[:h * lato_cella, :w * lato_cella].reshape(h, lato_cella, w, lato_cella)
    return conteggi.sum(axis=(1, 3)) >= CELLA_MIN_PIXEL


def _ncc_uguali(a, b):
    """Correlation coefficient between two crops of the same size (0.0 when either is flat)."""
    x = a.astype(np.float32).ravel()
    y = b.astype(np.float32).ravel()
    x -= x.mean()
    y -= y.mean()
    denominatore = float(np.linalg.norm(x) * np.linalg.norm(y))
    return float(x.dot(y) / denominatore) if denominatore > 1e-6 else 0.0


def _spostamenti(prec, g, macchie):
    """Pair the blobs that are one object seen before and after a move.

    Two blobs qualify when their sizes differ by at most 2 px, they are at least 6 px apart, their
    silhouettes overlap by 40% or more, and the crop taken where the object is present matches (new
    blob seen in `g`, old blob seen in `prec`; NCC >= 0.5). A lone interface change has no twin.
    Returns [(new blob, old blob)].
    """
    out = []
    for i in range(len(macchie)):
        for j in range(i + 1, len(macchie)):
            macchia_a, macchia_b = macchie[i], macchie[j]
            if (distanza_px(macchia_a[:2], macchia_b[:2]) < DISTANZA_MIN_GEMELLE
                    or abs(macchia_a[4] - macchia_b[4]) > 2 or abs(macchia_a[5] - macchia_b[5]) > 2):
                continue
            sagoma_a, sagoma_b = macchia_a[6], macchia_b[6]
            alto = max(sagoma_a.shape[0], sagoma_b.shape[0])
            largo = max(sagoma_a.shape[1], sagoma_b.shape[1])
            pieno_a = np.zeros((alto, largo), bool)
            pieno_b = np.zeros((alto, largo), bool)
            pieno_a[:sagoma_a.shape[0], :sagoma_a.shape[1]] = sagoma_a
            pieno_b[:sagoma_b.shape[0], :sagoma_b.shape[1]] = sagoma_b
            if (pieno_a & pieno_b).sum() < 0.4 * max(1, (pieno_a | pieno_b).sum()):
                continue
            lato = max(macchia_a[4], macchia_a[5], macchia_b[4], macchia_b[5]) + 4
            a_in_g = patch_a(g, macchia_a[0], macchia_a[1], lato)
            b_in_prec = patch_a(prec, macchia_b[0], macchia_b[1], lato)
            b_in_g = patch_a(g, macchia_b[0], macchia_b[1], lato)
            a_in_prec = patch_a(prec, macchia_a[0], macchia_a[1], lato)
            if a_in_g is None or b_in_prec is None or b_in_g is None or a_in_prec is None:
                continue
            # a is the new blob (b the old one), or the other way round
            ncc_a_nuova = _ncc_uguali(a_in_g, b_in_prec)
            ncc_b_nuova = _ncc_uguali(b_in_g, a_in_prec)
            if max(ncc_a_nuova, ncc_b_nuova) >= 0.5:
                if ncc_a_nuova >= ncc_b_nuova:
                    out.append((macchia_a, macchia_b))
                else:
                    out.append((macchia_b, macchia_a))
    return out


class Apprendista:
    """Collects cursor candidates frame after frame and derives the templates from them.

    Candidates: moving blobs (6-30 px, threshold 25) that have a twin (the object that moved);
    fixed positions (>= 15% of the pairs) are excluded. Crops of 32x32 around the centroid are
    grouped with NCC >= 0.7 (both polarities); the 4 largest groups are kept; a template is the
    median of its aligned crops.
    """

    def __init__(self):
        """Start with no candidates, no previous frame and no occupancy statistics."""
        self.cand = []
        self.celle = {}
        self.coppie = 0
        self.prec = None
        self.larghezza = 0
        self.occupazione = None
        # cells that are always moving (webcam face, video, animated view)
        self.occupate = None

    def aggiungi(self, g):
        """Feed the next gray frame.

        Updates the per-cell change counters and, when the pair of frames holds few small blobs,
        stores a candidate crop for every blob seen in both frames. The first frame only primes `prec`.
        """
        lato_patch = PAR['patch']
        prec = self.prec
        self.prec = g
        self.larghezza = g.shape[1]
        if prec is None:
            return
        self.coppie += 1
        cambiate = celle_cambiate(prec, g)
        if self.occupazione is None:
            self.occupazione = cambiate.astype(np.int32)
        else:
            self.occupazione = self.occupazione + cambiate
        macchie, frazione = macchie_mobili(prec, g, con_maschere=True)
        if not macchie or len(macchie) > PAR['max_macchie_coppia'] or frazione > PAR['max_frazione_cambio']:
            return
        if PAR['coppie_cursore']:
            # a moving object: two twin blobs, the old spot and the new one
            gemelle = _spostamenti(prec, g, macchie)
            esempi = [(nuova, g) for nuova, _ in gemelle] + [(vecchia, prec) for _, vecchia in gemelle]
        else:
            # test mode: every blob, seen in both frames
            esempi = [(macchia, g) for macchia in macchie] + [(macchia, prec) for macchia in macchie]
        for macchia, immagine in esempi:
            cx, cy, x, y, w, h = macchia[:6]
            ritaglio = patch_a(immagine, cx, cy, lato_patch)
            if ritaglio is None:
                continue
            cella = (int(cx // 4), int(cy // 4))
            self.celle.setdefault(cella, set()).add(self.coppie)
            self.cand.append((ritaglio.copy(), cx, cy, max(w, h) - 2, cella))

    def modelli(self):
        """Cluster the stored candidates into templates, largest group first.

        Drops candidates sitting on fixed spots or in always-moving cells, then repeatedly takes the
        candidate with most look-alikes as seed, aligns the group twice and keeps its median.
        Groups that are small, always in the same corner, flat or without a compact sharp
        silhouette are rejected. Returns a list of dicts {'img', 'forma', 'hot', 'n', 'scalati', 'mask'?}.
        """
        celle = self.celle
        limite = max(1, int(math.ceil(self.coppie * PAR['quota_fissa'])))

        def fissa(cella):
            """Is this cell, with its 8 neighbours, a spot that changes in too many pairs?"""
            visite = sum(len(celle.get((cella[0] + i, cella[1] + j), ()))
                         for i in (-1, 0, 1) for j in (-1, 0, 1))
            return visite >= limite

        if self.occupazione is None:
            self.occupate = None
        else:
            self.occupate = self.occupazione >= PAR['quota_occupata'] * max(1, self.coppie)
        lato_cella = PAR['cella']

        def occupata(cx, cy):
            """Does the point fall in a cell that is always moving?"""
            if self.occupate is None:
                return False
            riga = min(self.occupate.shape[0] - 1, int(cy // lato_cella))
            colonna = min(self.occupate.shape[1] - 1, int(cx // lato_cella))
            return bool(self.occupate[riga, colonna])

        cand = [c for c in self.cand if not fissa(c[4]) and not occupata(c[1], c[2])]
        if len(cand) < PAR['min_membri']:
            return []
        if len(cand) > PAR['max_candidati']:
            scelti = np.random.default_rng(0).choice(len(cand), PAR['max_candidati'], replace=False)
            cand = [cand[i] for i in sorted(scelti)]
        lato_patch = PAR['patch']
        patches = np.stack([c[0] for c in cand])
        posizioni = np.array([[c[1], c[2]] for c in cand], np.float32)
        lato = scegli_lato([c[3] for c in cand])
        scarto = (lato_patch - lato) // 2
        centrali = patches[:, scarto:scarto + lato, scarto:scarto + lato]
        righe_norm = _normalizza(centrali.reshape(len(cand), -1))
        similarita = righe_norm @ righe_norm.T
        vicini = np.abs(similarita) >= PAR['ncc_gruppo']
        del similarita
        usati = np.zeros(len(cand), bool)
        modelli = []
        estensione_min = max(float(PAR['estensione_min']), PAR['estensione_rel'] * self.larghezza)
        for _ in range(PAR['max_modelli']):
            conta = (vicini & ~usati[None, :]).sum(axis=1)
            conta[usati] = -1
            seme = int(np.argmax(conta))
            if conta[seme] < PAR['min_membri']:
                break
            mediana = patches[seme][scarto:scarto + lato, scarto:scarto + lato].copy()
            membri = []
            # align, take the median, align again
            for _giro in range(2):
                membri = _recluta(patches, np.nonzero(~usati)[0], mediana)
                if len(membri) < PAR['min_membri']:
                    break
                mediana = np.median(np.stack([ritaglio for _, ritaglio in membri]), axis=0).astype(np.uint8)
            usati[seme] = True                # the seed is never tried again
            if len(membri) < PAR['min_membri']:
                continue
            for j, _ in membri:
                usati[j] = True
            punti = posizioni[[j for j, _ in membri]]
            estensione = max(float(np.ptp(punti[:, 0])), float(np.ptp(punti[:, 1])))
            # always in the same corner (a face, an icon) or flat: not a cursor
            if estensione < estensione_min or float(mediana.std()) < 10.0:
                continue
            sagoma = primo_piano(mediana)
            quota_sagoma = float(sagoma.mean())
            tocca_bordo = bool(sagoma[0].any() or sagoma[-1].any()
                               or sagoma[:, 0].any() or sagoma[:, -1].any())
            # no compact sharp silhouette (a gradient, the edge of a surface): not a cursor
            if (int(sagoma.sum()) < 12 or quota_sagoma > 0.45 or tocca_bordo
                    or int(mediana.max()) - int(mediana.min()) < 60):
                continue
            forma, hot = descrivi_modello(mediana)
            modello = {'img': mediana, 'forma': forma, 'hot': hot, 'n': len(membri), 'scalati': {}}
            if PAR['maschera']:
                # compare on the cursor pixels (and a halo) only: the background changes, the cursor does not
                sagoma = primo_piano(mediana).astype(np.uint8)
                alone = cv2.dilate(sagoma, KERNEL_3X3, iterations=2 if forma == 'croce' else 1)
                if 12 <= int(alone.sum()) < 0.8 * alone.size:
                    modello['mask'] = alone * 255
            modelli.append(modello)
        modelli.sort(key=lambda modello: -modello['n'])
        return modelli


def impara_modelli(frames):
    """Learn the cursor templates from an iterable of (t, gray frame); returns the list of templates."""
    apprendista = Apprendista()
    for _, g in frames:
        apprendista.aggiungi(g)
    return apprendista.modelli()


def impara_da_video(video, durata_video=None):
    """Learn the templates from the first 60 s of the video, extending the window when needed.

    If there are not at least two templates with `membri_ok` examples, learning goes on to 120 and
    180 s inside one single read of the video (candidates accumulate: more examples, surer templates).
    Returns (templates, map of the cells that are always moving).
    """
    apprendista = Apprendista()
    modelli = []
    fine_blocco = PAR['addestra_s']
    ultimo = 0.0
    for t, g in fotogrammi(video, fino=PAR['addestra_s'] * PAR['addestra_finestre']):
        apprendista.aggiungi(g)
        ultimo = t
        if t >= fine_blocco:
            modelli = apprendista.modelli()
            fine_blocco += PAR['addestra_s']
            if len(modelli) >= 2 and modelli[1]['n'] >= PAR['membri_ok']:
                # two well documented cursor shapes: enough
                return modelli, apprendista.occupate
    modelli = apprendista.modelli()
    return modelli, apprendista.occupate


# ---------------------------------------------------------------- template search
def _modello_scalato(m, scala):
    """Return the template image of `m` resized by `scala`, caching each scale in m['scalati'].

    Scale 1.0 returns the original image; the resized side is at least 4 px.
    """
    if scala == 1.0:
        return m['img']
    if scala not in m['scalati']:
        lato = max(4, int(round(m['img'].shape[0] * scala)))
        m['scalati'][scala] = cv2.resize(m['img'], (lato, lato), interpolation=cv2.INTER_LINEAR)
    return m['scalati'][scala]


def candidati(img, modelli, ox=0.0, oy=0.0, scala=1.0):
    """Find the NCC peaks of every template in `img`, best first.

    Each template yields up to two peaks, one per polarity, kept only when the matched area has
    enough contrast (flat black or white areas are not a cursor). `ox`, `oy` is the offset of `img`
    in the full frame and `scala` the factor `img` was shrunk by.
    Returns [(score, ax, ay, template)] with (ax, ay) = template centre in frame px.
    """
    out = []
    for modello in modelli:
        scalato = _modello_scalato(modello, scala)
        th, tw = scalato.shape
        if img.shape[0] < th or img.shape[1] < tw:
            continue
        if 'mask' in modello and scala == 1.0:
            risposta = cv2.matchTemplate(img, scalato, cv2.TM_CCOEFF_NORMED, mask=modello['mask'])
        else:
            risposta = cv2.matchTemplate(img, scalato, cv2.TM_CCOEFF_NORMED)
        minimo, massimo, pos_minimo, pos_massimo = cv2.minMaxLoc(risposta)
        for valore, pos in ((massimo, pos_massimo), (-minimo, pos_minimo)):
            if not math.isfinite(valore):
                continue
            zona = img[pos[1]:pos[1] + th, pos[0]:pos[0] + tw]
            # flat areas (black, white) are not a cursor
            if float(zona.std()) >= PAR['contrasto_min']:
                out.append((float(min(1.0, valore)), (ox + pos[0] + tw / 2.0) / scala,
                            (oy + pos[1] + th / 2.0) / scala, modello))
    out.sort(key=lambda cand: -cand[0])
    return out


def soglia_di(m, base):
    """NCC threshold for a moving cursor: the one calibrated on the video for template `m`, else `base`."""
    return m.get('soglia', base)


def calibra_soglie(frames, modelli, semina=0):
    """Set m['soglia'] on every template from random 96x96 windows of the video.

    The threshold sits above the similarity the template reaches on windows where the cursor usually
    is absent (webcam faces, icons, grids): the 99th percentile of the best scores plus a margin,
    clamped between soglia_min and soglia_max. `frames` is a list of gray frames, `semina` the RNG seed.
    """
    rng = np.random.default_rng(semina)
    lato_finestra = PAR['finestra']
    punteggi = [[] for _ in modelli]
    for g in frames:
        h, w = g.shape
        if h < lato_finestra or w < lato_finestra:
            continue
        for _ in range(PAR['calibra_finestre']):
            x0 = int(rng.integers(0, w - lato_finestra + 1))
            y0 = int(rng.integers(0, h - lato_finestra + 1))
            finestra = g[y0:y0 + lato_finestra, x0:x0 + lato_finestra]
            for i, modello in enumerate(modelli):
                trovati = candidati(finestra, [modello])
                punteggi[i].append(trovati[0][0] if trovati else 0.0)
    for modello, valori in zip(modelli, punteggi):
        livello = float(np.percentile(valori, PAR['calibra_quantile'])) if valori else 0.0
        modello['soglia'] = float(np.clip(livello + PAR['calibra_margine'],
                                          PAR['soglia_min'], PAR['soglia_max']))


def campiona_fotogrammi(video, quanti, durata):
    """Grab `quanti` gray frames at regular intervals over the whole video.

    Seeks with POS_MSEC instead of decoding everything, so it stays cheap on long videos; frames
    that fail to decode are skipped. `durata` is the video length in s.
    """
    cap = cv2.VideoCapture(video)
    out = []
    try:
        for k in range(quanti):
            secondi = 0.5 + k * (max(1.0, durata) - 1.0) / max(1, quanti - 1)
            cap.set(cv2.CAP_PROP_POS_MSEC, secondi * 1000.0)
            ok, frame = cap.read()
            if ok:
                out.append(riduci(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)))
    finally:
        cap.release()
    return out


def cerca_locale(g, modelli, previsto):
    """Candidates inside the 96x96 window centred on the predicted position (x, y)."""
    h, w = g.shape
    mezza = PAR['finestra'] // 2
    x0 = int(max(0, min(w - 1, round(previsto[0]) - mezza)))
    y0 = int(max(0, min(h - 1, round(previsto[1]) - mezza)))
    return candidati(g[y0:y0 + 2 * mezza, x0:x0 + 2 * mezza], modelli, x0, y0)


def cerca_globale(g, modelli, cambio=None):
    """Candidates over the whole frame.

    By default the frame is halved (PAR['scala_globale']); with globale=regioni and a change mask
    `cambio`, only the 6 largest changed regions (grown by one template side) are searched at full size.
    """
    if PAR['globale'] == 'regioni' and cambio is not None:
        out = []
        lato = int(max(m['img'].shape[0] for m in modelli))
        zone = cv2.dilate(cambio, KERNEL_3X3, iterations=lato // 2)
        n_etichette, _, stat, _ = cv2.connectedComponentsWithStats(zone, connectivity=8)
        per_area = sorted(range(1, n_etichette), key=lambda i: -stat[i][cv2.CC_STAT_AREA])
        for i in per_area[:6]:
            x, y, w, h = (int(valore) for valore in stat[i][:4])
            x0 = max(0, x - lato)
            y0 = max(0, y - lato)
            out += candidati(g[y0:min(g.shape[0], y + h + lato), x0:min(g.shape[1], x + w + lato)],
                             modelli, x0, y0)
        out.sort(key=lambda cand: -cand[0])
        return out
    scala = PAR['scala_globale']
    if scala >= 1.0:
        return candidati(g, modelli)
    piccolo = cv2.resize(g, (max(1, int(g.shape[1] * scala)), max(1, int(g.shape[0] * scala))),
                         interpolation=cv2.INTER_LINEAR)
    return candidati(piccolo, modelli, scala=scala)


# ---------------------------------------------------------------- tracking
def _ncc_anello(a, b, anello):
    """Correlation coefficient between two patches restricted to the boolean ring mask `anello`."""
    x = a[anello].astype(np.float32)
    y = b[anello].astype(np.float32)
    x -= x.mean()
    y -= y.mean()
    denominatore = float(np.linalg.norm(x) * np.linalg.norm(y))
    return float(x.dot(y) / denominatore) if denominatore > 1e-6 else 0.0


class Inseguitore:
    """Follows the cursor frame by frame, keeping only the previous frame in memory.

    For every frame it gathers candidates (NCC score, position, template):
      - still: inside the 96x96 window around the predicted position, within 3 px of the last position;
      - moving: in the same window but elsewhere, or in the zones where the frame changed
        (compact blobs of 6-30 px). A moving candidate counts only if something changed around it.
    The best moving candidate wins, unless the still one beats it by more than `margine_fermo`:
    so a static icon that resembles the cursor does not hold the tracker while the real cursor moves.
    If nobody passes the threshold: still (the crop under the last position is unchanged), then a
    search over the whole frame halved (only if the frame changed), then the nearest moving blob
    (only a search hint, never "found").
    """

    def __init__(self, modelli, occupate=None):
        """Create a tracker for the given templates and the map of always-moving cells (or None)."""
        self.modelli = modelli
        self.prec = None
        self.occupate = occupate
        # last CONFIRMED position: {'ax', 'ay', 'modello'}
        self.ultimo = None
        # 32x32 crop under the last confirmed position
        self.crop = None
        # was the previous sample confirmed?
        self.prec_trovato = False
        # hint from a moving blob: never "found"
        self.seme = None
        self.vel = (0.0, 0.0)
        self.anello = np.ones((LATO_SEGUE, LATO_SEGUE), bool)
        self.anello[12:36, 12:36] = False
        self.lato_max = max([m['img'].shape[0] for m in modelli] or [32])
        # latest confirmed scores: the peak tells what the cursor looks like when it is really there
        self.recenti = []
        # moving averages (cell by cell): how little changes (noise) and how much changes;
        # a webcam or a video shows up halfway through the footage
        self.occ = None
        self.occ25 = None

    def _mosso(self, cambio, ax, ay):
        """Did pixels change around the candidate? A cursor that has just arrived there changed them.

        Counts the changed pixels of the mask `cambio` in a 28 px box and compares with PAR['moto_min'].
        """
        if cambio is None:
            return False
        raggio = 14
        h, w = cambio.shape
        x0 = max(0, int(ax) - raggio)
        y0 = max(0, int(ay) - raggio)
        return int(cambio[y0:min(h, int(ay) + raggio), x0:min(w, int(ax) + raggio)].sum()) >= PAR['moto_min']

    def _occupata(self, ax, ay):
        """Does the point lie in an always-moving zone (webcam, video)? A new cursor is not accepted there.

        Uses the map from the learning stage and the live moving averages `occ` and `occ25`.
        """
        lato_cella = PAR['cella']
        if self.occupate is not None:
            riga = min(self.occupate.shape[0] - 1, int(ay // lato_cella))
            colonna = min(self.occupate.shape[1] - 1, int(ax // lato_cella))
            if bool(self.occupate[riga, colonna]):
                return True
        if self.occ is None:
            return False
        i = min(self.occ.shape[0] - 1, int(ay // lato_cella))
        j = min(self.occ.shape[1] - 1, int(ax // lato_cella))
        # "noise" zone: changes all the time but only a little (webcam, video);
        # a moving 3D view changes a lot and does not count
        return float(self.occ[i, j]) >= PAR['occ_online'] and float(self.occ25[i, j]) <= PAR['occ_forte']

    def _picco(self):
        """Best confirmed score of the last ~3 s; a sudden collapse means the cursor went away."""
        return max(self.recenti) if self.recenti else 0.0

    def _vicino_all_ultimo(self, ax, ay):
        """Is (ax, ay) within PAR['salto_px'] of the last confirmed position?"""
        if self.ultimo is None:
            return False
        return distanza_px((ax, ay), (self.ultimo['ax'], self.ultimo['ay'])) <= PAR['salto_px']

    def _fermo(self, g):
        """Check whether the cursor is still where it was last confirmed.

        The crop under the last position must equal the previous one AND a template must still
        recognise the cursor there (at least ncc_fermo: a uniform or cursor-less crop is not enough).
        Returns (score, ax, ay, template) or None.
        """
        if self.ultimo is None or self.crop is None:
            return None
        ax, ay = self.ultimo['ax'], self.ultimo['ay']
        ritaglio = patch_a(g, ax, ay, LATO_RITAGLIO)
        if ritaglio is None or float(np.mean(cv2.absdiff(ritaglio, self.crop))) >= PAR['fermo_diff']:
            return None
        h, w = g.shape
        mezzo = self.lato_max // 2 + 6
        x0 = int(max(0, round(ax) - mezzo))
        y0 = int(max(0, round(ay) - mezzo))
        zona = g[y0:min(h, y0 + 2 * mezzo), x0:min(w, x0 + 2 * mezzo)]
        for punteggio, bx, by, modello in candidati(zona, self.modelli, x0, y0):
            if punteggio < max(PAR['ncc_fermo'], self._picco() - PAR['calo_fermo']):
                break
            if distanza_px((bx, by), (ax, ay)) <= 6:
                return (punteggio, ax, ay, modello)
        return None

    def _segue(self, prec, g, p0, p1):
        """Did the content around the cursor move with it (a drag)?

        Compares the ring of a 48 px patch around the old position `p0` in `prec` with the new
        position `p1` in `g` (must match, NCC >= 0.8) and with the old position in `g` (must not, < 0.6).
        """
        prima = patch_a(prec, p0[0], p0[1], LATO_SEGUE)
        dopo_nuova = patch_a(g, p1[0], p1[1], LATO_SEGUE)
        dopo_vecchia = patch_a(g, p0[0], p0[1], LATO_SEGUE)
        if prima is None or dopo_nuova is None or dopo_vecchia is None:
            return False
        if float(prima[self.anello].std()) < 12.0:
            return False                    # flat background: nothing to follow
        return (_ncc_anello(prima, dopo_nuova, self.anello) >= 0.80
                and _ncc_anello(prima, dopo_vecchia, self.anello) < 0.60)

    def _prevista(self):
        """Predicted (x, y) of the cursor: last position plus the last velocity, clamped to vel_max."""
        vm = PAR['vel_max']
        return (self.ultimo['ax'] + max(-vm, min(vm, self.vel[0])),
                self.ultimo['ay'] + max(-vm, min(vm, self.vel[1])))

    def _candidati(self, g, cambio, macchie):
        """Search the window around the hint, the prediction and each changed blob.

        Returns (best still, best moving), each (score, ax, ay, template) or None. Still candidates
        must not fall more than `calo_ferma` below the recent peak; moving ones must beat their
        calibrated threshold, avoid always-moving zones and (for the local window) sit near changed pixels.
        """
        fermo = None
        mobili = []
        centri = [self.seme] if self.seme is not None else []
        if self.ultimo is not None:
            centri = centri + [self._prevista()]
        for centro in centri:
            for punteggio, ax, ay, modello in cerca_locale(g, self.modelli, centro):
                if punteggio < PAR['ncc_ferma']:
                    break
                if self._vicino_all_ultimo(ax, ay):
                    # the cursor is where it was: the lower threshold is enough,
                    # but not a collapse from the peak
                    tiene_picco = punteggio >= self._picco() - PAR['calo_ferma']
                    if tiene_picco and (fermo is None or punteggio > fermo[0]):
                        fermo = (punteggio, ax, ay, modello)
                elif (punteggio >= soglia_di(modello, PAR['ncc_locale']) and not self._occupata(ax, ay)
                        and (not PAR['moto_conferma'] or self._mosso(cambio, ax, ay))):
                    # a cursor that moves needs a clear confirmation
                    mobili.append((punteggio, ax, ay, modello))
        h, w = g.shape
        mezzo = self.lato_max // 2 + 12
        # search the changed zones at full resolution
        for cx, cy, bx, by, bw, bh in macchie:
            x0 = int(max(0, min(w - 1, round(cx) - mezzo)))
            y0 = int(max(0, min(h - 1, round(cy) - mezzo)))
            zona = g[y0:y0 + 2 * mezzo, x0:x0 + 2 * mezzo]
            for punteggio, ax, ay, modello in candidati(zona, self.modelli, x0, y0):
                if punteggio < PAR['ncc_ferma']:
                    break
                if self._vicino_all_ultimo(ax, ay):
                    tiene_picco = punteggio >= self._picco() - PAR['calo_ferma']
                    if tiene_picco and (fermo is None or punteggio > fermo[0]):
                        fermo = (punteggio, ax, ay, modello)
                elif punteggio >= soglia_di(modello, PAR['ncc_globale']) and not self._occupata(ax, ay):
                    mobili.append((punteggio, ax, ay, modello))
        migliore_mobile = max(mobili, key=lambda cand: cand[0]) if mobili else None
        return fermo, migliore_mobile

    def passo(self, t, g):
        """Process the next gray frame `g` taken at time `t` and return its track sample.

        The sample is a dict with t, x, y (frame fractions), px, py (hotspot px), ax, ay (template
        centre), forma, conf, fonte (locale, fermo, globale, macchia or persa), cambio (compact change
        next to a still cursor), segue (content dragged along) and ferma (cursor did not move).
        """
        h, w = g.shape
        prec = self.prec
        self.prec = g
        diff = cv2.absdiff(prec, g) if prec is not None else None
        cambio = (diff >= PAR['soglia_diff']).astype(np.uint8) if prec is not None else None
        if diff is not None:
            cambiate = celle_cambiate(prec, g, diff).astype(np.float32)
            cambiate_forte = celle_cambiate(prec, g, diff, PAR['soglia_diff']).astype(np.float32)
            alfa = PAR['occ_alpha']
            if self.occ is None:
                self.occ = cambiate
            else:
                self.occ = (1.0 - alfa) * self.occ + alfa * cambiate
            if self.occ25 is None:
                self.occ25 = cambiate_forte
            else:
                self.occ25 = (1.0 - alfa) * self.occ25 + alfa * cambiate_forte
            if self.ultimo is not None and self._occupata(self.ultimo['ax'], self.ultimo['ay']):
                # a still cursor does not sit in a zone that changes all the time
                self.ultimo = None
                self.crop = None
                self.seme = None
                self.prec_trovato = False
                self.recenti = []
        mosso = cambio is not None and bool(cambio.any())
        campione = {'t': float(t), 'x': None, 'y': None, 'px': None, 'py': None, 'ax': None, 'ay': None,
                    'forma': '', 'conf': 0.0, 'fonte': 'persa', 'cambio': 0, 'segue': False, 'ferma': False}
        trovato = None
        fonte = ''
        macchie = []
        if mosso and cambio.mean() <= PAR['max_zona_cambiata']:
            macchie, _ = macchie_mobili(prec, g)
            if self.ultimo is not None:
                # only the ones nearest to the last position
                riferimento = (self.ultimo['ax'], self.ultimo['ay'])
                macchie = sorted(macchie, key=lambda macchia: distanza_px(macchia[:2], riferimento))
            macchie = macchie[:PAR['max_macchie_ricerca']]
        if self.modelli:
            fermo, mobile = self._candidati(g, cambio, macchie)
            if mobile is not None and (fermo is None or mobile[0] >= fermo[0] - PAR['margine_fermo']):
                trovato = mobile
                fonte = 'locale' if self._in_finestra(mobile) else 'globale'
            elif fermo is not None:
                trovato = fermo
                fonte = 'locale'
        if trovato is None and self.modelli:
            trovato = self._fermo(g)
            fonte = 'fermo'
        if trovato is None and self.modelli and mosso:
            for cand in cerca_globale(g, self.modelli, cambio):
                if cand[0] < soglia_di(cand[3], PAR['ncc_globale']):
                    continue
                if self._occupata(cand[1], cand[2]):
                    continue
                if self._vicino_all_ultimo(cand[1], cand[2]) or self._mosso(cambio, cand[1], cand[2]):
                    trovato = cand
                    fonte = 'globale'
                    break
        if trovato is not None:
            punteggio, ax, ay, modello = trovato
            consecutivo = self.prec_trovato and self.ultimo is not None
            if consecutivo and fonte != 'globale':
                self.vel = (ax - self.ultimo['ax'], ay - self.ultimo['ay'])
            else:
                self.vel = (0.0, 0.0)
            hx = ax + modello['hot'][0]
            hy = ay + modello['hot'][1]
            campione.update(x=hx / w, y=hy / h, px=hx, py=hy, ax=ax, ay=ay, forma=modello['forma'],
                            conf=round(min(1.0, max(0.0, punteggio)), 4), fonte=fonte)
            if consecutivo and cambio is not None:
                spostamento = distanza_px((ax, ay), (self.ultimo['ax'], self.ultimo['ay']))
                if spostamento <= 2.5:
                    campione['ferma'] = True
                    raggio = int(round(PAR['raggio_clic'] * w))
                    finestra = cambio[max(0, int(ay) - raggio):int(ay) + raggio,
                                      max(0, int(ax) - raggio):int(ax) + raggio]
                    cambiati = int(finestra.sum())
                    if 10 <= cambiati <= 0.3 * finestra.size:
                        # compact change next to the still cursor
                        campione['cambio'] = cambiati
                elif 4.0 <= spostamento <= 150.0 and fonte == 'locale':
                    campione['segue'] = self._segue(prec, g, (self.ultimo['ax'], self.ultimo['ay']), (ax, ay))
            self.ultimo = {'ax': ax, 'ay': ay, 'modello': modello}
            self.seme = None
            self.prec_trovato = True
            # a new lock restarts from its own score
            self.recenti = ([punteggio] if not consecutivo else self.recenti + [punteggio])[-30:]
            self.crop = patch_a(g, ax, ay, LATO_RITAGLIO)
        else:
            self.vel = (0.0, 0.0)
            self.prec_trovato = False
            self.seme = None
            if mosso and self.ultimo is not None:
                # hypothesis: the nearest moving blob
                if not macchie:
                    macchie, _ = macchie_mobili(prec, g)
                if macchie:
                    riferimento = (self.ultimo['ax'], self.ultimo['ay'])
                    vicina = min(macchie,
                                 key=lambda macchia: distanza_px((macchia[0], macchia[1]), riferimento))
                    self.seme = vicina[:2]
                    campione['fonte'] = 'macchia'
        return campione

    def _in_finestra(self, c):
        """Is candidate `c` (score, ax, ay, template) in the local window around the predicted position?"""
        if self.ultimo is None:
            return False
        px, py = self._prevista()
        return abs(c[1] - px) <= PAR['finestra'] / 2 and abs(c[2] - py) <= PAR['finestra'] / 2


def insegui(frames, modelli, occupate=None):
    """Run an Inseguitore over an iterable of (t, gray frame).

    Returns (track = list of samples from Inseguitore.passo, (width, height) of the frames used).
    """
    inseguitore = Inseguitore(modelli, occupate)
    traccia = []
    dim = (0, 0)
    for t, g in frames:
        dim = (g.shape[1], g.shape[0])
        traccia.append(inseguitore.passo(t, g))
    return traccia, dim


# ---------------------------------------------------------------- events
def _evento(tipo, c, durata, forza, stampa='nessuna'):
    """Build one event dict in the shared schema from the track sample `c`.

    The zone is a small box around the point; time, duration, point and strength are rounded and
    `forza` is clamped to 0-1. The id stays empty until estrai_eventi numbers the events.
    """
    x, y = c['x'], c['y']
    return {'id': '', 't': round(float(c['t']), 2), 'durata': round(float(durata), 2),
            'canale': 'cursore', 'tipo': tipo,
            'zona': [round(max(0.0, x - MEZZA_ZONA_X), 4), round(max(0.0, y - MEZZA_ZONA_Y), 4),
                     round(min(1.0, x + MEZZA_ZONA_X), 4), round(min(1.0, y + MEZZA_ZONA_Y), 4)],
            'punto': [round(x, 4), round(y, 4)], 'testo': '',
            'forza': round(min(1.0, max(0.0, forza)), 4), 'stampa': stampa, 'rif': [],
            'dati': {'forma': c.get('forma', '')}}


def _trovato(c):
    """Was the cursor found in track sample `c`?"""
    return c['x'] is not None


def _soste(tr):
    """Dwell events: still from 0.5 to 8 s, consecutive samples within a few px of the first one."""
    out = []
    i = 0
    n = len(tr)
    while i < n:
        primo = tr[i]
        if not _trovato(primo):
            i += 1
            continue
        j = i
        while (j + 1 < n and _trovato(tr[j + 1]) and tr[j + 1]['t'] - tr[j]['t'] <= SALTO_CAMPIONE_MAX
               and distanza_px((tr[j + 1]['px'], tr[j + 1]['py']),
                               (primo['px'], primo['py'])) <= PAR['raggio_sosta_px']):
            j += 1
        durata = tr[j]['t'] - primo['t']
        if durata >= PAR['sosta_min'] - EPSILON_TEMPO:
            x_mediana = float(np.median([c['x'] for c in tr[i:j + 1]]))
            y_mediana = float(np.median([c['y'] for c in tr[i:j + 1]]))
            punto = dict(primo, x=x_mediana, y=y_mediana)
            evento = _evento('sosta', punto, min(durata, PAR['sosta_max']), 0.5 + 0.1 * min(durata, 3.0))
            evento['_fine'] = tr[j]['t']
            out.append(evento)
        i = j + 1
    return out


def _clic(tr, larghezza):
    """Click events.

    A click is a still cursor with a compact change next to it (arrival -> change within 0.6 s:
    full strength), or a shape change between two stable runs close in time (0.3 s) and in space
    (<= 0.06 of the frame width `larghezza`).
    """
    out = []
    arrivo = None
    ultimo = -9.0
    for i, campione in enumerate(tr):
        if campione['ferma']:
            if arrivo is None:
                arrivo = tr[i - 1]['t'] if i else campione['t']
            attesa = campione['t'] - arrivo
            # a change after a long wait is not a click
            if campione['cambio'] > 0 and attesa <= PAR['clic_entro'] and campione['t'] - ultimo >= 0.8:
                out.append(_evento('clic', campione, 0.1, 0.80 if attesa <= 0.6 else 0.60, 'sezione'))
                ultimo = campione['t']
        else:
            arrivo = None
    tratti = []
    i = 0
    while i < len(tr):
        if not _trovato(tr[i]):
            i += 1
            continue
        j = i
        while j + 1 < len(tr) and _trovato(tr[j + 1]) and tr[j + 1]['forma'] == tr[i]['forma']:
            j += 1
        if j > i:
            tratti.append((i, j))       # at least 2 samples: the shape is stable
        i = j + 1
    for (_, fine_prima), (inizio_dopo, _) in zip(tratti, tratti[1:]):
        prima, dopo = tr[fine_prima], tr[inizio_dopo]
        if prima['forma'] == dopo['forma'] or dopo['t'] - prima['t'] > 0.3:
            continue
        if distanza_px((prima['px'], prima['py']), (dopo['px'], dopo['py'])) > PAR['raggio_clic'] * larghezza:
            continue
        if any(abs(evento['t'] - dopo['t']) <= 0.6 for evento in out):
            continue
        out.append(_evento('clic', dopo, 0.1, 0.60, 'sezione'))
    return out


def _trascina(tr):
    """Drag events: the content around the cursor follows it for at least 3 samples (one gap allowed).

    A drag still open at the end of the track is closed there.
    """
    out = []
    i = 0
    n = len(tr)
    while i < n:
        if not tr[i]['segue']:
            i += 1
            continue
        j = i
        k = i + 1
        while k < n and k <= j + 2:
            if tr[k]['segue']:
                j = k
            k += 1
        if j - i + 1 >= 3:
            out.append(_evento('trascina', tr[i], tr[j]['t'] - tr[i]['t'], 0.75, 'sezione'))
        i = j + 1
    return out


def _forme(tr, durata):
    """Shape events: the cursor is a text beam or a hand for at least 0.5 s.

    They are never printed (stampa 'nessuna'): they serve as field evidence or as links for other
    channels. At most max(2, forme_al_minuto per minute of `durata`) events are kept, longest first.
    """
    out = []
    i = 0
    n = len(tr)
    while i < n:
        if not _trovato(tr[i]):
            i += 1
            continue
        j = i
        while (j + 1 < n and _trovato(tr[j + 1]) and tr[j + 1]['forma'] == tr[i]['forma']
               and tr[j + 1]['t'] - tr[j]['t'] <= SALTO_CAMPIONE_MAX):
            j += 1
        lunghezza = tr[j]['t'] - tr[i]['t']
        if tr[i]['forma'] in ('testo', 'mano') and lunghezza >= 0.5 - EPSILON_TEMPO:
            out.append(_evento('forma', tr[i], lunghezza, 0.70))
        i = j + 1
    tetto = max(2, int(round(PAR['forme_al_minuto'] * durata / 60.0)))
    out.sort(key=lambda evento: (-evento['durata'], evento['t']))
    return out[:tetto]


def estrai_eventi(tr, durata=None, larghezza=1280):
    """Turn a cursor track into events: dwell, click, drag and shape, sorted by time.

    Text and print decisions are left to assegna_nomi. A dwell closed by a click is dropped, since
    the click already says it. `durata` defaults to the span of the track; `larghezza` is the frame
    width in px. Events get the ids D1, D2, ... in order.
    """
    if durata is None:
        durata = (tr[-1]['t'] - tr[0]['t']) if len(tr) > 1 else 0.0
    clic = _clic(tr, larghezza)

    def chiusa_da_clic(sosta):
        """Is the dwell ended by a click? Then the click already tells it."""
        return any(sosta['t'] - 0.3 <= c['t'] <= sosta['_fine'] + 0.6
                   and distanza_px(sosta['punto'], c['punto']) <= 0.02 for c in clic)

    soste = [sosta for sosta in _soste(tr) if not chiusa_da_clic(sosta)]
    tutti = soste + clic + _trascina(tr) + _forme(tr, durata)
    out = sorted(tutti, key=lambda evento: (evento['t'], evento['tipo']))
    for i, evento in enumerate(out, 1):
        evento['id'] = 'D%d' % i
        evento.pop('_fine', None)
    return out


# ---------------------------------------------------------------- names (Scena, then OCR)
class LettoreOCR:
    """OCR reader (rapidocr) for a 320x60 crop at native resolution, taken at t-0.2 s.

    Calling the instance with (t, point) returns the text nearest to the point. The engine is loaded
    once, on first use, and the video stays open until chiudi() is called.
    """

    def __init__(self, video):
        """Remember the video path; the capture and the OCR engine are created lazily."""
        self.video = video
        self.motore = None
        self.cap = None
        self.errore = ''

    def __call__(self, t, punto):
        """Read the text next to `punto` (x, y as frame fractions) at time `t`.

        Seeks to t-0.2 s, crops 320x60 px around the point, runs the OCR and keeps the line nearest
        to the point (within 60 px, confidence >= 0.5, cleaned and useful). Returns at most 80
        characters, or '' when nothing qualifies; any failure is stored in self.errore.
        """
        try:
            if self.cap is None:
                self.cap = cv2.VideoCapture(self.video)
            self.cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t - 0.2) * 1000.0)
            ok, frame = self.cap.read()
            if not ok:
                return ''
            h, w = frame.shape[:2]
            cx = int(round(punto[0] * w))
            cy = int(round(punto[1] * h))
            x0 = max(0, min(w - 320, cx - 160))
            y0 = max(0, min(h - 60, cy - 30))
            ritaglio = frame[y0:y0 + 60, x0:x0 + 320]
            if self.motore is None:
                from rapidocr_onnxruntime import RapidOCR
                # no upscaling: little RAM
                self.motore = RapidOCR(intra_op_num_threads=1, inter_op_num_threads=1,
                                       det_limit_type=PAR['ocr_tipo'], det_limit_side_len=PAR['ocr_lato'])
            righe, _ = self.motore(ritaglio, use_cls=False)
            migliore = None
            for riga in righe or []:
                if len(riga) < 3 or float(riga[2]) < 0.5:
                    continue
                testo = pulisci_ocr(str(riga[1]))
                if not utile(testo):
                    continue
                ascisse = [angolo[0] for angolo in riga[0]]
                ordinate = [angolo[1] for angolo in riga[0]]
                dx = max(min(ascisse) - (cx - x0), 0.0, (cx - x0) - max(ascisse))
                dy = max(min(ordinate) - (cy - y0), 0.0, (cy - y0) - max(ordinate))
                distanza = math.hypot(dx, dy)
                if distanza <= 60 and (migliore is None or distanza < migliore[0]):
                    migliore = (distanza, testo)
            return migliore[1][:LUNGHEZZA_TESTO_MAX] if migliore else ''
        except Exception as exc:
            self.errore = str(exc)[:120]
            return ''

    def chiudi(self):
        """Release the video capture and drop the OCR engine."""
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        self.motore = None


def nome_sotto_il_punto(scena, t, punto, margine=0.012, r=0.03):
    """Name of the Scena text that really lies under `punto` (x, y fractions) at time `t`.

    A text with a box matches when the box, grown by `margine` (1.8 times that vertically), contains
    the point; a text without a box matches within `r` of its own point. Readings with no position
    (status bar, dynamic zones) name nothing. Among several matches the smallest box wins (the most
    specific text), then the most confident. Returns '' when nothing matches.
    """
    migliore = None
    for t0, t1, testo, vp, zona, forza in scena.voci:
        if not (t0 <= t <= t1) or vp is None:
            continue
        if zona:
            dentro_x = zona[0] - margine <= punto[0] <= zona[2] + margine
            dentro_y = zona[1] - 1.8 * margine <= punto[1] <= zona[3] + 1.8 * margine
            if not (dentro_x and dentro_y):
                continue
            area = max(1e-6, (zona[2] - zona[0]) * (zona[3] - zona[1]))
        else:
            if math.hypot(punto[0] - vp[0], punto[1] - vp[1]) > r:
                continue
            area = 1.0
        chiave = (round(float(forza), 1), -area)
        if migliore is None or chiave > migliore[0]:
            migliore = (chiave, testo)
    return migliore[1] if migliore else ''


def assegna_nomi(trovati, scena, leggi, durata):
    """Give each event the name of the object under its point and decide which ones get printed.

    The name is the Scena text at t-0.3 s; when missing, `leggi` (the OCR, within a budget, and only
    where it fills the quota) is called. Only clicks, drags and dwells WITH a name are printed, at most
    `stampa_al_minuto` per minute of video (the strongest; ties go to the earliest in the minute).
    Clicks, drags and dwells without a name are dropped: the merge would name them with position-less
    texts (status bar) and make wrong deictic links. Shape events pass through untouched.
    Returns (events kept, number of OCR readings made).
    """
    letture = 0
    tenuti = [evento for evento in trovati if evento['tipo'] == 'forma']
    minuti = {}
    for evento in trovati:
        if evento['tipo'] in ('clic', 'trascina', 'sosta'):
            nome = nome_sotto_il_punto(scena, max(0.0, evento['t'] - 0.3), evento['punto'])
            evento['testo'] = nome[:LUNGHEZZA_TESTO_MAX]
            evento['stampa'] = 'nessuna'
            minuti.setdefault(int(evento['t'] // 60), []).append(evento)
    for minuto, gruppo in sorted(minuti.items()):
        resto = max(1.0, min(60.0, durata - 60.0 * minuto))
        quota = max(3, int(round(PAR['stampa_al_minuto'] * resto / 60.0)))
        ordinati = sorted(gruppo, key=lambda evento: (-evento['forza'], evento['t']))
        stampati = 0
        # first the ones that already have a name from the Scena
        for evento in ordinati:
            if evento['testo'] and stampati < quota:
                evento['stampa'] = 'sezione'
                stampati += 1
        # then the OCR, only to fill the quota
        for evento in ordinati:
            if stampati >= quota or letture >= PAR['letture_max'] or leggi is None:
                break
            if not evento['testo']:
                letture += 1
                evento['testo'] = leggi(evento['t'], evento['punto'])
                if evento['testo']:
                    evento['stampa'] = 'sezione'
                    stampati += 1
        # without a name nothing is emitted: the merge would name it with position-less texts
        tenuti += [evento for evento in ordinati if evento['stampa'] == 'sezione']
    return sorted(tenuti, key=lambda evento: (evento['t'], evento['id'])), letture


# ---------------------------------------------------------------- document and files
def documento(trovati, tr, secondi, modelli=0, durata_video=0.0):
    """Assemble the channel document written to eventi_cursore.json.

    `trovati` are the events, `tr` the track, `secondi` the run time and `modelli` the number of
    templates. The 'stat' block holds the coverage (found samples / all samples) and the count of
    samples per source (locale, fermo, globale, macchia, persa). `durata_video` is accepted but unused.
    """
    fonti = {}
    for campione in tr:
        fonti[campione['fonte']] = fonti.get(campione['fonte'], 0) + 1
    trovati_n = sum(1 for campione in tr if _trovato(campione))
    stat = {'copertura': round(trovati_n / float(max(1, len(tr))), 4), 'secondi': round(float(secondi), 3),
            'campioni': len(tr), 'modelli': int(modelli),
            'locale': fonti.get('locale', 0), 'fermo': fonti.get('fermo', 0),
            'globale': fonti.get('globale', 0), 'macchia': fonti.get('macchia', 0),
            'persa': fonti.get('persa', 0)}
    return {'schema': 1, 'canale': 'cursore', 'lettera': 'D', 'versione': VERSIONE, 'sezione': 'CURSORE',
            'legenda': "CURSORE = clic/sosta/trascinamento sull'oggetto sotto il puntatore",
            'formati': {'clic': 'clic {ogg}', 'sosta': 'su {ogg}', 'trascina': 'trascina {ogg}',
                        'forma': '{forma}'},
            'stat': stat, 'eventi': trovati}


def scrivi_traccia(lavoro, tr):
    """Write cursore.json in the folder `lavoro`: one row [t, x, y, shape, conf] per sample.

    Coordinates are frame fractions rounded to 4 decimals (null where the cursor was not found).
    The file is written to a .tmp name first and then moved into place, so readers never see half a file.
    """
    destinazione = os.path.join(lavoro, 'cursore.json')
    temporaneo = destinazione + '.tmp'
    dati = []
    for campione in tr:
        x = None if campione['x'] is None else round(campione['x'], 4)
        y = None if campione['y'] is None else round(campione['y'], 4)
        dati.append([round(campione['t'], 2), x, y, campione['forma'], round(campione['conf'], 4)])
    with open(temporaneo, 'w', encoding='utf-8', newline='\n') as file_json:
        json.dump(dati, file_json, ensure_ascii=False, separators=(',', ':'))
        file_json.write('\n')
    os.replace(temporaneo, destinazione)


def esegui(video, lavoro, parametri=None):
    """Run the whole channel on `video` and write its files into the folder `lavoro`.

    Learns the templates, tracks the cursor, extracts and names the events, then writes
    eventi_cursore.json (and cursore.json when a track exists). `parametri` overrides PAR through
    imposta(). Prints one CURSORE summary line. Returns {'doc', 'traccia', 'modelli'}.
    """
    avvio = time.perf_counter()
    imposta(parametri)
    os.makedirs(lavoro, exist_ok=True)
    _, _, _, durata_video = info_video(video)
    modelli, occupate = impara_da_video(video, durata_video)
    if modelli and PAR['calibra']:
        calibra_soglie(campiona_fotogrammi(video, PAR['calibra_fotogrammi'], durata_video), modelli)
    t_impara = time.perf_counter() - avvio
    ram_impara = rss_mb()
    t_insegui = t_nomi = 0.0
    ram_insegui = ram_impara
    letture = 0
    if not modelli:
        tr = []
        trovati = []
        print('CURSORE nessun modello automatico del cursore trovato nei primi %d s'
              % int(PAR['addestra_s'] * PAR['addestra_finestre']), flush=True)
    else:
        t1 = time.perf_counter()
        tr, (larghezza, _) = insegui(fotogrammi(video), modelli, occupate)
        t_insegui = time.perf_counter() - t1
        ram_insegui = rss_mb()
        durata = tr[-1]['t'] if tr else 0.0
        trovati = estrai_eventi(tr, durata, larghezza)
        t2 = time.perf_counter()
        lettore = LettoreOCR(video)
        try:
            trovati, letture = assegna_nomi(trovati, eventi.Scena(lavoro), lettore, durata)
        finally:
            lettore.chiudi()
        t_nomi = time.perf_counter() - t2
    trovati.sort(key=lambda evento: (evento['t'], evento['id']))
    doc = documento(trovati, tr, time.perf_counter() - avvio, len(modelli), durata_video)
    doc['stat'].update(secondi_apprendimento=round(t_impara, 1), secondi_inseguimento=round(t_insegui, 1),
                       secondi_nomi=round(t_nomi, 1), letture_ocr=letture,
                       ram_apprendimento_mb=round(ram_impara), ram_inseguimento_mb=round(ram_insegui),
                       ram_picco_mb=round(rss_mb()),
                       zone_occupate=round(float(occupate.mean()), 4) if occupate is not None else 0.0)
    eventi.scrivi(os.path.join(lavoro, 'eventi_cursore.json'), doc)
    if tr:
        scrivi_traccia(lavoro, tr)
    tipi = {}
    for evento in trovati:
        tipi[evento['tipo']] = tipi.get(evento['tipo'], 0) + 1
    stat = doc['stat']
    print('CURSORE copertura=%.4f tempo_s=%.1f (apprendimento %.1f, inseguimento %.1f, nomi %.1f '
          'con %d letture OCR) ram_picco_mb=%.0f (dopo apprendimento %.0f, dopo inseguimento %.0f) '
          'campioni=%d modelli=%s zone_occupate=%.1f%% '
          'fonti=locale %d/fermo %d/globale %d/macchia %d/persa %d eventi=%s' % (
              stat['copertura'], stat['secondi'], t_impara, t_insegui, t_nomi, letture,
              stat['ram_picco_mb'], ram_impara, ram_insegui, stat['campioni'],
              [(m['forma'], m['img'].shape[0], m['n'], round(m.get('soglia', 0), 2)) for m in modelli],
              100.0 * stat['zone_occupate'], stat['locale'], stat['fermo'], stat['globale'],
              stat['macchia'], stat['persa'], tipi), flush=True)
    return {'doc': doc, 'traccia': tr, 'modelli': modelli}


def main():
    """Command line entry point: video + _lavoro folder -> eventi_cursore.json.

    --par takes tuning overrides as name=value,name=value (see PREDEFINITI for the names).
    """
    parser = argparse.ArgumentParser(
        description='Canale D1 cursore: video + cartella _lavoro -> eventi_cursore.json')
    parser.add_argument('video')
    parser.add_argument('lavoro')
    parser.add_argument('--par', default='', help='prove di taratura: nome=valore,nome=valore')
    args = parser.parse_args()
    par = dict(voce.split('=', 1) for voce in args.par.split(',') if '=' in voce)
    esegui(os.path.abspath(args.video), os.path.abspath(args.lavoro), par)


if __name__ == '__main__':
    main()
