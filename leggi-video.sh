#!/usr/bin/env bash
set -u

QUI="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PY="$QUI/venv/bin/python"

if [ ! -x "$PY" ]; then
    printf '%s\n' "Strumento non installato: eseguire prima installa.sh" >&2
    exit 2
fi
if [ "$#" -lt 1 ]; then
    printf '%s\n' "Uso: leggi-video.sh <video o URL> [--uscita DIR] [--lingua en|it|auto] [--senza-occhio]" >&2
    exit 2
fi

printf 'DemoBrief %s\n' "$(head -n 1 "$QUI/version.txt")"
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
exec "$PY" "$QUI/src/leggi_video.py" "$@"
