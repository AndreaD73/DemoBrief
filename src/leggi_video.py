# -*- coding: utf-8 -*-
"""Main pipeline: read a tutorial video (file or URL) and produce racconto.md plus key frames.

Usage: leggi_video.py <video|URL> [--uscita DIR] [--lingua en|it|auto] [--senza-occhio]
Runs the analysis steps as child processes (movement, speech transcription with whisper, OCR,
optional cursor/state/style/result channels, optional Ollama eye), then extracts the key frames
and calls costruisci.py to assemble the story.
Reads the video and config.json; writes <out>/racconto.md, <out>/fotogrammi/ and the intermediate
files in <out>/_lavoro (one .log per step, removed at the end unless config keeps it).
"""
import argparse
import atexit
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import comune
import tempi
import tabelle

QUI = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
T0 = time.time()
# Number of trailing characters of a failed step's log shown in the error message.
CODA_LOG_CARATTERI = 600
TETTO_FASE_MINUTI_PREDEFINITO = 30
ALTEZZA_DOWNLOAD_PREDEFINITA = 720
# Below this much free RAM (GB) the analysis steps run one after the other.
MEMORIA_MINIMA_PARALLELO_GB = 6.0
# An action phase at least this long (seconds) gets an extra frame at its midpoint.
FASE_LUNGA_SECONDI = 6.0
# Offset (seconds) after the end of an action phase where its result frame is taken.
RITARDO_FOTOGRAMMA = 0.3
# Limits on the yt-dlp calls: the title lookup is quick, the download may take a long time.
TIMEOUT_TITOLO_SECONDI = 60
TIMEOUT_DOWNLOAD_SECONDI = 2 * 3600
DIMENSIONE_MASSIMA_DOWNLOAD = '4G'
# yt-dlp match filter that refuses live streams (they would download forever).
FILTRO_NO_DIRETTE = '!is_live'
CANALI_AMMESSI = frozenset(('cursore', 'stato', 'stile', 'risultato'))


def log(m):
    """Print message `m` prefixed with the seconds elapsed since the program started."""
    print('[%6.1fs] %s' % (time.time() - T0, m), flush=True)


