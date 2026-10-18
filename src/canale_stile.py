# -*- coding: utf-8 -*-
"""Channel C: colour, contrast and prominence of the texts read by the OCR.

Reads the video plus ocr3.json, ocr1.json and parole.json from the work folder and writes
eventi_stile.json. Everything is deterministic; no word or threshold comes from the examples:
  1. from the OCR (ocr3, ocr1) the VALID texts are chosen: enough letters, and either a word spoken
     in the video or the same text read identically at 2 different instants (no broken or mangled texts);
  2. ONLY the instants with at least one valid text are decoded (decoding forward when that costs
     less than a seek) and the ink and background colours are measured;
  3. equal texts in the same place form a TRACK; a colour change is real only if the new colour
     stays for at least 2 consecutive readings and the difference is clear;
  4. simultaneous changes of texts in the same column/row become ONE line;
  5. printed lines per minute are capped, with priorities.
"""
import json
import os
import re
import sys
import time

import cv2
import numpy as np

# With python -I the script folder is not on the path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eventi
from testi import let, lev, pulisci_ocr, stessi

VERSIONE = "0.2"
LEGENDA = "RILIEVO = colore, disattivazione, avviso, etichetta o titolo del testo"
FORMATI = {
    "cambio_colore": "{testo} {prima} -> {dopo}",
    "cambio_gruppo": "{n} {cosa} {prima} -> {dopo}: {testo}",
    "disattivato": "disattivato {testo}",
    "avviso": "avviso {testo}",
    "etichetta": "etichetta {colore_f} {testo}",
    "titolo": "titolo {testo}",
    "rilievo": "{testo}",
}
# Large titles are almost always logos and fragments: they are marked but not printed
# ("sezione" prints them).
STAMPA_TITOLI = "nessuna"
PRIORITA = {"avviso": 0, "disattivato": 1, "cambio_gruppo": 2, "etichetta": 3, "cambio_colore": 4,
            "titolo": 5}

FEMMINILE = {"nero": "nera", "grigio": "grigia", "bianco": "bianca", "rosso": "rossa", "giallo": "gialla",
             "azzurro": "azzurra"}
MAX_RIGHE_MINUTO = 4
# Low-value types do not fill the minute.
MAX_PER_TIPO_MINUTO = {"cambio_colore": 2, "etichetta": 2, "titolo": 1}
FINESTRA_MINUTO = 60.0          # seconds: the window in which the printed lines are capped
SOGLIA_CONTRASTO_BASSO = 0.18   # text almost invisible on the background (luminance contrast 0..1)
SOGLIA_SATURO = 90.0            # HSV saturation (0..255) of a "full" colour
SAT_CROMATICO = 45.0            # below this saturation (like "grigio") or with V<60 the hue is unreliable
DELTA_TINTA = 15.0              # minimum hue distance (OpenCV scale 0..180) between two saturated colours
DELTA_LUCE = 60.0               # minimum brightness distance (0..255) to call two colours "very different"
DISTANZA_VISIBILE = 40.0        # minimum BGR colour distance between ink and background to see the text
LETTURE_STABILI = 2             # consecutive readings in which the new colour must stay
SALTO_FOTOGRAMMI = 75           # below this distance decode forward, above it seek
SOSTA_FERMO = 45.0              # seconds: a colour held in one spot longer than this is the interface look
ALTEZZA_RIGA = 2.0              # an ocr1 zone taller than this many times the ocr3 text is not a text line

BYTE_PER_MB = 1048576.0         # bytes in a megabyte
MAX_TESTO = 80                  # characters kept of a text read from the screen
LARGHEZZA_RIF = 1280.0          # reference frame size (px) of the normalised zones and of the ocr1 readings
ALTEZZA_RIF = 720.0
ANELLO_MIN = 8                  # px: inner edge of the ring around a box used to see the outer background
ANELLO_MAX = 16                 # px: outer edge of that ring
PASSO_RIGA = 8                  # px: vertical bucket that tells apart the rows of a group
VALORE_PIENO_MIN = 100          # HSV value below which a colour is too dark to be a warning or a label
CONTRASTO_MIN_VOCE = .15        # minimum contrast of a grey item to be a candidate "deactivated" item
FATTORE_SPENTA = .7             # a deactivated item has a contrast under this share of its list


def _json(percorso, predefinito):
    """Load a JSON file and return its content, or `predefinito` if it is missing or unreadable."""
    try:
        with open(percorso, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, TypeError):
        return predefinito


def ram_picco_mb():
    """Peak memory of this process in MB (1 decimal), or None when the platform does not report it."""
    if sys.platform != "win32":
        try:
            import resource
            valore = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            divisore = BYTE_PER_MB if sys.platform == "darwin" else 1024.0
            return round(valore / divisore, 1)
        except (ImportError, AttributeError, OSError):
            return None

    try:
        import ctypes
        from ctypes import wintypes

        class Contatori(ctypes.Structure):
            """PROCESS_MEMORY_COUNTERS structure filled in by K32GetProcessMemoryInfo."""

            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]

        valori = Contatori()
        valori.cb = ctypes.sizeof(valori)
        funzione = ctypes.windll.kernel32.K32GetProcessMemoryInfo
        funzione.argtypes = [wintypes.HANDLE, ctypes.POINTER(Contatori), wintypes.DWORD]
        funzione.restype = wintypes.BOOL
        corrente = ctypes.windll.kernel32.GetCurrentProcess
        corrente.restype = wintypes.HANDLE
        if not funzione(corrente(), ctypes.byref(valori), valori.cb):
            return None
        return round(valori.PeakWorkingSetSize / BYTE_PER_MB, 1)
    except Exception:
        return None


# ---------------------------------------------------------------- colour
def nome_colore(hsv):
    """Name an OpenCV HSV colour (H 0..180, S and V 0..255) with one of the fixed Italian colour names."""
    tinta, saturazione, valore = [float(canale) for canale in hsv]
    if valore < 55:
        return "nero"
    if saturazione < 35 and valore > 205:
        return "bianco"
    if saturazione < 45:
        return "grigio"
    if tinta < 8 or tinta >= 170:
        return "rosso"
    if tinta < 20:
        return "arancio"
    if tinta < 35:
        return "giallo"
    if tinta < 85:
        return "verde"
    if tinta < 105:
        return "azzurro"
    if tinta < 140:
        return "blu"
    return "viola"


