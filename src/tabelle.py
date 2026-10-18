# -*- coding: utf-8 -*-
"""Pure reconstruction of tables from OCR boxes, row memory across frames, and frame picking.

Input: OCR boxes (text, confidence, x0, y0, x1, y1) in pixels, ocr1/ocr3 documents already loaded.
Output: tables as dicts (header, rows, boxes used), Markdown blocks for the story, and the
frame instants worth keeping when a table or list appears. Nothing is read from or written to disk.
"""
import collections
import math
import re
import statistics

import comune

CONF_MIN = 0.75
CONF_ISTANTI = 0.8
RIGA_PX = 6.0
NUMERO_PX = 8.0
VICINO_COLONNA_PX = 12.0
PASSO_PX = 4.0
RIGHE_MIN = 3
COLONNE_MIN = 3
OCCUPAZIONE_MIN = 0.5
PROFILO_MIN = 0.3
CV_MASSIMO = 0.6
DISTANZA_BLOCCO = 2.5
DISTANZA_INTESTAZIONE = 3.0
SOVRAPPOSIZIONE_MIN = 0.5
MEMORIA_PARZIALE_S = 120.0
CELLA_MAX = 40
COLONNE_MAX = 8
RIGHE_MAX = 12
BLOCCO_MAX = 700
CARATTERI_AL_SECONDO = 6
ISTANTI_VICINI_S = 3.0
ISTANTE_FASE_S = 1.5
SOSTITUZIONE_VICINA_S = 2.0
ISTANTI_MAX = 4
# Pixel thresholds are tuned on 1280-pixel-wide frames and scaled by width / LARGHEZZA_RIFERIMENTO.
LARGHEZZA_RIFERIMENTO = 1280.0
SCALA_MINIMA = 0.01
# A text row groups boxes within this fraction of the median box height.
RIGA_ALTEZZA_QUOTA = 0.5
# Column signature: horizontal positions are rounded to multiples of this many pixels.
FIRMA_COLONNA_PX = 20
# A table is "new" when at least this share of its rows has no match in the remembered table.
RIGHE_NUOVE_QUOTA = 0.5
# Two strings are comparable for containment when the shorter is at least this share of the longer.
CONTENIMENTO_MIN = 0.5
# A numeric column needs this many values to be reported as a list; the preview shows the first few.
LISTA_VALORI_MIN = 4
LISTA_ANTEPRIMA = 4
# A "new list" is dropped when at least this share of its values was already seen in the same place.
LISTA_NUOVA_QUOTA = 0.5
NON_ASCII = re.compile('[^\\x00-\\x7f]')
NON_NUMERO = re.compile('[^0-9.,:/+\\-]')
LETTERE_CIFRE = re.compile('[a-z0-9]', re.I)


def _let(s):
    """Lowercase letters a-z of `s` only."""
    return re.sub('[^a-z]', '', str(s).lower())


def _dig(s):
    """Decimal digits of `s` only."""
    return re.sub('\\D', '', str(s))


def _norm(s):
    """Comparison key of a cell: its letters followed by its digits."""
    return _let(s) + _dig(s)


def _scala(w):
    """Scale factor of a frame `w` pixels wide against the 1280-pixel reference (never below 0.01)."""
    return max(float(w) / LARGHEZZA_RIFERIMENTO, SCALA_MINIMA)


def _sovrappone(a, b):
    """True when the intervals `a` and `b` (start, end) overlap by at least half of the shorter one."""
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    stretto = min(a[1] - a[0], b[1] - b[0])
    return stretto > 0 and inter / stretto >= SOVRAPPOSIZIONE_MIN


def accese():
    """Return which table features are switched on, as a subset of {'S', 'F', 'I'}.

    The letters come from the environment or from the 'tabelle' key of the config.
    """
    valore = comune.ambiente('TABELLE')
    if valore is None:
        valore = comune.config().get('tabelle', '')
    return set(str(valore).upper()) & set('SFI')


def numerica(s):
    """True when `s` is a number once currency signs, percent and blanks are removed."""
    s = NON_ASCII.sub('', str(s)).replace(' ', '').replace('%', '').replace('$', '')
    s = s.replace('€', '').replace('\xa3', '')
    return bool(re.search('\\d', s)) and NON_NUMERO.sub('', s) == s


