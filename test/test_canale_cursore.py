# -*- coding: utf-8 -*-
"""Synthetic tests of the cursor channel: frames generated with numpy/opencv, no real videos.

Reads nothing from disk except what the tests write themselves; those files go to a scratch
folder under test/_tmp that is removed at the end of each test.
"""
import json
import os
import shutil
import sys
import time

import cv2
import numpy as np

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src'))
TMP = os.path.abspath(os.path.join(os.path.dirname(__file__), '_tmp'))
sys.path.insert(0, SRC)
import canale_cursore as cc
import eventi


def prepara_tmp(nome):
    """Create an empty scratch folder `nome` under test/_tmp and return its path."""
    os.makedirs(TMP, exist_ok=True)
    cartella = os.path.join(TMP, nome)
    shutil.rmtree(cartella, ignore_errors=True)
    os.makedirs(cartella)
    return cartella


def chiudi_tmp(cartella):
    """Delete the scratch folder `cartella` and the parent test/_tmp if it is left empty."""
    shutil.rmtree(cartella, ignore_errors=True)
    if os.path.isdir(TMP) and not os.listdir(TMP):
        os.rmdir(TMP)


def setup_function(_):
    """Pytest hook: put the channel parameters back to the defaults before every test."""
    cc.imposta()


# ---------- synthetic sprites and scenes
FRECCIA = np.array([[0, 0], [0, 14], [4, 11], [7, 17], [9, 16], [6, 10], [11, 10]], np.int32)


def sprite(forma='freccia', negativa=False, lato=24):
    """Draw a white cursor sprite of the given shape on a black square of `lato` px.

    `forma` is one of freccia, testo, croce, mano; `negativa` inverts the polarity.
    """
    immagine = np.zeros((lato, lato), np.uint8)
    if forma == 'freccia':
        cv2.fillPoly(immagine, [FRECCIA + [4, 3]], 255)
    elif forma == 'testo':
        cv2.line(immagine, (12, 3), (12, 20), 255, 2)
        cv2.line(immagine, (9, 3), (15, 3), 255, 1)
        cv2.line(immagine, (9, 20), (15, 20), 255, 1)
    elif forma == 'croce':
        cv2.line(immagine, (12, 3), (12, 20), 255, 2)
        cv2.line(immagine, (3, 12), (20, 12), 255, 2)
    elif forma == 'mano':
        immagine[2:9, 8:12] = 255
        immagine[9:21, 3:19] = 255
    return 255 - immagine if negativa else immagine


def fondo(h=180, w=320, seed=1):
    """Make a gray background: a horizontal gradient from 50 to 90 with +-6 levels of noise."""
    rng = np.random.default_rng(seed)
    gradiente = np.tile(np.linspace(50, 90, w, dtype=np.float32), (h, 1))
    return np.clip(gradiente + rng.integers(-6, 7, (h, w)), 0, 255).astype(np.uint8)


def disegna(bg, x, y):
    """Draw the white arrow with its tip at (x, y) on a copy of the background `bg`."""
    fotogramma = bg.copy()
    fotogramma[y - 3:y + 21, x - 4:x + 20] = np.maximum(fotogramma[y - 3:y + 21, x - 4:x + 20],
                                                        sprite('freccia'))
    return fotogramma


def percorso(n=200, w=320, h=180):
    """Cursor tip position at every frame: 12 frames of fast motion (15-25 px a step) and 8 at rest."""
    rng = np.random.default_rng(3)
    x, y = 60, 60
    dx, dy = 17, 5
    out = []
    for i in range(n):
        if i % 20 == 0:
            dx = int(rng.integers(15, 26)) * int(rng.choice([-1, 1]))
            dy = int(rng.integers(3, 10)) * int(rng.choice([-1, 1]))
        if i % 20 < 12:
            x += dx
            y += dy
            if not 20 < x < w - 40:
                dx = -dx
                x += 2 * dx
            if not 20 < y < h - 40:
                dy = -dy
                y += 2 * dy
        out.append((x, y))
    return out


