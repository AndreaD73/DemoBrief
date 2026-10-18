"""Test that the vectorised text-box discovery in ocr.py matches the slow reference version.

Builds random synthetic box sets, fakes frame reading and OCR, and compares the JSON of both results.
Reads and writes no files.
"""
import json
import os
import random
import sys

import numpy as np

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(RADICE, 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import ocr


def _caso_sintetico(rng, indice):
    """Build a random synthetic case: blank frames and the OCR boxes each frame would return.

    Boxes cluster around a few base rectangles, with small jitter, half-pixel offsets and
    exact repeats so that the merging logic of the discovery gets exercised.
    """
    numero_box = rng.randint(5, 400)
    numero_frame = rng.randint(4, 9)
    numero_basi = max(1, min(24, numero_box // 5))
    basi = []
    for _ in range(numero_basi):
        x = rng.randint(10, 560)
        y = rng.randint(10, 140)
        w = rng.randint(8, 48)
        h = rng.randint(8, 28)
        basi.append((x, y, min(632, x + w), min(172, y + h)))

    box_per_frame = [[] for _ in range(numero_frame)]
    ultimo = None
    for n in range(numero_box):
        base = basi[n % numero_basi]
        if n % 13 == 0 and ultimo is not None:
            box = list(ultimo)
        else:
            dx = rng.choice((-2, -1, 0, 0, 0, 1, 2))
            dy = rng.choice((-2, -1, 0, 0, 0, 1, 2))
            frazione = 0.5 if (n + indice) % 3 == 0 else 0.0
            box = [
                max(0.0, base[0] + dx + frazione),
                max(0.0, base[1] + dy + frazione),
                min(639.0, base[2] + dx + frazione),
                min(179.0, base[3] + dy + frazione),
            ]
        box_per_frame[n % numero_frame].append(box)
        ultimo = box

    immagini = [np.full((180, ocr.W, 3), 255 if i % 2 else 0, dtype=np.uint8) for i in range(numero_frame)]
    risultati = {'det:%d' % i: box_per_frame[i] for i in range(numero_frame)}
    return immagini, risultati


def test_scoperta_vettoriale_identica_alla_lenta(monkeypatch):
    """Fast and slow discovery must give byte-identical JSON and identical statistics."""
    rng = random.Random(17052026)
    for indice in range(20):
        immagini, risultati = _caso_sintetico(rng, indice)

        def frames_finti(video, fps, H, start=0, ss=None):
            return enumerate(immagini)

        def ocr_finto(pool, tasks, out, chunksize=1, **kwargs):
            return risultati

        monkeypatch.setattr(ocr, 'frames', frames_finti)
        monkeypatch.setattr(ocr, 'esegui_ocr', ocr_finto)

        stat_nuova = {}
        stat_lenta = {}
        nuova = ocr.scoperta('sintetico', 180, object(), stat_nuova)
        lenta = ocr._scoperta_lenta('sintetico', 180, object(), stat_lenta)

        assert json.dumps(nuova) == json.dumps(lenta)
        assert stat_nuova == stat_lenta
