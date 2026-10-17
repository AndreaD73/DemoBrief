# -*- coding: utf-8 -*-
"""movimento2.py <video> [outdir] -> eventi.txt (v2: cursore a template, fasi compatte)
Solo calcolo (opencv+numpy). Nessun modello."""
import sys, os, time, subprocess, cv2, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import comune
import tempi
FPS = 10
W = 640
ZONE = [("barra-superiore", 0, 0, 1, .06), ("timeline-inferiore", 0, .90, .79, 1),
        ("pannello-destro", .79, .06, 1, 1), ("barra-strumenti-sx", 0, .06, .06, .90),
        ("viewport-sinistra", .06, .06, .36, .90), ("viewport-centro", .36, .06, .66, .90),
        ("viewport-destra", .66, .06, .79, .90)]
UI = ("pannello-destro", "barra-superiore", "timeline-inferiore", "barra-strumenti-sx")
SHORT = {"viewport-sinistra": "viewport sx", "viewport-centro": "viewport centro", "viewport-destra": "viewport dx",
         "pannello-destro": "pannello dx", "barra-superiore": "barra sup", "timeline-inferiore": "timeline",
         "barra-strumenti-sx": "barra strumenti"}
CUR_R = 16
DEBUG = os.environ.get("MOV_DEBUG")
FASI = []
CURS = []


def zona_di(x, y, w, h):
    fx, fy = x / w, y / h
    for n, a, b, c, d in ZONE:
        if a <= fx < c and b <= fy < d:
            return n
    return "viewport-centro"


