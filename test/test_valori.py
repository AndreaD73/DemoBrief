# -*- coding: utf-8 -*-
"""Tests of valori.py: field parsing, value series, DOVE matching and the events written."""
import collections
import json
import os
import sys
import tempfile

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, SRC)

import valori as V
import eventi
import fondi


def A(t, tx, cf=0.95, cx=0.5, cy=0.5, src='z1', grp=None, box=None):
    """Build an OCR atom tuple as atomi() would: time, text, confidence, centre, box, source, group."""
    x0, y0, x1, y1 = box or (cx, cy, cx, cy)
    return (float(t), tx, cf, cx, cy, x0, y0, x1, y1, src, grp or (src, 'z', float(t)))


VOC = collections.Counter({'loop': 9, 'vertices': 9, 'span': 5, 'count': 5, 'edges': 5, 'quantity': 5,
                           'face': 6, 'nearest': 6, 'project': 6, 'snap': 5, 'target': 5,
                           'individual': 5, 'elements': 5})


def scrivi_json(cartella, nome, contenuto):
    """Write `contenuto` as JSON to the file `nome` inside `cartella`."""
    with open(os.path.join(cartella, nome), 'w') as file_json:
        json.dump(contenuto, file_json)


# ---- A: parsing and numbers
def test_campo_intero_e_varianti():
    """Whole 'Label: 12' cells in several spellings are parsed; a typo in the label is corrected."""
    corr = V.correttore(VOC)
    trovati = V.campi([A(1, 'Loop: 8'), A(2, 'Loop27'), A(3, 'Loop-20'), A(4, 'toop:20')], corr)
    assert {r[3] for r in trovati} == {'8', '27', '20'}
    assert {r[1] for r in V.campi([A(4, 'toop:20')], corr)} == {'loop'}          # toop -> loop (vocabulary)


def test_numeri():
    """Thousands separators, decimal commas, fractions and a letter O are normalised."""
    assert V.num('11.476') == '11476'
    assert V.num('7,058') == '7058'
    assert V.num('1,5') == '1.5'
    assert V.num('16/16') == '16/16'
    assert V.num('O') == '0'


def test_uguali_barra_prefisso():
    """Readings that differ by a lost slash or by a digit prefix are the same number."""
    assert V.uguali('123', '1/23')
    assert V.uguali('1164', '1/64')
    assert V.uguali('43', '43852')
    assert not V.uguali('16/16', '1/64')
    assert not V.uguali('8', '20')


def test_etichetta_e_numero_in_celle_vicine():
    """A label and a number in two neighbouring cells of the same row form one field."""
    gruppo = ('l3', 98.0)
    atomi = [A(98, 'Vertices', 0.97, .08, .21, src='l3', grp=gruppo, box=(.042, .20, .077, .222)),
             A(98, '1/64', 0.99, .09, .21, src='l3', grp=gruppo, box=(.076, .20, .105, .222))]
    assert [(r[1], r[3]) for r in V.campi(atomi, V.correttore(VOC))] == [('vertices', '1/64')]


def test_date_scartate():
    """Dates are not parameters and produce no field."""
    assert V.campi([A(1, 'February 27.2024')], V.correttore(VOC)) == []


# ---- A: series
def seq(atomi, dur=300):
    """Run valori() on `atomi` and return only (label, series) of each result."""
    return [(r[1], r[2]) for r in V.valori(atomi, VOC, dur)]


def test_loop_8_a_20_con_cifra_in_piu_riparata():
    """The series 8>20 survives an extra-digit misread (201 for 20)."""
    atomi = [A(25, 'Loop: 8', .92), A(26, 'Loop: 8', .97), A(29, 'Loop: 11', .85), A(30, 'Loop:201.', .85)]
    atomi += [A(t, 'Loop:20', .95, src='fin') for t in (40.5, 41.0, 41.5)]
    assert seq(atomi) == [('Loop', '8>20')]


def test_valore_singolo_non_si_stampa():
    """A value that never changes is not reported."""
    assert seq([A(5, 'Loop: 8', .95), A(6, 'Loop: 8', .95)]) == []


