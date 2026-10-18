# -*- coding: utf-8 -*-
import os
import sys

SRC = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', 'src')
)
sys.path.insert(0, SRC)

import tabelle


def b(testo, x0, y0, x1, y1, conf=0.99):
    return (testo, conf, x0, y0, x1, y1)


def griglia(righe=3, y0=30, passo=25):
    out = [
        b('Articolo', 0, 0, 65, 14),
        b('Qta', 80, 0, 105, 14),
        b('Prezzo', 130, 0, 180, 14),
        b('Totale', 210, 0, 260, 14),
    ]
    for i in range(righe):
        y = y0 + i * passo
        out.extend([
            b('Voce%d' % (i + 1), 0, y, 65, y + 14),
            b(str(i + 1), 88, y, 105, y + 14),
            b('%d.00' % (10 + i), 140, y, 180, y + 14),
            b('%d.00' % (20 + i), 220, y, 260, y + 14),
        ])
    return out


def test_griglia_4x4_con_intestazione():
    trovate = tabelle.trova(griglia(), 1280)
    assert len(trovate) == 1
    assert trovate[0]['intest'] == ['Articolo', 'Qta', 'Prezzo', 'Totale']
    assert trovate[0]['righe'][0] == ['Voce1', '1', '10.00', '20.00']


def test_numeri_allineati_a_destra_stessa_colonna():
    box = griglia()
    box.extend([
        b('123', 276, 120, 300, 134),
        b('12345', 260, 145, 300, 159),
        b('12345678', 236, 170, 300, 184),
    ])
    righe = tabelle.righe(box, 1)
    gruppi = tabelle._gruppi_numerici(righe, 1, 3)
    assert any(
        len(gruppo) == 3
        and all(cella[2]['x1'] == 300 for cella in gruppo)
        for gruppo in gruppi
    )


def test_intestazioni_vicine_non_fuse():
    box = [
        b('Articolo', 0, 0, 40, 14),
        b('Qta', 58, 0, 98, 14),
        b('Prezzo', 120, 0, 170, 14),
    ]
    for i in range(3):
        y = 30 + i * 25
        box.extend([
            b('Voce%d' % i, 0, y, 40, y + 14),
            b(str(i + 1), 58, y, 98, y + 14),
            b('%d.00' % (10 + i), 120, y, 170, y + 14),
        ])
    trovate = tabelle.trova(box, 1280)
    assert len(trovate) == 1
    assert trovate[0]['intest'] == ['Articolo', 'Qta', 'Prezzo']


def test_menu_una_riga_non_e_tabella():
    box = [b('File', 0, 0, 30, 14), b('10', 50, 0, 70, 14), b('20', 90, 0, 110, 14)]
    assert tabelle.trova(box, 1280) == []


def test_paragrafo_non_e_tabella():
    box = [b('Questa e una frase completa', 0, i * 25, 250, i * 25 + 14) for i in range(6)]
    assert tabelle.trova(box, 1280) == []


def test_pannello_etichetta_valore_non_e_tabella():
    box = []
    for i in range(6):
        y = i * 25
        box.extend([b('Etichetta%d' % i, 0, y, 90, y + 14), b(str(i + 1), 120, y, 160, y + 14)])
    assert tabelle.trova(box, 1280) == []


def test_popup_sopra_tabella_resta_fuori():
    popup = b('Messaggio completo sopra la tabella', 0, 5, 260, 19)
    box = [popup]
    box.extend(griglia(y0=55))
    trovate = tabelle.trova(box, 1280)
    assert len(trovate) == 1
    assert 0 not in trovate[0]['usati']


def test_riga_continuazione_si_accoda():
    box = griglia(y0=30, passo=50)
    box.append(b('esteso', 140, 65, 180, 79))
    trovate = tabelle.trova(box, 1280)
    assert len(trovate) == 1
    assert trovate[0]['righe'][0][2] == '10.00 esteso'


def test_riga_sezione():
    box = griglia(y0=30, passo=50)
    box.append(b('Sezione', 0, 65, 65, 79))
    trovate = tabelle.trova(box, 1280)
    assert len(trovate) == 1
    assert trovate[0]['righe'][1] == ['Sezione', '', '', '']


def test_memoria_nuova_poi_cambiata_poi_nulla():
    memoria = tabelle.Memoria()
    tabella = tabelle.trova(griglia(), 1280)[0]
    primo = memoria.confronta(1, tabella)
    assert primo[0] == 'nuova'
    modificata = dict(tabella, righe=[list(riga) for riga in tabella['righe']])
    modificata['righe'][1][3] = '99.00'
    secondo = memoria.confronta(2, modificata)
    assert secondo == ('cambiate', [modificata['righe'][1]])
    assert memoria.confronta(3, modificata) is None


def test_memoria_tollera_virgola_e_refuso():
    memoria = tabelle.Memoria()
    tabella = tabelle.trova(griglia(), 1280)[0]
    memoria.confronta(1, tabella)
    modificata = dict(tabella, righe=[list(riga) for riga in tabella['righe']])
    modificata['righe'][0][0] = 'Vocel'
    modificata['righe'][0][2] = '10,00'
    assert memoria.confronta(2, modificata) is None


