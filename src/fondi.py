# -*- coding: utf-8 -*-
"""Generic merge of channel events into the story; it knows nothing about individual channels.

Reads the eventi_*.json files written by the channels (schema in eventi.py) plus the OCR
files through eventi.Scena; writes nothing, it only hands the story builder the text
fragments (sections, lines, deictic hints) to print around each spoken sentence.
"""
import glob
import os
import string

import comune
import eventi
from testi import stessi

SOGLIE = {'default': 0.50}
DOTI = {'default': 60.0}
ORDINE = ('VOCE', 'TESTO', 'SCHERMO', 'CURSORE', 'STATO', 'SEGNI', 'MOTO', 'VISTA', 'DINAMICA',
          'STRUTTURA', 'SUONO')
DEITTICI = frozenset(('this', 'here', 'that', 'these', 'those', 'there', 'questo', 'questa', 'questi',
                      'queste', 'qui', 'qua', 'quello', 'quella', "li'", "la'"))
PRECEDENZA = {'segno': 5, 'evidenziazione': 4, 'comparsa': 4, 'cambio': 3, 'clic': 2, 'sosta': 1}

# An event at least this strong, with text, can complete a deictic word ("this" -> "this [->Save]").
FORZA_DEITTICO = 0.5
# A pause shorter than this (seconds) is not worth pointing at.
DURATA_SOSTA_MINIMA = 0.5
# Most deictic words completed in one sentence.
MAX_DEITTICI = 2
# Seconds before an event in which the OCR text under the pointer is looked up when it has no text.
ANTICIPO_TESTO = 0.3
# Search radius (fraction of the frame) for that OCR lookup.
RAGGIO_TESTO = 0.03
# Longest text kept from the OCR lookup.
MAX_TESTO = 80
# Events closer than this many seconds can be merged as duplicates.
FINESTRA_DOPPIONI = 1.0
# An event farther than this many seconds from every sentence is left "loose".
DISTANZA_FRASE_MAX = 1.5
# Smallest video length (in minutes) used for the per-minute event budget: one second.
MINUTI_MINIMI = 1.0 / 60.0


def sezioni_spente():
    """Return the set of section names (upper case) switched off by environment or config."""
    elenco = comune.ambiente('SENZA')
    if elenco is None:
        try:
            elenco = ','.join(comune.config().get('sezioni_spente', []))
        except Exception:
            elenco = ''
    return set(x.strip().upper() for x in elenco.split(',') if x.strip())


