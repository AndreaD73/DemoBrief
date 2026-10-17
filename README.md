# AD_Moviola

AD_Moviola is a local tool that converts a screen recording of a software tutorial into a compact `racconto.md` for AI agents, plus indexed frames for the moments where actions occur.

It is designed only for screen recordings of software tutorials. It is not a general-purpose video understanding tool. Interface and messages are in Italian.

## Platforms

- Windows: tested.
- Linux: tested on Ubuntu 24.04 (CPU and NVIDIA GPU for OCR).
- macOS: not yet tested.
- NVIDIA GPU transcription with Whisper is tested on Windows. On Linux, Whisper may fall back to CPU.

## Output

- `<output>/racconto.md`: a compact timeline.
- `<output>/fotogrammi/`: one image for each action phase.
- `<output>/fotogrammi/indice.md`: timestamp-to-image index.
- `<output>/_lavoro/`: intermediate files and logs. This directory is removed when `conserva_lavoro` is `false`.

Without `--uscita` on Linux/macOS or `-Uscita` on Windows, the output is written next to the input video in a directory named `<video-name>-letto`.

`VOCE` is speech, `TESTO` is OCR text, `SCHERMO` is a stable screen change, `MOTO` is an action or view movement, and `VISTA` is the optional Ollama observation. The optional Ollama eye and the `VISTA` field are tuned for tutorials recorded in 3D graphics applications.

`TESTO` is disabled by default through `config.json`, in `sezioni_spente`. Remove `"TESTO"` from that list to enable OCR text in `racconto.md`.

## Requirements

- Python 3.10, 3.11 or 3.12.
- ffmpeg and ffprobe.
- yt-dlp, installed from `requirements.txt`, used only for video links.
- Optional NVIDIA GPU on Windows or Linux.
- Optional Ollama for `VISTA` lines.
- On Linux: `sudo apt install ffmpeg python3-venv`.

## Installation

### Windows

```powershell
powershell -ExecutionPolicy Bypass -File .\installa.ps1
```

`installa.ps1` installs ffmpeg with winget when ffmpeg is missing and accepts the winget source and package agreements.

Add `-SenzaModelli` to skip model downloads. Add `-InstallaSkill` to install the Claude Code skill.

### Linux

```bash
sudo apt install ffmpeg python3-venv
chmod +x installa.sh leggi-video.sh
./installa.sh
```

### macOS

```bash
brew install ffmpeg
chmod +x installa.sh leggi-video.sh
./installa.sh
```

Add `--senza-modelli` to skip model downloads. Add `--installa-skill` to install the Claude Code skill.

## Usage

Windows:

```powershell
.\leggi-video.ps1 <video-or-URL> [-Uscita <directory>] [-Lingua en|it|auto] [-SenzaOcchio] [-Canali <name1,name2>]
```

Linux and macOS:

```bash
./leggi-video.sh <video-or-URL> [--uscita DIR] [--lingua en|it|auto] [--senza-occhio] [--canali name1,name2]
```

A URL is downloaded at a maximum height of 720 pixels.

## Experimental channels

The experimental channels are disabled by default. Enable them with `-Canali` on Windows or `--canali` on Linux/macOS, followed by a comma-separated list containing only these names:

- `cursore`: cursor clicks, pauses and targets.
- `stato`: visible interface state changes.
- `stile`: formatting and visual-style changes.
- `risultato`: the visible result of an operation.

Windows example:

```powershell
./leggi-video.ps1 tutorial.mp4 -Canali cursore,stato
```

Linux/macOS example:

```bash
./leggi-video.sh tutorial.mp4 --canali cursore,stato
```

## Configuration

`config.json` supports:

- `ollama.indirizzo`: Ollama server address.
- `ollama.modello`: Ollama model used for visual observations.
- `ollama.modello_risultato`: model used for result processing.
- `whisper.modello`: faster-whisper model.
- `whisper.dispositivo`: `auto`, `cuda` or `cpu`.
- `lingua`: `auto`, `en` or `it`.
- `processi_ocr`: number of OCR workers.
- `ocr_gpu`: enables OCR GPU support.
- `processi_ocr_gpu`: number of GPU OCR workers.
- `altezza_massima_download`: maximum downloaded video height.
- `conserva_lavoro`: keeps intermediate files and logs.
- `sezioni_spente`: disabled output sections.
- `occhio`: enables Ollama visual observations.

## Example output

```text
[00:03] VOCE "Open the generic editor and create a new document."
[00:11] SCHERMO new empty document in the main editor window
[00:18] VOCE "Select the heading and apply the title style."
[00:27] MOTO click on the alignment control
[00:35] SCHERMO the formatted tutorial page is visible
```

## Running the tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

## Known limitations

- The tool is specialized for software screen recordings.
- OCR accuracy decreases on low-resolution recordings.
- Crosshair cursors are not tracked.
- Long videos can require several gigabytes of RAM.
- CPU transcription is much slower than NVIDIA GPU transcription.
- Ollama output can vary between runs.
- Website downloads depend on yt-dlp and on the website.

## Legal notice

yt-dlp is third-party software. AD_Moviola does not decrypt or bypass DRM or other access protections, and protected content is not supported.

Downloading content from a website may violate that website's terms. The user is solely responsible for the download and use of the content. Use only videos that you have the right to use.

No warranty, see LICENSE.

Third-party notices are listed in `THIRD-PARTY-LICENSES.md`.
