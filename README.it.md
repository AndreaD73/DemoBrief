English: [README.md](README.md)

# DemoBrief

Trasforma le registrazioni dello schermo di dimostrazioni e tutorial di software in un breve documento Markdown illustrato, pensato per gli agenti AI.

Repository: https://github.com/AndreaD73/DemoBrief

Serve solo per le registrazioni dello schermo di tutorial di software. Non è uno strumento generico per capire qualsiasi video. Interfaccia e messaggi sono in italiano.

## Perché DemoBrief

Un tutorial di software di 4-5 minuti diventa un documento di circa 2.000-4.000 token. Su 7 tutorial reali, DemoBrief ha prodotto in media circa 3.000 token, contro circa 45.000 token di uno strumento basato sui fotogrammi che fa ispezionare il video a un agente.

L'elaborazione è locale e gratuita: DemoBrief non usa nessun servizio in rete e non richiede nessuna chiave API.

L'elaborazione richiede qualche minuto per video su un PC con scheda grafica NVIDIA, e più tempo su un computer senza scheda grafica.

DemoBrief è un aiuto, o skill, per gli agenti AI e non un riassuntore autonomo:

1. DemoBrief produce `racconto.md` e `fotogrammi/`.
2. Si passa la cartella di uscita a un agente come Claude Code o Codex e gli si chiede di scrivere il documento di specifiche completo, aprendo solo le immagini che gli servono.

L'agente legge poche migliaia di token invece di guardare il video originale.

## Piattaforme

