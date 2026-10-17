# -*- coding: utf-8 -*-
"""occhio.py <video> <outdir>  -> occhio.json
L'occhio locale usa qwen3-vl:8b tramite Ollama con domande chiuse.
Per ogni intervallo d'azione (fasi del movimento, spezzate in tratti <= 6 s): immagine 'prima | dopo' (ritaglio attorno al cursore),
riga di risposta in formato rigido; la stessa domanda su 'dopo' a t_fine e a t_fine+0.5 s: si accetta solo la parte coerente."""
import sys, os, json, time, re, base64, subprocess
import numpy as np, cv2, requests
from multiprocessing.pool import ThreadPool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import comune
import tempi
_C = comune.config()['ollama']
MODEL = _C['modello']
SERVER = _C['indirizzo'].rstrip('/')
CROP_W, CROP_H = 720, 450
STEP = 6.0
PROMPT = ("Immagine di uno schermo di software in due meta': a SINISTRA = PRIMA, a DESTRA = DOPO (stessa zona). "
          "Rispondi SOLO con questa riga, nessun altro testo:\n"
          "NUOVA_GEOMETRIA: si/no; FORMA: anello/fascia di quad/griglia/nessuna; "
          "DOVE: centro/alto/basso/sinistra/destra/vicino al cursore; ETICHETTA_VICINO_CURSORE: testo letto o nessuna\n"
          "Regole: NUOVA_GEOMETRIA=si solo se nella meta' DOPO compaiono linee, facce o maglie che nella meta' PRIMA non ci sono. "
          "Non nominare strumenti o comandi. Se non sei sicuro: no e nessuna.")


def frame_at(video, t):
    with tempi.blocco('decodifica_video_parziale'):
        p = subprocess.run([comune.ffmpeg(), '-v', 'error', '-ss', '%.3f' % max(0, t), '-i', video, '-frames:v', '1', '-vf', 'scale=1280:-2', '-f', 'image2pipe', '-vcodec', 'png', '-'], stdout=subprocess.PIPE)
    tempi.fotogrammi(n=1)
    return cv2.imdecode(np.frombuffer(p.stdout, np.uint8), cv2.IMREAD_COLOR)


SCHEMA = {"type": "object", "properties": {
    "NUOVA_GEOMETRIA": {"enum": ["si", "no"]},
    "FORMA": {"enum": ["anello", "fascia di quad", "griglia", "nessuna"]},
    "DOVE": {"enum": ["centro", "alto", "basso", "sinistra", "destra", "vicino al cursore"]},
    "ETICHETTA_VICINO_CURSORE": {"type": "string"}},
    "required": ["NUOVA_GEOMETRIA", "FORMA", "DOVE", "ETICHETTA_VICINO_CURSORE"]}


def chiedi(img, prompt=PROMPT, schema=SCHEMA, num_predict=150):
    ok, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    body = dict(model=MODEL, stream=False, think=False, keep_alive='5m', format=schema,
                options=dict(temperature=0.1, num_predict=num_predict),
                messages=[dict(role='user', content=prompt, images=[base64.b64encode(buf.tobytes()).decode()])])
    with tempi.blocco('ollama'):
        r = requests.post(SERVER + '/api/chat', json=body, timeout=300).json()
    m = r.get('message', {})
    return (m.get('content') or m.get('thinking') or '').strip()   # con format, questo modello scrive la risposta nel campo 'thinking'


def parse(txt):
    try:
        d = json.loads(txt)
        return {k: str(d[k]).strip().strip('"') for k in SCHEMA['required']}
    except Exception:
        return None


def intervalli(mv):
    out = []
    for f in mv['fasi']:
        if f['mods'] + f['ist'] == 0:
            continue
        a, b = f['t0'], f['t1']
        n = max(1, int(np.ceil((b - a) / STEP)))
        for i in range(n):
            out.append((a + (b - a) * i / n, a + (b - a) * (i + 1) / n, f))
    return out


def lavora(args):
    video, mv, (a, b, f) = args
    sc = 1280 / mv['W']
    cur = np.array(mv['cur']) if mv['cur'] else np.zeros((0, 3))
    c = None
    if len(cur):
        k = int(np.argmin(np.abs(cur[:, 0] - b)))
        if abs(cur[k, 0] - b) <= 1.5:
            c = (cur[k, 1] * sc, cur[k, 2] * sc)
    if c is None and f['bbox']:
        bb = f['bbox']; c = ((bb[0] + bb[2]) / 2 * sc, (bb[1] + bb[3]) / 2 * sc)
    if c is None:
        return dict(t0=a, t1=b, errore='senza posizione')
    pre = frame_at(video, a - 0.2)
    post1 = frame_at(video, b)
    post2 = frame_at(video, b + 0.5)
    H = pre.shape[0]
    x0 = int(min(max(0, c[0] - CROP_W / 2), 1280 - CROP_W)); y0 = int(min(max(0, c[1] - CROP_H / 2), H - CROP_H))
    cr = lambda im: im[y0:y0 + CROP_H, x0:x0 + CROP_W]
    r = []
    for post in (post1, post2):
        im = np.hstack([cr(pre), np.full((CROP_H, 6, 3), 255, np.uint8), cr(post)])
        txt = chiedi(im)
        r.append(dict(raw=txt, p=parse(txt)))
    return dict(t0=a, t1=b, crop=[x0, y0], r=r)


if __name__ == '__main__':
    video, out = sys.argv[1], sys.argv[2]
    tempi.configura('occhio', out, video)
    tempi.strumenta_cv2(cv2)
    mv = json.load(open(os.path.join(out, 'mov.json')))
    t0 = time.time()
    iv = intervalli(mv)
    with ThreadPool(2) as tp:
        res = tp.map(lavora, [(video, mv, i) for i in iv])
    json.dump(dict(res=res, calls=2 * len(iv), tempo=round(time.time() - t0, 1)), open(os.path.join(out, 'occhio.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
    print('occhio', len(iv), 'intervalli', 2 * len(iv), 'chiamate', round(time.time() - t0, 1), 's', flush=True)