def _hsv(bgr):
    """Convert one BGR colour (clipped to 0..255) to a [H, S, V] list of floats."""
    pixel = np.uint8([[np.clip(bgr, 0, 255)]])
    return [float(canale) for canale in cv2.cvtColor(pixel, cv2.COLOR_BGR2HSV)[0, 0]]


def _luce(bgr):
    """Gray-level brightness (0..255) of one BGR colour."""
    return float(cv2.cvtColor(np.uint8([[np.clip(bgr, 0, 255)]]), cv2.COLOR_BGR2GRAY)[0, 0])


def _cromatico(hsv):
    """True if the colour is saturated and bright enough for its hue to be reliable."""
    return hsv[1] >= SAT_CROMATICO and hsv[2] >= 60.0


def _distanza_tinta(tinta_a, tinta_b):
    """Circular distance between two OpenCV hues (0..180, so at most 90)."""
    distanza = abs(float(tinta_a) - float(tinta_b)) % 180.0
    return min(distanza, 180.0 - distanza)


def differenza_netta(colore_a, colore_b):
    """True if two (H, S, V) colours really differ: antialiasing or compression noise is not enough.

    Brightness alone decides when the gap is large; two coloured shades compare by hue; a coloured shade
    against a colourless one counts only if the coloured one is full and bright.
    """
    if abs(colore_a[2] - colore_b[2]) >= DELTA_LUCE:
        return True
    cromatico_a = _cromatico(colore_a)
    cromatico_b = _cromatico(colore_b)
    if cromatico_a and cromatico_b:
        return _distanza_tinta(colore_a[0], colore_b[0]) >= DELTA_TINTA
    if cromatico_a != cromatico_b:
        piena = colore_a if cromatico_a else colore_b
        return piena[1] >= SOGLIA_SATURO and piena[2] >= 80.0
    return False


def _anello_esterno(frame, x0, y0, x1, y1, predefinito):
    """Median colour of the crown 8 to 16 px from the box: tells whether the background is a tag or a bar.

    Returns `predefinito` when the crown is empty (the box fills the frame).
    """
    altezza, larghezza = frame.shape[:2]
    ex0, ey0 = max(0, x0 - ANELLO_MAX), max(0, y0 - ANELLO_MAX)
    ex1, ey1 = min(larghezza, x1 + ANELLO_MAX), min(altezza, y1 + ANELLO_MAX)
    zona = frame[ey0:ey1, ex0:ex1]
    corona = np.ones(zona.shape[:2], dtype=bool)
    corona[max(0, y0 - ANELLO_MIN - ey0):y1 + ANELLO_MIN - ey0,
           max(0, x0 - ANELLO_MIN - ex0):x1 + ANELLO_MIN - ex0] = False
    return np.median(zona[corona], axis=0) if corona.any() else predefinito


