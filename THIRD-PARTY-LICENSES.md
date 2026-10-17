# Licenze di terze parti

This file is in Italian; summary: all Python dependencies are permissive (MIT/BSD/Apache/MPL/LGPL via dynamic linking); NVIDIA CUDA wheels are proprietary and optional; ffmpeg is an external program.

Verifica del 16 ottobre 2026 sui metadati dei pacchetti installati nel venv (`venv\Lib\site-packages\*.dist-info`, campi `License-Expression`, `License`, `Classifier`), sui file di licenza inclusi nei pacchetti e, per i modelli, sulla scheda del modello (Hugging Face / Ollama). Nulla e' ridistribuito insieme allo strumento: tutto viene scaricato dall'utente da PyPI, Hugging Face, Ollama o winget al momento dell'installazione.

Codice dello strumento: MIT, vedi LICENSE.

## Da segnalare: licenze NON permissive o con condizioni

| Componente | Licenza | Perche' conta |
|---|---|---|
| onnxruntime-gpu | MIT | Runtime GPU opzionale per OCR, scaricato dall'utente da PyPI. |
| nvidia-cublas-cu12, nvidia-cuda-nvrtc-cu12, nvidia-cudnn-cu12 e i pacchetti nvidia-* da essi richiesti | NVIDIA Proprietary (`LicenseRef-NVIDIA-Proprietary`) | Proprietari NVIDIA, scaricati dall'utente da PyPI. Sono opzionali (`requirements-gpu.txt`) e non vengono distribuiti nel repository. |
| av (PyAV) 15.1.0 | BSD-3-Clause (pacchetto); DLL FFmpeg incluse: LGPL v3+ (stringa letta in avutil) | Collegamento dinamico di librerie LGPL: ok per l'uso, chi ridistribuisce deve accompagnare i testi di licenza. |
| ffmpeg (programma esterno, non pacchetto Python) | La build installata da winget (Gyan.FFmpeg "full") e' GPL v3 (`--enable-gpl --enable-version3`) | Lo strumento lo lancia come programma separato e non lo include: nessun obbligo di copyleft sul nostro codice, ma non va impacchettato nel repository ne' in un installatore. |
| opencv-python 5.0.0.93 | Apache-2.0 (pacchetto); la ruota include FFmpeg sotto LGPL (`LICENSE-3RD-PARTY.txt`) | Linking dinamico della libreria LGPL gia' contenuta nella ruota: va bene per l'uso, ma chi ridistribuisce il pacchetto deve accompagnare i testi di licenza. |
| certifi 2026.7.22 | MPL-2.0 | Copyleft debole a livello di file: nessun effetto sul nostro codice se non si modificano i file di certifi. |
| tqdm 4.70.1 | MPL-2.0 AND MIT | Come sopra. |
| pillow 12.3.0 | MIT-CMU (HPND) | Permissiva, citata per la forma non comune. |
| regex 2026.9.29 | Apache-2.0 AND CNRI-Python | Permissiva. |
| typing_extensions 4.16.0 | PSF-2.0 | Permissiva. |
| yt-dlp 2026.8.19 | Unlicense (pubblico dominio) | Nessuna condizione. Resta il rischio d'uso: scaricare video e' soggetto ai termini del sito di origine e al diritto d'autore. |

Nessun pacchetto Python usa GPL/AGPL. L'unica componente GPL e' il programma ffmpeg esterno.

## Dipendenze Python dirette

| Pacchetto | Versione | Licenza |
|---|---|---|
| faster-whisper | 1.2.1 | MIT |
| rapidocr-onnxruntime | 1.4.4 | Apache-2.0 (i modelli PP-OCR inclusi derivano da PaddleOCR, Apache-2.0) |
| opencv-python | 5.0.0.93 | Apache-2.0 (+ FFmpeg LGPL nella ruota) |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| tiktoken | 0.14.0 | MIT |
| requests | 2.34.2 | Apache-2.0 |
| yt-dlp | 2026.8.19 | Unlicense |

## Dipendenze indirette

| Pacchetto | Versione | Licenza |
|---|---|---|
| anyio | 4.15.1 | MIT |
| av (PyAV) | 15.1.0 | BSD-3-Clause (la ruota include DLL FFmpeg `avcodec/avformat/...` in `av.libs`: la stringa di licenza incorporata in avutil-*.dll dice LGPL v3 o successiva) |
| certifi | 2026.7.22 | MPL-2.0 |
| charset-normalizer | 3.5.2 | MIT |
| click | 8.5.0 | BSD-3-Clause |
| colorama | 0.4.6 | BSD |
| ctranslate2 | 4.8.3 | MIT |
| filelock | 4.2.0 | MIT |
| flatbuffers | 25.12.19 | Apache-2.0 |
| fsspec | 2026.9.0 | BSD-3-Clause |
| h11 | 0.16.0 | MIT |
| hf-xet | 1.7.0 | Apache-2.0 |
| httpcore2 | 2.13.1 | BSD-3-Clause |
| httpx2 | 2.13.1 | BSD-3-Clause |
| huggingface_hub | 2.2.0 | Apache-2.0 |
| idna | 3.20 | BSD-3-Clause |
| onnxruntime | 1.31.0 | MIT |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pillow | 12.3.0 | MIT-CMU |
| protobuf | 7.36.2 | BSD-3-Clause |
| pyclipper | 1.4.0 | MIT |
| PyYAML | 6.0.3 | MIT |
| regex | 2026.9.29 | Apache-2.0 AND CNRI-Python |
| shapely | 2.2.0 | BSD-3-Clause |
| six | 1.17.0 | MIT |
| tokenizers | 0.23.3 | Apache-2.0 (dal classificatore del pacchetto) |
| tqdm | 4.70.1 | MPL-2.0 AND MIT |
| truststore | 0.10.5 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| urllib3 | 2.8.0 | MIT |
| nvidia-cublas-cu12 / nvidia-cuda-nvrtc-cu12 / nvidia-cudnn-cu12 | 12.9.2.10 / 12.9.86 / 9.27.0.42 | NVIDIA Proprietary (opzionali, vedi sopra) |

## Modelli e programmi esterni

| Componente | Licenza | Fonte della verifica |
|---|---|---|
| Whisper "medium" in formato CTranslate2 (Systran/faster-whisper-medium) | MIT | scheda Hugging Face (`card_data.license`) |
| Modelli OCR PP-OCRv4 inclusi in rapidocr-onnxruntime | Apache-2.0 | metadati del pacchetto (`License: Apache-2.0`) |
| qwen3-vl:8b (occhio, via Ollama, facoltativo) | Apache-2.0 | `ollama show qwen3-vl:8b --license` |
| Ollama | programma esterno opzionale, installato dall'utente, non distribuito qui | sito e distribuzione ufficiale di Ollama |
| ffmpeg | vedi tabella sopra | `ffmpeg -version` |

Per rigenerare l'elenco dei pacchetti: `venv\Scripts\python.exe -m pip freeze` e lettura dei metadati con `importlib.metadata`.