def scarica(url, lavoro, cfg):
    """Download `url` with yt-dlp into the work folder (best mp4 up to the configured height).

    Live streams are refused and files over DIMENSIONE_MASSIMA_DOWNLOAD are skipped; the title
    lookup is capped at 60 s and the download at 2 hours. Returns (path of the downloaded video,
    video title). Exits with an error message when the download fails, times out or no video
    file shows up.
    """
    altezza = int(cfg.get('altezza_massima_download', ALTEZZA_DOWNLOAD_PREDEFINITA))
    modello = os.path.join(lavoro, 'video.%(ext)s')
    formato = 'bv*[height<=%d]+ba/b[height<=%d]' % (altezza, altezza)
    base = [PY, '-m', 'yt_dlp', '--no-playlist', '--no-warnings', '--ffmpeg-location',
            os.path.dirname(comune.ffmpeg()), '-f', formato, '--merge-output-format', 'mp4', '-o', modello]
    try:
        titolo = subprocess.run(base + ['--print', 'title', '--skip-download', url],
                                capture_output=True, text=True, encoding='utf-8',
                                timeout=TIMEOUT_TITOLO_SECONDI)
        testo_titolo = titolo.stdout
    except subprocess.TimeoutExpired:
        testo_titolo = ''
    try:
        scaricato = subprocess.run(base + ['--match-filter', FILTRO_NO_DIRETTE,
                                           '--max-filesize', DIMENSIONE_MASSIMA_DOWNLOAD, url],
                                   capture_output=True, text=True, encoding='utf-8',
                                   timeout=TIMEOUT_DOWNLOAD_SECONDI)
    except subprocess.TimeoutExpired:
        sys.exit('Download non riuscito: tempo massimo di %d ore superato.'
                 % (TIMEOUT_DOWNLOAD_SECONDI // 3600))
    if scaricato.returncode != 0:
        sys.exit('Download non riuscito: ' + (scaricato.stderr or scaricato.stdout).strip()[-400:])
    for nome_file in os.listdir(lavoro):
        if nome_file.startswith('video.') and not nome_file.endswith(('.part', '.ytdl')):
            return os.path.join(lavoro, nome_file), (testo_titolo.strip().splitlines() or [''])[0]
    sys.exit('Download riuscito ma file non trovato (diretta o file oltre %s rifiutati).'
             % DIMENSIONE_MASSIMA_DOWNLOAD)


def ollama_pronto(cfg):
    """Check that the Ollama server answers and that the configured model is installed.

    Returns (True, '') when ready, otherwise (False, reason text).
    """
    import requests
    srv, mod = cfg['ollama']['indirizzo'].rstrip('/'), cfg['ollama']['modello']
    try:
        modelli = requests.get(srv + '/api/tags', timeout=5).json().get('models', [])
        nomi = [modello['name'] for modello in modelli]
    except Exception:
        return False, 'Ollama non risponde su %s' % srv
    if mod not in nomi and (':' in mod or mod + ':latest' not in nomi):
        return False, 'modello %s non presente in Ollama' % mod
    return True, ''


FIGLI = []


def _termina_processo(p):
    """Stop the child process `p` and its descendants (taskkill on Windows, SIGTERM then SIGKILL)."""
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
    """Terminate every child process started by the pipeline (also registered to run at exit)."""
    for processo in FIGLI:
        _termina_processo(processo)


atexit.register(_ferma_figli)


def avvia(script, *args, nome=None, lavoro=None):
    """Start `script` (from the source folder) as a child process with its output in a log file.

    The log is <lavoro>/<nome>.log; `nome` defaults to the script name without extension.
    Returns the tuple (process, open log file, step name) that `attendi` expects.
    """
    nome = nome or os.path.splitext(script)[0]
    file_log = open(os.path.join(lavoro, nome + '.log'), 'w', encoding='utf-8')
    env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    processo = subprocess.Popen(
        [PY, os.path.join(QUI, script)] + list(args),
        stdout=file_log,
        stderr=subprocess.STDOUT,
        env=env,
        start_new_session=(os.name != 'nt'),
    )
    FIGLI.append(processo)
    return processo, file_log, nome


def attendi(p, lavoro, critico=True, tetto_minuti=30):
    """Wait for a step started with `avvia` and return its exit code.

    After `tetto_minuti` minutes the step is killed and the program exits with code 4.
    A non-zero exit code of a `critico` step stops the program with the tail of its log.
    """
    pr, file_log, nome = p
    try:
        codice = pr.wait(timeout=float(tetto_minuti) * 60)
    except subprocess.TimeoutExpired:
        _termina_processo(pr)
        file_log.close()
        log('fase %s ferma da oltre %s minuti: interrotta' % (nome, tetto_minuti))
        raise SystemExit(4)
    file_log.close()
    log('%s finito (rc=%d)' % (nome, codice))
    if codice != 0 and critico:
        coda = open(os.path.join(lavoro, nome + '.log'), encoding='utf-8',
                    errors='replace').read()[-CODA_LOG_CARATTERI:]
        sys.exit('Passo "%s" fallito (vedi %s.log):\n%s' % (nome, nome, coda))
    return codice


def memoria_libera_gb():
    """Return the available physical memory in GB, or None when it cannot be read."""
    try:
        if os.name == 'nt':
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                """Windows structure of the global memory status, filled in by GlobalMemoryStatusEx."""

                _fields_ = [
                    ('dwLength', ctypes.c_ulong),
                    ('dwMemoryLoad', ctypes.c_ulong),
                    ('ullTotalPhys', ctypes.c_ulonglong),
                    ('ullAvailPhys', ctypes.c_ulonglong),
                    ('ullTotalPageFile', ctypes.c_ulonglong),
                    ('ullAvailPageFile', ctypes.c_ulonglong),
                    ('ullTotalVirtual', ctypes.c_ulonglong),
                    ('ullAvailVirtual', ctypes.c_ulonglong),
                    ('ullAvailExtendedVirtual', ctypes.c_ulonglong),
                ]

            stato = MEMORYSTATUSEX()
            stato.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stato)):
                return None
            byte_liberi = stato.ullAvailPhys
        elif sys.platform.startswith('linux'):
            valori = {}
            with open('/proc/meminfo', encoding='ascii') as file:
                for riga in file:
                    nome, valore = riga.split(':', 1)
                    valori[nome] = int(valore.strip().split()[0])
            byte_liberi = valori['MemAvailable'] * 1024
        else:
            byte_liberi = os.sysconf('SC_AVPHYS_PAGES') * os.sysconf('SC_PAGE_SIZE')
        return byte_liberi / float(1024 ** 3)
    except (AttributeError, KeyError, OSError, TypeError, ValueError):
        return None


