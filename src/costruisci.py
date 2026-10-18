# -*- coding: utf-8 -*-
"""Build the story of a video (racconto.md) from the files of its work folder; fully automatic.

Usage: costruisci.py <outdir> [title]
Reads: parole.json (word-level speech), ocr1.json / ocr2.json (OCR of dynamic zones at 1 fps,
of action windows at 2 fps and of cursor windows), ocr3.json (full-screen readings),
mov.json and eventi.txt (movement), occhio.json (closed questions answered on two frames),
meta.json, plus the eventi_*.json files of the channels (merged through fondi.py).
Writes: racconto.md and costruisci_stat.json in <outdir>; prints the statistics.
One line per sentence: [mm:ss] VOCE "..." | TESTO ... | MOTO ... | VISTA [vista] ...
(empty fields are left out). No text is written by hand.
"""
import collections
import difflib
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fondi
import tabelle
import tempi
import valori
from comune import mmss
from testi import dig, let, lev, pulisci_ocr, ratio, stessi, utile

# ---- thresholds (seconds, confidences and distances come from tuning on the reference videos)
PAUSA_FRASE_S = 0.5          # silence between words that closes a sentence
ANCORA_MAX_S = 1.5           # farthest an event may be from a sentence to be attached to it
CONF_CELLA = 0.5             # OCR cells at or below this confidence are not part of a reading
STESSA_RIGA = 0.75           # stessi() threshold: two readings are the same line
DURATA_MIN_S = 1.0           # a text that appears and vanishes sooner is noise/hover
LETTURE_MIN = 2              # ...unless it was read at least this many times
CONF_VOCAB = 0.8             # readings above this confidence feed the vocabulary
INIZIO_BASE_S = 1.5          # texts present in the first seconds are the baseline (not narrated)
TRACCIA_GAP_S = 1.0          # window texts closer in time than this belong to one track
TRACCIA_RAGGIO_PX = 60       # ...and closer in position than this (pixels)
CELLA_PX = 12                # position grid used to tell where a window text sits
FASE_ANTICIPO_S = 1.0        # a phase starts this much earlier...
FASE_CODA_S = 1.5            # ...and ends this much later when matching texts to phases
FASI_FISSE = 3               # same text at the same spot in this many phases = fixed interface
CONF_CORRETTA = 0.85         # candidate text accepted without checking the vocabulary
CONF_MEDIA = 0.72            # candidate text accepted if at least half its words are known
NOTO_MEDIA = 0.5
NOTO_ALTA = 0.75             # ...or if three quarters of its words are known
SOMIGLIANZA_TESTO = 0.85     # ratio() from which two texts are the same one
FINESTRA_DEDUP_S = 120       # how far back a repeated text is merged into the previous event
FINESTRA_VALORI_S = 15       # how far back a changing-number text extends the previous event
SEQ_VISIBILI = 4             # a numeric series longer than this is abbreviated
OCCHIO_FINESTRA_S = 4        # the OCR must confirm an eye label within this many seconds
OCCHIO_SOMIGLIANZA = 0.8     # the two eye answers must agree at least this much
OCCHIO_ETICHETTA_OCR = 0.7   # stessi() threshold between eye label and OCR text
CONF_SCHERMO = 0.75          # minimum confidence of a full-screen OCR cell
RIGA_SCHERMO_PX = 8          # cells whose top differs by less than this are on the same row
CELLA_SPAZIO_PX = 25         # gap under which neighbouring texts form one cell
CELLA_MIN_CARATTERI = 2      # a cell needs this many letters+digits
SIMILE_RAPPORTO = 0.85       # similarity of two cells for the fixed-interface test
SIMILE_LUNGHEZZA_MIN = 4     # shorter cells are compared for equality only
FISSE_LETTURE_MIN = 3        # a cell seen in at least this many readings...
FISSE_QUOTA = 0.3            # ...or in this share of them is fixed interface
RECENTI_LETTURE = 2          # a cell seen in the last readings is not new
DETTO_S = 45                 # a field already narrated this recently (same digits) is not repeated
CELLA_TESTO_MAX = 80
RIGA_TESTO_MAX = 140
SCHERMO_RIGHE_MAX = 20
SCHERMO_TESTO_MAX = 700
AREA_PAGINA = 0.5            # share of the screen from which a box is "the whole page"
MOTO_ANCORA_S = 0.5          # movement is attached to the sentence nearest to its start + this
MOTO_INTERVALLO_S = 3        # a movement at least this long prints "start-end"
VOCE_BREVE_PAROLE = 5        # one-line voice-only sentences up to this length are dropped
TOKEN_PASSI = 2              # refinements of the token count (the header contains the count)
TOKEN_CARATTERI = 4          # characters per token when no tokenizer is available

URL = re.compile(r'https?://|www\.|\.com/|/web#', re.I)
SKIP = re.compile(r"^(thanks for watching|if you've got any questions|grazie per la visione|"
                  r"grazie per aver guardato)", re.I)
SCORC = re.compile(r'\b(Shift|Ctrl|Control|Alt|Cmd)\s*[-+]\s*([A-Za-z0-9])\b', re.I)

