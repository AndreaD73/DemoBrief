# -*- coding: utf-8 -*-
"""RESULT channel: targeted comparison of the still screen content before and after an action step.

Reads <work>/mov.json (movement phases and cursor track) and <work>/ocr3.json (OCR readings, used
to mask static interface text) plus the video. For every phase whose screen is still before and
after but different, it keeps a before/after frame pair, asks a local Ollama vision model to
describe the change, and writes <work>/eventi_risultato.json with one event per useful answer.
"""
import base64
import collections
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cv2
import numpy as np
import requests

import comune
import eventi
import occhio

VERSIONE = "0.2"
FPS_ANALISI = 4.0
FERMO_SECONDI = 1.0
SOGLIA_FERMO = 0.012
SOGLIA_CAMBIO = 0.018
MARGINE = 0.12
MAX_COPPIE_MINUTO = 3
LATO_LUNGO = 1024
MODELLO_PREDEFINITO = "qwen3-vl:8b"
# Grey-level difference at which a pixel counts as changed.
SOGLIA_PIXEL = 18
# Pixels added around every masked static text box.
MARGINE_TESTO = 8
# Side panels are cut as 35% x 50% of the frame, top/bottom panels as 50% x 35%.
PANNELLO_CORTO = 0.35
PANNELLO_LUNGO = 0.50
# A cursor sample further than this many seconds from the phase end is not trusted.
CURSORE_DISTANZA_MAX = 1.5

PROMPT = """Compare the BEFORE image with the AFTER image. A third image, when present, is a 2x crop of the \
panel nearest to the cursor in the AFTER image.
Return one JSON object and no prose, using exactly this schema:
{
  "pulsante_acceso": {"nome": "...", "dove": "sinistra|destra|alto|basso|centro"} or null,
  "valori_letti": [{"nome": "...", "valore": "..."}],
  "superficie": {"prima": "liscia|faccettata|a griglia|altro", \
"dopo": "liscia|faccettata|a griglia|altro"} or null,
  "griglia": "piu_fitta|piu_rada|uguale|non_visibile",
  "gruppi_colore": {"numero": N, "colori": ["..."]} or null,
  "forma_cambiata": "at most 10 words describing the shape change" or null,
  "certezza": "alta|media|bassa"
}
Rules:
- Report only visible facts.
- valori_letti contains at most three clearly readable labels and values.
- Do not infer commands, names, values, colors, states, or shape changes that are not visible.
- Use null for an absent or unreadable optional field.
- Use certezza bassa when the comparison is ambiguous."""
SCHEMA = {
    "pulsante_acceso": {"nome": "string", "dove": "sinistra|destra|alto|basso|centro"},
    "valori_letti": [{"nome": "string", "valore": "string"}],
    "superficie": {
        "prima": "liscia|faccettata|a griglia|altro",
        "dopo": "liscia|faccettata|a griglia|altro",
    },
    "griglia": "piu_fitta|piu_rada|uguale|non_visibile",
    "gruppi_colore": {"numero": "integer", "colori": ["string"]},
    "forma_cambiata": "string|null",
    "certezza": "alta|media|bassa",
}
FORMATI = {"cambio": "{cosa}"}
LEGENDA = "RISULTATO = cambiamento osservabile del contenuto fra il fermo prima e il fermo dopo"


def _json(percorso, predefinito):
    """Load a JSON file; return `predefinito` when it is missing, unreadable or not valid JSON."""
    try:
        with open(percorso, encoding="utf-8") as file:
            return json.load(file)
    except (OSError, ValueError, TypeError):
        return predefinito


def _grigio(frame):
    """Convert a BGR frame to grey and blur it (5x5 Gaussian) to suppress compression noise."""
    return cv2.GaussianBlur(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (5, 5), 0)


def _differenza(a, b, maschera):
    """Fraction of masked pixels that differ between frames `a` and `b`.

    Only pixels where `maschera` is non-zero count, and a pixel differs when its grey value moves
    by SOGLIA_PIXEL or more. Missing frames or different sizes count as fully different (1.0).
    """
    if a is None or b is None or a.shape != b.shape:
        return 1.0
    delta = cv2.absdiff(_grigio(a), _grigio(b))
    pixel = delta[maschera > 0]
    return float((pixel >= SOGLIA_PIXEL).mean()) if pixel.size else 0.0


