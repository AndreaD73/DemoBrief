# -*- coding: utf-8 -*-
"""Funzioni comuni: configurazione, ricerca di ffmpeg/ffprobe, dimensioni del video."""
import os, sys, json, shutil, subprocess

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def ambiente(suffisso):
    """Legge DEMOBRIEF_<suffisso> e ripiega sul nome AD_MOVIOLA_<suffisso>."""
    nuovo = "DEMOBRIEF_" + suffisso
    if nuovo in os.environ:
        return os.environ[nuovo]
    return os.environ.get("AD_MOVIOLA_" + suffisso)

PREDEFINITI = {
    "ollama": {"indirizzo": "http://127.0.0.1:11434", "modello": "qwen3-vl:8b"},
    "whisper": {"modello": "medium", "dispositivo": "auto"},
    "lingua": "auto",
    "tetto_fase_minuti": 30,
    "parallelo": True,
    "processi_ocr": 3,
    "altezza_massima_download": 720,
    "conserva_lavoro": True,
    "decodifica_ffmpeg": True,
}


def config():
    c = json.loads(json.dumps(PREDEFINITI))
    p = os.path.join(RADICE, "config.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8-sig") as f:
            u = json.load(f)
        for k, v in u.items():
            if isinstance(v, dict) and isinstance(c.get(k), dict):
                c[k].update(v)
            else:
                c[k] = v
    return c


def _trova(nome):
    p = shutil.which(nome)
    if p:
        return p
    if sys.platform == "win32":
        for base in (os.environ.get("LOCALAPPDATA", ""), os.environ.get("ProgramFiles", "")):
            for sub in ("Microsoft\\WinGet\\Links", "ffmpeg\\bin"):
                q = os.path.join(base, sub, nome + ".exe")
                if base and os.path.exists(q):
                    return q
    else:
        for base in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"):
            q = os.path.join(base, nome)
            if os.path.exists(q):
                return q
    return None


def ffmpeg():
    p = _trova("ffmpeg")
    if not p:
        sys.exit("ffmpeg non trovato: eseguire installa.ps1 (Windows) o installa.sh (Linux/macOS), oppure installarlo a mano.")
    return p


def ffprobe():
    return _trova("ffprobe")


def info_video(video):
    """(larghezza, altezza, durata_in_secondi)"""
    fp = ffprobe()
    if fp:
        o = subprocess.check_output([fp, "-v", "error", "-select_streams", "v:0", "-show_entries",
                                     "stream=width,height:format=duration", "-of", "json", video]).decode()
        j = json.loads(o)
        s = j["streams"][0]
        d = float(j.get("format", {}).get("duration") or 0)
        return int(s["width"]), int(s["height"]), d
    import cv2
    c = cv2.VideoCapture(video)
    w, h = int(c.get(3)), int(c.get(4))
    d = c.get(cv2.CAP_PROP_FRAME_COUNT) / max(1.0, c.get(cv2.CAP_PROP_FPS))
    c.release()
    return w, h, d


def mmss(t):
    return "%02d:%02d" % (int(t // 60), int(t % 60))