OUT = sys.argv[1]
tempi.configura('costruisci', OUT)
STAT = collections.OrderedDict()


def leggi_json(nome):
    """Load the JSON file `nome` from the work folder."""
    with open(os.path.join(OUT, nome), encoding='utf-8') as f:
        return json.load(f)


# ---------------- voice: words grouped into sentences
parole = leggi_json('parole.json')
frasi = []
corrente = []
for parola in parole:
    if corrente and (parola[0] - corrente[-1][1] > PAUSA_FRASE_S or corrente[-1][2][-1] in '.?!'):
        frasi.append(corrente)
        corrente = []
    corrente.append(parola)
if corrente:
    frasi.append(corrente)


def ancora(t):
    """Find the spoken word nearest to time `t`.

    Returns (distance in seconds, sentence index, word index), or None if even the nearest word
    is more than 1.5 s away.
    """
    migliore = None
    for i, frase in enumerate(frasi):
        for j, parola in enumerate(frase):
            distanza = 0 if parola[0] <= t <= parola[1] else min(abs(t - parola[0]), abs(t - parola[1]))
            if migliore is None or distanza < migliore[0]:
                migliore = (distanza, i, j)
    return migliore if migliore and migliore[0] <= ANCORA_MAX_S else None


# ---------------- OCR: series per zone -> stable segments
ocr1 = leggi_json('ocr1.json')
if os.path.exists(os.path.join(OUT, 'ocr2.json')):
    ocr2 = leggi_json('ocr2.json')
else:
    ocr2 = dict(stato={}, finestre=[])
movimento = leggi_json('mov.json')
DUR = ocr1['dur']
osservazioni = collections.defaultdict(list)
for zona_id, letture in ocr1['stato'].items():
    for secondo, celle in letture:
        osservazioni[zona_id].append((float(secondo), celle))
for zona_id, letture in ocr2['stato'].items():
    for secondo, celle in letture:
        osservazioni[zona_id].append((float(secondo), celle))


def testo_di(celle):
    """Join the OCR cells of one reading that are above the confidence floor into a cleaned text.

    Returns (text, mean confidence of the cells used; 0 when none was used).
    """
    valide = [x for x in celle if x[1] > CONF_CELLA]
    testo = pulisci_ocr(' '.join(x[0] for x in valide))
    return testo, (sum(x[1] for x in valide) / len(valide) if valide else 0)


def segmenti(seq, fine):
    """Group a zone's time-ordered readings into stable segments.

    `seq` is [(t, text, conf)]; consecutive readings with the same digits and similar letters
    (or both empty) form one segment, which lasts until the next one starts (or `fine`).
    Returns [(start, duration, most frequent text, readings, mean confidence of that text)].
    """
    gruppi = []
    for t, testo, conf in seq:
        if gruppi and (stessi(gruppi[-1]['v'][0], testo, STESSA_RIGA)
                       or (not testo and not gruppi[-1]['v'][0])):
            gruppi[-1]['v'].append(testo)
            gruppi[-1]['t'].append(t)
            gruppi[-1]['c'].append(conf)
        else:
            gruppi.append(dict(v=[testo], t=[t], c=[conf]))
    risultato = []
    for k, gruppo in enumerate(gruppi):
        inizio = gruppo['t'][0]
        fine_gruppo = gruppi[k + 1]['t'][0] if k + 1 < len(gruppi) else fine
        testo = collections.Counter(gruppo['v']).most_common(1)[0][0]
        confidenze = [c for v, c in zip(gruppo['v'], gruppo['c']) if v == testo]
        risultato.append((inizio, fine_gruppo - inizio, testo, len(gruppo['t']),
                          sum(confidenze) / len(confidenze)))
    return risultato


candidati = []      # (t, text, source, confidence)
base = []           # texts present at the start of the video (not narrated)
vocab = collections.Counter()
for parola in parole:
    lettere = re.sub(r'[^a-z]', '', parola[2].lower())
    if len(lettere) >= 3:
        vocab[lettere] += 1
n_segmenti_stabili = 0
for zona_id, letture in osservazioni.items():
    letture.sort()
    seq = [(t,) + testo_di(celle) for t, celle in letture]
    for inizio, durata, testo, n_letture, conf in segmenti(seq, DUR):
        if not utile(testo):
            continue
        if durata < DURATA_MIN_S and n_letture < LETTURE_MIN:     # appears and vanishes at once: noise/hover
            continue
        n_segmenti_stabili += 1
        if conf >= CONF_VOCAB:
            for parola_ocr in re.findall(r'[A-Za-z]{3,}', testo):
                vocab[parola_ocr.lower()] += n_letture
        if inizio < INIZIO_BASE_S:
            base.append(testo)
        else:
            candidati.append((inizio, testo, 'z', conf))
