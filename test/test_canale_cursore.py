# -*- coding: utf-8 -*-
"""Test sintetici del canale cursore: fotogrammi generati con numpy/opencv, niente video veri."""
import json, os, shutil, sys, time
import cv2
import numpy as np

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')); TMP = os.path.abspath(os.path.join(os.path.dirname(__file__), '_tmp')); sys.path.insert(0, SRC)
import canale_cursore as cc
import eventi


def prepara_tmp(nome):
    os.makedirs(TMP, exist_ok=True); p = os.path.join(TMP, nome); shutil.rmtree(p, ignore_errors=True); os.makedirs(p); return p


def chiudi_tmp(p):
    shutil.rmtree(p, ignore_errors=True)
    if os.path.isdir(TMP) and not os.listdir(TMP): os.rmdir(TMP)


def setup_function(_):
    cc.imposta()


# ---------- sprite e scene sintetiche
FRECCIA = np.array([[0, 0], [0, 14], [4, 11], [7, 17], [9, 16], [6, 10], [11, 10]], np.int32)


def sprite(forma='freccia', negativa=False, lato=24):
    im = np.zeros((lato, lato), np.uint8)
    if forma == 'freccia': cv2.fillPoly(im, [FRECCIA + [4, 3]], 255)
    elif forma == 'testo': cv2.line(im, (12, 3), (12, 20), 255, 2); cv2.line(im, (9, 3), (15, 3), 255, 1); cv2.line(im, (9, 20), (15, 20), 255, 1)
    elif forma == 'croce': cv2.line(im, (12, 3), (12, 20), 255, 2); cv2.line(im, (3, 12), (20, 12), 255, 2)
    elif forma == 'mano': im[2:9, 8:12] = 255; im[9:21, 3:19] = 255
    return 255 - im if negativa else im


def fondo(h=180, w=320, seed=1):
    rng = np.random.default_rng(seed)
    grad = np.tile(np.linspace(50, 90, w, dtype=np.float32), (h, 1))
    return np.clip(grad + rng.integers(-6, 7, (h, w)), 0, 255).astype(np.uint8)


def disegna(bg, x, y):
    """Freccia bianca con punta in (x, y) su una copia dello sfondo."""
    f = bg.copy(); f[y - 3:y + 21, x - 4:x + 20] = np.maximum(f[y - 3:y + 21, x - 4:x + 20], sprite('freccia')); return f


def percorso(n=200, w=320, h=180):
    """Punta del cursore a ogni fotogramma: 12 fotogrammi di movimento rapido (15-25 px a passo) e 8 di sosta."""
    rng = np.random.default_rng(3); x, y = 60, 60; dx, dy = 17, 5; out = []
    for i in range(n):
        if i % 20 == 0: dx, dy = int(rng.integers(15, 26)) * int(rng.choice([-1, 1])), int(rng.integers(3, 10)) * int(rng.choice([-1, 1]))
        if i % 20 < 12:
            x += dx; y += dy
            if not 20 < x < w - 40: dx = -dx; x += 2 * dx
            if not 20 < y < h - 40: dy = -dy; y += 2 * dy
        out.append((x, y))
    return out


def scena(n=200, bg=None):
    bg = fondo() if bg is None else bg; punti = percorso(n)
    return [(round(i / 10.0, 2), disegna(bg, x, y)) for i, (x, y) in enumerate(punti)], punti


def campione(t, x=0.5, y=0.5, forma='freccia', **kw):
    c = {'t': round(t, 2), 'x': x, 'y': y, 'px': x * 1280, 'py': y * 720, 'ax': x * 1280, 'ay': y * 720, 'forma': forma, 'conf': 0.9,
         'fonte': 'locale', 'cambio': 0, 'segue': False, 'ferma': False}
    c.update(kw); return c


def persa(t):
    return {'t': round(t, 2), 'x': None, 'y': None, 'px': None, 'py': None, 'ax': None, 'ay': None, 'forma': '', 'conf': 0.0,
            'fonte': 'persa', 'cambio': 0, 'segue': False, 'ferma': False}


# ---------- lettura e geometria
def test_riduzione_usa_inter_linear(monkeypatch):
    calls = []; originale = cv2.resize
    def registra(*a, **k): calls.append(k.get('interpolation')); return originale(*a, **k)
    monkeypatch.setattr(cv2, 'resize', registra)
    out = cc.riduci(np.zeros((720, 1920), np.uint8)); assert out.shape[1] == 1280; assert calls == [cv2.INTER_LINEAR]
    assert cc.riduci(np.zeros((720, 1280), np.uint8)).shape[1] == 1280 and calls == [cv2.INTER_LINEAR]   # niente riduzione sotto 1280