def test_vista_parziale_solo_con_memoria():
    completa = tabelle.trova(griglia(), 1280)[0]
    parziale = tabelle.trova(griglia(righe=2), 1280, 2)[0]
    parziale['righe'][0][3] = '99.00'
    vuota = tabelle.Memoria()
    assert vuota.parziale(30, parziale) is None
    recente = tabelle.Memoria()
    recente.confronta(0, completa)
    esito = recente.parziale(30, parziale)
    assert esito == ('cambiate', [parziale['righe'][0]])
    scaduta = tabelle.Memoria()
    scaduta.confronta(0, completa)
    assert scaduta.parziale(200, parziale) is None


def test_numerica():
    assert tabelle.numerica('160.00€')
    assert not tabelle.numerica('0%EX')
    assert tabelle.numerica('02/29/2024 11:25:25')


def test_md_formato_esatto():
    righe = [['Voce1', '1', '10.00'], ['Voce2', '2', '20.00'], ['Voce3', '3', '30.00']]
    esito = ('cambiate', righe)
    ottenuto = tabelle.md(201, 'pagina', esito, righe, ['Articolo', 'Qta', 'Totale'])
    atteso = ('[03:21] TABELLA pagina, cambiate 3 righe su 3:\n'
              '| Articolo | Qta | Totale |\n|-|-|-|\n'
              '| Voce1 | 1 | 10.00 |\n| Voce2 | 2 | 20.00 |\n| Voce3 | 3 | 30.00 |')
    assert ottenuto == atteso


def test_tetto_blocco_700():
    righe = [['Articolo molto lungo numero %d' % i, str(i), '1234567890' * 5] for i in range(30)]
    blocco = tabelle.md(1, 'pagina', ('nuova', righe), righe, ['Articolo', 'Qta', 'Prezzo'])
    assert len(blocco) <= 700
    mostrate = sum(1 for riga in blocco.splitlines() if riga.startswith('| Articolo molto'))
    assert '(+%d righe)' % (30 - mostrate) in blocco


def test_tetto_totale_per_durata():
    blocchi = [(1, 'pagina', 'x' * 40), (2, 'pagina', 'y' * 40)]
    rimasti = tabelle.taglia_totale(blocchi, 10)
    assert sum(len(x[2]) for x in rimasti) <= 60


def test_contenimento():
    assert tabelle.contenimento_ok('size', 'drawsize')
    assert not tabelle.contenimento_ok('delivered', 'deliveredinsideaverylongcompleteinterfacedialogsentence')


def test_istanti_colonna_numerica_con_cambio_pagina():
    ocr1 = {'zone': [[100, 0, 180, 14], [100, 20, 180, 34], [100, 40, 180, 54], [100, 60, 180, 74]],
            'stato': {'0': [[10, [['10.00', 0.99]]]], '1': [[10, [['20.00', 0.99]]]],
                      '2': [[10, [['30.00', 0.99]]]], '3': [[10, [['40.00', 0.99]]]]}}
    fasi = [{'t0': 9.5, 't1': 10.5, 'ist': 1}]
    istanti = tabelle.istanti_fotogramma({}, ocr1, fasi, 120)
    assert len(istanti) == 1
    assert istanti[0]['t'] == 10.2
    assert istanti[0]['tipo'] == 'tabella'


def test_istanti_colonna_numerica_senza_fase_ist():
    ocr1 = {'zone': [[100, 0, 180, 14], [100, 20, 180, 34], [100, 40, 180, 54], [100, 60, 180, 74]],
            'stato': {'0': [[10, [['10.00', 0.99]]]], '1': [[10, [['20.00', 0.99]]]],
                      '2': [[10, [['30.00', 0.99]]]], '3': [[10, [['40.00', 0.99]]]]}}
    fasi = [{'t0': 9.5, 't1': 10.5, 'ist': 0}]
    assert tabelle.istanti_fotogramma({}, ocr1, fasi, 120) == []


def test_fotogrammi_mai_oltre_base():
    base = [{'t': t, 'descrizione': 'azione', 'tipo': 'azione'} for t in (10, 20, 30, 40, 50)]
    istanti = [{'t': 21, 'descrizione': 'tabella uno', 'tipo': 'tabella'},
               {'t': 44, 'descrizione': 'tabella due', 'tipo': 'tabella'}]
    scelti = tabelle.scegli_fotogrammi(base, istanti, 120)
    assert len(scelti) == 5
    assert [x['t'] for x in scelti] == [10, 21, 30, 44, 50]
    assert tabelle.scegli_fotogrammi([], istanti, 120) == []


def test_accese_ambiente_e_config(monkeypatch):
    monkeypatch.setattr(tabelle.comune, 'ambiente', lambda nome: ' ')
    monkeypatch.setattr(tabelle.comune, 'config', lambda: {'tabelle': 'SFI'})
    assert tabelle.accese() == set()
    monkeypatch.setattr(tabelle.comune, 'ambiente', lambda nome: 'sf')
    assert tabelle.accese() == {'S', 'F'}
    monkeypatch.setattr(tabelle.comune, 'ambiente', lambda nome: None)
    monkeypatch.setattr(tabelle.comune, 'config', lambda: {'tabelle': 'i'})
    assert tabelle.accese() == {'I'}


def test_niente_parole_del_caso():
    vietate = ('odoo', 'invoice', 'invoiced', 'delivered', 'draft', 'quotation', 'sales')
    sorgente = open(tabelle.__file__, encoding='utf-8').read().lower()
    assert all(parola not in sorgente for parola in vietate)