# windows on the cursor: tracks per text row
finestre = ocr2['finestre']
tracce = []   # dict(txt, t[], pos, c[], raw)
for t, x0, y0, letture in finestre:
    for testo, conf, px, py in letture:
        if conf < CONF_CELLA:
            continue
        testo = pulisci_ocr(testo)
        if not utile(testo):
            continue
        raw = (x0 + px, y0 + py)
        pos = (round(raw[0] / CELLA_PX), round(raw[1] / CELLA_PX))
        for traccia in tracce:
            if (t - traccia['t'][-1] <= TRACCIA_GAP_S
                    and abs(traccia['raw'][0] - raw[0]) <= TRACCIA_RAGGIO_PX
                    and abs(traccia['raw'][1] - raw[1]) <= TRACCIA_RAGGIO_PX
                    and stessi(traccia['txt'], testo, STESSA_RIGA)):
                traccia['t'].append(t)
                traccia['c'].append(conf)
                traccia['raw'] = raw
                break
        else:
            tracce.append(dict(txt=testo, t=[t], pos=pos, c=[conf], raw=raw))
fasi = [(f['t0'], f['t1']) for f in movimento['fasi'] if f['mods'] + f['ist'] > 0]


def fase_di(t):
    """Index of the movement phase containing time `t` (with a little margin), or None."""
    for k, (inizio, fine) in enumerate(fasi):
        if inizio - FASE_ANTICIPO_S <= t <= fine + FASE_CODA_S:
            return k
    return None


fasi_per_testo = collections.defaultdict(set)
for traccia in tracce:
    k = fase_di(traccia['t'][0])
    fasi_per_testo[(traccia['pos'], let(traccia['txt']))].add(k)
n_finestre = 0
for traccia in tracce:
    durata = traccia['t'][-1] - traccia['t'][0] + 0.5
    if durata < DURATA_MIN_S:                                # appears and vanishes in < 1 s: menu hover
        continue
    if len(fasi_per_testo[(traccia['pos'], let(traccia['txt']))]) >= FASI_FISSE:
        continue                  # always at the same spot in different phases = fixed interface
    conf_media = sum(traccia['c']) / len(traccia['c'])
    if conf_media >= CONF_VOCAB:
        for parola_ocr in re.findall(r'[A-Za-z]{3,}', traccia['txt']):
            vocab[parola_ocr.lower()] += 1
    candidati.append((traccia['t'][0], traccia['txt'], 'w', sum(traccia['c']) / len(traccia['c'])))
    n_finestre += 1
STAT['segmenti_zona_stabili'] = n_segmenti_stabili
STAT['tracce_finestra_stabili'] = n_finestre


# ---------------- typo correction: vocabulary = voice + stable OCR texts, distance <= 2
def correggi(testo):
    """Replace rare words of `testo` with the known word (seen >= 3 times) they most resemble.

    A word under 7 letters may differ by one edit, longer words by two; the first letter must
    match. Counts the corrections in STAT['refusi_corretti'].
    """
    def sostituisci(m):
        """Return the replacement for the regex match `m` (the word itself if nothing fits)."""
        parola = m.group(0)
        minuscola = parola.lower()
        if len(minuscola) < 4 or vocab[minuscola] >= 3:
            return parola
        limite = 1 if len(minuscola) < 7 else 2
        migliore = None
        for nota, conteggio in vocab.items():
            if conteggio >= 3 and nota[0] == minuscola[0] and abs(len(nota) - len(minuscola)) <= limite:
                distanza = lev(minuscola, nota)
                if distanza <= limite and (migliore is None
                                           or (distanza, -conteggio) < (migliore[0], -migliore[1])):
                    migliore = (distanza, conteggio, nota)
        if not migliore:
            return parola
        STAT['refusi_corretti'] = STAT.get('refusi_corretti', 0) + 1
        corretta = migliore[2]
        return corretta.capitalize() if parola[0].isupper() else corretta
    return re.sub(r'[A-Za-z]{4,}', sostituisci, testo)


# ---------------- de-duplication, baseline, numeric chains
candidati.sort()
base = list(base)


def num_tokens(testo):
    """Return the numbers (digits with . , / separators) found in `testo`."""
    return re.findall(r'\d[\d.,/]*', testo)


eventi_testo = []


def noto(testo, quota):
    """True when at least the share `quota` of the words of 3+ letters of `testo` is known (>= 3 times)."""
    parole_testo = [x.lower() for x in re.findall(r'[A-Za-z]{3,}', testo)]
    return bool(parole_testo) and sum(vocab[x] >= 3 for x in parole_testo) >= quota * len(parole_testo)


for t, testo, sorgente, conf in candidati:
    testo = correggi(testo)
    if not (conf >= CONF_CORRETTA or (conf >= CONF_MEDIA and noto(testo, NOTO_MEDIA))
            or noto(testo, NOTO_ALTA)):
        STAT['scartati_bassa_fiducia'] = STAT.get('scartati_bassa_fiducia', 0) + 1
        continue
    if any(stessi(testo, b, SOMIGLIANZA_TESTO) for b in base):
        STAT['scartati_baseline'] = STAT.get('scartati_baseline', 0) + 1
        continue
    fatto = False
    for evento in reversed(eventi_testo):
        if t - evento['t1'] > FINESTRA_DEDUP_S:
            break
        if ratio(testo, evento['txt']) >= SOMIGLIANZA_TESTO:
            if dig(testo) == dig(evento['txt']) or testo in evento['vals']:
                evento['t1'] = t
                fatto = True
                STAT['doppioni'] = STAT.get('doppioni', 0) + 1
                break
            if (t - evento['t1'] <= FINESTRA_VALORI_S
                    and len(num_tokens(testo)) == len(num_tokens(evento['txt']))
                    and num_tokens(testo)):
                evento['vals'].append(testo)
                evento['t1'] = t
                fatto = True
                break
    if not fatto:
        eventi_testo.append(dict(t=t, t1=t, txt=testo, vals=[testo], src=sorgente))


