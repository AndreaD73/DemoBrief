# -*- coding: utf-8 -*-
"""Channel E: interface state (a chosen item, a control switched on or off).

Reads the video plus the work folder (ocr3.json, parole.json, eventi_cursore.json, cursore.json, mov.json)
and writes eventi_stato.json (and stato_diario.json when the diary is enabled). No word, position or
threshold is tied to one program or video: the channel only looks at how the screen changes.
 - E1 chosen: in a still-screen reading (ocr3), among aligned items exactly one has a different background.
 - E2 on/off: at 2 fps every COMPACT and PERSISTENT change of the screen (not the 3D view, not the
   cursor, not a tooltip, not a changing number) is a trigger; the name of the control comes from an OCR
   reading of the crop right after the change; the direction (on/off) comes from the before/after
   comparison (more vivid colour or higher-contrast text = on).
 - Two opposite changes among aligned items (one turns on, the other turns off) = a CHOICE.
"""
import collections
import json
import os
import re
import subprocess
import sys
import time

import cv2
import numpy as np

# With python -I the script folder is not on the path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eventi
import comune
from testi import pulisci_ocr, ratio, dig, let

VERSIONE = "0.3"
FORMATI = {"scelto": "scelto {ogg} ({dove})", "cambio": "{stato} {ogg}"}
LEGENDA = "STATO = voce scelta o controllo acceso/spento nell'interfaccia"

LARGHEZZA = 1280          # the ocr3 boxes and the pixel thresholds are in this scale
FPS = 2                   # one frame every 0.5 s
SOGLIA_PIXEL = 60         # sum of BGR differences above which a pixel counts as "changed"
ORIZZONTE = 9             # frames looked at ahead: +3 (1.5 s) confirms, +9 (4.5 s) confirms it holds
MAX_PER_MINUTO = 6        # channel cap: more than this is noise
# Limits the output to events close to the voice, avoiding isolated events in the 'fx' branch of
# costruisci.py.
SOLO_CON_VOCE = True
MARGINE_OCR = (60, 240, 10, 10)   # left, right, top, bottom around the change, to read the name
CONF_NOME = 0.75          # minimum OCR confidence to give a control a name
MAX_OCR = 150             # OCR readings per video at most (the sharpest candidates)
BARRE_CROMA = 25.0        # Lab chroma above which a pixel is "coloured" (interfaces are almost all grey)
MAX_BARRE = 12            # bars read per key frame at most
PASSO_CHIAVE = 40         # one key frame every 40 frames (20 s), plus the second second of the video

BYTE_PER_MB = 1048576.0   # bytes in a megabyte
LAB_NEUTRO = 128.0        # Lab a/b value of a colourless pixel in OpenCV's 8-bit encoding
MAX_TESTO = 80            # characters kept of a label read from the screen
LATO_PALLINO = 40         # largest side (px) of a dot or checkbox
LATO_CURSORE = 32         # largest side (px) of one of the two small blobs a moving cursor leaves
BIANCO_V_MIN = 225        # HSV value above which (with low saturation) a pixel is pure white
BIANCO_S_MAX = 40         # HSV saturation below which a bright pixel is pure white
NERO_V_MAX = 30           # HSV value below which a pixel is pure black
DELTA_MIN = 15.0          # minimum Lab distance for a control to count as changed
DELTA_VOCE_SCELTA = 40.0  # delta assumed for a chosen item, which has no measured change
FORZA_BASE = 0.5          # strength of an event with no spoken support
FORZA_PESO = 0.49         # extra strength given by the score (the total stays below 1)


# ---------------------------------------------------------------- reading utilities
def _json(percorso, predefinito):
    """Load a JSON file and return its content, or `predefinito` if it is missing or unreadable."""
    try:
        with open(percorso, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, TypeError):
        return predefinito


def colore_anello(frame, zona, spessore=3):
    """Median Lab colour of the ring just outside `zona` (x0, y0, x1, y1), `spessore` pixels thick."""
    if frame is None:
        return None
    h, w = frame.shape[:2]
    x0, y0, x1, y1 = [int(round(v)) for v in zona]
    x0 = max(0, min(w - 1, x0))
    x1 = max(x0 + 1, min(w, x1))
    y0 = max(0, min(h - 1, y0))
    y1 = max(y0 + 1, min(h, y1))
    ax0 = max(0, x0 - spessore)
    ay0 = max(0, y0 - spessore)
    ax1 = min(w, x1 + spessore)
    ay1 = min(h, y1 + spessore)
    ritaglio = frame[ay0:ay1, ax0:ax1]
    maschera = np.ones(ritaglio.shape[:2], dtype=bool)
    maschera[y0-ay0:y1-ay0, x0-ax0:x1-ax0] = False
    pixel = ritaglio[maschera]
    if pixel.size == 0:
        return None
    lab = cv2.cvtColor(pixel.reshape(-1, 1, 3), cv2.COLOR_BGR2LAB)
    return np.median(lab.reshape(-1, 3), axis=0).astype(float)


def _distanza(a, b):
    """Euclidean distance between two colour vectors (Lab triples)."""
    return float(np.linalg.norm(np.asarray(a, dtype=float) - np.asarray(b, dtype=float)))


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

        class _Contatori(ctypes.Structure):
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

        contatori = _Contatori()
        contatori.cb = ctypes.sizeof(contatori)
        info = ctypes.windll.kernel32.K32GetProcessMemoryInfo
        info.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Contatori), wintypes.DWORD]
        info.restype = wintypes.BOOL
        corrente = ctypes.windll.kernel32.GetCurrentProcess
        corrente.restype = wintypes.HANDLE
        if not info(corrente(), ctypes.byref(contatori), contatori.cb):
            return None
        return round(contatori.PeakWorkingSetSize / BYTE_PER_MB, 1)
    except Exception:
        return None


# ---------------------------------------------------------------- E1: item chosen in a reading
def _gruppi(voci):
    """Find groups of at least 3 aligned items (same row or same column) of similar height.

    In a column the items must also be contiguous: an item joins only if it is at most 2.5 heights
    away from its neighbour, so the sections of a menu stay separate. Returns a sorted list of
    index tuples into `voci`.
    """
    gruppi = set()
    for i, voce in enumerate(voci):
        h = max(1.0, voce["zona"][3] - voce["zona"][1])
        centro_y = (voce["zona"][1] + voce["zona"][3]) / 2.0
        verticali, orizzontali = [], []
        for j, altra in enumerate(voci):
            altezza_altra = max(1.0, altra["zona"][3] - altra["zona"][1])
            centro_y_altra = (altra["zona"][1] + altra["zona"][3]) / 2.0
            simile = 0.75 <= altezza_altra / h <= 1.25
            if simile and abs(altra["zona"][0] - voce["zona"][0]) <= max(3.0, 0.3*h):
                verticali.append(j)
            if simile and abs(centro_y_altra - centro_y) <= 0.5*max(h, altezza_altra):
                orizzontali.append(j)
        verticali = _contigue(voci, verticali, i)
        if len(verticali) >= 3:
            gruppi.add(tuple(sorted(verticali)))
        if len(orizzontali) >= 3:
            gruppi.add(tuple(sorted(orizzontali)))
    return sorted(gruppi)


def _contigue(voci, indici, i):
    """From a whole column of item indices, keep the contiguous stretch that contains item `i`."""
    ordine = sorted(indici, key=lambda k: voci[k]["zona"][1])
    if i not in ordine:
        return [i]
    pos = ordine.index(i)
    primo = pos
    ultimo = pos

    def vuoto(sopra, sotto):
        altezza = max(voci[sopra]["zona"][3] - voci[sopra]["zona"][1],
                      voci[sotto]["zona"][3] - voci[sotto]["zona"][1], 1.0)
        return voci[sotto]["zona"][1] - voci[sopra]["zona"][3] <= 2.5 * altezza

    while primo > 0 and vuoto(ordine[primo-1], ordine[primo]):
        primo -= 1
    while ultimo < len(ordine) - 1 and vuoto(ordine[ultimo], ordine[ultimo+1]):
        ultimo += 1
    return ordine[primo:ultimo+1]


