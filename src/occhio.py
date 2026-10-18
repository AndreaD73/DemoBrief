# -*- coding: utf-8 -*-
"""Local vision step: asks a vision model which action intervals created new geometry.

Usage: occhio.py <video> <outdir>. Reads <outdir>/mov.json (movement phases and cursor track)
and the video; writes <outdir>/occhio.json with the model answers for every interval.
The local eye is qwen3-vl:8b served by Ollama, queried with closed questions.
For each action interval (movement phases, split into stretches of at most 6 s) it builds a
'before | after' image cropped around the cursor and asks a fixed-format question; the same
question is repeated on 'after' at t_end and at t_end+0.5 s, so only the coherent part is trusted.
"""
import base64
import json
import os
import subprocess
import sys
import time
from multiprocessing.pool import ThreadPool

import cv2
import numpy as np
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import comune
import tempi
_C = comune.config()['ollama']
MODEL = _C['modello']
SERVER = _C['indirizzo'].rstrip('/')
CROP_W, CROP_H = 720, 450
STEP = 6.0
FRAME_WIDTH = 1280
SEPARATOR_WIDTH = 6
MAX_CURSOR_GAP = 1.5
POST_DELAY = 0.5
PRE_DELAY = 0.2
PROMPT = ("Immagine di uno schermo di software in due meta': "
          "a SINISTRA = PRIMA, a DESTRA = DOPO (stessa zona). "
          "Rispondi SOLO con questa riga, nessun altro testo:\n"
          "NUOVA_GEOMETRIA: si/no; FORMA: anello/fascia di quad/griglia/nessuna; "
          "DOVE: centro/alto/basso/sinistra/destra/vicino al cursore; "
          "ETICHETTA_VICINO_CURSORE: testo letto o nessuna\n"
          "Regole: NUOVA_GEOMETRIA=si solo se nella meta' DOPO compaiono linee, facce o maglie "
          "che nella meta' PRIMA non ci sono. "
          "Non nominare strumenti o comandi. Se non sei sicuro: no e nessuna.")


def frame_at(video, t):
    """Decode one frame of `video` at time `t` (seconds, clamped at 0), scaled to 1280 px wide.

    Spawns ffmpeg and returns the BGR image as a numpy array, or None when `t` is past the end
    of the video (ffmpeg then writes nothing).
    """
    with tempi.blocco('decodifica_video_parziale'):
        comando = [comune.ffmpeg(), '-v', 'error', '-ss', '%.3f' % max(0, t), '-i', video,
                   '-frames:v', '1', '-vf', 'scale=1280:-2', '-f', 'image2pipe', '-vcodec', 'png', '-']
        processo = subprocess.run(comando, stdout=subprocess.PIPE)
    tempi.fotogrammi(n=1)
    if not processo.stdout:
        return None
    return cv2.imdecode(np.frombuffer(processo.stdout, np.uint8), cv2.IMREAD_COLOR)


SCHEMA = {"type": "object", "properties": {
    "NUOVA_GEOMETRIA": {"enum": ["si", "no"]},
    "FORMA": {"enum": ["anello", "fascia di quad", "griglia", "nessuna"]},
    "DOVE": {"enum": ["centro", "alto", "basso", "sinistra", "destra", "vicino al cursore"]},
    "ETICHETTA_VICINO_CURSORE": {"type": "string"}},
    "required": ["NUOVA_GEOMETRIA", "FORMA", "DOVE", "ETICHETTA_VICINO_CURSORE"]}


def chiedi(img, prompt=PROMPT, schema=SCHEMA, num_predict=150):
    """Send `img` and `prompt` to the Ollama chat endpoint and return the model's answer text.

    `schema` constrains the reply format and `num_predict` caps the generated tokens.
    """
    ok, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    body = dict(model=MODEL, stream=False, think=False, keep_alive='5m', format=schema,
                options=dict(temperature=0.1, num_predict=num_predict),
                messages=[dict(role='user', content=prompt,
                               images=[base64.b64encode(buf.tobytes()).decode()])])
    with tempi.blocco('ollama'):
        risposta = requests.post(SERVER + '/api/chat', json=body, timeout=300).json()
    messaggio = risposta.get('message', {})
    # With a constrained format this model writes its answer in the 'thinking' field.
    return (messaggio.get('content') or messaggio.get('thinking') or '').strip()