def test_lettura_singola_bassa_fiducia_scartata():
    """A single low-confidence reading of a new value is not trusted."""
    assert seq([A(5, 'Loop: 8', .95), A(6, 'Loop: 8', .95), A(7, 'Loop: 9', .80)]) == []


def test_episodi_separati_da_pausa():
    """Two changes separated by a long silence are two separate episodes."""
    atomi = [A(t, 'Loop: %d' % v, .95) for t, v in ((10, 8), (11, 8), (12, 20), (13, 20),
                                                     (100, 8), (101, 8), (102, 27), (103, 27))]
    assert seq(atomi) == [('Loop', '8>20'), ('Loop', '8>27')]


def test_tabella_stessa_etichetta_due_righe_non_e_un_campo():
    """The same label on two rows with different values is a table, not a field that changes."""
    atomi = [A(5, 'Quantity 4', .95, .5, .30), A(5, 'Quantity 6', .95, .5, .40),
             A(9, 'Quantity 5', .95, .5, .30), A(9, 'Quantity 7', .95, .5, .40)]
    assert seq(atomi) == []


def test_contatore_volatile_scartato():
    """A counter that changes at every reading (a clock) is discarded."""
    atomi = [A(t, 'Clock %d' % (t * 7 % 50), .95) for t in range(1, 10)]
    assert seq(atomi) == []


def test_ocr3_senza_pausa_serie_vertices_edges():
    """Full-screen readings far apart in time still form one series, with slash forms unified."""
    gruppo_a, gruppo_b = ('l3', 49.0), ('l3', 95.0)
    atomi = [A(49, 'Vertices16/16', .96, .08, .21, src='l3', grp=gruppo_a),
             A(49, 'Vertices16/16', .96, .08, .21, src='l3', grp=gruppo_a)]
    atomi += [A(95, 'Vertices', .97, .06, .21, src='l3', grp=gruppo_b, box=(.042, .20, .077, .222)),
              A(95, '1164', .92, .09, .21, src='l3', grp=gruppo_b, box=(.078, .20, .105, .222)),
              A(98, 'Vertices', .93, .06, .21, src='l3', grp=('l3', 98.0), box=(.041, .20, .077, .222)),
              A(98, '1/64', .99, .09, .21, src='l3', grp=('l3', 98.0), box=(.076, .20, .105, .222))]
    assert seq(atomi, 126) == [('Vertices', '16/16>1/64')]


def test_cap_per_minuto():
    """The number of results is capped per minute of video: ceil(1.2 per minute), minimum 2."""
    atomi = []
    for k in range(10):
        nome = 'Param%s' % (chr(97 + k) * 3)
        atomi += [A(10 * k + 1, '%s %d' % (nome, 1), .95), A(10 * k + 2, '%s %d' % (nome, 1), .95),
                  A(10 * k + 3, '%s %d' % (nome, 2), .95), A(10 * k + 4, '%s %d' % (nome, 2), .95)]
    assert len(V.valori(atomi, collections.Counter(), 60)) == 2        # 60 s -> cap of 2


# ---- B
def frase(parole, t0=100.0):
    """Build a sentence of word entries [start, end, word] from a list of words."""
    return [[t0 + i * 0.4, t0 + i * 0.4 + 0.3, p] for i, p in enumerate(parole)]


def test_dove_face_nearest_con_contesto():
    """The spoken 'face nearest' finds the OCR text and returns its context lines and area."""
    g = ('l3', 101.0)
    atomi = [A(101, 'Snap Target for Individual Elements', .97, .55, .45, src='l3', grp=g,
               box=(.40, .44, .52, .46)),
             A(101, 'Face Project', .96, .43, .48, src='l3', grp=g, box=(.41, .47, .46, .49)),
             A(101, 'Face Nearest', .98, .43, .52, src='l3', grp=g, box=(.41, .51, .46, .53))]
    r = V.dove(atomi, [frase(['The', 'new', 'option', 'is', 'face', 'nearest'])], VOC, 120, [])
    assert [(x[1], x[2], x[3]) for x in r] == [('Face Nearest', 'finestra al centro',
                                                ['Snap Target for Individual Elements', 'Face Project'])]


