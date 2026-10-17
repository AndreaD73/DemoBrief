# -*- coding: utf-8 -*-
"""Canale E: stato dell'interfaccia (voce scelta, controllo acceso o spento).

Nessuna parola, posizione o soglia legata a un programma o a un video: il canale guarda solo
come cambia lo schermo.
 - E1 scelto: in una lettura di schermo fermo (ocr3), fra voci allineate una sola ha il fondo diverso.
 - E2 acceso/spento: a 2 fps ogni cambio COMPATTO e PERSISTENTE dello schermo (non la vista 3D, non
   il cursore, non un tooltip, non un numero che cambia) e' un innesco; il nome del controllo lo
   da' una lettura OCR del ritaglio nell'istante dopo il cambio; il verso (acceso/spento) lo da' il
   confronto prima/dopo (colore piu' vivo o testo piu' contrastato = acceso).
 - Due cambi opposti fra voci allineate (una si accende, l'altra si spegne) = una SCELTA.
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # con python -I la cartella dello script non e' nel percorso
import eventi
import comune
from testi import pulisci_ocr, ratio, dig, let

VERSIONE = "0.3"
FORMATI = {"scelto": "scelto {ogg} ({dove})", "cambio": "{stato} {ogg}"}
LEGENDA = "STATO = voce scelta o controllo acceso/spento nell'interfaccia"

LARGHEZZA = 1280          # i riquadri di ocr3 e le soglie di pixel sono in questa scala
FPS = 2                   # un fotogramma ogni 0,5 s
SOGLIA_PIXEL = 60         # somma delle differenze BGR oltre la quale un pixel e' "cambiato"
ORIZZONTE = 9             # fotogrammi guardati in avanti: +3 (1,5 s) conferma, +9 (4,5 s) conferma la tenuta
MAX_PER_MINUTO = 6        # tetto del canale: oltre e' rumore
SOLO_CON_VOCE = True      # limita l'uscita agli eventi vicini alla voce, evitando eventi isolati nel ramo 'fx' di costruisci.py
MARGINE_OCR = (60, 240, 10, 10)   # sinistra, destra, sopra, sotto attorno al cambio, per leggere il nome
CONF_NOME = 0.75          # fiducia minima dell'OCR per dare un nome a un controllo
MAX_OCR = 150            # letture OCR al massimo per video (i candidati piu' netti)
BARRE_CROMA = 25.0        # croma Lab oltre il quale un pixel e' "colorato" (le interfacce sono quasi tutte grigie)
MAX_BARRE = 12            # barre lette al massimo per fotogramma chiave
PASSO_CHIAVE = 40         # un fotogramma chiave ogni 40 fotogrammi (20 s), piu' il secondo secondo del video


# ---------------------------------------------------------------- utilita' di lettura
def _json(percorso, predefinito):
    try:
        with open(percorso, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, TypeError):
        return predefinito


def colore_anello(frame, zona, spessore=3):
    """Mediana Lab dell'anello esterno alla zona."""
    if frame is None:
        return None
    h, w = frame.shape[:2]
    x0, y0, x1, y1 = [int(round(v)) for v in zona]
    x0 = max(0, min(w - 1, x0)); x1 = max(x0 + 1, min(w, x1))
    y0 = max(0, min(h - 1, y0)); y1 = max(y0 + 1, min(h, y1))
    ax0 = max(0, x0 - spessore); ay0 = max(0, y0 - spessore)
    ax1 = min(w, x1 + spessore); ay1 = min(h, y1 + spessore)
    ritaglio = frame[ay0:ay1, ax0:ax1]
    maschera = np.ones(ritaglio.shape[:2], dtype=bool)
    maschera[y0-ay0:y1-ay0, x0-ax0:x1-ax0] = False
    pixel = ritaglio[maschera]
    if pixel.size == 0:
        return None
    lab = cv2.cvtColor(pixel.reshape(-1, 1, 3), cv2.COLOR_BGR2LAB)
    return np.median(lab.reshape(-1, 3), axis=0).astype(float)


def _distanza(a, b):
    return float(np.linalg.norm(np.asarray(a, dtype=float) - np.asarray(b, dtype=float)))


def ram_picco_mb():
    """Picco di memoria del processo in MB."""
    if sys.platform != "win32":
        try:
            import resource
            valore = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            divisore = 1048576.0 if sys.platform == "darwin" else 1024.0
            return round(valore / divisore, 1)
        except (ImportError, AttributeError, OSError):
            return None

    try:
        import ctypes
        from ctypes import wintypes

        class _Contatori(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        c = _Contatori(); c.cb = ctypes.sizeof(c)
        info = ctypes.windll.kernel32.K32GetProcessMemoryInfo
        info.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Contatori), wintypes.DWORD]; info.restype = wintypes.BOOL
        corrente = ctypes.windll.kernel32.GetCurrentProcess; corrente.restype = wintypes.HANDLE
        if not info(corrente(), ctypes.byref(c), c.cb): return None
        return round(c.PeakWorkingSetSize / 1048576.0, 1)
    except Exception:
        return None


# ---------------------------------------------------------------- E1: voce scelta in una lettura
def _gruppi(voci):
    """Gruppi di almeno 3 voci allineate (stessa riga o stessa colonna), di altezza simile; in colonna contigue
    (una voce entra solo se dista dalla vicina al massimo 2,5 altezze: le sezioni di un menu restano separate)."""
    gruppi = set()
    for i, voce in enumerate(voci):
        h = max(1.0, voce["zona"][3] - voce["zona"][1])
        cy = (voce["zona"][1] + voce["zona"][3]) / 2.0
        verticali, orizzontali = [], []
        for j, altra in enumerate(voci):
            ah = max(1.0, altra["zona"][3] - altra["zona"][1])
            acy = (altra["zona"][1] + altra["zona"][3]) / 2.0
            simile = 0.75 <= ah / h <= 1.25
            if simile and abs(altra["zona"][0] - voce["zona"][0]) <= max(3.0, 0.3*h): verticali.append(j)
            if simile and abs(acy-cy) <= 0.5*max(h, ah): orizzontali.append(j)
        verticali = _contigue(voci, verticali, i)
        if len(verticali) >= 3: gruppi.add(tuple(sorted(verticali)))
        if len(orizzontali) >= 3: gruppi.add(tuple(sorted(orizzontali)))
    return sorted(gruppi)