def testo_finale(evento):
    """Return the text of an event, with each number replaced by its series of values 'a>b>c'.

    A series longer than four values is shortened to 'first>second>..>last'.
    """
    if len(evento['vals']) == 1:
        return evento['txt']
    numeri = [num_tokens(v) for v in evento['vals']]
    serie = []
    for posizione in range(len(numeri[0])):
        valori_serie = []
        for lettura in numeri:
            if not valori_serie or valori_serie[-1] != lettura[posizione]:
                valori_serie.append(lettura[posizione])
        if len(valori_serie) > SEQ_VISIBILI:
            valori_serie = valori_serie[:2] + ['..'] + valori_serie[-1:]
        serie.append('>'.join(valori_serie))
    successiva = iter(serie)
    return re.sub(r'\d[\d.,/]*', lambda m: next(successiva), evento['txt'], count=len(serie))


for evento in eventi_testo:
    evento['f'] = testo_finale(evento)
STAT['eventi_testo'] = len(eventi_testo)


# ---------------- movement (from eventi.txt, as in v3)
def secondi(orario):
    """Convert 'mm:ss.s' into seconds."""
    return int(orario.split(':')[0]) * 60 + float(orario.split(':')[1])


moto = []
with open(os.path.join(OUT, 'eventi.txt'), encoding='utf-8') as file_eventi:
    for riga in file_eventi:
        m = re.match(r'\[([\d:.]+)(?:-([\d:.]+))?\]\s*(.*)', riga.strip())
        if not m:
            continue
        inizio = secondi(m.group(1))
        fine = secondi(m.group(2)) if m.group(2) else inizio
        descrizione = m.group(3)
        dettagli = []
        if 'modifica mesh' in descrizione:
            gesti = re.search(r'(\d+) gest', descrizione)
            dettagli.append('mesh %sg' % (gesti.group(1) if gesti else '1'))
        orbita = re.search(r'orbita/zoom (\d+)x \((\d+)s\)', descrizione)
        if orbita and orbita.group(2) != '0':
            dettagli.append('orbita %ss' % orbita.group(2))
        clic = max(len(re.findall(r'@([\d:.,]+)', descrizione)[0].split(',')) if '@' in descrizione else 0,
                   len(re.findall(r'clic \d', descrizione)))
        if clic:
            dettagli.append('clic%d' % clic)
        if descrizione.startswith('pannello dx') or 'pannello dx:' in descrizione[:12]:
            dettagli.append('pannello dx')
        if 'taglio' in descrizione:
            dettagli.append('taglio vista')
        if not dettagli:
            continue
        moto.append((inizio, fine, dettagli))

# ---------------- eye: consistency on 2 neighbouring images
vista = []
STAT['occhio_chiamate'] = 0
STAT['occhio_scartate_incoerenza'] = 0
STAT['occhio_accettate'] = 0
STAT['occhio_etichette_non_confermate'] = 0
if os.path.exists(os.path.join(OUT, 'occhio.json')):
    occhio = leggi_json('occhio.json')
    for risposta in occhio['res']:
        if 'r' not in risposta:
            continue
        STAT['occhio_chiamate'] += 2
        prima, seconda = risposta['r'][0]['p'], risposta['r'][1]['p']
        if (not prima or not seconda or prima['NUOVA_GEOMETRIA'] != seconda['NUOVA_GEOMETRIA']
                or prima['FORMA'] != seconda['FORMA']):
            STAT['occhio_scartate_incoerenza'] += 2
            continue
        if prima['NUOVA_GEOMETRIA'] != 'si' or prima['FORMA'] == 'nessuna':
            continue                                              # nothing to say
        STAT['occhio_accettate'] += 2
        dove = prima['DOVE'] if prima['DOVE'] == seconda['DOVE'] else ''
        accordo = ratio(prima['ETICHETTA_VICINO_CURSORE'], seconda['ETICHETTA_VICINO_CURSORE'])
        etichetta = prima['ETICHETTA_VICINO_CURSORE'] if accordo >= OCCHIO_SOMIGLIANZA else ''
        if etichetta:
            # the label counts only if the OCR confirms it near that instant; never command names from the eye
            confermate = [x for x in eventi_testo
                          if abs(x['t'] - risposta['t1']) <= OCCHIO_FINESTRA_S
                          and (stessi(x['f'], etichetta, OCCHIO_ETICHETTA_OCR)
                               or let(etichetta) in let(x['f']))]
            if not confermate:
                STAT['occhio_etichette_non_confermate'] += 1
                etichetta = ''
        vista.append((risposta['t1'], prima['FORMA'], dove, etichetta))


