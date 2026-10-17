# -*- coding: utf-8 -*-
"""Canale D1 - cursore (sezione CURSORE del racconto).

Uso:  python -I canale_cursore.py <video.mp4> <cartella _lavoro>
Scrive <_lavoro>\\eventi_cursore.json (schema unico, vedi eventi.py) e
<_lavoro>\\cursore.json (traccia densa [t, x, y, forma, conf] a 10 fps, in frazioni).

Passi:
  1. fotogrammi()         lettura a 10 fps, grigi, larghezza <= 1280 (INTER_LINEAR, mai INTER_AREA)
  2. impara_modelli()     modelli automatici del cursore dalle macchie in movimento
  3. Inseguitore          inseguimento fotogramma per fotogramma (locale, fermo, globale, macchia)
  4. estrai_eventi()      sosta / clic / trascina / forma dalla traccia
  5. assegna_nomi()       nome dell'oggetto sotto il punto: Scena, poi OCR di un ritaglio

Il calcolo (funzioni pure su array) e' separato dalla lettura (cv2.VideoCapture, rapidocr):
i test sintetici passano fotogrammi generati, non video.
Copertura = quota dei fotogrammi campionati con il cursore TROVATO, cioe' posizione con NCC
sopra soglia (fonte locale/globale) o ritaglio fermo confermato (fonte fermo). La macchia
mobile e' solo un'ipotesi di ricerca: non e' mai contata come trovato.
"""
import argparse, ctypes, json, math, os, sys, time
import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eventi
from testi import pulisci_ocr, utile

VERSIONE = '0.3'

PREDEFINITI = {
    # lettura
    'fps': 10.0, 'larghezza_max': 1280,
    # apprendimento dei modelli
    'addestra_s': 60.0, 'addestra_finestre': 3, 'soglia_diff': 25, 'lato_min': 6, 'lato_max': 30,
    'quota_fissa': 0.15, 'max_macchie_coppia': 4, 'max_frazione_cambio': 0.02,
    'patch': 48, 'lato_modello': 0, 'max_candidati': 2500, 'ncc_gruppo': 0.70, 'min_membri': 6,
    'max_modelli': 4, 'estensione_min': 40, 'estensione_rel': 0.12, 'maschera': True, 'coppie_cursore': True, 'membri_ok': 20,
    # inseguimento
    'finestra': 96, 'ncc_ferma': 0.60, 'ncc_locale': 0.72, 'ncc_globale': 0.72, 'cella': 32, 'quota_occupata': 0.35, 'soglia_occ': 10, 'occ_alpha': 0.03, 'occ_online': 0.5, 'occ_forte': 0.2, 'calibra': True, 'calibra_fotogrammi': 20, 'calibra_finestre': 12, 'calibra_quantile': 99.0, 'calibra_margine': 0.03, 'soglia_min': 0.62, 'soglia_max': 0.90, 'scala_globale': 0.5, 'globale': 'meta',
    'vel_max': 24, 'fermo_diff': 4.0, 'moto_conferma': True, 'moto_min': 3, 'salto_px': 3.0,
    'margine_fermo': 0.10, 'calo_ferma': 0.20, 'calo_fermo': 0.25, 'contrasto_min': 8.0, 'ncc_fermo': 0.60, 'max_zona_cambiata': 0.05, 'max_macchie_ricerca': 8,
    # eventi
    'clic_entro': 2.0, 'sosta_min': 0.5, 'sosta_max': 8.0, 'raggio_sosta_px': 4.0, 'raggio_clic': 0.06,
    'stampa_al_minuto': 12, 'forme_al_minuto': 6, 'letture_max': 60, 'ocr_tipo': 'min', 'ocr_lato': 128,
}
PAR = dict(PREDEFINITI)


def imposta(valori=None):
    """Riporta i parametri ai predefiniti e applica gli eventuali valori (prove di taratura)."""
    PAR.clear(); PAR.update(PREDEFINITI)
    for k, v in (valori or {}).items():
        if k not in PREDEFINITI: raise KeyError('parametro sconosciuto: ' + k)
        PAR[k] = type(PREDEFINITI[k])(v) if not isinstance(PREDEFINITI[k], bool) else (str(v).lower() in ('1', 'true', 'si'))


# ---------------------------------------------------------------- misure di sistema
def rss_mb():
    """Picco di memoria del processo, in MB."""
    if sys.platform != "win32":
        try:
            import resource
            valore = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            divisore = 1048576.0 if sys.platform == "darwin" else 1024.0
            return valore / divisore
        except (ImportError, AttributeError, OSError):
            return None

    class C(ctypes.Structure):
        _fields_ = [('cb', ctypes.c_ulong), ('PageFaultCount', ctypes.c_ulong), ('PeakWorkingSetSize', ctypes.c_size_t),
                    ('WorkingSetSize', ctypes.c_size_t), ('QuotaPeakPagedPoolUsage', ctypes.c_size_t),
                    ('QuotaPagedPoolUsage', ctypes.c_size_t), ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
                    ('QuotaNonPagedPoolUsage', ctypes.c_size_t), ('PagefileUsage', ctypes.c_size_t),
                    ('PeakPagefileUsage', ctypes.c_size_t)]
    try:
        c = C(); c.cb = ctypes.sizeof(c)
        kernel = ctypes.windll.kernel32; psapi = ctypes.windll.psapi
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(C), ctypes.c_ulong]
        ok = psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(c), c.cb)
        return c.PeakWorkingSetSize / (1024.0 * 1024.0) if ok else 0.0
    except (AttributeError, OSError):
        return 0.0


# ---------------------------------------------------------------- lettura del video
def riduci(img):
    """Larghezza al massimo 1280, INTER_LINEAR: INTER_AREA cancellerebbe la crocetta sottile."""
    h, w = img.shape[:2]
    if w <= PAR['larghezza_max']: return img
    s = PAR['larghezza_max'] / float(w)
    return cv2.resize(img, (PAR['larghezza_max'], max(1, int(round(h * s)))), interpolation=cv2.INTER_LINEAR)


def info_video(video):
    """(larghezza nativa, altezza nativa, fps, durata s)"""
    cap = cv2.VideoCapture(video)
    if not cap.isOpened(): raise RuntimeError('video non apribile')
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)); h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0); n = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
    cap.release()
    if not (1.0 <= fps <= 240.0): fps = 25.0
    return w, h, fps, n / fps


