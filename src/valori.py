# -*- coding: utf-8 -*-
"""VALORI and DOVE for DemoBrief: pure core (no video, no models), the executable reference of the order.

VALORI finds "Label 12" fields whose number changes during the video (before>after).
DOVE finds the on-screen texts that the narrator names aloud and says where they are.
Reads: <work>/ocr1.json, ocr2.json, ocr3.json (through atomi()), plus the sentences and the
vocabulary prepared by costruisci.py.
Writes: eventi_valori.json and eventi_dove.json (schema in eventi.py), printed by fondi.Fusione
as the VALORI and DOVE sections.
Cost: O(n) in OCR readings (corr is memoised per word; voice n-grams x a fixed +-3 s window).
"""
import bisect
import collections
import functools
import json
import math
import os
import re

import eventi
from testi import let, dig, pulisci_ocr, lev, ratio

# ---- FROZEN CONSTANTS (not to be tuned after looking at the validation results)
CONF_MIN = 0.5          # minimum OCR confidence of a number cell
CONF_SOLO = 0.93        # a value read only once must have at least this confidence
GAP_ZONA = 12.0         # seconds without readings that close an episode (zone/window sources)
GAP_L3 = 60.0           # same, for full-frame (ocr3) readings, which are sampled more sparsely
RIGA_DY = 0.012         # max vertical distance (frame fraction) between a label and its number
RIGA_DX = 0.06          # max horizontal gap between the end of a label and its number
RIGA_DX_MIN = -0.02     # min horizontal gap (small overlap tolerated)
VOLATILE = 0.5          # share of changes between consecutive readings above which a counter is "volatile"
VOLATILE_MIN = 6        # a series needs at least this many readings to be judged volatile
VAL_PER_MIN = 1.2       # VALORI events allowed per minute of video
DOVE_PER_MIN = 1.0      # DOVE events allowed per minute of video
RAGGIO_S = 3.0          # seconds around a spoken phrase in which an OCR text may match it
MINLET = 6              # minimum letters of a spoken phrase / OCR text for DOVE matching
MAX_PAROLE = 6          # most words of an OCR text that can be named by the voice
MAX_CAR = 48            # longest OCR text (characters) considered by DOVE
CTX_DY = 0.12           # how far above a text (frame fraction) its context lines are searched
LARGHEZZA_OCR = 1280.0  # reference frame size of the OCR coordinates
ALTEZZA_OCR = 720.0
CONF_ETICHETTA = 0.75   # minimum confidence of a label cell paired with a number cell
CONF_DOVE_L3 = 0.75     # minimum confidence of a full-frame text for DOVE
CONF_DOVE = 0.8         # same, for the other sources
CONF_CONTESTO = 0.8     # minimum confidence of a context line above a text
SOMIGLIANZA = 0.8       # ratio from which two texts are the same text
DIST_TABELLA = 0.04     # frame fraction: same label at farther positions = table, not a field
LETTURE_DISTINTE = 1.0  # seconds: closer readings are the same frame seen by ocr1 and ocr2
SEQ_VISIBILI = 4        # a series longer than this is abbreviated to "first>first2>..>last"
FINESTRA_SCHERMO = 4    # seconds: a DOVE text already printed in SCHERMO this close is skipped
FINESTRA_CLUSTER = 90   # seconds: same text mentioned farther apart counts as a different mention