def righe(box, k):
    """Group OCR boxes into text rows; `k` is the width scale of the frame.

    Boxes under the confidence threshold or without at least two letters/digits (unless numeric)
    are ignored. Returns rows as dicts {yc, celle}, sorted top to bottom, cells left to right.
    """
    validi = []
    for i, b in enumerate(box):
        testo, conf, x0, y0, x1, y1 = b
        if float(conf) < CONF_MIN or (len(LETTERE_CIFRE.findall(str(testo))) < 2 and (not numerica(testo))):
            continue
        validi.append(dict(indice=i, testo=str(testo).strip(), conf=float(conf), x0=float(x0), y0=float(y0),
                           x1=float(x1), y1=float(y1), yc=(float(y0) + float(y1)) / 2))
    validi.sort(key=lambda x: (x['yc'], x['x0']))
    if not validi:
        return []
    altezza = statistics.median(max(1, b['y1'] - b['y0']) for b in validi)
    risultato = []
    for b in validi:
        limite = max(RIGA_PX * k, RIGA_ALTEZZA_QUOTA * altezza)
        if risultato and abs(b['yc'] - risultato[-1]['yc']) <= limite:
            risultato[-1]['celle'].append(b)
            risultato[-1]['yc'] = statistics.mean(x['yc'] for x in risultato[-1]['celle'])
        else:
            risultato.append(dict(yc=b['yc'], celle=[b]))
    for riga in risultato:
        riga['celle'].sort(key=lambda x: x['x0'])
    return risultato


def _gruppi_numerici(rs, k, righe_min):
    """Group numeric cells whose right edges align (or whose boxes overlap) into columns.

    Returns the groups spanning at least `righe_min` rows; each item is (x1, row index, cell).
    """
    numeri = []
    for ri, riga in enumerate(rs):
        for cella in riga['celle']:
            if numerica(cella['testo']):
                numeri.append((cella['x1'], ri, cella))
    numeri.sort(key=lambda x: x[0])
    gruppi = []
    for voce in numeri:
        if gruppi:
            ultima = gruppi[-1][-1]
            allineata = abs(voce[0] - ultima[0]) <= NUMERO_PX * k
            sovrapposta = _sovrappone((voce[2]['x0'], voce[2]['x1']), (ultima[2]['x0'], ultima[2]['x1']))
        if gruppi and (allineata or sovrapposta):
            gruppi[-1].append(voce)
        else:
            gruppi.append([voce])
    return [g for g in gruppi if len(set(x[1] for x in g)) >= righe_min]