def test_macchie_mobili_da_sei_a_trenta_pixel():
    a = np.zeros((100, 100), np.uint8); b = a.copy(); b[20:28, 30:38] = 255; b[50:90, 50:90] = 255; b[5:7, 5:7] = 255
    m, frazione = cc.macchie_mobili(a, b)
    assert len(m) == 1 and abs(m[0][0] - 33.5) <= 1 and abs(m[0][1] - 23.5) <= 1 and frazione > 0


def test_patch_fuori_dal_fotogramma():
    im = np.zeros((50, 50), np.uint8)
    assert cc.patch_a(im, 25, 25, 32).shape == (32, 32) and cc.patch_a(im, 5, 25, 32) is None


# ---------- forma
def test_classificazione_forme():
    for forma in ('freccia', 'testo', 'croce', 'mano'):
        assert cc.classifica_forma(sprite(forma)) == forma
        assert cc.classifica_forma(255 - sprite(forma)) == forma                      # anche col negativo


def test_croce_con_vuoto_al_centro_e_punta_al_centro():
    im = np.full((20, 20), 60, np.uint8)                                 # come il cursore a mirino di Blender: quattro bracci staccati
    im[9:11, 3:8] = 230; im[9:11, 12:17] = 230; im[3:8, 9:11] = 230; im[12:17, 9:11] = 230
    forma, (hx, hy) = cc.descrivi_modello(cv2.GaussianBlur(im, (3, 3), 0.6))
    assert forma == 'croce' and abs(hx) <= 0.6 and abs(hy) <= 0.6


def test_punta_della_freccia_e_il_vertice_in_alto_a_sinistra():
    forma, (hx, hy) = cc.descrivi_modello(sprite('freccia'))
    assert forma == 'freccia'
    assert abs((12 + hx) - 4.5) <= 1.0 and abs((12 + hy) - 3.5) <= 1.0        # centro 12 + scarto = vertice (4,3)


# ---------- apprendimento e inseguimento
def test_modelli_automatici_dalla_scena_sintetica():
    frames, _ = scena()
    modelli = cc.impara_modelli(frames)
    assert 1 <= len(modelli) <= 4
    assert modelli[0]['forma'] == 'freccia' and modelli[0]['n'] >= 20
    assert cc.PAR['patch'] >= modelli[0]['img'].shape[0] >= 16


def test_posizioni_fisse_non_diventano_modelli():
    bg = fondo(); frames = []
    for i in range(120):                                 # un lampeggiante sempre nello stesso punto
        f = bg.copy()
        if i % 2: f[100:110, 200:210] = 255
        frames.append((i / 10.0, f))
    assert cc.impara_modelli(frames) == []


def test_cambi_isolati_dell_interfaccia_non_diventano_modelli():
    """Macchie che compaiono e spariscono senza gemella (pulsanti, icone) non sono un oggetto che si sposta."""
    bg = fondo(); rng = np.random.default_rng(5); frames = []
    for i in range(160):
        f = bg.copy()
        if i % 2: x, y = int(rng.integers(20, 280)), int(rng.integers(20, 140)); f[y:y + 12, x:x + 12] = 255
        frames.append((i / 10.0, f))
    assert cc.impara_modelli(frames) == []


def test_zona_sempre_in_movimento_non_da_modelli():
    """Un riquadro che cambia di continuo (webcam) con macchie gemelle non diventa un modello di cursore."""
    frames, _ = scena(200); rng = np.random.default_rng(11); nuovi = []
    for t, f in frames:
        f = f.copy()
        for _ in range(2):                                                   # due quadrati chiari che saltano nel riquadro in basso a destra
            x, y = int(rng.integers(225, 280)), int(rng.integers(130, 152)); f[y:y + 8, x:x + 8] = 255
        nuovi.append((t, f))
    a = cc.Apprendista()
    for _, g in nuovi: a.aggiungi(g)
    modelli = a.modelli()
    assert modelli and all(m['forma'] == 'freccia' for m in modelli)
    assert a.occupate is not None and bool(a.occupate[4, 7]) and bool(a.occupate[4, 8])


