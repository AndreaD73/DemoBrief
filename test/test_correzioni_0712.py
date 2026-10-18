# -*- coding: utf-8 -*-
"""Regression tests for the ten defects fixed in 0.7.12 (synthetic data, no real videos)."""
import os
import subprocess
import sys

import pytest

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, SRC)

import leggi_video


def test_tetto_fase_parallelo_esce_con_codice_4(monkeypatch, tmp_path):
    """A step over the time cap in parallel mode exits with code 4, not with a ValueError."""
    processi = []

    def avvia(script, *args, nome=None, lavoro=None):
        fase = nome or os.path.splitext(script)[0]
        registro = open(os.path.join(lavoro, fase + '.log'), 'w', encoding='utf-8')
        processo = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                                    stdout=registro, stderr=subprocess.STDOUT)
        processi.append(processo)
        return processo, registro, fase

    monkeypatch.setattr(leggi_video, 'avvia', avvia)
    monkeypatch.setattr(leggi_video, 'FIGLI', processi)
    with pytest.raises(SystemExit) as errore:
        leggi_video.esegui_fasi_parallele('video.mp4', str(tmp_path), 'it', 0)
    assert errore.value.code == 4
    assert all(processo.poll() is not None for processo in processi)


def _resultato(returncode=0, stdout='', stderr=''):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def test_scarica_limita_dirette_dimensione_e_tempo(monkeypatch, tmp_path):
    """The yt-dlp calls carry the live filter, the size cap and a timeout."""
    chiamate = []

    def finta_run(comando, **opzioni):
        chiamate.append((comando, opzioni))
        if '--skip-download' in comando:
            return _resultato(stdout='Titolo\n')
        (tmp_path / 'video.mp4').write_bytes(b'x')
        return _resultato()

    monkeypatch.setattr(leggi_video.subprocess, 'run', finta_run)
    percorso, titolo = leggi_video.scarica('http://x/y', str(tmp_path), {})
    assert titolo == 'Titolo'
    comando_titolo, opzioni_titolo = chiamate[0]
    comando_download, opzioni_download = chiamate[1]
    assert opzioni_titolo['timeout'] == 60
    assert opzioni_download['timeout'] == 2 * 3600
    assert comando_download[comando_download.index('--match-filter') + 1] == '!is_live'
    assert comando_download[comando_download.index('--max-filesize') + 1] == '4G'


def test_scarica_scaduto_da_messaggio_chiaro(monkeypatch, tmp_path):
    """A download that exceeds the timeout stops with a clear message, not a traceback."""
    def finta_run(comando, **opzioni):
        if '--skip-download' in comando:
            return _resultato(stdout='Titolo\n')
        raise subprocess.TimeoutExpired(comando, opzioni['timeout'])

    monkeypatch.setattr(leggi_video.subprocess, 'run', finta_run)
    with pytest.raises(SystemExit) as errore:
        leggi_video.scarica('http://x/y', str(tmp_path), {})
    assert 'Download non riuscito' in str(errore.value.code)


def _video_sintetico(percorso, secondi=2, con_audio=False):
    """Generate a tiny test video with ffmpeg (testsrc, optionally with a sine tone)."""
    import comune
    comando = [comune.ffmpeg(), '-v', 'error', '-y', '-f', 'lavfi', '-i',
               'testsrc=size=320x240:rate=10:duration=%d' % secondi]
    if con_audio:
        comando += ['-f', 'lavfi', '-i', 'sine=duration=%d' % secondi]
    comando += ['-pix_fmt', 'yuv420p', str(percorso)]
    subprocess.run(comando, check=True)
    return str(percorso)


def test_trascrivi_video_senza_audio_scrive_trascrizione_vuota(tmp_path):
    """A video with no audio track gives parole.json == [] and exit code 0."""
    import json
    video = _video_sintetico(tmp_path / 'muto.mp4')
    uscita = tmp_path / 'lavoro'
    uscita.mkdir()
    esito = subprocess.run([sys.executable, os.path.join(SRC, 'trascrivi.py'), video, str(uscita), 'en'],
                           capture_output=True, text=True, encoding='utf-8')
    assert esito.returncode == 0, esito.stderr
    assert json.loads((uscita / 'parole.json').read_text(encoding='utf-8')) == []
    assert (uscita / 'trascrivi_info.json').exists()