def posizione_schermo(larghezza, altezza, cx, cy):
    """Name the screen area of a box: its size and centre (cx, cy) are fractions of the screen."""
    if larghezza * altezza >= AREA_PAGINA:
        return 'pagina'
    if 0.3 <= cx <= 0.7 and 0.25 <= cy <= 0.75:
        return 'finestra al centro'
    if cx > 0.7:
        return 'pannello a destra'
    if cx < 0.3:
        return 'pannello a sinistra'
    if cy < 0.25:
        return 'pannello in alto'
    return 'pannello in basso'


# ---------------- new screens (ocr3): cells not seen recently, per box, with the position on screen
TAB = tabelle.accese()
MEM = tabelle.Memoria()
blocchi_tabella = []
FRASI = 'F' in TAB
schermate = []
STAT['schermate_lette'] = 0
STAT['schermate_righe_nuove'] = 0
if os.path.exists(os.path.join(OUT, 'ocr3.json')):
    ocr3 = leggi_json('ocr3.json')
    SW, SH = ocr3['W'], ocr3['H']
    visto = set(let(b) + dig(b) for b in base)
    for zona_id, letture in osservazioni.items():       # the dynamic zones are already narrated by TESTO
        for t, celle in letture:
            visto.add(let(testo_di(celle)[0]) + dig(testo_di(celle)[0]))

    def celle_di(voci_ocr):
        """Turn one full-screen reading into rows of cells (texts that are close are joined).

        Boxes with the same top (+-8 px) form a row; texts less than 25 px apart form one cell;
        cells with fewer than two letters+digits are dropped. Returns a list of rows of strings.
        """
        ordinate = []
        for testo, conf, x0b, y0b, x1b, y1b in voci_ocr:
            if conf >= CONF_SCHERMO:
                ordinate.append((y0b, x0b, x1b, pulisci_ocr(testo)))
        ordinate.sort(key=lambda r: (r[0], r[1]))
        righe_ocr = []
        for py, px, qx, testo in ordinate:                        # same row = same y (+-8 px)
            if righe_ocr and abs(righe_ocr[-1][0] - py) <= RIGA_SCHERMO_PX:
                righe_ocr[-1][1].append((px, qx, testo))
            else:
                righe_ocr.append([py, [(px, qx, testo)]])
        risultato = []
        for py, elementi in righe_ocr:
            elementi.sort()
            celle = []
            for px, qx, testo in elementi:      # cell = nearby texts (gap < 25 px); columns stay apart
                if celle and px - celle[-1][1] < CELLA_SPAZIO_PX:
                    celle[-1] = [celle[-1][0] + ' ' + testo, qx]
                else:
                    celle.append([testo, qx])
            risultato.append([c.strip() for c, _ in celle
                              if len(let(c)) + len(dig(c)) >= CELLA_MIN_CARATTERI])
        return risultato

    tutte = [celle_di(voci) for t, x0, y0, voci in ocr3['letture']]

    # fixed interface = cells present in many readings (menus, headers): never narrated
    # tolerant comparison: same digits and similar letters, or one contained in the other
    # ('Size 10.5' ~ 'Draw Size 10.5')
    def simili(lettere, cifre, lettere2, cifre2):
        """True when two cells (letters, digits) are the same text: equal, contained, or very alike."""
        if cifre != cifre2:
            return False
        if lettere == lettere2:
            return True
        if min(len(lettere), len(lettere2)) < SIMILE_LUNGHEZZA_MIN:
            return False
        contenuta = lettere in lettere2 or lettere2 in lettere
        if contenuta and (not FRASI or tabelle.contenimento_ok(lettere, lettere2)):
            return True
        confronto = difflib.SequenceMatcher(None, lettere, lettere2)
        return confronto.quick_ratio() >= SIMILE_RAPPORTO and confronto.ratio() >= SIMILE_RAPPORTO

    def in_insieme(lettere, cifre, insieme):
        """True when the cell (letters, digits) is in `insieme` or similar to one of its cells."""
        if (lettere, cifre) in insieme:
            return True
        return any(simili(lettere, cifre, lettere2, cifre2) for lettere2, cifre2 in insieme)

    insiemi = []
    for lettura_celle in tutte:
        insieme = set()
        for riga_celle in lettura_celle:
            for cella in riga_celle:
                insieme.add((let(cella), dig(cella)))
        insiemi.append(insieme)
    soglia = max(FISSE_LETTURE_MIN, FISSE_QUOTA * len(tutte))
    fisse = set()
    for insieme in insiemi:
        for lettere, cifre in insieme:
            if lettere + cifre in fisse:
                continue
            presenze = sum(1 for altro in insiemi if in_insieme(lettere, cifre, altro))
            if presenze >= soglia:
                fisse.add(lettere + cifre)
    # novelty against the last readings only: a table that comes back is narrated again
    recenti = collections.deque(maxlen=RECENTI_LETTURE)
    detto = {}

    def gia_vista(cella):
        """True when the cell must not be narrated: fixed interface, seen at start/recently, or just said."""
        lettere, cifre = let(cella), dig(cella)
        if lettere + cifre in fisse or lettere + cifre in visto:
            return True
        for lettura_celle in recenti:
            for lettere2, cifre2 in lettura_celle:
                if simili(lettere, cifre, lettere2, cifre2):
                    return True
        # already narrated a moment ago (same digits, last DETTO_S seconds): an unchanged field
        # is not repeated; beyond the window it is repeated, because a dialog that mentions it
        # changes its meaning
        for (lettere2, cifre2), t2 in detto.items():
            if cifre2 == cifre and t_ora - t2 <= DETTO_S:
                if (lettere, cifre) == (lettere2, cifre2) or simili(lettere, cifre, lettere2, cifre2):
                    return True
        return False

    def dove_riquadro(riquadro):
        """Name the screen area of a box (xa, ya, xb, yb) given in pixels."""
        xa, ya, xb, yb = riquadro
        larghezza = (xb - xa) / SW
        altezza = (yb - ya) / SH
        cx = (xb + xa) / 2 / SW
        cy = (yb + ya) / 2 / SH
        return posizione_schermo(larghezza, altezza, cx, cy)

    for (t, x0, y0, voci), celle_lettura in zip(ocr3['letture'], tutte):
        STAT['schermate_lette'] += 1
        t_ora = float(t)
        if 'S' in TAB:
            box_assoluti = [(testo, conf, x0 + xa, y0 + ya, x0 + xb, y0 + yb)
                            for testo, conf, xa, ya, xb, yb in voci]
            tabelle_trovate = tabelle.trova(box_assoluti, SW)
            parziali = False
            if not tabelle_trovate and MEM:
                tabelle_trovate = tabelle.trova(box_assoluti, SW, 2)
                parziali = True
            usati = set()
            for tabella in tabelle_trovate:
                for indice in tabella['usati']:
                    usati.add(indice)
            celle_lettura = celle_di([q for i, q in enumerate(voci) if i not in usati])
            for tabella in tabelle_trovate:
                if parziali:
                    esito = MEM.parziale(t_ora, tabella)
                else:
                    esito = MEM.confronta(t_ora, tabella)
                if esito is not None:
                    dove_tabella = dove_riquadro(tabella['riquadro'])
                    blocco = tabelle.md(t_ora, dove_tabella, esito, tabella['righe'], tabella['intest'])
                    blocchi_tabella.append((t_ora, dove_tabella, blocco))
        nuove = []
        questa = []
        for riga_celle in celle_lettura:
            nuove_riga = []
            for cella in riga_celle:
                if not URL.search(cella) and not gia_vista(cella):
                    nuove_riga.append(cella[:CELLA_TESTO_MAX])
                    detto[(let(cella), dig(cella))] = t_ora
                questa.append((let(cella), dig(cella)))
            if nuove_riga:
                nuove.append(' · '.join(nuove_riga)[:RIGA_TESTO_MAX])
        recenti.append(questa)
        if not nuove:
            continue
        xs = [x0 + q[2] for q in voci] + [x0 + q[4] for q in voci]
        ys = [y0 + q[3] for q in voci] + [y0 + q[5] for q in voci]
        larghezza = (max(xs) - min(xs)) / SW
        altezza = (max(ys) - min(ys)) / SH
        cx = (max(xs) + min(xs)) / 2 / SW
        cy = (max(ys) + min(ys)) / 2 / SH
        dove = posizione_schermo(larghezza, altezza, cx, cy)
        STAT['schermate_righe_nuove'] += len(nuove)
        schermate.append((float(t), dove, ' / '.join(nuove[:SCHERMO_RIGHE_MAX])[:SCHERMO_TESTO_MAX]))
    blocchi_tabella = tabelle.taglia_totale(blocchi_tabella, DUR)
    if 'S' in TAB:
        STAT['tabelle_stampate'] = len(blocchi_tabella)
        STAT['tabelle_righe'] = sum(int(re.search(r', (?:nuova|cambiate) (\d+) righe', blocco[2]).group(1))
                                    for blocco in blocchi_tabella)