def parse(txt):
    """Parse the model's JSON answer into a dict of the schema's keys (stripped strings).

    Returns None when the text is not valid JSON or a key is missing.
    """
    try:
        dati = json.loads(txt)
        return {chiave: str(dati[chiave]).strip().strip('"') for chiave in SCHEMA['required']}
    except Exception:
        return None


def intervalli(mv):
    """Split every movement phase that has events into stretches of at most STEP seconds.

    `mv` is the content of mov.json. Returns a list of (t_start, t_end, phase) tuples.
    """
    tratti = []
    for fase in mv['fasi']:
        if fase['mods'] + fase['ist'] == 0:
            continue
        inizio, fine = fase['t0'], fase['t1']
        n = max(1, int(np.ceil((fine - inizio) / STEP)))
        for i in range(n):
            inizio_tratto = inizio + (fine - inizio) * i / n
            fine_tratto = inizio + (fine - inizio) * (i + 1) / n
            tratti.append((inizio_tratto, fine_tratto, fase))
    return tratti


def lavora(args):
    """Ask the model about one interval, using a before/after crop centred on the cursor.

    `args` is (video, mov.json content, (t_start, t_end, phase)). The centre is the cursor
    sample nearest to t_end (within 1.5 s) or, failing that, the centre of the phase bbox.
    Returns a dict with the interval, the crop origin and both answers (or an error note, also
    when one of the frames lies past the end of the video).
    """
    video, mv, (inizio, fine, fase) = args
    scala = FRAME_WIDTH / mv['W']
    cursore = np.array(mv['cur']) if mv['cur'] else np.zeros((0, 3))
    centro = None
    if len(cursore):
        k = int(np.argmin(np.abs(cursore[:, 0] - fine)))
        if abs(cursore[k, 0] - fine) <= MAX_CURSOR_GAP:
            centro = (cursore[k, 1] * scala, cursore[k, 2] * scala)
    if centro is None and fase['bbox']:
        bbox = fase['bbox']
        centro = ((bbox[0] + bbox[2]) / 2 * scala, (bbox[1] + bbox[3]) / 2 * scala)
    if centro is None:
        return dict(t0=inizio, t1=fine, errore='senza posizione')
    prima = frame_at(video, inizio - PRE_DELAY)
    dopo1 = frame_at(video, fine)
    dopo2 = frame_at(video, fine + POST_DELAY)
    if prima is None or dopo1 is None or dopo2 is None:
        return dict(t0=inizio, t1=fine, errore='fotogramma oltre la fine del video')
    H = prima.shape[0]
    x0 = int(min(max(0, centro[0] - CROP_W / 2), FRAME_WIDTH - CROP_W))
    y0 = int(min(max(0, centro[1] - CROP_H / 2), H - CROP_H))

    def ritaglia(immagine):
        """Cut the CROP_W x CROP_H window starting at (x0, y0) out of `immagine`."""
        return immagine[y0:y0 + CROP_H, x0:x0 + CROP_W]

    risposte = []
    for dopo in (dopo1, dopo2):
        immagine = np.hstack([ritaglia(prima), np.full((CROP_H, SEPARATOR_WIDTH, 3), 255, np.uint8),
                              ritaglia(dopo)])
        testo = chiedi(immagine)
        risposte.append(dict(raw=testo, p=parse(testo)))
    return dict(t0=inizio, t1=fine, crop=[x0, y0], r=risposte)


if __name__ == '__main__':
    video, out = sys.argv[1], sys.argv[2]
    tempi.configura('occhio', out, video)
    tempi.strumenta_cv2(cv2)
    mv = json.load(open(os.path.join(out, 'mov.json')))
    avvio = time.time()
    iv = intervalli(mv)
    with ThreadPool(2) as pool:
        res = pool.map(lavora, [(video, mv, intervallo) for intervallo in iv])
    with open(os.path.join(out, 'occhio.json'), 'w', encoding='utf-8') as file_occhio:
        json.dump(dict(res=res, calls=2 * len(iv), tempo=round(time.time() - avvio, 1)), file_occhio,
                  ensure_ascii=False, indent=0)
    print('occhio', len(iv), 'intervalli', 2 * len(iv), 'chiamate', round(time.time() - avvio, 1), 's',
          flush=True)
