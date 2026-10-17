---
name: leggi-video
description: Legge un video (file locale o link) e lo trasforma in un racconto testuale con voce, scritte a schermo e azioni, senza guardare le immagini. Da usare quando l'utente passa un video o un link a un video da capire (tutorial, registrazione dello schermo, lezione, demo), o chiede cosa succede, cosa viene detto o come si fa qualcosa in un video.
---

# Leggi video con DemoBrief

DemoBrief trasforma un video in `racconto.md` (testo compatto, circa 10-20 token al secondo di video) e salva i fotogrammi dei momenti d'azione in una cartella, da aprire soltanto quando una risposta dipende da un'immagine.

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
3. Rispondi all'utente usando solo il racconto. Se la riga di intestazione segnala che la VISTA e' assente, dillo: significa solo che mancano le righe con la forma della geometria, il resto e' completo.
4. Apri un'immagine solo se una riga e' ambigua e la risposta dipende davvero da quello che si vede: cerca l'orario in `<uscita>\fotogrammi\indice.md`, apri il png indicato e basta. Mai aprire tutti i fotogrammi, mai aprirli "per sicurezza".

## Attenzioni

- Il testo OCR puo' avere refusi (video a bassa risoluzione): non citarlo come ortografia certa.
- Le righe VISTA sono domande chiuse a un modello locale, valide solo se coerenti su due immagini: indicano che e' comparsa geometria nuova, non nominano mai il comando usato.
- Non incollare nella risposta l'intero racconto: riassumi e cita gli orari.