valori.scrivi_eventi(OUT, frasi, vocab, DUR, schermate, spente=fondi.sezioni_spente())
FX = fondi.Fusione(OUT, parole, frasi, eventi_testo, schermate, moto, DUR)
eventi_testo = FX.tieni('TESTO', eventi_testo)
schermate = FX.tieni('SCHERMO', schermate)
moto = FX.tieni('MOTO', moto)
vista = FX.tieni('VISTA', vista)

# ---------------- assembly per sentence
righe = {i: dict(ev=[], mov=[], vis=[], scr=[], tab=[]) for i in range(len(frasi))}
isolati = []
for evento in eventi_testo:
    punto = ancora(evento['t'])
    if punto:
        righe[punto[1]]['ev'].append(evento)
    else:
        isolati.append(('ev', evento['t'], evento))
for inizio, fine, dettagli in moto:
    punto = ancora(inizio + MOTO_ANCORA_S)
    if punto:
        righe[punto[1]]['mov'].append((inizio, fine, dettagli))
    else:
        isolati.append(('mov', inizio, (inizio, fine, dettagli)))
for voce_vista in vista:
    punto = ancora(voce_vista[0])
    if punto:
        righe[punto[1]]['vis'].append(voce_vista)
    else:
        isolati.append(('vis', voce_vista[0], voce_vista))