def scena(n=200, bg=None):
    """Build `n` timed frames [(t, gray frame)] with the arrow along percorso(); also return the points."""
    sfondo = fondo() if bg is None else bg
    punti = percorso(n)
    return [(round(i / 10.0, 2), disegna(sfondo, x, y)) for i, (x, y) in enumerate(punti)], punti


def campione(t, x=0.5, y=0.5, forma='freccia', **kw):
    """Make a track sample where the cursor was found at (x, y) (frame fractions on a 1280x720 frame).

    Extra keyword arguments override any field of the sample.
    """
    riga = {'t': round(t, 2), 'x': x, 'y': y, 'px': x * 1280, 'py': y * 720, 'ax': x * 1280, 'ay': y * 720,
            'forma': forma, 'conf': 0.9, 'fonte': 'locale', 'cambio': 0, 'segue': False, 'ferma': False}
    riga.update(kw)
    return riga


def persa(t):
    """Make a track sample where the cursor was lost."""
    return {'t': round(t, 2), 'x': None, 'y': None, 'px': None, 'py': None, 'ax': None, 'ay': None,
            'forma': '', 'conf': 0.0, 'fonte': 'persa', 'cambio': 0, 'segue': False, 'ferma': False}


# ---------- reading and geometry
def test_riduzione_usa_inter_linear(monkeypatch):
    """riduci shrinks wide frames to 1280 with INTER_LINEAR only, and leaves 1280-wide frames alone."""
    chiamate = []
    originale = cv2.resize

    def registra(*args, **kwargs):
        """Record the interpolation flag of every cv2.resize call, then run the real one."""
        chiamate.append(kwargs.get('interpolation'))
        return originale(*args, **kwargs)

    monkeypatch.setattr(cv2, 'resize', registra)
    out = cc.riduci(np.zeros((720, 1920), np.uint8))
    assert out.shape[1] == 1280
    assert chiamate == [cv2.INTER_LINEAR]
    # no shrinking at 1280 or below
    assert cc.riduci(np.zeros((720, 1280), np.uint8)).shape[1] == 1280 and chiamate == [cv2.INTER_LINEAR]


def test_macchie_mobili_da_sei_a_trenta_pixel():
    """Only the 8x8 blob counts: the 2x2 one is too small and the 40x40 one too large."""
    prima = np.zeros((100, 100), np.uint8)
    dopo = prima.copy()
    dopo[20:28, 30:38] = 255
    dopo[50:90, 50:90] = 255
    dopo[5:7, 5:7] = 255
    macchie, frazione = cc.macchie_mobili(prima, dopo)
    assert len(macchie) == 1 and abs(macchie[0][0] - 33.5) <= 1 and abs(macchie[0][1] - 23.5) <= 1
    assert frazione > 0


def test_patch_fuori_dal_fotogramma():
    """patch_a returns a square inside the frame and None when it would stick out."""
    immagine = np.zeros((50, 50), np.uint8)
    assert cc.patch_a(immagine, 25, 25, 32).shape == (32, 32) and cc.patch_a(immagine, 5, 25, 32) is None


# ---------- shape
def test_classificazione_forme():
    """Every sprite is classified correctly, in both polarities."""
    for forma in ('freccia', 'testo', 'croce', 'mano'):
        assert cc.classifica_forma(sprite(forma)) == forma
        # also with the negative
        assert cc.classifica_forma(255 - sprite(forma)) == forma


def test_croce_con_vuoto_al_centro_e_punta_al_centro():
    """A crosshair with an empty centre is still a crosshair and its hotspot is the middle of the crop."""
    immagine = np.full((20, 20), 60, np.uint8)       # like the Blender crosshair cursor: four detached arms
    immagine[9:11, 3:8] = 230
    immagine[9:11, 12:17] = 230
    immagine[3:8, 9:11] = 230
    immagine[12:17, 9:11] = 230
    forma, (hx, hy) = cc.descrivi_modello(cv2.GaussianBlur(immagine, (3, 3), 0.6))
    assert forma == 'croce' and abs(hx) <= 0.6 and abs(hy) <= 0.6


