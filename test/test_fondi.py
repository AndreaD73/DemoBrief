# -*- coding: utf-8 -*-
"""Tests of fondi.Fusione: section switching, thresholds, legends and deictic completion."""
import json
import os
import sys
import tempfile

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, SRC)

import fondi


def evento_doc(forza=0.8):
    """Return a valid events document with one 'clic' event of the given strength."""
    evento = {'id': 'D1', 't': 1.0, 'durata': 0.2, 'canale': 'cursore', 'tipo': 'clic', 'zona': None,
              'punto': None, 'testo': 'Keep Groups', 'forza': forza, 'stampa': 'sezione', 'rif': [],
              'dati': {}}
    return {'schema': 1, 'canale': 'cursore', 'lettera': 'D', 'versione': '0.1', 'sezione': 'CURSORE',
            'legenda': 'CURSORE = clic e soste', 'formati': {'clic': 'clic {ogg}'},
            'stat': {'copertura': 1.0}, 'eventi': [evento]}


def scrivi_evento(cartella, forza=0.8):
    """Write eventi_cursore.json with one event of the given strength into `cartella`."""
    with open(os.path.join(cartella, 'eventi_cursore.json'), 'w', encoding='utf-8') as file_json:
        json.dump(evento_doc(forza), file_json)


def fusione(cartella):
    """Build a Fusione over `cartella` with a two-word sentence ('click this') and a 10 s video."""
    parole = [[0.5, 0.8, 'click'], [0.9, 1.2, 'this']]
    return fondi.Fusione(cartella, parole, [parole], [], [], [], 10.0)


def test_nessun_evento_e_noop(monkeypatch):
    """Without event files the merge is a no-op: nothing printed, lists returned untouched."""
    monkeypatch.delenv('AD_MOVIOLA_SENZA', raising=False)
    with tempfile.TemporaryDirectory() as cartella:
        fus = fusione(cartella)
        valori = [1, 2]
        assert fus.stat == {}
        assert fus.tieni('MOTO', valori) is valori
        assert fus.sezioni(0) == []
        assert fus.righe_prima(0) == []
        assert fus.isolati() == []
        assert fus.usate() == []
        assert fus.legenda() == ''


def test_sottrazione(monkeypatch):
    """Sections named in AD_MOVIOLA_SENZA are switched off (case and blanks ignored)."""
    monkeypatch.setenv('AD_MOVIOLA_SENZA', ' VOCE, testo ,MOTO ')
    with tempfile.TemporaryDirectory() as cartella:
        fus = fusione(cartella)
        assert fus.attiva('VOCE') is False
        assert fus.tieni('TESTO', [1]) == []
        assert fus.tieni('SCHERMO', [1]) == [1]
        assert fus.tieni('MOTO', [1]) == []


def test_evento_stampato(monkeypatch):
    """A valid event is printed in its section and listed in the sources and legend."""
    monkeypatch.delenv('AD_MOVIOLA_SENZA', raising=False)
    with tempfile.TemporaryDirectory() as cartella:
        scrivi_evento(cartella)
        fus = fusione(cartella)
        assert fus.sezioni(0) == ['CURSORE 00:01 clic "Keep Groups"']
        assert fus.usate() == ['cursore (1 eventi)']
        assert fus.legenda() == 'CURSORE = clic e soste'


def test_evento_sotto_soglia(monkeypatch):
    """An event under the strength threshold is dropped and counted in the statistics."""
    monkeypatch.delenv('AD_MOVIOLA_SENZA', raising=False)
    with tempfile.TemporaryDirectory() as cartella:
        scrivi_evento(cartella, 0.49)
        fus = fusione(cartella)
        assert fus.sezioni(0) == []
        assert fus.stat['eventi_scartati_soglia'] == 1


def test_canale_futuro_sottratto(monkeypatch):
    """A channel whose section is switched off prints nothing and contributes no legend."""
    monkeypatch.setenv('AD_MOVIOLA_SENZA', 'CURSORE')
    with tempfile.TemporaryDirectory() as cartella:
        scrivi_evento(cartella)
        fus = fusione(cartella)
        assert fus.sezioni(0) == []
        assert fus.legenda() == ''


def test_deittico_dopo_pulizia(monkeypatch):
    """The deictic word 'this' is completed with the text of the nearby click."""
    monkeypatch.delenv('AD_MOVIOLA_SENZA', raising=False)
    with tempfile.TemporaryDirectory() as cartella:
        scrivi_evento(cartella)
        fus = fusione(cartella)
        assert fus.voce(0, 'click this') == 'click this [→Keep Groups]'
