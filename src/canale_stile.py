# -*- coding: utf-8 -*-
"""Canale C: colore, contrasto e rilievo dei testi letti dall'OCR.

Come lavora (tutto deterministico, nessuna parola ne' soglia ricavata dagli esempi):
  1. dall'OCR (ocr3, ocr1) si scelgono i testi VALIDI: lettere abbastanza, parola detta nel
     video oppure letta identica in almeno 2 istanti diversi (niente testi spezzati o storpiati);
  2. si decodificano SOLO gli istanti in cui c'e' almeno un testo valido (decodifica in avanti
     quando costa meno di un salto) e si misura il colore dell'inchiostro e del fondo;
  3. i testi uguali e nello stesso posto formano una TRACCIA; un cambio colore e' vero solo se
     il nuovo colore resta per almeno 2 letture consecutive e la differenza e' netta;
  4. i cambi simultanei di testi della stessa colonna/riga diventano UNA riga;
  5. tetto di righe stampate al minuto, con priorita'.
"""
import ctypes
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cv2
import numpy as np
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
STAMPA_TITOLI = "nessuna"   # i titoli grandi sono quasi sempre loghi e frammenti: si segnano ma non si stampano ("sezione" per stamparli)
PRIORITA = {"avviso": 0, "disattivato": 1, "cambio_gruppo": 2, "etichetta": 3, "cambio_colore": 4, "titolo": 5}

COLORI_CALDI = frozenset(("rosso", "arancio", "giallo"))
FEMMINILE = {"nero": "nera", "grigio": "grigia", "bianco": "bianca", "rosso": "rossa", "giallo": "gialla", "azzurro": "azzurra"}
MAX_RIGHE_MINUTO = 4
MAX_PER_TIPO_MINUTO = {"cambio_colore": 2, "etichetta": 2, "titolo": 1}   # i tipi di poco valore non riempiono il minuto
SOGLIA_CONTRASTO_BASSO = 0.18   # testo quasi invisibile sul fondo (contrasto di luminosita' 0..1)
SOGLIA_SATURO = 90.0            # saturazione HSV (0..255) di un colore "pieno"
SAT_CROMATICO = 45.0            # sotto questa saturazione (come "grigio" in nome_colore) o con V<60 la tinta non e' affidabile
DELTA_TINTA = 15.0              # distanza minima di tinta (scala OpenCV 0..180) fra due colori saturi
DELTA_LUCE = 60.0               # distanza minima di luminosita' (0..255) per dire che due colori sono "molto diversi"
DISTANZA_VISIBILE = 40.0        # distanza di colore (BGR) minima fra inchiostro e fondo per dire che il testo si vede
LETTURE_STABILI = 2             # letture consecutive in cui il nuovo colore deve restare
SALTO_FOTOGRAMMI = 75           # sotto questa distanza si decodifica in avanti, sopra si salta
SOSTA_FERMO = 45.0              # secondi: un colore fermo nello stesso punto piu' di cosi' e' l'aspetto dell'interfaccia
ALTEZZA_RIGA = 2.0              # una zona ocr1 alta piu' di cosi' volte il testo ocr3 non e' una riga


def _json(percorso, predefinito):
    try:
        with open(percorso, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, TypeError):
        return predefinito


def ram_picco_mb():
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

        class Contatori(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]
        valori = Contatori(); valori.cb = ctypes.sizeof(valori)
        funzione = ctypes.windll.kernel32.K32GetProcessMemoryInfo
        funzione.argtypes = [wintypes.HANDLE, ctypes.POINTER(Contatori), wintypes.DWORD]; funzione.restype = wintypes.BOOL
        corrente = ctypes.windll.kernel32.GetCurrentProcess; corrente.restype = wintypes.HANDLE
        if not funzione(corrente(), ctypes.byref(valori), valori.cb):
            return None
        return round(valori.PeakWorkingSetSize / 1048576.0, 1)
    except Exception:
        return None


# ---------------------------------------------------------------- colore

def nome_colore(hsv):
    h, s, v = [float(x) for x in hsv]
    if v < 55: return "nero"
    if s < 35 and v > 205: return "bianco"
    if s < 45: return "grigio"
    if h < 8 or h >= 170: return "rosso"
    if h < 20: return "arancio"
    if h < 35: return "giallo"
    if h < 85: return "verde"
    if h < 105: return "azzurro"
    if h < 140: return "blu"
    return "viola"


def _hsv(bgr):
    return [float(x) for x in cv2.cvtColor(np.uint8([[np.clip(bgr, 0, 255)]]), cv2.COLOR_BGR2HSV)[0, 0]]


def _luce(bgr):
    return float(cv2.cvtColor(np.uint8([[np.clip(bgr, 0, 255)]]), cv2.COLOR_BGR2GRAY)[0, 0])


def _cromatico(hsv):
    return hsv[1] >= SAT_CROMATICO and hsv[2] >= 60.0


def _distanza_tinta(a, b):
    d = abs(float(a) - float(b)) % 180.0
    return min(d, 180.0 - d)


