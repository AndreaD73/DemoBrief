# -*- coding: utf-8 -*-
import os
import sys

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(RADICE, 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import ocr


class ProcessoSempreVivo:
    def is_alive(self):
        return True


class PoolFinto:
    def __init__(self):
        self._pool = [ProcessoSempreVivo()]
        self.chiamate = []

    def close(self):
        self.chiamate.append('close')

    def terminate(self):
        self.chiamate.append('terminate')

    def join(self):
        self.chiamate.append('join')


class OrologioFinto:
    def __init__(self):
        self.adesso = 0.0

    def monotonic(self):
        return self.adesso

    def sleep(self, secondi):
        self.adesso += secondi


def test_chiusura_pool_termina_worker_ancora_vivo(monkeypatch, capsys):
    pool = PoolFinto()
    orologio = OrologioFinto()
    monkeypatch.setattr(ocr.time, 'monotonic', orologio.monotonic)
    monkeypatch.setattr(ocr.time, 'sleep', orologio.sleep)

    ocr.chiudi_pool(pool, attesa=0.5, intervallo=0.2)

    assert pool.chiamate == ['close', 'terminate', 'join']
    assert orologio.adesso == 0.5
    assert capsys.readouterr().out == 'pool terminato dopo 20 s\n'


def test_worker_scarica_tempi_dopo_il_compito(monkeypatch):
    scaricamenti = []
    monkeypatch.setattr(ocr, 't_line', lambda immagine: [['testo', 0.95]])
    monkeypatch.setattr(ocr.tempi, 'scarica', lambda: scaricamenti.append('scarica'))

    risultato = ocr.tdisp(('line:1:0', object()))

    assert risultato == ('line:1:0', [['testo', 0.95]])
    assert scaricamenti == ['scarica']
