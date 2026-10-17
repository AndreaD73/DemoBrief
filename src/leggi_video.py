# -*- coding: utf-8 -*-
"""leggi_video.py <video|URL> [--uscita DIR] [--lingua en|it|auto] [--senza-occhio]
Voce (whisper), OCR, movimento e occhio opzionale (Ollama) producono racconto.md e fotogrammi/."""
import os, sys, re, json, time, argparse, subprocess, shutil, atexit, signal
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import comune
import tempi

QUI = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
T0 = time.time()


def log(m):
    print('[%6.1fs] %s' % (time.time() - T0, m), flush=True)


def scarica(url, lavoro, cfg):
    h = int(cfg.get('altezza_massima_download', 720))
    modello = os.path.join(lavoro, 'video.%(ext)s')
    fmt = 'bv*[height<=%d]+ba/b[height<=%d]' % (h, h)
    base = [PY, '-m', 'yt_dlp', '--no-playlist', '--no-warnings', '--ffmpeg-location', os.path.dirname(comune.ffmpeg()),
            '-f', fmt, '--merge-output-format', 'mp4', '-o', modello]
    titolo = subprocess.run(base + ['--print', 'title', '--skip-download', url], capture_output=True, text=True, encoding='utf-8')
    r = subprocess.run(base + [url], capture_output=True, text=True, encoding='utf-8')
    if r.returncode != 0:
        sys.exit('Download non riuscito: ' + (r.stderr or r.stdout).strip()[-400:])
    for f in os.listdir(lavoro):
        if f.startswith('video.') and not f.endswith(('.part', '.ytdl')):
            return os.path.join(lavoro, f), (titolo.stdout.strip().splitlines() or [''])[0]
    sys.exit('Download riuscito ma file non trovato.')


def ollama_pronto(cfg):
    """(ok, motivo) - raggiungibile e modello presente."""
    import requests
    srv, mod = cfg['ollama']['indirizzo'].rstrip('/'), cfg['ollama']['modello']
    try:
        nomi = [m['name'] for m in requests.get(srv + '/api/tags', timeout=5).json().get('models', [])]
    except Exception:
        return False, 'Ollama non risponde su %s' % srv
    if mod not in nomi and (':' in mod or mod + ':latest' not in nomi):
        return False, 'modello %s non presente in Ollama' % mod
    return True, ''


FIGLI = []