def test_punta_della_freccia_e_il_vertice_in_alto_a_sinistra():
    """The arrow hotspot is its vertex in the upper left corner of the sprite."""
    forma, (hx, hy) = cc.descrivi_modello(sprite('freccia'))
    assert forma == 'freccia'
    # centre 12 + offset = vertex (4, 3)
    assert abs((12 + hx) - 4.5) <= 1.0 and abs((12 + hy) - 3.5) <= 1.0


# ---------- learning and tracking
def test_modelli_automatici_dalla_scena_sintetica():
    """The learner finds the arrow template, with plenty of examples, in the synthetic scene."""
    frames, _ = scena()
    modelli = cc.impara_modelli(frames)
    assert 1 <= len(modelli) <= 4
    assert modelli[0]['forma'] == 'freccia' and modelli[0]['n'] >= 20
    assert cc.PAR['patch'] >= modelli[0]['img'].shape[0] >= 16


def test_posizioni_fisse_non_diventano_modelli():
    """A light blinking always at the same spot is not learned as a cursor."""
    sfondo = fondo()
    frames = []
    for i in range(120):
        fotogramma = sfondo.copy()
        if i % 2:
            fotogramma[100:110, 200:210] = 255
        frames.append((i / 10.0, fotogramma))
    assert cc.impara_modelli(frames) == []


def test_cambi_isolati_dell_interfaccia_non_diventano_modelli():
    """Blobs that appear and vanish with no twin (buttons, icons) are not an object that moves."""
    sfondo = fondo()
    rng = np.random.default_rng(5)
    frames = []
    for i in range(160):
        fotogramma = sfondo.copy()
        if i % 2:
            x, y = int(rng.integers(20, 280)), int(rng.integers(20, 140))
            fotogramma[y:y + 12, x:x + 12] = 255
        frames.append((i / 10.0, fotogramma))
    assert cc.impara_modelli(frames) == []


def test_zona_sempre_in_movimento_non_da_modelli():
    """A box that changes all the time (webcam) with twin blobs does not become a cursor template."""
    frames, _ = scena(200)
    rng = np.random.default_rng(11)
    nuovi = []
    for t, fotogramma in frames:
        fotogramma = fotogramma.copy()
        # two light squares jumping inside the box at the bottom right
        for _ in range(2):
            x, y = int(rng.integers(225, 280)), int(rng.integers(130, 152))
            fotogramma[y:y + 8, x:x + 8] = 255
        nuovi.append((t, fotogramma))
    apprendista = cc.Apprendista()
    for _, g in nuovi:
        apprendista.aggiungi(g)
    modelli = apprendista.modelli()
    assert modelli and all(m['forma'] == 'freccia' for m in modelli)
    assert apprendista.occupate is not None
    assert bool(apprendista.occupate[4, 7]) and bool(apprendista.occupate[4, 8])


def test_zona_con_rumore_continuo_diventa_occupata_durante_l_inseguimento():
    """A webcam (low level noise in every frame) appearing halfway through is flagged as an occupied zone."""
    modelli = cc.impara_modelli(scena(160)[0])
    inseguitore = cc.Inseguitore(modelli)
    rng = np.random.default_rng(2)
    sfondo = fondo()
    for i in range(60):
        fotogramma = sfondo.copy()
        rumore = rng.integers(-6, 7, (70, 100))
        fotogramma[100:170, 200:300] = np.clip(fotogramma[100:170, 200:300].astype(np.int16) + rumore,
                                               0, 255).astype(np.uint8)
        inseguitore.passo(i / 10.0, fotogramma)
    assert inseguitore._occupata(250, 140) and not inseguitore._occupata(40, 40)