def colonne(rs, w):
    """Find the column bands of the rows `rs` of a frame `w` pixels wide.

    A horizontal profile counts how many rows cover each 4-pixel step; runs covered by at least
    30% of the rows (and 2 rows) are columns. Returns [(x_start, x_end)].
    """
    if not rs:
        return []
    passo = max(1.0, PASSO_PX * _scala(w))
    occupazione = max(2, int(math.ceil(PROFILO_MIN * len(rs))))
    bordi = []
    for riga in rs:
        bordi.append(max(c['x1'] for c in riga['celle']))
    n = int(math.ceil(max(bordi) / passo)) + 1
    profilo = [0] * n
    for riga in rs:
        coperti = set()
        for c in riga['celle']:
            coperti.update(range(max(0, int(c['x0'] // passo)), min(n - 1, int(c['x1'] // passo)) + 1))
        for i in coperti:
            profilo[i] += 1
    risultato = []
    inizio = None
    for i, v in enumerate(profilo + [0]):
        if v >= occupazione and inizio is None:
            inizio = i
        elif v < occupazione and inizio is not None:
            risultato.append((inizio * passo, i * passo))
            inizio = None
    return risultato


def _assegna(c, cols, k):
    """Index of the column holding cell `c`, or of the nearest one within 12 scaled pixels; else None."""
    cx = (c['x0'] + c['x1']) / 2
    dentro = [i for i, banda in enumerate(cols) if banda[0] <= cx <= banda[1]]
    if dentro:
        return dentro[0]
    distanze = []
    for j, banda in enumerate(cols):
        distanze.append((min(abs(cx - banda[0]), abs(cx - banda[1])), j))
    distanza, indice = min(distanze or [(float('inf'), -1)])
    return indice if distanza <= VICINO_COLONNA_PX * k else None


def _celle_riga(r, cols, k):
    """Distribute the cells of row `r` over the columns `cols`.

    Returns (list of cell texts per column, set of box indices used), or (None, set()) when a
    cell fits no column.
    """
    colonne_celle = [[] for _ in cols]
    usati = set()
    for c in r['celle']:
        j = _assegna(c, cols, k)
        if j is None:
            return (None, set())
        colonne_celle[j].append(c)
        usati.add(c['indice'])
    testi = []
    for celle in colonne_celle:
        ordinate = sorted(celle, key=lambda z: z['x0'])
        testi.append(' '.join(x['testo'] for x in ordinate).strip())
    return (testi, usati)


def _intestazione(rs, ids, cols, usati, step):
    """Look above the first table row for a header line with text only; return its texts per column.

    Only the nearest row without digits and with 2+ cells counts; its boxes are added to `usati`.
    """
    primo = ids[0]
    intestazione = [''] * len(cols)
    for i in range(primo - 1, -1, -1):
        if rs[primo]['yc'] - rs[i]['yc'] > DISTANZA_INTESTAZIONE * step:
            break
        senza_cifre = not any(re.search('\\d', c['testo']) for c in rs[i]['celle'])
        if len(rs[i]['celle']) >= 2 and senza_cifre:
            for c in rs[i]['celle']:
                sovrapposte = []
                for x in range(len(cols)):
                    if max(0, min(c['x1'], cols[x][1]) - max(c['x0'], cols[x][0])) > 0:
                        sovrapposte.append(x)
                if len(sovrapposte) != 1:
                    continue
                j = sovrapposte[0]
                intestazione[j] = (intestazione[j] + ' ' + c['testo']).strip()
                usati.add(c['indice'])
            break
    return intestazione


def _righe_intercalate(rs, ids, cols, k, rows, usati):
    """Merge into `rows` the single-cell text lines found between two table rows (wrapped cells).

    A line in the first column becomes a new row; in other columns its text is appended to the
    cell of the preceding table row. Boxes used are added to `usati`.
    """
    for i in range(ids[0] + 1, ids[-1]):
        if i in ids or len(rs[i]['celle']) != 1 or re.search('\\d', rs[i]['celle'][0]['testo']):
            continue
        c = rs[i]['celle'][0]
        j = _assegna(c, cols, k)
        if j is None:
            continue
        precedente = max(x for x, z in enumerate(ids) if z < i)
        if j == 0:
            rows.insert(precedente + 1, [''] * len(cols))
            rows[precedente + 1][0] = c['testo']
        else:
            rows[precedente][j] = (rows[precedente][j] + ' ' + c['testo']).strip()
        usati.add(c['indice'])


def _tabella_da_blocco(rs, ids, box, larghezza, k, step, righe_min):
    """Build one table from the consecutive row indices `ids`, or return None if it is not a table.

    Rejects blocks that are too short, irregularly spaced, with too few columns, with a cell
    outside every column, or with columns that are mostly empty.
    """
    if len(ids) < righe_min:
        return None
    data = [rs[i] for i in ids]
    distanze = [b['yc'] - a['yc'] for a, b in zip(data, data[1:])]
    if distanze and statistics.pstdev(distanze) / statistics.mean(distanze) > CV_MASSIMO:
        return None
    cols = colonne(data, larghezza)
    if len(cols) < COLONNE_MIN:
        return None
    rows = []
    usati = set()
    for r in data:
        celle, indici = _celle_riga(r, cols, k)
        if celle is None:
            return None
        rows.append(celle)
        usati |= indici
    for i in range(len(cols)):
        if sum(bool(r[i]) for r in rows) < math.ceil(OCCUPAZIONE_MIN * len(rows)):
            return None
    intestazione = _intestazione(rs, ids, cols, usati, step)
    _righe_intercalate(rs, ids, cols, k, rows, usati)
    inclusi = [box[i] for i in usati]
    riquadro = (min(float(x[2]) for x in inclusi), min(float(x[3]) for x in inclusi),
                max(float(x[4]) for x in inclusi), max(float(x[5]) for x in inclusi))
    return dict(intest=intestazione, righe=rows, usati=usati, riquadro=riquadro, colonne=cols)


def trova(box, larghezza, righe_min=RIGHE_MIN):
    """Find the tables in the OCR boxes `box` of a frame `larghezza` pixels wide.

    A table is a run of rows where at least two numeric columns line up, evenly spaced, with
    three or more column bands. Returns a list of dicts with keys intest, righe, usati,
    riquadro, colonne.
    """
    k = _scala(larghezza)
    rs = righe(box, k)
    if not rs:
        return []
    gruppi = _gruppi_numerici(rs, k, righe_min)
    if len(gruppi) < 2:
        return []
    validi = []
    for i in range(len(rs)):
        if sum(any(x[1] == i for x in g) for g in gruppi) >= 2:
            validi.append(i)
    if len(validi) < righe_min:
        return []
    step = statistics.median([rs[b]['yc'] - rs[a]['yc'] for a, b in zip(validi, validi[1:])] or [1])
    blocchi = []
    corrente = [validi[0]]
    for a, b in zip(validi, validi[1:]):
        if rs[b]['yc'] - rs[a]['yc'] <= DISTANZA_BLOCCO * step:
            corrente.append(b)
        else:
            blocchi.append(corrente)
            corrente = [b]
    blocchi.append(corrente)
    risultati = []
    for ids in blocchi:
        tabella = _tabella_da_blocco(rs, ids, box, larghezza, k, step, righe_min)
        if tabella is not None:
            risultati.append(tabella)
    return risultati


def _firma(t, k=1):
    """Identity of a table across frames: its normalised header, else the column positions."""
    intestazione = tuple(_norm(x) for x in t.get('intest', []))
    if any(intestazione):
        return intestazione
    posizioni = tuple(int(round(c[0] / max(1, FIRMA_COLONNA_PX * k))) for c in t.get('colonne', []))
    return (len(t.get('colonne', [])),) + posizioni


def _chiave(r, i):
    """Key of row `r`: the normalised first non-numeric cell with letters, else '#<index>'."""
    for c in r:
        if _let(c) and (not numerica(c)):
            return _norm(c)
    return '#%d' % i


def _distanza_uno(a, b):
    """True when the normalised texts `a` and `b` are equal or differ by one character (typo)."""
    a = _norm(a)
    b = _norm(b)
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) <= 1
    corto, lungo = (a, b) if len(a) < len(b) else (b, a)
    for i in range(len(corto)):
        if corto[i] != lungo[i]:
            return corto[i:] == lungo[i + 1:]
    return True


def _numero_norm(s):
    """Number text without non-ASCII, blanks and currency/percent signs, decimal comma as dot."""
    s = NON_ASCII.sub('', str(s)).replace(' ', '').replace('%', '').replace('$', '')
    return s.replace('€', '').replace('£', '').replace(',', '.')


def _uguale(a, b):
    """True when two cells are the same value: equal numbers, or texts differing by one typo."""
    if numerica(a) or numerica(b):
        return numerica(a) and numerica(b) and (_numero_norm(a) == _numero_norm(b))
    return _distanza_uno(a, b)


def _righe_diverse(vecchia, nuova):
    """True when two rows have a different number of cells or any cell with a different value."""
    if len(vecchia) != len(nuova):
        return True
    return any(not _uguale(a, b) for a, b in zip(vecchia, nuova))


class Memoria:
    """Remembers the rows of the tables seen so far, to report only what is new or changed."""

    def __init__(self):
        """Start with no remembered table."""
        self.tabelle = {}

    def __bool__(self):
        """True when at least one table is remembered."""
        return bool(self.tabelle)

    def confronta(self, t, tabella):
        """Compare `tabella` seen at time `t` with the remembered table of the same signature.

        Returns ('nuova', all rows) for an unseen table (or one with half its rows unmatched),
        ('cambiate', changed rows) when some rows differ, None when nothing changed. The
        memory is updated in every case.
        """
        firma = _firma(tabella)
        corrente = collections.OrderedDict()
        for i, r in enumerate(tabella['righe']):
            corrente[_chiave(r, i)] = list(r)
        vecchia = self.tabelle.get(firma)
        if vecchia is None:
            esito = ('nuova', list(tabella['righe']))
        else:
            # identical keys first, then keys with one typo; each old row is used only once
            abbinate = {}
            usate = set()
            for k in corrente:
                if k in vecchia['righe']:
                    abbinate[k] = k
                    usate.add(k)
            for k in corrente:
                if k in abbinate:
                    continue
                for x in vecchia['righe']:
                    if x not in usate and _distanza_uno(k, x):
                        abbinate[k] = x
                        usate.add(x)
                        break
            senza_abbinamento = [k for k in corrente if k not in abbinate]
            if corrente and len(senza_abbinamento) >= math.ceil(RIGHE_NUOVE_QUOTA * len(corrente)):
                esito = ('nuova', list(tabella['righe']))
            else:
                cambiate = []
                for k, r in corrente.items():
                    if k not in abbinate or _righe_diverse(vecchia['righe'][abbinate[k]], r):
                        cambiate.append(r)
                esito = ('cambiate', cambiate) if cambiate else None
        self.tabelle[firma] = dict(t=float(t), righe=corrente, intest=list(tabella['intest']),
                                   colonne=list(tabella.get('colonne', [])))
        return esito

    def parziale(self, t, candidato):
        """Match a partly visible table `candidato` (seen at time `t`) with a recent remembered one.

        The remembered table needs two overlapping columns and a reading within two minutes. On a
        match, `candidato` takes its header and columns, changed rows are stored, and
        ('cambiate', rows) is returned; None when nothing matched or changed.
        """
        trovate = []
        for firma, memorizzata in self.tabelle.items():
            if float(t) - memorizzata['t'] <= MEMORIA_PARZIALE_S:
                sovrapposte = 0
                for banda in candidato.get('colonne', []):
                    if any(_sovrappone(banda, altra) for altra in memorizzata['colonne']):
                        sovrapposte += 1
                if sovrapposte >= 2:
                    trovate.append((memorizzata['t'], firma, memorizzata))
        if not trovate:
            return None
        _, _, memorizzata = max(trovate)
        corrente = collections.OrderedDict()
        for i, r in enumerate(candidato['righe']):
            corrente[_chiave(r, i)] = list(r)
        cambiate = []
        for k, r in corrente.items():
            if k in memorizzata['righe'] and _righe_diverse(memorizzata['righe'][k], r):
                cambiate.append(r)
                memorizzata['righe'][k] = r
        memorizzata['t'] = float(t)
        candidato['intest'] = list(memorizzata['intest'])
        candidato['colonne'] = list(memorizzata['colonne'])
        return ('cambiate', cambiate) if cambiate else None


def md(t, dove, esito, righe, intest):
    """Render a table outcome as a Markdown block of at most BLOCCO_MAX characters.

    `esito` is ('nuova' | 'cambiate', rows); `dove` says where the table is. Cells are cut,
    columns and rows are capped, and rows are dropped from the bottom until the block fits.
    """
    tipo, sel = esito
    cols = min(COLONNE_MAX, max(len(intest), max([len(r) for r in sel] or [0])))
    intestazione = [str(x)[:CELLA_MAX] for x in list(intest)[:cols]] + [''] * cols
    intestazione = intestazione[:cols]
    totale = len(sel)
    rows = [list(r)[:cols] for r in sel[:RIGHE_MAX]]
    titolo = '[%02d:%02d] TABELLA %s, %s %d righe su %d:' % (int(float(t) // 60), int(float(t) % 60), dove,
                                                           tipo, totale, totale)

    def make(rs):
        """Build the Markdown text for the rows `rs`, with a '(+N righe)' line for the omitted ones."""
        linee = [titolo, '| ' + ' | '.join(intestazione) + ' |', '|' + '|'.join(['-'] * cols) + '|']
        for r in rs:
            celle = [str(x)[:CELLA_MAX] for x in r] + [''] * (cols - len(r))
            linee.append('| ' + ' | '.join(celle) + ' |')
        if totale - len(rs):
            linee.append('| ... (+%d righe) |' % (totale - len(rs)))
        return '\n'.join(linee)

    while len(make(rows)) > BLOCCO_MAX and rows:
        rows.pop()
    return make(rows)[:BLOCCO_MAX]


def taglia_totale(blocchi, durata):
    """Keep the blocks within 6 characters per second of video, dropping the shortest first.

    `blocchi` items are tuples whose first element is the time and third the text. Among equal
    lengths the latest block is dropped first. Returns the kept blocks sorted by time.
    """
    tenuti = list(blocchi)
    tetto = max(0, int(float(durata) * CARATTERI_AL_SECONDO))
    while tenuti and sum(len(x[2]) for x in tenuti) > tetto:
        tenuti.pop(min(range(len(tenuti)), key=lambda i: (len(tenuti[i][2]), -float(tenuti[i][0]))))
    return sorted(tenuti, key=lambda x: float(x[0]))


def contenimento_ok(a, b):
    """True when the shorter of the strings `a`, `b` is at least half as long as the longer."""
    m = max(len(a), len(b))
    return m == 0 or min(len(a), len(b)) / float(m) >= CONTENIMENTO_MIN


def _box_ocr3(doc):
    """Yield (time, boxes) for each reading of an ocr3 document, boxes in absolute frame pixels."""
    for t, ox, oy, vals in doc.get('letture', []):
        yield (float(t), [(x, cf, ox + x0, oy + y0, ox + x1, oy + y1) for x, cf, x0, y0, x1, y1 in vals])


def _istantanee_ocr1(doc):
    """Collect the ocr1 status-zone texts per second as [(second, [(text, conf, *zone)])], sorted.

    Cells below CONF_ISTANTI are dropped; the cells of one reading are joined into one text.
    """
    zones = doc.get('zone', [])
    risultato = collections.defaultdict(list)
    for zi, letture in doc.get('stato', {}).items():
        z = zones[int(zi)]
        for n, vals in letture:
            testo = ' '.join(str(x[0]) for x in vals if len(x) >= 2 and float(x[1]) >= CONF_ISTANTI).strip()
            if testo:
                risultato[int(n)].append((testo, CONF_ISTANTI, *z))
    return sorted(risultato.items())


def _in_fase(n, fasi):
    """True when second `n` falls just after the start of a phase that has at least one instant."""
    for f in fasi:
        inizio = float(f.get('t0', -999))
        if inizio <= n <= inizio + ISTANTE_FASE_S and int(f.get('ist', 0)) >= 1:
            return True
    return False


def istanti_fotogramma(ocr3, ocr1, fasi, durata):
    """List the instants where a new table (ocr3) or a new numeric list (ocr1) appears.

    `fasi` are the video phases (keys t0, ist); `durata` is the video length in seconds. Returns
    dicts {t, descrizione, tipo='tabella'} in time order, at least 3 s apart, at most one per
    minute and four overall.
    """
    istanti = []
    memoria = Memoria()
    for t, box in _box_ocr3(ocr3 or {}):
        for tab in trova(box, (ocr3 or {}).get('W', 1280)):
            esito = memoria.confronta(t, tab)
            if esito and esito[0] == 'nuova':
                colonne_testo = ' | '.join(x for x in tab['intest'] if x)
                descrizione = 'tabella nuova: %d righe, colonne %s' % (len(tab['righe']), colonne_testo)
                istanti.append(dict(t=t, descrizione=descrizione, tipo='tabella'))
    visti = {}
    for n, box in _istantanee_ocr1(ocr1 or {}):
        numeri = sorted([b for b in box if numerica(b[0])], key=lambda b: b[4])
        gruppi = []
        for b in numeri:
            if gruppi and abs(float(b[4]) - float(gruppi[-1][-1][4])) <= NUMERO_PX:
                gruppi[-1].append(b)
            else:
                gruppi.append([b])
        for g in gruppi:
            if len(g) < LISTA_VALORI_MIN or not _in_fase(n, fasi):
                continue
            chiave = int(round(statistics.mean(float(x[4]) for x in g) / NUMERO_PX))
            valori = tuple(_norm(x[0]) for x in g)
            vecchi = visti.get(chiave)
            visti[chiave] = valori
            if vecchi is not None:
                quota_nota = sum(x in vecchi for x in valori) / float(max(len(valori), len(vecchi)))
                if 1 - quota_nota < LISTA_NUOVA_QUOTA:
                    continue
            anteprima = ', '.join(x[0] for x in g[:LISTA_ANTEPRIMA])
            puntini = ' ...' if len(g) > LISTA_ANTEPRIMA else ''
            descrizione = 'lista nuova: colonna di %d valori (%s%s)' % (len(g), anteprima, puntini)
            istanti.append(dict(t=float(n) + 0.2, descrizione=descrizione, tipo='tabella'))
    istanti.sort(key=lambda x: x['t'])
    distanziati = []
    for x in istanti:
        if not distanziati or x['t'] - distanziati[-1]['t'] >= ISTANTI_VICINI_S:
            distanziati.append(x)
    return distanziati[:min(ISTANTI_MAX, int(math.ceil(float(durata) / 60))) if durata else 0]


def scegli_fotogrammi(base, istanti, durata):
    """Replace, in the base frame list, the frames nearest to each table instant.

    A non-table frame within 2 s of the instant is preferred; otherwise the nearest frame of any
    kind is replaced. Returns the new list sorted by time (`durata` is not used).
    """
    if not base:
        return []
    risultato = [dict(x) for x in base]
    for n in sorted(istanti, key=lambda x: x['t']):
        vicini = [(abs(float(x['t']) - float(n['t'])), i) for i, x in enumerate(risultato)
                  if x.get('tipo') != 'tabella']
        entro = [x for x in vicini if x[0] <= SOSTITUZIONE_VICINA_S]
        if entro:
            i = min(entro)[1]
        else:
            i = min(range(len(risultato)), key=lambda j: abs(float(risultato[j]['t']) - float(n['t'])))
        risultato[i] = dict(n)
    return sorted(risultato, key=lambda x: float(x['t']))