def test_zona_con_rumore_continuo_diventa_occupata_durante_l_inseguimento():
    """Una webcam (rumore a basso livello in ogni fotogramma) che compare a meta' filmato viene riconosciuta come zona occupata."""
    modelli = cc.impara_modelli(scena(160)[0]); ins = cc.Inseguitore(modelli); rng = np.random.default_rng(2); bg = fondo()
    for i in range(60):
        f = bg.copy(); f[100:170, 200:300] = np.clip(f[100:170, 200:300].astype(np.int16) + rng.integers(-6, 7, (70, 100)), 0, 255).astype(np.uint8)
        ins.passo(i / 10.0, f)
    assert ins._occupata(250, 140) and not ins._occupata(40, 40)


def test_vista_che_cambia_molto_non_e_zona_di_rumore():
    """Una zona che cambia di continuo ma a forti salti (vista 3D in movimento) non e' una webcam: il cursore li' vale."""
    modelli = cc.impara_modelli(scena(160)[0]); ins = cc.Inseguitore(modelli); rng = np.random.default_rng(4); bg = fondo()
    for i in range(60):
        f = bg.copy(); f[100:170, 200:300] = rng.integers(0, 255, (70, 100), dtype=np.uint8)
        ins.passo(i / 10.0, f)
    assert not ins._occupata(250, 140)


def test_soglia_calibrata_sopra_lo_sfondo_del_video():
    """La soglia di un modello sta sopra il livello che raggiunge su finestre a caso: sfondo vuoto -> minimo; sfondo pieno di sosia -> massimo."""
    modelli = cc.impara_modelli(scena(160)[0])
    vuoti = [fondo(seed=k) for k in range(6)]
    cc.calibra_soglie(vuoti, modelli)
    assert all(m['soglia'] == cc.PAR['soglia_min'] for m in modelli)
    pieni = []
    for k in range(6):
        f = fondo(seed=k)
        for x in range(10, 300, 40):
            for y in range(10, 150, 40): f = disegna(f, x, y)               # reticolo di frecce identiche al cursore
        pieni.append(f)
    cc.calibra_soglie(pieni, modelli)
    assert all(m['soglia'] > 0.85 for m in modelli)


def test_sfumature_non_diventano_modelli():
    """Una macchia che si sposta ma e' solo una sfumatura (nessuna sagoma netta) non e' un cursore."""
    bg = fondo(); frames = []
    for i in range(160):
        f = bg.astype(np.float32)
        x = 40 + (i * 37) % 220; y = 30 + (i * 23) % 110
        yy, xx = np.mgrid[0:180, 0:320]; f += 40.0 * np.exp(-(((xx - x) ** 2 + (yy - y) ** 2) / (2 * 6.0 ** 2)))      # bagliore morbido che salta qua e la'
        frames.append((i / 10.0, np.clip(f, 0, 255).astype(np.uint8)))
    assert cc.impara_modelli(frames) == []


def test_continuazione_da_fermo_non_scende_sotto_il_picco():
    """Il picco recente dice com'e' il cursore quando c'e': un punteggio che crolla di oltre `calo_ferma` non e' piu' lui."""
    modelli = cc.impara_modelli(scena(160)[0]); ins = cc.Inseguitore(modelli)
    ins.recenti = [0.99] * 10; assert ins._picco() == 0.99
    ins.ultimo = {'ax': 100.0, 'ay': 80.0, 'modello': modelli[0]}
    # un punteggio .70 vicino all'ultima posizione: sopra ncc_ferma (.60) ma sotto picco - calo (.79): scartato
    cc.PAR['calo_ferma'] = 0.20
    assert 0.70 < ins._picco() - cc.PAR['calo_ferma']


def test_inseguimento_trova_la_punta_e_conta_la_copertura():
    frames, punti = scena()
    modelli = cc.impara_modelli(frames[:120])
    tr, dim = cc.insegui(frames, modelli)
    assert dim == (320, 180) and len(tr) == len(frames)
    trovati = [c for c in tr if c['x'] is not None]
    assert len(trovati) / float(len(tr)) >= 0.9
    errori = [np.hypot(c['px'] - punti[i][0], c['py'] - punti[i][1]) for i, c in enumerate(tr) if c['x'] is not None]
    assert np.median(errori) <= 2.0 and np.percentile(errori, 90) <= 4.0
    assert {c['fonte'] for c in tr} <= {'locale', 'fermo', 'globale', 'macchia', 'persa'}