def _contigue(voci, indici, i):
    """Dalla colonna intera, la parte contigua che contiene la voce i."""
    ordine = sorted(indici, key=lambda k: voci[k]["zona"][1])
    if i not in ordine: return [i]
    pos = ordine.index(i); da = pos; a = pos
    def vuoto(k1, k2):
        altezza = max(voci[k1]["zona"][3] - voci[k1]["zona"][1], voci[k2]["zona"][3] - voci[k2]["zona"][1], 1.0)
        return voci[k2]["zona"][1] - voci[k1]["zona"][3] <= 2.5 * altezza
    while da > 0 and vuoto(ordine[da-1], ordine[da]): da -= 1
    while a < len(ordine) - 1 and vuoto(ordine[a], ordine[a+1]): a += 1
    return ordine[da:a+1]


def scelte(voci):
    """Le voci scelte: in ogni gruppo di voci allineate (stessa riga o stessa colonna) la maggioranza ha lo stesso fondo
    (entro 8 in Lab); una voce che se ne stacca di piu' di 20 ed e' piu' colorata della maggioranza e' la scelta.
    Le voci scure o di altra natura finite per allineamento nel gruppo non contano come scelta."""
    trovate = {}
    for gruppo in _gruppi(voci):
        validi = [i for i in gruppo if voci[i].get("lab") is not None]
        if len(validi) < 3: continue
        labs = np.asarray([voci[i]["lab"] for i in validi], dtype=float)
        vicini = [np.linalg.norm(labs - labs[k], axis=1) <= 8.0 for k in range(len(validi))]
        maggioranza = vicini[int(np.argmax([v.sum() for v in vicini]))]
        minimo = 2 if len(validi) == 3 else 3
        if maggioranza.sum() < minimo or maggioranza.sum() < 0.4 * len(validi): continue
        centro = np.median(labs[maggioranza], axis=0); croma_centro = _croma(centro)
        devianti = [j for j in range(len(validi)) if not maggioranza[j] and np.linalg.norm(labs[j] - centro) > 20.0]
        colorate = [j for j in devianti if _croma(labs[j]) - croma_centro >= 15.0]
        if 1 <= len(colorate) <= 2:                                                    # una scelta, o due bottoni accesi insieme; di piu' e' un elenco colorato
            for j in colorate:
                voce = voci[validi[j]]; trovate.setdefault(voce["testo"], voce)
    return list(trovate.values())


def scelto(voci):
    """Una voce scelta (la prima), None se nessuna."""
    trovate = scelte(voci)
    return trovate[0] if trovate else None


def _cursore_fuori(cursori, t, zona):
    vicini = [x for x in cursori if abs(float(x[0])-float(t)) <= 0.25]
    if not vicini: return True
    _, x, y = min(vicini, key=lambda q: abs(float(q[0])-float(t)))[:3]
    x0, y0, x1, y1 = zona
    return not (x0 <= x <= x1 and y0 <= y <= y1)


# ---------------------------------------------------------------- E1b: barre evidenziate in un fotogramma fermo
def _lab_barra(frame, scatola):
    """Lab mediano dei pixel colorati dentro la scatola; None se meno del 30% e' colorato."""
    x0, y0, x1, y1 = scatola
    lab = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2LAB).reshape(-1, 3)
    croma = np.hypot(lab[:, 1].astype(np.float32) - 128.0, lab[:, 2].astype(np.float32) - 128.0)
    colorati = lab[croma >= BARRE_CROMA]
    if len(colorati) < 0.3 * len(lab): return None
    return np.median(colorati.astype(float), axis=0)


def barre_evidenziate(frame):
    """Rettangoli pieni e colorati (la voce scelta di un elenco, il bottone acceso) in un fotogramma:
    [(scatola, Lab mediano)]. Non serve il testo: serve per le zone che non cambiano mai e quindi nessuna lettura le vede."""
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
    croma = np.hypot(lab[:, :, 1].astype(np.float32) - 128.0, lab[:, :, 2].astype(np.float32) - 128.0)
    maschera = (croma >= BARRE_CROMA).astype(np.uint8)
    chiusa = cv2.morphologyEx(maschera, cv2.MORPH_CLOSE, np.ones((5, 15), np.uint8))      # il testo sulla barra non e' colorato: si richiude
    n, _, st, _ = cv2.connectedComponentsWithStats(chiusa)
    larghezza = frame.shape[1]; barre = []
    for k in range(1, n):
        x, y, w, h, area = [int(v) for v in st[k]]
        if w < 40 or h < 10 or h > 40 or w < 2.0 * h or w > 0.4 * larghezza: continue
        if area < 0.85 * w * h: continue                                                   # un rettangolo pieno, non una linea o una macchia
        mediano = _lab_barra(frame, [x, y, x + w, y + h])
        if mediano is not None: barre.append(([x, y, x + w, y + h], mediano))
    return barre[:MAX_BARRE]


def nome_barra(righe, scatola):
    """La scritta dentro la barra (la riga di testo che piu' si sovrappone), None se non ce n'e'."""
    bx0, by0, bx1, by1 = scatola; migliore = None
    for testo, conf, r in righe:
        if conf < CONF_NOME: continue
        ix = max(0, min(bx1, r[2]) - max(bx0, r[0])); iy = max(0, min(by1, r[3]) - max(by0, r[1]))
        area = float(max(1, (r[2]-r[0])*(r[3]-r[1])))
        if ix * iy >= 0.5 * area and (migliore is None or ix * iy > migliore[0]): migliore = (ix * iy, testo)
    if migliore is None: return None
    testo = pulisci_ocr(migliore[1])
    return testo[:80] if e_voce(testo) else None


