# -*- coding: utf-8 -*-
"""Tests for tempi.py: the timing recorder must keep wall-clock and CPU seconds per block.

Reads nothing from disk except the temporary folder pytest provides; writes tempi.jsonl there.
"""
import json
import os
import sys

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, SRC)
import tempi


def test_blocco_registra_parete_e_cpu(monkeypatch):
    """A timed block stores the wall-clock and CPU seconds read from the (faked) clocks."""
    tempi._dati = {}
    tempi._overhead = 0.0
    parete = iter((10.0, 13.0, 20.0, 20.25))
    cpu = iter((5.0, 7.0))
    monkeypatch.setattr(tempi.time, 'perf_counter', lambda: next(parete))
    monkeypatch.setattr(tempi.time, 'process_time', lambda: next(cpu))
    with tempi.blocco('prova'):
        pass
    assert tempi._dati['prova']['secondi'] == 3.0
    assert tempi._dati['prova']['cpu_secondi'] == 2.0


def test_chiudi_scrive_cpu_secondi(tmp_path, monkeypatch):
    """Closing the recorder writes the CPU seconds of each block into tempi.jsonl."""
    lavoro = str(tmp_path)
    monkeypatch.setattr(tempi, '_fase', 'prova')
    monkeypatch.setattr(tempi, '_lavoro', lavoro)
    monkeypatch.setattr(tempi, '_radice', None)
    monkeypatch.setattr(tempi, '_chiuso', False)
    monkeypatch.setattr(tempi, '_catture', [])
    monkeypatch.setattr(tempi, '_overhead', 0.0)
    dati_blocco = {'secondi': 3.0, 'cpu_secondi': 2.0, 'chiamate': 1, 'fotogrammi': 0}
    monkeypatch.setattr(tempi, '_dati', {'blocco': dati_blocco})
    tempi.chiudi()
    with open(tmp_path / 'tempi.jsonl', encoding='utf-8') as file:
        righe = [json.loads(riga) for riga in file]
    blocco = next(riga for riga in righe if riga['blocco'] == 'blocco')
    assert blocco['secondi_totali'] == 3.0
    assert blocco['cpu_secondi'] == 2.0