def scelte(voci):
    """Return the chosen items among `voci`, one per distinct text.

    In every group of aligned items (same row or column) the majority has the same background
    (within 8 in Lab); an item that departs by more than 20 and is more coloured than the majority
    is the choice. Dark or unrelated items that ended up in the group by alignment do not count.
    """
    trovate = {}
    for gruppo in _gruppi(voci):
        validi = [i for i in gruppo if voci[i].get("lab") is not None]
        if len(validi) < 3:
            continue
        labs = np.asarray([voci[i]["lab"] for i in validi], dtype=float)
        vicini = [np.linalg.norm(labs - labs[k], axis=1) <= 8.0 for k in range(len(validi))]
        maggioranza = vicini[int(np.argmax([vicino.sum() for vicino in vicini]))]
        minimo = 2 if len(validi) == 3 else 3
        if maggioranza.sum() < minimo or maggioranza.sum() < 0.4 * len(validi):
            continue
        centro = np.median(labs[maggioranza], axis=0)
        croma_centro = _croma(centro)
        devianti = [j for j in range(len(validi))
                    if not maggioranza[j] and np.linalg.norm(labs[j] - centro) > 20.0]
        colorate = [j for j in devianti if _croma(labs[j]) - croma_centro >= 15.0]
        # One choice, or two buttons switched on together; more than that is a coloured list.
        if 1 <= len(colorate) <= 2:
            for j in colorate:
                voce = voci[validi[j]]
                trovate.setdefault(voce["testo"], voce)
    return list(trovate.values())


def scelto(voci):
    """Return the first chosen item among `voci`, or None when no item is chosen."""
    trovate = scelte(voci)
    return trovate[0] if trovate else None


def _cursore_fuori(cursori, t, zona):
    """True if the cursor was not inside `zona` around time `t` (or no cursor sample is within 0.25 s)."""
    vicini = [punto for punto in cursori if abs(float(punto[0])-float(t)) <= 0.25]
    if not vicini:
        return True
    _, x, y = min(vicini, key=lambda campione: abs(float(campione[0])-float(t)))[:3]
    x0, y0, x1, y1 = zona
    return not (x0 <= x <= x1 and y0 <= y <= y1)


# ---------------------------------------------------------------- E1b: bars highlighted in a still frame
def _lab_barra(frame, scatola):
    """Median Lab of the coloured pixels inside the box, or None when under 30% of them are coloured."""
    x0, y0, x1, y1 = scatola
    lab = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2LAB).reshape(-1, 3)
    croma = np.hypot(lab[:, 1].astype(np.float32) - LAB_NEUTRO, lab[:, 2].astype(np.float32) - LAB_NEUTRO)
    colorati = lab[croma >= BARRE_CROMA]
    if len(colorati) < 0.3 * len(lab):
        return None
    return np.median(colorati.astype(float), axis=0)


def barre_evidenziate(frame):
    """Find the solid coloured rectangles of a frame (a chosen list entry, a lit button).

    Returns [(box, median Lab)], at most MAX_BARRE. No text is needed: this covers the zones that
    never change, which no reading of changes can see.
    """
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
    croma = np.hypot(lab[:, :, 1].astype(np.float32) - LAB_NEUTRO,
                     lab[:, :, 2].astype(np.float32) - LAB_NEUTRO)
    maschera = (croma >= BARRE_CROMA).astype(np.uint8)
    # The text on the bar is not coloured: close the holes it leaves.
    chiusa = cv2.morphologyEx(maschera, cv2.MORPH_CLOSE, np.ones((5, 15), np.uint8))
    quante, _, statistiche, _ = cv2.connectedComponentsWithStats(chiusa)
    larghezza = frame.shape[1]
    barre = []
    for k in range(1, quante):
        x, y, w, h, area = [int(valore) for valore in statistiche[k]]
        if w < 40 or h < 10 or h > 40 or w < 2.0 * h or w > 0.4 * larghezza:
            continue
        # A solid rectangle, not a line or a smear.
        if area < 0.85 * w * h:
            continue
        mediano = _lab_barra(frame, [x, y, x + w, y + h])
        if mediano is not None:
            barre.append(([x, y, x + w, y + h], mediano))
    return barre[:MAX_BARRE]


def nome_barra(righe, scatola):
    """Return the text written inside a bar (the OCR line overlapping it most), or None if there is none.

    `righe` is [(text, confidence, [x0, y0, x1, y1])]; `scatola` is the bar box in pixels.
    """
    bx0, by0, bx1, by1 = scatola
    migliore = None
    for testo, conf, riquadro in righe:
        if conf < CONF_NOME:
            continue
        sovr_x = max(0, min(bx1, riquadro[2]) - max(bx0, riquadro[0]))
        sovr_y = max(0, min(by1, riquadro[3]) - max(by0, riquadro[1]))
        area = float(max(1, (riquadro[2]-riquadro[0])*(riquadro[3]-riquadro[1])))
        if sovr_x * sovr_y >= 0.5 * area and (migliore is None or sovr_x * sovr_y > migliore[0]):
            migliore = (sovr_x * sovr_y, testo)
    if migliore is None:
        return None
    testo = pulisci_ocr(migliore[1])
    return testo[:MAX_TESTO] if e_voce(testo) else None