def test_vista_che_cambia_molto_non_e_zona_di_rumore():
    """A zone changing all the time with big jumps (moving 3D view) is not a webcam: a cursor there counts."""
    modelli = cc.impara_modelli(scena(160)[0])
    inseguitore = cc.Inseguitore(modelli)
    rng = np.random.default_rng(4)
    sfondo = fondo()
    for i in range(60):
        fotogramma = sfondo.copy()
        fotogramma[100:170, 200:300] = rng.integers(0, 255, (70, 100), dtype=np.uint8)
        inseguitore.passo(i / 10.0, fotogramma)
    assert not inseguitore._occupata(250, 140)


def test_soglia_calibrata_sopra_lo_sfondo_del_video():
    """The threshold sits above the level a template reaches on random windows.

    An empty background gives the minimum; a background full of look-alikes gives the maximum.
    """
    modelli = cc.impara_modelli(scena(160)[0])
    vuoti = [fondo(seed=k) for k in range(6)]
    cc.calibra_soglie(vuoti, modelli)
    assert all(m['soglia'] == cc.PAR['soglia_min'] for m in modelli)
    pieni = []
    for k in range(6):
        fotogramma = fondo(seed=k)
        for x in range(10, 300, 40):
            for y in range(10, 150, 40):
                # a grid of arrows identical to the cursor
                fotogramma = disegna(fotogramma, x, y)
        pieni.append(fotogramma)
    cc.calibra_soglie(pieni, modelli)
    assert all(m['soglia'] > 0.85 for m in modelli)


def test_sfumature_non_diventano_modelli():
    """A moving blob that is only a gradient (no sharp silhouette) is not a cursor."""
    sfondo = fondo()
    frames = []
    for i in range(160):
        fotogramma = sfondo.astype(np.float32)
        x = 40 + (i * 37) % 220
        y = 30 + (i * 23) % 110
        yy, xx = np.mgrid[0:180, 0:320]
        # a soft glow jumping around
        fotogramma += 40.0 * np.exp(-(((xx - x) ** 2 + (yy - y) ** 2) / (2 * 6.0 ** 2)))
        frames.append((i / 10.0, np.clip(fotogramma, 0, 255).astype(np.uint8)))
    assert cc.impara_modelli(frames) == []


def test_continuazione_da_fermo_non_scende_sotto_il_picco():
    """The recent peak says what the cursor looks like when present.

    A score collapsing by more than `calo_ferma` below it is not the cursor.
    """
    modelli = cc.impara_modelli(scena(160)[0])
    inseguitore = cc.Inseguitore(modelli)
    inseguitore.recenti = [0.99] * 10
    assert inseguitore._picco() == 0.99
    inseguitore.ultimo = {'ax': 100.0, 'ay': 80.0, 'modello': modelli[0]}
    # a .70 score near the last position: above ncc_ferma (.60) but below peak - drop (.79): discarded
    cc.PAR['calo_ferma'] = 0.20
    assert 0.70 < inseguitore._picco() - cc.PAR['calo_ferma']


def test_inseguimento_trova_la_punta_e_conta_la_copertura():
    """Tracking finds the arrow tip in at least 90% of the frames, with a median error of 2 px or less."""
    frames, punti = scena()
    modelli = cc.impara_modelli(frames[:120])
    tr, dim = cc.insegui(frames, modelli)
    assert dim == (320, 180) and len(tr) == len(frames)
    trovati = [c for c in tr if c['x'] is not None]
    assert len(trovati) / float(len(tr)) >= 0.9
    errori = []
    for i, campione_i in enumerate(tr):
        if campione_i['x'] is not None:
            errori.append(np.hypot(campione_i['px'] - punti[i][0], campione_i['py'] - punti[i][1]))
    assert np.median(errori) <= 2.0 and np.percentile(errori, 90) <= 4.0
    assert {c['fonte'] for c in tr} <= {'locale', 'fermo', 'globale', 'macchia', 'persa'}