def misura_stile(frame, zona):
    """Measure the text colour (ink core) and the background (3 px ring) of a box of `frame`.

    The ink is whatever stands out from the background (colour distance from the ring, Otsu threshold),
    so text and background are not swapped when the box is narrow or wide. Returns a dict with colour
    names, HSV values, contrast, prominence, visibility and the box height.
    """
    altezza, larghezza = frame.shape[:2]
    x0, y0, x1, y1 = [int(round(float(valore))) for valore in zona]
    x0 = max(0, min(larghezza - 1, x0))
    y0 = max(0, min(altezza - 1, y0))
    x1 = max(x0 + 1, min(larghezza, x1))
    y1 = max(y0 + 1, min(altezza, y1))
    ritaglio = frame[y0:y1, x0:x1]
    ax0, ay0 = max(0, x0 - 3), max(0, y0 - 3)
    ax1, ay1 = min(larghezza, x1 + 3), min(altezza, y1 + 3)
    esteso = frame[ay0:ay1, ax0:ax1]
    anello = np.ones(esteso.shape[:2], dtype=bool)
    anello[y0 - ay0:y1 - ay0, x0 - ax0:x1 - ax0] = False
    pixel_fondo = esteso[anello]
    # Box as big as the frame: the background is the border of the crop.
    if pixel_fondo.size == 0:
        pixel_fondo = np.concatenate([ritaglio[0], ritaglio[-1], ritaglio[:, 0], ritaglio[:, -1]])
    bgr_fondo = np.median(pixel_fondo, axis=0)
    bgr_esterno = _anello_esterno(frame, x0, y0, x1, y1, bgr_fondo)
    distanza = np.linalg.norm(ritaglio.astype(np.float32) - bgr_fondo.astype(np.float32), axis=2)
    soglia, _ = cv2.threshold(np.clip(distanza, 0, 255).astype(np.uint8), 0, 255,
                              cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    inchiostro = distanza > max(float(soglia), DISTANZA_VISIBILE * 0.6)
    visibile = (float(distanza.max()) >= DISTANZA_VISIBILE
                and int(inchiostro.sum()) >= max(1, int(0.005 * inchiostro.size)))
    pixel_testo = ritaglio[inchiostro].astype(np.float32)
    if len(pixel_testo) >= 8:
        # Core: the ink pixels farthest from the background (antialiased edges fade toward it).
        lontani = distanza[inchiostro]
        pixel_testo = pixel_testo[lontani >= np.percentile(lontani, 50)]
    bgr_testo = np.median(pixel_testo, axis=0) if len(pixel_testo) else bgr_fondo
    hsv_testo = _hsv(bgr_testo)
    hsv_fondo = _hsv(bgr_fondo)
    hsv_esterno = _hsv(bgr_esterno)
    lum_testo = _luce(bgr_testo)
    lum_fondo = _luce(bgr_fondo)
    contrasto = abs(lum_testo - lum_fondo) / 255.0
    rilievo = min(1.0, 0.65 * contrasto + 0.35 * min(1.0, float(inchiostro.mean()) * 8.0))
    return {"colore": nome_colore(hsv_testo), "colore_fondo": nome_colore(hsv_fondo),
            "hsv": [round(valore, 1) for valore in hsv_testo],
            "hsv_fondo": [round(valore, 1) for valore in hsv_fondo],
            "hsv_esterno": [round(valore, 1) for valore in hsv_esterno],
            "saturazione": round(hsv_testo[1], 2), "saturazione_fondo": round(hsv_fondo[1], 2),
            "contrasto": round(contrasto, 4), "rilievo": round(rilievo, 4), "visibile": bool(visibile),
            "disattivato": contrasto < SOGLIA_CONTRASTO_BASSO, "altezza": y1 - y0}


# ---------------------------------------------------------------- valid text
def _gettoni(testo):
    """Split a text into its runs of ASCII letters."""
    return re.findall(r"[A-Za-z]+", testo)


def vocabolario(parole):
    """Set of the words spoken in the video (from parole.json): lowercase letters only, 3 or more."""
    voci = set()
    for parola in parole:
        if isinstance(parola, list) and len(parola) >= 3:
            for gettone in _gettoni(str(parola[2])):
                if len(gettone) >= 3:
                    voci.add(gettone.lower())
    return voci


def in_vocabolario(testo, voci):
    """True if some word of the text (4+ letters) is spoken in the video, allowing one letter of slack."""
    for gettone in _gettoni(testo):
        gettone = gettone.lower()
        if len(gettone) < 4:
            continue
        if gettone in voci:
            return True
        # Words glued together by the OCR ("Gardengnome").
        if len(gettone) >= 8 and any(len(detta) >= 5 and detta in gettone for detta in voci):
            return True
        margine = 1 if len(gettone) < 8 else 2
        if len(gettone) >= 5 and any(
                abs(len(gettone) - len(detta)) <= margine and lev(gettone, detta) <= margine
                for detta in voci):
            return True
    return False


def forma_valida(testo):
    """True if the text has 3+ letters, is almost only letters and digits, and has no lone letter.

    A lone letter is a symptom of broken text.
    """
    lettere = len(re.findall(r"[A-Za-z]", testo))
    alfanum = len(re.findall(r"[A-Za-z0-9]", testo))
    compatti = len(testo.replace(" ", ""))
    if lettere < 3 or compatti == 0:
        return False
    if lettere / float(alfanum) < 0.7 or alfanum / float(compatti) < 0.7:
        return False
    # Addresses, paths, codes.
    if len(re.findall(r"[/\#@_]", testo)) > 1:
        return False
    return not any(len(gettone) == 1 and gettone not in "aAI" for gettone in _gettoni(testo))


def testi_validi(voci, parole):
    """Tell for each reading (dict with 'testo' and 't') whether its text is valid; uses only the OCR.

    Returns {id(reading): bool}. A text is valid if its shape is right and it is either read at 2 or
    more distinct instants or contains a spoken word.
    """
    voci_dette = vocabolario(parole)
    istanti = {}
    for voce in voci:
        istanti.setdefault(let(voce["testo"]), set()).add(round(float(voce["t"]), 2))
    esito = {}
    cache = {}
    for voce in voci:
        testo = voce["testo"]
        if testo not in cache:
            ripetuto = len(istanti.get(let(testo), ())) >= 2
            cache[testo] = forma_valida(testo) and (ripetuto or in_vocabolario(testo, voci_dette))
        esito[id(voce)] = cache[testo]
    return esito


# ---------------------------------------------------------------- OCR and video reading
def _scala_zona(zona, origine_w, origine_h, frame):
    """Scale a box from the pixels of its OCR source (size origine_w x origine_h) to those of `frame`."""
    h, w = frame.shape[:2]
    return [float(zona[0]) * w / max(1.0, float(origine_w)), float(zona[1]) * h / max(1.0, float(origine_h)),
            float(zona[2]) * w / max(1.0, float(origine_w)), float(zona[3]) * h / max(1.0, float(origine_h))]


def _voci_ocr(lavoro):
    """Collect the OCR readings of the work folder (ocr3.json and ocr1.json) grouped by time.

    Returns {time rounded to 3 decimals: [reading]}; a reading holds t, text, OCR strength, source
    size, source name ('ocr3' or 'ocr1') and the box in source pixels.
    """
    ocr3 = _json(os.path.join(lavoro, "ocr3.json"), {})
    ocr1 = _json(os.path.join(lavoro, "ocr1.json"), {})
    risultato = {}
    w3, h3 = float(ocr3.get("W") or 1), float(ocr3.get("H") or 1)
    for lettura in ocr3.get("letture", []):
        if not isinstance(lettura, list) or len(lettura) != 4:
            continue
        istante, base_x, base_y, valori = lettura
        for voce in valori:
            if not isinstance(voce, list) or len(voce) < 6:
                continue
            testo, forza, ax, ay, bx, by = voce[:6]
            testo = pulisci_ocr(str(testo))[:MAX_TESTO]
            if testo:
                risultato.setdefault(round(float(istante), 3), []).append({
                    "t": float(istante), "testo": testo, "forza_ocr": float(forza),
                    "origine_w": w3, "origine_h": h3, "fonte": "ocr3",
                    "zona_px": [base_x + ax, base_y + ay, base_x + bx, base_y + by]})
    zone = ocr1.get("zone", [])
    w1, h1 = LARGHEZZA_RIF, float(ocr1.get("H") or ALTEZZA_RIF)
    for indice, letture in ocr1.get("stato", {}).items():
        try:
            zona = zone[int(indice)]
        except (ValueError, TypeError, IndexError):
            continue
        for lettura in letture:
            if not isinstance(lettura, list) or len(lettura) != 2:
                continue
            istante, celle = lettura
            for cella in celle:
                if isinstance(cella, list) and len(cella) >= 2:
                    testo = pulisci_ocr(str(cella[0]))[:MAX_TESTO]
                    if testo:
                        risultato.setdefault(round(float(istante), 3), []).append({
                            "t": float(istante), "testo": testo, "forza_ocr": float(cella[1]),
                            "origine_w": w1, "origine_h": h1, "fonte": "ocr1", "zona_px": list(zona)})
    return risultato


def _altezza_riga(per_tempo):
    """Typical height of a text line: median of the ocr3 zones, in source pixels (None without ocr3 zones)."""
    alte = []
    for voci in per_tempo.values():
        for voce in voci:
            if voce["fonte"] == "ocr3":
                alte.append(voce["zona_px"][3] - voce["zona_px"][1])
    return float(np.median(alte)) if alte else None


def scegli_voci(per_tempo, parole):
    """Mark every reading of `per_tempo` as valid (text) and measurable, in place.

    An ocr1 zone that is too tall is not a text line, so it is not measurable. Returns the count of
    readings dropped by reason: {'testo': n, 'zona': n}.
    """
    tutte = []
    for istante in sorted(per_tempo):
        for voce in per_tempo[istante]:
            tutte.append(voce)
    esito = testi_validi(tutte, parole)
    riga = _altezza_riga(per_tempo)
    scartate = {"testo": 0, "zona": 0}
    for voce in tutte:
        altezza = voce["zona_px"][3] - voce["zona_px"][1]
        limite = ALTEZZA_RIGA * riga if riga else 0.055 * voce["origine_h"]
        voce["valido"] = bool(esito[id(voce)])
        voce["misurabile"] = voce["fonte"] == "ocr3" or altezza <= limite
        if not voce["valido"]:
            scartate["testo"] += 1
        elif not voce["misurabile"]:
            scartate["zona"] += 1
    return scartate


def _fotogrammi(cattura, tempi, fps, conteggi):
    """Yield (time, frame) for each requested instant, decoding forward when that costs less than a seek.

    `conteggi` is updated in place with the number of seeks and of decoded frames.
    """
    posizione = None
    for istante in tempi:
        bersaglio = int(round(istante * fps)) if fps > 0 else 0
        if fps <= 0 or posizione is None or bersaglio < posizione or bersaglio - posizione > SALTO_FOTOGRAMMI:
            cattura.set(cv2.CAP_PROP_POS_MSEC, 1000.0 * istante)
            conteggi["salti"] += 1
            ok, frame = cattura.read()
            posizione = int(round(cattura.get(cv2.CAP_PROP_POS_FRAMES))) if ok else None
        else:
            ok = True
            while posizione < bersaglio and ok:
                ok = cattura.grab()
                posizione += 1
            if ok:
                ok, frame = cattura.read()
                posizione += 1
            if not ok:
                posizione = None
        if ok:
            conteggi["fotogrammi"] += 1
            yield istante, frame


def leggi(video, lavoro):
    """Read the OCR files of `lavoro`, decode only the useful instants of `video` and measure every text.

    Returns {letture, parole, durata, conteggi}: per instant the readings with their style
    measures (colour, contrast, normalised zone), the spoken words, the video length and some counters.
    Raises RuntimeError if the video cannot be opened.
    """
    per_tempo = _voci_ocr(lavoro)
    parole = _json(os.path.join(lavoro, "parole.json"), [])
    scartate = scegli_voci(per_tempo, parole)
    utili = sorted(istante for istante in per_tempo
                   if any(voce["valido"] and voce["misurabile"] for voce in per_tempo[istante]))
    cattura = cv2.VideoCapture(video)
    if not cattura.isOpened():
        raise RuntimeError("video non apribile: " + video)
    fps = float(cattura.get(cv2.CAP_PROP_FPS) or 0.0)
    totale_fotogrammi = float(cattura.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
    durata = totale_fotogrammi / fps if fps > 0 else max(list(per_tempo) or [0.0])
    letture = []
    conteggi = {"salti": 0, "fotogrammi": 0}
    misure = 0
    try:
        for istante, frame in _fotogrammi(cattura, utili, fps, conteggi):
            voci = []
            riusi = {}
            for voce in per_tempo[round(istante, 3)]:
                corrente = dict(voce)
                zona = _scala_zona(voce["zona_px"], voce["origine_w"], voce["origine_h"], frame)
                corrente["zona_px_frame"] = zona
                corrente["zona_norm"] = [zona[0] / frame.shape[1], zona[1] / frame.shape[0],
                                         zona[2] / frame.shape[1], zona[3] / frame.shape[0]]
                # Crop already measured in the same frame.
                chiave = tuple(int(round(valore)) for valore in zona)
                if chiave not in riusi:
                    riusi[chiave] = misura_stile(frame, zona)
                    misure += 1
                corrente.update(riusi[chiave])
                voci.append(corrente)
            letture.append({"t": float(istante), "voci": voci})
    finally:
        cattura.release()
    return {"letture": letture, "parole": parole, "durata": float(durata),
            "conteggi": {"fotogrammi_decodificati": conteggi["fotogrammi"], "salti": conteggi["salti"],
                         "misure": misure,
                         "voci_ocr": sum(len(voci_tempo) for voci_tempo in per_tempo.values()),
                         "scartate_testo": scartate["testo"], "scartate_zona": scartate["zona"]}}


# ---------------------------------------------------------------- geometry
def _px(voce):
    """Box of a reading in frame pixels: the measured one, or the normalised one scaled to 1280x720."""
    zona_px = voce.get("zona_px_frame")
    if zona_px:
        return zona_px
    norm = voce["zona_norm"]
    return [norm[0] * LARGHEZZA_RIF, norm[1] * ALTEZZA_RIF, norm[2] * LARGHEZZA_RIF, norm[3] * ALTEZZA_RIF]


def _altezza(zona):
    """Height of a box [x0, y0, x1, y1], never below 1e-6 so it is safe as a divisor."""
    return max(1e-6, zona[3] - zona[1])


def _simili(zona_a, zona_b):
    """True if two boxes have comparable heights (ratio between .55 and 1.8)."""
    return .55 <= _altezza(zona_a) / _altezza(zona_b) <= 1.8


def _stessa_colonna(zona_a, zona_b):
    """True if two boxes of similar height share a left edge, a centre or a right edge, on different lines."""
    tolleranza = .8 * max(_altezza(zona_a), _altezza(zona_b))
    centro_a = (zona_a[1] + zona_a[3]) / 2
    centro_b = (zona_b[1] + zona_b[3]) / 2
    return (_simili(zona_a, zona_b)
            and (abs(zona_a[0] - zona_b[0]) <= tolleranza
                 or abs((zona_a[0] + zona_a[2]) / 2 - (zona_b[0] + zona_b[2]) / 2) <= tolleranza
                 or abs(zona_a[2] - zona_b[2]) <= tolleranza)
            and abs(centro_a - centro_b) > .5 * max(_altezza(zona_a), _altezza(zona_b)))


def _stessa_riga(zona_a, zona_b):
    """True if two boxes of similar height have their vertical centres on the same line."""
    centro_a = (zona_a[1] + zona_a[3]) / 2
    centro_b = (zona_b[1] + zona_b[3]) / 2
    tolleranza = .45 * max(_altezza(zona_a), _altezza(zona_b))
    return _simili(zona_a, zona_b) and abs(centro_a - centro_b) <= tolleranza


def _allineate(zona_a, zona_b):
    """True if two boxes are aligned in a column or in a row."""
    return _stessa_colonna(zona_a, zona_b) or _stessa_riga(zona_a, zona_b)


def _stesso_posto(zona_a, zona_b):
    """True if the two boxes are the same text rectangle (read at two different instants)."""
    h = max(_altezza(zona_a), _altezza(zona_b))
    larg = max(zona_a[2] - zona_a[0], zona_b[2] - zona_b[0])
    return (abs((zona_a[0] + zona_a[2]) / 2 - (zona_b[0] + zona_b[2]) / 2) <= max(.8 * h, .12 * larg)
            and abs((zona_a[1] + zona_a[3]) / 2 - (zona_b[1] + zona_b[3]) / 2) <= .5 * h)


def _compagne(voce, voci):
    """The other readings of `voci` aligned (column or row) with `voce`."""
    zona = _px(voce)
    return [altra for altra in voci if altra is not voce and _allineate(zona, _px(altra))]


def _in_gruppo(voce, voci):
    """Return (True if `voce` has at least 2 aligned companions, the list of companions)."""
    compagne = _compagne(voce, voci)
    return len(compagne) >= 2, compagne


# ---------------------------------------------------------------- events
def _evento(t, tipo, voce, forza, dati, stampa="sezione"):
    """Build one event dict of the common schema from a reading (or a merged group) and its strength.

    The zone is clipped to 0..1, `punto` is its centre and `forza` is clipped to 0..1.
    """
    zona = [max(0.0, min(1.0, float(valore))) for valore in voce["zona_norm"]]
    return {"id": "", "t": round(float(t), 2), "durata": 0., "canale": "stile", "tipo": tipo,
            "zona": [round(valore, 6) for valore in zona],
            "punto": [round((zona[0] + zona[2]) / 2, 6), round((zona[1] + zona[3]) / 2, 6)],
            "testo": voce["testo"][:MAX_TESTO], "forza": round(max(0., min(1., forza)), 4), "stampa": stampa,
            "rif": [], "dati": dict(dati)}


def _entro_il_tetto(tempi, massimo):
    """True if no 60 s window starting at one of `tempi` holds more than `massimo` of them."""
    for inizio in tempi:
        in_finestra = 0
        for tempo in tempi:
            if inizio <= tempo < inizio + FINESTRA_MINUTO:
                in_finestra += 1
        if in_finestra > massimo:
            return False
    return True


def _limita_righe(eventi_calcolati, scarti):
    """Keep at most MAX_RIGHE_MINUTO printed lines in any 60 s window, chosen by type priority.

    Silent events (stampa == 'nessuna') always stay. `scarti['tetto']` counts the lines dropped.
    """
    stampabili = [evento for evento in eventi_calcolati if evento["stampa"] != "nessuna"]
    silenziosi = [evento for evento in eventi_calcolati if evento["stampa"] == "nessuna"]
    scelti = []
    ordinati = sorted(stampabili, key=lambda evento: (PRIORITA[evento["tipo"]], -evento["forza"],
                                                      evento["t"], evento["testo"]))
    for candidato in ordinati:
        tempi = sorted([evento["t"] for evento in scelti] + [candidato["t"]])
        stesso_tipo = sorted([evento["t"] for evento in scelti if evento["tipo"] == candidato["tipo"]]
                             + [candidato["t"]])
        massimo = MAX_PER_TIPO_MINUTO.get(candidato["tipo"], MAX_RIGHE_MINUTO)
        libero = _entro_il_tetto(tempi, MAX_RIGHE_MINUTO) and _entro_il_tetto(stesso_tipo, massimo)
        if libero:
            scelti.append(candidato)
        else:
            scarti["tetto"] = scarti.get("tetto", 0) + 1
    return sorted(scelti + silenziosi, key=lambda evento: (evento["t"], evento["tipo"], evento["testo"]))


def _utile(voce):
    """True if the reading can feed the colour events: valid text, a real text line, colour measured."""
    return (voce.get("valido", True) and voce.get("misurabile", True) and voce.get("visibile", True)
            and "hsv" in voce)


def nome_breve(testo, larghezza=30):
    """Shorten a text for display: cut it to `larghezza` characters and drop a trailing open bracket.

    The open bracket is the fragment of a broken reading; surrounding spaces and punctuation go too.
    """
    testo = testo.strip()[:larghezza]
    testo = re.sub(r"\s*[\[(][^])]*$", "", testo).strip(" ,;:.-")
    return testo


def _mediana_hsv(lista):
    """Component-wise median of a list of [H, S, V] colours."""
    return [float(np.median([colore[i] for colore in lista])) for i in range(3)]


def _tracce(letture):
    """Group the readings of the same text in the same place into tracks (valid readings, in time order).

    A track is {testo, zona, voci, tempi}; the same line read by two sources at one instant counts once.
    """
    tracce = []
    for lettura in letture:
        for voce in lettura["voci"]:
            if not _utile(voce):
                continue
            zona = _px(voce)
            scelta = None
            for traccia in tracce:
                if _stesso_posto(traccia["zona"], zona) and stessi(traccia["testo"], voce["testo"]):
                    scelta = traccia
                    break
            if scelta is None:
                scelta = {"testo": voce["testo"], "zona": zona, "voci": [], "tempi": set()}
                tracce.append(scelta)
            # Same line read by two sources at the same instant.
            if round(voce["t"], 3) in scelta["tempi"]:
                continue
            scelta["tempi"].add(round(voce["t"], 3))
            scelta["voci"].append(voce)
            scelta["zona"] = zona
    return tracce


def _cambi_di_traccia(traccia, scarti):
    """Find the real colour changes of one track: a clear new colour that holds for LETTURE_STABILI readings.

    Changes where text and background simply swapped (the measure flipped) are dropped.
    `scarti` counts the rejected passages. Returns a list of change dicts.
    """
    voci = traccia["voci"]
    cambi = []
    if len(voci) < LETTURE_STABILI + 1:
        return cambi
    corrente = [voci[0]]
    i = 1
    while i < len(voci):
        stato = _mediana_hsv([voce["hsv"] for voce in corrente])
        candidata = voci[i]
        if not differenza_netta(stato, candidata["hsv"]):
            corrente.append(candidata)
            i += 1
            continue
        seguenti = voci[i:i + LETTURE_STABILI]
        stabile = len(seguenti) == LETTURE_STABILI and all(
            not differenza_netta(candidata["hsv"], seguente["hsv"])
            and differenza_netta(stato, seguente["hsv"])
            for seguente in seguenti[1:])
        if not stabile:
            scarti["passaggio"] = scarti.get("passaggio", 0) + 1
            i += 1
            continue
        nuovo = _mediana_hsv([seguente["hsv"] for seguente in seguenti])
        prima, dopo = nome_colore(stato), nome_colore(nuovo)
        fondo_prima = None
        fondo_dopo = None
        if "hsv_fondo" in candidata:
            fondo_prima = _mediana_hsv([voce["hsv_fondo"] for voce in corrente])
            fondo_dopo = _mediana_hsv([seguente["hsv_fondo"] for seguente in seguenti])
        inversione = (fondo_prima and not differenza_netta(nuovo, fondo_prima)
                      and not differenza_netta(fondo_dopo, stato))
        if inversione:
            # Text and background swapped: the measure flipped.
            scarti["inversione"] = scarti.get("inversione", 0) + 1
        elif prima != dopo:
            cambi.append({"traccia": traccia, "t": candidata["t"], "t_prima": corrente[-1]["t"],
                          "prima": prima, "dopo": dopo, "voce": candidata,
                          "letture_prima": len(corrente), "letture_dopo": len(seguenti),
                          "d_luce": round(abs(stato[2] - nuovo[2]), 1),
                          "d_tinta": round(_distanza_tinta(stato[0], nuovo[0]), 1),
                          "fondo": ([nome_colore(fondo_prima), nome_colore(fondo_dopo)]
                                    if fondo_prima else None)})
        corrente = list(seguenti)
        i += LETTURE_STABILI
    return cambi


def _eventi_cambio(letture, scarti):
    """Build the colour-change events: one per change, or one per group of simultaneous aligned changes.

    Changes with the same colour passage, touching time windows (15 s at most apart) and aligned in a
    column or row are merged into a 'cambio_gruppo' event. `scarti` counts repeats and rejections.
    """
    cambi = []
    for traccia in _tracce(letture):
        visti = set()
        for cambio in _cambi_di_traccia(traccia, scarti):
            # The same text repeating the same passage (a switch) counts once.
            if (cambio["prima"], cambio["dopo"]) in visti:
                scarti["ripetuto"] = scarti.get("ripetuto", 0) + 1
            else:
                visti.add((cambio["prima"], cambio["dopo"]))
                cambi.append(cambio)
    # Groups: same colour passage, touching time windows, same column or row.
    padre = list(range(len(cambi)))

    def radice(i):
        while padre[i] != i:
            padre[i] = padre[padre[i]]
            i = padre[i]
        return i

    for i in range(len(cambi)):
        for j in range(i + 1, len(cambi)):
            cambio_a, cambio_b = cambi[i], cambi[j]
            if (cambio_a["prima"], cambio_a["dopo"]) != (cambio_b["prima"], cambio_b["dopo"]):
                continue
            if (cambio_a["t"] < cambio_b["t_prima"] or cambio_b["t"] < cambio_a["t_prima"]
                    or abs(cambio_a["t"] - cambio_b["t"]) > 15.0):
                continue
            if _allineate(_px(cambio_a["voce"]), _px(cambio_b["voce"])):
                radice_i = radice(i)
                padre[radice(j)] = radice_i
    gruppi = {}
    for i, cambio in enumerate(cambi):
        gruppi.setdefault(radice(i), []).append(cambio)
    risultato = []
    for membri in gruppi.values():
        membri.sort(key=lambda cambio: (round(_px(cambio["voce"])[1] / PASSO_RIGA), _px(cambio["voce"])[0]))
        if len(membri) == 1:
            cambio = membri[0]
            dati = {"prima": cambio["prima"], "dopo": cambio["dopo"], "letture": cambio["letture_dopo"],
                    "d_luce": cambio["d_luce"], "d_tinta": cambio["d_tinta"], "fondo": cambio["fondo"]}
            risultato.append(_evento(cambio["t"], "cambio_colore", cambio["voce"], .85, dati))
            continue
        distinti = []
        for cambio in membri:
            if cambio["voce"]["testo"] not in distinti:
                distinti.append(cambio["voce"]["testo"])
        # Four names fit in the 80 characters.
        quanti = min(4, len(distinti))
        largo = max(12, min(30, (75 - 2 * (quanti - 1)) // quanti))
        nomi = []
        for testo_voce in distinti:
            nome = nome_breve(testo_voce, largo)
            if nome not in nomi:
                nomi.append(nome)
        testo = ", ".join(nomi[:4]) + (", ..." if len(nomi) > 4 else "")
        zone = [cambio["voce"]["zona_norm"] for cambio in membri]
        riga_y = {round(_px(cambio["voce"])[1] / PASSO_RIGA) for cambio in membri}
        unione = {"testo": testo,
                  "zona_norm": [min(zona[0] for zona in zone), min(zona[1] for zona in zone),
                                max(zona[2] for zona in zone), max(zona[3] for zona in zone)]}
        tempo = min(cambio["t"] for cambio in membri)
        dati = {"n": len(membri), "nomi": nomi, "cosa": "righe" if len(riga_y) >= 2 else "voci",
                "prima": membri[0]["prima"], "dopo": membri[0]["dopo"],
                "letture": min(cambio["letture_dopo"] for cambio in membri),
                "d_luce": max(cambio["d_luce"] for cambio in membri),
                "d_tinta": max(cambio["d_tinta"] for cambio in membri), "fondo": membri[0]["fondo"]}
        risultato.append(_evento(tempo, "cambio_gruppo", unione, min(.99, .9 + .01 * len(membri)), dati))
    return risultato


def _scelta_regolare(voce, voci):
    """Find the list of choices `voce` belongs to: readings of its column at a regular vertical pitch.

    The column shares the left edge with `voce`; the chain grows up and down while the spacing stays
    within .7 to 1.4 times the pitch. Returns [voce] alone if the pitch is zero or too large.
    """
    zona = _px(voce)
    h = _altezza(zona)
    colonna = [altra for altra in voci
               if altra is voce
               or (_stessa_colonna(zona, _px(altra)) and abs(_px(altra)[0] - zona[0]) <= .8 * h)]
    colonna.sort(key=lambda altra: (_px(altra)[1] + _px(altra)[3]) / 2.0)
    centri = [(_px(altra)[1] + _px(altra)[3]) / 2.0 for altra in colonna]
    i = colonna.index(voce)
    vicini = [abs(centri[j] - centri[i]) for j in (i - 1, i + 1) if 0 <= j < len(colonna)]
    passo = min(vicini) if vicini else 0.0
    if passo <= 0 or passo > 3.0 * h:
        return [voce]
    catena = [voce]
    for direzione in (-1, 1):
        j = i + direzione
        while 0 <= j < len(colonna) and .7 * passo <= abs(centri[j] - centri[j - direzione]) <= 1.4 * passo:
            catena.append(colonna[j])
            j += direzione
    return catena


def _eventi_di_lettura(lettura, vocab, visti):
    """Build the 'disattivato' and 'titolo' events of one reading, each once per text.

    `vocab` is the set of spoken words; `visti` ({'disattivato': [...], 'titolo': [...]}) remembers the
    texts already reported and is updated in place.
    """
    voci = [voce for voce in lettura.get("voci", []) if _utile(voce)]
    risultato = []
    # Only the ocr3 zones are tight boxes: their height tells the size of the text.
    righe = [voce for voce in voci if voce["fonte"] == "ocr3"]
    altezze = [float(voce["altezza"]) for voce in righe if float(voce["altezza"]) > 0]
    mediana = float(np.median(altezze)) if altezze else 0.
    grandi = set(id(voce) for voce in sorted(righe, key=lambda riga: -float(riga["altezza"]))[:3])

    def nuovo(tipo, voce):
        if any(stessi(voce["testo"], testo_visto) for testo_visto in visti[tipo]):
            return False
        visti[tipo].append(voce["testo"])
        return True

    for voce in voci:
        if voce["saturazione"] < SAT_CROMATICO and voce["contrasto"] >= CONTRASTO_MIN_VOCE:
            catena = _scelta_regolare(voce, voci)
            normali = [altra for altra in catena if altra is not voce and altra["contrasto"] >= .5]
            # The item is off if its contrast is clearly lower than the others in the same list,
            # and the off ones are few: if they are half, it is the style of the list, not a
            # deactivated choice.
            riferimento = float(np.median([altra["contrasto"] for altra in normali])) if normali else 1.0
            spente = [altra for altra in catena
                      if altra["saturazione"] < SAT_CROMATICO
                      and CONTRASTO_MIN_VOCE <= altra["contrasto"] <= FATTORE_SPENTA * riferimento]
            if (len(catena) >= 3 and len(normali) >= 2 and voce["contrasto"] <= FATTORE_SPENTA * riferimento
                    and len(spente) <= .4 * len(catena) and nuovo("disattivato", voce)):
                dati = {"marca": "~", "contrasto": voce["contrasto"], "lista": len(catena)}
                risultato.append(_evento(voce["t"], "disattivato", voce, .9, dati))
        titolo = (mediana > 0 and voce["altezza"] >= 1.6 * mediana and id(voce) in grandi
                  and voce["forza_ocr"] >= .9 and len(re.findall(r"[A-Za-z]", voce["testo"])) >= 5
                  and not in_vocabolario(voce["testo"], vocab))
        if titolo and nuovo("titolo", voce):
            risultato.append(_evento(voce["t"], "titolo", voce, .75, {"marca": "#"}, STAMPA_TITOLI))
    return risultato


def _classe_vistosa(voce, voci):
    """Classify a reading as a conspicuous text: ('avviso' | 'etichetta', colour), or None.

    A warning is red/orange text or a red/orange background; a label is yellow text or a coloured tag
    that stands apart from its surroundings and is not part of an aligned group.
    """
    gruppo, compagne = _in_gruppo(voce, voci)
    simili = [altra for altra in compagne if altra["colore"] == voce["colore"]]
    # A column of the same colour is data, not a warning.
    pieno = voce["saturazione"] >= SOGLIA_SATURO and voce["hsv"][2] >= VALORE_PIENO_MIN and len(simili) < 2
    if (voce["colore_fondo"] in ("rosso", "arancio") and voce["saturazione_fondo"] >= SOGLIA_SATURO
            and voce["hsv_fondo"][2] >= VALORE_PIENO_MIN):
        return "avviso", voce["colore_fondo"]
    if voce["colore"] in ("rosso", "arancio") and pieno:
        return "avviso", voce["colore"]
    if voce["colore"] == "giallo" and pieno:
        return "etichetta", "giallo"
    delimitata = "hsv_esterno" not in voce or differenza_netta(voce["hsv_fondo"], voce["hsv_esterno"])
    if voce["saturazione_fondo"] >= SOGLIA_SATURO and not gruppo and delimitata:
        return "etichetta", voce["colore_fondo"]
    return None


def _eventi_vistosi(letture, durata, con_cambio=()):
    """Build the warning and coloured-label events.

    The text must be conspicuous at 2 or more instants and must not stay anchored for the whole video.
    A colour that always sits in the same spot (a button state, a theme) is not a warning: a warning
    appears and disappears, a label floats over the content. `con_cambio` holds the texts that already
    have a colour-change event (a text that changes colour is a state turning on).
    """
    per_chiave = {}
    for lettura in letture:
        voci = [voce for voce in lettura.get("voci", []) if _utile(voce)]
        for voce in voci:
            classe = _classe_vistosa(voce, voci)
            if classe:
                per_chiave.setdefault((classe[0], let(voce["testo"])), []).append((voce, classe[1]))
    risultato = []
    for (tipo, chiave), letture_k in per_chiave.items():
        if chiave in con_cambio:
            continue
        istanti = sorted({round(voce["t"], 2) for voce, _ in letture_k})
        if len(istanti) < LETTURE_STABILI:
            continue
        gruppi_posto = []
        for voce, _ in letture_k:
            for gruppo in gruppi_posto:
                if _stesso_posto(_px(gruppo[0]), _px(voce)):
                    gruppo.append(voce)
                    break
            else:
                gruppi_posto.append([voce])
        fermo = max(gruppi_posto, key=lambda gruppo: len({round(voce["t"], 2) for voce in gruppo}))
        tempi_fermo = sorted({round(voce["t"], 2) for voce in fermo})
        if len(tempi_fermo) >= 2 and tempi_fermo[-1] - tempi_fermo[0] >= max(SOSTA_FERMO, .25 * durata):
            continue
        voce, colore = min(letture_k, key=lambda coppia: coppia[0]["t"])
        if tipo == "avviso":
            forza = min(.99, .65 + voce["saturazione"] / 850.)
            dati = {"marca": "!", "colore": colore, "letture": len(istanti)}
            risultato.append(_evento(voce["t"], "avviso", voce, forza, dati))
        else:
            forza = min(.94, .60 + max(voce["saturazione"], voce["saturazione_fondo"]) / 1000.)
            dati = {"colore_f": FEMMINILE.get(colore, colore), "letture": len(istanti)}
            risultato.append(_evento(voce["t"], "etichetta", voce, forza, dati))
    return risultato


def _eventi_pota(lettura, vocab):
    """Build the silent 'rilievo' events: weak ocr1 cells, outside the vocabulary and without digits.

    They are only marked (pota), never printed. Weak means below the 30th percentile of prominence.
    """
    voci = [voce for voce in lettura.get("voci", []) if "rilievo" in voce]
    if not voci:
        return []
    percentile = float(np.percentile([float(voce["rilievo"]) for voce in voci], 30))
    risultato = []
    for voce in voci:
        if (voce["fonte"] == "ocr1" and voce["rilievo"] < percentile and not re.search(r"\d", voce["testo"])
                and not in_vocabolario(voce["testo"], vocab)):
            forza = max(.01, voce["rilievo"])
            risultato.append(_evento(voce["t"], "rilievo", voce, forza, {"pota": True}, "nessuna"))
    return risultato


def calcola(dati):
    """Turn the measured readings in `dati` into the final list of style events (ids C1, C2, ...).

    Marks text validity if missing, collects colour changes, conspicuous texts, deactivated items,
    titles and silent prominence marks, then caps the printed lines. Stores the rejection counters in
    `dati['scarti']`.
    """
    letture = sorted(dati.get("letture", []), key=lambda lettura: lettura["t"])
    parole = dati.get("parole", [])
    vocab = vocabolario(parole)
    scarti = {}
    # Text validity depends on ALL the readings; if it is not already marked, compute it here.
    senza = []
    for lettura in letture:
        for voce in lettura.get("voci", []):
            if "valido" not in voce:
                senza.append(voce)
    if senza:
        esito = testi_validi(senza, parole)
        for voce in senza:
            voce["valido"] = bool(esito[id(voce)])
    durata = float(dati.get("durata") or (letture[-1]["t"] if letture else 0.0))
    risultato = _eventi_cambio(letture, scarti)
    con_cambio = {let(evento["testo"]) for evento in risultato if evento["tipo"] == "cambio_colore"}
    for evento in risultato:
        if evento["tipo"] == "cambio_gruppo":
            con_cambio.update(let(nome) for nome in evento["dati"].get("nomi", []))
    risultato.extend(_eventi_vistosi(letture, durata, con_cambio))
    visti = {"disattivato": [], "titolo": []}
    for lettura in letture:
        risultato.extend(_eventi_di_lettura(lettura, vocab, visti))
        risultato.extend(_eventi_pota(lettura, vocab))
    finali = _limita_righe(risultato, scarti)
    for indice, evento in enumerate(finali, 1):
        evento["id"] = "C%d" % indice
    dati["scarti"] = scarti
    return finali


def documento(eventi_calcolati, secondi, letture, durata, extra=None):
    """Build the eventi_stile.json document: schema header, run statistics (plus `extra`) and the events."""
    tipi = {}
    for evento in eventi_calcolati:
        tipi[evento["tipo"]] = tipi.get(evento["tipo"], 0) + 1
    stat = {"secondi": round(float(secondi), 3), "cpu_secondi": round(time.process_time(), 3),
            "durata": round(float(durata), 3), "letture": int(letture), "eventi": len(eventi_calcolati),
            "eventi_per_tipo": tipi, "ram_picco_mb": ram_picco_mb()}
    stat.update(extra or {})
    return {"schema": 1, "canale": "stile", "lettera": "C", "versione": VERSIONE, "sezione": "RILIEVO",
            "legenda": LEGENDA, "formati": dict(FORMATI), "stat": stat, "eventi": eventi_calcolati}


def main(argv):
    """Command line entry: `canale_stile.py <video.mp4> <work folder>`.

    Writes eventi_stile.json in the work folder and prints a one-line summary.
    """
    if len(argv) != 3:
        raise SystemExit("uso: canale_stile.py <video.mp4> <cartella _lavoro>")
    video = os.path.abspath(argv[1])
    lavoro = os.path.abspath(argv[2])
    inizio = time.perf_counter()
    dati = leggi(video, lavoro)
    calcolati = calcola(dati)
    extra = dict(dati.get("conteggi", {}))
    extra.update({"scarti_" + chiave: valore for chiave, valore in dati.get("scarti", {}).items()})
    doc = documento(calcolati, time.perf_counter() - inizio, len(dati["letture"]), dati["durata"], extra)
    eventi.scrivi(os.path.join(lavoro, "eventi_stile.json"), doc)
    print("canale_stile: eventi=%d letture=%d tempo=%.1fs ram_picco=%s MB tipi=%s" % (
        len(calcolati), len(dati["letture"]), doc["stat"]["secondi"], doc["stat"]["ram_picco_mb"],
        json.dumps(doc["stat"]["eventi_per_tipo"], ensure_ascii=False, sort_keys=True)))


if __name__ == "__main__":
    main(sys.argv)
