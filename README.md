Italiano: [README.it.md](README.it.md)

# DemoBrief

Turn screen-recorded software demos and tutorials into an illustrated Markdown brief for AI agents.

Repository: https://github.com/AndreaD73/DemoBrief

It is designed only for screen recordings of software tutorials. It is not a general-purpose video understanding tool. Interface and messages are in Italian.

## Why DemoBrief

A 4-5 minute software tutorial becomes a brief of about 3,000 tokens (2,000-4,000), plus a folder of frames the agent opens only when it needs them.

Measured on 7 real tutorials with Claude Opus (API prices, indicative):

| | What the agent reads | Average cost per video | Key facts captured |
|---|---|---|---|
| Watching the video directly (1 image every 2 seconds, about 140 images) | tens of thousands of image tokens | about $1.40 | 85% |
| DemoBrief `racconto.md` only | about 3,000 text tokens | about $0.30 | 71% |
| `racconto.md` + about 30 frames chosen by the agent | 3,000 tokens + about 30 images | about $0.65 | 80% |

In short: DemoBrief costs about half (with images) or a fifth (text only) and captures most of what an agent catches when it watches the whole video. It is not a 100% replacement: data tables in business applications, for example, are still partly lost. Figures come from our own benchmark on 7 tutorials, plus one 11-minute Blender tutorial checked afterwards (about 5,800 tokens; an agent reading it used about 2.4 times fewer tokens than one watching all frames); results on other videos may vary.

Processing is local and free: DemoBrief uses no cloud service and requires no API key.

Processing takes a few minutes per video on a PC with an NVIDIA GPU, and longer on a CPU-only computer.

## How you use it

Install DemoBrief (see Installation: the skill for Claude Code is installed too), then tell your agent:

> Read this video: `<file or link>`

The agent does everything on its own: it runs DemoBrief, reads `racconto.md`, opens the frames it needs and writes the complete document next to the video. You do not run commands or learn options. For Codex, see `skill/AGENTS-codex.md`.

## What to expect

- Disk: about 2.5 GB for the Python environment on Windows with an NVIDIA GPU (about 1.4 GB of that is CUDA libraries, skipped without a GPU), plus the speech and OCR models downloaded on first run.
- Time: about 6 minutes for a 5-minute tutorial, and about 11 minutes for an 11-minute tutorial, on a PC with an NVIDIA RTX 3070 (roughly real time). DemoBrief saves tokens, not time: watching a video frame by frame can be faster. Time on a CPU-only computer has not been measured.
- Accuracy: text read from the screen (OCR) can contain typos (for example "Vlewport" for "Viewport"); the agent can check the frames when it matters.
- The Claude Code skill is written to `~/.claude/skills/leggi-video` (use `-SenzaSkill` / `--senza-skill` to skip it).
- Nothing leaves your computer, except the download of a video when you pass a link.
- The interface and the messages of the tool are in Italian; the brief follows the language of the video.

## Check it yourself

1. Look at the size of `racconto.md` (the header gives the estimated tokens).
2. Ask your agent to read the folder, then check the usage it reports (for Claude Code, `/context`).
3. Ask the agent 3-4 questions that only the video can answer (which menu, which value, at what minute) and compare with the video.

The benchmark videos are CC BY tutorials; `example/` holds the output for one of them, but not the video itself, so download it from its original source before comparing.

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

Without `--uscita` on Linux/macOS or `-Uscita` on Windows, the output is written next to the input video in a directory named `<video-name>-letto`. For a URL, the default is a directory named `video-letto` in the current working directory.

`VOCE` is speech, `TESTO` is OCR text, `SCHERMO` is a stable screen change, `MOTO` is an action or view movement, and `VISTA` is the optional Ollama observation. The optional Ollama eye and the `VISTA` field are tuned for tutorials recorded in 3D graphics applications.

The Ollama eye is off by default (`"occhio": false` in `config.json`), so a default run produces no `VISTA` lines even when Ollama and the model are installed. Set `"occhio": true` to enable it. When `VISTA` is missing, for any reason, the second line of `racconto.md` says so ("VISTA assente: ..."); the legend on the third line always lists `VISTA`.

`TESTO` is disabled by default through `config.json`, in `sezioni_spente`. Remove `"TESTO"` from that list to enable OCR text in `racconto.md`.

## Requirements

- Python 3.10, 3.11 or 3.12.
- ffmpeg and ffprobe.
- yt-dlp, installed from `requirements.txt`, used only for video links.
- Optional NVIDIA GPU on Windows or Linux.
- OCR runs on CPU by default. GPU OCR is experimental: install `onnxruntime-gpu==1.31.0` in place of `onnxruntime`; it is used automatically when CUDA works.
- Optional Ollama for `VISTA` lines (also requires `"occhio": true` in `config.json`).
- On Linux: `sudo apt install ffmpeg python3-venv`.