def test_macchia_mobile_non_conta_come_trovato():
    """A moving noise box is only a search hint ('macchia'): it never counts as found nor as coverage."""
    frames, _ = scena(80)
    modelli = cc.impara_modelli(frames)
    sfondo = fondo()
    rumore = np.random.default_rng(9).integers(0, 255, (12, 12), dtype=np.uint8)
    altri = []
    for i in range(40):
        # no cursor: only a box of noise that moves
        fotogramma = sfondo.copy()
        fotogramma[40 + i:52 + i, 60 + 2 * i:72 + 2 * i] = rumore
        altri.append((2.0 + i / 10.0, fotogramma))
    cc.PAR['ncc_locale'] = 0.9
    cc.PAR['ncc_globale'] = 0.9          # the box resembles the template only ~0.7: it is not the cursor
    tr, _ = cc.insegui(frames[:20] + altri, modelli)
    coda = tr[20:]
    assert all(c['x'] is None for c in coda)
    # the hint is there, but it is not "found"
    assert any(c['fonte'] == 'macchia' for c in coda)
    assert cc.documento([], tr, 1.0)['stat']['copertura'] <= 20.0 / len(tr) + 1e-9


def test_icona_statica_identica_al_cursore_non_trattiene_l_inseguitore():
    """An icon identical to the cursor but fixed in the background does not win.

    The real cursor is where things move.
    """
    frames, _ = scena(160)
    modelli = cc.impara_modelli(frames)
    sfondo = disegna(fondo(), 250, 20)                       # fixed icon in the background
    nuovi, punti = scena(160, sfondo)
    tr, _ = cc.insegui(nuovi, modelli)
    giusti = 0
    for i in range(10, 160):
        if tr[i]['x'] is not None and np.hypot(tr[i]['px'] - punti[i][0], tr[i]['py'] - punti[i][1]) < 4:
            giusti += 1
    assert giusti >= 0.85 * 150


def test_cursore_fermo_con_ritaglio_uguale():
    """A still cursor with an unchanged crop is reported as 'fermo' when the search fails.

    Once the crop changes it is not 'fermo' any more.
    """
    modelli = cc.impara_modelli(scena(160)[0])
    inseguitore = cc.Inseguitore(modelli)
    frames, _ = scena(40)
    for t, g in frames[:14]:
        inseguitore.passo(t, g)                          # frames 12-19: the cursor is still
    # no template passes the search threshold: the search fails
    cc.PAR['ncc_ferma'] = 1.1
    cc.PAR['ncc_locale'] = 1.1
    cc.PAR['ncc_globale'] = 1.1
    fermi = [inseguitore.passo(t, g) for t, g in frames[14:19]]
    assert fermi[0]['fonte'] == 'fermo' and fermi[0]['conf'] >= 0.5 and fermi[0]['x'] is not None
    # the cursor is gone: the crop changes, it is no longer still
    vuoto = inseguitore.passo(9.0, fondo())
    assert vuoto['fonte'] != 'fermo' and vuoto['x'] is None


def test_fermo_non_vale_su_una_zona_uniforme():
    """An equal crop is not enough: with no template matching there (black screen) it is not 'fermo'."""
    modelli = cc.impara_modelli(scena(160)[0])
    inseguitore = cc.Inseguitore(modelli)
    nero = np.zeros((180, 320), np.uint8)
    inseguitore.ultimo = {'ax': 100.0, 'ay': 80.0, 'modello': modelli[0]}
    inseguitore.crop = cc.patch_a(nero, 100, 80, 32)
    inseguitore.prec = nero
    risultato = inseguitore.passo(1.0, nero.copy())
    assert risultato['x'] is None and risultato['fonte'] != 'fermo'


