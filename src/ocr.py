# -*- coding: utf-8 -*-
"""ocr4.py <fase> <video> <outdir>
fase 'letture': (1) scoperta zone: fotogrammi ogni 2 s, solo RILEVAMENTO dei riquadri di testo (nessun riconoscimento),
                    i riquadri che ricorrono nello stesso punto con contenuto di pixel diverso = zone dinamiche (una per riga di testo);
                (2) lettura a 1 fps delle sole zone dinamiche, solo se il ritaglio cambia, riconoscimento di riga singola.
fase 'schermate': 1 fps; quando lo schermo si ferma su una vista diversa dall'ultima letta (finestra, maschera,
                 pannello, pagina nuova) legge per intero il riquadro cambiato, una volta sola -> ocr3.json.
fase 'azione' : usa mov.json (fasi di azione dal movimento): 2 fps nelle fasi (mezzi secondi) + finestre 300x150 attorno al cursore.
Tutto in multiprocessing; nessuna coordinata o soglia legata a un video."""
import subprocess, sys, os, json, time, re, hashlib
import numpy as np, cv2
from multiprocessing import Pool
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import comune
import tempi

def _cuda_disponibile():
    if not comune.config().get('ocr_gpu', True):
        return False
    try:
        import onnxruntime
        return 'CUDAExecutionProvider' in onnxruntime.get_available_providers()
    except Exception:
        return False


_USA_CUDA = _cuda_disponibile()
if _USA_CUDA:
    NPROC = max(1, min(int(comune.config().get('processi_ocr_gpu', 2)), 2, os.cpu_count() or 1))
else:
    NPROC = max(1, min(int(comune.config().get('processi_ocr', 3)), 3, os.cpu_count() or 1))


class _MotoreConFallback:
    def __init__(self, rapidocr, usa_cuda):
        self._rapidocr = rapidocr
        self._cuda = bool(usa_cuda)
        self._prima_chiamata = self._cuda
        if self._cuda:
            try:
                import onnxruntime
                preload = getattr(onnxruntime, 'preload_dlls', None)
                if preload is not None:
                    preload()
                self._motore = rapidocr(
                    det_use_cuda=True,
                    rec_use_cuda=True,
                    cls_use_cuda=True,
                )
                immagine_prova = np.full((64, 256, 3), 255, dtype=np.uint8)
                self._motore(immagine_prova)
            except Exception:
                self._attiva_processore(cuda_non_funzionante=True)
        else:
            self._attiva_processore()

    def _attiva_processore(self, cuda_non_funzionante=False):
        self._cuda = False
        self._prima_chiamata = False
        self._motore = self._rapidocr(
            intra_op_num_threads=1,
            inter_op_num_threads=1,
        )
        if cuda_non_funzionante:
            print('OCR su processore (CUDA non funzionante)', flush=True)
        else:
            print('OCR su processore', flush=True)

    def __call__(self, *args, **kwargs):
        if not self._prima_chiamata:
            return self._motore(*args, **kwargs)
        try:
            risultato = self._motore(*args, **kwargs)
        except Exception:
            self._attiva_processore(cuda_non_funzionante=True)
            return self._motore(*args, **kwargs)
        self._prima_chiamata = False
        print('OCR su CUDA', flush=True)
        return risultato

    def __getattr__(self, nome):
        return getattr(self._motore, nome)
W = 1280
_eng = None


def _init():
    global _eng
    from rapidocr_onnxruntime import RapidOCR
    tempi.configura(comune.ambiente('TEMPI_FASE'), comune.ambiente('TEMPI_LAVORO'), calcolo=False)
    _eng = tempi.strumenta_rapidocr(
        _MotoreConFallback(RapidOCR, _cuda_disponibile())
    )


def t_det(img):
    r, _ = _eng(img, use_cls=False, use_rec=False)
    out = []
    for q in (r or []):
        xs = [p[0] for p in q]; ys = [p[1] for p in q]
        out.append([min(xs), min(ys), max(xs), max(ys)])
    return out