for schermata in schermate:
    punto = ancora(schermata[0])
    if punto:
        righe[punto[1]]['scr'].append(schermata)
    else:
        isolati.append(('scr', schermata[0], schermata))
for blocco in blocchi_tabella:
    punto = ancora(blocco[0])
    if punto:
        righe[punto[1]]['tab'].append(blocco)
    else:
        isolati.append(('tab', blocco[0], blocco))


def fmt_scr(elenco):
    """Format screen entries (t, area, text) as 'mm:ss area: text' joined by '; '."""
    return '; '.join('%s %s: %s' % (mmss(t), area, testo) for t, area, testo in elenco)


def fmt_ev(elenco):
    """Format text events grouped by second: 'mm:ss "text", "text"; ...' (each text cut to 60 characters)."""
    per_secondo = collections.OrderedDict()
    for evento in sorted(elenco, key=lambda e: e['t']):
        per_secondo.setdefault(mmss(evento['t']), []).append('"%s"' % evento['f'][:60])
    return '; '.join('%s %s' % (orario, ', '.join(testi)) for orario, testi in per_secondo.items())


def fmt_mov(elenco):
    """Format movements (start, end, details) as 'mm:ss[-mm:ss] detail detail', joined by '; '."""
    voci = []
    for inizio, fine, dettagli in elenco:
        intervallo = '-' + mmss(fine) if fine - inizio >= MOTO_INTERVALLO_S else ''
        voci.append('%s%s %s' % (mmss(inizio), intervallo, ' '.join(dettagli)))
    return '; '.join(voci)


def fmt_vis(elenco):
    """Format eye observations (t, shape, area, label) as '[vista] mm:ss shape (area) etichetta "label"'."""
    risultato = []
    for t, forma, dove, etichetta in elenco:
        zona = ' (%s)' % dove if dove and dove != 'vicino al cursore' else ''
        nome = ' etichetta "%s"' % etichetta if etichetta else ''
        risultato.append('[vista] %s %s%s%s' % (mmss(t), forma, zona, nome))
    return '; '.join(risultato)


def pulisci(testo):
    """Tidy a spoken sentence: fix split decimals, drop leading and filler words (obviously, really...)."""
    testo = re.sub(r'(\d) \.(\d)', r'\1.\2', testo)
    testo = re.sub(r'^(So|Now|And|But|Which|Obviously),? ', '', testo)
    testo = re.sub(r'\b(obviously|actually|a little bit|a bit|really|quite|very|you can see (that )?|'
                   r'as you can imagine|a touch|according to the camera)\b ?', '', testo)
    return testo.strip()


linee = []
tutti_eventi = ([(frasi[i][0][0], i, None) for i in range(len(frasi))]
                + [(t, None, (tipo, t, contenuto)) for tipo, t, contenuto in isolati]
                + FX.isolati())
for t, i, isolato in sorted(tutti_eventi, key=lambda x: x[0]):
    if i is not None:
        testo = ' '.join(x[2] for x in frasi[i])
        riga_frase = righe[i]
        testi_ev = fmt_ev(riga_frase['ev'])
        moti = fmt_mov(riga_frase['mov'])
        viste = fmt_vis(riga_frase['vis'])
        schermi = fmt_scr(riga_frase['scr'])
        voce = FX.voce(i, pulisci(testo))
        if SKIP.match(testo) and not (testi_ev or moti or viste or schermi or FX.sezioni(i)):
            continue
        linee.extend(FX.righe_prima(i))
        parti = []
        if FX.attiva('VOCE'):
            if not SKIP.match(testo):
                parti.append('[%s] VOCE "%s"' % (mmss(t), voce))
            else:
                parti.append('[%s]' % mmss(t))
        elif testi_ev or moti or viste or schermi or FX.sezioni(i):
            parti.append('[%s]' % mmss(t))
        if testi_ev:
            parti.append('TESTO ' + testi_ev)
        if schermi:
            parti.append('SCHERMO ' + schermi)
        if moti:
            parti.append('MOTO ' + moti)
        if viste:
            parti.append('VISTA ' + viste)
        parti += FX.sezioni(i)                     # new channels printing as 'sezione' (hook 1.4.5)
        linee.append(' | '.join(parti))
        for blocco_tabella in riga_frase['tab']:
            linee.append(blocco_tabella[2])
    else:
        tipo, t, contenuto = isolato
        if tipo == 'mov':
            linee.append('[%s] MOTO %s' % (mmss(t), fmt_mov([contenuto])))
        elif tipo == 'scr':
            linee.append('[%s] SCHERMO %s' % (mmss(t), fmt_scr([contenuto])))
        elif tipo == 'tab':
            linee.append(contenuto[2])
        elif tipo == 'ev':
            linee.append('[%s] TESTO %s' % (mmss(t), fmt_ev([contenuto])))
        elif tipo == 'fx':
            linee.append(contenuto)      # event of a new channel with no sentence nearby, already formatted
        else:
            linee.append('[%s] VISTA %s' % (mmss(t), fmt_vis([contenuto])))


