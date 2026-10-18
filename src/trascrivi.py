# -*- coding: utf-8 -*-
"""Word-level speech transcription of a video with faster-whisper.

Usage: trascrivi.py <video> <outdir> [language]   (language: en | it | auto, auto = detected by whisper)
Reads the video and the whisper settings from the project configuration.
Writes <outdir>/parole.json (list of [start, end, word]) and <outdir>/trascrivi_info.json
(language and device actually used); the temporary a.wav is removed at the end.
Device: CUDA GPU when available, otherwise CPU (int8). Runs as a script, not as a library.
"""
import json
import os
import site
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import comune
import tempi

SAMPLE_RATE_WHISPER = '16000'
BEAM_SIZE = 5
MAX_ERROR_CHARS = 120

if sys.platform == 'win32':
    # The NVIDIA wheels ship the cublas/cudnn DLLs inside site-packages: make them findable.
    for site_dir in site.getsitepackages():
        for pacchetto in ('cublas', 'cudnn'):
            cartella_dll = os.path.join(site_dir, 'nvidia', pacchetto, 'bin')
            if os.path.isdir(cartella_dll):
                os.add_dll_directory(cartella_dll)
                os.environ['PATH'] = cartella_dll + os.pathsep + os.environ['PATH']


def senza_audio(percorso_video):
    """Return True when ffprobe confirms that the video has no audio stream at all.

    Silent screen recordings are common and ffmpeg cannot extract a wav from them. When ffprobe is
    missing or fails the answer is False, so the normal extraction runs and reports its own error.
    """
    sonda = comune.ffprobe()
    if not sonda:
        return False
    esito = subprocess.run([sonda, '-v', 'error', '-select_streams', 'a', '-show_entries',
                            'stream=index', '-of', 'csv=p=0', percorso_video],
                           capture_output=True, text=True)
    return esito.returncode == 0 and not esito.stdout.strip()


video, out = sys.argv[1], sys.argv[2]
lingua = (sys.argv[3] if len(sys.argv) > 3 else 'auto').lower()
cfg = comune.config()['whisper']
tempi.configura('trascrivi', out, video)
if senza_audio(video):
    # No speech to transcribe: an empty transcript lets the rest of the pipeline go on.
    with open(os.path.join(out, 'parole.json'), 'w', encoding='utf-8') as file_parole:
        json.dump([], file_parole)
    with open(os.path.join(out, 'trascrivi_info.json'), 'w') as file_info:
        json.dump(dict(lingua=None if lingua == 'auto' else lingua, dispositivo='nessuno'), file_info)
    print(0, 'parole', 'video senza traccia audio', flush=True)
    sys.exit(0)
with tempi.blocco('import_whisper'):
    from faster_whisper import WhisperModel
avvio = time.time()
wav = os.path.join(out, 'a.wav')
with tempi.blocco('decodifica_video'):
    subprocess.run([comune.ffmpeg(), '-v', 'error', '-y', '-i', video, '-vn', '-ac', '1',
                    '-ar', SAMPLE_RATE_WHISPER, wav], check=True)
tempi.conta('video_decodifiche_complete')


def dispositivo():
    """Return the whisper device: the configured one, or cuda/cpu detected through ctranslate2."""
    scelto = cfg.get('dispositivo', 'auto')
    if scelto != 'auto':
        return scelto
    try:
        import ctranslate2
        return 'cuda' if ctranslate2.get_cuda_device_count() > 0 else 'cpu'
    except Exception:
        return 'cpu'


# Fixed prompt (independent of the video): teaches whisper how keyboard shortcuts are written,
# which in tutorials used to come out garbled ("shift 8" for Shift+A).
# With language 'auto' there is no prompt: it would bias the language detection.
PROMPT = {
    'en': 'Software tutorial with screen recording. Keyboard shortcuts: Shift+A, Ctrl+Z, Ctrl+Shift+S, '
          'Alt+click, Ctrl+scroll, Tab, F3, Esc.',
    'it': 'Tutorial di un programma con registrazione dello schermo. Scorciatoie: Maiusc+A, Ctrl+Z, '
          'Ctrl+Maiusc+S, Alt+clic, Ctrl+rotella, Tab, F3, Esc.',
}
prompt = cfg.get('prompt', PROMPT.get(lingua)) or None
dev = dispositivo()
try:
    with tempi.blocco('carica_modello'):
        modello = WhisperModel(cfg.get('modello', 'medium'), device=dev,
                               compute_type='float16' if dev == 'cuda' else 'int8')
    with tempi.blocco('trascrizione_whisper'):
        segmenti, info = modello.transcribe(wav, language=None if lingua == 'auto' else lingua,
                                            word_timestamps=True, vad_filter=True, beam_size=BEAM_SIZE,
                                            initial_prompt=prompt)
        parole = [[round(parola.start, 2), round(parola.end, 2), parola.word.strip()]
                  for segmento in segmenti for parola in segmento.words]
except Exception as errore:
    # CUDA GPU present but its libraries are missing: fall back to CPU.
    if dev != 'cuda':
        raise
    print('GPU non utilizzabile (%s): uso la CPU' % str(errore)[:MAX_ERROR_CHARS], flush=True)
    dev = 'cpu'
    with tempi.blocco('carica_modello'):
        modello = WhisperModel(cfg.get('modello', 'medium'), device='cpu', compute_type='int8')
    with tempi.blocco('trascrizione_whisper'):
        segmenti, info = modello.transcribe(wav, language=None if lingua == 'auto' else lingua,
                                            word_timestamps=True, vad_filter=True, beam_size=BEAM_SIZE,
                                            initial_prompt=prompt)
        parole = [[round(parola.start, 2), round(parola.end, 2), parola.word.strip()]
                  for segmento in segmenti for parola in segmento.words]
with open(os.path.join(out, 'parole.json'), 'w', encoding='utf-8') as file_parole:
    json.dump(parole, file_parole, ensure_ascii=False)
with open(os.path.join(out, 'trascrivi_info.json'), 'w') as file_info:
    json.dump(dict(lingua=info.language, dispositivo=dev), file_info)
try:
    os.remove(wav)
except OSError:
    pass
print(len(parole), 'parole', info.language, dev, round(time.time() - avvio, 1), 's', flush=True)