def _json(out, nome):
    """Load the JSON file `nome` from folder `out`; None if it is missing or unreadable."""
    percorso = os.path.join(out, nome)
    if not os.path.exists(percorso):
        return None
    try:
        with open(percorso, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def atomi(out):
    """Return every OCR text of folder `out` as a tuple, sorted by time.

    Tuple: (t, text, conf, cx, cy, x0, y0, x1, y1, src, grp). Coordinates are frame fractions
    (centre, then box); `grp` identifies one reading, so the cells of a reading can be paired.
    """
    risultato = []
    ocr1 = _json(out, 'ocr1.json')
    ocr2 = _json(out, 'ocr2.json')
    ocr3 = _json(out, 'ocr3.json')
    zone = (ocr1 or {}).get('zone', [])
    for nome, ocr in (('z1', ocr1), ('z2', ocr2)):
        for zi, letture in ((ocr or {}).get('stato', {})).items():
            if int(zi) >= len(zone):
                continue
            a, b, c, d = zone[int(zi)]
            for t, celle in letture:
                for cella in celle:
                    testo = pulisci_ocr(str(cella[0]))
                    if testo:
                        risultato.append((float(t), testo, float(cella[1]),
                                          (a + c) / 2 / LARGHEZZA_OCR, (b + d) / 2 / ALTEZZA_OCR,
                                          a / LARGHEZZA_OCR, b / ALTEZZA_OCR, c / LARGHEZZA_OCR,
                                          d / ALTEZZA_OCR, nome, (nome, zi, float(t))))
    for t, x0, y0, valori_ in (ocr2 or {}).get('finestre', []):
        for cella in valori_:
            testo = pulisci_ocr(str(cella[0]))
            if testo and len(cella) >= 4:
                px, py = (x0 + cella[2]) / LARGHEZZA_OCR, (y0 + cella[3]) / ALTEZZA_OCR
                risultato.append((float(t), testo, float(cella[1]), px, py, px, py, px, py, 'fin',
                                  ('fin', float(t))))
    if ocr3:
        w = float(ocr3.get('W') or LARGHEZZA_OCR)
        h = float(ocr3.get('H') or ALTEZZA_OCR)
        for t, x0, y0, valori_ in ocr3.get('letture', []):
            for cella in valori_:
                testo = pulisci_ocr(str(cella[0]))
                if testo and len(cella) >= 6:
                    ax, ay = (x0 + cella[2]) / w, (y0 + cella[3]) / h
                    bx, by = (x0 + cella[4]) / w, (y0 + cella[5]) / h
                    risultato.append((float(t), testo, float(cella[1]), (ax + bx) / 2, (ay + by) / 2,
                                      ax, ay, bx, by, 'l3', ('l3', float(t))))
    risultato.sort(key=lambda atomo: atomo[0])
    return risultato


def correttore(voc):
    """Build corr(word): maps a rare word to the known word (seen >= 3 times in `voc`) it resembles.

    Same rule as costruisci.correggi(): edit distance <= 1 for words under 7 letters, else <= 2.
    The function is memoised because the same words recur in every reading.
    """
    @functools.lru_cache(maxsize=None)
    def corr(parola):
        if len(parola) < 4 or voc[parola] >= 3:
            return parola
        limite = 1 if len(parola) < 7 else 2
        migliore = None
        for nota, conteggio in voc.items():
            if conteggio >= 3 and abs(len(nota) - len(parola)) <= limite:
                distanza = lev(parola, nota)
                if distanza <= limite and (migliore is None
                                           or (distanza, -conteggio) < (migliore[0], -migliore[1])):
                    migliore = (distanza, conteggio, nota)
        return migliore[2] if migliore else parola
    return corr


# ---------------------------------------------------------------- A: VALORI
CAMPO = re.compile(r"^\W*(?P<lab>[A-Za-z][A-Za-z ]{1,28}?[A-Za-z])\s*[:=;\-]?\s*"
                   r"(?P<val>[-+]?\d[\d.,]*(?:/\d[\d.,]*)?|[Oo])\W*$")
SOLO_NUM = re.compile(r"^\W*(?P<val>[-+]?\d[\d.,]*(?:/\d[\d.,]*)?)\W*$")
SOLO_LAB = re.compile(r"^\W*(?P<lab>[A-Za-z][A-Za-z ]{2,28}[A-Za-z])\W*$")
ANNO = re.compile(r"(19|20)\d\d")


def num(v):
    """Normalise an OCR number: 'O' becomes '0', thousands separators go, decimal comma becomes dot.

    Fractions such as '16/16' are returned unchanged.
    """
    v = v.strip('.,')
    if v in ('O', 'o'):
        return '0'
    if '/' in v:
        return v
    if re.fullmatch(r'\d{1,3}([.,]+\d{3})+', v):
        v = re.sub(r'[.,]', '', v)      # thousands separator
    return v.replace(',', '.')


def campi(A, corr):
    """Find label+number fields in the OCR atoms `A`, whole in one cell or split in adjacent cells.

    Returns [(t, key, label, value, conf, cx, cy, src)]; `corr` fixes OCR typos in the label.
    Dates and years are not parameters and are dropped.
    """
    risultato = []
    per_gruppo = collections.defaultdict(list)
    for atomo in A:
        per_gruppo[atomo[10]].append(atomo)

    def emit(atomo, lab, val, conf):
        """Record the field `lab`=`val` read from `atomo`, unless the label is short or the value a year."""
        parole = [corr(p.lower()) for p in lab.split()]
        chiave = ''.join(parole)
        if len(chiave) < 4:
            return
        valore = num(val)
        if ANNO.search(valore):                                       # dates/years: not parameters
            return
        risultato.append((atomo[0], chiave, ' '.join(p.capitalize() for p in parole), valore, conf,
                          atomo[3], atomo[4], atomo[9]))

    for grp, gruppo in per_gruppo.items():
        usati = set()
        if grp[0] in ('l3', 'fin'):
            etichette = [b for b in gruppo if SOLO_LAB.match(b[1]) and b[2] >= CONF_ETICHETTA]
            for numero in gruppo:
                m = SOLO_NUM.match(numero[1])
                if not m or numero[2] < CONF_MIN:
                    continue
                candidate = [e for e in etichette
                             if abs(e[4] - numero[4]) <= RIGA_DY
                             and RIGA_DX_MIN <= numero[5] - e[7] <= RIGA_DX]
                if candidate:
                    etichetta = max(candidate, key=lambda e: e[7])
                    usati.add(id(numero))
                    usati.add(id(etichetta))
                    emit(numero, SOLO_LAB.match(etichetta[1]).group('lab'), m.group('val'),
                         min(numero[2], etichetta[2]))
        else:
            for k in range(len(gruppo) - 1):
                etichetta, numero = gruppo[k], gruppo[k + 1]
                if SOLO_LAB.match(etichetta[1]) and SOLO_NUM.match(numero[1]) and numero[2] >= CONF_MIN:
                    usati.add(id(numero))
                    usati.add(id(etichetta))
                    emit(numero, SOLO_LAB.match(etichetta[1]).group('lab'),
                         SOLO_NUM.match(numero[1]).group('val'), min(numero[2], etichetta[2]))
        for atomo in gruppo:
            if id(atomo) in usati or atomo[2] < CONF_MIN:
                continue
            m = CAMPO.match(atomo[1])
            if m:
                emit(atomo, m.group('lab').strip(), m.group('val'), atomo[2])
    return risultato


def uguali(a, b):
    """True when two readings are the same number misread.

    Covers a lost slash or a slash read as '1' (1/64 ~ 164 ~ 1164) and one reading being the
    digit prefix of the other (43 ~ 43852).
    """
    if a == b:
        return True
    if '/' in a and '/' in b:
        return False
    if '/' in b:
        a, b = b, a
    if '/' in a and (a.replace('/', '') == b or a.replace('/', '1') == b):
        return True
    cifre_a, cifre_b = dig(a), dig(b)
    if len(cifre_a) > len(cifre_b):
        cifre_a, cifre_b = cifre_b, cifre_a
    return len(cifre_a) >= 2 and cifre_a != cifre_b and cifre_b.startswith(cifre_a)


def rappresentanti(vals):
    """Group equivalent readings and map each to its class representative.

    The representative is the most frequent member (on a tie: the one with a slash, then the
    longest); a slash form of the same digits wins over the plain one. Returns {reading: rep}.
    """
    conteggi = collections.Counter(vals)
    classi = []
    for v in sorted(conteggi, key=lambda x: (-conteggi[x], -len(x), x)):
        for classe in classi:
            if uguali(v, classe[0]):
                classe.append(v)
                break
        else:
            classi.append([v])
    risultato = {}
    for classe in classi:
        rep = max(classe, key=lambda x: (conteggi[x], '/' in x, len(x)))
        if '/' not in rep:
            con_barra = [x for x in classe
                         if '/' in x and (x.replace('/', '') == rep or x.replace('/', '1') == rep)]
            if con_barra:
                rep = max(con_barra, key=lambda x: conteggi[x])
        for v in classe:
            risultato[v] = rep
    return risultato


def _e_tabella(righe):
    """True when one second shows the same label with different values at different positions.

    That is a table (several rows of the same label), not a single field that changes.
    """
    per_secondo = collections.defaultdict(list)
    for r in righe:
        per_secondo[int(r[0])].append(r)
    for gruppo in per_secondo.values():
        if len(set(r[1].replace('/', '') for r in gruppo)) >= 2:
            for p in gruppo:
                for q in gruppo:
                    if abs(p[3] - q[3]) > DIST_TABELLA or abs(p[4] - q[4]) > DIST_TABELLA:
                        return True
    return False


def _episodi(righe):
    """Split the time-sorted readings of one label into episodes separated by long silences."""
    episodi = []
    for r in righe:
        if episodi:
            ultima = episodi[-1][-1]
            gap = GAP_L3 if (r[5] == 'l3' and ultima[5] == 'l3') else GAP_ZONA
        if episodi and r[0] - ultima[0] <= gap:
            episodi[-1].append(r)
        else:
            episodi.append([r])
    return episodi


def _sequenza(episodio):
    """Reduce one episode to its list of (t, value) readings, or None if it carries no change.

    Only confirmed values survive (read twice, or once with confidence >= CONF_SOLO); an extra
    digit (201 for 20) is repaired with a confirmed value of the same episode. Fast counters
    (clock, FPS, coordinates) are rejected.
    """
    forma = rappresentanti([r[1] for r in episodio])
    episodio = [(r[0], forma[r[1]]) + tuple(r[2:]) for r in episodio]
    istanti = collections.defaultdict(list)
    confidenza = {}
    for r in episodio:
        # ocr1 and ocr2 see the same frame: only readings >= 1 s apart are distinct
        if not istanti[r[1]] or r[0] - istanti[r[1]][-1] >= LETTURE_DISTINTE:
            istanti[r[1]].append(r[0])
        confidenza[r[1]] = max(confidenza.get(r[1], 0), r[2])
    confermati = set(v for v in istanti if len(istanti[v]) >= 2 or confidenza[v] >= CONF_SOLO)
    letture = []
    for r in episodio:
        v = r[1]
        if v not in confermati:                  # extra digit (201 for 20, 116 for 16)
            riparati = [c for c in confermati if len(v) == len(c) + 1 and (v[:-1] == c or v[1:] == c)]
            if not riparati:
                continue
            v = riparati[0]
        letture.append((r[0], v))
    if not letture:
        return None
    cambi = sum(1 for x, y in zip(letture, letture[1:]) if x[1] != y[1])
    if len(letture) >= VOLATILE_MIN and cambi / (len(letture) - 1) > VOLATILE:
        return None                  # counter changing at every reading (clock, FPS, coordinates)
    return letture


def valori(A, voc, durata):
    """Find the labelled numbers that change value during the video.

    Returns [(t, label, 'a>b>c', distinct_values)] in time order, at most ceil(1.2 per minute).
    A series longer than four values is shown as first>second>..>last.
    """
    corr = correttore(voc)
    per_chiave = collections.defaultdict(list)
    etichette = collections.defaultdict(collections.Counter)
    risultato = []
    for t, chiave, lab, v, conf, cx, cy, src in campi(A, corr):
        per_chiave[chiave].append((t, v, conf, cx, cy, src))
        etichette[chiave][lab] += 1
    for chiave, righe in per_chiave.items():
        righe.sort()
        if _e_tabella(righe):
            continue                 # same label at two places, different values at once = a table
        for episodio in _episodi(righe):
            letture = _sequenza(episodio)
            if letture is None:
                continue
            serie = []
            for t, v in letture:
                if not serie or serie[-1] != v:
                    serie.append(v)
            if len(serie) >= 2:
                distinti = len(serie)
                if distinti > SEQ_VISIBILI:
                    serie = serie[:2] + ['..'] + serie[-1:]
                risultato.append((letture[0][0], etichette[chiave].most_common(1)[0][0], '>'.join(serie),
                                  distinti))
    risultato.sort()
    tetto = max(2, math.ceil(durata / 60.0 * VAL_PER_MIN))
    if len(risultato) > tetto:
        tenuti = sorted(range(len(risultato)),
                        key=lambda i: (-min(risultato[i][3], SEQ_VISIBILI), risultato[i][0]))[:tetto]
        risultato = [risultato[i] for i in sorted(tenuti)]
    return risultato


# ---------------------------------------------------------------- B: DOVE
def zona(cx, cy):
    """Name the area of the frame holding the point (cx, cy), with the same words as SCHERMO."""
    if 0.3 <= cx <= 0.7 and 0.25 <= cy <= 0.75:
        return 'finestra al centro'
    if cx > 0.7:
        return 'pannello a destra'
    if cx < 0.3:
        return 'pannello a sinistra'
    if cy < 0.25:
        return 'pannello in alto'
    return 'pannello in basso'


def pulito(tx, voc):
    """True for a short text whose words of 3+ letters are at least 60% known (seen >= 3 times in `voc`)."""
    parole = [x.lower() for x in re.findall(r'[A-Za-z]{3,}', tx)]
    return bool(parole) and sum(voc[x] >= 3 for x in parole) >= 0.6 * len(parole) and len(tx) <= MAX_CAR


def contesto(G, a, voc, k=2):
    """Return up to `k` clean text lines placed just above atom `a` in its reading `G`, top to bottom.

    They name the panel or group the text belongs to; numbers-only cells and near copies of `a`
    are ignored.
    """
    sopra = []
    for b in G:
        if b is a or b[2] < CONF_CONTESTO or re.fullmatch(r'[\d\W]+', b[1]):
            continue
        if not (b[4] < a[4] - 0.004 and a[4] - b[4] <= CTX_DY):
            continue
        if not (min(b[7], a[7]) - max(b[5], a[5]) > 0 or abs(b[5] - a[5]) <= 0.05):
            continue
        if ratio(b[1], a[1]) < SOMIGLIANZA and pulito(b[1], voc):
            sopra.append(b)
    sopra.sort(key=lambda b: -b[4])
    return [b[1] for b in sopra[:k]][::-1]


def _abbinamenti(E, ts, frasi):
    """Match the spoken phrases (1-3 word n-grams) with the OCR texts of `E` heard within +-3 s.

    Returns [((n_words, letters), t_start, atom)]. A match is the same letters, a close edit
    distance on phrases of 7+ letters, or a long phrase contained in the text.
    """
    abbinati = []
    for frase in frasi:
        parole = [(w[0], w[1], re.sub(r"[^a-z0-9']", '', w[2].lower())) for w in frase]
        for i in range(len(parole)):
            for n in (3, 2, 1):
                if i + n > len(parole):
                    continue
                lettere = re.sub(r'[^a-z]', '', ''.join(p[2] for p in parole[i:i + n]))
                if len(lettere) < MINLET:
                    continue
                t0, t1 = parole[i][0], parole[i + n - 1][1]
                for atomo in E[bisect.bisect_left(ts, t0 - RAGGIO_S):bisect.bisect_right(ts, t1 + RAGGIO_S)]:
                    lettere_ocr = let(atomo[1])
                    tolleranza = 1 if len(lettere) < 10 else 2
                    if (lettere_ocr == lettere
                            or (len(lettere) >= 7 and lev(lettere_ocr, lettere) <= tolleranza)
                            or (n >= 2 and len(lettere) >= 9 and lettere in lettere_ocr)):
                        abbinati.append(((n, len(lettere)), t0, atomo))
    return abbinati


def dove(A, frasi, voc, durata, scr):
    """Find the voice phrases that name a text read by the OCR (within +-3 s) and locate that text.

    Returns [(t_voice, label, zone, [context lines], (n_words, letters))], best ones first by
    specificity and capped per minute. `scr` = [(t, zone, text)] already printed in SCHERMO,
    which are skipped.
    """
    corr = correttore(voc)
    E = []
    for a in A:
        soglia = CONF_DOVE_L3 if a[9] == 'l3' else CONF_DOVE
        if a[2] >= soglia and not re.search(r'\d', a[1]) and 1 <= len(a[1].split()) <= MAX_PAROLE \
                and len(a[1]) <= MAX_CAR and len(let(a[1])) >= MINLET:
            E.append(a)
    ts = [a[0] for a in E]
    per_gruppo = collections.defaultdict(list)
    for a in A:
        per_gruppo[a[10]].append(a)
    M = _abbinamenti(E, ts, frasi)
    gruppi = []
    for punteggio, t0, a in sorted(M, key=lambda m: (m[1], -m[0][0], -m[0][1])):
        for gruppo in gruppi:
            if ratio(gruppo['a'][1], a[1]) >= SOMIGLIANZA and abs(t0 - gruppo['t0']) <= FINESTRA_CLUSTER:
                gruppo['atomi'].append(a)
                gruppo['sc'] = max(gruppo['sc'], punteggio)
                break
        else:
            gruppi.append(dict(a=a, t0=t0, sc=punteggio, atomi=[a]))
    trovati = []
    for gruppo in gruppi:
        migliore = max(gruppo['atomi'],
                       key=lambda a: (len(contesto(per_gruppo[a[10]], a, voc)) > 0,
                                      {'l3': 3, 'fin': 2}.get(a[9], 1), a[2], len(a[1])))
        gia_in_schermo = False
        for t_, z_, tx in scr:
            if abs(t_ - migliore[0]) <= FINESTRA_SCHERMO and let(migliore[1]) in let(tx):
                gia_in_schermo = True
                break
        if gia_in_schermo:
            continue
        parole = []
        for p in migliore[1].split():
            if p.isalpha() and (p.islower() or p.istitle()):
                parole.append(corr(p.lower()).capitalize())
            else:
                parole.append(p)
        trovati.append((gruppo['t0'], ' '.join(parole), zona(migliore[3], migliore[4]),
                        contesto(per_gruppo[migliore[10]], migliore, voc), gruppo['sc']))
    tetto = max(2, math.ceil(durata / 60.0 * DOVE_PER_MIN))
    # more letters named by the voice = more specific text
    migliori = sorted(trovati, key=lambda r: (-r[4][1], -r[4][0], -len(r[3])))[:tetto]
    return sorted(migliori)


# ---------------------------------------------------------------- event output
def _doc(canale, lettera, sezione, legenda, tipo, formato, eventi_):
    """Wrap a list of events into a document of the eventi.py schema, with a single event type."""
    return {'schema': 1, 'canale': canale, 'lettera': lettera, 'versione': '0.1', 'sezione': sezione,
            'legenda': legenda, 'formati': {tipo: formato}, 'stat': {'proposti': len(eventi_)},
            'eventi': eventi_}


def scrivi_eventi(out, frasi, voc, durata, scr, spente=()):
    """Write eventi_valori.json and eventi_dove.json into `out`; return (n_valori, n_dove).

    A section that is switched off (`spente`) or empty has its file deleted, so the story stays
    identical to a run without the section.
    """
    atomi_ocr = atomi(out)
    pv = os.path.join(out, 'eventi_valori.json')
    pd = os.path.join(out, 'eventi_dove.json')
    vv = [] if 'VALORI' in spente else valori(atomi_ocr, voc, durata)
    dd = [] if 'DOVE' in spente else dove(atomi_ocr, frasi, voc, durata, scr)
    for percorso, elenco in ((pv, vv), (pd, dd)):
        if not elenco and os.path.exists(percorso):
            os.remove(percorso)
    if vv:
        ev = []
        for i, (t, lab, seq, n) in enumerate(vv):
            ev.append({'id': 'v%d' % i, 't': t, 'durata': 0.0, 'canale': 'valori', 'tipo': 'variazione',
                       'zona': None, 'punto': None, 'testo': lab, 'forza': 0.8, 'stampa': 'sezione',
                       'rif': [], 'dati': {'seq': seq}})
        eventi.scrivi(pv, _doc('valori', 'V', 'VALORI',
                               'VALORI = campi etichetta+numero che cambiano (prima>dopo)',
                               'variazione', '{ogg} {seq}', ev))
    if dd:
        ev = []
        for i, (t, lab, z, ctx, sc) in enumerate(dd):
            dove_testo = z + ((' / ' + ' > '.join(ctx)) if ctx else '')
            ev.append({'id': 'd%d' % i, 't': t, 'durata': 0.0, 'canale': 'dove', 'tipo': 'ubicazione',
                       'zona': None, 'punto': None, 'testo': lab, 'forza': 0.8, 'stampa': 'sezione',
                       'rif': [], 'dati': {'dove': dove_testo}})
        eventi.scrivi(pd, _doc('dove', 'P', 'DOVE',
                               'DOVE = dove sta il comando nominato dalla voce (zona / testi sopra)',
                               'ubicazione', '{ogg} {dove}', ev))
    return len(vv), len(dd)