def test_costruisci_durata_zero_scrive_il_racconto(tmp_path):
    """A zero duration (container without duration) must not divide by zero."""
    import json
    lavoro = tmp_path / 'lavoro'
    lavoro.mkdir()
    (lavoro / 'parole.json').write_text(json.dumps([[0.0, 0.5, 'Hello'], [0.6, 1.0, 'world.']]),
                                        encoding='utf-8')
    (lavoro / 'ocr1.json').write_text(json.dumps(dict(dur=0, stato={}, finestre=[])), encoding='utf-8')
    (lavoro / 'mov.json').write_text(json.dumps(dict(fasi=[], eventi=[])), encoding='utf-8')
    (lavoro / 'eventi.txt').write_text('', encoding='utf-8')
    (lavoro / 'meta.json').write_text(json.dumps(dict(titolo='T')), encoding='utf-8')
    esito = subprocess.run([sys.executable, os.path.join(SRC, 'costruisci.py'), str(lavoro), 'T'],
                           capture_output=True, text=True, encoding='utf-8')
    assert esito.returncode == 0, esito.stderr[-800:]
    assert (lavoro / 'racconto.md').exists()
    statistiche = json.loads((lavoro / 'costruisci_stat.json').read_text())
    assert statistiche['token_al_secondo'] == 0


def test_movimento_ffmpeg_senza_dimensioni_non_si_blocca(tmp_path):
    """Unknown frame size (0x0) is refused at once instead of stalling on a full pipe."""
    import threading
    import movimento
    video = _video_sintetico(tmp_path / 'v.mp4')
    risultato = []

    def prova():
        try:
            movimento._load_ffmpeg(video, 1, 10.0, 20, 0, 0)
        except RuntimeError as errore:
            risultato.append(errore)

    filo = threading.Thread(target=prova, daemon=True)
    filo.start()
    filo.join(10)
    assert not filo.is_alive(), 'decoding stalled'
    assert risultato


def test_movimento_ffmpeg_stderr_non_e_una_pipe(tmp_path, monkeypatch):
    """ffmpeg stderr must never be a PIPE that nobody drains while stdout is read."""
    import movimento
    video = _video_sintetico(tmp_path / 'v.mp4')
    originale = subprocess.Popen
    opzioni_viste = []

    def popen_spia(comando, **opzioni):
        opzioni_viste.append(opzioni)
        return originale(comando, **opzioni)

    monkeypatch.setattr(movimento.subprocess, 'Popen', popen_spia)
    movimento._load_ffmpeg(video, 1, 10.0, 20, 320, 240)
    assert opzioni_viste[0]['stderr'] != subprocess.PIPE


def test_occhio_frame_oltre_la_fine_restituisce_none(tmp_path):
    """frame_at past the end of the video returns None instead of raising cv2.error."""
    import occhio
    video = _video_sintetico(tmp_path / 'v.mp4')
    assert occhio.frame_at(video, 0.5) is not None
    assert occhio.frame_at(video, 30.0) is None


def test_occhio_lavora_salta_l_intervallo_se_manca_un_fotogramma(monkeypatch):
    """An interval whose frames lie past the video end is skipped, not fatal."""
    import occhio
    monkeypatch.setattr(occhio, 'frame_at', lambda video, t: None)
    fase = dict(bbox=[10, 10, 50, 50])
    mv = dict(W=1280, cur=[], fasi=[fase])
    risultato = occhio.lavora(('v.mp4', mv, (1.0, 2.0, fase)))
    assert risultato['t0'] == 1.0
    assert 'errore' in risultato


