# -*- coding: utf-8 -*-
"""costruisci4.py <outdir> <titolo> -> racconto.md  (solo automatico; nessun testo scritto a mano)
Ingressi: parole.json (whisper a parola), ocr1.json/ocr2.json (OCR zone dinamiche 1 fps + 2 fps in azione + finestre cursore),
mov.json/eventi.txt (movimento), occhio.json (domande chiuse, coerenza su 2 immagini).
Riga per evento: [mm:ss] VOCE "..." | TESTO ... | MOTO ... | VISTA [vista] ...   (campi vuoti omessi)"""
import json, re, sys, os, difflib, collections
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import comune
import tempi
import fondi
from testi import *
OUT = sys.argv[1]
tempi.configura('costruisci', OUT)
J = lambda n: json.load(open(os.path.join(OUT, n), encoding='utf-8'))
mmss = lambda t: '%02d:%02d' % (int(t // 60), int(t % 60))
STAT = collections.OrderedDict()

# ---------------- utilita' testo: importate da testi.py

# ---------------- voce
W = J('parole.json')
fr = []; cur = []
for w in W:
    if cur and (w[0] - cur[-1][1] > 0.5 or cur[-1][2][-1] in '.?!'): fr.append(cur); cur = []
    cur.append(w)
if cur: fr.append(cur)
def ancora(t):
    best = None
    for i, f in enumerate(fr):
        for j, w in enumerate(f):
            d = 0 if w[0] <= t <= w[1] else min(abs(t - w[0]), abs(t - w[1]))
            if best is None or d < best[0]: best = (d, i, j)
    return best if best and best[0] <= 1.5 else None

# ---------------- OCR: serie per zona -> segmenti stabili
o1 = J('ocr1.json'); o2 = J('ocr2.json') if os.path.exists(os.path.join(OUT, 'ocr2.json')) else dict(stato={}, finestre=[])
mv = J('mov.json'); DUR = o1['dur']
obs = collections.defaultdict(list)
for zi, L in o1['stato'].items():
    for n, v in L: obs[zi].append((float(n), v))
for zi, L in o2['stato'].items():
    for n, v in L: obs[zi].append((float(n), v))
def testo_di(v):
    L = [x for x in v if x[1] > 0.5]
    return pulisci_ocr(' '.join(x[0] for x in L)), (sum(x[1] for x in L) / len(L) if L else 0)
def segmenti(seq, fine):
    """seq: [(t,testo,conf)] ordinata -> [(t0, dur, testo, n, conf)] ; stessa riga = stesse cifre e lettere simili"""
    segs = []
    for t, s, c in seq:
        if segs and (stessi(segs[-1]['v'][0], s, 0.75) or (not s and not segs[-1]['v'][0])):
            segs[-1]['v'].append(s); segs[-1]['t'].append(t); segs[-1]['c'].append(c)
        else: segs.append(dict(v=[s], t=[t], c=[c]))
    out = []
    for k, g in enumerate(segs):
        t0 = g['t'][0]; t1 = segs[k + 1]['t'][0] if k + 1 < len(segs) else fine
        txt = collections.Counter(g['v']).most_common(1)[0][0]
        cs = [c for v, c in zip(g['v'], g['c']) if v == txt]
        out.append((t0, t1 - t0, txt, len(g['t']), sum(cs) / len(cs)))
    return out
cand = []      # (t, testo, sorgente)
base = []      # testi presenti all'inizio (non si scaricano)
vocab = collections.Counter()
for w in W:
    x = re.sub(r'[^a-z]', '', w[2].lower())
    if len(x) >= 3: vocab[x] += 1
nstab = 0
for zi, L in obs.items():
    L.sort()
    seq = [(t,) + testo_di(v) for t, v in L]
    for t0, dur, txt, n, cf in segmenti(seq, DUR):
        if not utile(txt): continue
        if dur < 1.0 and n < 2: continue                      # compare e sparisce subito: rumore/hover
        nstab += 1
        if cf >= 0.8:
            for x in re.findall(r'[A-Za-z]{3,}', txt): vocab[x.lower()] += n
        if t0 < 1.5: base.append(txt)
        else: cand.append((t0, txt, 'z', cf))
# finestre sul cursore: tracce per riga
fin = o2['finestre']
tracce = []   # dict(testo, t[], pos)
for t, x0, y0, v in fin:
    for tx, cf, px, py in v:
        if cf < 0.5: continue
        tx = pulisci_ocr(tx)
        if not utile(tx): continue
        raw = (x0 + px, y0 + py)
        pos = (round(raw[0] / 12), round(raw[1] / 12))
        for tr in tracce:
            if t - tr['t'][-1] <= 1.0 and abs(tr['raw'][0] - raw[0]) <= 60 and abs(tr['raw'][1] - raw[1]) <= 60 and stessi(tr['txt'], tx, 0.75):
                tr['t'].append(t); tr['c'].append(cf); tr['raw'] = raw; break
        else: tracce.append(dict(txt=tx, t=[t], pos=pos, c=[cf], raw=raw))
fasi = [(f['t0'], f['t1']) for f in mv['fasi'] if f['mods'] + f['ist'] > 0]
def fase_di(t):
    for k, (a, b) in enumerate(fasi):
        if a - 1.0 <= t <= b + 1.5: return k
    return None
posn = collections.defaultdict(set)
for tr in tracce:
    k = fase_di(tr['t'][0])
    posn[(tr['pos'], let(tr['txt']))].add(k)
nwin = 0
for tr in tracce:
    dur = tr['t'][-1] - tr['t'][0] + 0.5
    if dur < 1.0: continue                                    # compare e sparisce in < 1 s: hover dei menu
    if len(posn[(tr['pos'], let(tr['txt']))]) >= 3: continue  # sempre nello stesso punto in fasi diverse = interfaccia fissa
    if sum(tr['c']) / len(tr['c']) >= 0.8:
        for x in re.findall(r'[A-Za-z]{3,}', tr['txt']): vocab[x.lower()] += 1
    cand.append((tr['t'][0], tr['txt'], 'w', sum(tr['c']) / len(tr['c']))); nwin += 1
STAT['segmenti_zona_stabili'] = nstab; STAT['tracce_finestra_stabili'] = nwin

# ---------------- correzione refusi: vocabolario = voce + testi OCR stabili, distanza <= 2
def correggi(txt):
    def f(m):
        w = m.group(0); lw = w.lower()
        if len(lw) < 4 or vocab[lw] >= 3: return w
        lim = 1 if len(lw) < 7 else 2
        best = None
        for v, c in vocab.items():
            if c >= 3 and v[0] == lw[0] and abs(len(v) - len(lw)) <= lim:
                d = lev(lw, v)
                if d <= lim and (best is None or (d, -c) < (best[0], -best[1])): best = (d, c, v)
        if not best: return w
        STAT['refusi_corretti'] = STAT.get('refusi_corretti', 0) + 1
        r = best[2]; return r.capitalize() if w[0].isupper() else r
    return re.sub(r'[A-Za-z]{4,}', f, txt)

# ---------------- deduplica, baseline, catene numeriche
cand.sort()
base = [b for b in base]
def num_tokens(s): return re.findall(r'\d[\d.,/]*', s)
ev = []
def noto(txt, q):
    w = [x.lower() for x in re.findall(r'[A-Za-z]{3,}', txt)]
    return bool(w) and sum(vocab[x] >= 3 for x in w) >= q * len(w)
for t, txt, src, cf in cand:
    txt = correggi(txt)
    if not (cf >= 0.85 or (cf >= 0.72 and noto(txt, 0.5)) or noto(txt, 0.75)): STAT['scartati_bassa_fiducia'] = STAT.get('scartati_bassa_fiducia', 0) + 1; continue
    if any(stessi(txt, b, 0.85) for b in base): STAT['scartati_baseline'] = STAT.get('scartati_baseline', 0) + 1; continue
    done = False
    for e in reversed(ev):
        if t - e['t1'] > 120: break
        if ratio(txt, e['txt']) >= 0.85:
            if dig(txt) == dig(e['txt']) or txt in e['vals']:
                e['t1'] = t; done = True; STAT['doppioni'] = STAT.get('doppioni', 0) + 1; break
            if t - e['t1'] <= 15 and len(num_tokens(txt)) == len(num_tokens(e['txt'])) and num_tokens(txt):
                e['vals'].append(txt); e['t1'] = t; done = True; break
    if not done: ev.append(dict(t=t, t1=t, txt=txt, vals=[txt], src=src))
def testo_finale(e):
    if len(e['vals']) == 1: return e['txt']
    toks = [num_tokens(v) for v in e['vals']]
    seqs = []
    for p in range(len(toks[0])):
        s = []
        for tk in toks:
            if not s or s[-1] != tk[p]: s.append(tk[p])
        if len(s) > 4: s = s[:2] + ['..'] + s[-1:]
        seqs.append('>'.join(s))
    it = iter(seqs)
    return re.sub(r'\d[\d.,/]*', lambda m: next(it), e['txt'], count=len(seqs))
for e in ev: e['f'] = testo_finale(e)
STAT['eventi_testo'] = len(ev)

# ---------------- movimento (da eventi.txt, come v3)
mov = []
for l in open(os.path.join(OUT, 'eventi.txt'), encoding='utf-8'):
    m = re.match(r'\[([\d:.]+)(?:-([\d:.]+))?\]\s*(.*)', l.strip())
    if not m: continue
    ts = lambda s: int(s.split(':')[0]) * 60 + float(s.split(':')[1])
    a = ts(m.group(1)); b = ts(m.group(2)) if m.group(2) else a; x = m.group(3)
    d = []
    if 'modifica mesh' in x:
        g = re.search(r'(\d+) gest', x); d.append('mesh %sg' % (g.group(1) if g else '1'))
    n = re.search(r'orbita/zoom (\d+)x \((\d+)s\)', x)
    if n and n.group(2) != '0': d.append('orbita %ss' % n.group(2))
    c = max(len(re.findall(r'@([\d:.,]+)', x)[0].split(',')) if '@' in x else 0, len(re.findall(r'clic \d', x)))
    if c: d.append('clic%d' % c)
    if x.startswith('pannello dx') or 'pannello dx:' in x[:12]: d.append('pannello dx')
    if 'taglio' in x: d.append('taglio vista')
    if not d: continue
    mov.append((a, b, d))

# ---------------- occhio: coerenza su 2 immagini vicine
vista = []
STAT['occhio_chiamate'] = 0; STAT['occhio_scartate_incoerenza'] = 0; STAT['occhio_accettate'] = 0; STAT['occhio_etichette_non_confermate'] = 0
if os.path.exists(os.path.join(OUT, 'occhio.json')):
    oc = J('occhio.json')
    for r in oc['res']:
        if 'r' not in r: continue
        STAT['occhio_chiamate'] += 2
        a, b = r['r'][0]['p'], r['r'][1]['p']
        if not a or not b or a['NUOVA_GEOMETRIA'] != b['NUOVA_GEOMETRIA'] or a['FORMA'] != b['FORMA']:
            STAT['occhio_scartate_incoerenza'] += 2; continue
        if a['NUOVA_GEOMETRIA'] != 'si' or a['FORMA'] == 'nessuna': continue          # nulla da dire
        STAT['occhio_accettate'] += 2
        dove = a['DOVE'] if a['DOVE'] == b['DOVE'] else ''
        et = a['ETICHETTA_VICINO_CURSORE'] if ratio(a['ETICHETTA_VICINO_CURSORE'], b['ETICHETTA_VICINO_CURSORE']) >= 0.8 else ''
        if et:
            # l'etichetta vale solo se l'OCR la conferma vicino a quell'istante; mai nomi di comandi dall'occhio
            ok = [x for x in ev if abs(x['t'] - r['t1']) <= 4 and (stessi(x['f'], et, 0.7) or let(et) in let(x['f']))]
            if not ok: STAT['occhio_etichette_non_confermate'] += 1; et = ''
        vista.append((r['t1'], a['FORMA'], dove, et))

# ---------------- schermate nuove (ocr3): celle non viste di recente, per riquadro, con la posizione sullo schermo
scr = []
STAT['schermate_lette'] = 0; STAT['schermate_righe_nuove'] = 0
if os.path.exists(os.path.join(OUT, 'ocr3.json')):
    o3 = J('ocr3.json'); SW, SH = o3['W'], o3['H']
    visto = set(let(b) + dig(b) for b in base)
    for zi, L in obs.items():                              # le zone dinamiche le racconta gia' TESTO
        for t, v in L: visto.add(let(testo_di(v)[0]) + dig(testo_di(v)[0]))
    def celle_di(v):
        it = sorted([(y0b, x0b, x1b, pulisci_ocr(tx)) for tx, cf, x0b, y0b, x1b, y1b in v if cf >= 0.75], key=lambda r: (r[0], r[1]))
        rows = []
        for py, px, qx, tx in it:                           # stessa riga = stessa y (+-8 px)
            if rows and abs(rows[-1][0] - py) <= 8: rows[-1][1].append((px, qx, tx))
            else: rows.append([py, [(px, qx, tx)]])
        out = []
        for py, cel in rows:
            cel.sort(); celle = []
            for px, qx, tx in cel:                          # cella = testi vicini (spazio < 25 px); colonne separate
                if celle and px - celle[-1][1] < 25: celle[-1] = [celle[-1][0] + ' ' + tx, qx]
                else: celle.append([tx, qx])
            out.append([c.strip() for c, _ in celle if len(let(c)) + len(dig(c)) >= 2])
        return out
    tutte = [celle_di(v) for t, x0, y0, v in o3['letture']]
    # interfaccia fissa = celle presenti in molte letture (menu, intestazioni): mai raccontate
    # confronto tollerante: stesse cifre e lettere simili, o una contenuta nell'altra ('Size 10.5' ~ 'Draw Size 10.5')
    def simili(l, d, l2, d2):
        if d != d2: return False
        if l == l2: return True
        if min(len(l), len(l2)) < 4: return False
        if l in l2 or l2 in l: return True
        sm = difflib.SequenceMatcher(None, l, l2)
        return sm.quick_ratio() >= 0.85 and sm.ratio() >= 0.85
    insiemi = [set((let(c), dig(c)) for r in R for c in r) for R in tutte]
    soglia = max(3, 0.3 * len(tutte))
    fisse = set()
    for i, S in enumerate(insiemi):
        for l, d in S:
            if l + d in fisse: continue
            n = sum(1 for S2 in insiemi if (l, d) in S2 or any(simili(l, d, l2, d2) for l2, d2 in S2))
            if n >= soglia: fisse.add(l + d)
    recenti = collections.deque(maxlen=2)                  # novita' rispetto alle ultime 2 letture: una tabella che torna si racconta di nuovo
    def gia_vista(c):
        l, d = let(c), dig(c)
        if l + d in fisse or l + d in visto: return True
        if any(simili(l, d, l2, d2) for R in recenti for l2, d2 in R): return True
        # gia' raccontata da poco (stesse cifre, ultimi DETTO_S secondi): un campo che non cambia non si ripete;
        # oltre la finestra si ripete, perche' una finestra di dialogo che lo cita ne cambia il senso
        return any(t_ora - t2 <= DETTO_S and ((l, d) == (l2, d2) or simili(l, d, l2, d2)) for (l2, d2), t2 in detto.items() if d2 == d)
    detto = {}
    DETTO_S = 45
    URL = re.compile(r'https?://|www\.|\.com/|/web#', re.I)
    for (t, x0, y0, v), R in zip(o3['letture'], tutte):
        STAT['schermate_lette'] += 1
        t_ora = float(t)
        nuove = []; questa = []
        for r in R:
            nr = []
            for c in r:
                if not URL.search(c) and not gia_vista(c):
                    nr.append(c[:80]); detto[(let(c), dig(c))] = t_ora
                questa.append((let(c), dig(c)))
            if nr: nuove.append(' · '.join(nr)[:140])
        recenti.append(questa)
        if not nuove: continue
        xs = [x0 + q[2] for q in v] + [x0 + q[4] for q in v]; ys = [y0 + q[3] for q in v] + [y0 + q[5] for q in v]
        bw = (max(xs) - min(xs)) / SW; bh = (max(ys) - min(ys)) / SH; cx = (max(xs) + min(xs)) / 2 / SW; cy = (max(ys) + min(ys)) / 2 / SH
        dove = ('pagina' if bw * bh >= 0.5 else 'finestra al centro' if 0.3 <= cx <= 0.7 and 0.25 <= cy <= 0.75 else
                'pannello ' + ('a destra' if cx > 0.7 else 'a sinistra' if cx < 0.3 else 'in alto' if cy < 0.25 else 'in basso'))
        STAT['schermate_righe_nuove'] += len(nuove)
        scr.append((float(t), dove, ' / '.join(nuove[:20])[:700]))

FX = fondi.Fusione(OUT, W, fr, ev, scr, mov, DUR)
ev = FX.tieni('TESTO', ev)
scr = FX.tieni('SCHERMO', scr)
mov = FX.tieni('MOTO', mov)
vista = FX.tieni('VISTA', vista)

# ---------------- assemblaggio per frase
righe = {i: dict(ev=[], mov=[], vis=[], scr=[]) for i in range(len(fr))}
isol = []
for e in ev:
    a = ancora(e['t'])
    (righe[a[1]]['ev'].append(e) if a else isol.append(('ev', e['t'], e)))
for a, b, d in mov:
    an = ancora(a + 0.5)
    (righe[an[1]]['mov'].append((a, b, d)) if an else isol.append(('mov', a, (a, b, d))))
for v in vista:
    an = ancora(v[0])
    (righe[an[1]]['vis'].append(v) if an else isol.append(('vis', v[0], v)))
for v in scr:
    an = ancora(v[0])
    (righe[an[1]]['scr'].append(v) if an else isol.append(('scr', v[0], v)))
def fmt_scr(L): return '; '.join('%s %s: %s' % (mmss(t), d, x) for t, d, x in L)
def fmt_ev(L):
    byt = collections.OrderedDict()
    for e in sorted(L, key=lambda e: e['t']):
        byt.setdefault(mmss(e['t']), []).append('"%s"' % e['f'][:60])
    return '; '.join('%s %s' % (k, ', '.join(v)) for k, v in byt.items())
def fmt_mov(L): return '; '.join('%s%s %s' % (mmss(a), '-' + mmss(b) if b - a >= 3 else '', ' '.join(d)) for a, b, d in L)
def fmt_vis(L):
    out = []
    for t, forma, dove, et in L:
        out.append('[vista] %s %s%s%s' % (mmss(t), forma, ' (%s)' % dove if dove and dove != 'vicino al cursore' else '', ' etichetta "%s"' % et if et else ''))
    return '; '.join(out)
SKIP = re.compile(r"^(thanks for watching|if you've got any questions|grazie per la visione|grazie per aver guardato)", re.I)
def pulisci(s):
    s = re.sub(r'(\d) \.(\d)', r'\1.\2', s)
    s = re.sub(r'^(So|Now|And|But|Which|Obviously),? ', '', s)
    s = re.sub(r'\b(obviously|actually|a little bit|a bit|really|quite|very|you can see (that )?|as you can imagine|a touch|according to the camera)\b ?', '', s)
    return s.strip()
lines = []
allr = ([(fr[i][0][0], i, None) for i in range(len(fr))] +
        [(t, None, (k, t, v)) for k, t, v in isol] +
        FX.isolati())
for t, i, iso in sorted(allr, key=lambda x: x[0]):
    if i is not None:
        text = ' '.join(x[2] for x in fr[i]); r = righe[i]
        e = fmt_ev(r['ev']); m = fmt_mov(r['mov']); vv = fmt_vis(r['vis']); sc = fmt_scr(r['scr'])
        voce = FX.voce(i, pulisci(text))
        if SKIP.match(text) and not (e or m or vv or sc or FX.sezioni(i)): continue
        lines.extend(FX.righe_prima(i))
        p = []
        if FX.attiva('VOCE'):
            p.append('[%s] VOCE "%s"' % (mmss(t), voce) if not SKIP.match(text) else '[%s]' % mmss(t))
        elif e or m or vv or sc or FX.sezioni(i):
            p.append('[%s]' % mmss(t))
        if e: p.append('TESTO ' + e)
        if sc: p.append('SCHERMO ' + sc)
        if m: p.append('MOTO ' + m)
        if vv: p.append('VISTA ' + vv)
        p += FX.sezioni(i)                     # canali nuovi con stampa 'sezione' (gancio 1.4.5)
        lines.append(' | '.join(p))
    else:
        k, t, v = iso
        if k == 'mov': lines.append('[%s] MOTO %s' % (mmss(t), fmt_mov([v])))
        elif k == 'scr': lines.append('[%s] SCHERMO %s' % (mmss(t), fmt_scr([v])))
        elif k == 'ev': lines.append('[%s] TESTO %s' % (mmss(t), fmt_ev([v])))
        elif k == 'fx': lines.append(v)            # evento di un canale nuovo senza frase vicina, gia' formattato
        else: lines.append('[%s] VISTA %s' % (mmss(t), fmt_vis([v])))
def solo_voce(l): return ' | SCHERMO' not in l and ' | TESTO' not in l and ' | MOTO' not in l and ' | VISTA' not in l and '[→' not in l and '*' not in l and 'VOCE "' in l
M = []
for l in lines:
    if M and solo_voce(l) and solo_voce(M[-1]): M[-1] = M[-1][:-1] + ' ' + l.split('VOCE "', 1)[1]
    else: M.append(l)
lines = [l for l in M if not (solo_voce(l) and len(l.split('VOCE "', 1)[1].split()) <= 5)]
meta = J('meta.json') if os.path.exists(os.path.join(OUT, 'meta.json')) else {}
def conta_token(s):
    try:
        import tiktoken
        return len(tiktoken.get_encoding('cl100k_base').encode(s))
    except Exception:                       # senza rete / senza cache: stima 1 token ogni 4 caratteri
        return len(s) // 4
usate = []
if W: usate.append('voce (%d parole)' % len(W))
if ev: usate.append('OCR (%d eventi)' % len(ev))
if mov: usate.append('moto (%d)' % len(mov))
if vista: usate.append('vista (%d)' % len(vista))
if scr: usate.append('schermate (%d)' % len(scr))
usate += FX.usate()
# scorciatoie nella forma unica Tasto+Tasto ('Shift -A', 'ctrl - z' -> 'Shift+A', 'Ctrl+Z')
SCORC = re.compile(r'\b(Shift|Ctrl|Control|Alt|Cmd)\s*[-+]\s*([A-Za-z0-9])\b', re.I)
lines = [SCORC.sub(lambda m: m.group(1).capitalize().replace('Control', 'Ctrl') + '+' + m.group(2).upper(), l) for l in lines]
corpo = '\n'.join(lines) + '\n'
def intestazione(tok):
    titolo = meta.get('titolo') or (sys.argv[2] if len(sys.argv) > 2 else 'Video')
    r1 = '# %s - durata %s - token stimati ~%d' % (titolo, mmss(DUR), tok)
    vf = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'version.txt')
    ver = open(vf, encoding='utf-8').read().strip() if os.path.exists(vf) else '?'
    r2 = ('AD_Moviola ' + ver + '. ') + 'Fonti usate: %s.%s' % (', '.join(usate) or 'nessuna', (' ATTENZIONE: ' + meta['vista_nota']) if meta.get('vista_nota') else '')
    r3 = ('Legenda: [mm:ss] istante; VOCE = parlato; TESTO = scritte che cambiano a schermo (OCR); SCHERMO = righe nuove lette quando lo schermo si ferma su una vista nuova (dove: pagina, finestra, pannello); MOTO = azioni (mesh Ng = gesti sulla geometria, orbita Ns = rotazione vista, clicN = clic, taglio vista = cambio scena); '
 'VISTA = geometria nuova vista dall\'occhio (forma, zona). Immagini a richiesta: %s.' % meta.get('indice_fotogrammi', 'fotogrammi/indice.md'))
    if FX.legenda(): r3 += ' ' + FX.legenda() + '.'
    return '\n'.join((r1, r2, r3)) + '\n'
STAT.update(FX.stat)
tok = conta_token(intestazione(0) + corpo)
for _ in range(2): tok = conta_token(intestazione(tok) + corpo)
txt = intestazione(tok) + corpo
open(os.path.join(OUT, 'racconto.md'), 'w', encoding='utf-8').write(txt)
STAT['token'] = tok; STAT['token_corpo'] = conta_token(corpo); STAT['token_al_secondo'] = round(tok / DUR, 1); STAT['righe'] = len(lines)
json.dump(STAT, open(os.path.join(OUT, 'costruisci_stat.json'), 'w'))
print(dict(STAT))