- Windows: provato.
- Linux: provato su Ubuntu 24.04 (CPU e scheda NVIDIA per l'OCR).
- macOS: non ancora provato.
- La trascrizione con Whisper su scheda NVIDIA è provata su Windows. Su Linux, Whisper può ripiegare sulla CPU.

## Uscita

- `<uscita>/racconto.md`: una cronologia compatta.
- `<uscita>/fotogrammi/`: un'immagine per ogni fase d'azione.
- `<uscita>/fotogrammi/indice.md`: indice che collega ogni istante alla sua immagine.
- `<uscita>/_lavoro/`: file intermedi e registri. Questa cartella viene eliminata quando `conserva_lavoro` è `false`.

Senza `--uscita` su Linux/macOS o `-Uscita` su Windows, l'uscita viene scritta accanto al video di ingresso, in una cartella chiamata `<nome-del-video>-letto`. Per un link, la cartella predefinita si chiama `video-letto` e si trova nella cartella corrente.

`VOCE` è il parlato, `TESTO` è il testo letto con l'OCR, `SCHERMO` è un cambiamento stabile dello schermo, `MOTO` è un'azione o un movimento della vista, e `VISTA` è l'osservazione facoltativa di Ollama. L'occhio facoltativo di Ollama e il campo `VISTA` sono tarati per i tutorial registrati in programmi di grafica 3D.

L'occhio di Ollama è spento per impostazione predefinita (`"occhio": false` in `config.json`): un'esecuzione predefinita non produce righe `VISTA` anche se Ollama e il modello sono installati. Per attivarlo si imposta `"occhio": true`. Quando `VISTA` manca, per qualsiasi motivo, la seconda riga di `racconto.md` lo dice ("VISTA assente: ..."); la legenda sulla terza riga elenca sempre `VISTA`.

`TESTO` è disattivato per impostazione predefinita tramite `config.json`, nell'elenco `sezioni_spente`. Per avere il testo OCR in `racconto.md` si toglie `"TESTO"` da quell'elenco.

## Requisiti

- Python 3.10, 3.11 o 3.12.
- ffmpeg e ffprobe.
- yt-dlp, installato da `requirements.txt`, usato solo per i link ai video.
- Scheda grafica NVIDIA facoltativa, su Windows o Linux.
- L'OCR funziona sulla CPU per impostazione predefinita. L'OCR su scheda grafica è sperimentale: si installa `onnxruntime-gpu==1.31.0` al posto di `onnxruntime`; viene usato automaticamente quando CUDA funziona.
- Ollama facoltativo per le righe `VISTA` (richiede anche `"occhio": true` in `config.json`).
- Su Linux: `sudo apt install ffmpeg python3-venv`.

## Installazione

### Windows

```powershell
powershell -ExecutionPolicy Bypass -File .\installa.ps1
```

`installa.ps1` installa ffmpeg con winget quando ffmpeg manca e accetta gli accordi della sorgente e dei pacchetti di winget.

Con `-SenzaModelli` si saltano i download dei modelli. Con `-InstallaSkill` si installa la skill per Claude Code.

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

Con `--senza-modelli` si saltano i download dei modelli. Con `--installa-skill` si installa la skill per Claude Code.

## Uso

Windows:

```powershell
.\leggi-video.ps1 <video-o-URL> [-Uscita <cartella>] [-Lingua en|it|auto] [-SenzaOcchio] [-Canali <nome1,nome2>]
```

Linux e macOS:

```bash
./leggi-video.sh <video-o-URL> [--uscita DIR] [--lingua en|it|auto] [--senza-occhio] [--canali nome1,nome2]
```

Un link viene scaricato a un'altezza massima di 720 pixel (`altezza_massima_download`). Le dirette vengono rifiutate, i file oltre 4 GB vengono rifiutati e il download ha dei limiti di tempo (60 secondi per il titolo, 2 ore per il video).

`-SenzaOcchio` / `--senza-occhio` spegne l'occhio per quell'esecuzione. Non riguarda il canale sperimentale `risultato`, che interroga comunque Ollama.

I passi di analisi (movimento, trascrizione, OCR) girano insieme per impostazione predefinita quando ci sono almeno 6 GB di memoria libera; altrimenti, o con `"parallelo": false`, girano uno dopo l'altro. Un passo che dura più di `tetto_fase_minuti` viene fermato e il programma termina con codice 4.

## Canali sperimentali

I canali sperimentali sono disattivati per impostazione predefinita. Si attivano con `-Canali` su Windows o `--canali` su Linux/macOS, seguito da un elenco separato da virgole che contenga solo questi nomi:

- `cursore`: clic del cursore, pause e bersagli.
- `stato`: cambiamenti visibili dello stato dell'interfaccia.
- `stile`: cambiamenti di formattazione e di stile visivo.
- `risultato`: il risultato visibile di un'operazione.

Esempio per Windows:

```powershell
./leggi-video.ps1 tutorial.mp4 -Canali cursore,stato
```

Esempio per Linux/macOS:

```bash
./leggi-video.sh tutorial.mp4 --canali cursore,stato
```

## Configurazione

`config.json` accetta:

- `ollama.indirizzo`: indirizzo del server Ollama.
- `ollama.modello`: modello Ollama usato per le osservazioni visive.
- `ollama.modello_risultato`: modello usato per l'elaborazione del risultato.
- `whisper.modello`: modello faster-whisper.
- `whisper.dispositivo`: `auto`, `cuda` o `cpu`.
- `lingua`: `auto`, `en` o `it`.
- `whisper.prompt`: testo facoltativo che sostituisce il suggerimento di trascrizione incorporato.
- `tetto_fase_minuti`: limite di tempo per ogni passo, in minuti (predefinito 30); un passo che lo supera viene fermato e il programma termina con codice 4.
- `parallelo`: fa girare insieme i passi indipendenti (predefinito `true`; si spegne anche quando la memoria libera è sotto i 6 GB).
- `decodifica_ffmpeg`: decodifica i fotogrammi del movimento con ffmpeg (predefinito `true`; con `false` si usa OpenCV).
- `processi_ocr`: numero di processi OCR (CPU; massimo 3).
- `ocr_gpu`: attiva l'OCR su scheda grafica (usato solo quando CUDA funziona).
- `processi_ocr_gpu`: numero di processi OCR su scheda grafica (massimo 2).
- `altezza_massima_download`: altezza massima del video scaricato.
- `conserva_lavoro`: conserva i file intermedi e i registri.
- `sezioni_spente`: sezioni di uscita disattivate (valore distribuito: `TESTO`, `VALORI`, `DOVE`).
- `occhio`: attiva le osservazioni visive di Ollama (valore distribuito: `false`, spento).
- `tabelle`: lettere che scelgono le funzioni per le tabelle: `S` (tabelle lette da `SCHERMO`), `F` (conservazione delle frasi intere, il valore distribuito) e `I` (fotogrammi delle tabelle). Si usano solo queste lettere; qualsiasi altra lettera viene ignorata.

## Esempio di uscita

```text
[00:03] VOCE "Open the generic editor and create a new document."
[00:11] VOCE "Select the heading and apply the title style." | SCHERMO 00:12 finestra al centro: Style / Title / Heading 1 | MOTO 00:14 clic1
[00:21] VOCE "Then rotate the view to check the result." | MOTO 00:22-00:27 orbita 5s
[00:30] VOCE "The formatted page is now ready." | MOTO 00:31 clic2 | SCHERMO 00:33 pagina: Tutorial / Introduction
[00:41] MOTO 00:41 mesh 2g
```

I nomi delle aree in `SCHERMO` (per esempio `pagina`, `finestra al centro`, `pannello dx`) sono in italiano.

## Per gli agenti AI che leggono l'uscita

1. Leggere `racconto.md` e `fotogrammi/indice.md`.
2. Aprire al massimo 32 fotogrammi da `fotogrammi/`, scegliendo quelli che chiariscono di più (valori, finestre di dialogo, risultati visibili). Se sono meno di 32, aprirli tutti.

Misurato sul banco di prova (14 tutorial, voto dato alla cieca), rispetto al solo testo: +10-16 punti, a circa 0,66 USD e 90 secondi per video con Claude Opus. Oltre 32 non c'è nessun guadagno: con 64 immagini il risultato è lo stesso. Questo coincide con la skill inclusa (`skill/SKILL.md`).

## Eseguire i test

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

## Limiti noti

- Lo strumento è specializzato per le registrazioni dello schermo di software.
- La precisione dell'OCR cala sulle registrazioni a bassa risoluzione.
- I cursori a mirino non vengono seguiti.
- I video lunghi possono richiedere diversi gigabyte di RAM.
- La trascrizione sulla CPU è molto più lenta di quella su scheda NVIDIA.
- L'uscita di Ollama può variare da un'esecuzione all'altra.
- I download dai siti dipendono da yt-dlp e dal sito.

## Avvertenze legali

yt-dlp è un software di terzi. DemoBrief non decifra né aggira DRM o altre protezioni di accesso, e i contenuti protetti non sono supportati.

Scaricare contenuti da un sito può violare le condizioni d'uso del sito. L'utente è il solo responsabile del download e dell'uso dei contenuti. Si usino solo video che si ha il diritto di usare.

Nessuna garanzia, vedere LICENSE.

Le note sui software di terzi sono elencate in `THIRD-PARTY-LICENSES.md`.