def test_macchia_mobile_non_conta_come_trovato():
    frames, _ = scena(80)
    modelli = cc.impara_modelli(frames)
    bg = fondo(); rumore = np.random.default_rng(9).integers(0, 255, (12, 12), dtype=np.uint8); altri = []
    for i in range(40):                                  # niente cursore: solo un riquadro di rumore che si muove
        f = bg.copy(); f[40 + i:52 + i, 60 + 2 * i:72 + 2 * i] = rumore; altri.append((2.0 + i / 10.0, f))
    cc.PAR['ncc_locale'] = 0.9; cc.PAR['ncc_globale'] = 0.9          # il riquadro somiglia al modello solo ~0,7: non e' il cursore
    tr, _ = cc.insegui(frames[:20] + altri, modelli)
    coda = tr[20:]
    assert all(c['x'] is None for c in coda)
    assert any(c['fonte'] == 'macchia' for c in coda)            # l'ipotesi c'e', ma non e' "trovato"
    assert cc.documento([], tr, 1.0)['stat']['copertura'] <= 20.0 / len(tr) + 1e-9


def test_icona_statica_identica_al_cursore_non_trattiene_l_inseguitore():
    """Un'icona identica al cursore ma ferma nello sfondo non vince: il cursore vero e' dove qualcosa si muove."""
    frames, punti = scena(160)
    modelli = cc.impara_modelli(frames)
    bg = disegna(fondo(), 250, 20)                       # icona fissa nello sfondo
    nuovi, punti = scena(160, bg)
    tr, _ = cc.insegui(nuovi, modelli)
    giusti = sum(1 for i in range(10, 160) if tr[i]['x'] is not None and np.hypot(tr[i]['px'] - punti[i][0], tr[i]['py'] - punti[i][1]) < 4)
    assert giusti >= 0.85 * 150


def test_cursore_fermo_con_ritaglio_uguale():
    modelli = cc.impara_modelli(scena(160)[0]); ins = cc.Inseguitore(modelli); frames, punti = scena(40)
    for t, g in frames[:14]: ins.passo(t, g)                          # fotogrammi 12-19: il cursore e' fermo
    cc.PAR['ncc_ferma'] = 1.1; cc.PAR['ncc_locale'] = 1.1; cc.PAR['ncc_globale'] = 1.1   # nessun modello supera la soglia di ricerca: la ricerca fallisce
    fermi = [ins.passo(t, g) for t, g in frames[14:19]]
    assert fermi[0]['fonte'] == 'fermo' and fermi[0]['conf'] >= 0.5 and fermi[0]['x'] is not None
    vuoto = ins.passo(9.0, fondo())                                   # il cursore e' sparito: il ritaglio cambia, non e' piu' fermo
    assert vuoto['fonte'] != 'fermo' and vuoto['x'] is None


def test_fermo_non_vale_su_una_zona_uniforme():
    """Il ritaglio uguale non basta: se nessun modello riconosce il cursore li' (schermo nero) non e' fermo."""
    modelli = cc.impara_modelli(scena(160)[0]); ins = cc.Inseguitore(modelli)
    nero = np.zeros((180, 320), np.uint8)
    ins.ultimo = {'ax': 100.0, 'ay': 80.0, 'modello': modelli[0]}; ins.crop = cc.patch_a(nero, 100, 80, 32); ins.prec = nero
    r = ins.passo(1.0, nero.copy())
    assert r['x'] is None and r['fonte'] != 'fermo'


def test_nome_solo_da_scritte_con_posizione_sotto_il_punto():
    class S:
        voci = [(0.0, 5.0, 'Keep Groups', (0.5, 0.5), [0.45, 0.48, 0.60, 0.52], 0.9),         # riquadro che contiene il punto
                (0.0, 5.0, 'Barra di stato', None, None, 1.0),                                  # senza posizione: non nomina
                (0.0, 5.0, 'Lontano', (0.1, 0.1), [0.05, 0.08, 0.15, 0.12], 1.0),
                (0.0, 5.0, 'Grande', (0.5, 0.5), [0.0, 0.0, 1.0, 1.0], 0.9),                    # contiene il punto ma e' lo sfondo: vince il piu' piccolo
                (9.0, 12.0, 'Dopo', (0.5, 0.5), [0.45, 0.48, 0.60, 0.52], 1.0)]                 # fuori tempo
    assert cc.nome_sotto_il_punto(S, 2.0, [0.55, 0.50]) == 'Keep Groups'
    assert cc.nome_sotto_il_punto(S, 2.0, [0.80, 0.90]) == 'Grande'
    class V: voci = [(0.0, 5.0, 'Barra di stato', None, None, 1.0)]
    assert cc.nome_sotto_il_punto(V, 2.0, [0.5, 0.5]) == ''


