#!/usr/bin/env bash
set -u

QUI="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
VENV="$QUI/venv"
PY="$VENV/bin/python"
SENZA_MODELLI=0
INSTALLA_SKILL=1

for argomento in "$@"; do
    case "$argomento" in
        --senza-modelli) SENZA_MODELLI=1 ;;
        --senza-skill) INSTALLA_SKILL=0 ;;
        *)
            printf '%s\n' "Opzione sconosciuta: $argomento" >&2
            exit 2
            ;;
    esac
done

passo() {
    printf '==> %s\n' "$1"
}

passo "Installo DemoBrief"
passo "Cerco Python 3.10-3.12"
BASE=""
for candidato in python3.12 python3.11 python3.10 python3; do
    if command -v "$candidato" >/dev/null 2>&1; then
        versione="$("$candidato" -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>/dev/null || true)"
        case "$versione" in
            3.10|3.11|3.12)
                BASE="$(command -v "$candidato")"
                break
                ;;
        esac
    fi
done
if [ -z "$BASE" ]; then
    printf '%s\n' "Serve Python 3.10, 3.11 o 3.12. Non trovato." >&2
    exit 1
fi
printf '    %s\n' "$BASE"

passo "Controllo ffmpeg e ffprobe"
mancante=0
command -v ffmpeg >/dev/null 2>&1 || mancante=1
command -v ffprobe >/dev/null 2>&1 || mancante=1
if [ "$mancante" -ne 0 ]; then
    if [ "$(uname -s)" = "Darwin" ]; then
        printf '%s\n' "Installare ffmpeg e ffprobe con: brew install ffmpeg" >&2
    else
        printf '%s\n' "Installare ffmpeg e ffprobe con: sudo apt install ffmpeg" >&2
    fi
    exit 3
fi

passo "Creo il venv"
if [ ! -x "$PY" ]; then
    "$BASE" -m venv "$VENV" || exit 1
fi
"$PY" -m pip install --upgrade pip >/dev/null || exit 1

passo "Installo le dipendenze"
"$PY" -m pip install -r "$QUI/requirements.txt" || exit 1

if [ "$(uname -s)" != "Darwin" ] && command -v nvidia-smi >/dev/null 2>&1; then
    passo "Scheda NVIDIA trovata: installo le librerie CUDA per Whisper"
    "$PY" -m pip install -r "$QUI/requirements-gpu.txt" || exit 1
else
    printf '%s\n' "    Runtime GPU non usato: uso il processore."
fi

if [ "$SENZA_MODELLI" -eq 0 ]; then
    passo "Scarico il modello vocale e il vocabolario dei token"
    export PYTHONUTF8=1
    export TIKTOKEN_CACHE_DIR="$QUI/cache"
    "$PY" -I -c "import sys;sys.path.insert(0,sys.argv[1]);import comune;from faster_whisper import download_model;download_model(comune.config()['whisper']['modello'])" "$QUI/src" ||
        printf '%s\n' "Modello whisper non scaricato: sarà scaricato al primo uso." >&2
    "$PY" -I -c "import tiktoken;tiktoken.get_encoding('cl100k_base')" ||
        printf '%s\n' "Vocabolario token non scaricato: i token saranno stimati a caratteri/4." >&2
fi

passo "Controllo Ollama"
INDIRIZZO="$("$PY" -c "import json,sys;print(json.load(open(sys.argv[1], encoding='utf-8-sig'))['ollama']['indirizzo'].rstrip('/'))" "$QUI/config.json")"
if ! curl -s --max-time 5 "$INDIRIZZO/api/tags" >/dev/null; then
    printf '%s\n' "Ollama non risponde: il racconto uscirà senza righe VISTA." >&2
fi

if [ "$INSTALLA_SKILL" -eq 1 ]; then
    passo "Installo la skill per Claude Code"
    DEST="$HOME/.claude/skills/leggi-video"
    mkdir -p "$DEST" || exit 1
    "$PY" - "$QUI" "$DEST/SKILL.md" <<'PY'
import pathlib
import sys

radice = pathlib.Path(sys.argv[1])
destinazione = pathlib.Path(sys.argv[2])
testo = (radice / "skill" / "SKILL.md").read_text(encoding="utf-8")
destinazione.write_text(testo.replace("{{STRUMENTO}}", str(radice)), encoding="utf-8", newline="\n")
PY
fi

printf '%s\n' "DemoBrief installato. Uso: ./leggi-video.sh <file video oppure URL>"