def tf(t):
    return "%02d:%02d" % (int(t // 60), int(t % 60))


def tf1(t):
    return "%02d:%04.1f" % (int(t // 60), t % 60)


def _parametri_video(video):
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
    h = int(fotogramma.shape[0] * W / fotogramma.shape[1])
    colore = cv2.resize(fotogramma, (W, h), interpolation=cv2.INTER_AREA)
    grigio = cv2.cvtColor(colore, cv2.COLOR_BGR2GRAY)
    return colore, grigio


def _load_cv2(video, step=None, fps=None, motivo=None):
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
            ok, f = cap.retrieve()
            if not ok:
                break
            colore, grigio = _riduci(f)
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
    filtro = "select=not(mod(n\\,%d)),scale=in_color_matrix=bt601:out_color_matrix=bt601" % step
    comando = [
        comune.ffmpeg(), "-v", "error", "-i", video,
        "-vf", filtro, "-fps_mode", "passthrough",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-"
    ]
    processo = subprocess.Popen(
        comando,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
    )
    dimensione = larghezza * altezza * 3
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
            f = np.frombuffer(blocco, dtype=np.uint8).reshape(
                altezza, larghezza, 3
            )
            colore, grigio = _riduci(f)
            if primo_colore is None:
                primo_colore = colore
            gray.append(grigio)
        errore = processo.stderr.read().decode("utf-8", errors="replace").strip()
        codice = processo.wait()
    except BaseException:
        processo.kill()
        processo.wait()
        raise
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


# ---------- cursore: template matching ----------
def bin_white(g):
    return (g >= 205).astype(np.float32)


def derive_template(gray):
    """Modello del puntatore dai fotogrammi in cui l'unico blob in movimento e' una freccia bianca con bordo nero."""
    cands = []
    for k in range(1, len(gray)):
        d = (cv2.absdiff(gray[k], gray[k - 1]) > 18).astype(np.uint8)
        if d.sum() < 5 or d.mean() * 100 > 0.4:
            continue
        with tempi.blocco('componenti'):
            n, lab, st, cen = cv2.connectedComponentsWithStats(cv2.dilate(d, np.ones((3, 3), np.uint8)))
        for j in range(1, n):
            x, y, w, h, a = st[j]
            if 6 <= w <= 14 and 8 <= h <= 18 and x > 1 and y > 1:
                cr = gray[k][y:y + h, x:x + w]
                wh = int((cr >= 215).sum())
                bl = int((cr < 55).sum())
                if wh >= 10 and bl >= 4:
                    cands.append((k, x, y, w, h, wh))
        if len(cands) >= 60:
            break
    # GENERALE: un puntatore si muove; i candidati che ricompaiono sempre nello stesso punto sono icone/overlay fissi
    from collections import Counter
    rip = Counter((c[1] // 4, c[2] // 4) for c in cands)
    cands = [c for c in cands if rip[(c[1] // 4, c[2] // 4)] <= 2][:12]
    best, bs = None, -1
    for (k, x, y, w, h, wh) in cands:
        t = bin_white(gray[k][y - 1:y + h + 1, x - 1:x + w + 1])
        if t.shape[0] < 6 or t.sum() < 8:
            continue
        sc = 0
        for (k2, x2, y2, w2, h2, _) in cands:
            reg = bin_white(gray[k2][max(0, y2 - 20):y2 + h2 + 20, max(0, x2 - 20):x2 + w2 + 20])
            if reg.shape[0] < t.shape[0] or reg.shape[1] < t.shape[1]:
                continue
            with tempi.blocco('cursore_template'):
                r = cv2.matchTemplate(reg, t, cv2.TM_CCOEFF_NORMED)
            if r.max() > 0.75:
                sc += 1
        if sc > bs:
            bs, best = sc, t
    return best, len(cands), bs


def track_cursor(gray, tpl, thr=0.72):
    """Picchi del template per fotogramma; le posizioni che compaiono in >=15% dei fotogrammi
    sono icone statiche dell'interfaccia (play, ecc.) e si scartano."""
    peaks = []
    for g in gray:
        with tempi.blocco('cursore_template'):
            r = cv2.matchTemplate(bin_white(g), tpl, cv2.TM_CCOEFF_NORMED)
        pk = []
        for _ in range(5):
            _, mx, _, loc = cv2.minMaxLoc(r)
            if mx < thr:
                break
            pk.append((loc[0] + 1.0, loc[1] + 1.0, float(mx)))
            r[max(0, loc[1] - 8):loc[1] + 9, max(0, loc[0] - 8):loc[0] + 9] = -1
        peaks.append(pk)
    cnt = {}
    for pk in peaks:
        for x, y, _ in pk:
            key = (int(x // 4), int(y // 4))
            cnt[key] = cnt.get(key, 0) + 1
    stat = {k for k, v in cnt.items() if v >= 0.15 * len(gray)}
    stat |= {(a + da, b + db) for a, b in list(stat) for da in (-1, 0, 1) for db in (-1, 0, 1)}
    pos = []
    for pk in peaks:
        ok = [p for p in pk if (int(p[0] // 4), int(p[1] // 4)) not in stat]
        pos.append(max(ok, key=lambda p: p[2]) if ok else None)
    return pos


# ---------- flusso ottico ----------
def flow_stats(a, b):
    with tempi.blocco('flusso_ottico'):
        fl = cv2.calcOpticalFlowFarneback(a, b, None, .5, 2, 13, 2, 5, 1.1, 0)
    mag = np.linalg.norm(fl, axis=2)
    mv = mag > 0.3
    hh, ww = mag.shape
    fmov = float(mv.mean())
    if mv.sum() < 15:
        return dict(fmov=fmov, R=0, mmag=0, ext=0, med=(0, 0), div=0, curl=0)
    vx, vy = fl[..., 0][mv], fl[..., 1][mv]
    ang = np.arctan2(vy, vx)
    R = float(np.hypot(np.cos(ang).mean(), np.sin(ang).mean()))
    yy, xx = np.nonzero(mv)
    ext = (np.percentile(xx, 95) - np.percentile(xx, 5)) * (np.percentile(yy, 95) - np.percentile(yy, 5)) / (ww * hh)
    cx, cy = xx - ww / 2, yy - hh / 2
    rr = np.sqrt(cx ** 2 + cy ** 2) + 1e-6
    div = float(((vx * cx + vy * cy) / rr).mean())
    curl = float(((-vx * cy + vy * cx) / rr).mean())
    return dict(fmov=fmov, R=R, mmag=float(np.median(mag[mv])), ext=float(ext),
                med=(float(np.median(vx)), float(np.median(vy))), div=div, curl=curl)


def analizza(video):
    t0 = time.time()
    color, gray, dt = load(video)
    n = len(gray)
    H = gray[0].shape[0]
    vx0, vy0, vx1, vy1 = int(.06 * W), int(.06 * H), int(.79 * W), int(.90 * H)
    tpl, ncand, conf = derive_template(gray)
    cur = track_cursor(gray, tpl) if tpl is not None else [None] * n

    def curdisk(m, k):
        c = cur[k]
        if c is not None:
            cv2.circle(m, (int(c[0] + 4), int(c[1] + 6)), CUR_R, 0, -1)

    blur = [cv2.GaussianBlur(g, (3, 3), 0) for g in gray]
    small = [cv2.resize(g[vy0:vy1, vx0:vx1], (160, int(160 * (vy1 - vy0) / (vx1 - vx0)))) for g in gray]
    rows = []
    K7 = np.ones((7, 7), np.uint8)
    for k in range(1, n):
        d = cv2.absdiff(blur[k], blur[k - 1])
        m = (d > 18).astype(np.uint8)
        curdisk(m, k - 1)
        curdisk(m, k)
        m[int(.945 * H):, :] = 0                       # barra di stato (suggerimenti tasti)
        m[int(.68 * H):int(.90 * H), int(.62 * W):int(.78 * W)] = 0   # overlay screencast (tasti/mouse)
        area = m.mean() * 100
        varea = m[vy0:vy1, vx0:vx1].mean() * 100
        energy = float(d.mean())
        zone, comp, cpx = None, None, 0
        if m.sum() > 12:
            with tempi.blocco('componenti'):
                nc, lab, st, cen = cv2.connectedComponentsWithStats(cv2.dilate(m, K7))
            if nc > 1:
                cnt = np.bincount(lab[m > 0], minlength=nc)
                cnt[0] = 0
                j = int(cnt.argmax())
                cpx = int(cnt[j])
                x, y, w_, h_, _ = st[j]
                zone = zona_di(cen[j][0], cen[j][1], W, H)
                comp = (int(x), int(y), int(x + w_), int(y + h_), (float(cen[j][0]), float(cen[j][1])))
        fs = flow_stats(small[k - 1], small[k])
        rows.append(dict(t=(k - 1) * dt, k=k, area=area, varea=varea, energy=energy, zone=zone, comp=comp,
                         cpx=cpx, cpct=100.0 * cpx / (W * H), **fs))
    return color, gray, rows, cur, (ncand, conf, None if tpl is None else tpl.shape), dt, t0


def classifica(rows):
    for r in rows:
        a, va = r['area'], r['varea']
        if a > 60:
            c = "salto"
        elif a < 0.25:
            c = "fermo"
        elif r['zone'] in UI:
            c = "ui"
        elif r['fmov'] >= 0.15 and r['ext'] >= 0.30 and r['mmag'] > 0.3:
            c = "vista"
        else:
            c = "mod"
        r['c'] = c
    for r in rows:
        if r['c'] == "vista":
            mx, my = r['med']
            if np.hypot(mx, my) > 0.7 and r['R'] > 0.8:
                r['sub'] = "pan"
            else:
                r['sub'] = "orbita/zoom"


def eventi(rows, cur, dt, H=360):
    n = len(cur)
    segs = []
    k = 0
    while k < len(rows):
        c = rows[k]['c']
        if c == "fermo":
            k += 1
            continue
        j = k
        while j + 1 < len(rows):
            if rows[j + 1]['c'] == c:
                j += 1
                continue
            nxt = [q for q in range(j + 2, min(len(rows), j + 4)) if rows[q]['c'] == c]
            if nxt and all(rows[q]['c'] in ("fermo", c) for q in range(j + 1, nxt[0])):
                j = nxt[0]
                continue
            break
        segs.append((k, j, c))
        k = j + 1
    evs = []
    for k, j, c in segs:
        seg = rows[k:j + 1]
        ta, tb = seg[0]['t'], seg[-1]['t'] + dt
        me = float(np.mean([s['energy'] for s in seg]))
        inten = "alta" if me > 6 else "media" if me > 2 else "bassa"
        bbs = [s['comp'][:4] for s in seg if s['comp']]
        bb = [min(b[0] for b in bbs), min(b[1] for b in bbs), max(b[2] for b in bbs), max(b[3] for b in bbs)] if bbs else None
        zs = [s['zone'] for s in seg if s['zone']]
        zone = max(set(zs), key=zs.count) if zs else "viewport-centro"
        ma = float(np.mean([s['area'] for s in seg]))
        if c == "salto":
            evs.append(dict(t0=ta, t1=tb, kind="salto", zone="viewport", inten=inten, bbox=bb))
        elif c == "vista":
            if tb - ta < 0.3:
                quiet = rows[max(0, k - 1)]['c'] == "fermo" and rows[min(len(rows) - 1, j + 1)]['c'] == "fermo"
                if quiet and ma > 0.6:      # 1-2 fotogrammi isolati: cambio istantaneo, non movimento di vista
                    evs.append(dict(t0=ta, t1=tb, kind="mod-istantaneo", zone=zone, inten=inten, bbox=bb))
                continue
            subs = [s['sub'] for s in seg if 'sub' in s]
            evs.append(dict(t0=ta, t1=tb, kind="vista", sub=max(set(subs), key=subs.count), zone="viewport", inten=inten))
        elif c == "ui":
            if len(seg) <= 2 and ma > 0.4:
                evs.append(dict(t0=ta, t1=tb, kind="ui-istantaneo", zone=zone, inten=inten))
            elif ma >= 0.6:
                evs.append(dict(t0=ta, t1=tb, kind="ui-graduale", zone=zone, inten=inten))
        else:
            quiet = rows[max(0, k - 1)]['c'] == "fermo" and rows[min(len(rows) - 1, j + 1)]['c'] == "fermo"
            if len(seg) <= 2 and ma > 0.6 and quiet:
                evs.append(dict(t0=ta, t1=tb, kind="mod-istantaneo", zone=zone, inten=inten, bbox=bb))
            elif len(seg) >= 3 and ma >= 0.9:
                evs.append(dict(t0=ta, t1=tb, kind="mod", zone=zone, inten=inten, bbox=bb))
    clicks = []
    kk = 1
    while kk < n:
        if cur[kk] is None:
            kk += 1
            continue
        s = kk
        while kk + 1 < n and cur[kk + 1] is not None and np.hypot(cur[kk + 1][0] - cur[s][0], cur[kk + 1][1] - cur[s][1]) < 4:
            kk += 1
        if (kk - s + 1) * dt >= 0.3 - 1e-9:
            tp = kk * dt
            p = cur[s]
            hit = None
            for q in range(kk, min(len(rows), kk + 1 + int(round(0.5 / dt)))):
                r = rows[q]
                if r['c'] in ("fermo", "vista", "salto") or r['comp'] is None or r['area'] < 0.3:
                    continue
                cx, cy = r['comp'][4]
                dist = float(np.hypot(cx - p[0], cy - p[1]))
                pz = zona_di(p[0], p[1], W, H)
                if dist < 60 or (r['zone'] in UI and pz != r['zone']) or (r['zone'] in UI and dist < 120):
                    hit = (r, dist)
                    break
            if hit:
                clicks.append(dict(t=tp, x=int(p[0]), y=int(p[1]), zone=zona_di(p[0], p[1], W, H),
                                   eff=hit[0]['zone'], near=hit[1] < 60, area=hit[0]['area']))
        kk += 1
    cl2 = []
    for c in clicks:
        if cl2 and c['t'] - cl2[-1]['t'] < 0.4:
            continue
        cl2.append(c)
    return evs, cl2


def rng(a, b):
    return "[%s-%s]" % (tf(a), tf(b)) if b - a >= 2.0 else "[%s-%s]" % (tf1(a), tf1(b)) if b - a >= 0.3 else "[%s]" % tf1(a)


def componi(evs, clicks):
    """Eventi -> FASI (righe compatte). Niente clic o cambio istantaneo perso."""
    out = []
    # salti: cluster entro 0.7 s
    salti = [e for e in evs if e['kind'] == "salto"]
    cl = []
    for e in salti:
        if cl and e['t0'] - cl[-1][1] <= 0.7:
            cl[-1][1] = e['t1']
            cl[-1][2] += 1
        else:
            cl.append([e['t0'], e['t1'], 1])
    for a, b, nn in cl:
        out.append((a, "%s taglio/salto di vista (area >60%% in %d fotogrammi)" % (rng(a, b), nn)))
    # pannelli/UI
    ui = [e for e in evs if e['kind'].startswith("ui")]
    grp = []
    for e in ui:
        if grp and grp[-1]['zone'] == e['zone'] and e['t0'] - grp[-1]['t1'] <= 3.0:
            grp[-1]['ev'].append(e)
            grp[-1]['t1'] = e['t1']
        else:
            grp.append(dict(zone=e['zone'], t0=e['t0'], t1=e['t1'], ev=[e]))
    used_clicks = set()
    for g in grp:
        ni = sum(1 for e in g['ev'] if e['kind'] == "ui-istantaneo")
        ng = len(g['ev']) - ni
        txt = "%s %s: %s" % (rng(g['t0'], g['t1']), SHORT.get(g['zone'], g['zone']),
                              ", ".join(x for x in (("%d cambio istantaneo" if ni == 1 else "%d cambi istantanei") % ni + " (menu/pannello aperto-chiuso)" if ni else "",
                                                     ("%d graduale" if ng == 1 else "%d graduali") % ng if ng else "") if x))
        for i, c in enumerate(clicks):
            if g['t0'] - 0.8 <= c['t'] <= g['t1'] and (c['eff'] == g['zone'] or c['zone'] == g['zone']):
                txt += "; clic %s (%d,%d)->%s" % (tf1(c['t']), c['x'], c['y'], SHORT.get(c['eff'], c['eff']))
                used_clicks.add(i)
        out.append((g['t0'], txt))
    # fasi nel viewport
    vp = [e for e in evs if e['kind'] in ("vista", "mod", "mod-istantaneo")]
    ph = []
    for e in vp:
        if ph and e['t0'] - ph[-1]['t1'] <= 2.0:
            ph[-1]['ev'].append(e)
            ph[-1]['t1'] = max(ph[-1]['t1'], e['t1'])
        else:
            ph.append(dict(t0=e['t0'], t1=e['t1'], ev=[e]))
    for f in ph:
        ev = f['ev']
        mods = [e for e in ev if e['kind'] == "mod"]
        ist = [e for e in ev if e['kind'] == "mod-istantaneo"]
        vis = [e for e in ev if e['kind'] == "vista"]
        parts = []
        zs = [e['zone'] for e in mods + ist]
        zn = SHORT.get(max(set(zs), key=zs.count), "viewport") if zs else "viewport"
        if mods:
            me = [e['inten'] for e in mods]
            parts.append("modifica mesh in %s, %d %s, intensita %s" % (zn, len(mods), "gesto" if len(mods) == 1 else "gesti", max(set(me), key=me.count)))
        if vis:
            subs = {}
            for e in vis:
                subs[e['sub']] = subs.get(e['sub'], 0) + 1
            tot = sum(e['t1'] - e['t0'] for e in vis)
            parts.append("vista " + ", ".join("%s %dx" % (k, v) for k, v in subs.items()) + " (%.0fs)" % tot)
        if ist:
            parts.append("%d %s in %s (clic/modifica puntuale) @%s" % (len(ist), "cambio istantaneo" if len(ist) == 1 else "cambi istantanei", zn, ",".join(tf1(e['t0']) for e in ist)))
        cc = [(i, c) for i, c in enumerate(clicks) if f['t0'] - 0.8 <= c['t'] <= f['t1'] + 0.5 and i not in used_clicks]
        for i, c in cc:
            used_clicks.add(i)
        if cc:
            parts.append("clic " + " ".join("%s(%d,%d %s)%s" % (tf1(c['t']), c['x'], c['y'], SHORT.get(c['zone'], c['zone']), "" if c['near'] else "->" + SHORT.get(c['eff'], c['eff'])) for i, c in cc))
        bbs2 = [e['bbox'] for e in mods + ist if e.get('bbox')]
        FASI.append(dict(t0=f['t0'], t1=f['t1'], mods=len(mods), ist=len(ist),
                         bbox=[min(b[0] for b in bbs2), min(b[1] for b in bbs2), max(b[2] for b in bbs2), max(b[3] for b in bbs2)] if bbs2 else None,
                         clicks=[[c['t'], c['x'], c['y']] for i, c in cc]))
        head = "fase" if mods else ("fase navigazione" if vis else "fase")
        out.append((f['t0'], "%s %s: %s" % (rng(f['t0'], f['t1']), head, "; ".join(parts))))
    for i, c in enumerate(clicks):
        if i not in used_clicks:
            out.append((c['t'], "[%s] clic probabile (%d,%d) in %s, effetto in %s" % (tf1(c['t']), c['x'], c['y'],
                                                                                SHORT.get(c['zone'], c['zone']), SHORT.get(c['eff'], c['eff']))))
    out.sort(key=lambda x: x[0])
    return [o[1] for o in out]


if __name__ == "__main__":
    video = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.dirname(os.path.abspath(__file__))
    os.makedirs(out, exist_ok=True)
    tempi.configura('movimento', out, video)
    tempi.strumenta_cv2(cv2)
    color, gray, rows, cur, info, dt, t0 = analizza(video)
    classifica(rows)
    if DEBUG:
        for r in rows:
            if r['area'] >= 0.25:
                print("%s a=%5.1f va=%5.1f c=%-6s z=%-18s cpct=%4.1f fm=%.2f R=%.2f mm=%.2f ext=%.2f %s" % (
                    tf1(r['t']), r['area'], r['varea'], r['c'], r['zone'], r['cpct'], r['fmov'], r['R'], r['mmag'],
                    r['ext'], r.get('sub', '')))
    evs, clicks = eventi(rows, cur, dt, color[0].shape[0])
    lines = componi(evs, clicks)
    import json
    json.dump(dict(fasi=FASI, dt=dt, W=W, H=color[0].shape[0], cur=[[round(k * dt, 2), round(c[0], 1), round(c[1], 1)] for k, c in enumerate(cur) if c is not None]),
              open(os.path.join(out, "mov.json"), "w"))
    txt = chr(10).join(lines) + chr(10)
    with open(os.path.join(out, "eventi.txt"), "w", encoding="utf-8") as fh:
        fh.write(txt)
    print("tempo %.1fs  righe %d  caratteri %d  token~%d  cursore %d/%d fotogrammi" % (
        time.time() - t0, len(lines), len(txt), len(txt) // 4, sum(c is not None for c in cur), len(cur)))