class Fusione:
    """Merges the events of all channels and serves them to the story builder, sentence by sentence."""

    def __init__(self, out, parole, frasi, testi, schermate, movimenti, durata):
        """Load the channel files found in `out`, then filter, name, de-duplicate and assign events.

        `parole`, `frasi` are the transcript words and sentences; `schermate` are the on-screen
        texts; `durata` is the video length in seconds.
        """
        self.out = out
        self.parole = parole
        self.frasi = frasi
        self.testi = testi
        self.schermate = schermate
        self.movimenti = movimenti
        self.durata = durata
        self.senza = sezioni_spente()
        self.stat = {}
        self.documenti = []
        self.eventi = []
        self.per_frase = {i: [] for i in range(len(frasi))}
        self.righe = {i: [] for i in range(len(frasi))}
        self.sciolti = []
        self.legende = {}
        self.formati = {}
        self.scene = eventi.Scena(out, schermate)
        self._carica()
        if self.documenti:
            self._filtra()
            self._nomina()
            self._doppioni()
            self._assegna()

    def attiva(self, sezione):
        """True when the section is not switched off."""
        return sezione.upper() not in self.senza

    def tieni(self, sezione, valori):
        """Return `valori` if the section is active, otherwise an empty list."""
        return valori if self.attiva(sezione) else []

    def voce(self, indice, testo):
        """Return the sentence `testo` (number `indice`), completing deictic words with event text.

        A word such as "this" becomes "this [->Keep Groups]" when a strong event with text
        happened near it; at most two words per sentence are completed. Empty if VOCE is off.
        """
        if not self.attiva('VOCE'):
            return ''
        if not self.attiva('DEITTICI') or indice not in self.per_frase:
            return testo
        candidati = [e for e in self.per_frase[indice] if e.get('_deittico') and e.get('testo')]
        parole = testo.split()
        usati = set()
        aggiunti = 0

        def chiave(e, pos):
            """Ranking of a candidate: event kind, strength, then closeness in time to the word."""
            return (PRECEDENZA.get(e['tipo'], 0), e['forza'], -abs(e['t'] - self._tempo_parola(indice, pos)))

        for pos, parola in enumerate(parole):
            nuda = parola.lower().strip(string.punctuation)
            if nuda not in DEITTICI:
                continue
            validi = []
            for e in candidati:
                if e['testo'].lower() in testo.lower():
                    continue
                if e['testo'].lower() in usati:
                    continue
                if nuda in ('that', 'there') and e['tipo'] not in ('clic', 'segno', 'evidenziazione'):
                    continue
                validi.append(e)
            if not validi:
                continue
            scelto = max(validi, key=lambda e: chiave(e, pos))
            parole[pos] = parola + ' [→' + scelto['testo'] + ']'
            usati.add(scelto['testo'].lower())
            aggiunti += 1
            if aggiunti == MAX_DEITTICI:
                break
        return ' '.join(parole)

    def sezioni(self, indice):
        """Return the section strings ("NAME fragment; fragment") to print after sentence `indice`.

        Also records in `stat` how many fragments each section printed.
        """
        gruppi = {}
        for e in self.per_frase.get(indice, []):
            if e['stampa'] == 'sezione' and self.attiva(e['_sezione']):
                gruppi.setdefault(e['_sezione'], []).append(e)
        risultato = []
        for sezione in self._ordine_sezioni(gruppi):
            parti = [self._formatta(e) for e in gruppi[sezione]]
            parti = [x for x in parti if x]
            if parti:
                risultato.append(sezione + ' ' + '; '.join(parti))
                self.stat[e_key(sezione, 'stampati')] = len(parti)
        return risultato

    def righe_prima(self, indice):
        """Return the one-line events "[mm:ss] SECTION text" to print before sentence `indice`."""
        if not self.per_frase.get(indice):
            return []
        t = self.frasi[indice][0][0]
        risultato = []
        for e in self.per_frase[indice]:
            if e['stampa'] == 'riga' and self.attiva(e['_sezione']):
                testo = self._formatta(e)
                if testo:
                    risultato.append('[%s] %s %s' % (mmss(t), e['_sezione'], testo))
        return risultato

    def isolati(self):
        """Return the events far from every sentence as (time, None, ('fx', time, line)) entries."""
        risultato = []
        for e in self.sciolti:
            if e['stampa'] in ('sezione', 'riga') and self.attiva(e['_sezione']):
                testo = self._formatta(e)
                if testo:
                    riga = '[%s] %s %s' % (mmss(e['t']), e['_sezione'], testo)
                    risultato.append((e['t'], None, ('fx', e['t'], riga)))
        return risultato

    def usate(self):
        """Return "channel (N eventi)" for each loaded channel that still has events after filtering."""
        risultato = []
        for doc in self.documenti:
            totale = sum(1 for e in self.eventi if e['_canale'] == doc['canale'])
            if totale:
                risultato.append('%s (%d eventi)' % (doc['canale'], totale))
        return risultato

    def legenda(self):
        """Return the legends of the sections that have events, in print order, joined by "; "."""
        presenti = {x: [] for x in set(e['_sezione'] for e in self.eventi)}
        return '; '.join(self.legende[s] for s in self._ordine_sezioni(presenti)
                         if self.attiva(s) and self.legende.get(s))

    def _carica(self):
        """Read every valid eventi_*.json in the output folder into `eventi`, `legende`, `formati`."""
        invalidi = 0
        for percorso in sorted(glob.glob(os.path.join(self.out, 'eventi_*.json'))):
            doc, errore = eventi.carica(percorso)
            if doc is None:
                invalidi += 1
                continue
            self.documenti.append(doc)
            self.legende[doc['sezione'].upper()] = doc['legenda']
            for tipo, formato in doc['formati'].items():
                self.formati[(doc['canale'], tipo)] = formato
            for originale in doc['eventi']:
                e = dict(originale)
                e['rif'] = list(originale['rif'])
                e['dati'] = dict(originale['dati'])
                e['_canale'] = doc['canale']
                e['_sezione'] = doc['sezione'].upper()
                self.eventi.append(e)
        if invalidi:
            self.stat['eventi_file_invalidi'] = invalidi
        if self.documenti:
            self.stat['eventi_file_validi'] = len(self.documenti)
            self.stat['eventi_letti'] = len(self.eventi)

    def _filtra(self):
        """Drop weak events (below the per-type threshold) and cap each channel to its per-minute budget."""
        tenuti = []
        soglia_scartati = 0
        dote_scartati = 0
        per_canale = {}
        for e in self.eventi:
            if e['forza'] < SOGLIE.get(e['tipo'], SOGLIE['default']):
                soglia_scartati += 1
                continue
            per_canale.setdefault(e['_canale'], []).append(e)
        for canale, valori in per_canale.items():
            minuti = max(float(self.durata) / 60.0, MINUTI_MINIMI)
            limite = max(1, int(DOTI.get(canale, DOTI['default']) * minuti))
            valori.sort(key=lambda e: (-e['forza'], e['t'], e['id']))
            tenuti.extend(valori[:limite])
            dote_scartati += max(0, len(valori) - limite)
        self.eventi = sorted(tenuti, key=lambda e: (e['t'], e['id']))
        self.stat['eventi_scartati_soglia'] = soglia_scartati
        self.stat['eventi_scartati_dote'] = dote_scartati

    def _nomina(self):
        """Give a name to events without text, using the OCR text found under their pointer."""
        for e in self.eventi:
            if not e['testo']:
                testo, posizione = self.scene.testo_a(max(0.0, e['t'] - ANTICIPO_TESTO), e['punto'],
                                                      RAGGIO_TESTO)
                e['testo'] = testo[:MAX_TESTO]
                if posizione:
                    e['dati']['dove'] = posizione

    def _doppioni(self):
        """Merge near-simultaneous events of the same family and compatible text into one.

        The merged event keeps the combined strength 1-(1-a)(1-b) and references both ids.
        """
        risultato = []
        for e in self.eventi:
            unito = None
            for precedente in reversed(risultato):
                if e['t'] - precedente['t'] > FINESTRA_DOPPIONI:
                    break
                stessa_famiglia = famiglia(e['tipo']) == famiglia(precedente['tipo'])
                if stessa_famiglia and (not e['testo'] or not precedente['testo']
                                        or stessi(e['testo'], precedente['testo'])):
                    unito = precedente
                    break
            if unito is None:
                risultato.append(e)
                continue
            unito['forza'] = round(1 - (1 - unito['forza']) * (1 - e['forza']), 4)
            unito['rif'] = sorted(set(unito['rif'] + e['rif'] + [unito['id'], e['id']]))
            unito['testo'] = unito['testo'] or e['testo']
            self.stat['eventi_doppioni'] = self.stat.get('eventi_doppioni', 0) + 1
        self.eventi = risultato

    def _assegna(self):
        """Attach each event to the nearest sentence, or to `sciolti` if none is close enough.

        Also flags the events strong enough to complete a deictic word.
        """
        for e in self.eventi:
            frase = self._frase_vicina(e['t'])
            e['_deittico'] = (e['forza'] >= FORZA_DEITTICO and bool(e['testo']) and e['tipo'] in PRECEDENZA
                              and (e['tipo'] != 'sosta' or e['durata'] >= DURATA_SOSTA_MINIMA))
            if frase is None:
                self.sciolti.append(e)
            else:
                self.per_frase[frase].append(e)

    def _frase_vicina(self, t):
        """Return the index of the sentence nearest to time `t`, or None if it is over 1.5 s away."""
        migliore = None
        for i, frase in enumerate(self.frasi):
            inizio = frase[0][0]
            fine = frase[-1][1]
            distanza = 0.0 if inizio <= t <= fine else min(abs(t - inizio), abs(t - fine))
            if migliore is None or distanza < migliore[0]:
                migliore = (distanza, i)
        return migliore[1] if migliore and migliore[0] <= DISTANZA_FRASE_MAX else None

    def _tempo_parola(self, indice, posizione):
        """Start time of word `posizione` of sentence `indice` (the last word if out of range)."""
        return self.frasi[indice][min(posizione, len(self.frasi[indice]) - 1)][0]

    def _formatta(self, e):
        """Render event `e` with its channel's format string; '' when the format does not fit its data."""
        dati = dict(e['dati'])
        dati.setdefault('ogg', e['testo'])
        dati.setdefault('testo', e['testo'])
        try:
            testo = self.formati.get((e['_canale'], e['tipo']), '').format(**dati)
        except (KeyError, ValueError, IndexError):
            return ''
        if e['testo']:
            testo = testo.replace(e['testo'], '"%s"' % e['testo'], 1)
        if e['dati'].get('marca'):
            testo = str(e['dati']['marca']) + testo
        return mmss(e['t']) + ' ' + testo

    def _ordine_sezioni(self, gruppi):
        """Order section names: the known ones in ORDINE first, then any others alphabetically."""
        return [x for x in ORDINE if x in gruppi] + sorted(x for x in gruppi if x not in ORDINE)


def famiglia(tipo):
    """Map an event type to its family so that synonyms (click, pressione...) can be merged."""
    tipo = tipo.lower()
    if tipo in ('clic', 'click', 'pressione'):
        return 'clic'
    if tipo in ('cambio', 'valore', 'stato'):
        return 'cambio'
    if tipo in ('segno', 'deittico'):
        return 'segno'
    return tipo


def mmss(t):
    """Format `t` seconds as MM:SS."""
    return '%02d:%02d' % (int(t // 60), int(t % 60))


def e_key(sezione, nome):
    """Build the stat key 'eventi_<section>_<name>' for a section counter."""
    return 'eventi_%s_%s' % (sezione.lower(), nome)