class Barre:
    """Barre evidenziate nei fotogrammi chiave: le conferma un secondo dopo, le nomina (una sola lettura per barra ferma)
    e segnala solo la prima volta che compaiono."""

    def __init__(self, lettore, larghezza, altezza):
        self.lettore = lettore; self.w = larghezza; self.h = altezza
        self.pendenti = []; self.cache = {}; self.attive = {}; self.eventi = []; self.stat = collections.Counter()

    def fotogramma_chiave(self, n, frame):
        barre = barre_evidenziate(frame)
        self.pendenti.append({"n": n, "barre": barre, "frame": None})
        return len(barre)

    def aggiungi(self, n, frame):
        """Chiamata a ogni fotogramma: chiude i fotogrammi chiave di almeno 1 s fa."""
        for p in [q for q in self.pendenti if n >= q["n"] + 2]:
            self.pendenti.remove(p); self._chiudi(p, frame)

    def fine(self, frame):
        for p in list(self.pendenti): self.pendenti.remove(p); self._chiudi(p, frame)

    def _chiudi(self, p, dopo):
        presenti = {}
        for scatola, lab in p["barre"]:
            x0, y0, x1, y1 = scatola
            adesso = _lab_barra(dopo, scatola)
            if adesso is None or _distanza(adesso, lab) > 12.0: self.stat["barra che non dura"] += 1; continue
            chiave = (x0 // 6, y0 // 6, x1 // 6, y1 // 6); noto = self.cache.get(chiave)
            if noto is not None and _distanza(noto[1], lab) < 8.0: nome = noto[0]
            else:
                ox0, oy0, ox1, oy1 = max(0, x0 - 4), max(0, y0 - 4), min(self.w, x1 + 4), min(self.h, y1 + 4)
                nome = nome_barra(self.lettore.leggi(dopo[oy0:oy1, ox0:ox1], [ox0, oy0]), scatola); self.cache[chiave] = (nome, lab)
            if nome is None: self.stat["barra senza nome"] += 1; continue
            presenti[nome] = scatola
        for nome in [k for k in self.attive if k not in presenti]: del self.attive[nome]       # sparita: se torna e' una scelta nuova
        for nome, scatola in presenti.items():
            if nome in self.attive: continue
            self.attive[nome] = scatola; x0, y0, x1, y1 = scatola; t = round(p["n"] / float(FPS), 2)
            self.eventi.append({"t": t, "testo": nome, "zona_norm": [x0 / float(self.w), y0 / float(self.h), x1 / float(self.w), y1 / float(self.h)]})


# ---------------------------------------------------------------- E2: cambi compatti e persistenti
def _diff(x, y):
    return np.abs(x.astype(np.int16) - y.astype(np.int16)).sum(2) > SOGLIA_PIXEL


def _croma(lab):
    return float(np.hypot(float(lab[1]) - 128.0, float(lab[2]) - 128.0))


def _estremi(img, scatola, maschera):
    """Quota dei pixel cambiati che sono bianchi o neri puri: la freccia del cursore e le icone bianco-nere."""
    x0, y0, x1, y1 = scatola
    hsv = cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2HSV).reshape(-1, 3)[maschera.reshape(-1)]
    return float((((hsv[:, 2] > 225) & (hsv[:, 1] < 40)) | (hsv[:, 2] < 30)).mean()) if len(hsv) else 0.0


def misure_cambio(a, b, c, d, scatola, maschera):
    """Misure di un cambio fra il fotogramma prima (a), subito dopo (b), 1,5 s dopo (c), 4,5 s dopo (d).
    scatola=[x0,y0,x1,y1] in pixel; maschera = pixel cambiati dentro la scatola (bool)."""
    x0, y0, x1, y1 = scatola
    ra, rb, rc, rd = a[y0:y1, x0:x1], b[y0:y1, x0:x1], c[y0:y1, x0:x1], d[y0:y1, x0:x1]
    sel = maschera.reshape(-1)
    lab_a = np.median(cv2.cvtColor(ra, cv2.COLOR_BGR2LAB).reshape(-1, 3)[sel].astype(float), axis=0)
    lab_c = np.median(cv2.cvtColor(rc, cv2.COLOR_BGR2LAB).reshape(-1, 3)[sel].astype(float), axis=0)
    hsv = cv2.cvtColor(rc, cv2.COLOR_BGR2HSV).reshape(-1, 3)[sel]
    return {"persiste": float(_diff(ra, rc)[maschera].mean()),            # il cambio c'e' ancora dopo 1,5 s
            "assestato": float(_diff(rb, rc)[maschera].mean()),           # e non ha continuato a muoversi
            "tiene": float(_diff(ra, rd)[maschera].mean()),               # c'e' ancora dopo 4,5 s (il cursore se ne va, lo stato resta)
            "delta": _distanza(lab_a, lab_c),
            "estremi": float((((hsv[:, 2] > 225) & (hsv[:, 1] < 40)) | (hsv[:, 2] < 30)).mean())}   # bianco/nero puri: la freccia del cursore