def t_line(img):
    r, _ = _eng(img, use_det=False, use_cls=False, use_rec=True)
    return [[x[0], round(float(x[1]), 2)] for x in (r or [])]


def t_full(img):
    r, _ = _eng(img, use_cls=False)
    return [[x[1], round(float(x[2]), 2), int(x[0][0][0]), int(x[0][0][1])] for x in (r or [])]


def t_box(img):
    r, _ = _eng(img, use_cls=False)
    return [[x[1], round(float(x[2]), 2), int(min(p[0] for p in x[0])), int(min(p[1] for p in x[0])), int(max(p[0] for p in x[0])), int(max(p[1] for p in x[0]))] for x in (r or [])]


def tdisp(a):
    k, img = a
    return k, {'det': t_det, 'line': t_line, 'full': t_full, 'box': t_box}[k.split(':')[0]](img)


def dims(video):
    w, h, dur = comune.info_video(video)
    H = int(round(h * W / w / 2) * 2)
    return H, dur


def frames(video, fps, H, start=0, ss=None):
    cmd = [comune.ffmpeg(), '-v', 'error', '-i', video, '-vf', 'fps=%s,scale=%d:%d' % (fps, W, H), '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-']
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    n = 0
    while True:
        with tempi.blocco('decodifica_video'):
            b = p.stdout.read(W * H * 3)
        if len(b) < W * H * 3:
            break
        yield n, np.frombuffer(b, np.uint8).reshape(H, W, 3)
        tempi.fotogrammi(n=1)
        n += 1
    tempi.conta('video_decodifiche_complete')


def iou(a, b):
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    i = max(0, x1 - x0) * max(0, y1 - y0)
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i
    return i / u if u else 0


def crop(im, z):
    return im[z[1]:z[3], z[0]:z[2]]


def changed(a, b):
    if a is None or a.shape != b.shape:
        return True
    return (np.abs(a.astype(np.int16) - b.astype(np.int16)).sum(2) > 60).mean() >= 0.004


