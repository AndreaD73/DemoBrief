# -*- coding: utf-8 -*-
"""Movement analysis of a screen recording: cursor tracking, activity phases, clicks.

Usage: movimento.py <video> [outdir]. Pure computation (opencv + numpy), no models.
Reads the video (ffmpeg decoding, cv2 as fallback) at about 10 frames per second, downscaled to
640 px wide. Finds the mouse cursor by template matching, classifies each frame step (still,
jump, interface change, view navigation, geometry edit), merges steps into events, detects
clicks and composes compact phase lines.
Writes <outdir>/mov.json (phases, cursor track, size) and <outdir>/eventi.txt (one line per phase).
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from collections import Counter

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import comune
import tempi

FPS = 10
W = 640
# Screen zones as (name, left, top, right, bottom) in fractions of the frame.
ZONE = [("barra-superiore", 0, 0, 1, .06), ("timeline-inferiore", 0, .90, .79, 1),
        ("pannello-destro", .79, .06, 1, 1), ("barra-strumenti-sx", 0, .06, .06, .90),
        ("viewport-sinistra", .06, .06, .36, .90), ("viewport-centro", .36, .06, .66, .90),
        ("viewport-destra", .66, .06, .79, .90)]
UI = ("pannello-destro", "barra-superiore", "timeline-inferiore", "barra-strumenti-sx")
SHORT = {"viewport-sinistra": "viewport sx", "viewport-centro": "viewport centro",
         "viewport-destra": "viewport dx", "pannello-destro": "pannello dx",
         "barra-superiore": "barra sup", "timeline-inferiore": "timeline",
         "barra-strumenti-sx": "barra strumenti"}
# Radius (pixels at width W) of the disc blanked around the cursor in the difference masks.
CUR_R = 16
DEBUG = os.environ.get("MOV_DEBUG")
FASI = []
# Grey-level difference between consecutive frames that counts as a changed pixel.
SOGLIA_DIFF = 18
# Cursor positions are bucketed in cells of this many pixels when looking for static icons.
CELLA_POSIZIONE = 4
# Mean energy (grey-level difference) above which a segment is rated high / medium intensity.
ENERGIA_ALTA = 6
ENERGIA_MEDIA = 2
# Mean changed area (percent of the frame) above which an isolated change counts as instant.
AREA_ISTANTANEA = 0.6
# Seconds: a click needs the cursor to rest this long (the tiny epsilon absorbs rounding).
SOSTA_CLIC = 0.3


def zona_di(x, y, w, h):
    """Name of the screen zone containing the point (x, y) of a frame of size w x h.

    Falls back to the central viewport when the point lies in no zone.
    """
    fx, fy = x / w, y / h
    for nome, sinistra, alto, destra, basso in ZONE:
        if sinistra <= fx < destra and alto <= fy < basso:
            return nome
    return "viewport-centro"


def tf(secondi):
    """Format a time in seconds as mm:ss."""
    return "%02d:%02d" % (int(secondi // 60), int(secondi % 60))


def tf1(secondi):
    """Format a time in seconds as mm:ss.s (one decimal)."""
    return "%02d:%04.1f" % (int(secondi // 60), secondi % 60)


def _parametri_video(video):
    """Read frame rate, frame count and size of `video` with cv2.

    Returns (fps, step, expected samples, width, height), where `step` keeps one frame every
    round(fps / FPS) and the expected count is the number of frames that sampling yields.
    """
    cap = cv2.VideoCapture(video)
    fps = cap.get(cv2.CAP_PROP_FPS)
    numero = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    larghezza = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    altezza = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    step = max(1, round(fps / FPS))
    attesi = (numero + step - 1) // step
    return fps, step, attesi, larghezza, altezza


def _riduci(fotogramma):
    """Downscale a BGR frame to width W (area filter) and return (colour image, grey image)."""
    h = int(fotogramma.shape[0] * W / fotogramma.shape[1])
    colore = cv2.resize(fotogramma, (W, h), interpolation=cv2.INTER_AREA)
    grigio = cv2.cvtColor(colore, cv2.COLOR_BGR2GRAY)
    return colore, grigio


def _load_cv2(video, step=None, fps=None, motivo=None):
    """Decode the video with cv2, keeping one frame every `step`.

    `step` and `fps` are measured from the file when omitted; `motivo` (why this fallback is
    used) is printed when given. Returns ([first colour frame], grey frames, seconds per sample).
    """
    cap = cv2.VideoCapture(video)
    if fps is None:
        fps = cap.get(cv2.CAP_PROP_FPS)
    if step is None:
        step = max(1, round(fps / FPS))
    primo_colore, gray = None, []
    i = 0
    while True:
        if not cap.grab():
            break
        if i % step == 0:
            ok, fotogramma = cap.retrieve()
            if not ok:
                break
            colore, grigio = _riduci(fotogramma)
            if primo_colore is None:
                primo_colore = colore
            gray.append(grigio)
        i += 1
    cap.release()
    if motivo is not None:
        print("decodifica cv2 (%s)" % motivo, flush=True)
    color = [] if primo_colore is None else [primo_colore]
    return color, gray, step / fps


def _load_ffmpeg(video, step, fps, attesi, larghezza, altezza):
    """Decode the video through an ffmpeg pipe (raw BGR), keeping one frame every `step`.

    The BT.601 matrix is forced so grey levels match cv2. Raises RuntimeError on a truncated
    frame, a non-zero ffmpeg exit code, unknown frame size, or when the frame count differs from
    `attesi`. The ffmpeg messages go to a temporary file, so a full stderr pipe can never stall
    the decoding. Returns ([first colour frame], grey frames, seconds per sample).
    """
    dimensione = larghezza * altezza * 3
    if dimensione <= 0:
        raise RuntimeError("dimensioni del video sconosciute: %dx%d" % (larghezza, altezza))
    filtro = "select=not(mod(n\\,%d)),scale=in_color_matrix=bt601:out_color_matrix=bt601" % step
    comando = [
        comune.ffmpeg(), "-v", "error", "-i", video,
        "-vf", filtro, "-fps_mode", "passthrough",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-"
    ]
    registro_errori = tempfile.TemporaryFile()
    processo = subprocess.Popen(
        comando,
        stdout=subprocess.PIPE,
        stderr=registro_errori,
        stdin=subprocess.DEVNULL,
    )
    primo_colore, gray = None, []
    try:
        while True:
            blocco = processo.stdout.read(dimensione)
            if not blocco:
                break
            if len(blocco) != dimensione:
                raise RuntimeError(
                    "fotogramma incompleto: %d byte invece di %d"
                    % (len(blocco), dimensione)
                )
            fotogramma = np.frombuffer(blocco, dtype=np.uint8).reshape(
                altezza, larghezza, 3
            )
            colore, grigio = _riduci(fotogramma)
            if primo_colore is None:
                primo_colore = colore
            gray.append(grigio)
        codice = processo.wait()
        registro_errori.seek(0)
        errore = registro_errori.read().decode("utf-8", errors="replace").strip()
    except BaseException:
        processo.kill()
        processo.wait()
        raise
    finally:
        registro_errori.close()
    if codice != 0:
        raise RuntimeError(
            "ffmpeg terminato con codice %d%s"
            % (codice, ": " + errore if errore else "")
        )
    if len(gray) != attesi:
        raise RuntimeError(
            "ricevuti %d fotogrammi invece di %d" % (len(gray), attesi)
        )
    print("decodifica ffmpeg", flush=True)
    color = [] if primo_colore is None else [primo_colore]
    return color, gray, step / fps


def load(video):
    """Load the downscaled frames of `video`: ffmpeg first, cv2 when ffmpeg fails or is disabled.

    Config key 'decodifica_ffmpeg' turns ffmpeg decoding off. Returns
    ([first colour frame], grey frames, seconds per sample).
    """
    fps, step, attesi, larghezza, altezza = _parametri_video(video)
    if not comune.config().get("decodifica_ffmpeg", True):
        return _load_cv2(
            video, step, fps, "disattivata da configurazione"
        )
    try:
        return _load_ffmpeg(
            video, step, fps, attesi, larghezza, altezza
        )
    except (Exception, SystemExit) as errore:
        return _load_cv2(video, step, fps, str(errore))


# ---------- cursor: template matching ----------
def bin_white(grigio):
    """Binarise a grey image: 1.0 where the pixel is white enough (>= 205), else 0.0 (float32)."""
    return (grigio >= 205).astype(np.float32)


def derive_template(gray):
    """Learn the pointer shape from the frames where the only moving blob is a white arrow.

    Collects candidate blobs (small, white with a black edge) from frame differences, drops
    those that keep reappearing at the same place (fixed icons), then picks the candidate whose
    white mask matches the most other candidates. Returns (template or None, number of
    candidates, best score).
    """
    cands = []
    for k in range(1, len(gray)):
        diff = (cv2.absdiff(gray[k], gray[k - 1]) > SOGLIA_DIFF).astype(np.uint8)
        if diff.sum() < 5 or diff.mean() * 100 > 0.4:
            continue
        with tempi.blocco('componenti'):
            numero, etichette, statistiche, centroidi = cv2.connectedComponentsWithStats(
                cv2.dilate(diff, np.ones((3, 3), np.uint8)))
        for j in range(1, numero):
            x, y, w, h, area = statistiche[j]
            if 6 <= w <= 14 and 8 <= h <= 18 and x > 1 and y > 1:
                ritaglio = gray[k][y:y + h, x:x + w]
                bianchi = int((ritaglio >= 215).sum())
                neri = int((ritaglio < 55).sum())
                if bianchi >= 10 and neri >= 4:
                    cands.append((k, x, y, w, h, bianchi))
        if len(cands) >= 60:
            break
    # General rule: a pointer moves; candidates that keep reappearing at the same spot are
    # fixed icons/overlays.
    ripetizioni = Counter((cand[1] // CELLA_POSIZIONE, cand[2] // CELLA_POSIZIONE) for cand in cands)
    cands = [cand for cand in cands
             if ripetizioni[(cand[1] // CELLA_POSIZIONE, cand[2] // CELLA_POSIZIONE)] <= 2][:12]
    migliore, punteggio_migliore = None, -1
    for (k, x, y, w, h, bianchi) in cands:
        modello = bin_white(gray[k][y - 1:y + h + 1, x - 1:x + w + 1])
        if modello.shape[0] < 6 or modello.sum() < 8:
            continue
        punteggio = 0
        for (k2, x2, y2, w2, h2, _) in cands:
            regione = bin_white(gray[k2][max(0, y2 - 20):y2 + h2 + 20, max(0, x2 - 20):x2 + w2 + 20])
            if regione.shape[0] < modello.shape[0] or regione.shape[1] < modello.shape[1]:
                continue
            with tempi.blocco('cursore_template'):
                risposta = cv2.matchTemplate(regione, modello, cv2.TM_CCOEFF_NORMED)
            if risposta.max() > 0.75:
                punteggio += 1
        if punteggio > punteggio_migliore:
            punteggio_migliore, migliore = punteggio, modello
    return migliore, len(cands), punteggio_migliore


def track_cursor(gray, tpl, thr=0.72):
    """Find the cursor in every grey frame by template matching.

    Up to five peaks above `thr` are taken per frame. Positions that appear in at least 15% of
    the frames (in a 4 px grid, neighbours included) are static interface icons (play button,
    etc.) and are discarded. Returns one (x, y, score) per frame, or None when no cursor is found.
    """
    peaks = []
    for grigio in gray:
        with tempi.blocco('cursore_template'):
            risposta = cv2.matchTemplate(bin_white(grigio), tpl, cv2.TM_CCOEFF_NORMED)
        picchi_fotogramma = []
        for _ in range(5):
            _, massimo, _, loc = cv2.minMaxLoc(risposta)
            if massimo < thr:
                break
            picchi_fotogramma.append((loc[0] + 1.0, loc[1] + 1.0, float(massimo)))
            risposta[max(0, loc[1] - 8):loc[1] + 9, max(0, loc[0] - 8):loc[0] + 9] = -1
        peaks.append(picchi_fotogramma)
    conteggi = {}
    for picchi_fotogramma in peaks:
        for x, y, _ in picchi_fotogramma:
            chiave = (int(x // CELLA_POSIZIONE), int(y // CELLA_POSIZIONE))
            conteggi[chiave] = conteggi.get(chiave, 0) + 1
    statiche = {chiave for chiave, volte in conteggi.items() if volte >= 0.15 * len(gray)}
    vicine = set()
    for cella_x, cella_y in list(statiche):
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                vicine.add((cella_x + dx, cella_y + dy))
    statiche |= vicine
    posizioni = []
    for picchi_fotogramma in peaks:
        validi = [picco for picco in picchi_fotogramma
                  if (int(picco[0] // CELLA_POSIZIONE), int(picco[1] // CELLA_POSIZIONE)) not in statiche]
        posizioni.append(max(validi, key=lambda picco: picco[2]) if validi else None)
    return posizioni


# ---------- optical flow ----------
def flow_stats(prima, dopo):
    """Dense optical-flow statistics between two small grey frames.

    Returns a dict: fmov (fraction of moving pixels), R (direction coherence, 1 = all pixels
    move the same way), mmag (median magnitude), ext (relative area spanned by the moving
    pixels), med (median motion vector), div (divergence) and curl (rotation around the centre).
    With fewer than 15 moving pixels the direction measures are zero.
    """
    with tempi.blocco('flusso_ottico'):
        flusso = cv2.calcOpticalFlowFarneback(prima, dopo, None, .5, 2, 13, 2, 5, 1.1, 0)
    mag = np.linalg.norm(flusso, axis=2)
    in_movimento = mag > 0.3
    altezza, larghezza = mag.shape
    fmov = float(in_movimento.mean())
    if in_movimento.sum() < 15:
        return dict(fmov=fmov, R=0, mmag=0, ext=0, med=(0, 0), div=0, curl=0)
    vx, vy = flusso[..., 0][in_movimento], flusso[..., 1][in_movimento]
    angoli = np.arctan2(vy, vx)
    coerenza = float(np.hypot(np.cos(angoli).mean(), np.sin(angoli).mean()))
    righe_mov, colonne_mov = np.nonzero(in_movimento)
    estensione = ((np.percentile(colonne_mov, 95) - np.percentile(colonne_mov, 5))
                  * (np.percentile(righe_mov, 95) - np.percentile(righe_mov, 5))
                  / (larghezza * altezza))
    cx, cy = colonne_mov - larghezza / 2, righe_mov - altezza / 2
    raggio = np.sqrt(cx ** 2 + cy ** 2) + 1e-6
    div = float(((vx * cx + vy * cy) / raggio).mean())
    curl = float(((-vx * cy + vy * cx) / raggio).mean())
    return dict(fmov=fmov, R=coerenza, mmag=float(np.median(mag[in_movimento])), ext=float(estensione),
                med=(float(np.median(vx)), float(np.median(vy))), div=div, curl=curl)


def analizza(video):
    """Measure every step between consecutive sampled frames of `video`.

    For each step computes the changed-area percentages (whole frame and viewport, with the
    cursor disc, the status bar and the screencast overlay masked out), the mean energy, the
    largest changed component with its screen zone, and the optical-flow statistics.
    Returns (colour frames, grey frames, rows, cursor track, (candidate count, confidence,
    template shape), seconds per sample, start time).
    """
    avvio = time.time()
    color, gray, dt = load(video)
    numero = len(gray)
    H = gray[0].shape[0]
    vx0, vy0, vx1, vy1 = int(.06 * W), int(.06 * H), int(.79 * W), int(.90 * H)
    tpl, ncand, conf = derive_template(gray)
    cur = track_cursor(gray, tpl) if tpl is not None else [None] * numero

    def disco_cursore(maschera, k):
        """Blank a disc of radius CUR_R around the cursor of frame `k` in `maschera`, if found."""
        posizione = cur[k]
        if posizione is not None:
            cv2.circle(maschera, (int(posizione[0] + 4), int(posizione[1] + 6)), CUR_R, 0, -1)

    blur = [cv2.GaussianBlur(grigio, (3, 3), 0) for grigio in gray]
    small = [cv2.resize(grigio[vy0:vy1, vx0:vx1], (160, int(160 * (vy1 - vy0) / (vx1 - vx0))))
             for grigio in gray]
    rows = []
    K7 = np.ones((7, 7), np.uint8)
    for k in range(1, numero):
        diff = cv2.absdiff(blur[k], blur[k - 1])
        maschera = (diff > SOGLIA_DIFF).astype(np.uint8)
        disco_cursore(maschera, k - 1)
        disco_cursore(maschera, k)
        # Status bar (key hints).
        maschera[int(.945 * H):, :] = 0
        # Screencast overlay (keys / mouse).
        maschera[int(.68 * H):int(.90 * H), int(.62 * W):int(.78 * W)] = 0
        area = maschera.mean() * 100
        varea = maschera[vy0:vy1, vx0:vx1].mean() * 100
        energy = float(diff.mean())
        zone, comp, cpx = None, None, 0
        if maschera.sum() > 12:
            with tempi.blocco('componenti'):
                nc, etichette, statistiche, centroidi = cv2.connectedComponentsWithStats(
                    cv2.dilate(maschera, K7))
            if nc > 1:
                conteggi = np.bincount(etichette[maschera > 0], minlength=nc)
                conteggi[0] = 0
                j = int(conteggi.argmax())
                cpx = int(conteggi[j])
                x, y, larg, alt, _ = statistiche[j]
                zone = zona_di(centroidi[j][0], centroidi[j][1], W, H)
                comp = (int(x), int(y), int(x + larg), int(y + alt),
                        (float(centroidi[j][0]), float(centroidi[j][1])))
        flusso = flow_stats(small[k - 1], small[k])
        rows.append(dict(t=(k - 1) * dt, k=k, area=area, varea=varea, energy=energy, zone=zone, comp=comp,
                         cpx=cpx, cpct=100.0 * cpx / (W * H), **flusso))
    return color, gray, rows, cur, (ncand, conf, None if tpl is None else tpl.shape), dt, avvio


def classifica(rows):
    """Label every row in place with a class 'c' and, for view changes, a sub-kind 'sub'.

    Classes: salto (changed area above 60%), fermo (below 0.25%), ui (largest change in an
    interface zone), vista (coherent wide motion: view navigation) or mod (geometry edit).
    A 'vista' row becomes 'pan' when the flow is coherent and fast, otherwise 'orbita/zoom'.
    """
    for riga in rows:
        area = riga['area']
        if area > 60:
            classe = "salto"
        elif area < 0.25:
            classe = "fermo"
        elif riga['zone'] in UI:
            classe = "ui"
        elif riga['fmov'] >= 0.15 and riga['ext'] >= 0.30 and riga['mmag'] > 0.3:
            classe = "vista"
        else:
            classe = "mod"
        riga['c'] = classe
    for riga in rows:
        if riga['c'] == "vista":
            mx, my = riga['med']
            if np.hypot(mx, my) > 0.7 and riga['R'] > 0.8:
                riga['sub'] = "pan"
            else:
                riga['sub'] = "orbita/zoom"


def _unione_scatole(scatole):
    """Smallest box [x0, y0, x1, y1] containing all `scatole` (same format), or None if empty."""
    if not scatole:
        return None
    return [min(riquadro[0] for riquadro in scatole), min(riquadro[1] for riquadro in scatole),
            max(riquadro[2] for riquadro in scatole), max(riquadro[3] for riquadro in scatole)]


def _isolato(rows, inizio, fine):
    """True when the rows just before `inizio` and just after `fine` are both 'fermo' (still)."""
    return rows[max(0, inizio - 1)]['c'] == "fermo" and rows[min(len(rows) - 1, fine + 1)]['c'] == "fermo"


def eventi(rows, cur, dt, H=360):
    """Merge classified rows into events and detect cursor clicks.

    Consecutive rows of the same class form a segment (a gap of up to two rows of 'fermo' is
    bridged). Each segment becomes an event dict (t0, t1, kind, zone, inten, bbox...) or is
    dropped when it is too weak. A click is a cursor rest of at least 0.3 s followed within
    0.5 s by a screen change near it (or in an interface zone); clicks closer than 0.4 s are
    merged. `H` is the frame height used to find the cursor zone. Returns (events, clicks).
    """
    numero_cursori = len(cur)
    segmenti = []
    k = 0
    while k < len(rows):
        classe = rows[k]['c']
        if classe == "fermo":
            k += 1
            continue
        j = k
        while j + 1 < len(rows):
            if rows[j + 1]['c'] == classe:
                j += 1
                continue
            prossimi = [q for q in range(j + 2, min(len(rows), j + 4)) if rows[q]['c'] == classe]
            if prossimi and all(rows[q]['c'] in ("fermo", classe) for q in range(j + 1, prossimi[0])):
                j = prossimi[0]
                continue
            break
        segmenti.append((k, j, classe))
        k = j + 1
    eventi_rilevati = []
    for k, j, classe in segmenti:
        segmento = rows[k:j + 1]
        inizio, fine = segmento[0]['t'], segmento[-1]['t'] + dt
        energia_media = float(np.mean([riga['energy'] for riga in segmento]))
        if energia_media > ENERGIA_ALTA:
            intensita = "alta"
        elif energia_media > ENERGIA_MEDIA:
            intensita = "media"
        else:
            intensita = "bassa"
        scatola = _unione_scatole([riga['comp'][:4] for riga in segmento if riga['comp']])
        zone_segmento = [riga['zone'] for riga in segmento if riga['zone']]
        zone = max(set(zone_segmento), key=zone_segmento.count) if zone_segmento else "viewport-centro"
        area_media = float(np.mean([riga['area'] for riga in segmento]))
        if classe == "salto":
            eventi_rilevati.append(dict(t0=inizio, t1=fine, kind="salto", zone="viewport",
                                        inten=intensita, bbox=scatola))
        elif classe == "vista":
            if fine - inizio < 0.3:
                isolato = _isolato(rows, k, j)
                if isolato and area_media > AREA_ISTANTANEA:
                    # 1-2 isolated frames: an instant change, not a view movement.
                    eventi_rilevati.append(dict(t0=inizio, t1=fine, kind="mod-istantaneo", zone=zone,
                                                inten=intensita, bbox=scatola))
                continue
            sotto_tipi = [riga['sub'] for riga in segmento if 'sub' in riga]
            eventi_rilevati.append(dict(t0=inizio, t1=fine, kind="vista",
                                        sub=max(set(sotto_tipi), key=sotto_tipi.count),
                                        zone="viewport", inten=intensita))
        elif classe == "ui":
            if len(segmento) <= 2 and area_media > 0.4:
                eventi_rilevati.append(dict(t0=inizio, t1=fine, kind="ui-istantaneo", zone=zone,
                                            inten=intensita))
            elif area_media >= 0.6:
                eventi_rilevati.append(dict(t0=inizio, t1=fine, kind="ui-graduale", zone=zone,
                                            inten=intensita))
        else:
            isolato = _isolato(rows, k, j)
            if len(segmento) <= 2 and area_media > AREA_ISTANTANEA and isolato:
                eventi_rilevati.append(dict(t0=inizio, t1=fine, kind="mod-istantaneo", zone=zone,
                                            inten=intensita, bbox=scatola))
            elif len(segmento) >= 3 and area_media >= 0.9:
                eventi_rilevati.append(dict(t0=inizio, t1=fine, kind="mod", zone=zone,
                                            inten=intensita, bbox=scatola))
    clicks = []
    indice = 1
    while indice < numero_cursori:
        if cur[indice] is None:
            indice += 1
            continue
        inizio_sosta = indice
        while (indice + 1 < numero_cursori and cur[indice + 1] is not None
               and np.hypot(cur[indice + 1][0] - cur[inizio_sosta][0],
                            cur[indice + 1][1] - cur[inizio_sosta][1]) < 4):
            indice += 1
        if (indice - inizio_sosta + 1) * dt >= SOSTA_CLIC - 1e-9:
            istante = indice * dt
            punto = cur[inizio_sosta]
            effetto = None
            for q in range(indice, min(len(rows), indice + 1 + int(round(0.5 / dt)))):
                riga = rows[q]
                if riga['c'] in ("fermo", "vista", "salto") or riga['comp'] is None or riga['area'] < 0.3:
                    continue
                cx, cy = riga['comp'][4]
                distanza = float(np.hypot(cx - punto[0], cy - punto[1]))
                zona_punto = zona_di(punto[0], punto[1], W, H)
                if (distanza < 60 or (riga['zone'] in UI and zona_punto != riga['zone'])
                        or (riga['zone'] in UI and distanza < 120)):
                    effetto = (riga, distanza)
                    break
            if effetto:
                clicks.append(dict(t=istante, x=int(punto[0]), y=int(punto[1]),
                                   zone=zona_di(punto[0], punto[1], W, H),
                                   eff=effetto[0]['zone'], near=effetto[1] < 60, area=effetto[0]['area']))
        indice += 1
    clic_filtrati = []
    for clic in clicks:
        if clic_filtrati and clic['t'] - clic_filtrati[-1]['t'] < 0.4:
            continue
        clic_filtrati.append(clic)
    return eventi_rilevati, clic_filtrati


def rng(inizio, fine):
    """Format the time span [inizio, fine] as '[mm:ss-mm:ss]'.

    Spans of 2 s or more use whole seconds, spans of 0.3 s or more use tenths, shorter ones a
    single instant '[mm:ss.s]'.
    """
    if fine - inizio >= 2.0:
        return "[%s-%s]" % (tf(inizio), tf(fine))
    if fine - inizio >= 0.3:
        return "[%s-%s]" % (tf1(inizio), tf1(fine))
    return "[%s]" % tf1(inizio)


def componi(evs, clicks):
    """Turn events and clicks into compact text lines, filling the global FASI list as well.

    Output lines cover view cuts (jumps closer than 0.7 s merged), interface changes grouped by
    zone (gaps up to 3 s), viewport phases (gaps up to 2 s) with their clicks, and the clicks
    left unassigned. Nothing is lost: no click and no instant change is dropped. FASI receives,
    for each viewport phase, its times, edit counts, bounding box and clicks. Returns the lines
    sorted by time.
    """
    out = []
    # Jumps: clusters within 0.7 s.
    salti = [evento for evento in evs if evento['kind'] == "salto"]
    cluster_salti = []
    for evento in salti:
        if cluster_salti and evento['t0'] - cluster_salti[-1][1] <= 0.7:
            cluster_salti[-1][1] = evento['t1']
            cluster_salti[-1][2] += 1
        else:
            cluster_salti.append([evento['t0'], evento['t1'], 1])
    for inizio, fine, numero in cluster_salti:
        riga_salto = "%s taglio/salto di vista (area >60%% in %d fotogrammi)" % (rng(inizio, fine), numero)
        out.append((inizio, riga_salto))
    # Panels / interface.
    eventi_ui = [evento for evento in evs if evento['kind'].startswith("ui")]
    gruppi = []
    for evento in eventi_ui:
        if gruppi and gruppi[-1]['zone'] == evento['zone'] and evento['t0'] - gruppi[-1]['t1'] <= 3.0:
            gruppi[-1]['ev'].append(evento)
            gruppi[-1]['t1'] = evento['t1']
        else:
            gruppi.append(dict(zone=evento['zone'], t0=evento['t0'], t1=evento['t1'], ev=[evento]))
    used_clicks = set()
    for gruppo in gruppi:
        n_istantanei = sum(1 for evento in gruppo['ev'] if evento['kind'] == "ui-istantaneo")
        n_graduali = len(gruppo['ev']) - n_istantanei
        descrizioni = []
        if n_istantanei:
            formato = "%d cambio istantaneo" if n_istantanei == 1 else "%d cambi istantanei"
            descrizioni.append(formato % n_istantanei + " (menu/pannello aperto-chiuso)")
        if n_graduali:
            formato = "%d graduale" if n_graduali == 1 else "%d graduali"
            descrizioni.append(formato % n_graduali)
        testo = "%s %s: %s" % (rng(gruppo['t0'], gruppo['t1']), SHORT.get(gruppo['zone'], gruppo['zone']),
                               ", ".join(descrizioni))
        for i, clic in enumerate(clicks):
            if (gruppo['t0'] - 0.8 <= clic['t'] <= gruppo['t1']
                    and (clic['eff'] == gruppo['zone'] or clic['zone'] == gruppo['zone'])):
                testo += "; clic %s (%d,%d)->%s" % (tf1(clic['t']), clic['x'], clic['y'],
                                                    SHORT.get(clic['eff'], clic['eff']))
                used_clicks.add(i)
        out.append((gruppo['t0'], testo))
    # Phases in the viewport.
    eventi_viewport = [evento for evento in evs if evento['kind'] in ("vista", "mod", "mod-istantaneo")]
    fasi_viewport = []
    for evento in eventi_viewport:
        if fasi_viewport and evento['t0'] - fasi_viewport[-1]['t1'] <= 2.0:
            fasi_viewport[-1]['ev'].append(evento)
            fasi_viewport[-1]['t1'] = max(fasi_viewport[-1]['t1'], evento['t1'])
        else:
            fasi_viewport.append(dict(t0=evento['t0'], t1=evento['t1'], ev=[evento]))
    for fase in fasi_viewport:
        eventi_fase = fase['ev']
        mods = [evento for evento in eventi_fase if evento['kind'] == "mod"]
        ist = [evento for evento in eventi_fase if evento['kind'] == "mod-istantaneo"]
        vis = [evento for evento in eventi_fase if evento['kind'] == "vista"]
        parts = []
        zone_fase = [evento['zone'] for evento in mods + ist]
        zn = SHORT.get(max(set(zone_fase), key=zone_fase.count), "viewport") if zone_fase else "viewport"
        if mods:
            intensita_mod = [evento['inten'] for evento in mods]
            parts.append("modifica mesh in %s, %d %s, intensita %s" % (
                zn, len(mods), "gesto" if len(mods) == 1 else "gesti",
                max(set(intensita_mod), key=intensita_mod.count)))
        if vis:
            sotto_tipi = {}
            for evento in vis:
                sotto_tipi[evento['sub']] = sotto_tipi.get(evento['sub'], 0) + 1
            totale = sum(evento['t1'] - evento['t0'] for evento in vis)
            parts.append("vista " + ", ".join("%s %dx" % (nome, volte) for nome, volte in sotto_tipi.items())
                         + " (%.0fs)" % totale)
        if ist:
            nome_ist = "cambio istantaneo" if len(ist) == 1 else "cambi istantanei"
            parts.append("%d %s in %s (clic/modifica puntuale) @%s" % (
                len(ist), nome_ist, zn, ",".join(tf1(evento['t0']) for evento in ist)))
        clic_fase = [(i, clic) for i, clic in enumerate(clicks)
                     if fase['t0'] - 0.8 <= clic['t'] <= fase['t1'] + 0.5 and i not in used_clicks]
        for i, clic in clic_fase:
            used_clicks.add(i)
        if clic_fase:
            descrizioni_clic = []
            for _, clic in clic_fase:
                destinazione = "" if clic['near'] else "->" + SHORT.get(clic['eff'], clic['eff'])
                zona_clic = SHORT.get(clic['zone'], clic['zone'])
                descrizioni_clic.append("%s(%d,%d %s)%s" % (tf1(clic['t']), clic['x'], clic['y'],
                                                            zona_clic, destinazione))
            parts.append("clic " + " ".join(descrizioni_clic))
        scatole_fase = [evento['bbox'] for evento in mods + ist if evento.get('bbox')]
        FASI.append(dict(t0=fase['t0'], t1=fase['t1'], mods=len(mods), ist=len(ist),
                         bbox=_unione_scatole(scatole_fase),
                         clicks=[[clic['t'], clic['x'], clic['y']] for _, clic in clic_fase]))
        intestazione = "fase" if mods else ("fase navigazione" if vis else "fase")
        out.append((fase['t0'], "%s %s: %s" % (rng(fase['t0'], fase['t1']), intestazione, "; ".join(parts))))
    for i, clic in enumerate(clicks):
        if i not in used_clicks:
            out.append((clic['t'], "[%s] clic probabile (%d,%d) in %s, effetto in %s" % (
                tf1(clic['t']), clic['x'], clic['y'],
                SHORT.get(clic['zone'], clic['zone']), SHORT.get(clic['eff'], clic['eff']))))
    out.sort(key=lambda voce: voce[0])
    return [voce[1] for voce in out]


if __name__ == "__main__":
    video = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.dirname(os.path.abspath(__file__))
    os.makedirs(out, exist_ok=True)
    tempi.configura('movimento', out, video)
    tempi.strumenta_cv2(cv2)
    color, gray, rows, cur, info, dt, avvio = analizza(video)
    classifica(rows)
    if DEBUG:
        for riga in rows:
            if riga['area'] >= 0.25:
                print("%s a=%5.1f va=%5.1f c=%-6s z=%-18s cpct=%4.1f fm=%.2f R=%.2f mm=%.2f ext=%.2f %s" % (
                    tf1(riga['t']), riga['area'], riga['varea'], riga['c'], riga['zone'], riga['cpct'],
                    riga['fmov'], riga['R'], riga['mmag'], riga['ext'], riga.get('sub', '')))
    evs, clicks = eventi(rows, cur, dt, color[0].shape[0])
    lines = componi(evs, clicks)
    traccia_cursore = [[round(k * dt, 2), round(posizione[0], 1), round(posizione[1], 1)]
                       for k, posizione in enumerate(cur) if posizione is not None]
    with open(os.path.join(out, "mov.json"), "w") as file_mov:
        json.dump(dict(fasi=FASI, dt=dt, W=W, H=color[0].shape[0], cur=traccia_cursore), file_mov)
    testo = chr(10).join(lines) + chr(10)
    with open(os.path.join(out, "eventi.txt"), "w", encoding="utf-8") as file_eventi:
        file_eventi.write(testo)
    print("tempo %.1fs  righe %d  caratteri %d  token~%d  cursore %d/%d fotogrammi" % (
        time.time() - avvio, len(lines), len(testo), len(testo) // 4,
        sum(posizione is not None for posizione in cur), len(cur)))