class Rivelatore:
    """Riceve i fotogrammi a 2 fps (BGR) e raccoglie i cambi compatti e persistenti.
    In memoria restano solo ORIZZONTE+1 fotogrammi, il contatore dei pixel vivi e i ritagli dei candidati."""

    def __init__(self, larghezza, altezza, soglie=None):
        self.w = int(larghezza); self.h = int(altezza)
        self.buf = collections.deque(maxlen=ORIZZONTE + 1)
        self.i = 0; self.intervalli = 0
        self.vivi = np.zeros((self.h, self.w), np.uint16)
        self.candidati = []; self.scartati = collections.Counter()
        self.k2 = np.ones((2, 2), np.uint8); self.k3 = np.ones((3, 3), np.uint8); self.k9 = np.ones((9, 9), np.uint8)
        self.s = {"persiste": 0.5, "assestato": 0.4, "tiene": 0.35, "delta": 15.0, "estremi": 0.9, "npix": 30, "lato": 9,
                  "riempimento": 0.25, "larga": 0.3, "alta": 0.12, "scena": 0.02, "scena_dopo": 0.03, "vivo": 0.10, "ricorrente": 6}
        if soglie: self.s.update(soglie)

    def aggiungi(self, frame):
        self.buf.append(frame)
        if len(self.buf) == self.buf.maxlen:
            self._intervallo(list(self.buf)); self.i += 1

    def fine(self):
        resto = list(self.buf)[1:] if len(self.buf) == self.buf.maxlen else list(self.buf)
        while len(resto) >= 2:
            self._intervallo(resto); resto = resto[1:]; self.i += 1
        vivo = self.vivi >= self.s["vivo"] * max(1, self.intervalli)
        tenuti = []
        for c in self.candidati:
            x0, y0, x1, y1 = c["scatola"]
            if float(vivo[y0:y1, x0:x1].mean()) >= 0.5: self.scartati["zona sempre viva"] += 1; continue
            tenuti.append(c)
        tenuti = self._unisci(tenuti)
        ricorrenti = [sum(1 for q in tenuti if abs(q["t"] - c["t"]) > 2.0 and _iou(q["scatola"], c["scatola"]) > 0.5) for c in tenuti]
        self.candidati = [c for c, n in zip(tenuti, ricorrenti) if n < self.s["ricorrente"]]     # contatori e animazioni: cambiano sempre nello stesso punto
        self.scartati["contatore o animazione"] += len(tenuti) - len(self.candidati)
        return self.candidati

    def _unisci(self, candidati):
        """Lo stesso cambio visto in due intervalli di fila resta uno solo (il primo)."""
        risultato = []
        for c in sorted(candidati, key=lambda x: x["t"]):
            doppio = False
            for p in reversed(risultato):
                if c["t"] - p["t"] > 1.5: break
                if _iou(c["scatola"], p["scatola"]) > 0.5: doppio = True; break
            if doppio: self.scartati["doppione"] += 1
            else: risultato.append(c)
        return risultato

    def _intervallo(self, F):
        a, b = F[0], F[1]
        c = F[min(3, len(F)-1)]; d = F[min(ORIZZONTE, len(F)-1)]
        aperto = cv2.morphologyEx(_diff(a, b).astype(np.uint8), cv2.MORPH_OPEN, self.k2)   # via i puntini isolati di compressione; i tratti del testo (1-2 px) restano
        self.intervalli += 1
        self.vivi[aperto > 0] += 1
        if not aperto.any(): return
        if float(aperto.mean()) > self.s["scena"]: self.scartati["scena che cambia"] += 1; return   # orbita, anteprima, pagina nuova: non e' un controllo
        n, lab, st, _ = cv2.connectedComponentsWithStats(cv2.dilate(aperto, self.k9))
        t = round((self.i + 1) / float(FPS), 2); dopo_calmo = None
        comp = []
        for k in range(1, n):
            x, y, ww, hh = [int(v) for v in st[k][:4]]
            if ww > self.s["larga"] * self.w or hh > self.s["alta"] * self.h: self.scartati["troppo grande"] += 1; continue
            sub = (lab[y:y+hh, x:x+ww] == k) & (aperto[y:y+hh, x:x+ww] > 0)
            if int(sub.sum()) < self.s["npix"]: self.scartati["troppo piccolo"] += 1; continue
            ys, xs = np.nonzero(sub)
            x0, x1, y0, y1 = x + int(xs.min()), x + int(xs.max()) + 1, y + int(ys.min()), y + int(ys.max()) + 1
            if x1 - x0 < self.s["lato"] or y1 - y0 < self.s["lato"]: self.scartati["troppo piccolo"] += 1; continue
            maschera = sub[y0-y:y1-y, x0-x:x1-x]; scatola = [x0, y0, x1, y1]
            if float(maschera.mean()) < self.s["riempimento"]: self.scartati["tratto sottile (disegno, cursore)"] += 1; continue
            comp.append((scatola, maschera))
        # il cursore che si sposta lascia due macchie piccole bianco-nere nello stesso intervallo (dove era, dove e'): vanno via tutte e due
        piccole = [i for i, (s_, m_) in enumerate(comp) if s_[2] - s_[0] <= 32 and s_[3] - s_[1] <= 32 and max(_estremi(a, s_, m_), _estremi(b, s_, m_)) >= 0.6]
        cursore = set(piccole) if len(piccole) >= 2 else set()
        for i_comp, (scatola, maschera) in enumerate(comp):
            x0, y0, x1, y1 = scatola
            if i_comp in cursore: self.scartati["cursore che si sposta"] += 1; continue
            m = misure_cambio(a, b, c, d, scatola, maschera)
            if m["persiste"] < self.s["persiste"] or m["assestato"] > self.s["assestato"]: self.scartati["non persiste"] += 1; continue
            if m["tiene"] < self.s["tiene"]: self.scartati["non tiene"] += 1; continue
            if m["delta"] < self.s["delta"]: self.scartati["colore uguale"] += 1; continue
            if m["estremi"] >= self.s["estremi"]: self.scartati["cursore o icona bianco-nera"] += 1; continue
            if dopo_calmo is None:
                dopo_calmo = float(cv2.morphologyEx(_diff(b, c).astype(np.uint8), cv2.MORPH_OPEN, self.k3).mean()) <= self.s["scena_dopo"]
            if not dopo_calmo: self.scartati["scena che cambia dopo"] += 1; continue
            l, r, su, giu = MARGINE_OCR
            ox0, oy0 = max(0, x0 - l), max(0, y0 - su); ox1, oy1 = min(self.w, x1 + r), min(self.h, y1 + giu)
            self.candidati.append({"t": t, "scatola": scatola, "misure": m, "prima": a[oy0:oy1, ox0:ox1].copy(), "dopo": c[oy0:oy1, ox0:ox1].copy(), "origine": [ox0, oy0]})


def _iou(p, q):
    ix = max(0, min(p[2], q[2]) - max(p[0], q[0])); iy = max(0, min(p[3], q[3]) - max(p[1], q[1]))
    inter = ix * iy
    unione = (p[2]-p[0])*(p[3]-p[1]) + (q[2]-q[0])*(q[3]-q[1]) - inter
    return inter / float(unione) if unione > 0 else 0.0


# ---------------------------------------------------------------- nome e verso del controllo
def _pallino_in_testa(scatola, r):
    """Il cambio e' un pallino o una casella (non piu' di 40 px, quasi quadrato) all'inizio della riga di testo r."""
    larg = scatola[2] - scatola[0]; alt = scatola[3] - scatola[1]
    if not (larg <= 40 and alt <= 40 and 0.5 <= larg / float(max(1, alt)) <= 2.0): return False
    return (scatola[0] + scatola[2]) / 2.0 <= r[0] + 1.5 * max(1, r[3] - r[1])