def impronta(img):
    """Blake2b-128 del ritaglio grigio, largo 32 pixel e quantizzato a 16 livelli."""
    if img is None or img.size == 0:
        raise ValueError('Ritaglio OCR vuoto')
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    altezza = max(1, int(round(gray.shape[0] * 32.0 / gray.shape[1])))
    piccolo = cv2.resize(gray, (32, altezza), interpolation=cv2.INTER_AREA)
    quantizzato = (piccolo // 16).astype(np.uint8)
    forma = np.asarray([altezza, 32], dtype=np.uint32).tobytes()
    return hashlib.blake2b(forma + quantizzato.tobytes(), digest_size=16).hexdigest()


class CacheOCR:
    def __init__(self, out):
        self.percorso = os.path.join(out, 'ocr_cache.json')
        if os.path.isfile(self.percorso):
            with open(self.percorso, encoding='utf-8') as file:
                self.dati = json.load(file)
        else:
            self.dati = {}
        self.modificata = False

    def leggi(self, chiave, tipo):
        return self.dati.get(chiave, {}).get(tipo)

    def scrivi(self, chiave, tipo, valore):
        self.dati.setdefault(chiave, {})[tipo] = valore
        self.modificata = True

    def salva(self):
        if not self.modificata:
            return
        temporaneo = self.percorso + '.tmp.%d' % os.getpid()
        with open(temporaneo, 'w', encoding='utf-8', newline='\n') as file:
            json.dump(self.dati, file, ensure_ascii=False, sort_keys=True)
        os.replace(temporaneo, self.percorso)
        self.modificata = False


def esegui_ocr(pool, tasks, out, chunksize, richieste_aggiuntive=0):
    """Risolve una lista di richieste tramite cache e invia a RapidOCR solo le impronte mancanti."""
    cache = CacheOCR(out)
    risultati = {}
    mancanti = {}
    ordine = []
    for chiave, img in tasks:
        tipo = chiave.split(':', 1)[0]
        firma = impronta(img)
        valore = cache.leggi(firma, tipo)
        if valore is not None:
            risultati[chiave] = valore
            continue
        coppia = (firma, tipo)
        if coppia not in mancanti:
            mancanti[coppia] = {'img': img, 'chiavi': []}
            ordine.append(coppia)
        mancanti[coppia]['chiavi'].append(chiave)

    lavoro = []
    riferimenti = {}
    for indice, coppia in enumerate(ordine):
        firma, tipo = coppia
        chiave_lavoro = '%s:cache:%d' % (tipo, indice)
        lavoro.append((chiave_lavoro, mancanti[coppia]['img']))
        riferimenti[chiave_lavoro] = coppia

    calcolati = dict(pool.imap(tdisp, lavoro, chunksize=chunksize)) if lavoro else {}
    for chiave_lavoro, valore in calcolati.items():
        firma, tipo = riferimenti[chiave_lavoro]
        cache.scrivi(firma, tipo, valore)
        for chiave in mancanti[(firma, tipo)]['chiavi']:
            risultati[chiave] = valore

    richieste = len(tasks) + int(richieste_aggiuntive)
    vere = len(lavoro)
    tempi.ocr(richieste=richieste, cache=richieste - vere, rapidocr=vere)
    cache.salva()
    return risultati


# Le fasce restano disattivate: suddividere l'immagine aumenta il numero di chiamate OCR e il tempo
# complessivo, perche' ogni chiamata RapidOCR ha un costo fisso. Si legge il riquadro intero.
FASCE = False


def fasce_orizzontali(y0, y1, altezza=64, sovrapposizione=16):
    """Restituisce fasce assolute alte al massimo 64 pixel con 16 pixel sovrapposti."""
    if y1 <= y0:
        return []
    if y1 - y0 <= altezza:
        return [(y0, y1)]
    passo = altezza - sovrapposizione
    risultato = []
    y = y0
    while True:
        fine = min(y1, y + altezza)
        coppia = (y, fine)
        if coppia not in risultato:
            risultato.append(coppia)
        if fine == y1:
            break
        y += passo
        if y + altezza > y1:
            y = y1 - altezza
    return risultato


def _iou_riga(a, b):
    x0 = max(a[2], b[2])
    y0 = max(a[3], b[3])
    x1 = min(a[4], b[4])
    y1 = min(a[5], b[5])
    intersezione = max(0, x1 - x0) * max(0, y1 - y0)
    area_a = max(0, a[4] - a[2]) * max(0, a[5] - a[3])
    area_b = max(0, b[4] - b[2]) * max(0, b[5] - b[3])
    unione = area_a + area_b - intersezione
    return intersezione / unione if unione else 0.0


def ricomponi_fasce(riferimenti, risultati):
    """Riporta le coordinate al riquadro originale e fonde i duplicati nella sovrapposizione."""
    righe = []
    for scarto_y, chiave in riferimenti:
        for voce in risultati[chiave]:
            corrente = [voce[0], voce[1], int(voce[2]), int(voce[3] + scarto_y), int(voce[4]), int(voce[5] + scarto_y)]
            testo = re.sub(r'\s+', ' ', corrente[0]).strip().casefold()
            duplicato = None
            for indice, esistente in enumerate(righe):
                altro = re.sub(r'\s+', ' ', esistente[0]).strip().casefold()
                if testo == altro and _iou_riga(corrente, esistente) >= 0.2:
                    duplicato = indice
                    break
            if duplicato is None:
                righe.append(corrente)
            elif corrente[1] > righe[duplicato][1]:
                righe[duplicato] = corrente
    righe.sort(key=lambda voce: (voce[3], voce[2], voce[0]))
    return righe


def scoperta(video, H, pool, stat):
    imgs = []
    for n, im in frames(video, '1/2', H):
        imgs.append(im)
    res = esegui_ocr(pool, [('det:%d' % i, im) for i, im in enumerate(imgs)], comune.ambiente('TEMPI_LAVORO'), chunksize=2)
    stat['det'] = len(imgs)
    widgets = []
    for i in range(len(imgs)):
        for b in res['det:%d' % i]:
            if (b[2] - b[0]) < 6 or (b[3] - b[1]) < 6:
                continue
            best = max(widgets, key=lambda w: iou(w['box'], b), default=None)
            if best is not None and iou(best['box'], b) > 0.5:
                best['obs'].append(i)
                best['box'] = [min(best['box'][0], b[0]), min(best['box'][1], b[1]), max(best['box'][2], b[2]), max(best['box'][3], b[3])]
            else:
                widgets.append(dict(box=list(b), obs=[i]))
    zone = []
    for w in widgets:
        if len(w['obs']) < 2:
            continue
        b = [int(max(0, w['box'][0] - 3)), int(max(0, w['box'][1] - 3)), int(min(W, w['box'][2] + 3)), int(min(H, w['box'][3] + 3))]
        if b[2] - b[0] > 0.6 * W or b[3] - b[1] > 60:
            continue
        cs = [cv2.cvtColor(crop(imgs[i], b), cv2.COLOR_BGR2GRAY).astype(np.int16) for i in w['obs']]
        dif = max(float((np.abs(cs[k] - cs[k - 1]) > 40).mean()) for k in range(1, len(cs)))
        if dif >= 0.02:
            zone.append((b, len(w['obs'])))
    # fusione: zone quasi coincidenti e zone sulla stessa riga (etichetta + valore, gap orizzontale <= 14 px)
    bs = [b for b, n in sorted(zone, key=lambda x: -x[1])]
    fuso = True
    while fuso:
        fuso = False
        for i in range(len(bs)):
            for j in range(i + 1, len(bs)):
                a, b = bs[i], bs[j]
                yo = min(a[3], b[3]) - max(a[1], b[1])
                riga = yo >= 0.5 * min(a[3] - a[1], b[3] - b[1]) and a[0] <= b[2] + 14 and b[0] <= a[2] + 14
                u = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
                if (iou(a, b) > 0.4 or riga) and u[2] - u[0] <= 0.5 * W:
                    bs[i] = u; del bs[j]; fuso = True; break
            if fuso:
                break
    out = sorted(bs, key=lambda z: (z[1], z[0]))
    stat['widgets'] = len(widgets); stat['zone'] = len(out)
    return out


def letture(video, out):
    H, dur = dims(video)
    t0 = time.time(); stat = {}
    with Pool(NPROC, _init) as pool:
        zone = scoperta(video, H, pool, stat)
        stat['t_scoperta'] = round(time.time() - t0, 1)
        tasks = []; prev = {}
        for n, im in frames(video, 1, H):
            for zi, z in enumerate(zone):
                c = crop(im, z)
                if changed(prev.get(zi), c):
                    prev[zi] = c.copy()
                    tasks.append(('line:%d:%d' % (n, zi), prev[zi]))
        res = esegui_ocr(pool, tasks, out, chunksize=8)
        stat['rec'] = len(tasks)
    ev = {}
    for k, v in res.items():
        _, n, zi = k.split(':')
        ev.setdefault(int(zi), []).append([int(n), v])
    json.dump(dict(zone=zone, stato=ev, H=H, dur=dur, passo=1.0), open(os.path.join(out, 'ocr1.json'), 'w', encoding='utf-8'), ensure_ascii=False)
    stat['t_tot'] = round(time.time() - t0, 1)
    json.dump(stat, open(os.path.join(out, 'ocr1_stat.json'), 'w'))
    print('letture', stat, flush=True)


def azione(video, out):
    H, dur = dims(video)
    t0 = time.time(); stat = {}
    base = json.load(open(os.path.join(out, 'ocr1.json'), encoding='utf-8'))
    zone = base['zone']
    mv = json.load(open(os.path.join(out, 'mov.json')))
    sc = W / mv['W']
    cur = np.array(mv['cur']) if mv['cur'] else np.zeros((0, 3))
    # tempi d'azione: mezzi secondi + secondi interi dentro le fasi (con margine)
    win = [(f['t0'] - 0.5, f['t1'] + 1.0) for f in mv['fasi'] if f['mods'] + f['ist'] > 0]
    def in_win(t):
        return any(a <= t <= b for a, b in win)
    def cursor_at(t, fase):
        if len(cur):
            k = int(np.argmin(np.abs(cur[:, 0] - t)))
            if abs(cur[k, 0] - t) <= 1.0:
                return cur[k, 1] * sc, cur[k, 2] * sc
        for f in mv['fasi']:
            if f['t0'] - 0.5 <= t <= f['t1'] + 1.0 and f['bbox']:
                b = f['bbox']; return (b[0] + b[2]) / 2 * sc, (b[1] + b[3]) / 2 * sc
        return None
    with Pool(NPROC, _init) as pool:
        tasks = []; prevz = {}; prevframe = None
        for n, im in frames(video, 2, H):
            t = n / 2
            if n % 2 == 1 and in_win(t):                                 # mezzo secondo: lettura zone
                for zi, z in enumerate(zone):
                    c = crop(im, z); p = crop(prevframe, z)
                    if changed(p, c):
                        tasks.append(('line:%d:%d' % (n, zi), c.copy()))
            if in_win(t):
                p = cursor_at(t, None)
                if p is not None:
                    cx, cy = int(p[0]), int(p[1])
                    x0 = min(max(0, cx - 150), W - 300); y0 = min(max(0, cy - 75), H - 150)
                    tasks.append(('full:%d:%d:%d' % (n, x0, y0), im[y0:y0 + 150, x0:x0 + 300].copy()))
            prevframe = im
        res = esegui_ocr(pool, tasks, out, chunksize=4)
    stato = {}; fin = []
    for k, v in res.items():
        a = k.split(':')
        if a[0] == 'line':
            stato.setdefault(int(a[2]), []).append([int(a[1]) / 2, v])
        else:
            fin.append([int(a[1]) / 2, int(a[2]), int(a[3]), v])
    fin.sort()
    json.dump(dict(stato=stato, finestre=fin), open(os.path.join(out, 'ocr2.json'), 'w', encoding='utf-8'), ensure_ascii=False)
    stat['rec_zone'] = sum(1 for k in res if k.startswith('line')); stat['finestre'] = len(fin)
    stat['t_tot'] = round(time.time() - t0, 1)
    json.dump(stat, open(os.path.join(out, 'ocr2_stat.json'), 'w'))
    print('azione', stat, flush=True)


def regione_cambiata(a, b, H, vivo=None):
    """riquadro dei pixel cambiati tra a e b (cursore e rumore tolti), frazione dello schermo; None se troppo poco"""
    m = (np.abs(a.astype(np.int16) - b.astype(np.int16)).sum(2) > 60).astype(np.uint8)
    if vivo is not None:
        m[vivo] = 0
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))       # via puntini e bordi del cursore
    m = cv2.dilate(m, np.ones((15, 15), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(m)
    keep = [k for k in range(1, n) if st[k, 2] * st[k, 3] >= 0.004 * W * H]  # isole piccole = cursore, lampeggi
    if not keep:
        return None, 0.0
    x0 = min(st[k, 0] for k in keep); y0 = min(st[k, 1] for k in keep)
    x1 = max(st[k, 0] + st[k, 2] for k in keep); y1 = max(st[k, 1] + st[k, 3] for k in keep)
    return [int(x0), int(y0), int(x1), int(y1)], float(m.mean())


def schermate(video, out):
    H, dur = dims(video)
    t0 = time.time()
    stat = {'letture': 0, 'scartate_mosse': 0, 'fasce': 0, 'fasce_riusate': 0}
    cnt = None
    prev = None
    nf = 0
    for n, im in frames(video, 1, H):
        if prev is not None:
            d = (np.abs(im.astype(np.int16) - prev.astype(np.int16)).sum(2) > 60)
            cnt = d.astype(np.uint16) if cnt is None else cnt + d
            nf += 1
        prev = im
    vivo = cv2.dilate(
        ((cnt >= 0.3 * nf) if cnt is not None else np.zeros((H, W), bool)).astype(np.uint8),
        np.ones((9, 9), np.uint8),
    ).astype(bool)
    stat['vivo_frazione'] = round(float(vivo.mean()), 3)

    tasks = []
    eventi = []
    precedenti = {}
    riusi = 0
    letto = None
    prev = None
    for n, im in frames(video, 1, H):
        if letto is None:
            letto = im; prev = im; continue
        dd = (np.abs(im.astype(np.int16) - prev.astype(np.int16)).sum(2) > 60)
        dd[vivo] = False
        fermo = dd.mean() < 0.002
        prev = im
        if not fermo:
            continue
        box, fr = regione_cambiata(letto, im, H, vivo)
        if box is None or fr < 0.008:
            continue
        if (box[2] - box[0]) * (box[3] - box[1]) < 0.01 * W * H:
            continue

        b = [
            max(0, box[0] - 8),
            max(0, box[1] - 8),
            min(W, box[2] + 8),
            min(H, box[3] + 8),
        ]
        riferimenti = []
        for fy0, fy1 in (fasce_orizzontali(b[1], b[3]) if FASCE else [(b[1], b[3])]):
            ritaglio = im[fy0:fy1, b[0]:b[2]].copy()
            firma = impronta(ritaglio)
            zona = (b[0], b[2], fy0, fy1)
            precedente = precedenti.get(zona)
            if precedente is not None and precedente[0] == firma:
                chiave = precedente[1]
                riusi += 1
            else:
                chiave = 'box:%d:%d:%d:%d:%d' % (n, b[0], fy0, b[2], fy1)
                tasks.append((chiave, ritaglio))
                precedenti[zona] = (firma, chiave)
            riferimenti.append((fy0 - b[1], chiave))
        eventi.append((n, b, riferimenti))
        letto = im

    with Pool(NPROC, _init) as pool:
        res = esegui_ocr(
            pool,
            tasks,
            out,
            chunksize=1,
            richieste_aggiuntive=riusi,
        )

    letture = []
    for n, b, riferimenti in eventi:
        letture.append([n, b[0], b[1], ricomponi_fasce(riferimenti, res)])
    letture.sort()

    with open(os.path.join(out, 'ocr3.json'), 'w', encoding='utf-8') as file:
        json.dump(
            dict(letture=letture, W=W, H=H),
            file,
            ensure_ascii=False,
        )
    stat['letture'] = len(letture)
    stat['fasce'] = len(tasks) + riusi
    stat['fasce_riusate'] = riusi
    stat['t_tot'] = round(time.time() - t0, 1)
    with open(os.path.join(out, 'ocr3_stat.json'), 'w', encoding='utf-8') as file:
        json.dump(stat, file)
    print('schermate', stat, flush=True)


if __name__ == '__main__':
    fase = 'ocr_' + sys.argv[1]
    os.environ['DEMOBRIEF_TEMPI_FASE'] = fase
    os.environ['DEMOBRIEF_TEMPI_LAVORO'] = os.path.abspath(sys.argv[3])
    tempi.configura(fase, sys.argv[3], sys.argv[2])
    {'letture': letture, 'azione': azione, 'schermate': schermate}[sys.argv[1]](sys.argv[2], sys.argv[3])
