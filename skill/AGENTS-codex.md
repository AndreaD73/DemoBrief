<!-- Da aggiungere al file AGENTS.md di Codex. Sostituire {{STRUMENTO}} con il percorso reale della cartella dello strumento. Usare quando l'utente passa un video o un link a un video da capire. -->

# Leggi video con DemoBrief

DemoBrief trasforma un video in `racconto.md` (testo compatto, circa 10-20 token al secondo di video) e salva i fotogrammi dei momenti d'azione in una cartella con il suo indice.

Cartella dello strumento: `{{STRUMENTO}}`

## Come si usa

1. Lancia (PowerShell, anche con un link; il video viene scaricato a 720p al massimo):

   ```powershell
   & "{{STRUMENTO}}\leggi-video.ps1" "<file video oppure URL>" -Uscita "<cartella di uscita>" [-Lingua en|it|auto] [-SenzaOcchio]
   ```

   Su Linux e macOS:

   ```bash
   "{{STRUMENTO}}/leggi-video.sh" "<file video oppure URL>"
   ```

   - Scegli tu una cartella di uscita (per esempio una sottocartella di lavoro dedicata a quel video).
   - Il tempo di elaborazione e' circa 1,5 volte la durata del video: per video lunghi lancia il comando in background e attendi la fine.
   - Se risponde che lo strumento non e' installato, esegui prima `{{STRUMENTO}}\installa.ps1`.
2. Leggi `<uscita>\racconto.md`. Le prime 3 righe dicono durata, token, fonti usate e legenda dei campi. Ogni riga e' un istante: `[mm:ss] VOCE "..." | TESTO ... | MOTO ... | VISTA ...`.
3. Leggi `<uscita>\fotogrammi\indice.md` e apri **al massimo 32 fotogrammi**, scegliendo quelli che chiariscono di piu' (valori, finestre di dialogo, risultati visibili); se sono meno di 32, aprili tutti. Misurato sul banco di prova: rispetto al solo testo la resa sale di 10-16 punti, a circa 0,66 $ e 90 secondi per video; oltre 32 non migliora piu'.
4. Scrivi tu il documento completo del video unendo racconto e immagini e salvalo accanto al video (`<nome-video>-documento.md`, con i riferimenti alle immagini usate). Poi rispondi all'utente con il riassunto e il percorso del file. Se la riga di intestazione segnala che la VISTA e' assente, dillo: significa solo che mancano le righe con la forma della geometria, il resto e' completo.

## Autonomia

L'utente non deve conoscere lo strumento: gli basta dire "leggi questo video". Fai tutto da solo, dall'installazione (se manca) al documento finale, senza chiedergli di lanciare comandi o di scegliere opzioni. Fermati solo se il video non e' accessibile.

## Attenzioni

- Il testo OCR puo' avere refusi (video a bassa risoluzione): non citarlo come ortografia certa.
- Le righe VISTA sono domande chiuse a un modello locale, valide solo se coerenti su due immagini: indicano che e' comparsa geometria nuova, non nominano mai il comando usato.
- Non incollare nella risposta l'intero racconto: riassumi e cita gli orari.