## Installation

### Windows

```powershell
powershell -ExecutionPolicy Bypass -File .\installa.ps1
```

`installa.ps1` installs ffmpeg with winget when ffmpeg is missing and accepts the winget source and package agreements.

With an NVIDIA GPU the installer also downloads about 1.4 GB of CUDA libraries to speed up transcription; without one it skips them and uses the CPU.

Add `-SenzaModelli` to skip model downloads. Add `-SenzaSkill` to skip the Claude Code skill (installed by default).

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

Add `--senza-modelli` to skip model downloads. Add `--senza-skill` to skip the Claude Code skill (installed by default).

## Usage

Windows:

```powershell
.\leggi-video.ps1 <video-or-URL> [-Uscita <directory>] [-Lingua en|it|auto] [-SenzaOcchio] [-Canali <name1,name2>]
```

Linux and macOS:

```bash
./leggi-video.sh <video-or-URL> [--uscita DIR] [--lingua en|it|auto] [--senza-occhio] [--canali name1,name2]
```

A URL is downloaded at a maximum height of 720 pixels (`altezza_massima_download`). Live streams are refused, files over 4 GB are refused, and the download has timeouts (60 seconds for the title, 2 hours for the video).

`-SenzaOcchio` / `--senza-occhio` switches the eye off for the run. It does not affect the experimental `risultato` channel, which queries Ollama anyway.

Analysis steps (movement, transcription, OCR) run concurrently by default when at least 6 GB of memory is free; otherwise, or with `"parallelo": false`, they run one after another. A step that runs longer than `tetto_fase_minuti` is stopped and the program exits with code 4.

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
- `whisper.prompt`: optional text that replaces the built-in transcription prompt.
- `tetto_fase_minuti`: per-step time cap in minutes (default 30); a step over the cap is stopped and the program exits with code 4.
- `parallelo`: runs independent steps concurrently (default `true`; also off when less than 6 GB of memory is free).
- `decodifica_ffmpeg`: decodes the movement frames through ffmpeg (default `true`; `false` uses OpenCV).
- `processi_ocr`: number of OCR workers (CPU; capped at 3).
- `ocr_gpu`: enables OCR GPU support (used only when CUDA works).
- `processi_ocr_gpu`: number of GPU OCR workers (capped at 2).
- `altezza_massima_download`: maximum downloaded video height.
- `conserva_lavoro`: keeps intermediate files and logs.
- `sezioni_spente`: disabled output sections (shipped value: `TESTO`, `VALORI`, `DOVE`).
- `occhio`: enables Ollama visual observations (shipped value: `false`, off).
- `tabelle`: letters selecting the table features: `S` (tables read from `SCHERMO`), `F` (whole-sentence preservation, the shipped value) and `I` (table frames). Use only these letters; any other letter is ignored.

## Example output

```text
[00:03] VOCE "Open the generic editor and create a new document."
[00:11] VOCE "Select the heading and apply the title style." | SCHERMO 00:12 finestra al centro: Style / Title / Heading 1 | MOTO 00:14 clic1
[00:21] VOCE "Then rotate the view to check the result." | MOTO 00:22-00:27 orbita 5s
[00:30] VOCE "The formatted page is now ready." | MOTO 00:31 clic2 | SCHERMO 00:33 pagina: Tutorial / Introduction
[00:41] MOTO 00:41 mesh 2g
```

The area names in `SCHERMO` (for example `pagina`, `finestra al centro`, `pannello dx`) are in Italian.

## For AI agents reading the output

1. Read `racconto.md` and `fotogrammi/indice.md`.
2. Open at most 32 frames from `fotogrammi/`, choosing those that clarify the most (values, dialogs, visible results). If there are fewer than 32, open them all.

Measured on the test bench (14 tutorials, blind scoring), compared with the text alone: +10-16 points, at about 0.66 USD and 90 seconds per video with Claude Opus. Beyond 32 there is no gain: 64 images gave the same result. This matches the bundled skill (`skill/SKILL.md`).

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

yt-dlp is third-party software. DemoBrief does not decrypt or bypass DRM or other access protections, and protected content is not supported.

Downloading content from a website may violate that website's terms. The user is solely responsible for the download and use of the content. Use only videos that you have the right to use.

No warranty, see LICENSE.

Third-party notices are listed in `THIRD-PARTY-LICENSES.md`.