def _termina_processo(p):
    if p.poll() is not None:
        return
    if os.name == 'nt':
        try:
            subprocess.run(
                ['taskkill', '/F', '/T', '/PID', str(p.pid)],
                capture_output=True,
                check=False,
            )
        except FileNotFoundError:
            p.terminate()
        return
    try:
        os.killpg(p.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        p.terminate()
    try:
        p.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            p.kill()


def _ferma_figli():
    """Termina i processi figli avviati dalla pipeline."""
    for p in FIGLI:
        _termina_processo(p)


atexit.register(_ferma_figli)


def avvia(script, *args, nome=None, lavoro=None):
    nome = nome or os.path.splitext(script)[0]
    f = open(os.path.join(lavoro, nome + '.log'), 'w', encoding='utf-8')
    env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    p = subprocess.Popen(
        [PY, os.path.join(QUI, script)] + list(args),
        stdout=f,
        stderr=subprocess.STDOUT,
        env=env,
        start_new_session=(os.name != 'nt'),
    )
    FIGLI.append(p)
    return p, f, nome


def attendi(p, lavoro, critico=True, tetto_minuti=30):
    pr, f, nome = p
    try:
        rc = pr.wait(timeout=float(tetto_minuti) * 60)
    except subprocess.TimeoutExpired:
        _termina_processo(pr)
        f.close()
        log('fase %s ferma da oltre %s minuti: interrotta' % (nome, tetto_minuti))
        raise SystemExit(4)
    f.close()
    log('%s finito (rc=%d)' % (nome, rc))
    if rc != 0 and critico:
        coda = open(os.path.join(lavoro, nome + '.log'), encoding='utf-8', errors='replace').read()[-600:]
        sys.exit('Passo "%s" fallito (vedi %s.log):\n%s' % (nome, nome, coda))
    return rc


def fotogrammi(video, uscita, lavoro):
    """Un png per ogni fase d'azione (fine fase; + meta' per le fasi lunghe) e indice orario->png."""
    mv = json.load(open(os.path.join(lavoro, 'mov.json'), encoding='utf-8'))
    cartella = os.path.join(uscita, 'fotogrammi')
    shutil.rmtree(cartella, ignore_errors=True)
    os.makedirs(cartella)
    voci = []
    for f in mv['fasi']:
        if f['mods'] + f['ist'] == 0:
            continue
        istanti = [f['t1'] + 0.3]
        if f['t1'] - f['t0'] >= 6.0:
            istanti.insert(0, (f['t0'] + f['t1']) / 2)
        desc = []
        if f['mods']: desc.append('modifica geometria (%d gest%s)' % (f['mods'], 'o' if f['mods'] == 1 else 'i'))
        if f['ist']: desc.append('%d cambi istantanei' % f['ist'])
        if f['clicks']: desc.append('%d clic' % len(f['clicks']))
        for t in istanti:
            nome = '%s_%s.png' % (comune.mmss(t).replace(':', '-'), 'azione')
            n = 2
            while any(v[1] == nome for v in voci):
                nome = '%s_azione%d.png' % (comune.mmss(t).replace(':', '-'), n); n += 1
            r = subprocess.run([comune.ffmpeg(), '-v', 'error', '-y', '-ss', '%.3f' % max(0, t), '-i', video, '-frames:v', '1',
                                '-vf', 'scale=1280:-2', os.path.join(cartella, nome)])
            if r.returncode == 0 and os.path.exists(os.path.join(cartella, nome)):
                voci.append((comune.mmss(t), nome, '; '.join(desc), t))
    voci.sort(key=lambda v: v[3])
    with open(os.path.join(cartella, 'indice.md'), 'w', encoding='utf-8') as fh:
        fh.write('# Indice fotogrammi (orario -> png). Aprire un\'immagine solo se una riga di racconto.md e\' ambigua.\n')
        for t, n, d, _ in voci:
            fh.write('[%s] %s - %s\n' % (t, n, d))
    return len(voci)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('ingresso')
    ap.add_argument('--uscita')
    ap.add_argument('--lingua')
    ap.add_argument('--senza-occhio', action='store_true')
    ap.add_argument('--canali', default=None)
    a = ap.parse_args()
    canali = [x.strip() for x in (a.canali or '').split(',') if x.strip()]
    canali_ammessi = frozenset(('cursore', 'stato', 'stile', 'risultato'))
    non_validi = [x for x in canali if x not in canali_ammessi]
    if non_validi:
        print(
            'Canale non valido: %s. Valori ammessi: cursore, stato, stile, risultato.'
            % ', '.join(non_validi),
            file=sys.stderr,
        )
        raise SystemExit(2)
    cfg = comune.config()
    tetto_fase_minuti = cfg.get('tetto_fase_minuti', 30)
    lingua = (a.lingua or cfg.get('lingua', 'auto')).lower()
    if lingua not in ('en', 'it', 'auto'):
        sys.exit('Lingua non valida: usare en, it o auto.')
    url = bool(re.match(r'^https?://', a.ingresso, re.I))
    if not url and not os.path.isfile(a.ingresso):
        sys.exit('File non trovato: ' + a.ingresso)
    if a.uscita:
        uscita = os.path.abspath(a.uscita)
    elif url:
        uscita = os.path.join(os.getcwd(), 'video-letto')
    else:
        uscita = os.path.join(os.path.dirname(os.path.abspath(a.ingresso)), os.path.splitext(os.path.basename(a.ingresso))[0] + '-letto')
    lavoro = os.path.join(uscita, '_lavoro')
    shutil.rmtree(lavoro, ignore_errors=True)
    os.makedirs(lavoro)
    tempi.configura('pipeline', lavoro)
    os.environ.setdefault('TIKTOKEN_CACHE_DIR', os.path.join(comune.RADICE, 'cache'))
    titolo = ''
    if url:
        log('scarico il video (max %dp)' % int(cfg.get('altezza_massima_download', 720)))
        video, titolo = scarica(a.ingresso, lavoro, cfg)
    else:
        video = os.path.abspath(a.ingresso)
    titolo = titolo or os.path.splitext(os.path.basename(video))[0]
    w, h, dur = comune.info_video(video)
    log('video %dx%d, %s' % (w, h, comune.mmss(dur)))

    nota = ''
    occhio = False
    if a.senza_occhio:
        nota = 'VISTA assente: occhio disattivato (-SenzaOcchio).'
    elif not cfg.get('occhio', True):
        # Disattivato nella configurazione predefinita: mantiene disponibile la pipeline senza osservazioni VISTA,
        # riducendo il tempo di elaborazione quando Ollama non viene richiesto.
        log('occhio spento da config.json ("occhio": false)')
    else:
        occhio, motivo = ollama_pronto(cfg)
        if not occhio:
            nota = 'VISTA assente: %s.' % motivo
    if nota:
        log(nota)

    # Le fasi pesanti restano sequenziali per limitare l'uso contemporaneo di CPU, GPU e memoria.
    attendi(avvia('movimento.py', video, lavoro, lavoro=lavoro), lavoro, tetto_minuti=tetto_fase_minuti)
    attendi(avvia('trascrivi.py', video, lavoro, lingua, lavoro=lavoro), lavoro, tetto_minuti=tetto_fase_minuti)
    attendi(avvia('ocr.py', 'letture', video, lavoro, nome='ocr_letture', lavoro=lavoro), lavoro, tetto_minuti=tetto_fase_minuti)
    attendi(avvia('ocr.py', 'azione', video, lavoro, nome='ocr_azione', lavoro=lavoro), lavoro, tetto_minuti=tetto_fase_minuti)
    attendi(avvia('ocr.py', 'schermate', video, lavoro, nome='ocr_schermate', lavoro=lavoro), lavoro, tetto_minuti=tetto_fase_minuti)
    for canale in canali:
        attendi(avvia('canale_%s.py' % canale, video, lavoro, lavoro=lavoro), lavoro, critico=False, tetto_minuti=tetto_fase_minuti)
    if occhio:
        if attendi(avvia('occhio.py', video, lavoro, lavoro=lavoro), lavoro, critico=False, tetto_minuti=tetto_fase_minuti) != 0:
            nota = 'VISTA assente: l\'occhio (%s) ha dato errore durante l\'analisi.' % cfg['ollama']['modello']
            try:
                os.remove(os.path.join(lavoro, 'occhio.json'))
            except OSError:
                pass
    n = fotogrammi(video, uscita, lavoro)
    log('%d fotogrammi salvati' % n)
    json.dump(dict(titolo=titolo, vista_nota=nota, indice_fotogrammi='fotogrammi/indice.md'), open(os.path.join(lavoro, 'meta.json'), 'w', encoding='utf-8'), ensure_ascii=False)
    r = subprocess.run([PY, os.path.join(QUI, 'costruisci.py'), lavoro, titolo], capture_output=True, text=True, encoding='utf-8',
                       env=dict(os.environ, PYTHONUTF8='1'))
    if r.returncode != 0:
        sys.exit('Costruzione del racconto fallita:\n' + (r.stderr or r.stdout)[-600:])
    shutil.copyfile(os.path.join(lavoro, 'racconto.md'), os.path.join(uscita, 'racconto.md'))
    for f in ('a.wav',):
        try: os.remove(os.path.join(lavoro, f))
        except OSError: pass
    if url:
        try: shutil.move(video, os.path.join(uscita, os.path.basename(video)))
        except Exception: pass
    st = json.load(open(os.path.join(lavoro, 'costruisci_stat.json'))) if os.path.exists(os.path.join(lavoro, 'costruisci_stat.json')) else {}
    if not cfg.get('conserva_lavoro', True):
        shutil.rmtree(lavoro, ignore_errors=True)
    log('FATTO: %s  (righe %s, token ~%s)' % (os.path.join(uscita, 'racconto.md'), st.get('righe', '?'), st.get('token', '?')))


if __name__ == '__main__':
    main()