class Barre:
    """Tracks highlighted bars across key frames.

    A bar is confirmed one second after it is seen, named with one OCR reading per still bar, and
    reported only the first time it appears. Detected events accumulate in `eventi`.
    """

    def __init__(self, lettore, larghezza, altezza):
        """Store the OCR reader and the frame size (px); start with no pending key frame."""
        self.lettore = lettore
        self.w = larghezza
        self.h = altezza
        self.pendenti = []
        self.cache = {}
        self.attive = {}
        self.eventi = []
        self.stat = collections.Counter()

    def fotogramma_chiave(self, n, frame):
        """Detect the bars of key frame number `n` and queue them for confirmation; return their count."""
        barre = barre_evidenziate(frame)
        self.pendenti.append({"n": n, "barre": barre, "frame": None})
        return len(barre)

    def aggiungi(self, n, frame):
        """Feed frame `n`: close every queued key frame that is at least 1 s old (2 frames at 2 fps)."""
        for pendente in [q for q in self.pendenti if n >= q["n"] + 2]:
            self.pendenti.remove(pendente)
            self._chiudi(pendente, frame)

    def fine(self, frame):
        """At the end of the video, close all the queued key frames using the last frame."""
        for pendente in list(self.pendenti):
            self.pendenti.remove(pendente)
            self._chiudi(pendente, frame)

    def _chiudi(self, pendente, dopo):
        """Confirm the bars of one key frame against the later frame `dopo`, name them, emit new ones.

        A bar must keep its colour (Lab distance under 12) and have a readable name; a bar that
        disappears is forgotten, so if it comes back it is a new choice.
        """
        presenti = {}
        for scatola, lab in pendente["barre"]:
            x0, y0, x1, y1 = scatola
            adesso = _lab_barra(dopo, scatola)
            if adesso is None or _distanza(adesso, lab) > 12.0:
                self.stat["barra che non dura"] += 1
                continue
            chiave = (x0 // 6, y0 // 6, x1 // 6, y1 // 6)
            noto = self.cache.get(chiave)
            if noto is not None and _distanza(noto[1], lab) < 8.0:
                nome = noto[0]
            else:
                ox0, oy0 = max(0, x0 - 4), max(0, y0 - 4)
                ox1, oy1 = min(self.w, x1 + 4), min(self.h, y1 + 4)
                nome = nome_barra(self.lettore.leggi(dopo[oy0:oy1, ox0:ox1], [ox0, oy0]), scatola)
                self.cache[chiave] = (nome, lab)
            if nome is None:
                self.stat["barra senza nome"] += 1
                continue
            presenti[nome] = scatola
        # Gone: if it returns, it is a new choice.
        for nome in [attivo for attivo in self.attive if attivo not in presenti]:
            del self.attive[nome]
        for nome, scatola in presenti.items():
            if nome in self.attive:
                continue
            self.attive[nome] = scatola
            x0, y0, x1, y1 = scatola
            tempo = round(pendente["n"] / float(FPS), 2)
            self.eventi.append({"t": tempo, "testo": nome,
                                "zona_norm": [x0 / float(self.w), y0 / float(self.h),
                                              x1 / float(self.w), y1 / float(self.h)]})


# ---------------------------------------------------------------- E2: compact and persistent changes
def _diff(immagine_a, immagine_b):
    """Boolean mask of the pixels whose summed BGR difference between two images exceeds SOGLIA_PIXEL."""
    return np.abs(immagine_a.astype(np.int16) - immagine_b.astype(np.int16)).sum(2) > SOGLIA_PIXEL


def _croma(lab):
    """Chroma of one Lab triple: its distance from the neutral axis."""
    return float(np.hypot(float(lab[1]) - LAB_NEUTRO, float(lab[2]) - LAB_NEUTRO))


def _estremi(img, scatola, maschera):
    """Share of the changed pixels that are pure white or pure black (cursor arrow, black-and-white icons)."""
    x0, y0, x1, y1 = scatola
    hsv = cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2HSV).reshape(-1, 3)[maschera.reshape(-1)]
    if len(hsv) == 0:
        return 0.0
    bianchi = (hsv[:, 2] > BIANCO_V_MIN) & (hsv[:, 1] < BIANCO_S_MAX)
    neri = hsv[:, 2] < NERO_V_MAX
    return float((bianchi | neri).mean())


def misure_cambio(prima, subito_dopo, dopo_breve, dopo_lungo, scatola, maschera):
    """Measure one change across four frames: before, just after, 1.5 s after, 4.5 s after.

    `scatola` is [x0, y0, x1, y1] in pixels; `maschera` is the boolean mask of the changed pixels
    inside the box. Returns the persistence ratios, the Lab distance and the pure white/black share.
    """
    x0, y0, x1, y1 = scatola
    ritaglio_prima = prima[y0:y1, x0:x1]
    ritaglio_subito = subito_dopo[y0:y1, x0:x1]
    ritaglio_breve = dopo_breve[y0:y1, x0:x1]
    ritaglio_lungo = dopo_lungo[y0:y1, x0:x1]
    sel = maschera.reshape(-1)
    pixel_prima = cv2.cvtColor(ritaglio_prima, cv2.COLOR_BGR2LAB).reshape(-1, 3)[sel]
    lab_prima = np.median(pixel_prima.astype(float), axis=0)
    pixel_dopo = cv2.cvtColor(ritaglio_breve, cv2.COLOR_BGR2LAB).reshape(-1, 3)[sel]
    lab_dopo = np.median(pixel_dopo.astype(float), axis=0)
    hsv = cv2.cvtColor(ritaglio_breve, cv2.COLOR_BGR2HSV).reshape(-1, 3)[sel]
    bianchi = (hsv[:, 2] > BIANCO_V_MIN) & (hsv[:, 1] < BIANCO_S_MAX)
    neri = hsv[:, 2] < NERO_V_MAX
    return {
        # The change is still there after 1.5 s.
        "persiste": float(_diff(ritaglio_prima, ritaglio_breve)[maschera].mean()),
        # And it did not keep moving.
        "assestato": float(_diff(ritaglio_subito, ritaglio_breve)[maschera].mean()),
        # Still there after 4.5 s (the cursor goes away, a state stays).
        "tiene": float(_diff(ritaglio_prima, ritaglio_lungo)[maschera].mean()),
        "delta": _distanza(lab_prima, lab_dopo),
        # Pure white/black: the cursor arrow.
        "estremi": float((bianchi | neri).mean()),
    }


