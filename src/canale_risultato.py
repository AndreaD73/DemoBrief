# -*- coding: utf-8 -*-
"""Canale RISULTATO: confronto mirato del contenuto fermo prima e dopo un passo."""
import collections
import base64
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

PROMPT = """Compare the BEFORE image with the AFTER image. A third image, when present, is a 2x crop of the panel nearest to the cursor in the AFTER image.
Return one JSON object and no prose, using exactly this schema:
{
  "pulsante_acceso": {"nome": "...", "dove": "sinistra|destra|alto|basso|centro"} or null,
  "valori_letti": [{"nome": "...", "valore": "..."}],
  "superficie": {"prima": "liscia|faccettata|a griglia|altro", "dopo": "liscia|faccettata|a griglia|altro"} or null,
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
    "superficie": {"prima": "liscia|faccettata|a griglia|altro", "dopo": "liscia|faccettata|a griglia|altro"},
    "griglia": "piu_fitta|piu_rada|uguale|non_visibile",
    "gruppi_colore": {"numero": "integer", "colori": ["string"]},
    "forma_cambiata": "string|null",
    "certezza": "alta|media|bassa",
}
FORMATI = {"cambio": "{cosa}"}
LEGENDA = "RISULTATO = cambiamento osservabile del contenuto fra il fermo prima e il fermo dopo"


def _json(percorso, predefinito):
    try:
        with open(percorso, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, TypeError):
        return predefinito


def _grigio(frame):
    return cv2.GaussianBlur(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (5, 5), 0)


def _differenza(a, b, maschera):
    if a is None or b is None or a.shape != b.shape:
        return 1.0
    delta = cv2.absdiff(_grigio(a), _grigio(b))
    pixel = delta[maschera > 0]
    return float((pixel >= 18).mean()) if pixel.size else 0.0


def _maschera_centrale(larghezza, altezza, ocr3):
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
            scatola = tuple(int(v) for v in voce[2:6])
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
        bx0 = max(0, int(bx0 * scala_x) - 8)
        by0 = max(0, int(by0 * scala_y) - 8)
        bx1 = min(larghezza, int(np.ceil(bx1 * scala_x)) + 8)
        by1 = min(altezza, int(np.ceil(by1 * scala_y)) + 8)
        maschera[by0:by1, bx0:bx1] = 0
    return maschera


def _scatola_cambio(prima, dopo, maschera):
    delta = cv2.absdiff(_grigio(prima), _grigio(dopo))
    cambiati = ((delta >= 18) & (maschera > 0)).astype(np.uint8)
    cambiati = cv2.morphologyEx(cambiati, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    cambiati = cv2.dilate(cambiati, np.ones((9, 9), np.uint8))
    ys, xs = np.nonzero(cambiati)
    if not len(xs):
        return None, 0
    return [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1], int(cambiati.sum())


def _fotogramma(video, t):
    return occhio.frame_at(video, max(0.0, float(t)))


def candidati(video, mov, ocr3, leggi_frame=_fotogramma):
    larghezza = int(mov.get("W") or ocr3.get("W") or 0)
    altezza = int(mov.get("H") or ocr3.get("H") or 0)
    risultato = []
    cache = {}

    def frame(t):
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
        if any(x is None for x in (prima_ferma, prima_controllo, dopo_fermo, dopo_controllo)):
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
            if abs(float(vicino[0]) - t1) <= 1.5:
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
    per_minuto = collections.defaultdict(list)
    for coppia in coppie:
        per_minuto[int(float(coppia["t"]) // 60)].append(coppia)
    risultato = []
    for minuto in sorted(per_minuto):
        scelti = sorted(
            per_minuto[minuto],
            key=lambda x: (-int(x["area"]), float(x["t"]), x["rif"]),
        )[:MAX_COPPIE_MINUTO]
        risultato.extend(scelti)
    return sorted(risultato, key=lambda x: (float(x["t"]), x["rif"]))


def _ridimensiona(img, lato=LATO_LUNGO):
    h, w = img.shape[:2]
    scala = float(lato) / float(max(h, w))
    interpolazione = cv2.INTER_AREA if scala < 1.0 else cv2.INTER_CUBIC
    return cv2.resize(
        img,
        (max(1, int(round(w * scala))), max(1, int(round(h * scala)))),
        interpolation=interpolazione,
    )


def _ritaglio_pannello(frame, cursore):
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
        larghezza = max(1, int(round(w * 0.35)))
        altezza = max(1, int(round(h * 0.50)))
        x0 = 0 if bordo == "sinistra" else w - larghezza
        y0 = min(max(0, y - altezza // 2), h - altezza)
    else:
        larghezza = max(1, int(round(w * 0.50)))
        altezza = max(1, int(round(h * 0.35)))
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
    immagini = [
        _ridimensiona(coppia["prima"]),
        _ridimensiona(coppia["dopo"]),
    ]
    pannello = _ritaglio_pannello(coppia["dopo"], coppia.get("cursore"))
    if pannello is not None:
        immagini.append(pannello)
    return immagini


def configurazione_ollama(config):
    ollama = config.get("ollama", {}) if isinstance(config, dict) else {}
    modello = str(ollama.get("modello_risultato") or MODELLO_PREDEFINITO)
    server = str(ollama.get("indirizzo") or "http://127.0.0.1:11434").rstrip("/")
    return modello, server


def chiedi_ollama(immagini, modello, server):
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
    posizioni = {"sinistra": "sinistro", "destra": "destro", "alto": "alto", "basso": "basso", "centro": "centrale"}
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

    griglie = {"piu_fitta": "piu' fitta", "piu_rada": "piu' rada", "uguale": "uguale", "non_visibile": "non visibile"}
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
    parole = str(valore or "").strip().split()
    return " ".join(parole[:massimo])


def interroga(
    coppie,
    modello=MODELLO_PREDEFINITO,
    server="http://127.0.0.1:11434",
    chiedi=chiedi_ollama,
):
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
