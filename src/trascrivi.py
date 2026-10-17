# -*- coding: utf-8 -*-
"""trascrivi.py <video> <outdir> [lingua]  -> parole.json  (whisper a parola, prompt fisso sulle scorciatoie da tastiera)
Dispositivo: GPU CUDA se disponibile, altrimenti CPU (int8). Lingua: en|it|auto (auto = rilevata da whisper)."""
import os, sys, json, time, site, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import comune
import tempi

if sys.platform == 'win32':
    for sp in site.getsitepackages():
        for p in ('cublas', 'cudnn'):
            d = os.path.join(sp, 'nvidia', p, 'bin')
            if os.path.isdir(d):
                os.add_dll_directory(d)
                os.environ['PATH'] = d + os.pathsep + os.environ['PATH']
video, out = sys.argv[1], sys.argv[2]
lingua = (sys.argv[3] if len(sys.argv) > 3 else 'auto').lower()
cfg = comune.config()['whisper']
tempi.configura('trascrivi', out, video)
with tempi.blocco('import_whisper'):
    from faster_whisper import WhisperModel
t = time.time()
wav = os.path.join(out, 'a.wav')
with tempi.blocco('decodifica_video'):
    subprocess.run([comune.ffmpeg(), '-v', 'error', '-y', '-i', video, '-vn', '-ac', '1', '-ar', '16000', wav], check=True)
tempi.conta('video_decodifiche_complete')


def dispositivo():
    d = cfg.get('dispositivo', 'auto')
    if d != 'auto':
        return d
    try:
        import ctranslate2
        return 'cuda' if ctranslate2.get_cuda_device_count() > 0 else 'cpu'
    except Exception:
        return 'cpu'


# Prompt fisso (non dipende dal video): insegna a whisper come si scrivono le scorciatoie, che nei tutorial
# venivano storpiate ("shift 8" per Shift+A). Con lingua 'auto' niente prompt: falserebbe il riconoscimento.
PROMPT = {
    'en': 'Software tutorial with screen recording. Keyboard shortcuts: Shift+A, Ctrl+Z, Ctrl+Shift+S, Alt+click, Ctrl+scroll, Tab, F3, Esc.',
    'it': 'Tutorial di un programma con registrazione dello schermo. Scorciatoie: Maiusc+A, Ctrl+Z, Ctrl+Maiusc+S, Alt+clic, Ctrl+rotella, Tab, F3, Esc.',
}
prompt = cfg.get('prompt', PROMPT.get(lingua)) or None
dev = dispositivo()
try:
    with tempi.blocco('carica_modello'):
        m = WhisperModel(cfg.get('modello', 'medium'), device=dev, compute_type='float16' if dev == 'cuda' else 'int8')
    with tempi.blocco('trascrizione_whisper'):
        seg, info = m.transcribe(wav, language=None if lingua == 'auto' else lingua, word_timestamps=True, vad_filter=True, beam_size=5, initial_prompt=prompt)
        W = [[round(w.start, 2), round(w.end, 2), w.word.strip()] for s in seg for w in s.words]
except Exception as e:                       # GPU presente ma librerie CUDA mancanti: ripiego su CPU
    if dev != 'cuda':
        raise
    print('GPU non utilizzabile (%s): uso la CPU' % str(e)[:120], flush=True)
    dev = 'cpu'
    with tempi.blocco('carica_modello'):
        m = WhisperModel(cfg.get('modello', 'medium'), device='cpu', compute_type='int8')
    with tempi.blocco('trascrizione_whisper'):
        seg, info = m.transcribe(wav, language=None if lingua == 'auto' else lingua, word_timestamps=True, vad_filter=True, beam_size=5, initial_prompt=prompt)
        W = [[round(w.start, 2), round(w.end, 2), w.word.strip()] for s in seg for w in s.words]
json.dump(W, open(os.path.join(out, 'parole.json'), 'w', encoding='utf-8'), ensure_ascii=False)
json.dump(dict(lingua=info.language, dispositivo=dev), open(os.path.join(out, 'trascrivi_info.json'), 'w'))
try:
    os.remove(wav)
except OSError:
    pass
print(len(W), 'parole', info.language, dev, round(time.time() - t, 1), 's', flush=True)