def scegli_etichetta(scatola, righe):
    """righe = [(testo, confidenza, [x0,y0,x1,y1])] in pixel dello schermo.
    Ritorna (testo, riquadro, relazione) con relazione 'copre' | 'accanto' | 'dentro'; None se nessun nome.
      copre   = il cambio ricopre il testo (fondo del bottone, testo che sbiadisce)
      accanto = il cambio e' un pallino/casella a fianco del testo (scelta, spunta)
      dentro  = il cambio sta dentro un testo piu' grande: un numero che cambia, non uno stato (scartato dal chiamante)."""
    bx0, by0, bx1, by1 = scatola
    area_b = float(max(1, (bx1-bx0)*(by1-by0)))
    migliore = None
    for testo, conf, r in righe:
        if conf < CONF_NOME or len(testo.strip()) < 2: continue
        ix = max(0, min(bx1, r[2]) - max(bx0, r[0])); iy = max(0, min(by1, r[3]) - max(by0, r[1]))
        inter = float(ix*iy); area_r = float(max(1, (r[2]-r[0])*(r[3]-r[1])))
        if inter >= 0.4 * area_r:
            punto = (inter, "copre")
        elif inter >= 0.8 * area_b and area_b < 0.4 * area_r:
            punto = (inter, "dentro")
        else:
            continue
        if migliore is None or punto[0] > migliore[0]: migliore = (punto[0], testo, r, punto[1])
    if migliore and migliore[3] == "dentro" and _pallino_in_testa(scatola, migliore[2]):     # il pallino letto come "O" insieme alla scritta: e' una scelta, non un numero
        parti = migliore[1].strip().split(None, 1)
        if len(parti) == 2 and len(parti[0]) <= 2 and (len(parti[0]) == 1 or not parti[0].isalpha()): return parti[1], migliore[2], "accanto"
    if migliore: return migliore[1].strip(), migliore[2], migliore[3]
    larg, alt = bx1 - bx0, by1 - by0
    if not (larg <= 40 and alt <= 40 and 0.5 <= larg / float(max(1, alt)) <= 2.0): return None   # solo pallini e caselle
    cy = (by0 + by1) / 2.0
    scelti = []
    for testo, conf, r in righe:
        if conf < CONF_NOME or len(testo.strip()) < 2: continue
        altezza = float(max(1, r[3]-r[1])); sovrapposto = min(by1, r[3]) - max(by0, r[1])
        if sovrapposto < 0.5 * min(altezza, alt) and not (r[1] <= cy <= r[3]): continue
        destra = r[0] - bx1; sinistra = bx0 - r[2]
        if 0 <= destra <= 2.5 * altezza: scelti.append((destra, testo, r))
        elif 0 <= sinistra <= 1.5 * altezza: scelti.append((sinistra + altezza, testo, r))
    if not scelti: return None
    _, testo, r = min(scelti, key=lambda q: q[0])
    return testo.strip(), r, "accanto"