def test_dove_salta_se_gia_in_schermo():
    """A text already printed in SCHERMO is not repeated by DOVE."""
    g = ('l3', 101.0)
    atomi = [A(101, 'Face Nearest', .98, .43, .52, src='l3', grp=g)]
    gia_stampato = [(101.0, 'x', 'Snap / Face Nearest')]
    assert V.dove(atomi, [frase(['face', 'nearest'])], VOC, 120, gia_stampato) == []


def test_dove_fuori_finestra_3s_non_corrisponde():
    """An OCR text more than 3 s away from the spoken phrase does not match it."""
    atomi = [A(110, 'Face Nearest', .98, .43, .52, src='l3', grp=('l3', 110.0))]
    assert V.dove(atomi, [frase(['face', 'nearest'])], VOC, 120, []) == []


def test_dove_scarta_frasi_lunghe_e_celle_con_cifre():
    """Long sentences and cells containing digits are not candidates for DOVE."""
    atomi = [A(101, 'First you have to import the Angel Clouds brushes before', .98),
             A(101, 'Edge 12 Loop', .98)]
    parlato = frase(['first', 'you', 'have', 'to', 'import', 'the', 'angel', 'edge', 'loop'])
    assert V.dove(atomi, [parlato], VOC, 120, []) == []


# ---- event output and merge
def test_eventi_validi_e_stampa_fusione(monkeypatch):
    """The events written by scrivi_eventi are valid and fondi.Fusione prints the VALORI section."""
    # the section print must not depend on config.json (VALORI is off by default)
    monkeypatch.setenv('AD_MOVIOLA_SENZA', ' ')
    with tempfile.TemporaryDirectory() as cartella:
        scrivi_json(cartella, 'parole.json', [[10.0, 10.5, 'Hello']])
        stato = {'0': [[25, [['Loop: 8', .92]]], [26, [['Loop: 8', .97]]]]}
        scrivi_json(cartella, 'ocr1.json', {'zone': [[0, 0, 0, 0]], 'stato': stato, 'H': 720, 'dur': 60.0,
                                            'passo': 1.0})
        finestre = [[t, 0, 0, [['Loop:20', .95, 10, 10]]] for t in (28.5, 29.0, 29.5)]
        scrivi_json(cartella, 'ocr2.json', {'stato': {}, 'finestre': finestre})
        scrivi_json(cartella, 'ocr3.json', {'W': 1280, 'H': 720, 'letture': []})
        frasi = [[[24.5, 25.0, 'Adjust'], [25.1, 25.6, 'loops']]]
        n_valori, n_dove = V.scrivi_eventi(cartella, frasi, VOC, 60.0, [])
        assert n_valori == 1
        assert os.path.exists(os.path.join(cartella, 'eventi_valori.json'))
        doc, errore = eventi.carica(os.path.join(cartella, 'eventi_valori.json'))
        assert errore == ''
        assert doc['eventi'][0]['dati']['seq'] == '8>20'
        fusione = fondi.Fusione(cartella, frasi[0], frasi, [], [], [], 60.0)
        assert fusione.sezioni(0) == ['VALORI 00:25 "Loop" 8>20']


def test_sezione_spenta_cancella_e_non_scrive():
    """A switched-off section deletes its stale file and writes nothing."""
    with tempfile.TemporaryDirectory() as cartella:
        percorso = os.path.join(cartella, 'eventi_valori.json')
        with open(percorso, 'w') as file_json:
            file_json.write('{}')
        V.scrivi_eventi(cartella, [], VOC, 60.0, [], spente=('VALORI', 'DOVE'))
        assert not os.path.exists(percorso)
        assert not os.path.exists(os.path.join(cartella, 'eventi_dove.json'))


def test_scala_lineare():
    """20000 readings are processed in a few seconds (the algorithm is linear)."""
    import time
    atomi = [A(i * 0.5, 'Loop: %d' % (i % 3), .95) for i in range(20000)]
    inizio = time.time()
    V.valori(atomi, VOC, 10000)
    assert time.time() - inizio < 5.0