class Rivelatore:
    """Collects compact, persistent changes from the 2 fps BGR frames fed to it.

    Memory holds only ORIZZONTE+1 frames, the per-pixel "alive" counter and the crops of the candidates.
    """

    def __init__(self, larghezza, altezza, soglie=None):
        """Set the frame size (px) and the thresholds; `soglie` overrides the defaults by name."""
        self.w = int(larghezza)
        self.h = int(altezza)
        self.buf = collections.deque(maxlen=ORIZZONTE + 1)
        self.i = 0
        self.intervalli = 0
        self.vivi = np.zeros((self.h, self.w), np.uint16)
        self.candidati = []
        self.scartati = collections.Counter()
        self.k2 = np.ones((2, 2), np.uint8)
        self.k3 = np.ones((3, 3), np.uint8)
        self.k9 = np.ones((9, 9), np.uint8)
        self.s = {"persiste": 0.5, "assestato": 0.4, "tiene": 0.35, "delta": DELTA_MIN, "estremi": 0.9,
                  "npix": 30, "lato": 9, "riempimento": 0.25, "larga": 0.3, "alta": 0.12, "scena": 0.02,
                  "scena_dopo": 0.03, "vivo": 0.10, "ricorrente": 6}
        if soglie:
            self.s.update(soglie)

    def aggiungi(self, frame):
        """Append one frame; once the buffer is full, analyse the interval it starts."""
        self.buf.append(frame)
        if len(self.buf) == self.buf.maxlen:
            self._intervallo(list(self.buf))
            self.i += 1

    def fine(self):
        """Flush the remaining frames and return the surviving candidates.

        Drops changes in zones that are almost always alive, duplicates, and counters or animations
        (the same box changing again and again).
        """
        resto = list(self.buf)[1:] if len(self.buf) == self.buf.maxlen else list(self.buf)
        while len(resto) >= 2:
            self._intervallo(resto)
            resto = resto[1:]
            self.i += 1
        vivo = self.vivi >= self.s["vivo"] * max(1, self.intervalli)
        tenuti = []
        for candidato in self.candidati:
            x0, y0, x1, y1 = candidato["scatola"]
            if float(vivo[y0:y1, x0:x1].mean()) >= 0.5:
                self.scartati["zona sempre viva"] += 1
                continue
            tenuti.append(candidato)
        tenuti = self._unisci(tenuti)
        ricorrenti = []
        for candidato in tenuti:
            ripetizioni = sum(1 for altro in tenuti
                              if abs(altro["t"] - candidato["t"]) > 2.0
                              and _iou(altro["scatola"], candidato["scatola"]) > 0.5)
            ricorrenti.append(ripetizioni)
        # Counters and animations: they always change in the same spot.
        self.candidati = []
        for candidato, ripetizioni in zip(tenuti, ricorrenti):
            if ripetizioni < self.s["ricorrente"]:
                self.candidati.append(candidato)
        self.scartati["contatore o animazione"] += len(tenuti) - len(self.candidati)
        return self.candidati

    def _unisci(self, candidati):
        """Keep only the first of a change seen in two consecutive intervals (within 1.5 s, IoU > 0.5)."""
        risultato = []
        for candidato in sorted(candidati, key=lambda dato: dato["t"]):
            doppio = False
            for precedente in reversed(risultato):
                if candidato["t"] - precedente["t"] > 1.5:
                    break
                if _iou(candidato["scatola"], precedente["scatola"]) > 0.5:
                    doppio = True
                    break
            if doppio:
                self.scartati["doppione"] += 1
            else:
                risultato.append(candidato)
        return risultato

    def _intervallo(self, finestra):
        """Analyse the interval that starts at `finestra[0]` and queue the changes that look like controls.

        Frames used: before, just after, 1.5 s after and 4.5 s after (clamped to the window end).
        Each changed blob goes through the size, persistence, colour and white/black filters; the
        reason of every rejection is counted in `scartati`.
        """
        prima, subito_dopo = finestra[0], finestra[1]
        dopo_breve = finestra[min(3, len(finestra)-1)]
        dopo_lungo = finestra[min(ORIZZONTE, len(finestra)-1)]
        # Drop isolated compression dots; text strokes (1-2 px) survive.
        aperto = cv2.morphologyEx(_diff(prima, subito_dopo).astype(np.uint8), cv2.MORPH_OPEN, self.k2)
        self.intervalli += 1
        self.vivi[aperto > 0] += 1
        if not aperto.any():
            return
        # Orbit, preview, new page: not a control.
        if float(aperto.mean()) > self.s["scena"]:
            self.scartati["scena che cambia"] += 1
            return
        quanti, etichette, statistiche, _ = cv2.connectedComponentsWithStats(cv2.dilate(aperto, self.k9))
        tempo = round((self.i + 1) / float(FPS), 2)
        dopo_calmo = None
        comp = []
        for k in range(1, quanti):
            x, y, comp_w, comp_h = [int(valore) for valore in statistiche[k][:4]]
            if comp_w > self.s["larga"] * self.w or comp_h > self.s["alta"] * self.h:
                self.scartati["troppo grande"] += 1
                continue
            sub = (etichette[y:y+comp_h, x:x+comp_w] == k) & (aperto[y:y+comp_h, x:x+comp_w] > 0)
            if int(sub.sum()) < self.s["npix"]:
                self.scartati["troppo piccolo"] += 1
                continue
            indici_y, indici_x = np.nonzero(sub)
            x0 = x + int(indici_x.min())
            x1 = x + int(indici_x.max()) + 1
            y0 = y + int(indici_y.min())
            y1 = y + int(indici_y.max()) + 1
            if x1 - x0 < self.s["lato"] or y1 - y0 < self.s["lato"]:
                self.scartati["troppo piccolo"] += 1
                continue
            maschera = sub[y0-y:y1-y, x0-x:x1-x]
            scatola = [x0, y0, x1, y1]
            if float(maschera.mean()) < self.s["riempimento"]:
                self.scartati["tratto sottile (disegno, cursore)"] += 1
                continue
            comp.append((scatola, maschera))
        # A moving cursor leaves two small black-and-white blobs in the same interval (where it was,
        # where it is): both must go.
        piccole = []
        for indice, (scat, mask) in enumerate(comp):
            if scat[2] - scat[0] <= LATO_CURSORE and scat[3] - scat[1] <= LATO_CURSORE:
                if max(_estremi(prima, scat, mask), _estremi(subito_dopo, scat, mask)) >= 0.6:
                    piccole.append(indice)
        cursore = set(piccole) if len(piccole) >= 2 else set()
        for indice_comp, (scatola, maschera) in enumerate(comp):
            x0, y0, x1, y1 = scatola
            if indice_comp in cursore:
                self.scartati["cursore che si sposta"] += 1
                continue
            misure = misure_cambio(prima, subito_dopo, dopo_breve, dopo_lungo, scatola, maschera)
            if misure["persiste"] < self.s["persiste"] or misure["assestato"] > self.s["assestato"]:
                self.scartati["non persiste"] += 1
                continue
            if misure["tiene"] < self.s["tiene"]:
                self.scartati["non tiene"] += 1
                continue
            if misure["delta"] < self.s["delta"]:
                self.scartati["colore uguale"] += 1
                continue
            if misure["estremi"] >= self.s["estremi"]:
                self.scartati["cursore o icona bianco-nera"] += 1
                continue
            if dopo_calmo is None:
                differenza = _diff(subito_dopo, dopo_breve).astype(np.uint8)
                residuo = cv2.morphologyEx(differenza, cv2.MORPH_OPEN, self.k3).mean()
                dopo_calmo = float(residuo) <= self.s["scena_dopo"]
            if not dopo_calmo:
                self.scartati["scena che cambia dopo"] += 1
                continue
            sinistra, destra, sopra, sotto = MARGINE_OCR
            ox0, oy0 = max(0, x0 - sinistra), max(0, y0 - sopra)
            ox1, oy1 = min(self.w, x1 + destra), min(self.h, y1 + sotto)
            self.candidati.append({"t": tempo, "scatola": scatola, "misure": misure,
                                   "prima": prima[oy0:oy1, ox0:ox1].copy(),
                                   "dopo": dopo_breve[oy0:oy1, ox0:ox1].copy(),
                                   "origine": [ox0, oy0]})


def _iou(scatola_a, scatola_b):
    """Intersection over union of two boxes [x0, y0, x1, y1]; 0.0 when the union is empty."""
    sovr_x = max(0, min(scatola_a[2], scatola_b[2]) - max(scatola_a[0], scatola_b[0]))
    sovr_y = max(0, min(scatola_a[3], scatola_b[3]) - max(scatola_a[1], scatola_b[1]))
    inter = sovr_x * sovr_y
    unione = ((scatola_a[2]-scatola_a[0])*(scatola_a[3]-scatola_a[1])
              + (scatola_b[2]-scatola_b[0])*(scatola_b[3]-scatola_b[1]) - inter)
    return inter / float(unione) if unione > 0 else 0.0


# ---------------------------------------------------------------- name and direction of the control
def _pallino_in_testa(scatola, riga):
    """True if the change is a dot or checkbox (max 40 px, nearly square) at the start of text line `riga`."""
    larg = scatola[2] - scatola[0]
    alt = scatola[3] - scatola[1]
    if not (larg <= LATO_PALLINO and alt <= LATO_PALLINO and 0.5 <= larg / float(max(1, alt)) <= 2.0):
        return False
    return (scatola[0] + scatola[2]) / 2.0 <= riga[0] + 1.5 * max(1, riga[3] - riga[1])


