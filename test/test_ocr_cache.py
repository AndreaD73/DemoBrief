import json
import os
import sys

import numpy as np

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(RADICE, 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import ocr


class PoolFinto:
    def imap(self, funzione, elementi, chunksize=1):
        return map(funzione, elementi)


class MotoreFinto:
    def __init__(self):
        self.chiamate = 0

    def __call__(self, *args, **kwargs):
        self.chiamate += 1
        return [['testo', 0.95]], None


def test_impronta_quantizza_a_sedici_livelli():
    a = np.zeros((48, 96, 3), dtype=np.uint8)
    b = np.full((48, 96, 3), 15, dtype=np.uint8)
    c = np.full((48, 96, 3), 16, dtype=np.uint8)
    assert ocr.impronta(a) == ocr.impronta(b)
    assert ocr.impronta(a) != ocr.impronta(c)
    assert len(ocr.impronta(a)) == 32


def test_cache_deduplica_nel_lotto_e_persiste(tmp_path, monkeypatch):
    motore = MotoreFinto()
    ocr._eng = motore
    contatori = []
    monkeypatch.setattr(
        ocr.tempi,
        'ocr',
        lambda richieste=0, cache=0, rapidocr=0: contatori.append(
            (richieste, cache, rapidocr)
        ),
    )
    immagine = np.zeros((40, 120, 3), dtype=np.uint8)
    primo = ocr.esegui_ocr(
        PoolFinto(),
        [('line:1', immagine), ('line:2', immagine.copy())],
        str(tmp_path),
        chunksize=8,
    )
    secondo = ocr.esegui_ocr(
        PoolFinto(),
        [('line:3', immagine.copy())],
        str(tmp_path),
        chunksize=8,
    )
    assert primo == {
        'line:1': [['testo', 0.95]],
        'line:2': [['testo', 0.95]],
    }
    assert secondo == {'line:3': [['testo', 0.95]]}
    assert motore.chiamate == 1
    assert contatori == [(2, 1, 1), (1, 1, 0)]
    cache = json.loads((tmp_path / 'ocr_cache.json').read_text(encoding='utf-8'))
    assert len(cache) == 1
    assert list(cache.values())[0]['line'] == [['testo', 0.95]]


def test_fasce_hanno_altezza_e_sovrapposizione_fisse():
    assert ocr.fasce_orizzontali(0, 128) == [
        (0, 64),
        (48, 112),
        (64, 128),
    ]
    assert ocr.fasce_orizzontali(10, 50) == [(10, 50)]


def test_ricomposizione_sposta_coordinate_e_fonde_duplicati():
    risultati = {
        'box:1': [['Titolo', 0.80, 4, 50, 44, 60]],
        'box:2': [['Titolo', 0.95, 4, 2, 44, 12]],
    }
    assert ocr.ricomponi_fasce([(0, 'box:1'), (48, 'box:2')], risultati) == [
        ['Titolo', 0.95, 4, 50, 44, 60]
    ]