def fotogrammi(video, fino=None, da=0.0):
    """Genera (t, grigio) a ~10 fps tra `da` e `fino` secondi. Non tiene fotogrammi in memoria."""
    cap = cv2.VideoCapture(video)
    if not cap.isOpened(): raise RuntimeError('video non apribile')
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    if not (1.0 <= fps <= 240.0): fps = 25.0
    passo = 1.0 / PAR['fps']; prossimo = da; indice = 0
    try:
        while True:
            if not cap.grab(): break
            t = indice / fps; indice += 1
            if fino is not None and t > fino: break
            if t < da - 1e-6 or t + 1e-6 < prossimo: continue
            ok, frame = cap.retrieve()
            if not ok: break
            prossimo += passo
            if prossimo < t: prossimo = t + passo
            yield round(t, 3), riduci(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
    finally:
        cap.release()


# ---------------------------------------------------------------- geometria e ritagli
def patch_a(img, cx, cy, lato):
    """Quadrato lato x lato centrato su (cx, cy); None se esce dal fotogramma."""
    h, w = img.shape[:2]; x0 = int(round(cx)) - lato // 2; y0 = int(round(cy)) - lato // 2
    if x0 < 0 or y0 < 0 or x0 + lato > w or y0 + lato > h: return None
    return img[y0:y0 + lato, x0:x0 + lato]


def distanza_px(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def macchie_mobili(prec, g, con_maschere=False):
    """Macchie di cambio tra due fotogrammi consecutivi con lato 6-30 px.
    Ritorna ([(cx, cy, x, y, w, h)], quota di pixel cambiati); con con_maschere ogni macchia ha in coda la sua
    silhouette booleana. Dilatazione 3x3 per unire i bordi."""
    mask = (cv2.absdiff(prec, g) >= PAR['soglia_diff']).astype(np.uint8)
    cambiati = int(mask.sum())
    if cambiati == 0: return [], 0.0
    mask = cv2.dilate(mask, np.ones((3, 3), np.uint8))
    n, lab, st, cen = cv2.connectedComponentsWithStats(mask, connectivity=8)
    out = []
    for i in range(1, n):
        x, y, w, h = (int(v) for v in st[i][:4])
        if PAR['lato_min'] <= max(w, h) - 2 <= PAR['lato_max']:        # -2: la dilatazione aggiunge 1 px per lato
            m = (float(cen[i][0]), float(cen[i][1]), x, y, w, h)
            out.append(m + (lab[y:y + h, x:x + w] == i,) if con_maschere else m)
    return out, cambiati / float(mask.size)


# ---------------------------------------------------------------- forma del modello
def primo_piano(img):
    """Maschera dei pixel che si staccano dallo sfondo del modello (le componenti non trascurabili)."""
    bordo = np.concatenate([img[:2].ravel(), img[-2:].ravel(), img[:, :2].ravel(), img[:, -2:].ravel()])
    d = np.abs(img.astype(np.float32) - float(np.median(bordo)))
    fg = (d >= max(40.0, 0.35 * float(d.max()))).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(fg, connectivity=8)
    if n <= 1: return fg > 0
    aree = st[1:, cv2.CC_STAT_AREA]; tenute = 1 + np.nonzero(aree >= max(4, 0.15 * aree.max()))[0]       # i bracci staccati di una crocetta restano
    return np.isin(lab, tenute)


def _a_croce(fg):
    """Crocetta: quattro bracci sottili che partono dal centro (anche con un vuoto al centro) e quasi nessuna massa fuori dai bracci."""
    h, w = fg.shape
    if w < 7 or h < 7 or not 0.6 <= w / float(h) <= 1.6: return False
    yy, xx = np.mgrid[0:h, 0:w]; cx = (w - 1) / 2.0; cy = (h - 1) / 2.0
    fascia_o = np.abs(yy - cy) <= 1.5; fascia_v = np.abs(xx - cx) <= 1.5
    totale = int(fg.sum())
    if totale == 0 or int((fg & ~fascia_o & ~fascia_v).sum()) > 0.2 * totale: return False
    bracci = [fg & fascia_o & (xx < cx - 1.5), fg & fascia_o & (xx > cx + 1.5), fg & fascia_v & (yy < cy - 1.5), fg & fascia_v & (yy > cy + 1.5)]
    lunghezza = [w / 2.0 - 1.5, w / 2.0 - 1.5, h / 2.0 - 1.5, h / 2.0 - 1.5]
    return all(int(b.sum()) >= 0.35 * l * 2 for b, l in zip(bracci, lunghezza))        # banda di 3 px: ~3 px per ogni px di lunghezza ma i tratti sono da 1-2


def descrivi_modello(img):
    """(forma, (hot_x, hot_y)): forma dalla geometria del primo piano, punta (hotspot) relativa al centro."""
    lato = img.shape[0]; ys, xs = np.nonzero(primo_piano(img))
    centro = (0.0, 0.0)
    if len(xs) < 6: return 'freccia', centro
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max()); w = x1 - x0 + 1; h = y1 - y0 + 1
    mx = (xs.mean() - x0) / max(1.0, w - 1.0); my = (ys.mean() - y0) / max(1.0, h - 1.0)
    fg = np.zeros((h, w), bool); fg[ys - y0, xs - x0] = True
    if h >= 2 * w and h >= 8: forma = 'testo'                       # alto e stretto
    elif _a_croce(fg): forma = 'croce'
    elif mx < 0.46 and my < 0.62: forma = 'freccia'                   # massa in alto a sinistra
    else: forma = 'mano'
    if forma in ('testo', 'croce'): hx, hy = (x0 + x1 + 1) / 2.0, (y0 + y1 + 1) / 2.0
    elif forma == 'mano': hx, hy = x0 + 0.35 * w, y0 + 0.5            # la punta del dito indice, in alto
    else: hx, hy = x0 + 0.5, y0 + 0.5                                # freccia: la punta e' l'angolo in alto a sinistra
    return forma, (hx - lato / 2.0, hy - lato / 2.0)


def classifica_forma(img):
    return descrivi_modello(img)[0]


# ---------------------------------------------------------------- apprendimento dei modelli
def _ncc_migliore(patch, modello):
    """(punteggio, x, y, negativo): migliore corrispondenza TM_CCOEFF_NORMED del modello nella patch,
    nelle due polarita' (il negativo del modello ha correlazione col segno opposto)."""
    r = cv2.matchTemplate(patch, modello, cv2.TM_CCOEFF_NORMED)
    mn, mx, pmn, pmx = cv2.minMaxLoc(r)
    mx = float(mx) if math.isfinite(mx) else 0.0; mn = float(mn) if math.isfinite(mn) else 0.0
    return (mx, pmx[0], pmx[1], False) if mx >= -mn else (-mn, pmn[0], pmn[1], True)


def _normalizza(righe):
    v = righe.astype(np.float32); v -= v.mean(axis=1, keepdims=True)
    n = np.linalg.norm(v, axis=1, keepdims=True); n[n < 1e-3] = np.inf       # ritagli piatti: vettore nullo
    return v / n


def _recluta(patches, liberi, modello):
    """Allinea al modello (scorrendolo dentro la patch 48x48) i ritagli liberi che lo somigliano:
    -> [(indice, ritaglio allineato e riportato alla polarita' del modello)]."""
    lato = modello.shape[0]; membri = []
    for j in liberi:
        punteggio, x, y, neg = _ncc_migliore(patches[j], modello)
        if punteggio >= PAR['ncc_gruppo']:
            r = patches[j][y:y + lato, x:x + lato]
            membri.append((j, 255 - r if neg else r))
    return membri


def scegli_lato(lati):
    """Lato del modello: 32 se imposto; altrimenti adattivo alla macchia tipica (16-32, pari)."""
    if PAR['lato_modello']: return int(PAR['lato_modello'])
    med = float(np.median(lati)) if len(lati) else 12.0
    return int(min(32, max(16, 2 * round((med + 8) / 2.0))))


def celle_cambiate(prec, g, diff=None, soglia=None):
    """Mappa booleana delle celle (cella x cella px) in cui il fotogramma e' cambiato (almeno 6 pixel sopra `soglia_occ`:
    bassa, perche' il rumore di una webcam o di un video non arriva a 25 livelli, mentre lo schermo digitale e' fermo)."""
    c = PAR['cella']; m = (cv2.absdiff(prec, g) if diff is None else diff) >= (PAR['soglia_occ'] if soglia is None else soglia); h = m.shape[0] // c; w = m.shape[1] // c
    return m[:h * c, :w * c].reshape(h, c, w, c).sum(axis=(1, 3)) >= 6


def _ncc_uguali(a, b):
    """Coefficiente di correlazione fra due ritagli della stessa misura."""
    x = a.astype(np.float32).ravel(); y = b.astype(np.float32).ravel(); x -= x.mean(); y -= y.mean()
    d = float(np.linalg.norm(x) * np.linalg.norm(y))
    return float(x.dot(y) / d) if d > 1e-6 else 0.0


def _spostamenti(prec, g, macchie):
    """Coppie (nuova, vecchia) di macchie che sono lo stesso oggetto spostato: due macchie di misura quasi uguale,
    lontane almeno 6 px, con silhouette sovrapponibili, e il cui ritaglio nel fotogramma in cui l'oggetto c'e'
    e' lo stesso (nuova: vista in `g`; vecchia: vista in `prec`). Un cambio isolato dell'interfaccia non ha gemelle."""
    out = []
    for i in range(len(macchie)):
        for j in range(i + 1, len(macchie)):
            a, b = macchie[i], macchie[j]
            if distanza_px(a[:2], b[:2]) < 6 or abs(a[4] - b[4]) > 2 or abs(a[5] - b[5]) > 2: continue
            ma, mb = a[6], b[6]; H = max(ma.shape[0], mb.shape[0]); W = max(ma.shape[1], mb.shape[1])
            pa = np.zeros((H, W), bool); pb = np.zeros((H, W), bool); pa[:ma.shape[0], :ma.shape[1]] = ma; pb[:mb.shape[0], :mb.shape[1]] = mb
            if (pa & pb).sum() < 0.4 * max(1, (pa | pb).sum()): continue
            lato = max(a[4], a[5], b[4], b[5]) + 4
            ag, bp, bg, ap = patch_a(g, a[0], a[1], lato), patch_a(prec, b[0], b[1], lato), patch_a(g, b[0], b[1], lato), patch_a(prec, a[0], a[1], lato)
            if ag is None or bp is None or bg is None or ap is None: continue
            x = _ncc_uguali(ag, bp); y = _ncc_uguali(bg, ap)         # a e' la nuova (b la vecchia), oppure il contrario
            if max(x, y) >= 0.5: out.append((a, b) if x >= y else (b, a))
    return out


class Apprendista:
    """Raccoglie, fotogramma dopo fotogramma, i candidati del cursore e ne ricava i modelli.

    Candidati: le macchie in movimento (lato 6-30 px, soglia 25) che hanno una gemella (l'oggetto che si e'
    spostato); posizioni fisse escluse (>= 15% delle coppie). Ritagli 32x32 attorno al baricentro, raggruppati con NCC >= 0.7
    (entrambe le polarita'); i 4 gruppi piu' numerosi; modello = mediana dei ritagli allineati."""

    def __init__(self):
        self.cand = []; self.celle = {}; self.coppie = 0; self.prec = None; self.larghezza = 0
        self.occupazione = None; self.occupate = None      # celle sempre in movimento (volto in webcam, video, vista animata)

    def aggiungi(self, g):
        P = PAR['patch']; prec = self.prec; self.prec = g; self.larghezza = g.shape[1]
        if prec is None: return
        self.coppie += 1
        cc_ = celle_cambiate(prec, g); self.occupazione = cc_.astype(np.int32) if self.occupazione is None else self.occupazione + cc_
        macchie, frazione = macchie_mobili(prec, g, con_maschere=True)
        if not macchie or len(macchie) > PAR['max_macchie_coppia'] or frazione > PAR['max_frazione_cambio']: return
        if PAR['coppie_cursore']:      # un oggetto che si sposta: due macchie gemelle, l'ex posto e il nuovo
            sp = _spostamenti(prec, g, macchie)
            esempi = [(nuova, g) for nuova, _ in sp] + [(vecchia, prec) for _, vecchia in sp]
        else:                          # modalita' di prova: ogni macchia, vista in entrambi i fotogrammi
            esempi = [(m, g) for m in macchie] + [(m, prec) for m in macchie]
        for m, immagine in esempi:
            cx, cy, x, y, w, h = m[:6]; a = patch_a(immagine, cx, cy, P)
            if a is None: continue
            cella = (int(cx // 4), int(cy // 4)); self.celle.setdefault(cella, set()).add(self.coppie)
            self.cand.append((a.copy(), cx, cy, max(w, h) - 2, cella))

    def modelli(self):
        """Ritorna la lista di {'img', 'forma', 'hot', 'n', 'mask'?} dal piu' numeroso."""
        celle = self.celle; limite = max(1, int(math.ceil(self.coppie * PAR['quota_fissa'])))
        def fissa(cella):
            return sum(len(celle.get((cella[0] + i, cella[1] + j), ())) for i in (-1, 0, 1) for j in (-1, 0, 1)) >= limite
        self.occupate = None if self.occupazione is None else self.occupazione >= PAR['quota_occupata'] * max(1, self.coppie)
        cella = PAR['cella']
        def occupata(cx, cy):
            return self.occupate is not None and bool(self.occupate[min(self.occupate.shape[0] - 1, int(cy // cella)), min(self.occupate.shape[1] - 1, int(cx // cella))])
        cand = [c for c in self.cand if not fissa(c[4]) and not occupata(c[1], c[2])]
        if len(cand) < PAR['min_membri']: return []
        if len(cand) > PAR['max_candidati']:
            scelti = np.random.default_rng(0).choice(len(cand), PAR['max_candidati'], replace=False); cand = [cand[i] for i in sorted(scelti)]
        P = PAR['patch']; patches = np.stack([c[0] for c in cand]); pos = np.array([[c[1], c[2]] for c in cand], np.float32)
        L = scegli_lato([c[3] for c in cand]); o = (P - L) // 2
        A = _normalizza(patches[:, o:o + L, o:o + L].reshape(len(cand), -1)); S = A @ A.T
        vicini = np.abs(S) >= PAR['ncc_gruppo']; del S
        usati = np.zeros(len(cand), bool); modelli = []
        estensione_min = max(float(PAR['estensione_min']), PAR['estensione_rel'] * self.larghezza)
        for _ in range(PAR['max_modelli']):
            conta = (vicini & ~usati[None, :]).sum(axis=1); conta[usati] = -1
            seme = int(np.argmax(conta))
            if conta[seme] < PAR['min_membri']: break
            m = patches[seme][o:o + L, o:o + L].copy(); membri = []
            for _giro in range(2):                                   # allinea, mediana, riallinea
                membri = _recluta(patches, np.nonzero(~usati)[0], m)
                if len(membri) < PAR['min_membri']: break
                m = np.median(np.stack([r for _, r in membri]), axis=0).astype(np.uint8)
            usati[seme] = True                                       # il seme non si riprova mai
            if len(membri) < PAR['min_membri']: continue
            for j, _ in membri: usati[j] = True
            p = pos[[j for j, _ in membri]]; estensione = max(float(np.ptp(p[:, 0])), float(np.ptp(p[:, 1])))
            if estensione < estensione_min or float(m.std()) < 10.0: continue   # sempre nello stesso angolo (un volto, un'icona) o piatto: non e' un cursore
            fg = primo_piano(m); quota_fg = float(fg.mean())
            tocca_bordo = bool(fg[0].any() or fg[-1].any() or fg[:, 0].any() or fg[:, -1].any())
            if int(fg.sum()) < 12 or quota_fg > 0.45 or tocca_bordo or int(m.max()) - int(m.min()) < 60: continue    # nessuna sagoma compatta e netta (sfumatura, bordo di una superficie): non e' un cursore
            forma, hot = descrivi_modello(m)
            mod = {'img': m, 'forma': forma, 'hot': hot, 'n': len(membri), 'scalati': {}}
            if PAR['maschera']:        # confronto solo sui pixel del cursore (e un alone): lo sfondo cambia, il cursore no
                fg = primo_piano(m).astype(np.uint8)
                alone = cv2.dilate(fg, np.ones((3, 3), np.uint8), iterations=2 if forma == 'croce' else 1)
                if 12 <= int(alone.sum()) < 0.8 * alone.size: mod['mask'] = alone * 255
            modelli.append(mod)
        modelli.sort(key=lambda m: -m['n'])
        return modelli


def impara_modelli(frames):
    """frames: iterabile di (t, grigio) -> modelli automatici del cursore."""
    a = Apprendista()
    for _, g in frames: a.aggiungi(g)
    return a.modelli()


def impara_da_video(video, durata_video=None):
    """Impara dai primi 60 s; se non ci sono almeno due modelli con `membri_ok` esempi continua fino a 120 e 180 s,
    in un'unica lettura del video (i candidati si accumulano: piu' esempi, modelli piu' sicuri).
    Ritorna (modelli, mappa delle celle sempre in movimento)."""
    a = Apprendista(); modelli = []; fine_blocco = PAR['addestra_s']; ultimo = 0.0
    for t, g in fotogrammi(video, fino=PAR['addestra_s'] * PAR['addestra_finestre']):
        a.aggiungi(g); ultimo = t
        if t >= fine_blocco:
            modelli = a.modelli(); fine_blocco += PAR['addestra_s']
            if len(modelli) >= 2 and modelli[1]['n'] >= PAR['membri_ok']: return modelli, a.occupate      # due forme del cursore ben documentate: basta
    modelli = a.modelli()
    return modelli, a.occupate


# ---------------------------------------------------------------- ricerca dei modelli
def _modello_scalato(m, scala):
    if scala == 1.0: return m['img']
    if scala not in m['scalati']:
        lato = max(4, int(round(m['img'].shape[0] * scala)))
        m['scalati'][scala] = cv2.resize(m['img'], (lato, lato), interpolation=cv2.INTER_LINEAR)
    return m['scalati'][scala]


def candidati(img, modelli, ox=0.0, oy=0.0, scala=1.0):
    """Picchi NCC (uno per polarita' e per modello) in `img`, ordinati dal migliore.
    Ritorna [(punteggio, ax, ay, modello)] con (ax, ay) = centro del modello in px del fotogramma."""
    out = []
    for m in modelli:
        t = _modello_scalato(m, scala); th, tw = t.shape
        if img.shape[0] < th or img.shape[1] < tw: continue
        if 'mask' in m and scala == 1.0: r = cv2.matchTemplate(img, t, cv2.TM_CCOEFF_NORMED, mask=m['mask'])
        else: r = cv2.matchTemplate(img, t, cv2.TM_CCOEFF_NORMED)
        mn, mx, pmn, pmx = cv2.minMaxLoc(r)
        for v, p in ((mx, pmx), (-mn, pmn)):
            if math.isfinite(v) and float(img[p[1]:p[1] + th, p[0]:p[0] + tw].std()) >= PAR['contrasto_min']:      # zone piatte (nero, bianco) non sono un cursore
                out.append((float(min(1.0, v)), (ox + p[0] + tw / 2.0) / scala, (oy + p[1] + th / 2.0) / scala, m))
    out.sort(key=lambda c: -c[0])
    return out


def soglia_di(m, base):
    """Soglia NCC per un cursore che si sposta: quella calibrata sul video per il modello o, senza, quella fissa."""
    return m.get('soglia', base)


def calibra_soglie(frames, modelli, semina=0):
    """Imposta m['soglia'] per ogni modello: sopra il livello di somiglianza che il modello raggiunge su finestre 96x96
    prese a caso dal video (volti in webcam, icone, reticoli), dove il cursore di solito non c'e'.
    soglia = quantile 99 dei migliori punteggi + margine, fra soglia_min e soglia_max. frames: lista di grigi."""
    rng = np.random.default_rng(semina); f = PAR['finestra']; punteggi = [[] for _ in modelli]
    for g in frames:
        h, w = g.shape
        if h < f or w < f: continue
        for _ in range(PAR['calibra_finestre']):
            x0 = int(rng.integers(0, w - f + 1)); y0 = int(rng.integers(0, h - f + 1)); finestra = g[y0:y0 + f, x0:x0 + f]
            for i, m in enumerate(modelli):
                c = candidati(finestra, [m])
                punteggi[i].append(c[0][0] if c else 0.0)
    for m, v in zip(modelli, punteggi):
        livello = float(np.percentile(v, PAR['calibra_quantile'])) if v else 0.0
        m['soglia'] = float(np.clip(livello + PAR['calibra_margine'], PAR['soglia_min'], PAR['soglia_max']))


def campiona_fotogrammi(video, quanti, durata):
    """`quanti` fotogrammi grigi presi a intervalli regolari su tutto il video (salti con POS_MSEC, nessuna lettura completa)."""
    cap = cv2.VideoCapture(video); out = []
    try:
        for k in range(quanti):
            cap.set(cv2.CAP_PROP_POS_MSEC, (0.5 + k * (max(1.0, durata) - 1.0) / max(1, quanti - 1)) * 1000.0)
            ok, frame = cap.read()
            if ok: out.append(riduci(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)))
    finally:
        cap.release()
    return out


def cerca_locale(g, modelli, previsto):
    """Candidati nella finestra 96x96 attorno alla posizione prevista."""
    h, w = g.shape; m = PAR['finestra'] // 2
    x0 = int(max(0, min(w - 1, round(previsto[0]) - m))); y0 = int(max(0, min(h - 1, round(previsto[1]) - m)))
    return candidati(g[y0:y0 + 2 * m, x0:x0 + 2 * m], modelli, x0, y0)


def cerca_globale(g, modelli, cambio=None):
    """Candidati sull'intero fotogramma: ridotto a meta' (predefinito) o, con globale=regioni, solo dove e' cambiato qualcosa."""
    if PAR['globale'] == 'regioni' and cambio is not None:
        out = []; lato = int(max(m['img'].shape[0] for m in modelli))
        zone = cv2.dilate(cambio, np.ones((3, 3), np.uint8), iterations=lato // 2)
        n, _, st, _ = cv2.connectedComponentsWithStats(zone, connectivity=8)
        for i in sorted(range(1, n), key=lambda i: -st[i][cv2.CC_STAT_AREA])[:6]:
            x, y, w, h = (int(v) for v in st[i][:4]); x0 = max(0, x - lato); y0 = max(0, y - lato)
            out += candidati(g[y0:min(g.shape[0], y + h + lato), x0:min(g.shape[1], x + w + lato)], modelli, x0, y0)
        out.sort(key=lambda c: -c[0]); return out
    s = PAR['scala_globale']
    if s >= 1.0: return candidati(g, modelli)
    piccolo = cv2.resize(g, (max(1, int(g.shape[1] * s)), max(1, int(g.shape[0] * s))), interpolation=cv2.INTER_LINEAR)
    return candidati(piccolo, modelli, scala=s)


# ---------------------------------------------------------------- inseguimento
def _ncc_anello(a, b, anello):
    x = a[anello].astype(np.float32); y = b[anello].astype(np.float32); x -= x.mean(); y -= y.mean()
    d = float(np.linalg.norm(x) * np.linalg.norm(y))
    return float(x.dot(y) / d) if d > 1e-6 else 0.0


class Inseguitore:
    """Segue il cursore fotogramma per fotogramma. Tiene in memoria solo il fotogramma precedente.

    Per ogni fotogramma raccoglie candidati (punteggio NCC, posizione, modello):
      - fermi: nella finestra 96x96 attorno alla posizione prevista, a meno di 3 px dall'ultima posizione;
      - in movimento: nella stessa finestra ma altrove, o nelle zone dove il fotogramma e' cambiato
        (macchie compatte 6-30 px). Un candidato in movimento vale solo se attorno a lui qualcosa e' cambiato.
    Vince il migliore in movimento, a meno che il candidato fermo lo superi di piu' di `margine_fermo`:
    cosi' un'icona statica simile al cursore non trattiene l'inseguitore mentre il cursore vero si muove.
    Se nessuno supera la soglia: fermo (il ritaglio sotto l'ultima posizione e' uguale), poi ricerca
    sull'intero fotogramma ridotto a meta' (solo se il fotogramma e' cambiato), poi la macchia mobile
    piu' vicina (solo ipotesi di ricerca, mai "trovato")."""

    def __init__(self, modelli, occupate=None):
        self.modelli = modelli; self.prec = None; self.occupate = occupate
        self.ultimo = None          # ultima posizione CONFERMATA: {'ax','ay','modello'}
        self.crop = None            # ritaglio 32x32 sotto l'ultima posizione confermata
        self.prec_trovato = False   # il campione precedente era confermato?
        self.seme = None            # ipotesi da macchia mobile: non e' mai "trovato"
        self.vel = (0.0, 0.0)
        self.anello = np.ones((48, 48), bool); self.anello[12:36, 12:36] = False
        self.lato_max = max([m['img'].shape[0] for m in modelli] or [32])
        self.recenti = []           # ultimi punteggi confermati: il picco dice com'e' fatto il cursore quando c'e' davvero
        self.occ = None; self.occ25 = None   # medie mobili (cella per cella): quanto cambia poco (rumore) e quanto cambia molto; webcam e video compaiono a meta' filmato

    def _mosso(self, cambio, ax, ay):
        """Ci sono pixel cambiati attorno al candidato? Un cursore appena arrivato li' li ha cambiati."""
        if cambio is None: return False
        r = 14; h, w = cambio.shape; x0 = max(0, int(ax) - r); y0 = max(0, int(ay) - r)
        return int(cambio[y0:min(h, int(ay) + r), x0:min(w, int(ax) + r)].sum()) >= PAR['moto_min']

    def _occupata(self, ax, ay):
        """Il punto cade in una zona sempre in movimento (webcam, video)? Li' un cursore nuovo non si accetta."""
        c = PAR['cella']
        if self.occupate is not None and bool(self.occupate[min(self.occupate.shape[0] - 1, int(ay // c)), min(self.occupate.shape[1] - 1, int(ax // c))]): return True
        if self.occ is None: return False
        i = min(self.occ.shape[0] - 1, int(ay // c)); j = min(self.occ.shape[1] - 1, int(ax // c))
        # zona "di rumore": cambia di continuo ma di poco (webcam, video); una vista 3D in movimento cambia molto e non vale
        return float(self.occ[i, j]) >= PAR['occ_online'] and float(self.occ25[i, j]) <= PAR['occ_forte']

    def _picco(self):
        """Miglior punteggio confermato negli ultimi ~3 s: se all'improvviso il confronto crolla, il cursore se n'e' andato."""
        return max(self.recenti) if self.recenti else 0.0

    def _vicino_all_ultimo(self, ax, ay):
        return self.ultimo is not None and distanza_px((ax, ay), (self.ultimo['ax'], self.ultimo['ay'])) <= PAR['salto_px']

    def _fermo(self, g):
        """Fermo: il ritaglio sotto l'ultima posizione e' uguale al precedente E un modello vi riconosce ancora il cursore
        (almeno ncc_fermo: un ritaglio uniforme o senza cursore non basta). Ritorna (punteggio, ax, ay, modello) o None."""
        if self.ultimo is None or self.crop is None: return None
        ax, ay = self.ultimo['ax'], self.ultimo['ay']; p = patch_a(g, ax, ay, 32)
        if p is None or float(np.mean(cv2.absdiff(p, self.crop))) >= PAR['fermo_diff']: return None
        h, w = g.shape; mezzo = self.lato_max // 2 + 6; x0 = int(max(0, round(ax) - mezzo)); y0 = int(max(0, round(ay) - mezzo))
        for punteggio, bx, by, m in candidati(g[y0:min(h, y0 + 2 * mezzo), x0:min(w, x0 + 2 * mezzo)], self.modelli, x0, y0):
            if punteggio < max(PAR['ncc_fermo'], self._picco() - PAR['calo_fermo']): break
            if distanza_px((bx, by), (ax, ay)) <= 6: return (punteggio, ax, ay, m)
        return None

    def _segue(self, prec, g, p0, p1):
        """Il contenuto attorno al cursore si e' spostato con lui (trascinamento)?"""
        a = patch_a(prec, p0[0], p0[1], 48); b = patch_a(g, p1[0], p1[1], 48); c = patch_a(g, p0[0], p0[1], 48)
        if a is None or b is None or c is None: return False
        if float(a[self.anello].std()) < 12.0: return False             # sfondo piatto: niente da seguire
        return _ncc_anello(a, b, self.anello) >= 0.80 and _ncc_anello(a, c, self.anello) < 0.60

    def _prevista(self):
        vm = PAR['vel_max']
        return (self.ultimo['ax'] + max(-vm, min(vm, self.vel[0])), self.ultimo['ay'] + max(-vm, min(vm, self.vel[1])))

    def _candidati(self, g, cambio, macchie):
        """(migliore fermo, migliore in movimento): ciascuno (punteggio, ax, ay, modello) o None."""
        fermo = None; mobili = []
        centri = ([self.seme] if self.seme is not None else []) + ([self._prevista()] if self.ultimo is not None else [])
        for centro in centri:
            for punteggio, ax, ay, m in cerca_locale(g, self.modelli, centro):
                if punteggio < PAR['ncc_ferma']: break
                if self._vicino_all_ultimo(ax, ay):          # il cursore sta dove stava: basta la soglia piu' bassa, ma non un crollo rispetto al picco
                    if punteggio >= self._picco() - PAR['calo_ferma'] and (fermo is None or punteggio > fermo[0]): fermo = (punteggio, ax, ay, m)
                elif punteggio >= soglia_di(m, PAR['ncc_locale']) and not self._occupata(ax, ay) and (not PAR['moto_conferma'] or self._mosso(cambio, ax, ay)):
                    mobili.append((punteggio, ax, ay, m))    # un cursore che si sposta deve avere un riscontro netto
        h, w = g.shape; mezzo = self.lato_max // 2 + 12
        for cx, cy, bx, by, bw, bh in macchie:                              # ricerca nelle zone cambiate, a risoluzione piena
            x0 = int(max(0, min(w - 1, round(cx) - mezzo))); y0 = int(max(0, min(h - 1, round(cy) - mezzo)))
            for punteggio, ax, ay, m in candidati(g[y0:y0 + 2 * mezzo, x0:x0 + 2 * mezzo], self.modelli, x0, y0):
                if punteggio < PAR['ncc_ferma']: break
                if self._vicino_all_ultimo(ax, ay):
                    if punteggio >= self._picco() - PAR['calo_ferma'] and (fermo is None or punteggio > fermo[0]): fermo = (punteggio, ax, ay, m)
                elif punteggio >= soglia_di(m, PAR['ncc_globale']) and not self._occupata(ax, ay): mobili.append((punteggio, ax, ay, m))
        return fermo, (max(mobili, key=lambda c: c[0]) if mobili else None)

    def passo(self, t, g):
        h, w = g.shape; prec = self.prec; self.prec = g
        diff = cv2.absdiff(prec, g) if prec is not None else None
        cambio = (diff >= PAR['soglia_diff']).astype(np.uint8) if prec is not None else None
        if diff is not None:
            c_ = celle_cambiate(prec, g, diff).astype(np.float32); c25 = celle_cambiate(prec, g, diff, PAR['soglia_diff']).astype(np.float32); a_ = PAR['occ_alpha']
            self.occ = c_ if self.occ is None else (1.0 - a_) * self.occ + a_ * c_
            self.occ25 = c25 if self.occ25 is None else (1.0 - a_) * self.occ25 + a_ * c25
            if self.ultimo is not None and self._occupata(self.ultimo['ax'], self.ultimo['ay']):
                self.ultimo = None; self.crop = None; self.seme = None; self.prec_trovato = False; self.recenti = []      # un cursore fermo non sta in una zona che cambia di continuo
        mosso = cambio is not None and bool(cambio.any())
        s = {'t': float(t), 'x': None, 'y': None, 'px': None, 'py': None, 'ax': None, 'ay': None, 'forma': '', 'conf': 0.0,
             'fonte': 'persa', 'cambio': 0, 'segue': False, 'ferma': False}
        trovato = None; fonte = ''; macchie = []
        if mosso and cambio.mean() <= PAR['max_zona_cambiata']:
            macchie, _ = macchie_mobili(prec, g)
            if self.ultimo is not None:                                      # solo le piu' vicine all'ultima posizione
                ref = (self.ultimo['ax'], self.ultimo['ay']); macchie = sorted(macchie, key=lambda z: distanza_px(z[:2], ref))
            macchie = macchie[:PAR['max_macchie_ricerca']]
        if self.modelli:
            fermo, mobile = self._candidati(g, cambio, macchie)
            if mobile is not None and (fermo is None or mobile[0] >= fermo[0] - PAR['margine_fermo']): trovato = mobile; fonte = 'locale' if self._in_finestra(mobile) else 'globale'
            elif fermo is not None: trovato = fermo; fonte = 'locale'
        if trovato is None and self.modelli:
            trovato = self._fermo(g); fonte = 'fermo'
        if trovato is None and self.modelli and mosso:
            for c in cerca_globale(g, self.modelli, cambio):
                if c[0] < soglia_di(c[3], PAR['ncc_globale']): continue
                if self._occupata(c[1], c[2]): continue
                if self._vicino_all_ultimo(c[1], c[2]) or self._mosso(cambio, c[1], c[2]): trovato = c; fonte = 'globale'; break
        if trovato is not None:
            punteggio, ax, ay, m = trovato; consecutivo = self.prec_trovato and self.ultimo is not None
            self.vel = (ax - self.ultimo['ax'], ay - self.ultimo['ay']) if consecutivo and fonte != 'globale' else (0.0, 0.0)
            hx, hy = ax + m['hot'][0], ay + m['hot'][1]
            s.update(x=hx / w, y=hy / h, px=hx, py=hy, ax=ax, ay=ay, forma=m['forma'], conf=round(min(1.0, max(0.0, punteggio)), 4), fonte=fonte)
            if consecutivo and cambio is not None:
                d = distanza_px((ax, ay), (self.ultimo['ax'], self.ultimo['ay']))
                if d <= 2.5:
                    s['ferma'] = True
                    R = int(round(PAR['raggio_clic'] * w)); win = cambio[max(0, int(ay) - R):int(ay) + R, max(0, int(ax) - R):int(ax) + R]
                    n = int(win.sum())
                    if 10 <= n <= 0.3 * win.size: s['cambio'] = n               # cambio compatto accanto al cursore fermo
                elif 4.0 <= d <= 150.0 and fonte == 'locale':
                    s['segue'] = self._segue(prec, g, (self.ultimo['ax'], self.ultimo['ay']), (ax, ay))
            self.ultimo = {'ax': ax, 'ay': ay, 'modello': m}; self.seme = None; self.prec_trovato = True
            self.recenti = ([punteggio] if not consecutivo else self.recenti + [punteggio])[-30:]       # un nuovo aggancio riparte dal proprio punteggio
            self.crop = patch_a(g, ax, ay, 32)
        else:
            self.vel = (0.0, 0.0); self.prec_trovato = False; self.seme = None
            if mosso and self.ultimo is not None:                               # ipotesi: la macchia mobile piu' vicina
                if not macchie: macchie, _ = macchie_mobili(prec, g)
                if macchie:
                    ref = (self.ultimo['ax'], self.ultimo['ay'])
                    self.seme = min(macchie, key=lambda z: distanza_px((z[0], z[1]), ref))[:2]; s['fonte'] = 'macchia'
        return s

    def _in_finestra(self, c):
        """Il candidato sta nella finestra locale attorno alla posizione prevista?"""
        if self.ultimo is None: return False
        px, py = self._prevista(); return abs(c[1] - px) <= PAR['finestra'] / 2 and abs(c[2] - py) <= PAR['finestra'] / 2


def insegui(frames, modelli, occupate=None):
    """frames: iterabile di (t, grigio). Ritorna (traccia, (larghezza, altezza) dei fotogrammi usati)."""
    ins = Inseguitore(modelli, occupate); tr = []; dim = (0, 0)
    for t, g in frames:
        dim = (g.shape[1], g.shape[0]); tr.append(ins.passo(t, g))
    return tr, dim


# ---------------------------------------------------------------- eventi
def _evento(tipo, c, durata, forza, stampa='nessuna'):
    x, y = c['x'], c['y']
    return {'id': '', 't': round(float(c['t']), 2), 'durata': round(float(durata), 2), 'canale': 'cursore', 'tipo': tipo,
            'zona': [round(max(0.0, x - .015), 4), round(max(0.0, y - .025), 4), round(min(1.0, x + .015), 4), round(min(1.0, y + .025), 4)],
            'punto': [round(x, 4), round(y, 4)], 'testo': '', 'forza': round(min(1.0, max(0.0, forza)), 4), 'stampa': stampa, 'rif': [],
            'dati': {'forma': c.get('forma', '')}}


def _trovato(c):
    return c['x'] is not None


def _soste(tr):
    """Fermo da 0,5 a 8 s: campioni consecutivi dentro pochi pixel dal primo."""
    out = []; i = 0; n = len(tr)
    while i < n:
        a = tr[i]
        if not _trovato(a): i += 1; continue
        j = i
        while (j + 1 < n and _trovato(tr[j + 1]) and tr[j + 1]['t'] - tr[j]['t'] <= 0.25
               and distanza_px((tr[j + 1]['px'], tr[j + 1]['py']), (a['px'], a['py'])) <= PAR['raggio_sosta_px']): j += 1
        d = tr[j]['t'] - a['t']
        if d >= PAR['sosta_min'] - 1e-9:
            punto = dict(a, x=float(np.median([c['x'] for c in tr[i:j + 1]])), y=float(np.median([c['y'] for c in tr[i:j + 1]])))
            e = _evento('sosta', punto, min(d, PAR['sosta_max']), 0.5 + 0.1 * min(d, 3.0)); e['_fine'] = tr[j]['t']; out.append(e)
        i = j + 1
    return out


def _clic(tr, larghezza):
    """Clic = cursore fermo e cambio compatto accanto a lui (arrivo -> cambio entro 0,6 s: forza piena);
    oppure cambio di forma fra due tratti stabili, vicini nel tempo e nello spazio (<= 0,06 W)."""
    out = []; arrivo = None; ultimo = -9.0
    for i, c in enumerate(tr):
        if c['ferma']:
            if arrivo is None: arrivo = tr[i - 1]['t'] if i else c['t']
            dt = c['t'] - arrivo
            if c['cambio'] > 0 and dt <= PAR['clic_entro'] and c['t'] - ultimo >= 0.8:      # un cambio dopo una lunga attesa non e' un clic
                out.append(_evento('clic', c, 0.1, 0.80 if dt <= 0.6 else 0.60, 'sezione')); ultimo = c['t']
        else:
            arrivo = None
    tratti = []; i = 0
    while i < len(tr):
        if not _trovato(tr[i]): i += 1; continue
        j = i
        while j + 1 < len(tr) and _trovato(tr[j + 1]) and tr[j + 1]['forma'] == tr[i]['forma']: j += 1
        if j > i: tratti.append((i, j))                      # almeno 2 campioni: la forma e' stabile
        i = j + 1
    for (_, a1), (b0, _) in zip(tratti, tratti[1:]):
        A, B = tr[a1], tr[b0]
        if A['forma'] == B['forma'] or B['t'] - A['t'] > 0.3: continue
        if distanza_px((A['px'], A['py']), (B['px'], B['py'])) > PAR['raggio_clic'] * larghezza: continue
        if any(abs(e['t'] - B['t']) <= 0.6 for e in out): continue
        out.append(_evento('clic', B, 0.1, 0.60, 'sezione'))
    return out


def _trascina(tr):
    """Il contenuto attorno segue il cursore per almeno 3 campioni (un buco di tolleranza). Chiude anche a fine traccia."""
    out = []; i = 0; n = len(tr)
    while i < n:
        if not tr[i]['segue']: i += 1; continue
        j = i; k = i + 1
        while k < n and k <= j + 2:
            if tr[k]['segue']: j = k
            k += 1
        if j - i + 1 >= 3: out.append(_evento('trascina', tr[i], tr[j]['t'] - tr[i]['t'], 0.75, 'sezione'))
        i = j + 1
    return out


def _forme(tr, durata):
    """Cursore a testo o a mano per almeno 0,5 s: stampa nessuna (prova di campo o link per altri canali)."""
    out = []; i = 0; n = len(tr)
    while i < n:
        if not _trovato(tr[i]): i += 1; continue
        j = i
        while j + 1 < n and _trovato(tr[j + 1]) and tr[j + 1]['forma'] == tr[i]['forma'] and tr[j + 1]['t'] - tr[j]['t'] <= 0.25: j += 1
        d = tr[j]['t'] - tr[i]['t']
        if tr[i]['forma'] in ('testo', 'mano') and d >= 0.5 - 1e-9: out.append(_evento('forma', tr[i], d, 0.70))
        i = j + 1
    tetto = max(2, int(round(PAR['forme_al_minuto'] * durata / 60.0)))
    out.sort(key=lambda e: (-e['durata'], e['t'])); return out[:tetto]


def estrai_eventi(tr, durata=None, larghezza=1280):
    """Traccia -> eventi (senza testo ne' decisione di stampa: lo fa assegna_nomi)."""
    if durata is None: durata = (tr[-1]['t'] - tr[0]['t']) if len(tr) > 1 else 0.0
    clic = _clic(tr, larghezza)
    def chiusa_da_clic(s):                                  # una sosta chiusa da un clic la dice gia' il clic
        return any(s['t'] - 0.3 <= c['t'] <= s['_fine'] + 0.6 and distanza_px(s['punto'], c['punto']) <= 0.02 for c in clic)
    soste = [s for s in _soste(tr) if not chiusa_da_clic(s)]
    out = sorted(soste + clic + _trascina(tr) + _forme(tr, durata), key=lambda e: (e['t'], e['tipo']))
    for i, e in enumerate(out, 1): e['id'] = 'D%d' % i; e.pop('_fine', None)
    return out


# ---------------------------------------------------------------- nomi (Scena, poi OCR)
class LettoreOCR:
    """OCR rapidocr di un ritaglio 320x60 a risoluzione nativa a t-0,2 s. Carica il motore una volta sola."""

    def __init__(self, video):
        self.video = video; self.motore = None; self.cap = None; self.errore = ''

    def __call__(self, t, punto):
        try:
            if self.cap is None:
                self.cap = cv2.VideoCapture(self.video)
            self.cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t - 0.2) * 1000.0); ok, frame = self.cap.read()
            if not ok: return ''
            h, w = frame.shape[:2]; cx = int(round(punto[0] * w)); cy = int(round(punto[1] * h))
            x0 = max(0, min(w - 320, cx - 160)); y0 = max(0, min(h - 60, cy - 30)); ritaglio = frame[y0:y0 + 60, x0:x0 + 320]
            if self.motore is None:
                from rapidocr_onnxruntime import RapidOCR
                self.motore = RapidOCR(intra_op_num_threads=1, inter_op_num_threads=1, det_limit_type=PAR['ocr_tipo'], det_limit_side_len=PAR['ocr_lato'])   # niente ingrandimento: poca RAM
            righe, _ = self.motore(ritaglio, use_cls=False)
            migliore = None
            for r in righe or []:
                if len(r) < 3 or float(r[2]) < 0.5: continue
                testo = pulisci_ocr(str(r[1]))
                if not utile(testo): continue
                xs = [p[0] for p in r[0]]; ys = [p[1] for p in r[0]]
                dx = max(min(xs) - (cx - x0), 0.0, (cx - x0) - max(xs)); dy = max(min(ys) - (cy - y0), 0.0, (cy - y0) - max(ys)); d = math.hypot(dx, dy)
                if d <= 60 and (migliore is None or d < migliore[0]): migliore = (d, testo)
            return migliore[1][:80] if migliore else ''
        except Exception as exc:
            self.errore = str(exc)[:120]; return ''

    def chiudi(self):
        if self.cap is not None: self.cap.release(); self.cap = None
        self.motore = None


def nome_sotto_il_punto(scena, t, punto, margine=0.012, r=0.03):
    """Testo della Scena che sta DAVVERO sotto il punto all'istante t: una scritta con riquadro che lo contiene (con un margine),
    o senza riquadro a meno di r dal suo punto. Le letture senza posizione (barra di stato, zone dinamiche) non nominano nulla:
    non sanno dov'e'. Fra piu' candidati vince il riquadro piu' piccolo (la scritta piu' specifica), a parita' la piu' sicura."""
    migliore = None
    for t0, t1, testo, vp, zona, forza in scena.voci:
        if not (t0 <= t <= t1) or vp is None: continue
        if zona:
            if not (zona[0] - margine <= punto[0] <= zona[2] + margine and zona[1] - 1.8 * margine <= punto[1] <= zona[3] + 1.8 * margine): continue
            area = max(1e-6, (zona[2] - zona[0]) * (zona[3] - zona[1]))
        else:
            if math.hypot(punto[0] - vp[0], punto[1] - vp[1]) > r: continue
            area = 1.0
        chiave = (round(float(forza), 1), -area)
        if migliore is None or chiave > migliore[0]: migliore = (chiave, testo)
    return migliore[1] if migliore else ''


def assegna_nomi(trovati, scena, leggi, durata):
    """Nome dell'oggetto sotto il punto: testo della Scena a t-0,3; se manca, OCR (a budget, dove serve a riempire la quota).
    Si stampano solo clic, trascina e soste CON nome, al massimo `stampa_al_minuto` per ogni minuto di video
    (i piu' forti; a parita' di forza i piu' vicini all'inizio del minuto). Clic, trascina e soste senza nome si scartano:
    la fusione li nominerebbe con testi senza posizione (barra di stato) e ne farebbe agganci deittici sbagliati.
    Ritorna (eventi tenuti, letture OCR fatte)."""
    letture = 0; tenuti = [e for e in trovati if e['tipo'] == 'forma']; minuti = {}
    for e in trovati:
        if e['tipo'] in ('clic', 'trascina', 'sosta'):
            e['testo'] = nome_sotto_il_punto(scena, max(0.0, e['t'] - 0.3), e['punto'])[:80]; e['stampa'] = 'nessuna'
            minuti.setdefault(int(e['t'] // 60), []).append(e)
    for k, gruppo in sorted(minuti.items()):
        resto = max(1.0, min(60.0, durata - 60.0 * k)); quota = max(3, int(round(PAR['stampa_al_minuto'] * resto / 60.0)))
        ordinati = sorted(gruppo, key=lambda e: (-e['forza'], e['t'])); stampati = 0
        for e in ordinati:                                              # prima chi ha gia' un nome dalla Scena
            if e['testo'] and stampati < quota: e['stampa'] = 'sezione'; stampati += 1
        for e in ordinati:                                              # poi l'OCR, solo per riempire la quota
            if stampati >= quota or letture >= PAR['letture_max'] or leggi is None: break
            if not e['testo']:
                letture += 1; e['testo'] = leggi(e['t'], e['punto'])
                if e['testo']: e['stampa'] = 'sezione'; stampati += 1
        tenuti += [e for e in ordinati if e['stampa'] == 'sezione']       # senza nome non si emette: la fusione lo nominerebbe con testi senza posizione
    return sorted(tenuti, key=lambda e: (e['t'], e['id'])), letture


# ---------------------------------------------------------------- documento e file
def documento(trovati, tr, secondi, modelli=0, durata_video=0.0):
    fonti = {}
    for c in tr: fonti[c['fonte']] = fonti.get(c['fonte'], 0) + 1
    trovati_n = sum(1 for c in tr if _trovato(c))
    stat = {'copertura': round(trovati_n / float(max(1, len(tr))), 4), 'secondi': round(float(secondi), 3), 'campioni': len(tr), 'modelli': int(modelli),
            'locale': fonti.get('locale', 0), 'fermo': fonti.get('fermo', 0), 'globale': fonti.get('globale', 0), 'macchia': fonti.get('macchia', 0), 'persa': fonti.get('persa', 0)}
    return {'schema': 1, 'canale': 'cursore', 'lettera': 'D', 'versione': VERSIONE, 'sezione': 'CURSORE',
            'legenda': "CURSORE = clic/sosta/trascinamento sull'oggetto sotto il puntatore",
            'formati': {'clic': 'clic {ogg}', 'sosta': 'su {ogg}', 'trascina': 'trascina {ogg}', 'forma': '{forma}'},
            'stat': stat, 'eventi': trovati}


def scrivi_traccia(lavoro, tr):
    """cursore.json: [t, x, y, forma, conf] a 10 fps in frazioni (x e y null dove il cursore non e' stato trovato)."""
    dst = os.path.join(lavoro, 'cursore.json'); tmp = dst + '.tmp'
    dati = [[round(c['t'], 2), None if c['x'] is None else round(c['x'], 4), None if c['y'] is None else round(c['y'], 4), c['forma'], round(c['conf'], 4)] for c in tr]
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(dati, f, ensure_ascii=False, separators=(',', ':')); f.write('\n')
    os.replace(tmp, dst)


def esegui(video, lavoro, parametri=None):
    avvio = time.perf_counter(); imposta(parametri); os.makedirs(lavoro, exist_ok=True)
    _, _, _, durata_video = info_video(video)
    modelli, occupate = impara_da_video(video, durata_video)
    if modelli and PAR['calibra']: calibra_soglie(campiona_fotogrammi(video, PAR['calibra_fotogrammi'], durata_video), modelli)
    t_impara = time.perf_counter() - avvio; ram_impara = rss_mb(); t_insegui = t_nomi = 0.0; ram_insegui = ram_impara; letture = 0
    if not modelli:
        tr = []; trovati = []
        print('CURSORE nessun modello automatico del cursore trovato nei primi %d s' % int(PAR['addestra_s'] * PAR['addestra_finestre']), flush=True)
    else:
        t1 = time.perf_counter()
        tr, (larghezza, _) = insegui(fotogrammi(video), modelli, occupate)
        t_insegui = time.perf_counter() - t1; ram_insegui = rss_mb()
        durata = tr[-1]['t'] if tr else 0.0
        trovati = estrai_eventi(tr, durata, larghezza)
        t2 = time.perf_counter(); lettore = LettoreOCR(video)
        try: trovati, letture = assegna_nomi(trovati, eventi.Scena(lavoro), lettore, durata)
        finally: lettore.chiudi()
        t_nomi = time.perf_counter() - t2
        trovati.sort(key=lambda e: (e['t'], e['id']))
    doc = documento(trovati, tr, time.perf_counter() - avvio, len(modelli), durata_video)
    doc['stat'].update(secondi_apprendimento=round(t_impara, 1), secondi_inseguimento=round(t_insegui, 1), secondi_nomi=round(t_nomi, 1), letture_ocr=letture,
                       ram_apprendimento_mb=round(ram_impara), ram_inseguimento_mb=round(ram_insegui), ram_picco_mb=round(rss_mb()),
                       zone_occupate=round(float(occupate.mean()), 4) if occupate is not None else 0.0)
    eventi.scrivi(os.path.join(lavoro, 'eventi_cursore.json'), doc)
    if tr: scrivi_traccia(lavoro, tr)
    tipi = {}
    for e in trovati: tipi[e['tipo']] = tipi.get(e['tipo'], 0) + 1
    st = doc['stat']
    print('CURSORE copertura=%.4f tempo_s=%.1f (apprendimento %.1f, inseguimento %.1f, nomi %.1f con %d letture OCR) ram_picco_mb=%.0f (dopo apprendimento %.0f, dopo inseguimento %.0f) campioni=%d modelli=%s zone_occupate=%.1f%% fonti=locale %d/fermo %d/globale %d/macchia %d/persa %d eventi=%s' % (
        st['copertura'], st['secondi'], t_impara, t_insegui, t_nomi, letture, st['ram_picco_mb'], ram_impara, ram_insegui, st['campioni'],
        [(m['forma'], m['img'].shape[0], m['n'], round(m.get('soglia', 0), 2)) for m in modelli], 100.0 * st['zone_occupate'], st['locale'], st['fermo'], st['globale'], st['macchia'], st['persa'], tipi), flush=True)
    return {'doc': doc, 'traccia': tr, 'modelli': modelli}


def main():
    p = argparse.ArgumentParser(description='Canale D1 cursore: video + cartella _lavoro -> eventi_cursore.json')
    p.add_argument('video'); p.add_argument('lavoro'); p.add_argument('--par', default='', help='prove di taratura: nome=valore,nome=valore')
    a = p.parse_args()
    par = dict(x.split('=', 1) for x in a.par.split(',') if '=' in x)
    esegui(os.path.abspath(a.video), os.path.abspath(a.lavoro), par)


if __name__ == '__main__':
    main()