def test_nome_solo_da_scritte_con_posizione_sotto_il_punto():
    """Only texts with a position under the point name it; the smallest box wins; no match gives ''."""
    class S:
        """Stub of eventi.Scena with five readings."""

        # box containing the point
        voci = [(0.0, 5.0, 'Keep Groups', (0.5, 0.5), [0.45, 0.48, 0.60, 0.52], 0.9),
                # no position: names nothing
                (0.0, 5.0, 'Barra di stato', None, None, 1.0),
                (0.0, 5.0, 'Lontano', (0.1, 0.1), [0.05, 0.08, 0.15, 0.12], 1.0),
                # contains the point but it is the background: the smallest wins
                (0.0, 5.0, 'Grande', (0.5, 0.5), [0.0, 0.0, 1.0, 1.0], 0.9),
                (9.0, 12.0, 'Dopo', (0.5, 0.5), [0.45, 0.48, 0.60, 0.52], 1.0)]         # out of time
    assert cc.nome_sotto_il_punto(S, 2.0, [0.55, 0.50]) == 'Keep Groups'
    assert cc.nome_sotto_il_punto(S, 2.0, [0.80, 0.90]) == 'Grande'

    class V:
        """Stub of eventi.Scena with a single position-less reading."""

        voci = [(0.0, 5.0, 'Barra di stato', None, None, 1.0)]
    assert cc.nome_sotto_il_punto(V, 2.0, [0.5, 0.5]) == ''


def test_stampa_al_massimo_dodici_righe_per_ogni_minuto():
    """At most 12 events per minute are printed; the last 57 s minute allows 11."""
    def ev(i, t):
        """Build a bare click event with id D<i> at time t."""
        return {'id': 'D%d' % i, 't': t, 'durata': 0.1, 'canale': 'cursore', 'tipo': 'clic', 'zona': None,
                'punto': [0.5, 0.5], 'testo': '', 'forza': 0.8, 'stampa': 'nessuna', 'rif': [], 'dati': {}}

    # 78 clicks in 117 s: 40 in the first minute, 38 in the second
    lista = [ev(i, i * 1.5) for i in range(1, 79)]

    class Tutti:
        """Stub of eventi.Scena that names every point."""

        voci = [(0.0, 1e9, 'Voce', (0.5, 0.5), None, 1.0)]
    out, _ = cc.assegna_nomi(lista, Tutti(), None, 117.0)
    assert sum(1 for e in out if e['stampa'] == 'sezione' and e['t'] < 60) == 12
    # the last minute lasts 57 s: 11 rows
    assert sum(1 for e in out if e['stampa'] == 'sezione' and e['t'] >= 60) == 11


# ---------- events
def test_sosta_da_mezzo_secondo_con_i_float_del_10_fps():
    """A dwell of exactly 0.5 s is found despite float error at 10 fps; 0.4 s is not."""
    tr = [campione(i / 10.0) for i in range(6)]          # 0.0 ... 0.5 (0.5 - 0.0 with float rounding errors)
    soste = [e for e in cc.estrai_eventi(tr, 60.0) if e['tipo'] == 'sosta']
    assert len(soste) == 1 and soste[0]['durata'] == 0.5
    breve = [campione(i / 10.0) for i in range(5)]
    assert not [e for e in cc.estrai_eventi(breve, 60.0) if e['tipo'] == 'sosta']


def test_sosta_lunga_limitata_a_otto_secondi():
    """A long dwell is reported with a duration capped at 8 s."""
    tr = [campione(i / 10.0) for i in range(150)]
    soste = [e for e in cc.estrai_eventi(tr, 60.0) if e['tipo'] == 'sosta']
    assert len(soste) == 1 and soste[0]['durata'] == 8.0


def test_clic_fermo_con_cambio_compatto():
    """A cursor that stops and then shows a compact change next to it is a strong click."""
    tr = [campione(i / 10.0, 0.3 + 0.05 * i, 0.5) for i in range(5)]
    tr += [campione(0.5, 0.5, 0.5, ferma=True), campione(0.6, 0.5, 0.5, ferma=True, cambio=40),
           campione(0.7, 0.5, 0.5, ferma=True)]
    clic = [e for e in cc.estrai_eventi(tr, 60.0) if e['tipo'] == 'clic']
    assert len(clic) == 1 and clic[0]['stampa'] == 'sezione' and clic[0]['forza'] >= 0.7