def scegli_etichetta(scatola, righe):
    """Pick the OCR line that names the control behind a change box.

    `righe` is [(text, confidence, [x0, y0, x1, y1])] in screen pixels. Returns (text, box, relation)
    or None when no line can name it. The relation is one of:
      copre   = the change covers the text (button background, fading text)
      accanto = the change is a dot or checkbox beside the text (choice, tick)
      dentro  = the change sits inside a bigger text: a changing number, not a state (the caller drops it).
    """
    bx0, by0, bx1, by1 = scatola
    area_b = float(max(1, (bx1-bx0)*(by1-by0)))
    migliore = None
    for testo, conf, riquadro in righe:
        if conf < CONF_NOME or len(testo.strip()) < 2:
            continue
        sovr_x = max(0, min(bx1, riquadro[2]) - max(bx0, riquadro[0]))
        sovr_y = max(0, min(by1, riquadro[3]) - max(by0, riquadro[1]))
        inter = float(sovr_x*sovr_y)
        area_r = float(max(1, (riquadro[2]-riquadro[0])*(riquadro[3]-riquadro[1])))
        if inter >= 0.4 * area_r:
            punto = (inter, "copre")
        elif inter >= 0.8 * area_b and area_b < 0.4 * area_r:
            punto = (inter, "dentro")
        else:
            continue
        if migliore is None or punto[0] > migliore[0]:
            migliore = (punto[0], testo, riquadro, punto[1])
    # The dot read as an "O" together with the label: it is a choice, not a number.
    if migliore and migliore[3] == "dentro" and _pallino_in_testa(scatola, migliore[2]):
        parti = migliore[1].strip().split(None, 1)
        if len(parti) == 2 and len(parti[0]) <= 2 and (len(parti[0]) == 1 or not parti[0].isalpha()):
            return parti[1], migliore[2], "accanto"
    if migliore:
        return migliore[1].strip(), migliore[2], migliore[3]
    larg, alt = bx1 - bx0, by1 - by0
    # Only dots and checkboxes.
    if not (larg <= LATO_PALLINO and alt <= LATO_PALLINO and 0.5 <= larg / float(max(1, alt)) <= 2.0):
        return None
    centro_y = (by0 + by1) / 2.0
    scelti = []
    for testo, conf, riquadro in righe:
        if conf < CONF_NOME or len(testo.strip()) < 2:
            continue
        altezza = float(max(1, riquadro[3]-riquadro[1]))
        sovrapposto = min(by1, riquadro[3]) - max(by0, riquadro[1])
        if sovrapposto < 0.5 * min(altezza, alt) and not (riquadro[1] <= centro_y <= riquadro[3]):
            continue
        destra = riquadro[0] - bx1
        sinistra = bx0 - riquadro[2]
        if 0 <= destra <= 2.5 * altezza:
            scelti.append((destra, testo, riquadro))
        elif 0 <= sinistra <= 1.5 * altezza:
            scelti.append((sinistra + altezza, testo, riquadro))
    if not scelti:
        return None
    _, testo, riquadro = min(scelti, key=lambda candidato: candidato[0])
    return testo.strip(), riquadro, "accanto"