def _maschera_centrale(larghezza, altezza, ocr3):
    """Build the uint8 mask (255 = compare) of the screen area where changes are meaningful.

    Starts from the central window (10%-90% wide, 8%-92% high) and blanks every text box that
    the OCR saw in at least 60% of the readings (min 2): that is static interface text.
    `ocr3` is the content of ocr3.json; its boxes are rescaled to the mask size.
    """
    maschera = np.zeros((altezza, larghezza), np.uint8)
    x0, x1 = int(larghezza * 0.10), int(larghezza * 0.90)
    y0, y1 = int(altezza * 0.08), int(altezza * 0.92)
    maschera[y0:y1, x0:x1] = 255

    letture = ocr3.get("letture", []) if isinstance(ocr3, dict) else []
    ricorrenze = collections.Counter()
    zone = {}
    for lettura in letture:
        voci = lettura[3] if isinstance(lettura, list) and len(lettura) > 3 else []
        viste = set()
        for voce in voci:
            if not isinstance(voce, list) or len(voce) < 6:
                continue
            testo = str(voce[0]).strip().lower()
            scatola = tuple(int(coordinata) for coordinata in voce[2:6])
            chiave = (testo, scatola)
            viste.add(chiave)
            zone[chiave] = scatola
        ricorrenze.update(viste)

    minimo = max(2, int(np.ceil(len(letture) * 0.60)))
    scala_x = larghezza / float(ocr3.get("W") or larghezza)
    scala_y = altezza / float(ocr3.get("H") or altezza)
    for chiave, conteggio in ricorrenze.items():
        if conteggio < minimo:
            continue
        bx0, by0, bx1, by1 = zone[chiave]
        bx0 = max(0, int(bx0 * scala_x) - MARGINE_TESTO)
        by0 = max(0, int(by0 * scala_y) - MARGINE_TESTO)
        bx1 = min(larghezza, int(np.ceil(bx1 * scala_x)) + MARGINE_TESTO)
        by1 = min(altezza, int(np.ceil(by1 * scala_y)) + MARGINE_TESTO)
        maschera[by0:by1, bx0:bx1] = 0
    return maschera