def solo_voce(linea):
    """True for a plain voice line: no other channel, no deictic hint, no '*' marker."""
    return ' | ' not in linea and '[→' not in linea and '*' not in linea and 'VOCE "' in linea


unite = []
for linea in linee:
    if unite and solo_voce(linea) and solo_voce(unite[-1]):
        unite[-1] = unite[-1][:-1] + ' ' + linea.split('VOCE "', 1)[1]
    else:
        unite.append(linea)
linee = [linea for linea in unite
         if not (solo_voce(linea) and len(linea.split('VOCE "', 1)[1].split()) <= VOCE_BREVE_PAROLE)]
if os.path.exists(os.path.join(OUT, 'meta.json')):
    meta = leggi_json('meta.json')
else:
    meta = {}


def conta_token(testo):
    """Count the tokens of `testo` with tiktoken, or estimate them (4 characters each) without it."""
    try:
        import tiktoken
        return len(tiktoken.get_encoding('cl100k_base').encode(testo))
    except Exception:                       # no network / no cache: estimate 1 token every 4 characters
        return len(testo) // TOKEN_CARATTERI


usate = []
if parole:
    usate.append('voce (%d parole)' % len(parole))
if eventi_testo:
    usate.append('OCR (%d eventi)' % len(eventi_testo))
if moto:
    usate.append('moto (%d)' % len(moto))
if vista:
    usate.append('vista (%d)' % len(vista))
if schermate:
    usate.append('schermate (%d)' % len(schermate))
if blocchi_tabella:
    usate.append('tabelle (%d)' % len(blocchi_tabella))
usate += FX.usate()


def forma_scorciatoia(m):
    """Rewrite a matched shortcut such as 'Shift -A' or 'ctrl - z' as 'Shift+A' / 'Ctrl+Z'."""
    return m.group(1).capitalize().replace('Control', 'Ctrl') + '+' + m.group(2).upper()


# shortcuts in the single form Key+Key ('Shift -A', 'ctrl - z' -> 'Shift+A', 'Ctrl+Z')
linee = [SCORC.sub(forma_scorciatoia, linea) for linea in linee]
corpo = '\n'.join(linee) + '\n'


def intestazione(token):
    """Build the three header lines (title and token count, sources used, legend) of the story."""
    titolo = meta.get('titolo') or (sys.argv[2] if len(sys.argv) > 2 else 'Video')
    riga1 = '# %s - durata %s - token stimati ~%d' % (titolo, mmss(DUR), token)
    file_versione = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'version.txt')
    if os.path.exists(file_versione):
        versione = open(file_versione, encoding='utf-8').read().strip()
    else:
        versione = '?'
    nota = (' ATTENZIONE: ' + meta['vista_nota']) if meta.get('vista_nota') else ''
    riga2 = ('DemoBrief ' + versione + '. ') + 'Fonti usate: %s.%s' % (', '.join(usate) or 'nessuna', nota)
    riga3 = ('Legenda: [mm:ss] istante; VOCE = parlato; TESTO = scritte che cambiano a schermo (OCR); '
             'SCHERMO = righe nuove lette quando lo schermo si ferma su una vista nuova '
             '(dove: pagina, finestra, pannello); MOTO = azioni (mesh Ng = gesti sulla geometria, '
             'orbita Ns = rotazione vista, clicN = clic, taglio vista = cambio scena); '
             "VISTA = geometria nuova vista dall'occhio (forma, zona). "
             'Immagini a richiesta: %s.' % meta.get('indice_fotogrammi', 'fotogrammi/indice.md'))
    if 'S' in TAB:
        riga3 += ' TABELLA = tabella letta a schermo (righe nuove o cambiate, colonne con intestazione).'
    if FX.legenda():
        riga3 += ' ' + FX.legenda() + '.'
    return '\n'.join((riga1, riga2, riga3)) + '\n'


STAT.update(FX.stat)
token_stimati = conta_token(intestazione(0) + corpo)
for _ in range(TOKEN_PASSI):
    token_stimati = conta_token(intestazione(token_stimati) + corpo)
racconto = intestazione(token_stimati) + corpo
with open(os.path.join(OUT, 'racconto.md'), 'w', encoding='utf-8') as file_racconto:
    file_racconto.write(racconto)
STAT['token'] = token_stimati
STAT['token_corpo'] = conta_token(corpo)
# A container without a declared duration gives DUR == 0: no rate can be computed then.
STAT['token_al_secondo'] = round(token_stimati / DUR, 1) if DUR > 0 else 0
STAT['righe'] = len(linee)
with open(os.path.join(OUT, 'costruisci_stat.json'), 'w') as file_stat:
    json.dump(STAT, file_stat)
print(dict(STAT))