def stato_occhio(cfg, senza_occhio):
    """Decide whether the Ollama eye runs and which header note explains when it does not.

    `senza_occhio` is the command line switch. The eye is off when the switch is given or
    config 'occhio' is false (off in the default configuration, to keep the pipeline fast when
    Ollama is not requested); otherwise Ollama and its model must answer. Returns
    (eye enabled, note for the story header or '' when there is nothing to say).
    """
    if senza_occhio:
        return False, 'VISTA assente: occhio disattivato (-SenzaOcchio).'
    if not cfg.get('occhio', True):
        log('occhio spento da config.json ("occhio": false)')
        return False, 'VISTA assente: occhio disattivato da config.json ("occhio": false).'
    pronto, motivo = ollama_pronto(cfg)
    if not pronto:
        return False, 'VISTA assente: %s.' % motivo
    return True, ''


def usa_fasi_parallele(cfg):
    """Decide whether the analysis steps may run in parallel.

    False when config 'parallelo' is off or free memory is below MEMORIA_MINIMA_PARALLELO_GB
    (a log line explains the second case).
    """
    if not cfg.get('parallelo', True):
        return False
    gb = memoria_libera_gb()
    if gb is not None and gb < MEMORIA_MINIMA_PARALLELO_GB:
        log('memoria libera %.1f GB: fasi in sequenza' % gb)
        return False
    return True


def _fallimento_fase(fase, lavoro, rc):
    """Stop the program with the tail of the log of the failed step `fase` (`rc` is not used)."""
    coda = open(
        os.path.join(lavoro, fase + '.log'),
        encoding='utf-8',
        errors='replace',
    ).read()[-CODA_LOG_CARATTERI:]
    raise SystemExit(
        'Passo "%s" fallito (vedi %s.log):\n%s'
        % (fase, fase, coda)
    )


def esegui_fasi_parallele(video, lavoro, lingua, tetto_minuti):
    """Run the independent steps together and honour the dependency of ocr_azione.

    Starts movement, transcription, and the OCR 'letture' and 'schermate' passes at once; the
    OCR 'azione' pass starts when movement and 'letture' are both done. A failing step stops
    the others and the program; a step over `tetto_minuti` minutes exits with code 4.
    """
    attive = {}
    completate = set()
    azione_avviata = False
    specifiche = (
        ('movimento', ('movimento.py', video, lavoro)),
        ('trascrivi', ('trascrivi.py', video, lavoro, lingua)),
        ('ocr_letture', ('ocr.py', 'letture', video, lavoro)),
        ('ocr_schermate', ('ocr.py', 'schermate', video, lavoro)),
    )

    for nome, argomenti in specifiche:
        attive[nome] = (
            avvia(*argomenti, nome=nome, lavoro=lavoro),
            time.monotonic(),
        )

    while attive:
        adesso = time.monotonic()
        for nome in tuple(attive):
            processo, avviata_il = attive[nome]
            pr, registro, fase = processo
            rc = pr.poll()
            if rc is not None:
                registro.close()
                log('%s finito (rc=%d)' % (fase, rc))
                del attive[nome]
                completate.add(nome)
                if rc != 0:
                    for (_, registro_attivo, _), _ in attive.values():
                        registro_attivo.close()
                    _ferma_figli()
                    _fallimento_fase(fase, lavoro, rc)
                continue
            if adesso - avviata_il >= float(tetto_minuti) * 60:
                _termina_processo(pr)
                registro.close()
                del attive[nome]
                log(
                    'fase %s ferma da oltre %s minuti: interrotta'
                    % (fase, tetto_minuti)
                )
                for (_, registro_attivo, _), _ in attive.values():
                    registro_attivo.close()
                _ferma_figli()
                raise SystemExit(4)

        if (
            not azione_avviata
            and {'movimento', 'ocr_letture'} <= completate
        ):
            attive['ocr_azione'] = (
                avvia(
                    'ocr.py',
                    'azione',
                    video,
                    lavoro,
                    nome='ocr_azione',
                    lavoro=lavoro,
                ),
                time.monotonic(),
            )
            azione_avviata = True

        if attive:
            time.sleep(0.02)