def test_cache_ocr_tollera_permission_error(tmp_path, monkeypatch):
    """A cache file locked by another OCR phase never makes the phase fail."""
    import json
    import ocr
    monkeypatch.setattr(ocr, 'CACHE_ATTESA_SECONDI', 0)
    (tmp_path / 'ocr_cache.json').write_text(json.dumps({'f': {'line': [['a', 0.9]]}}),
                                             encoding='utf-8')
    aperture = []
    originale = open

    def open_bloccato(percorso, *args, **kwargs):
        if str(percorso).endswith('ocr_cache.json') and len(aperture) < 2:
            aperture.append(percorso)
            raise PermissionError(13, 'locked')
        return originale(percorso, *args, **kwargs)

    monkeypatch.setattr(ocr, 'open', open_bloccato, raising=False)
    cache = ocr.CacheOCR(str(tmp_path))
    assert cache.leggi('f', 'line') == [['a', 0.9]]

    def replace_bloccato(sorgente, destinazione):
        raise PermissionError(13, 'locked')

    monkeypatch.setattr(ocr.os, 'replace', replace_bloccato)
    cache.scrivi('g', 'line', [['b', 0.8]])
    cache.salva()
    assert not [f for f in os.listdir(tmp_path) if '.tmp.' in f]


RADICE = os.path.abspath(os.path.join(SRC, '..'))


def test_installa_sh_senza_caratteri_rotti():
    """installa.sh must not contain double-encoded UTF-8 (A tilde followed by NBSP)."""
    with open(os.path.join(RADICE, 'installa.sh'), encoding='utf-8') as file:
        testo = file.read()
    assert 'Ã' not in testo
    assert 'sarà scaricato' in testo
    assert 'uscirà senza' in testo


@pytest.mark.skipif(sys.platform != 'win32', reason='needs Windows PowerShell')
def test_leggi_video_ps1_toglie_la_barra_finale(tmp_path):
    """-Uscita ending with a backslash reaches Python without the backslash and without quotes."""
    import shutil
    cartella = tmp_path / 'strumento'
    (cartella / 'venv' / 'Scripts').mkdir(parents=True)
    (cartella / 'src').mkdir()
    shutil.copy(os.path.join(RADICE, 'leggi-video.ps1'), cartella)
    shutil.copy(os.path.join(RADICE, 'version.txt'), cartella)
    shutil.copy(os.path.join(RADICE, 'venv', 'pyvenv.cfg'), cartella / 'venv')
    shutil.copy(os.path.join(RADICE, 'venv', 'Scripts', 'python.exe'), cartella / 'venv' / 'Scripts')
    (cartella / 'src' / 'leggi_video.py').write_text('import sys\nprint("ARGV", sys.argv[1:])\n')
    esito = subprocess.run(
        ['powershell', '-NoProfile', '-File', str(cartella / 'leggi-video.ps1'), 'v.mp4',
         '-Uscita', str(tmp_path / 'mia cartella') + os.sep, '-SenzaOcchio'],
        capture_output=True, text=True)
    riga = [r for r in esito.stdout.splitlines() if r.startswith('ARGV')][0]
    assert '"' not in riga
    assert "mia cartella'" in riga
    assert '--senza-occhio' in riga


def test_occhio_spento_da_config_lo_dice_l_intestazione(monkeypatch):
    """With "occhio": false the header note says so, in the same form as the other notes."""
    def vietata(cfg):
        raise AssertionError('Ollama must not be queried when the eye is off')

    monkeypatch.setattr(leggi_video, 'ollama_pronto', vietata)
    attivo, nota = leggi_video.stato_occhio({'occhio': False}, False)
    assert attivo is False
    assert nota.startswith('VISTA assente: ')
    assert 'config.json' in nota


def test_occhio_nota_per_switch_e_per_ollama_assente(monkeypatch):
    """The switch and an unreachable Ollama keep their own notes; a ready Ollama gives none."""
    attivo, nota = leggi_video.stato_occhio({'occhio': True}, True)
    assert (attivo, nota) == (False, 'VISTA assente: occhio disattivato (-SenzaOcchio).')
    monkeypatch.setattr(leggi_video, 'ollama_pronto', lambda cfg: (False, 'Ollama non risponde su x'))
    assert leggi_video.stato_occhio({'occhio': True}, False) == (
        False, 'VISTA assente: Ollama non risponde su x.')
    monkeypatch.setattr(leggi_video, 'ollama_pronto', lambda cfg: (True, ''))
    assert leggi_video.stato_occhio({'occhio': True}, False) == (True, '')