def test_cambio_senza_cursore_fermo_non_e_un_clic():
    """A change while the cursor is moving is not a click."""
    tr = [campione(i / 10.0, 0.3 + 0.05 * i, 0.5, cambio=50) for i in range(8)]       # moving: no click
    assert not [e for e in cc.estrai_eventi(tr, 60.0) if e['tipo'] == 'clic']


def test_clic_per_cambio_di_forma():
    """A shape change between two stable runs at the same place is a click."""
    tr = [campione(i / 10.0, 0.5, 0.5, 'freccia') for i in range(4)]
    tr += [campione(0.4 + i / 10.0, 0.5, 0.5, 'mano') for i in range(4)]
    assert [e for e in cc.estrai_eventi(tr, 60.0) if e['tipo'] == 'clic']


def test_cambio_di_forma_isolato_non_e_un_clic():
    """A one-sample shape flip is unstable and gives no click."""
    # a single sample: unstable
    tr = [campione(i / 10.0, 0.3 + 0.01 * i, 0.5, 'freccia' if i != 3 else 'mano') for i in range(8)]
    assert not [e for e in cc.estrai_eventi(tr, 60.0) if e['tipo'] == 'clic']


def test_trascina_chiuso_anche_a_fine_traccia():
    """A drag still going at the last sample is closed there."""
    # `segue` up to the last sample
    tr = [campione(i / 10.0, 0.3 + 0.02 * i, 0.5, segue=(i >= 2)) for i in range(7)]
    trascine = [e for e in cc.estrai_eventi(tr, 60.0) if e['tipo'] == 'trascina']
    assert len(trascine) == 1 and trascine[0]['t'] == 0.2 and trascine[0]['durata'] >= 0.4
    assert trascine[0]['stampa'] == 'sezione'


def test_forma_testo_o_mano_senza_stampa():
    """Hand and text shapes make unprinted events; the arrow makes none."""
    tr = [campione(i / 10.0, 0.3 + 0.03 * i, 0.5, 'mano') for i in range(8)]
    eventi_forma = [e for e in cc.estrai_eventi(tr, 60.0) if e['tipo'] == 'forma']
    assert eventi_forma and all(e['stampa'] == 'nessuna' for e in eventi_forma)
    frecce = [campione(i / 10.0, 0.3 + 0.03 * i, 0.5, 'freccia') for i in range(8)]
    assert not [e for e in cc.estrai_eventi(frecce, 60.0) if e['tipo'] == 'forma']


def test_eventi_con_buchi_nella_traccia():
    """A lost sample in the middle of the track breaks the dwell."""
    tr = [campione(0.0), persa(0.1), campione(0.2), campione(0.3)]
    assert not [e for e in cc.estrai_eventi(tr, 60.0) if e['tipo'] == 'sosta']


class ScenaFinta:
    """Stands in for eventi.Scena: `voci` = (t0, t1, text, point, zone, strength).

    Here there is one text for each abscissa, valid at any time.
    """

    def __init__(self, nomi):
        """Build the readings from a {x: text} mapping."""
        self.voci = [(0.0, 1e9, testo, (x, 0.5), None, 1.0) for x, testo in nomi.items()]