def differenza_netta(a, b):
    """True se due colori (H,S,V) sono davvero diversi: non basta l'antialiasing o la compressione."""
    if abs(a[2] - b[2]) >= DELTA_LUCE:
        return True
    ca, cb = _cromatico(a), _cromatico(b)
    if ca and cb:
        return _distanza_tinta(a[0], b[0]) >= DELTA_TINTA
    if ca != cb:
        piena = a if ca else b
        return piena[1] >= SOGLIA_SATURO and piena[2] >= 80.0
    return False


def _anello_esterno(frame, x0, y0, x1, y1, predefinito):
    """Colore mediano della corona fra 8 e 16 px dal riquadro: dice se il fondo e' una targhetta o una barra."""
    altezza, larghezza = frame.shape[:2]
    ex0, ey0, ex1, ey1 = max(0, x0 - 16), max(0, y0 - 16), min(larghezza, x1 + 16), min(altezza, y1 + 16)
    zona = frame[ey0:ey1, ex0:ex1]
    corona = np.ones(zona.shape[:2], dtype=bool)
    corona[max(0, y0 - 8 - ey0):y1 + 8 - ey0, max(0, x0 - 8 - ex0):x1 + 8 - ex0] = False
    return np.median(zona[corona], axis=0) if corona.any() else predefinito