def fotogrammi(video, uscita, lavoro):
    """Extract the key frames of the video as PNG files and write their index (indice.md).

    Files go to the sub-folder "fotogrammi" of the output folder `uscita`.

    Base frames come from mov.json: one just after the end of each action phase, plus one at the
    midpoint of long phases. With table output 'I' on, tabelle replaces some of them without
    raising the total. Returns the number of PNG files written.
    """
    movimento = json.load(open(os.path.join(lavoro, 'mov.json'), encoding='utf-8'))
    durata = max([float(fase.get('t1', 0)) for fase in movimento.get('fasi', [])] + [0.0])
    base = []
    for fase in movimento.get('fasi', []):
        if fase.get('mods', 0) + fase.get('ist', 0) == 0:
            continue
        istanti = [float(fase['t1']) + RITARDO_FOTOGRAMMA]
        if float(fase['t1']) - float(fase['t0']) >= FASE_LUNGA_SECONDI:
            istanti.insert(0, (float(fase['t0']) + float(fase['t1'])) / 2.0)
        descrizioni = []
        if fase.get('mods'):
            suffisso_gesto = 'o' if fase['mods'] == 1 else 'i'
            descrizioni.append('modifica geometria (%d gest%s)' % (fase['mods'], suffisso_gesto))
        if fase.get('ist'):
            descrizioni.append('%d cambi istantanei' % fase['ist'])
        if fase.get('clicks'):
            descrizioni.append('%d clic' % len(fase['clicks']))
        for istante in istanti:
            base.append(dict(t=istante, descrizione='; '.join(descrizioni), tipo='azione'))
    if 'I' in tabelle.accese():
        def carica(nome):
            """Load the JSON file `nome` from the work folder, or {} when it does not exist."""
            percorso = os.path.join(lavoro, nome)
            return json.load(open(percorso, encoding='utf-8')) if os.path.exists(percorso) else {}
        istanti_tabella = tabelle.istanti_fotogramma(
            carica('ocr3.json'), carica('ocr1.json'), movimento.get('fasi', []), durata)
        scelte = tabelle.scegli_fotogrammi(base, istanti_tabella, durata)
    else:
        scelte = base
    cartella = os.path.join(uscita, 'fotogrammi')
    shutil.rmtree(cartella, ignore_errors=True)
    os.makedirs(cartella)
    voci = []
    nomi = set()
    for voce in scelte:
        t = float(voce['t'])
        suffisso = 'tabella' if voce.get('tipo') == 'tabella' else 'azione'
        nome = '%s_%s.png' % (comune.mmss(t).replace(':', '-'), suffisso)
        progressivo = 2
        while nome in nomi:
            nome = '%s_%s%d.png' % (comune.mmss(t).replace(':', '-'), suffisso, progressivo)
            progressivo += 1
        nomi.add(nome)
        estrazione = subprocess.run([comune.ffmpeg(), '-v', 'error', '-y', '-ss', '%.3f' % max(0, t),
                                     '-i', video, '-frames:v', '1', '-vf', 'scale=1280:-2',
                                     os.path.join(cartella, nome)])
        if estrazione.returncode == 0 and os.path.exists(os.path.join(cartella, nome)):
            voci.append((comune.mmss(t), nome, voce['descrizione'], t))
    voci.sort(key=lambda voce_indice: voce_indice[3])
    with open(os.path.join(cartella, 'indice.md'), 'w', encoding='utf-8') as file_indice:
        file_indice.write('# Indice fotogrammi (orario -> png). '
                          'Aprire un\'immagine solo se una riga di racconto.md e\' ambigua.\n')
        for orario, nome_png, descrizione, _ in voci:
            file_indice.write('[%s] %s - %s\n' % (orario, nome_png, descrizione))
    return len(voci)