def _fondo_e_segno(img, riquadro):
    """Median Lab of the background (3 px ring around `riquadro`) and of the mark (pixels farthest from it).

    Returns (background, mark), or (None, None) when the ring or the box is empty.
    """
    x0, y0, x1, y1 = [int(round(valore)) for valore in riquadro]
    fondo = colore_anello(img, [x0, y0, x1, y1], 3)
    zona = img[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
    if fondo is None or zona.size == 0:
        return None, None
    lab = cv2.cvtColor(zona, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(float)
    distanze = np.linalg.norm(lab - fondo, axis=1)
    lontani = lab[distanze >= max(20.0, float(np.percentile(distanze, 80)))]
    return fondo, (np.median(lontani, axis=0) if len(lontani) >= 4 else fondo)


def misura_stato(prima, dopo, riquadro):
    """Compare background and mark of a control before and after a change.

    Returns {'delta', 'verso'} (verso is 'acceso' or 'spento'), or None if the control does not change.
    The control itself is examined (the box of the text or dot), not the changed area: a circle drawn
    around it or a cursor in the same area do not count.
    """
    fondo_prima, segno_prima = _fondo_e_segno(prima, riquadro)
    fondo_dopo, segno_dopo = _fondo_e_segno(dopo, riquadro)
    if fondo_prima is None or fondo_dopo is None:
        return None
    d_fondo = _distanza(fondo_prima, fondo_dopo)
    d_segno = _distanza(segno_prima, segno_dopo)
    delta = max(d_fondo, d_segno)
    if delta < DELTA_MIN:
        return None
    if d_fondo >= d_segno:
        croma_prima, croma_dopo = _croma(fondo_prima), _croma(fondo_dopo)
    else:
        croma_prima, croma_dopo = _croma(segno_prima), _croma(segno_dopo)
    if croma_dopo - croma_prima >= 8.0:
        verso = "acceso"
    elif croma_prima - croma_dopo >= 8.0:
        verso = "spento"
    else:
        # More contrast = on.
        contrasto_prima = abs(segno_prima[0] - fondo_prima[0])
        contrasto_dopo = abs(segno_dopo[0] - fondo_dopo[0])
        verso = "acceso" if contrasto_dopo >= contrasto_prima else "spento"
    return {"delta": delta, "verso": verso}


def e_voce(testo):
    """True if `testo` looks like an interface item: it has letters, while a counter is mostly digits."""
    lettere = sum(1 for carattere in testo if carattere.isalpha())
    cifre = sum(1 for carattere in testo if carattere.isdigit())
    if lettere < 3 or cifre > 0.3 * (lettere + cifre):
        return False
    ripulito = testo.strip()
    # A number at the end (with or without a unit) or at the start is a field value, not a state.
    return not (re.search(r"\d[\d.,]*\s*\S{0,2}$", ripulito) or re.match(r"\d", ripulito))


class Lettore:
    """OCR reader for crops, based on rapidocr (venv libraries only); the engine loads on first use."""

    def __init__(self):
        """Create the reader without loading the OCR engine yet."""
        self.motore = None
        self.chiamate = 0

    def leggi(self, immagine, origine):
        """Run OCR on a crop and return [(text, confidence, [x0, y0, x1, y1])] in screen pixels.

        `origine` is the [x, y] of the crop inside the screen; it is added to the boxes found.
        """
        if self.motore is None:
            from rapidocr_onnxruntime import RapidOCR
            # Minimum side 128: the crops are short (35-100 px) and the default (736) magnifies them
            # 10-20 times, 2 s per reading for nothing.
            self.motore = RapidOCR(intra_op_num_threads=1, inter_op_num_threads=1,
                                   det_limit_side_len=128, det_limit_type="min")
        self.chiamate += 1
        risultato, _ = self.motore(immagine, use_cls=False)
        righe = []
        for elemento in (risultato or []):
            ascisse = [punto[0] for punto in elemento[0]]
            ordinate = [punto[1] for punto in elemento[0]]
            righe.append((str(elemento[1]), float(elemento[2]),
                          [int(min(ascisse)) + origine[0], int(min(ordinate)) + origine[1],
                           int(max(ascisse)) + origine[0], int(max(ordinate)) + origine[1]]))
        return righe


def _stessa_voce_prima(righe_prima, riquadro, testo):
    """True if the control already had the same label in the same place before the change.

    A control that changes state was there with the same text; an item that just appeared or whose text
    differs is not a state change. `righe_prima` is the OCR of the frame before.
    """
    for testo_prima, conf, riquadro_prima in righe_prima:
        sovr_x = max(0, min(riquadro[2], riquadro_prima[2]) - max(riquadro[0], riquadro_prima[0]))
        sovr_y = max(0, min(riquadro[3], riquadro_prima[3]) - max(riquadro[1], riquadro_prima[1]))
        area = float(max(1, min((riquadro[2]-riquadro[0])*(riquadro[3]-riquadro[1]),
                                (riquadro_prima[2]-riquadro_prima[0])*(riquadro_prima[3]-riquadro_prima[1]))))
        if sovr_x * sovr_y >= 0.4 * area and dig(testo_prima) == dig(testo):
            lettere_prima = let(pulisci_ocr(testo_prima))
            lettere_dopo = let(testo)
            # The reading can lose the first letters.
            if (ratio(pulisci_ocr(testo_prima), testo) >= 0.7
                    or (len(lettere_prima) >= 6 and lettere_prima in lettere_dopo)
                    or (len(lettere_dopo) >= 6 and lettere_dopo in lettere_prima)):
                return True
    return False


def _migliore_coperta(candidato, righe):
    """Among the texts covered by the change, pick the one whose control changes most.

    Returns (name, box, state) or None. `candidato` is a candidate dict with its crops, `righe` the OCR lines.
    """
    bx0, by0, bx1, by1 = candidato["scatola"]
    ox, oy = candidato["origine"]
    migliore = None
    for testo, conf, riquadro in righe:
        if conf < CONF_NOME:
            continue
        sovr_x = max(0, min(bx1, riquadro[2]) - max(bx0, riquadro[0]))
        sovr_y = max(0, min(by1, riquadro[3]) - max(by0, riquadro[1]))
        if sovr_x * sovr_y < 0.4 * max(1, (riquadro[2]-riquadro[0])*(riquadro[3]-riquadro[1])):
            continue
        nome = pulisci_ocr(testo)
        if not e_voce(nome):
            continue
        stato = misura_stato(candidato["prima"], candidato["dopo"],
                             [riquadro[0] - ox, riquadro[1] - oy, riquadro[2] - ox, riquadro[3] - oy])
        if stato is not None and (migliore is None or stato["delta"] > migliore[2]["delta"]):
            migliore = (nome, riquadro, stato)
    return migliore


def nomina(candidati, lettore, larghezza, altezza, stat, diario=None):
    """Turn candidates into named changes with a direction; drop the ones that cannot be used.

    Dropped: no name, a changing number, a control that does not change. `stat` counts the reasons.
    `diario`, if given, collects the outcome of every candidate (to see why a fact is missing).
    Returns a list of change dicts with normalised zones.
    """
    cambi = []

    def esito(candidato, motivo, testo=""):
        """Append one diary row for `candidato` when the diary is enabled."""
        if diario is not None:
            diario.append({"t": candidato["t"], "scatola": candidato["scatola"],
                           "delta": round(candidato["misure"]["delta"], 1),
                           "estremi": round(candidato["misure"].get("estremi", 0.0), 2),
                           "esito": motivo, "testo": testo})

    ordinati = sorted(candidati, key=lambda candidato: -candidato["misure"]["delta"])
    if len(ordinati) > MAX_OCR:
        stat["oltre il tetto OCR"] += len(ordinati) - MAX_OCR
    for candidato in ordinati[:MAX_OCR]:
        righe = lettore.leggi(candidato["dopo"], candidato["origine"])
        scelta = scegli_etichetta(candidato["scatola"], righe)
        if scelta is None:
            stat["senza nome"] += 1
            esito(candidato, "senza nome", " | ".join(riga[0] for riga in righe)[:60])
            continue
        testo, riquadro, relazione = scelta
        # Dot read inside the line: the label is re-read only to the right of the dot.
        if relazione == "dentro" and _pallino_in_testa(candidato["scatola"], riquadro):
            ox, oy = candidato["origine"]
            bx1 = candidato["scatola"][2] + 2
            centro_y = (candidato["scatola"][1] + candidato["scatola"][3]) / 2.0
            vicine = [riga for riga in lettore.leggi(candidato["dopo"][:, max(0, bx1 - ox):], [bx1, oy])
                      if riga[1] >= CONF_NOME and riga[2][1] <= centro_y <= riga[2][3]]
            if vicine:
                testo, _, riquadro = max(vicine, key=lambda riga: riga[2][2] - riga[2][0])
                relazione = "accanto"
        if relazione == "dentro":
            stat["valore che cambia"] += 1
            esito(candidato, "valore che cambia", testo)
            continue
        testo = pulisci_ocr(testo)
        if not e_voce(testo):
            stat["non e una voce"] += 1
            esito(candidato, "non e una voce", testo)
            continue
        ox, oy = candidato["origine"]
        if relazione == "copre":
            locale = [riquadro[0] - ox, riquadro[1] - oy, riquadro[2] - ox, riquadro[3] - oy]
        else:
            scat = candidato["scatola"]
            locale = [scat[0] - ox, scat[1] - oy, scat[2] - ox, scat[3] - oy]
        stato = misura_stato(candidato["prima"], candidato["dopo"], locale)
        # The change covers several texts (a circle drawn around them): the one that changes most wins.
        if relazione == "copre":
            migliore = _migliore_coperta(candidato, righe)
            if migliore is not None and (stato is None or migliore[2]["delta"] > stato["delta"]):
                testo, riquadro, stato = migliore
        if stato is None:
            stat["il controllo non cambia"] += 1
            esito(candidato, "il controllo non cambia", testo)
            continue
        if not _stessa_voce_prima(lettore.leggi(candidato["prima"], candidato["origine"]), riquadro, testo):
            stat["prima non c'era (comparsa, scritta cambiata)"] += 1
            esito(candidato, "prima non c'era", testo)
            continue
        esito(candidato, "ok " + stato["verso"], testo)
        x0, y0, x1, y1 = riquadro if relazione == "copre" else candidato["scatola"]
        zona = [x0 / float(larghezza), y0 / float(altezza), x1 / float(larghezza), y1 / float(altezza)]
        cambi.append({"t": candidato["t"], "testo": testo[:MAX_TESTO],
                      "zona_norm": [min(1.0, max(0.0, valore)) for valore in zona],
                      "delta": stato["delta"], "stato": stato["verso"], "rif": [], "relazione": relazione})
    return cambi


# ---------------------------------------------------------------- pure function
def _allineate(zona_a, zona_b):
    """True if two normalised zones have similar heights and share a row or a column."""
    altezza_a = max(1e-6, zona_a[3] - zona_a[1])
    altezza_b = max(1e-6, zona_b[3] - zona_b[1])
    if not 0.5 <= altezza_a / altezza_b <= 2.0:
        return False
    centro_a = (zona_a[1]+zona_a[3])/2.0
    centro_b = (zona_b[1]+zona_b[3])/2.0
    stessa_riga = abs(centro_a - centro_b) <= 0.8 * max(altezza_a, altezza_b)
    stessa_colonna = abs(zona_a[0] - zona_b[0]) <= max(0.006, 0.75 * max(altezza_a, altezza_b))
    return stessa_riga or stessa_colonna


def _evento(t, durata, tipo, zona, testo, forza, rif, dati, stampa="riga"):
    """Build one event dict of the common schema, rounding time, zone and strength; `punto` is the centre."""
    zona = [round(float(valore), 4) for valore in zona]
    return {"t": round(float(t), 2), "durata": round(float(durata), 2), "tipo": tipo, "zona": zona,
            "punto": [round((zona[0]+zona[2])/2.0, 4), round((zona[1]+zona[3])/2.0, 4)],
            "testo": testo[:MAX_TESTO], "forza": round(float(forza), 4), "stampa": stampa,
            "rif": list(rif), "dati": dati}


def _limita(eventi_ordinati, massimo=MAX_PER_MINUTO, finestra=60.0):
    """Cap the events to `massimo` in any window of `finestra` seconds, keeping the strongest.

    Returns the kept events sorted by time, type and text.
    """
    tenuti = []
    for evento in sorted(eventi_ordinati, key=lambda dato: (-dato["forza"], dato["t"])):
        tempi = sorted([tenuto["t"] for tenuto in tenuti] + [evento["t"]])
        if any(tempi[j+massimo] - tempi[j] < finestra for j in range(0, len(tempi) - massimo)):
            continue
        tenuti.append(evento)
    return sorted(tenuti, key=lambda dato: (dato["t"], dato["tipo"], dato["testo"]))


def parlato(testo, parole, t, raggio=20.0):
    """Fraction (0..1) of the words of `testo` (lowercase, 3+ letters) heard within `raggio` s of time `t`.

    `parole` is the list of spoken words as (start, end, text). A word counts when it matches exactly,
    or, for words of 5+ letters, when its similarity ratio is at least 0.85.
    """
    tokens = []
    for pezzo in testo.split():
        parola = "".join(carattere for carattere in pezzo.lower() if carattere.isalpha())
        if len(parola) >= 3:
            tokens.append(parola)
    if not tokens:
        return 0.0
    dette = []
    for parola in parole:
        if len(parola) >= 3 and parola[0] - raggio <= t <= parola[1] + raggio:
            dette.append("".join(carattere for carattere in str(parola[2]).lower() if carattere.isalpha()))
    trovati = 0
    for tok in tokens:
        for detta in dette:
            if tok == detta or (len(tok) >= 5 and len(detta) >= 5 and ratio(tok, detta) >= 0.85):
                trovati += 1
                break
    return trovati / float(len(tokens))


def vicino_a_parola(t, parole, raggio=1.4):
    """True if a spoken word falls within `raggio` seconds of `t`: the event has a sentence to attach to."""
    return any(parola[0] - raggio <= t <= parola[1] + raggio for parola in parole if len(parola) >= 2)


def _punteggio(cambio, parole):
    """Score a change: 40% its colour delta (saturating at 60) plus 60% how much of its text is spoken."""
    return 0.4 * min(1.0, cambio["delta"] / 60.0) + 0.6 * parlato(cambio["testo"], parole, cambio["t"])


def _forza_voce_scelta(testo, t, parole):
    """Strength of a chosen item at time `t`, scoring it with the assumed delta DELTA_VOCE_SCELTA."""
    punteggio = _punteggio({"delta": DELTA_VOCE_SCELTA, "testo": testo, "t": t}, parole)
    return FORZA_BASE + FORZA_PESO * min(1.0, punteggio)


def _stessa_voce(primo, secondo):
    """True if two changes name the same item: equal or contained text, similar text, or overlapping zones."""
    testo_a, testo_b = primo["testo"].lower(), secondo["testo"].lower()
    sovrapposte = _iou(primo["zona_norm"], secondo["zona_norm"]) > 0.2
    return (testo_a == testo_b or testo_a in testo_b or testo_b in testo_a
            or ratio(testo_a, testo_b) >= 0.8 or sovrapposte)


def _fondi_vicini(cambi, parole):
    """Merge fragments of one button change (border, label), keeping the best-scoring one within 1.5 s."""
    tenuti = []
    for cambio in sorted(cambi, key=lambda dato: (-_punteggio(dato, parole), dato["t"])):
        if any(abs(cambio["t"] - tenuto["t"]) <= 1.5 and _stessa_voce(cambio, tenuto) for tenuto in tenuti):
            continue
        tenuti.append(cambio)
    return sorted(tenuti, key=lambda dato: dato["t"])


def calcola(dati):
    """Turn the observations and changes in `dati` into events of the common schema.

    `dati` holds readings, bars, changes, cursor track and spoken words. A chosen item needs a stable
    background and a spoken mention; two opposite aligned changes become a choice; the result is capped
    per minute and, with SOLO_CON_VOCE, limited to events near a spoken word. Free of side effects.
    """
    risultato, attive = [], {}
    cursori = dati.get("cursori", [])
    parole = dati.get("parole", [])
    for lettura in sorted(dati.get("letture", []), key=lambda dato: dato["t"]):
        voci = lettura["voci"]
        # Is the same item still chosen 1 s later?
        dopo = scelte([dict(voce, lab=voce.get("lab2", voce.get("lab"))) for voce in voci])
        confermate = set(voce["testo"] for voce in dopo)
        ora = {}
        for voce in scelte(voci):
            nome = pulisci_ocr(voce["testo"])
            # With the cursor over it, it is only the mouse passing.
            if (voce["testo"] in confermate and voce.get("forza", 1.0) >= 0.85 and e_voce(nome)
                    and _cursore_fuori(cursori, lettura["t"], voce["zona_norm"])):
                ora[nome] = voce
        attive = {chiave: voce for chiave, voce in attive.items() if chiave in ora}
        for chiave, voce in ora.items():
            # Already reported in the previous reading.
            if chiave in attive:
                continue
            attive[chiave] = voce
            zona = voce["zona_norm"]
            # A highlighted item is news only if it is mentioned aloud.
            if parlato(chiave, parole, lettura["t"]) < 0.5:
                continue
            forza = _forza_voce_scelta(chiave, lettura["t"], parole)
            risultato.append(_evento(lettura["t"], 1.0, "scelto", zona, chiave, forza, [],
                                     {"dove": eventi.dove(zona)}))
    # Item highlighted in a zone that never changes: it counts only if it is mentioned aloud.
    for barra in dati.get("barre", []):
        if not e_voce(barra["testo"]) or parlato(barra["testo"], parole, barra["t"]) < 0.5:
            continue
        zona = barra["zona_norm"]
        forza = _forza_voce_scelta(barra["testo"], barra["t"], parole)
        risultato.append(_evento(barra["t"], 0.0, "scelto", zona, barra["testo"], forza, [],
                                 {"dove": eventi.dove(zona)}))
    cambi, ultimo = [], {}
    for cambio in sorted(dati.get("cambi", []), key=lambda dato: dato["t"]):
        chiave = (cambio["testo"], cambio["stato"])
        if chiave in ultimo and cambio["t"]-ultimo[chiave] <= 1.0:
            continue
        ultimo[chiave] = cambio["t"]
        cambi.append(dict(cambio))
    cambi = _fondi_vicini(cambi, parole)
    persi = set()
    # One turns on and another, aligned, turns off: it is a choice.
    for i, nuovo in enumerate(cambi):
        if nuovo["stato"] != "acceso":
            continue
        for j, vecchio in enumerate(cambi):
            if (vecchio["stato"] == "spento" and j not in persi and vecchio["testo"] != nuovo["testo"]
                    and abs(vecchio["t"]-nuovo["t"]) <= 1.5
                    and _allineate(vecchio["zona_norm"], nuovo["zona_norm"])):
                nuovo["scelta"] = True
                persi.add(j)
                break
    for i, cambio in enumerate(cambi):
        if i in persi:
            continue
        zona = cambio["zona_norm"]
        forza = FORZA_BASE + FORZA_PESO * min(1.0, _punteggio(cambio, parole))
        if cambio.get("scelta"):
            risultato.append(_evento(cambio["t"], 0.0, "scelto", zona, cambio["testo"], forza,
                                     cambio.get("rif", []), {"dove": eventi.dove(zona)}))
        else:
            risultato.append(_evento(cambio["t"], 0.0, "cambio", zona, cambio["testo"], forza,
                                     cambio.get("rif", []), {"stato": cambio["stato"]}))
    # The same chosen item read still and seen changing: the change stays (the right instant).
    unici = []
    for evento in sorted(risultato, key=lambda dato: (0 if dato["durata"] == 0.0 else 1, dato["t"])):
        if any(tenuto["tipo"] == "scelto" and evento["tipo"] == "scelto"
               and tenuto["testo"] == evento["testo"] and abs(tenuto["t"]-evento["t"]) <= 3.0
               for tenuto in unici):
            continue
        unici.append(evento)
    if SOLO_CON_VOCE:
        unici = [evento for evento in unici if vicino_a_parola(evento["t"], parole)]
    finali = _limita(unici)
    for numero, evento in enumerate(finali, 1):
        evento["id"] = "E%d" % numero
        evento["canale"] = "stato"
    return finali


# ---------------------------------------------------------------- reading the video
def fotogrammi(video, larghezza, altezza):
    """Yield (number, BGR frame) for one frame every 0.5 s of `video`, scaled to `larghezza` x `altezza`.

    Frames come from an ffmpeg pipe (keyframes only), at the scale used by ocr.py.
    """
    comando = [comune.ffmpeg(), "-v", "error", "-skip_frame", "noref", "-i", video, "-vf",
               "fps=%d,scale=%d:%d" % (FPS, larghezza, altezza), "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    processo = subprocess.Popen(comando, stdout=subprocess.PIPE)
    numero = 0
    try:
        while True:
            dati = processo.stdout.read(larghezza * altezza * 3)
            if len(dati) < larghezza * altezza * 3:
                break
            yield numero, np.frombuffer(dati, np.uint8).reshape(altezza, larghezza, 3)
            numero += 1
    finally:
        processo.stdout.close()
        processo.kill()
        processo.wait()


def _clic_cursore(lavoro):
    """Read the clicks from eventi_cursore.json of the work folder as [(time, point, id)]."""
    doc = _json(os.path.join(lavoro, "eventi_cursore.json"), {})
    return [(float(evento["t"]), evento.get("punto"), str(evento.get("id")))
            for evento in doc.get("eventi", []) if evento.get("tipo") == "clic" and evento.get("id")]


def _cursori(lavoro):
    """Load the cursor track from cursore.json, falling back to the 'cur' samples of mov.json (normalised)."""
    denso = _json(os.path.join(lavoro, "cursore.json"), [])
    traccia = eventi.cursore_da_mov(denso)
    if traccia:
        return traccia
    mov = _json(os.path.join(lavoro, "mov.json"), {})
    w = float(mov.get("W") or 1.0)
    h = float(mov.get("H") or 1.0)
    convertiti = [[campione[0], campione[1]/w, campione[2]/h, "", 1.0]
                  for campione in mov.get("cur", []) if isinstance(campione, list) and len(campione) >= 3]
    return eventi.cursore_da_mov(convertiti)


def leggi(video, lavoro, lettore=None):
    """Read the video once at 2 fps and collect what `calcola` needs.

    Gathers the item colours of the ocr3 readings (for E1), the compact changes with their names (for E2),
    the highlighted bars, the cursor track and the spoken words from the work folder `lavoro`.
    Returns the dict {letture, cambi, barre, cursori, parole, stat, diario}.
    """
    ocr = _json(os.path.join(lavoro, "ocr3.json"), {})
    larghezza_video, altezza_video, _ = comune.info_video(video)
    w = LARGHEZZA
    h = int(round(altezza_video * w / float(larghezza_video) / 2.0) * 2)
    larghezza_ocr = float(ocr.get("W") or w)
    altezza_ocr = float(ocr.get("H") or h)
    grezze = {}
    for lettura_grezza in ocr.get("letture", []):
        if isinstance(lettura_grezza, list) and len(lettura_grezza) == 4:
            numero_frame = int(round(float(lettura_grezza[0]) * FPS))
            grezze.setdefault(numero_frame, []).append(lettura_grezza)
    letture = []
    rileggere = {}
    rivelatore = Rivelatore(w, h)
    stat = collections.Counter()
    lettore = lettore or Lettore()
    barre = Barre(lettore, w, h)
    ultimo = None
    t_lettura = time.perf_counter()
    for numero, frame in fotogrammi(video, w, h):
        rivelatore.aggiungi(frame)
        barre.aggiungi(numero, frame)
        ultimo = frame
        if numero == 2 or (numero > 0 and numero % PASSO_CHIAVE == 0):
            barre.fotogramma_chiave(numero, frame)
        # One second after the reading: is the background of the same items still the same?
        for lettura in rileggere.pop(numero, []):
            for voce in lettura["voci"]:
                voce["lab2"] = colore_anello(frame, voce["zona"])
        for tempo, xbase, ybase, valori in grezze.get(numero, []):
            voci = []
            for voce in valori:
                if not isinstance(voce, list) or len(voce) < 6:
                    continue
                testo, forza, ax, ay, bx, by = voce[:6]
                zona = [xbase+ax, ybase+ay, xbase+bx, ybase+by]
                voci.append({"testo": str(testo)[:MAX_TESTO], "forza": float(forza), "zona": zona,
                             "zona_norm": [zona[0]/larghezza_ocr, zona[1]/altezza_ocr,
                                           zona[2]/larghezza_ocr, zona[3]/altezza_ocr],
                             "lab": colore_anello(frame, zona)})
            lettura = {"t": float(tempo), "voci": voci}
            letture.append(lettura)
            rileggere.setdefault(numero + 2, []).append(lettura)
    if ultimo is not None:
        barre.fine(ultimo)
    t_ocr = time.perf_counter()
    stat["secondi_video"] = round(t_ocr - t_lettura, 1)
    candidati = rivelatore.fine()
    stat.update(rivelatore.scartati)
    stat.update(barre.stat)
    diario = [] if comune.ambiente("STATO_DIARIO") else None
    cambi = nomina(candidati, lettore, w, h, stat, diario)
    parole = _json(os.path.join(lavoro, "parole.json"), [])
    clic = _clic_cursore(lavoro)
    # A click from channel D near the change confirms it.
    for cambio in cambi:
        zona = cambio["zona_norm"]
        margine = 0.03
        cambio["rif"] = [id_clic for t_clic, punto, id_clic in clic
                         if -1.0 <= cambio["t"] - t_clic <= 1.0 and punto
                         and zona[0]-margine <= punto[0] <= zona[2]+margine
                         and zona[1]-margine <= punto[1] <= zona[3]+margine][:2]
    stat["secondi_ocr_cambi"] = round(time.perf_counter() - t_ocr, 1)
    stat["intervalli"] = rivelatore.intervalli
    stat["candidati"] = len(candidati)
    stat["ocr_chiamate"] = lettore.chiamate
    return {"letture": letture, "cambi": cambi, "barre": barre.eventi, "cursori": _cursori(lavoro),
            "parole": parole, "stat": dict(stat), "diario": diario}


def documento(eventi_calcolati, secondi, letture, extra=None):
    """Build the eventi_stato.json document: schema header, run statistics (plus `extra`) and the events."""
    stat = {"secondi": round(float(secondi), 3), "letture": int(letture), "eventi": len(eventi_calcolati)}
    if extra:
        stat.update(extra)
    return {"schema": 1, "canale": "stato", "lettera": "E", "versione": VERSIONE, "sezione": "STATO",
            "legenda": LEGENDA, "formati": dict(FORMATI), "stat": stat, "eventi": eventi_calcolati}


def main(argv):
    """Command line entry: `canale_stato.py <video.mp4> <work folder>`.

    Writes eventi_stato.json (and stato_diario.json when the diary is on) in the work folder and
    prints a one-line summary.
    """
    if len(argv) != 3:
        raise SystemExit("uso: canale_stato.py <video.mp4> <cartella _lavoro>")
    video = os.path.abspath(argv[1])
    lavoro = os.path.abspath(argv[2])
    inizio = time.perf_counter()
    dati = leggi(video, lavoro)
    calcolati = calcola(dati)
    secondi = time.perf_counter() - inizio
    extra = dict(dati.get("stat", {}))
    extra["ram_picco_mb"] = ram_picco_mb()
    eventi.scrivi(os.path.join(lavoro, "eventi_stato.json"),
                  documento(calcolati, secondi, len(dati["letture"]), extra))
    # With DEMOBRIEF_STATO_DIARIO, or the compatible AD_MOVIOLA_STATO_DIARIO.
    if dati.get("diario") is not None:
        with open(os.path.join(lavoro, "stato_diario.json"), "w", encoding="utf-8") as f:
            json.dump({"candidati": dati["diario"], "barre": dati.get("barre", []),
                       "cambi": dati.get("cambi", [])}, f, ensure_ascii=False)
    print("canale_stato: eventi=%d letture=%d tempo=%.1fs ram_picco=%s MB scartati=%s"
          % (len(calcolati), len(dati["letture"]), secondi, extra["ram_picco_mb"],
             json.dumps(dati.get("stat", {}), ensure_ascii=False)))


if __name__ == "__main__":
    main(sys.argv)