def test_nomi_solo_clic_trascina_e_soste_con_nome_e_tetto_per_minuto():
    """Only named clicks, drags and dwells survive, OCR fills the gaps, and a per-minute cap applies."""
    def ev(i, tipo, x, forza):
        """Build a bare event of the given type at abscissa x, one every 5 s."""
        return {'id': 'D%d' % i, 't': i * 5.0, 'durata': 1.0, 'canale': 'cursore', 'tipo': tipo, 'zona': None,
                'punto': [x, .5], 'testo': '', 'forza': forza, 'stampa': 'nessuna', 'rif': [], 'dati': {}}

    lista = [ev(1, 'clic', .1, .8), ev(2, 'sosta', .2, .6), ev(3, 'sosta', .3, .6), ev(4, 'clic', .4, .8)]
    scena_ = ScenaFinta({0.1: 'Keep Groups', 0.2: 'Smooth'})
    out, letture = cc.assegna_nomi(lista, scena_, lambda t, p: 'Letto' if round(p[0], 2) == 0.4 else '', 60.0)
    per_id = {e['id']: e for e in out}
    assert per_id['D1']['testo'] == 'Keep Groups' and per_id['D1']['stampa'] == 'sezione'
    assert per_id['D2']['testo'] == 'Smooth' and per_id['D2']['stampa'] == 'sezione'
    # dwell without a name: dropped
    assert 'D3' not in per_id
    # OCR only where the Scena did not know (D3 tried and failed, D4 read)
    assert per_id['D4']['testo'] == 'Letto' and letture == 2
    cc.imposta({'stampa_al_minuto': 1})
    scena4 = ScenaFinta({0.1: 'A', 0.2: 'B', 0.3: 'C', 0.4: 'D'})
    quattro = [ev(1, 'clic', .1, .8), ev(2, 'sosta', .2, .6), ev(3, 'clic', .3, .7), ev(4, 'clic', .4, .75)]
    out, _ = cc.assegna_nomi(quattro, scena4, None, 60.0)
    # cap: at least 3 rows
    assert sum(1 for e in out if e['stampa'] == 'sezione') == 3


# ---------- document and files
def test_documento_valido_con_copertura():
    """The document passes eventi.valida and reports coverage 2/3 and one lost sample."""
    tr = [campione(0), campione(0.1), persa(0.2)]
    doc = eventi.valida(cc.documento([], tr, 1.25))
    assert doc['sezione'] == 'CURSORE' and doc['stat']['copertura'] == 0.6667
    assert doc['stat']['secondi'] == 1.25 and doc['stat']['persa'] == 1


def test_eventi_nel_documento_sono_validi():
    """The events built from a track are accepted by eventi.valida inside the document."""
    tr = [campione(i / 10.0) for i in range(8)] + [campione(0.8, 0.6, 0.5, ferma=True, cambio=30)]
    doc = cc.documento(cc.estrai_eventi(tr, 60.0), tr, 0.5)
    eventi.valida(doc)


def test_scrittura_nella_tmp_del_progetto():
    """eventi_cursore.json and cursore.json are written and read back correctly."""
    cartella = prepara_tmp('scrittura')
    try:
        tr = [campione(0, .5, .5), persa(0.1)]
        eventi.scrivi(os.path.join(cartella, 'eventi_cursore.json'), cc.documento([], tr, .1))
        cc.scrivi_traccia(cartella, tr)
        percorso_eventi = os.path.join(cartella, 'eventi_cursore.json')
        assert json.load(open(percorso_eventi, encoding='utf-8'))['stat']['copertura'] == 0.5
        traccia = json.load(open(os.path.join(cartella, 'cursore.json'), encoding='utf-8'))
        assert traccia[0][:3] == [0, 0.5, 0.5] and traccia[1][1] is None
    finally:
        chiudi_tmp(cartella)


def test_parametro_sconosciuto_rifiutato():
    """imposta raises KeyError on a parameter name it does not know."""
    try:
        cc.imposta({'nonesiste': 1})
        assert False
    except KeyError:
        pass


def test_inseguimento_sintetico_sotto_due_secondi():
    """Tracking 120 synthetic frames takes less than 2 s."""
    frames, _ = scena(120)
    modelli = cc.impara_modelli(frames)
    avvio = time.perf_counter()
    cc.insegui(frames, modelli)
    assert time.perf_counter() - avvio < 2.0
