# -*- coding: utf-8 -*-
"""Shared helpers: configuration, ffmpeg/ffprobe lookup, video size and duration.

Reads config.json (next to the program) and environment variables; writes nothing.
"""
import json
import os
import shutil
import subprocess
import sys

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ambiente(suffisso):
    """Read DEMOBRIEF_<suffisso> from the environment, falling back to AD_MOVIOLA_<suffisso>."""
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
    """Return the built-in defaults overlaid with the user's settings file (nested dicts merged)."""
    risultato = json.loads(json.dumps(PREDEFINITI))
    percorso = os.path.join(RADICE, "config.json")
    if os.path.exists(percorso):
        with open(percorso, encoding="utf-8-sig") as f:
            utente = json.load(f)
        for chiave, valore in utente.items():
            if isinstance(valore, dict) and isinstance(risultato.get(chiave), dict):
                risultato[chiave].update(valore)
            else:
                risultato[chiave] = valore
    return risultato


def _trova(nome):
    """Locate the executable `nome` on PATH or in the usual install folders; None if absent."""
    trovato = shutil.which(nome)
    if trovato:
        return trovato
    if sys.platform == "win32":
        for base in (os.environ.get("LOCALAPPDATA", ""), os.environ.get("ProgramFiles", "")):
            for sub in ("Microsoft\\WinGet\\Links", "ffmpeg\\bin"):
                candidato = os.path.join(base, sub, nome + ".exe")
                if base and os.path.exists(candidato):
                    return candidato
    else:
        for base in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"):
            candidato = os.path.join(base, nome)
            if os.path.exists(candidato):
                return candidato
    return None


def ffmpeg():
    """Return the path of the video encoder tool, or exit with an install hint if it is missing."""
    percorso = _trova("ffmpeg")
    if not percorso:
        sys.exit("ffmpeg non trovato: eseguire installa.ps1 (Windows) o installa.sh (Linux/macOS), "
                 "oppure installarlo a mano.")
    return percorso


def ffprobe():
    """Return the path of the media probing tool, or None when it is not installed."""
    return _trova("ffprobe")


def info_video(video):
    """Return (width, height, duration_in_seconds) of `video`, via ffprobe or OpenCV as fallback."""
    sonda = ffprobe()
    if sonda:
        uscita = subprocess.check_output([sonda, "-v", "error", "-select_streams", "v:0", "-show_entries",
                                          "stream=width,height:format=duration", "-of", "json",
                                          video]).decode()
        dati = json.loads(uscita)
        flusso = dati["streams"][0]
        durata = float(dati.get("format", {}).get("duration") or 0)
        return int(flusso["width"]), int(flusso["height"]), durata
    import cv2
    cattura = cv2.VideoCapture(video)
    w, h = int(cattura.get(3)), int(cattura.get(4))
    durata = cattura.get(cv2.CAP_PROP_FRAME_COUNT) / max(1.0, cattura.get(cv2.CAP_PROP_FPS))
    cattura.release()
    return w, h, durata


def mmss(t):
    """Format `t` seconds as MM:SS (minutes are not wrapped at 60)."""
    return "%02d:%02d" % (int(t // 60), int(t % 60))