def _scatola_cambio(prima, dopo, maschera):
    """Bounding box of the changed pixels between two frames, restricted to `maschera`.

    Small specks are removed (3x3 opening) and the rest is merged (9x9 dilation) first.
    Returns ([x0, y0, x1, y1], changed-pixel count), or (None, 0) when nothing changed.
    """
    delta = cv2.absdiff(_grigio(prima), _grigio(dopo))
    cambiati = ((delta >= SOGLIA_PIXEL) & (maschera > 0)).astype(np.uint8)
    cambiati = cv2.morphologyEx(cambiati, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    cambiati = cv2.dilate(cambiati, np.ones((9, 9), np.uint8))
    ys, xs = np.nonzero(cambiati)
    if not len(xs):
        return None, 0
    return [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1], int(cambiati.sum())


def _fotogramma(video, t):
    """Decode the frame of `video` at time `t` (never before 0 s) through the vision module."""
    return occhio.frame_at(video, max(0.0, float(t)))


def candidati(video, mov, ocr3, leggi_frame=_fotogramma):
    """Find the action phases that left a visible, stable change on screen.

    For each phase with edits it reads the frames at the phase start/end and one second outside
    each side. The phase qualifies when the screen is still on both sides (change below
    SOGLIA_FERMO) and differs across the step (at least SOGLIA_CAMBIO). `leggi_frame(video, t)`
    is injectable for tests. Returns a list of dicts with the frame pair, change box, area,
    strength, frame size and the cursor position (in frame pixels, or None).
    """
    larghezza = int(mov.get("W") or ocr3.get("W") or 0)
    altezza = int(mov.get("H") or ocr3.get("H") or 0)
    risultato = []
    cache = {}

    def frame(t):
        """Frame at time `t`, read once per rounded time."""
        chiave = round(max(0.0, float(t)), 3)
        if chiave not in cache:
            cache[chiave] = leggi_frame(video, chiave)
        return cache[chiave]

    for indice, fase in enumerate(mov.get("fasi", []), 1):
        if int(fase.get("mods", 0)) + int(fase.get("ist", 0)) == 0:
            continue
        t0 = float(fase.get("t0", 0.0))
        t1 = float(fase.get("t1", t0))
        prima_ferma = frame(t0)
        prima_controllo = frame(t0 - FERMO_SECONDI)
        dopo_fermo = frame(t1)
        dopo_controllo = frame(t1 + FERMO_SECONDI)
        if any(immagine is None for immagine in (prima_ferma, prima_controllo, dopo_fermo, dopo_controllo)):
            continue
        h, w = prima_ferma.shape[:2]
        if larghezza <= 0 or altezza <= 0:
            larghezza, altezza = w, h
        maschera = _maschera_centrale(w, h, ocr3)
        fermo_prima = _differenza(prima_controllo, prima_ferma, maschera)
        fermo_dopo = _differenza(dopo_fermo, dopo_controllo, maschera)
        cambio = _differenza(prima_ferma, dopo_fermo, maschera)
        if fermo_prima > SOGLIA_FERMO or fermo_dopo > SOGLIA_FERMO or cambio < SOGLIA_CAMBIO:
            continue
        scatola, area = _scatola_cambio(prima_ferma, dopo_fermo, maschera)
        if scatola is None:
            continue
        cursore = None
        traccia = [
            voce for voce in mov.get("cur", [])
            if isinstance(voce, list) and len(voce) >= 3
        ]
        if traccia:
            vicino = min(traccia, key=lambda voce: abs(float(voce[0]) - t1))
            if abs(float(vicino[0]) - t1) <= CURSORE_DISTANZA_MAX:
                cursore = [
                    round(float(vicino[1]) * w / float(larghezza), 2),
                    round(float(vicino[2]) * h / float(altezza), 2),
                ]
        risultato.append({
            "rif": "F%d" % indice,
            "t": round(t1, 2),
            "prima": prima_ferma,
            "dopo": dopo_fermo,
            "scatola": scatola,
            "area": area,
            "forza": min(1.0, max(0.5, cambio * 8.0)),
            "dimensioni": [w, h],
            "cursore": cursore,
        })
    return risultato


def limita(coppie):
    """Keep at most MAX_COPPIE_MINUTO pairs per minute of video, preferring the largest areas.

    Ties are broken by time and reference; the result is sorted by time.
    """
    per_minuto = collections.defaultdict(list)
    for coppia in coppie:
        per_minuto[int(float(coppia["t"]) // 60)].append(coppia)
    risultato = []
    for minuto in sorted(per_minuto):
        scelti = sorted(
            per_minuto[minuto],
            key=lambda coppia: (-int(coppia["area"]), float(coppia["t"]), coppia["rif"]),
        )[:MAX_COPPIE_MINUTO]
        risultato.extend(scelti)
    return sorted(risultato, key=lambda coppia: (float(coppia["t"]), coppia["rif"]))


def _ridimensiona(img, lato=LATO_LUNGO):
    """Scale `img` so that its longer side is `lato` pixels (area filter down, cubic up)."""
    h, w = img.shape[:2]
    scala = float(lato) / float(max(h, w))
    interpolazione = cv2.INTER_AREA if scala < 1.0 else cv2.INTER_CUBIC
    return cv2.resize(
        img,
        (max(1, int(round(w * scala))), max(1, int(round(h * scala)))),
        interpolation=interpolazione,
    )


def _ritaglio_pannello(frame, cursore):
    """Cut and double the screen panel that touches the screen edge nearest to the cursor.

    `cursore` is [x, y] in frame pixels; without it nothing is cut and None is returned. The
    crop is enlarged 2x and shrunk back to LATO_LUNGO if it exceeds that size.
    """
    if cursore is None:
        return None
    h, w = frame.shape[:2]
    x = min(max(0, int(round(cursore[0]))), w - 1)
    y = min(max(0, int(round(cursore[1]))), h - 1)
    bordo = min(
        ((x, "sinistra"), (w - 1 - x, "destra"),
         (y, "alto"), (h - 1 - y, "basso")),
        key=lambda voce: (voce[0], voce[1]),
    )[1]
    if bordo in ("sinistra", "destra"):
        larghezza = max(1, int(round(w * PANNELLO_CORTO)))
        altezza = max(1, int(round(h * PANNELLO_LUNGO)))
        x0 = 0 if bordo == "sinistra" else w - larghezza
        y0 = min(max(0, y - altezza // 2), h - altezza)
    else:
        larghezza = max(1, int(round(w * PANNELLO_LUNGO)))
        altezza = max(1, int(round(h * PANNELLO_CORTO)))
        x0 = min(max(0, x - larghezza // 2), w - larghezza)
        y0 = 0 if bordo == "alto" else h - altezza
    ritaglio = frame[y0:y0 + altezza, x0:x0 + larghezza]
    ingrandito = cv2.resize(
        ritaglio,
        (ritaglio.shape[1] * 2, ritaglio.shape[0] * 2),
        interpolation=cv2.INTER_CUBIC,
    )
    if max(ingrandito.shape[:2]) > LATO_LUNGO:
        ingrandito = _ridimensiona(ingrandito)
    return ingrandito


def immagini_separate(coppia):
    """Images sent to the model for one pair: before, after and (with a cursor) the panel crop.

    `coppia` is one item from `candidati`. Each image is scaled to LATO_LUNGO on its long side.
    """
    immagini = [
        _ridimensiona(coppia["prima"]),
        _ridimensiona(coppia["dopo"]),
    ]
    pannello = _ritaglio_pannello(coppia["dopo"], coppia.get("cursore"))
    if pannello is not None:
        immagini.append(pannello)
    return immagini


def configurazione_ollama(config):
    """Pick the model and server address for this channel from the project configuration.

    Uses 'modello_risultato' (fallback MODELLO_PREDEFINITO) and 'indirizzo' (fallback local
    Ollama on port 11434, trailing slash removed). Returns (model, server).
    """
    ollama = config.get("ollama", {}) if isinstance(config, dict) else {}
    modello = str(ollama.get("modello_risultato") or MODELLO_PREDEFINITO)
    server = str(ollama.get("indirizzo") or "http://127.0.0.1:11434").rstrip("/")
    return modello, server


def chiedi_ollama(immagini, modello, server):
    """Send the images and the fixed PROMPT to Ollama's chat API and return the raw answer text.

    Images are JPEG (quality 90) base64 encoded; JSON output, temperature 0, at most 300 tokens.
    Raises on encoding failure or HTTP error; the answer may be in 'content' or 'thinking'.
    """
    codificate = []
    for immagine in immagini:
        ok, buffer = cv2.imencode(
            ".jpg", immagine, [cv2.IMWRITE_JPEG_QUALITY, 90]
        )
        if not ok:
            raise ValueError("codifica immagine fallita")
        codificate.append(base64.b64encode(buffer.tobytes()).decode("ascii"))
    corpo = {
        "model": modello,
        "stream": False,
        "think": False,
        "keep_alive": "5m",
        "format": "json",
        "options": {"temperature": 0, "num_predict": 300},
        "messages": [{"role": "user", "content": PROMPT, "images": codificate}],
    }
    risposta_http = requests.post(
        server + "/api/chat", json=corpo, timeout=300
    )
    risposta_http.raise_for_status()
    messaggio = risposta_http.json().get("message", {})
    return str(
        messaggio.get("content") or messaggio.get("thinking") or ""
    ).strip()


def risposta(txt):
    """Turn the model's JSON answer into a short description of the change.

    Only answers with confidence 'alta' or 'media' are accepted. The text lists, in order: lit
    button, up to three read values, surface change, grid density, colour groups and shape change,
    cut to 30 words. Returns {"cosa", "certezza"}, or None when invalid, unsure or empty.
    """
    try:
        dato = json.loads(txt)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(dato, dict):
        return None
    certezza = str(dato.get("certezza", "")).strip().lower()
    if certezza not in ("alta", "media"):
        return None

    parti = []
    pulsante = dato.get("pulsante_acceso")
    posizioni = {
        "sinistra": "sinistro", "destra": "destro", "alto": "alto", "basso": "basso", "centro": "centrale",
    }
    if isinstance(pulsante, dict):
        nome = _testo_breve(pulsante.get("nome"), 5)
        dove = str(pulsante.get("dove", "")).strip().lower()
        if nome and dove in posizioni:
            parti.append('pannello %s: acceso "%s"' % (posizioni[dove], nome))

    valori = dato.get("valori_letti")
    if isinstance(valori, list):
        for voce in valori[:3]:
            if not isinstance(voce, dict):
                continue
            nome = _testo_breve(voce.get("nome"), 4)
            valore = _testo_breve(voce.get("valore"), 3)
            if nome and valore:
                parti.append("%s = %s" % (nome, valore))

    superficie = dato.get("superficie")
    tipi_superficie = {"liscia", "faccettata", "a griglia", "altro"}
    if isinstance(superficie, dict):
        prima = str(superficie.get("prima", "")).strip().lower()
        dopo = str(superficie.get("dopo", "")).strip().lower()
        if prima in tipi_superficie and dopo in tipi_superficie:
            parti.append("superficie %s -> %s" % (prima, dopo))

    griglie = {
        "piu_fitta": "piu' fitta",
        "piu_rada": "piu' rada",
        "uguale": "uguale",
        "non_visibile": "non visibile",
    }
    griglia = str(dato.get("griglia", "")).strip().lower()
    if griglia in griglie:
        parti.append("griglia %s" % griglie[griglia])

    gruppi = dato.get("gruppi_colore")
    if isinstance(gruppi, dict):
        try:
            numero = int(gruppi.get("numero"))
        except (TypeError, ValueError):
            numero = 0
        colori = gruppi.get("colori")
        if numero > 0 and isinstance(colori, list):
            nomi = [_testo_breve(colore, 1) for colore in colori[:numero]]
            nomi = [nome for nome in nomi if nome]
            if nomi:
                parti.append("%d gruppi: %s" % (numero, ", ".join(nomi)))

    forma = _testo_breve(dato.get("forma_cambiata"), 10)
    if forma:
        parti.append(forma)
    if not parti:
        return None
    parole = "; ".join(parti).split()
    return {"cosa": " ".join(parole[:30]), "certezza": certezza}


def _testo_breve(valore, massimo):
    """Normalise whitespace in `valore` (None allowed) and keep only its first `massimo` words."""
    parole = str(valore or "").strip().split()
    return " ".join(parole[:massimo])


def interroga(
    coppie,
    modello=MODELLO_PREDEFINITO,
    server="http://127.0.0.1:11434",
    chiedi=chiedi_ollama,
):
    """Ask the model about each pair and build one event per usable answer.

    `chiedi(images, model, server)` is injectable for tests. Failed requests are skipped and
    only the exception class name is collected. Event zone and point are normalised to 0-1.
    Returns (events list, error names list).
    """
    eventi_calcolati = []
    errori = []
    for indice, coppia in enumerate(coppie, 1):
        try:
            raw = chiedi(immagini_separate(coppia), modello, server)
            esito = risposta(raw)
        except Exception as exc:
            errori.append(type(exc).__name__)
            continue
        if esito is None:
            continue
        w, h = coppia["dimensioni"]
        x0, y0, x1, y1 = coppia["scatola"]
        zona = [
            round(x0 / float(w), 4),
            round(y0 / float(h), 4),
            round(x1 / float(w), 4),
            round(y1 / float(h), 4),
        ]
        eventi_calcolati.append({
            "id": "R%d" % indice,
            "t": coppia["t"],
            "durata": 0.0,
            "canale": "risultato",
            "tipo": "cambio",
            "zona": zona,
            "punto": [round((zona[0] + zona[2]) / 2.0, 4),
                      round((zona[1] + zona[3]) / 2.0, 4)],
            "testo": esito["cosa"],
            "forza": 0.9 if esito["certezza"] == "alta" else 0.7,
            "stampa": "riga",
            "rif": [coppia["rif"]],
            "dati": {"cosa": esito["cosa"], "certezza": esito["certezza"]},
        })
    return eventi_calcolati, errori


def documento(eventi_calcolati, secondi, coppie, motivo=""):
    """Assemble the channel's output document (schema 1) from the events and run statistics.

    `secondi` is the elapsed time, `coppie` the number of pairs examined, `motivo` the reason
    shown when the channel produced nothing.
    """
    return {
        "schema": 1,
        "canale": "risultato",
        "lettera": "O1",
        "versione": VERSIONE,
        "sezione": "RISULTATO",
        "legenda": LEGENDA,
        "formati": dict(FORMATI),
        "stat": {
            "secondi": round(float(secondi), 3),
            "coppie": int(coppie),
            "eventi": len(eventi_calcolati),
            "motivo": str(motivo),
        },
        "eventi": eventi_calcolati,
    }


def main(argv):
    """Command line entry: canale_risultato.py <video> <work folder>.

    Runs candidate search, model queries and writes eventi_risultato.json in the work folder.
    Prints a one-line summary and returns exit code 0, even when Ollama is unreachable.
    """
    if len(argv) != 3:
        raise SystemExit("uso: canale_risultato.py <video> <cartella _lavoro>")
    video = os.path.abspath(argv[1])
    lavoro = os.path.abspath(argv[2])
    inizio = time.perf_counter()
    mov = _json(os.path.join(lavoro, "mov.json"), {})
    ocr3 = _json(os.path.join(lavoro, "ocr3.json"), {})
    scelti = limita(candidati(video, mov, ocr3))
    modello, server = configurazione_ollama(comune.config())
    calcolati, errori = interroga(scelti, modello=modello, server=server)
    motivo = ""
    if scelti and errori and not calcolati:
        motivo = "Ollama non raggiungibile o risposta non valida"
    elif not scelti:
        motivo = "nessuna coppia stabile con cambio del contenuto"
    doc = documento(calcolati, time.perf_counter() - inizio, len(scelti), motivo)
    eventi.scrivi(os.path.join(lavoro, "eventi_risultato.json"), doc)
    print(
        "canale_risultato: coppie=%d eventi=%d tempo=%.1fs motivo=%s"
        % (len(scelti), len(calcolati), doc["stat"]["secondi"], motivo or "nessuno"),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