def test_stampa_al_massimo_dodici_righe_per_ogni_minuto():
    def ev(i, t): return {'id': 'D%d' % i, 't': t, 'durata': 0.1, 'canale': 'cursore', 'tipo': 'clic', 'zona': None, 'punto': [0.5, 0.5],
                          'testo': '', 'forza': 0.8, 'stampa': 'nessuna', 'rif': [], 'dati': {}}
    lista = [ev(i, i * 1.5) for i in range(1, 79)]                    # 78 clic in 117 s: 40 nel primo minuto, 38 nel secondo
    class Tutti:
        voci = [(0.0, 1e9, 'Voce', (0.5, 0.5), None, 1.0)]
    out, _ = cc.assegna_nomi(lista, Tutti(), None, 117.0)
    assert sum(1 for e in out if e['stampa'] == 'sezione' and e['t'] < 60) == 12
    assert sum(1 for e in out if e['stampa'] == 'sezione' and e['t'] >= 60) == 11       # l'ultimo minuto dura 57 s: 11 righe


# ---------- eventi
def test_sosta_da_mezzo_secondo_con_i_float_del_10_fps():
    tr = [campione(i / 10.0) for i in range(6)]          # 0.0 ... 0.5 (0.5 - 0.0 con errori di virgola mobile)
    soste = [e for e in cc.estrai_eventi(tr, 60.0) if e['tipo'] == 'sosta']
    assert len(soste) == 1 and soste[0]['durata'] == 0.5
    assert not [e for e in cc.estrai_eventi([campione(i / 10.0) for i in range(5)], 60.0) if e['tipo'] == 'sosta']


def test_sosta_lunga_limitata_a_otto_secondi():
    tr = [campione(i / 10.0) for i in range(150)]
    s = [e for e in cc.estrai_eventi(tr, 60.0) if e['tipo'] == 'sosta']
    assert len(s) == 1 and s[0]['durata'] == 8.0


def test_clic_fermo_con_cambio_compatto():
    tr = [campione(i / 10.0, 0.3 + 0.05 * i, 0.5) for i in range(5)]
    tr += [campione(0.5, 0.5, 0.5, ferma=True), campione(0.6, 0.5, 0.5, ferma=True, cambio=40), campione(0.7, 0.5, 0.5, ferma=True)]
    clic = [e for e in cc.estrai_eventi(tr, 60.0) if e['tipo'] == 'clic']
    assert len(clic) == 1 and clic[0]['stampa'] == 'sezione' and clic[0]['forza'] >= 0.7


def test_cambio_senza_cursore_fermo_non_e_un_clic():
    tr = [campione(i / 10.0, 0.3 + 0.05 * i, 0.5, cambio=50) for i in range(8)]       # in movimento: niente clic
    assert not [e for e in cc.estrai_eventi(tr, 60.0) if e['tipo'] == 'clic']


def test_clic_per_cambio_di_forma():
    tr = [campione(i / 10.0, 0.5, 0.5, 'freccia') for i in range(4)] + [campione(0.4 + i / 10.0, 0.5, 0.5, 'mano') for i in range(4)]
    assert [e for e in cc.estrai_eventi(tr, 60.0) if e['tipo'] == 'clic']


def test_cambio_di_forma_isolato_non_e_un_clic():
    tr = [campione(i / 10.0, 0.3 + 0.01 * i, 0.5, 'freccia' if i != 3 else 'mano') for i in range(8)]   # un solo campione: instabile
    assert not [e for e in cc.estrai_eventi(tr, 60.0) if e['tipo'] == 'clic']


def test_trascina_chiuso_anche_a_fine_traccia():
    tr = [campione(i / 10.0, 0.3 + 0.02 * i, 0.5, segue=(i >= 2)) for i in range(7)]       # segue fino all'ultimo campione
    t = [e for e in cc.estrai_eventi(tr, 60.0) if e['tipo'] == 'trascina']
    assert len(t) == 1 and t[0]['t'] == 0.2 and t[0]['durata'] >= 0.4 and t[0]['stampa'] == 'sezione'


def test_forma_testo_o_mano_senza_stampa():
    tr = [campione(i / 10.0, 0.3 + 0.03 * i, 0.5, 'mano') for i in range(8)]
    ev = [e for e in cc.estrai_eventi(tr, 60.0) if e['tipo'] == 'forma']
    assert ev and all(e['stampa'] == 'nessuna' for e in ev)
    assert not [e for e in cc.estrai_eventi([campione(i / 10.0, 0.3 + 0.03 * i, 0.5, 'freccia') for i in range(8)], 60.0) if e['tipo'] == 'forma']