def _fondo_e_segno(img, riquadro):
    """Lab mediano del fondo (anello di 3 px attorno al riquadro) e del segno (i pixel piu' lontani dal fondo)."""
    x0, y0, x1, y1 = [int(round(v)) for v in riquadro]
    fondo = colore_anello(img, [x0, y0, x1, y1], 3)
    zona = img[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
    if fondo is None or zona.size == 0: return None, None
    lab = cv2.cvtColor(zona, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(float)
    d = np.linalg.norm(lab - fondo, axis=1)
    lontani = lab[d >= max(20.0, float(np.percentile(d, 80)))]
    return fondo, (np.median(lontani, axis=0) if len(lontani) >= 4 else fondo)


def misura_stato(prima, dopo, riquadro):
    """Confronta fondo e segno del controllo prima/dopo. Ritorna {'delta','verso'} o None se il controllo non cambia.
    Si guarda il controllo (il riquadro del testo o del pallino), non l'area che e' cambiata: un cerchio disegnato
    attorno o un cursore nella stessa area non contano."""
    fa, sa = _fondo_e_segno(prima, riquadro); fb, sb = _fondo_e_segno(dopo, riquadro)
    if fa is None or fb is None: return None
    d_fondo = _distanza(fa, fb); d_segno = _distanza(sa, sb); delta = max(d_fondo, d_segno)
    if delta < 15.0: return None
    ca, cb = (_croma(fa), _croma(fb)) if d_fondo >= d_segno else (_croma(sa), _croma(sb))
    if cb - ca >= 8.0: verso = "acceso"
    elif ca - cb >= 8.0: verso = "spento"
    else: verso = "acceso" if abs(sb[0] - fb[0]) >= abs(sa[0] - fa[0]) else "spento"     # piu' contrastato = acceso
    return {"delta": delta, "verso": verso}


def e_voce(testo):
    """Una voce dell'interfaccia ha lettere; un contatore o un valore e' fatto soprattutto di cifre."""
    lettere = sum(1 for ch in testo if ch.isalpha()); cifre = sum(1 for ch in testo if ch.isdigit())
    if lettere < 3 or cifre > 0.3 * (lettere + cifre): return False
    t = testo.strip()
    return not (re.search(r"\d[\d.,]*\s*\S{0,2}$", t) or re.match(r"\d", t))     # un numero in coda (con o senza unita') o in testa e' il valore di un campo, non uno stato


class Lettore:
    """OCR rapidocr dei ritagli (solo librerie del venv); il motore si carica alla prima lettura."""

    def __init__(self):
        self.motore = None; self.chiamate = 0

    def leggi(self, immagine, origine):
        if self.motore is None:
            from rapidocr_onnxruntime import RapidOCR
            # lato minimo 128: i ritagli sono bassi (35-100 px) e il valore di serie (736) li ingrandisce di 10-20 volte, 2 s a lettura per niente
            self.motore = RapidOCR(intra_op_num_threads=1, inter_op_num_threads=1, det_limit_side_len=128, det_limit_type="min")
        self.chiamate += 1
        risultato, _ = self.motore(immagine, use_cls=False)
        righe = []
        for q in (risultato or []):
            xs = [p[0] for p in q[0]]; ys = [p[1] for p in q[0]]
            righe.append((str(q[1]), float(q[2]), [int(min(xs)) + origine[0], int(min(ys)) + origine[1], int(max(xs)) + origine[0], int(max(ys)) + origine[1]]))
        return righe


def _stessa_voce_prima(righe_prima, riquadro, testo):
    """Un controllo che cambia stato aveva gia' la stessa scritta nello stesso posto: una comparsa o un testo diverso no."""
    for t, conf, r in righe_prima:
        ix = max(0, min(riquadro[2], r[2]) - max(riquadro[0], r[0])); iy = max(0, min(riquadro[3], r[3]) - max(riquadro[1], r[1]))
        area = float(max(1, min((riquadro[2]-riquadro[0])*(riquadro[3]-riquadro[1]), (r[2]-r[0])*(r[3]-r[1]))))
        if ix * iy >= 0.4 * area and dig(t) == dig(testo):
            a, b = let(pulisci_ocr(t)), let(testo)
            if ratio(pulisci_ocr(t), testo) >= 0.7 or (len(a) >= 6 and a in b) or (len(b) >= 6 and b in a): return True      # la lettura puo' perdere le prime lettere
    return False


def _migliore_coperta(c, righe):
    """Fra le scritte ricoperte dal cambio, quella il cui controllo cambia di piu': (testo, riquadro, stato) o None."""
    bx0, by0, bx1, by1 = c["scatola"]; ox, oy = c["origine"]; migliore = None
    for testo, conf, r in righe:
        if conf < CONF_NOME: continue
        ix = max(0, min(bx1, r[2]) - max(bx0, r[0])); iy = max(0, min(by1, r[3]) - max(by0, r[1]))
        if ix * iy < 0.4 * max(1, (r[2]-r[0])*(r[3]-r[1])): continue
        nome = pulisci_ocr(testo)
        if not e_voce(nome): continue
        stato = misura_stato(c["prima"], c["dopo"], [r[0] - ox, r[1] - oy, r[2] - ox, r[3] - oy])
        if stato is not None and (migliore is None or stato["delta"] > migliore[2]["delta"]): migliore = (nome, r, stato)
    return migliore


def nomina(candidati, lettore, larghezza, altezza, stat, diario=None):
    """Da ogni candidato a un cambio con nome e verso; scarta chi non ha un nome, un numero che cambia, un controllo che non cambia.
    `diario`, se dato, raccoglie l'esito di ogni candidato (per capire perche' un fatto manca)."""
    cambi = []
    def esito(c, motivo, testo=""):
        if diario is not None: diario.append({"t": c["t"], "scatola": c["scatola"], "delta": round(c["misure"]["delta"], 1), "estremi": round(c["misure"].get("estremi", 0.0), 2), "esito": motivo, "testo": testo})
    ordinati = sorted(candidati, key=lambda c: -c["misure"]["delta"])
    if len(ordinati) > MAX_OCR: stat["oltre il tetto OCR"] += len(ordinati) - MAX_OCR
    for c in ordinati[:MAX_OCR]:
        righe = lettore.leggi(c["dopo"], c["origine"])
        scelta = scegli_etichetta(c["scatola"], righe)
        if scelta is None: stat["senza nome"] += 1; esito(c, "senza nome", " | ".join(r[0] for r in righe)[:60]); continue
        testo, riquadro, relazione = scelta
        if relazione == "dentro" and _pallino_in_testa(c["scatola"], riquadro):      # pallino letto dentro la riga: la scritta si rilegge solo a destra del pallino
            ox, oy = c["origine"]; bx1 = c["scatola"][2] + 2; cy = (c["scatola"][1] + c["scatola"][3]) / 2.0
            vicine = [r for r in lettore.leggi(c["dopo"][:, max(0, bx1 - ox):], [bx1, oy]) if r[1] >= CONF_NOME and r[2][1] <= cy <= r[2][3]]
            if vicine: testo, _, riquadro = max(vicine, key=lambda q: q[2][2] - q[2][0]); relazione = "accanto"
        if relazione == "dentro": stat["valore che cambia"] += 1; esito(c, "valore che cambia", testo); continue
        testo = pulisci_ocr(testo)
        if not e_voce(testo): stat["non e una voce"] += 1; esito(c, "non e una voce", testo); continue
        ox, oy = c["origine"]
        locale = [riquadro[0] - ox, riquadro[1] - oy, riquadro[2] - ox, riquadro[3] - oy] if relazione == "copre" else [c["scatola"][0] - ox, c["scatola"][1] - oy, c["scatola"][2] - ox, c["scatola"][3] - oy]
        stato = misura_stato(c["prima"], c["dopo"], locale)
        if relazione == "copre":                           # il cambio ricopre piu' scritte (un cerchio disegnato attorno): e' quella che cambia di piu'
            migliore = _migliore_coperta(c, righe)
            if migliore is not None and (stato is None or migliore[2]["delta"] > stato["delta"]): testo, riquadro, stato = migliore
        if stato is None: stat["il controllo non cambia"] += 1; esito(c, "il controllo non cambia", testo); continue
        if not _stessa_voce_prima(lettore.leggi(c["prima"], c["origine"]), riquadro, testo): stat["prima non c'era (comparsa, scritta cambiata)"] += 1; esito(c, "prima non c'era", testo); continue
        esito(c, "ok " + stato["verso"], testo)
        x0, y0, x1, y1 = riquadro if relazione == "copre" else c["scatola"]
        zona = [x0 / float(larghezza), y0 / float(altezza), x1 / float(larghezza), y1 / float(altezza)]
        cambi.append({"t": c["t"], "testo": testo[:80], "zona_norm": [min(1.0, max(0.0, v)) for v in zona], "delta": stato["delta"], "stato": stato["verso"], "rif": [], "relazione": relazione})
    return cambi


# ---------------------------------------------------------------- funzione pura
def _allineate(z1, z2):
    h1 = max(1e-6, z1[3] - z1[1]); h2 = max(1e-6, z2[3] - z2[1])
    if not 0.5 <= h1 / h2 <= 2.0: return False
    stessa_riga = abs((z1[1]+z1[3])/2.0 - (z2[1]+z2[3])/2.0) <= 0.8 * max(h1, h2)
    stessa_colonna = abs(z1[0] - z2[0]) <= max(0.006, 0.75 * max(h1, h2))
    return stessa_riga or stessa_colonna


def _evento(t, durata, tipo, zona, testo, forza, rif, dati, stampa="riga"):
    zona = [round(float(x), 4) for x in zona]
    return {"t": round(float(t), 2), "durata": round(float(durata), 2), "tipo": tipo, "zona": zona,
            "punto": [round((zona[0]+zona[2])/2.0, 4), round((zona[1]+zona[3])/2.0, 4)], "testo": testo[:80],
            "forza": round(float(forza), 4), "stampa": stampa, "rif": list(rif), "dati": dati}


def _limita(eventi_ordinati, massimo=MAX_PER_MINUTO, finestra=60.0):
    """Al massimo `massimo` eventi in qualunque finestra di `finestra` secondi; restano i piu' forti."""
    tenuti = []
    for e in sorted(eventi_ordinati, key=lambda x: (-x["forza"], x["t"])):
        tempi = sorted([x["t"] for x in tenuti] + [e["t"]])
        if any(tempi[j+massimo] - tempi[j] < finestra for j in range(0, len(tempi) - massimo)): continue
        tenuti.append(e)
    return sorted(tenuti, key=lambda x: (x["t"], x["tipo"], x["testo"]))


def parlato(testo, parole, t, raggio=20.0):
    """Quanta parte delle parole della voce (minuscole, almeno 3 lettere) si sente a voce entro `raggio` secondi da t (0..1)."""
    tokens = [w for w in (''.join(ch for ch in p.lower() if ch.isalpha()) for p in testo.split()) if len(w) >= 3]
    if not tokens: return 0.0
    dette = [''.join(ch for ch in str(w[2]).lower() if ch.isalpha()) for w in parole if len(w) >= 3 and w[0] - raggio <= t <= w[1] + raggio]
    trovati = sum(1 for tok in tokens if any(tok == d or (len(tok) >= 5 and len(d) >= 5 and ratio(tok, d) >= 0.85) for d in dette))
    return trovati / float(len(tokens))


def vicino_a_parola(t, parole, raggio=1.4):
    """True se una parola della voce cade entro `raggio` secondi da t: l'evento trova una frase a cui agganciarsi."""
    return any(w[0] - raggio <= t <= w[1] + raggio for w in parole if len(w) >= 2)


def _punteggio(cambio, parole):
    return 0.4 * min(1.0, cambio["delta"] / 60.0) + 0.6 * parlato(cambio["testo"], parole, cambio["t"])


def _stessa_voce(p, q):
    a, b = p["testo"].lower(), q["testo"].lower()
    sovrapposte = _iou(p["zona_norm"], q["zona_norm"]) > 0.2
    return a == b or a in b or b in a or ratio(a, b) >= 0.8 or sovrapposte


def _fondi_vicini(cambi, parole):
    """Un bottone che cambia da' piu' frammenti (il bordo, la scritta): resta quello col punteggio migliore."""
    tenuti = []
    for c in sorted(cambi, key=lambda x: (-_punteggio(x, parole), x["t"])):
        if any(abs(c["t"] - u["t"]) <= 1.5 and _stessa_voce(c, u) for u in tenuti): continue
        tenuti.append(c)
    return sorted(tenuti, key=lambda x: x["t"])


def calcola(dati):
    """Funzione pura: osservazioni e cambi entrano, eventi schema unico escono."""
    risultato, attive = [], {}
    cursori = dati.get("cursori", []); parole = dati.get("parole", [])
    for lettura in sorted(dati.get("letture", []), key=lambda x: x["t"]):
        voci = lettura["voci"]
        dopo = scelte([dict(v, lab=v.get("lab2", v.get("lab"))) for v in voci])                 # la stessa voce e' ancora scelta 1 s dopo?
        confermate = set(v["testo"] for v in dopo)
        ora = {}
        for voce in scelte(voci):
            nome = pulisci_ocr(voce["testo"])
            if voce["testo"] in confermate and voce.get("forza", 1.0) >= 0.85 and e_voce(nome) and _cursore_fuori(cursori, lettura["t"], voce["zona_norm"]): ora[nome] = voce       # col cursore sopra e' solo il passaggio del mouse
        attive = {k: v for k, v in attive.items() if k in ora}
        for chiave, voce in ora.items():
            if chiave in attive: continue                                                       # gia' segnalata nella lettura precedente
            attive[chiave] = voce; zona = voce["zona_norm"]
            if parlato(chiave, parole, lettura["t"]) < 0.5: continue                            # una voce evidenziata e' notizia solo se se ne parla a voce
            risultato.append(_evento(lettura["t"], 1.0, "scelto", zona, chiave, 0.5 + 0.49 * min(1.0, _punteggio({"delta": 40.0, "testo": chiave, "t": lettura["t"]}, parole)), [], {"dove": eventi.dove(zona)}))
    for barra in dati.get("barre", []):                    # voce evidenziata in una zona che non cambia: vale solo se se ne parla a voce
        if not e_voce(barra["testo"]) or parlato(barra["testo"], parole, barra["t"]) < 0.5: continue
        zona = barra["zona_norm"]; risultato.append(_evento(barra["t"], 0.0, "scelto", zona, barra["testo"], 0.5 + 0.49 * min(1.0, _punteggio({"delta": 40.0, "testo": barra["testo"], "t": barra["t"]}, parole)), [], {"dove": eventi.dove(zona)}))
    cambi, ultimo = [], {}
    for cambio in sorted(dati.get("cambi", []), key=lambda x: x["t"]):
        chiave = (cambio["testo"], cambio["stato"])
        if chiave in ultimo and cambio["t"]-ultimo[chiave] <= 1.0: continue
        ultimo[chiave] = cambio["t"]; cambi.append(dict(cambio))
    cambi = _fondi_vicini(cambi, parole)
    persi = set()
    for i, nuovo in enumerate(cambi):                      # una si accende e un'altra, allineata, si spegne: e' una scelta
        if nuovo["stato"] != "acceso": continue
        for j, vecchio in enumerate(cambi):
            if vecchio["stato"] == "spento" and j not in persi and vecchio["testo"] != nuovo["testo"] and abs(vecchio["t"]-nuovo["t"]) <= 1.5 and _allineate(vecchio["zona_norm"], nuovo["zona_norm"]):
                nuovo["scelta"] = True; persi.add(j); break
    for i, cambio in enumerate(cambi):
        if i in persi: continue
        zona = cambio["zona_norm"]; forza = 0.5 + 0.49 * min(1.0, _punteggio(cambio, parole))
        if cambio.get("scelta"): risultato.append(_evento(cambio["t"], 0.0, "scelto", zona, cambio["testo"], forza, cambio.get("rif", []), {"dove": eventi.dove(zona)}))
        else: risultato.append(_evento(cambio["t"], 0.0, "cambio", zona, cambio["testo"], forza, cambio.get("rif", []), {"stato": cambio["stato"]}))
    unici = []                                             # la stessa voce scelta letta ferma e vista cambiare: resta il cambio (l'istante giusto)
    for e in sorted(risultato, key=lambda x: (0 if x["durata"] == 0.0 else 1, x["t"])):
        if any(u["tipo"] == "scelto" and e["tipo"] == "scelto" and u["testo"] == e["testo"] and abs(u["t"]-e["t"]) <= 3.0 for u in unici): continue
        unici.append(e)
    if SOLO_CON_VOCE: unici = [e for e in unici if vicino_a_parola(e["t"], parole)]
    finali = _limita(unici)
    for n, evento in enumerate(finali, 1): evento["id"] = "E%d" % n; evento["canale"] = "stato"
    return finali


# ---------------------------------------------------------------- lettura del video
def fotogrammi(video, larghezza, altezza):
    """Un fotogramma ogni 0,5 s, BGR, alla scala di ocr.py."""
    import comune
    comando = [comune.ffmpeg(), "-v", "error", "-skip_frame", "noref", "-i", video, "-vf", "fps=%d,scale=%d:%d" % (FPS, larghezza, altezza), "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    processo = subprocess.Popen(comando, stdout=subprocess.PIPE)
    n = 0
    try:
        while True:
            dati = processo.stdout.read(larghezza * altezza * 3)
            if len(dati) < larghezza * altezza * 3: break
            yield n, np.frombuffer(dati, np.uint8).reshape(altezza, larghezza, 3)
            n += 1
    finally:
        processo.stdout.close(); processo.kill(); processo.wait()


def _clic_cursore(lavoro):
    doc = _json(os.path.join(lavoro, "eventi_cursore.json"), {})
    return [(float(e["t"]), e.get("punto"), str(e.get("id"))) for e in doc.get("eventi", []) if e.get("tipo") == "clic" and e.get("id")]


def _cursori(lavoro):
    denso = _json(os.path.join(lavoro, "cursore.json"), [])
    traccia = eventi.cursore_da_mov(denso)
    if traccia: return traccia
    mov = _json(os.path.join(lavoro, "mov.json"), {})
    w = float(mov.get("W") or 1.0); h = float(mov.get("H") or 1.0)
    convertiti = [[v[0], v[1]/w, v[2]/h, "", 1.0] for v in mov.get("cur", []) if isinstance(v, list) and len(v) >= 3]
    return eventi.cursore_da_mov(convertiti)


def leggi(video, lavoro, lettore=None):
    """Un solo passaggio sul video a 2 fps: colori delle voci di ocr3 per E1, cambi compatti per E2."""
    import comune
    ocr = _json(os.path.join(lavoro, "ocr3.json"), {})
    larghezza_video, altezza_video, _ = comune.info_video(video)
    w = LARGHEZZA; h = int(round(altezza_video * w / float(larghezza_video) / 2.0) * 2)
    ow = float(ocr.get("W") or w); oh = float(ocr.get("H") or h)
    grezze = {}
    for g in ocr.get("letture", []):
        if isinstance(g, list) and len(g) == 4: grezze.setdefault(int(round(float(g[0]) * FPS)), []).append(g)
    letture = []; rileggere = {}; rivelatore = Rivelatore(w, h); stat = collections.Counter()
    lettore = lettore or Lettore(); barre = Barre(lettore, w, h); ultimo = None; t_lettura = time.perf_counter()
    for n, frame in fotogrammi(video, w, h):
        rivelatore.aggiungi(frame); barre.aggiungi(n, frame); ultimo = frame
        if n == 2 or (n > 0 and n % PASSO_CHIAVE == 0): barre.fotogramma_chiave(n, frame)
        for lettura in rileggere.pop(n, []):               # un secondo dopo la lettura: il fondo delle stesse voci e' ancora quello?
            for voce in lettura["voci"]: voce["lab2"] = colore_anello(frame, voce["zona"])
        for t, xbase, ybase, valori in grezze.get(n, []):
            voci = []
            for voce in valori:
                if not isinstance(voce, list) or len(voce) < 6: continue
                testo, forza, ax, ay, bx, by = voce[:6]; zona = [xbase+ax, ybase+ay, xbase+bx, ybase+by]
                voci.append({"testo": str(testo)[:80], "forza": float(forza), "zona": zona, "zona_norm": [zona[0]/ow, zona[1]/oh, zona[2]/ow, zona[3]/oh], "lab": colore_anello(frame, zona)})
            lettura = {"t": float(t), "voci": voci}; letture.append(lettura); rileggere.setdefault(n + 2, []).append(lettura)
    if ultimo is not None: barre.fine(ultimo)
    t_ocr = time.perf_counter(); stat["secondi_video"] = round(t_ocr - t_lettura, 1)
    candidati = rivelatore.fine()
    stat.update(rivelatore.scartati); stat.update(barre.stat)
    diario = [] if comune.ambiente("STATO_DIARIO") else None
    cambi = nomina(candidati, lettore, w, h, stat, diario)
    parole = _json(os.path.join(lavoro, "parole.json"), [])
    clic = _clic_cursore(lavoro)
    for cambio in cambi:                                   # un clic di D vicino al cambio lo conferma
        z = cambio["zona_norm"]; m = 0.03
        cambio["rif"] = [i for t, p, i in clic if -1.0 <= cambio["t"] - t <= 1.0 and p and z[0]-m <= p[0] <= z[2]+m and z[1]-m <= p[1] <= z[3]+m][:2]
    stat["secondi_ocr_cambi"] = round(time.perf_counter() - t_ocr, 1)
    stat["intervalli"] = rivelatore.intervalli; stat["candidati"] = len(candidati); stat["ocr_chiamate"] = lettore.chiamate
    return {"letture": letture, "cambi": cambi, "barre": barre.eventi, "cursori": _cursori(lavoro), "parole": parole, "stat": dict(stat), "diario": diario}


def documento(eventi_calcolati, secondi, letture, extra=None):
    stat = {"secondi": round(float(secondi), 3), "letture": int(letture), "eventi": len(eventi_calcolati)}
    if extra: stat.update(extra)
    return {"schema": 1, "canale": "stato", "lettera": "E", "versione": VERSIONE, "sezione": "STATO", "legenda": LEGENDA,
            "formati": dict(FORMATI), "stat": stat, "eventi": eventi_calcolati}


def main(argv):
    if len(argv) != 3: raise SystemExit("uso: canale_stato.py <video.mp4> <cartella _lavoro>")
    video = os.path.abspath(argv[1]); lavoro = os.path.abspath(argv[2]); inizio = time.perf_counter()
    dati = leggi(video, lavoro); calcolati = calcola(dati); secondi = time.perf_counter() - inizio
    extra = dict(dati.get("stat", {})); extra["ram_picco_mb"] = ram_picco_mb()
    eventi.scrivi(os.path.join(lavoro, "eventi_stato.json"), documento(calcolati, secondi, len(dati["letture"]), extra))
    if dati.get("diario") is not None:                    # con DEMOBRIEF_STATO_DIARIO o il compatibile AD_MOVIOLA_STATO_DIARIO
        with open(os.path.join(lavoro, "stato_diario.json"), "w", encoding="utf-8") as f:
            json.dump({"candidati": dati["diario"], "barre": dati.get("barre", []), "cambi": dati.get("cambi", [])}, f, ensure_ascii=False)
    print("canale_stato: eventi=%d letture=%d tempo=%.1fs ram_picco=%s MB scartati=%s" % (len(calcolati), len(dati["letture"]), secondi, extra["ram_picco_mb"], json.dumps(dati.get("stat", {}), ensure_ascii=False)))


if __name__ == "__main__":
    main(sys.argv)