def misura_stile(frame, zona):
    """Colore del testo (nucleo dell'inchiostro) e del fondo (anello di 3 px) di un riquadro.

    L'inchiostro e' cio' che si distingue dal fondo (distanza di colore dall'anello, soglia di Otsu):
    cosi' non si scambiano testo e fondo quando il riquadro e' stretto o largo."""
    altezza, larghezza = frame.shape[:2]
    x0, y0, x1, y1 = [int(round(float(x))) for x in zona]
    x0 = max(0, min(larghezza - 1, x0)); y0 = max(0, min(altezza - 1, y0))
    x1 = max(x0 + 1, min(larghezza, x1)); y1 = max(y0 + 1, min(altezza, y1))
    ritaglio = frame[y0:y1, x0:x1]
    ax0, ay0 = max(0, x0 - 3), max(0, y0 - 3); ax1, ay1 = min(larghezza, x1 + 3), min(altezza, y1 + 3)
    esteso = frame[ay0:ay1, ax0:ax1]
    anello = np.ones(esteso.shape[:2], dtype=bool); anello[y0 - ay0:y1 - ay0, x0 - ax0:x1 - ax0] = False
    pixel_fondo = esteso[anello]
    if pixel_fondo.size == 0:   # riquadro grande come il fotogramma: il fondo e' il bordo del ritaglio
        pixel_fondo = np.concatenate([ritaglio[0], ritaglio[-1], ritaglio[:, 0], ritaglio[:, -1]])
    bgr_fondo = np.median(pixel_fondo, axis=0)
    bgr_esterno = _anello_esterno(frame, x0, y0, x1, y1, bgr_fondo)
    distanza = np.linalg.norm(ritaglio.astype(np.float32) - bgr_fondo.astype(np.float32), axis=2)
    soglia, _ = cv2.threshold(np.clip(distanza, 0, 255).astype(np.uint8), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    inchiostro = distanza > max(float(soglia), DISTANZA_VISIBILE * 0.6)
    visibile = float(distanza.max()) >= DISTANZA_VISIBILE and int(inchiostro.sum()) >= max(1, int(0.005 * inchiostro.size))
    pixel_testo = ritaglio[inchiostro].astype(np.float32)
    if len(pixel_testo) >= 8:
        # nucleo: i pixel d'inchiostro piu' lontani dal fondo (i bordi antialias sfumano verso il fondo)
        lontani = distanza[inchiostro]
        pixel_testo = pixel_testo[lontani >= np.percentile(lontani, 50)]
    bgr_testo = np.median(pixel_testo, axis=0) if len(pixel_testo) else bgr_fondo
    hsv_testo = _hsv(bgr_testo); hsv_fondo = _hsv(bgr_fondo); hsv_esterno = _hsv(bgr_esterno)
    lum_testo = _luce(bgr_testo); lum_fondo = _luce(bgr_fondo)
    contrasto = abs(lum_testo - lum_fondo) / 255.0
    rilievo = min(1.0, 0.65 * contrasto + 0.35 * min(1.0, float(inchiostro.mean()) * 8.0))
    return {"colore": nome_colore(hsv_testo), "colore_fondo": nome_colore(hsv_fondo),
            "hsv": [round(x, 1) for x in hsv_testo], "hsv_fondo": [round(x, 1) for x in hsv_fondo], "hsv_esterno": [round(x, 1) for x in hsv_esterno],
            "saturazione": round(hsv_testo[1], 2), "saturazione_fondo": round(hsv_fondo[1], 2),
            "contrasto": round(contrasto, 4), "rilievo": round(rilievo, 4), "visibile": bool(visibile),
            "disattivato": contrasto < SOGLIA_CONTRASTO_BASSO, "altezza": y1 - y0}


# ---------------------------------------------------------------- testo valido

def _gettoni(testo):
    return re.findall(r"[A-Za-z]+", testo)


def vocabolario(parole):
    """Parole dette nel video (da parole.json): solo lettere minuscole, almeno 3."""
    voci = set()
    for parola in parole:
        if isinstance(parola, list) and len(parola) >= 3:
            for g in _gettoni(str(parola[2])):
                if len(g) >= 3: voci.add(g.lower())
    return voci


def in_vocabolario(testo, voci):
    """Almeno una parola del testo (4+ lettere) e' detta nel video, anche con una lettera di scarto."""
    for g in _gettoni(testo):
        g = g.lower()
        if len(g) < 4: continue
        if g in voci: return True
        if len(g) >= 8 and any(len(v) >= 5 and v in g for v in voci): return True   # parole attaccate dall'OCR ("Gardengnome")
        margine = 1 if len(g) < 8 else 2
        if len(g) >= 5 and any(abs(len(g) - len(v)) <= margine and lev(g, v) <= margine for v in voci): return True
    return False


def forma_valida(testo):
    """Almeno 3 lettere, quasi solo lettere e cifre, nessuna lettera isolata (sintomo di testo spezzato)."""
    lettere = len(re.findall(r"[A-Za-z]", testo))
    alfanum = len(re.findall(r"[A-Za-z0-9]", testo))
    compatti = len(testo.replace(" ", ""))
    if lettere < 3 or compatti == 0: return False
    if lettere / float(alfanum) < 0.7 or alfanum / float(compatti) < 0.7: return False
    if len(re.findall(r"[/\#@_]", testo)) > 1: return False     # indirizzi, percorsi, codici
    return not any(len(g) == 1 and g not in "aAI" for g in _gettoni(testo))


def testi_validi(voci, parole):
    """Per ogni voce (dict con 'testo' e 't') dice se il testo e' valido. Usa solo l'OCR, non il video."""
    voci_dette = vocabolario(parole); istanti = {}
    for v in voci:
        istanti.setdefault(let(v["testo"]), set()).add(round(float(v["t"]), 2))
    esito = {}; cache = {}
    for v in voci:
        testo = v["testo"]
        if testo not in cache:
            cache[testo] = forma_valida(testo) and (len(istanti.get(let(testo), ())) >= 2 or in_vocabolario(testo, voci_dette))
        esito[id(v)] = cache[testo]
    return esito


# ---------------------------------------------------------------- lettura OCR e video

def _scala_zona(zona, origine_w, origine_h, frame):
    h, w = frame.shape[:2]
    return [float(zona[0]) * w / max(1.0, float(origine_w)), float(zona[1]) * h / max(1.0, float(origine_h)),
            float(zona[2]) * w / max(1.0, float(origine_w)), float(zona[3]) * h / max(1.0, float(origine_h))]


def _voci_ocr(lavoro):
    ocr3 = _json(os.path.join(lavoro, "ocr3.json"), {}); ocr1 = _json(os.path.join(lavoro, "ocr1.json"), {})
    risultato = {}; w3, h3 = float(ocr3.get("W") or 1), float(ocr3.get("H") or 1)
    for lettura in ocr3.get("letture", []):
        if not isinstance(lettura, list) or len(lettura) != 4: continue
        t, base_x, base_y, valori = lettura
        for voce in valori:
            if not isinstance(voce, list) or len(voce) < 6: continue
            testo, forza, ax, ay, bx, by = voce[:6]; testo = pulisci_ocr(str(testo))[:80]
            if testo:
                risultato.setdefault(round(float(t), 3), []).append({
                    "t": float(t), "testo": testo, "forza_ocr": float(forza), "origine_w": w3, "origine_h": h3, "fonte": "ocr3",
                    "zona_px": [base_x + ax, base_y + ay, base_x + bx, base_y + by]})
    zone = ocr1.get("zone", []); w1, h1 = 1280.0, float(ocr1.get("H") or 720)
    for indice, letture in ocr1.get("stato", {}).items():
        try: zona = zone[int(indice)]
        except (ValueError, TypeError, IndexError): continue
        for lettura in letture:
            if not isinstance(lettura, list) or len(lettura) != 2: continue
            t, celle = lettura
            for cella in celle:
                if isinstance(cella, list) and len(cella) >= 2:
                    testo = pulisci_ocr(str(cella[0]))[:80]
                    if testo:
                        risultato.setdefault(round(float(t), 3), []).append({
                            "t": float(t), "testo": testo, "forza_ocr": float(cella[1]), "origine_w": w1, "origine_h": h1,
                            "fonte": "ocr1", "zona_px": list(zona)})
    return risultato


def _altezza_riga(per_tempo):
    """Altezza tipica di una riga di testo (mediana delle zone ocr3, in pixel dell'origine)."""
    alte = [v["zona_px"][3] - v["zona_px"][1] for voci in per_tempo.values() for v in voci if v["fonte"] == "ocr3"]
    return float(np.median(alte)) if alte else None


def scegli_voci(per_tempo, parole):
    """Marca ogni voce: valida (testo) e misurabile (una zona ocr1 troppo alta non e' una riga di testo)."""
    tutte = [v for t in sorted(per_tempo) for v in per_tempo[t]]
    esito = testi_validi(tutte, parole); riga = _altezza_riga(per_tempo)
    scartate = {"testo": 0, "zona": 0}
    for v in tutte:
        altezza = v["zona_px"][3] - v["zona_px"][1]
        limite = ALTEZZA_RIGA * riga if riga else 0.055 * v["origine_h"]
        v["valido"] = bool(esito[id(v)])
        v["misurabile"] = v["fonte"] == "ocr3" or altezza <= limite
        if not v["valido"]: scartate["testo"] += 1
        elif not v["misurabile"]: scartate["zona"] += 1
    return scartate


def _fotogrammi(cattura, tempi, fps, conteggi):
    """(t, frame) per ogni istante richiesto: decodifica in avanti quando costa meno di un salto."""
    posizione = None
    for t in tempi:
        bersaglio = int(round(t * fps)) if fps > 0 else 0
        if fps <= 0 or posizione is None or bersaglio < posizione or bersaglio - posizione > SALTO_FOTOGRAMMI:
            cattura.set(cv2.CAP_PROP_POS_MSEC, 1000.0 * t); conteggi["salti"] += 1
            ok, frame = cattura.read()
            posizione = int(round(cattura.get(cv2.CAP_PROP_POS_FRAMES))) if ok else None
        else:
            ok = True
            while posizione < bersaglio and ok:
                ok = cattura.grab(); posizione += 1
            if ok:
                ok, frame = cattura.read(); posizione += 1
            if not ok: posizione = None
        if ok:
            conteggi["fotogrammi"] += 1
            yield t, frame


def leggi(video, lavoro):
    per_tempo = _voci_ocr(lavoro); parole = _json(os.path.join(lavoro, "parole.json"), [])
    scartate = scegli_voci(per_tempo, parole)
    utili = sorted(t for t in per_tempo if any(v["valido"] and v["misurabile"] for v in per_tempo[t]))
    cattura = cv2.VideoCapture(video)
    if not cattura.isOpened(): raise RuntimeError("video non apribile: " + video)
    fps = float(cattura.get(cv2.CAP_PROP_FPS) or 0.0); fotogrammi = float(cattura.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
    durata = fotogrammi / fps if fps > 0 else max(list(per_tempo) or [0.0])
    letture = []; conteggi = {"salti": 0, "fotogrammi": 0}; misure = 0
    try:
        for t, frame in _fotogrammi(cattura, utili, fps, conteggi):
            voci = []; riusi = {}
            for voce in per_tempo[round(t, 3)]:
                corrente = dict(voce); zona = _scala_zona(voce["zona_px"], voce["origine_w"], voce["origine_h"], frame)
                corrente["zona_px_frame"] = zona
                corrente["zona_norm"] = [zona[0] / frame.shape[1], zona[1] / frame.shape[0], zona[2] / frame.shape[1], zona[3] / frame.shape[0]]
                chiave = tuple(int(round(x)) for x in zona)   # ritaglio gia' misurato nello stesso fotogramma
                if chiave not in riusi:
                    riusi[chiave] = misura_stile(frame, zona); misure += 1
                corrente.update(riusi[chiave]); voci.append(corrente)
            letture.append({"t": float(t), "voci": voci})
    finally:
        cattura.release()
    return {"letture": letture, "parole": parole, "durata": float(durata),
            "conteggi": {"fotogrammi_decodificati": conteggi["fotogrammi"], "salti": conteggi["salti"], "misure": misure,
                         "voci_ocr": sum(len(v) for v in per_tempo.values()), "scartate_testo": scartate["testo"],
                         "scartate_zona": scartate["zona"]}}


# ---------------------------------------------------------------- geometria

def _px(voce):
    return voce.get("zona_px_frame") or [voce["zona_norm"][0] * 1280.0, voce["zona_norm"][1] * 720.0,
                                         voce["zona_norm"][2] * 1280.0, voce["zona_norm"][3] * 720.0]


def _altezza(z): return max(1e-6, z[3] - z[1])
def _simili(a, b): return .55 <= _altezza(a) / _altezza(b) <= 1.8


def _stessa_colonna(a, b):
    tol = .8 * max(_altezza(a), _altezza(b))
    return _simili(a, b) and (abs(a[0] - b[0]) <= tol or abs((a[0] + a[2]) / 2 - (b[0] + b[2]) / 2) <= tol or abs(a[2] - b[2]) <= tol) and abs((a[1] + a[3]) / 2 - (b[1] + b[3]) / 2) > .5 * max(_altezza(a), _altezza(b))


def _stessa_riga(a, b):
    return _simili(a, b) and abs((a[1] + a[3]) / 2 - (b[1] + b[3]) / 2) <= .45 * max(_altezza(a), _altezza(b))


def _allineate(a, b): return _stessa_colonna(a, b) or _stessa_riga(a, b)


def _stesso_posto(a, b):
    """Le due zone sono lo stesso riquadro di testo (letto in due istanti)."""
    h = max(_altezza(a), _altezza(b)); larg = max(a[2] - a[0], b[2] - b[0])
    return abs((a[0] + a[2]) / 2 - (b[0] + b[2]) / 2) <= max(.8 * h, .12 * larg) and abs((a[1] + a[3]) / 2 - (b[1] + b[3]) / 2) <= .5 * h


def _compagne(voce, voci):
    z = _px(voce)
    return [x for x in voci if x is not voce and _allineate(z, _px(x))]


def _in_gruppo(voce, voci):
    c = _compagne(voce, voci)
    return len(c) >= 2, c


# ---------------------------------------------------------------- eventi

def _evento(t, tipo, voce, forza, dati, stampa="sezione"):
    z = [max(0.0, min(1.0, float(x))) for x in voce["zona_norm"]]
    return {"id": "", "t": round(float(t), 2), "durata": 0., "canale": "stile", "tipo": tipo, "zona": [round(x, 6) for x in z],
            "punto": [round((z[0] + z[2]) / 2, 6), round((z[1] + z[3]) / 2, 6)], "testo": voce["testo"][:80],
            "forza": round(max(0., min(1., forza)), 4), "stampa": stampa, "rif": [], "dati": dict(dati)}


def _limita_righe(eventi_calcolati, scarti):
    """Al massimo MAX_RIGHE_MINUTO righe in ogni finestra di 60 s, scelte per priorita' di tipo."""
    stampabili = [e for e in eventi_calcolati if e["stampa"] != "nessuna"]
    silenziosi = [e for e in eventi_calcolati if e["stampa"] == "nessuna"]; scelti = []
    for candidato in sorted(stampabili, key=lambda e: (PRIORITA[e["tipo"]], -e["forza"], e["t"], e["testo"])):
        tempi = sorted([e["t"] for e in scelti] + [candidato["t"]])
        stesso_tipo = sorted([e["t"] for e in scelti if e["tipo"] == candidato["tipo"]] + [candidato["t"]])
        massimo = MAX_PER_TIPO_MINUTO.get(candidato["tipo"], MAX_RIGHE_MINUTO)
        libero = (all(sum(1 for x in tempi if inizio <= x < inizio + 60.0) <= MAX_RIGHE_MINUTO for inizio in tempi)
                  and all(sum(1 for x in stesso_tipo if inizio <= x < inizio + 60.0) <= massimo for inizio in stesso_tipo))
        if libero: scelti.append(candidato)
        else: scarti["tetto"] = scarti.get("tetto", 0) + 1
    return sorted(scelti + silenziosi, key=lambda e: (e["t"], e["tipo"], e["testo"]))


def _utile(voce):
    """Voce usabile per gli eventi di colore: testo valido, riga di testo vera, colore misurato."""
    return voce.get("valido", True) and voce.get("misurabile", True) and voce.get("visibile", True) and "hsv" in voce


def nome_breve(testo, larghezza=30):
    """Nome da mostrare: senza il frammento di parentesi aperta in coda (lettura spezzata) e senza spazi attorno."""
    testo = testo.strip()[:larghezza]
    testo = re.sub(r"\s*[\[(][^])]*$", "", testo).strip(" ,;:.-")
    return testo


def _mediana_hsv(lista):
    return [float(np.median([x[i] for x in lista])) for i in range(3)]


def _tracce(letture):
    """Raggruppa le letture dello stesso testo nello stesso posto (voci valide, in ordine di tempo)."""
    tracce = []
    for lettura in letture:
        for voce in lettura["voci"]:
            if not _utile(voce): continue
            z = _px(voce); scelta = None
            for traccia in tracce:
                if _stesso_posto(traccia["zona"], z) and stessi(traccia["testo"], voce["testo"]):
                    scelta = traccia; break
            if scelta is None:
                scelta = {"testo": voce["testo"], "zona": z, "voci": [], "tempi": set()}; tracce.append(scelta)
            if round(voce["t"], 3) in scelta["tempi"]:
                continue   # stessa riga letta da due fonti nello stesso istante
            scelta["tempi"].add(round(voce["t"], 3)); scelta["voci"].append(voce); scelta["zona"] = z
    return tracce


def _cambi_di_traccia(traccia, scarti):
    """Cambi colore veri: nuovo colore netto che resta per LETTURE_STABILI letture consecutive."""
    voci = traccia["voci"]; cambi = []
    if len(voci) < LETTURE_STABILI + 1: return cambi
    corrente = [voci[0]]; i = 1
    while i < len(voci):
        stato = _mediana_hsv([v["hsv"] for v in corrente]); candidata = voci[i]
        if not differenza_netta(stato, candidata["hsv"]):
            corrente.append(candidata); i += 1; continue
        seguenti = voci[i:i + LETTURE_STABILI]
        stabile = len(seguenti) == LETTURE_STABILI and all(not differenza_netta(candidata["hsv"], s["hsv"]) and differenza_netta(stato, s["hsv"]) for s in seguenti[1:])
        if not stabile:
            scarti["passaggio"] = scarti.get("passaggio", 0) + 1; i += 1; continue
        nuovo = _mediana_hsv([s["hsv"] for s in seguenti]); prima, dopo = nome_colore(stato), nome_colore(nuovo)
        fondo_prima = _mediana_hsv([v["hsv_fondo"] for v in corrente]) if "hsv_fondo" in candidata else None
        fondo_dopo = _mediana_hsv([s["hsv_fondo"] for s in seguenti]) if "hsv_fondo" in candidata else None
        inversione = fondo_prima and not differenza_netta(nuovo, fondo_prima) and not differenza_netta(fondo_dopo, stato)
        if inversione:
            scarti["inversione"] = scarti.get("inversione", 0) + 1     # testo e fondo scambiati: e' la misura che si e' capovolta
        elif prima != dopo:
            cambi.append({"traccia": traccia, "t": candidata["t"], "t_prima": corrente[-1]["t"], "prima": prima, "dopo": dopo,
                          "voce": candidata, "letture_prima": len(corrente), "letture_dopo": len(seguenti),
                          "d_luce": round(abs(stato[2] - nuovo[2]), 1), "d_tinta": round(_distanza_tinta(stato[0], nuovo[0]), 1),
                          "fondo": [nome_colore(fondo_prima), nome_colore(fondo_dopo)] if fondo_prima else None})
        corrente = list(seguenti); i += LETTURE_STABILI
    return cambi


def _eventi_cambio(letture, scarti):
    cambi = []
    for traccia in _tracce(letture):
        visti = set()
        for c in _cambi_di_traccia(traccia, scarti):
            if (c["prima"], c["dopo"]) in visti:   # lo stesso testo che ripete lo stesso passaggio (interruttore) vale una volta
                scarti["ripetuto"] = scarti.get("ripetuto", 0) + 1
            else:
                visti.add((c["prima"], c["dopo"])); cambi.append(c)
    # gruppi: stesso passaggio di colore, finestre di tempo che si toccano, stessa colonna o riga
    padre = list(range(len(cambi)))
    def radice(i):
        while padre[i] != i: padre[i] = padre[padre[i]]; i = padre[i]
        return i
    for i in range(len(cambi)):
        for j in range(i + 1, len(cambi)):
            a, b = cambi[i], cambi[j]
            if (a["prima"], a["dopo"]) != (b["prima"], b["dopo"]): continue
            if a["t"] < b["t_prima"] or b["t"] < a["t_prima"] or abs(a["t"] - b["t"]) > 15.0: continue
            if _allineate(_px(a["voce"]), _px(b["voce"])): padre[radice(j)] = radice(i)
    gruppi = {}
    for i, c in enumerate(cambi): gruppi.setdefault(radice(i), []).append(c)
    risultato = []
    for membri in gruppi.values():
        membri.sort(key=lambda c: (round(_px(c["voce"])[1] / 8), _px(c["voce"])[0]))
        if len(membri) == 1:
            c = membri[0]
            risultato.append(_evento(c["t"], "cambio_colore", c["voce"], .85, {"prima": c["prima"], "dopo": c["dopo"], "letture": c["letture_dopo"], "d_luce": c["d_luce"], "d_tinta": c["d_tinta"], "fondo": c["fondo"]}))
            continue
        distinti = []
        for c in membri:
            if c["voce"]["testo"] not in distinti: distinti.append(c["voce"]["testo"])
        quanti = min(4, len(distinti)); largo = max(12, min(30, (75 - 2 * (quanti - 1)) // quanti))   # quattro nomi stanno nei 80 caratteri
        nomi = []
        for testo_voce in distinti:
            nome = nome_breve(testo_voce, largo)
            if nome not in nomi: nomi.append(nome)
        testo = ", ".join(nomi[:4]) + (", ..." if len(nomi) > 4 else "")
        zone = [c["voce"]["zona_norm"] for c in membri]; riga_y = {round(_px(c["voce"])[1] / 8) for c in membri}
        unione = {"testo": testo, "zona_norm": [min(z[0] for z in zone), min(z[1] for z in zone), max(z[2] for z in zone), max(z[3] for z in zone)]}
        t = min(c["t"] for c in membri)
        risultato.append(_evento(t, "cambio_gruppo", unione, min(.99, .9 + .01 * len(membri)),
                                 {"n": len(membri), "nomi": nomi, "cosa": "righe" if len(riga_y) >= 2 else "voci", "prima": membri[0]["prima"], "dopo": membri[0]["dopo"],
                                  "letture": min(c["letture_dopo"] for c in membri), "d_luce": max(c["d_luce"] for c in membri), "d_tinta": max(c["d_tinta"] for c in membri), "fondo": membri[0]["fondo"]}))
    return risultato


def _scelta_regolare(voce, voci):
    """Le voci della stessa colonna (bordo sinistro comune) a passo regolare che includono la voce: una lista di scelte."""
    z = _px(voce); h = _altezza(z)
    colonna = [x for x in voci if x is voce or (_stessa_colonna(z, _px(x)) and abs(_px(x)[0] - z[0]) <= .8 * h)]
    colonna.sort(key=lambda x: (_px(x)[1] + _px(x)[3]) / 2.0)
    centri = [(_px(x)[1] + _px(x)[3]) / 2.0 for x in colonna]; i = colonna.index(voce)
    vicini = [abs(centri[j] - centri[i]) for j in (i - 1, i + 1) if 0 <= j < len(colonna)]
    passo = min(vicini) if vicini else 0.0
    if passo <= 0 or passo > 3.0 * h: return [voce]
    catena = [voce]
    for direzione in (-1, 1):
        j = i + direzione
        while 0 <= j < len(colonna) and .7 * passo <= abs(centri[j] - centri[j - direzione]) <= 1.4 * passo:
            catena.append(colonna[j]); j += direzione
    return catena


def _eventi_di_lettura(lettura, vocab, visti):
    """Disattivato e titolo di una lettura (ognuno una volta per testo)."""
    voci = [v for v in lettura.get("voci", []) if _utile(v)]; risultato = []
    righe = [v for v in voci if v["fonte"] == "ocr3"]   # solo le zone ocr3 sono riquadri stretti: l'altezza dice la dimensione del testo
    altezze = [float(v["altezza"]) for v in righe if float(v["altezza"]) > 0]; mediana = float(np.median(altezze)) if altezze else 0.
    grandi = set(id(v) for v in sorted(righe, key=lambda x: -float(x["altezza"]))[:3])
    def nuovo(tipo, voce):
        if any(stessi(voce["testo"], x) for x in visti[tipo]): return False
        visti[tipo].append(voce["testo"]); return True
    for voce in voci:
        if voce["saturazione"] < SAT_CROMATICO and voce["contrasto"] >= .15:
            catena = _scelta_regolare(voce, voci)
            normali = [x for x in catena if x is not voce and x["contrasto"] >= .5]
            # la voce e' spenta se il suo contrasto e' nettamente piu' basso di quello delle altre della stessa lista,
            # e le spente sono poche: se sono la meta' e' lo stile della lista, non una scelta disattivata
            riferimento = float(np.median([x["contrasto"] for x in normali])) if normali else 1.0
            spente = [x for x in catena if x["saturazione"] < SAT_CROMATICO and .15 <= x["contrasto"] <= .7 * riferimento]
            if len(catena) >= 3 and len(normali) >= 2 and voce["contrasto"] <= .7 * riferimento and len(spente) <= .4 * len(catena) and nuovo("disattivato", voce):
                risultato.append(_evento(voce["t"], "disattivato", voce, .9, {"marca": "~", "contrasto": voce["contrasto"], "lista": len(catena)}))
        titolo = (mediana > 0 and voce["altezza"] >= 1.6 * mediana and id(voce) in grandi and voce["forza_ocr"] >= .9
                  and len(re.findall(r"[A-Za-z]", voce["testo"])) >= 5 and not in_vocabolario(voce["testo"], vocab))
        if titolo and nuovo("titolo", voce):
            risultato.append(_evento(voce["t"], "titolo", voce, .75, {"marca": "#"}, STAMPA_TITOLI))
    return risultato


def _classe_vistosa(voce, voci):
    """('avviso'|'etichetta', colore) se la voce e' un testo rosso/arancio, una targhetta colorata o un testo giallo; altrimenti None."""
    gruppo, compagne = _in_gruppo(voce, voci)
    simili = [x for x in compagne if x["colore"] == voce["colore"]]
    pieno = voce["saturazione"] >= SOGLIA_SATURO and voce["hsv"][2] >= 100 and len(simili) < 2   # una colonna dello stesso colore e' un dato, non un avviso
    if voce["colore_fondo"] in ("rosso", "arancio") and voce["saturazione_fondo"] >= SOGLIA_SATURO and voce["hsv_fondo"][2] >= 100:
        return "avviso", voce["colore_fondo"]
    if voce["colore"] in ("rosso", "arancio") and pieno: return "avviso", voce["colore"]
    if voce["colore"] == "giallo" and pieno: return "etichetta", "giallo"
    delimitata = "hsv_esterno" not in voce or differenza_netta(voce["hsv_fondo"], voce["hsv_esterno"])
    if voce["saturazione_fondo"] >= SOGLIA_SATURO and not gruppo and delimitata: return "etichetta", voce["colore_fondo"]
    return None


def _eventi_vistosi(letture, durata, con_cambio=()):
    """Avvisi ed etichette colorate: testo letto vistoso in almeno 2 istanti e non ancorato fermo per tutto il video.

    Un colore che sta sempre nello stesso punto (stato di un pulsante, tema) non e' un avviso; un avviso compare e
    sparisce, un'etichetta galleggia sul contenuto."""
    per_chiave = {}
    for lettura in letture:
        voci = [v for v in lettura.get("voci", []) if _utile(v)]
        for voce in voci:
            classe = _classe_vistosa(voce, voci)
            if classe: per_chiave.setdefault((classe[0], let(voce["testo"])), []).append((voce, classe[1]))
    risultato = []
    for (tipo, chiave), letture_k in per_chiave.items():
        if chiave in con_cambio: continue    # un testo che cambia colore e' uno stato che si accende, e' gia' un cambio
        istanti = sorted({round(v["t"], 2) for v, _ in letture_k})
        if len(istanti) < LETTURE_STABILI: continue
        gruppi_posto = []
        for voce, _ in letture_k:
            for g in gruppi_posto:
                if _stesso_posto(_px(g[0]), _px(voce)): g.append(voce); break
            else: gruppi_posto.append([voce])
        fermo = max(gruppi_posto, key=lambda g: len({round(v["t"], 2) for v in g}))
        tempi_fermo = sorted({round(v["t"], 2) for v in fermo})
        if len(tempi_fermo) >= 2 and tempi_fermo[-1] - tempi_fermo[0] >= max(SOSTA_FERMO, .25 * durata): continue
        voce, colore = min(letture_k, key=lambda x: x[0]["t"])
        if tipo == "avviso":
            risultato.append(_evento(voce["t"], "avviso", voce, min(.99, .65 + voce["saturazione"] / 850.), {"marca": "!", "colore": colore, "letture": len(istanti)}))
        else:
            risultato.append(_evento(voce["t"], "etichetta", voce, min(.94, .60 + max(voce["saturazione"], voce["saturazione_fondo"]) / 1000.), {"colore_f": FEMMINILE.get(colore, colore), "letture": len(istanti)}))
    return risultato


def _eventi_pota(lettura, vocab):
    """Celle ocr1 deboli, fuori dal vocabolario e senza cifre: solo segnate (pota), mai stampate."""
    voci = [v for v in lettura.get("voci", []) if "rilievo" in v]
    if not voci: return []
    percentile = float(np.percentile([float(v["rilievo"]) for v in voci], 30)); risultato = []
    for voce in voci:
        if voce["fonte"] == "ocr1" and voce["rilievo"] < percentile and not re.search(r"\d", voce["testo"]) and not in_vocabolario(voce["testo"], vocab):
            risultato.append(_evento(voce["t"], "rilievo", voce, max(.01, voce["rilievo"]), {"pota": True}, "nessuna"))
    return risultato


def calcola(dati):
    letture = sorted(dati.get("letture", []), key=lambda x: x["t"]); parole = dati.get("parole", []); vocab = vocabolario(parole)
    scarti = {}
    # la validita' del testo dipende da TUTTE le letture; se non e' gia' marcata la si calcola qui
    senza = [v for lettura in letture for v in lettura.get("voci", []) if "valido" not in v]
    if senza:
        esito = testi_validi(senza, parole)
        for v in senza: v["valido"] = bool(esito[id(v)])
    durata = float(dati.get("durata") or (letture[-1]["t"] if letture else 0.0))
    risultato = _eventi_cambio(letture, scarti); con_cambio = {let(e["testo"]) for e in risultato if e["tipo"] == "cambio_colore"}
    for e in risultato:
        if e["tipo"] == "cambio_gruppo": con_cambio.update(let(x) for x in e["dati"].get("nomi", []))
    risultato.extend(_eventi_vistosi(letture, durata, con_cambio)); visti = {"disattivato": [], "titolo": []}
    for lettura in letture:
        risultato.extend(_eventi_di_lettura(lettura, vocab, visti)); risultato.extend(_eventi_pota(lettura, vocab))
    finali = _limita_righe(risultato, scarti)
    for indice, e in enumerate(finali, 1): e["id"] = "C%d" % indice
    dati["scarti"] = scarti
    return finali


def documento(eventi_calcolati, secondi, letture, durata, extra=None):
    tipi = {}
    for e in eventi_calcolati: tipi[e["tipo"]] = tipi.get(e["tipo"], 0) + 1
    stat = {"secondi": round(float(secondi), 3), "cpu_secondi": round(time.process_time(), 3), "durata": round(float(durata), 3),
            "letture": int(letture), "eventi": len(eventi_calcolati), "eventi_per_tipo": tipi, "ram_picco_mb": ram_picco_mb()}
    stat.update(extra or {})
    return {"schema": 1, "canale": "stile", "lettera": "C", "versione": VERSIONE, "sezione": "RILIEVO", "legenda": LEGENDA,
            "formati": dict(FORMATI), "stat": stat, "eventi": eventi_calcolati}


def main(argv):
    if len(argv) != 3: raise SystemExit("uso: canale_stile.py <video.mp4> <cartella _lavoro>")
    video = os.path.abspath(argv[1]); lavoro = os.path.abspath(argv[2]); inizio = time.perf_counter()
    dati = leggi(video, lavoro); calcolati = calcola(dati)
    extra = dict(dati.get("conteggi", {})); extra.update({"scarti_" + k: v for k, v in dati.get("scarti", {}).items()})
    doc = documento(calcolati, time.perf_counter() - inizio, len(dati["letture"]), dati["durata"], extra)
    eventi.scrivi(os.path.join(lavoro, "eventi_stile.json"), doc)
    print("canale_stile: eventi=%d letture=%d tempo=%.1fs ram_picco=%s MB tipi=%s" % (
        len(calcolati), len(dati["letture"]), doc["stat"]["secondi"], doc["stat"]["ram_picco_mb"],
        json.dumps(doc["stat"]["eventi_per_tipo"], ensure_ascii=False, sort_keys=True)))


if __name__ == "__main__":
    main(sys.argv)
