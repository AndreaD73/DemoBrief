# -*- coding: utf-8 -*-
import os
import sys

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src'))
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import comune


def test_demobrief_ha_precedenza(monkeypatch):
    monkeypatch.setenv('AD_MOVIOLA_PROVA', 'vecchio')
    monkeypatch.setenv('DEMOBRIEF_PROVA', 'nuovo')
    assert comune.ambiente('PROVA') == 'nuovo'


def test_ad_moviola_resta_compatibile(monkeypatch):
    monkeypatch.delenv('DEMOBRIEF_PROVA', raising=False)
    monkeypatch.setenv('AD_MOVIOLA_PROVA', 'vecchio')
    assert comune.ambiente('PROVA') == 'vecchio'


def test_demobrief_vuota_ha_precedenza(monkeypatch):
    monkeypatch.setenv('AD_MOVIOLA_PROVA', 'vecchio')
    monkeypatch.setenv('DEMOBRIEF_PROVA', '')
    assert comune.ambiente('PROVA') == ''