def test_eventi_con_buchi_nella_traccia():
    tr = [campione(0.0), persa(0.1), campione(0.2), campione(0.3)]
    assert not [e for e in cc.estrai_eventi(tr, 60.0) if e['tipo'] == 'sosta']


class ScenaFinta:
    """Come eventi.Scena: `voci` = (t0, t1, testo, punto, zona, forza); qui un testo per ogni ascissa, valido sempre."""
    def __init__(self, nomi): self.voci = [(0.0, 1e9, testo, (x, 0.5), None, 1.0) for x, testo in nomi.items()]


def test_nomi_solo_clic_trascina_e_soste_con_nome_e_tetto_per_minuto():
    def ev(i, tipo, x, forza): return {'id': 'D%d' % i, 't': i * 5.0, 'durata': 1.0, 'canale': 'cursore', 'tipo': tipo, 'zona': None, 'punto': [x, .5],
                                      'testo': '', 'forza': forza, 'stampa': 'nessuna', 'rif': [], 'dati': {}}
    lista = [ev(1, 'clic', .1, .8), ev(2, 'sosta', .2, .6), ev(3, 'sosta', .3, .6), ev(4, 'clic', .4, .8)]
    scena_ = ScenaFinta({0.1: 'Keep Groups', 0.2: 'Smooth'})
    out, letture = cc.assegna_nomi(lista, scena_, lambda t, p: 'Letto' if round(p[0], 2) == 0.4 else '', 60.0)
    per_id = {e['id']: e for e in out}
    assert per_id['D1']['testo'] == 'Keep Groups' and per_id['D1']['stampa'] == 'sezione'
    assert per_id['D2']['testo'] == 'Smooth' and per_id['D2']['stampa'] == 'sezione'
    assert 'D3' not in per_id                                  # sosta senza nome: scartata
    assert per_id['D4']['testo'] == 'Letto' and letture == 2   # OCR solo dove la Scena non sapeva (D3 ha provato e fallito, D4 ha letto)
    cc.imposta({'stampa_al_minuto': 1})
    scena4 = ScenaFinta({0.1: 'A', 0.2: 'B', 0.3: 'C', 0.4: 'D'})
    out, _ = cc.assegna_nomi([ev(1, 'clic', .1, .8), ev(2, 'sosta', .2, .6), ev(3, 'clic', .3, .7), ev(4, 'clic', .4, .75)], scena4, None, 60.0)
    assert sum(1 for e in out if e['stampa'] == 'sezione') == 3      # tetto: al minimo 3 righe


# ---------- documento e file
def test_documento_valido_con_copertura():
    tr = [campione(0), campione(0.1), persa(0.2)]; d = eventi.valida(cc.documento([], tr, 1.25))
    assert d['sezione'] == 'CURSORE' and d['stat']['copertura'] == 0.6667 and d['stat']['secondi'] == 1.25 and d['stat']['persa'] == 1


def test_eventi_nel_documento_sono_validi():
    tr = [campione(i / 10.0) for i in range(8)] + [campione(0.8, 0.6, 0.5, ferma=True, cambio=30)]
    doc = cc.documento(cc.estrai_eventi(tr, 60.0), tr, 0.5); eventi.valida(doc)


def test_scrittura_nella_tmp_del_progetto():
    p = prepara_tmp('scrittura')
    try:
        tr = [campione(0, .5, .5), persa(0.1)]
        eventi.scrivi(os.path.join(p, 'eventi_cursore.json'), cc.documento([], tr, .1)); cc.scrivi_traccia(p, tr)
        assert json.load(open(os.path.join(p, 'eventi_cursore.json'), encoding='utf-8'))['stat']['copertura'] == 0.5
        traccia = json.load(open(os.path.join(p, 'cursore.json'), encoding='utf-8'))
        assert traccia[0][:3] == [0, 0.5, 0.5] and traccia[1][1] is None
    finally: chiudi_tmp(p)


def test_parametro_sconosciuto_rifiutato():
    try: cc.imposta({'nonesiste': 1}); assert False
    except KeyError: pass


def test_inseguimento_sintetico_sotto_due_secondi():
    frames, _ = scena(120); modelli = cc.impara_modelli(frames)
    t0 = time.perf_counter(); cc.insegui(frames, modelli); assert time.perf_counter() - t0 < 2.0