def main():
    """Command line entry point: run the whole pipeline and print the summary line.

    Validates the arguments (language, optional channels, input file), runs the steps in
    parallel or in sequence, extracts the frames, builds racconto.md through costruisci.py and
    optionally removes the work folder. With --solo-fotogrammi only the frames are extracted
    from an existing work folder. Returns 0 on success; errors exit with a message.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument('ingresso')
    parser.add_argument('--uscita')
    parser.add_argument('--lingua')
    parser.add_argument('--senza-occhio', action='store_true')
    parser.add_argument('--canali', default=None)
    parser.add_argument('--solo-fotogrammi')
    argomenti = parser.parse_args()
    if argomenti.solo_fotogrammi:
        if not argomenti.uscita:
            parser.error('--solo-fotogrammi richiede --uscita')
        fotogrammi(argomenti.ingresso, argomenti.uscita, argomenti.solo_fotogrammi)
        return 0
    canali = [voce.strip() for voce in (argomenti.canali or '').split(',') if voce.strip()]
    non_validi = [voce for voce in canali if voce not in CANALI_AMMESSI]
    if non_validi:
        print(
            'Canale non valido: %s. Valori ammessi: cursore, stato, stile, risultato.'
            % ', '.join(non_validi),
            file=sys.stderr,
        )
        raise SystemExit(2)
    cfg = comune.config()
    tetto_fase_minuti = cfg.get('tetto_fase_minuti', TETTO_FASE_MINUTI_PREDEFINITO)
    lingua = (argomenti.lingua or cfg.get('lingua', 'auto')).lower()
    if lingua not in ('en', 'it', 'auto'):
        sys.exit('Lingua non valida: usare en, it o auto.')
    url = bool(re.match(r'^https?://', argomenti.ingresso, re.I))
    if not url and not os.path.isfile(argomenti.ingresso):
        sys.exit('File non trovato: ' + argomenti.ingresso)
    if argomenti.uscita:
        uscita = os.path.abspath(argomenti.uscita)
    elif url:
        uscita = os.path.join(os.getcwd(), 'video-letto')
    else:
        cartella_video = os.path.dirname(os.path.abspath(argomenti.ingresso))
        nome_base = os.path.splitext(os.path.basename(argomenti.ingresso))[0]
        uscita = os.path.join(cartella_video, nome_base + '-letto')
    lavoro = os.path.join(uscita, '_lavoro')
    shutil.rmtree(lavoro, ignore_errors=True)
    os.makedirs(lavoro)
    tempi.configura('pipeline', lavoro)
    os.environ.setdefault('TIKTOKEN_CACHE_DIR', os.path.join(comune.RADICE, 'cache'))
    titolo = ''
    if url:
        altezza_massima = int(cfg.get('altezza_massima_download', ALTEZZA_DOWNLOAD_PREDEFINITA))
        log('scarico il video (max %dp)' % altezza_massima)
        video, titolo = scarica(argomenti.ingresso, lavoro, cfg)
    else:
        video = os.path.abspath(argomenti.ingresso)
    titolo = titolo or os.path.splitext(os.path.basename(video))[0]
    w, h, dur = comune.info_video(video)
    log('video %dx%d, %s' % (w, h, comune.mmss(dur)))

    occhio, nota = stato_occhio(cfg, argomenti.senza_occhio)
    if nota:
        log(nota)

    if usa_fasi_parallele(cfg):
        esegui_fasi_parallele(
            video,
            lavoro,
            lingua,
            tetto_fase_minuti,
        )
    else:
        attendi(
            avvia('movimento.py', video, lavoro, lavoro=lavoro),
            lavoro,
            tetto_minuti=tetto_fase_minuti,
        )
        attendi(
            avvia('trascrivi.py', video, lavoro, lingua, lavoro=lavoro),
            lavoro,
            tetto_minuti=tetto_fase_minuti,
        )
        attendi(
            avvia(
                'ocr.py',
                'letture',
                video,
                lavoro,
                nome='ocr_letture',
                lavoro=lavoro,
            ),
            lavoro,
            tetto_minuti=tetto_fase_minuti,
        )
        attendi(
            avvia(
                'ocr.py',
                'azione',
                video,
                lavoro,
                nome='ocr_azione',
                lavoro=lavoro,
            ),
            lavoro,
            tetto_minuti=tetto_fase_minuti,
        )
        attendi(
            avvia(
                'ocr.py',
                'schermate',
                video,
                lavoro,
                nome='ocr_schermate',
                lavoro=lavoro,
            ),
            lavoro,
            tetto_minuti=tetto_fase_minuti,
        )
    for canale in canali:
        attendi(avvia('canale_%s.py' % canale, video, lavoro, lavoro=lavoro), lavoro,
                critico=False, tetto_minuti=tetto_fase_minuti)
    if occhio:
        esito_occhio = attendi(avvia('occhio.py', video, lavoro, lavoro=lavoro), lavoro,
                               critico=False, tetto_minuti=tetto_fase_minuti)
        if esito_occhio != 0:
            modello_occhio = cfg['ollama']['modello']
            nota = 'VISTA assente: l\'occhio (%s) ha dato errore durante l\'analisi.' % modello_occhio
            try:
                os.remove(os.path.join(lavoro, 'occhio.json'))
            except OSError:
                pass
    numero_fotogrammi = fotogrammi(video, uscita, lavoro)
    log('%d fotogrammi salvati' % numero_fotogrammi)
    meta = dict(titolo=titolo, vista_nota=nota, indice_fotogrammi='fotogrammi/indice.md')
    json.dump(meta, open(os.path.join(lavoro, 'meta.json'), 'w', encoding='utf-8'), ensure_ascii=False)
    costruzione = subprocess.run([PY, os.path.join(QUI, 'costruisci.py'), lavoro, titolo],
                                 capture_output=True, text=True, encoding='utf-8',
                                 env=dict(os.environ, PYTHONUTF8='1'))
    if costruzione.returncode != 0:
        errore = (costruzione.stderr or costruzione.stdout)[-CODA_LOG_CARATTERI:]
        sys.exit('Costruzione del racconto fallita:\n' + errore)
    shutil.copyfile(os.path.join(lavoro, 'racconto.md'), os.path.join(uscita, 'racconto.md'))
    for nome_file in ('a.wav',):
        try:
            os.remove(os.path.join(lavoro, nome_file))
        except OSError:
            pass
    if url:
        try:
            shutil.move(video, os.path.join(uscita, os.path.basename(video)))
        except Exception:
            pass
    percorso_stat = os.path.join(lavoro, 'costruisci_stat.json')
    statistiche = json.load(open(percorso_stat)) if os.path.exists(percorso_stat) else {}
    if not cfg.get('conserva_lavoro', True):
        shutil.rmtree(lavoro, ignore_errors=True)
    log('FATTO: %s  (righe %s, token ~%s)' % (os.path.join(uscita, 'racconto.md'),
                                              statistiche.get('righe', '?'), statistiche.get('token', '?')))


if __name__ == '__main__':
    main()
