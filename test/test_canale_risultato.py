# -*- coding: utf-8 -*-
"""Tests for canale_risultato.py (the RESULT channel) using synthetic frames and a fake Ollama.

Writes only inside test/_tmp/canale_risultato, which is created and removed around each test.
"""
import json
import os
import shutil
import sys

import cv2
import numpy as np

QUI = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(QUI), "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import canale_risultato
import eventi

TMP = os.path.join(QUI, "_tmp", "canale_risultato")


def setup_function():
    """Start every test with an empty temporary folder."""
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP, exist_ok=True)


def teardown_function():
    """Remove the temporary folder after every test."""
    shutil.rmtree(TMP, ignore_errors=True)


def _frame(contenuto=False, rumore=False):
    """Synthetic 320x240 screen: a grey side panel, optional central content and optional noise."""
    img = np.full((240, 320, 3), 35, np.uint8)
    cv2.rectangle(img, (0, 0), (50, 239), (80, 80, 80), -1)
    if contenuto:
        cv2.rectangle(img, (120, 80), (220, 170), (220, 220, 220), -1)
        for x in range(130, 220, 15):
            cv2.line(img, (x, 85), (x, 165), (20, 20, 20), 2)
    if rumore:
        cv2.rectangle(img, (245, 25), (305, 85), (255, 255, 255), -1)
    return img


def _mov(fasi, cur=None):
    """Minimal mov.json content for a 320x240 video with the given phases and cursor track."""
    return {"W": 320, "H": 240, "cur": cur or [], "fasi": fasi}


def _ocr3():
    """ocr3.json content where the side-panel label is read in every reading (static text)."""
    voce = ["Pannello", 0.99, 0, 0, 50, 239]
    return {
        "W": 320,
        "H": 240,
        "letture": [[0, 0, 0, [voce]], [1, 0, 0, [voce]], [2, 0, 0, [voce]]],
    }


def test_sceglie_fine_passo_con_fermo_prima_e_dopo():
    """A step with still screens on both sides and new content yields one candidate."""
    frames = {1.0: _frame(False), 2.0: _frame(False), 4.0: _frame(True), 5.0: _frame(True)}

    def leggi(_video, t):
        return frames[round(t, 1)].copy()

    fasi = [{"t0": 2.0, "t1": 4.0, "mods": 1, "ist": 0, "bbox": [100, 60, 230, 180]}]
    ottenuti = canale_risultato.candidati("finto.mp4", _mov(fasi), _ocr3(), leggi)
    assert len(ottenuti) == 1
    assert ottenuti[0]["t"] == 4.0
    assert ottenuti[0]["area"] > 0
    assert ottenuti[0]["scatola"][0] >= 100


def test_limite_tre_coppie_al_minuto_tiene_le_aree_maggiori():
    """Only the three largest areas of each minute survive, sorted by time."""
    coppie = [
        {"t": 5.0, "area": 10, "rif": "F1"},
        {"t": 10.0, "area": 40, "rif": "F2"},
        {"t": 20.0, "area": 20, "rif": "F3"},
        {"t": 30.0, "area": 30, "rif": "F4"},
        {"t": 65.0, "area": 5, "rif": "F5"},
    ]
    ottenuti = canale_risultato.limita(coppie)
    assert [coppia["rif"] for coppia in ottenuti] == ["F2", "F3", "F4", "F5"]


def test_immagini_separate_hanno_lato_lungo_1024_e_ritaglio_pannello():
    """Before and after images are scaled to 1024 px; a cursor adds a third panel crop."""
    coppia = {"prima": _frame(False), "dopo": _frame(True), "scatola": [100, 60, 240, 190],
              "cursore": [20, 120]}
    immagini = canale_risultato.immagini_separate(coppia)
    assert len(immagini) == 3
    assert max(immagini[0].shape[:2]) == 1024
    assert max(immagini[1].shape[:2]) == 1024
    assert immagini[0] is not immagini[1]
    assert immagini[2].shape[1] > 0
    assert immagini[2].shape[0] > 0


def test_senza_cursore_invia_solo_prima_e_dopo():
    """Without a cursor position only the before and after images are produced."""
    coppia = {"prima": _frame(False), "dopo": _frame(True), "scatola": [100, 60, 240, 190],
              "cursore": None}
    assert len(canale_risultato.immagini_separate(coppia)) == 2


def test_candidato_conserva_il_cursore_piu_vicino():
    """The candidate keeps the cursor sample nearest to the end of the step."""
    frames = {1.0: _frame(False), 2.0: _frame(False), 4.0: _frame(True), 5.0: _frame(True)}

    def leggi(_video, t):
        return frames[round(t, 1)].copy()

    fasi = [{"t0": 2.0, "t1": 4.0, "mods": 1, "ist": 0, "bbox": [100, 60, 230, 180]}]
    movimento = _mov(fasi, [[3.0, 20, 30], [4.1, 40, 50]])
    ottenuti = canale_risultato.candidati("finto.mp4", movimento, _ocr3(), leggi)
    assert ottenuti[0]["cursore"] == [40.0, 50.0]


def test_ollama_simulato_riceve_json_chiuso_e_modello_configurato():
    """With a fake model: the right model and 3 images are used, and unsure answers are dropped."""
    base = {
        "t": 85.0, "area": 100, "rif": "F7", "prima": _frame(False), "dopo": _frame(True),
        "scatola": [120, 80, 221, 171], "forza": 0.8, "dimensioni": [320, 240], "cursore": [15, 100],
    }
    risposta_sicura = {
        "pulsante_acceso": {"nome": "Keep Groups", "dove": "destra"},
        "valori_letti": [{"nome": "AdaptiveSize", "valore": "0"}],
        "superficie": {"prima": "faccettata", "dopo": "liscia"},
        "griglia": "piu_fitta",
        "gruppi_colore": None,
        "forma_cambiata": None,
        "certezza": "alta",
    }
    risposta_incerta = {
        "pulsante_acceso": None,
        "valori_letti": [],
        "superficie": None,
        "griglia": "uguale",
        "gruppi_colore": None,
        "forma_cambiata": None,
        "certezza": "bassa",
    }
    risposte = iter([json.dumps(risposta_sicura), json.dumps(risposta_incerta)])

    def chiedi(immagini, modello, server):
        assert len(immagini) == 3
        assert modello == "modello-prova"
        assert server == "http://127.0.0.1:11434"
        return next(risposte)

    calcolati, errori = canale_risultato.interroga(
        [dict(base, rif="F7"), dict(base, rif="F8")],
        modello="modello-prova",
        server="http://127.0.0.1:11434",
        chiedi=chiedi,
    )
    assert errori == []
    assert len(calcolati) == 1
    assert calcolati[0]["testo"] == (
        'pannello destro: acceso "Keep Groups"; AdaptiveSize = 0; superficie faccettata -> liscia; '
        'griglia piu\' fitta'
    )
    assert len(calcolati[0]["testo"].split()) <= 30
    assert calcolati[0]["t"] == 85.0
    assert calcolati[0]["stampa"] == "riga"
    assert eventi.valida(canale_risultato.documento(calcolati, 0.2, 2))["sezione"] == "RISULTATO"


def test_risposta_rispetta_ordine_e_limite_trenta_parole():
    """The description follows the fixed field order, takes 3 values at most and 30 words at most."""
    testo = json.dumps({
        "pulsante_acceso": {"nome": "Keep Groups", "dove": "destra"},
        "valori_letti": [
            {"nome": "AdaptiveSize", "valore": "0"},
            {"nome": "Density", "valore": "12"},
            {"nome": "Mode", "valore": "Fine"},
            {"nome": "Ignorato", "valore": "99"},
        ],
        "superficie": {"prima": "a griglia", "dopo": "liscia"},
        "griglia": "piu_fitta",
        "gruppi_colore": {"numero": 3, "colori": ["rosso", "verde", "giallo"]},
        "forma_cambiata": "il cubo diventa una sfera regolare con bordi allineati",
        "certezza": "media",
    })
    esito = canale_risultato.risposta(testo)
    assert len(esito["cosa"].split()) == 30
    assert esito["cosa"].startswith('pannello destro: acceso "Keep Groups"; AdaptiveSize = 0')
    assert "Ignorato" not in esito["cosa"]
    assert esito["cosa"].find("superficie") < esito["cosa"].find("griglia")
    assert esito["cosa"].find("griglia") < esito["cosa"].find("gruppi")


def test_configurazione_modello_risultato_e_default():
    """The result model is read from 'modello_risultato' and falls back to qwen3-vl:8b."""
    con_modello = {"ollama": {
        "indirizzo": "http://localhost:11434/",
        "modello": "modello-generale",
        "modello_risultato": "modello-risultato",
    }}
    assert canale_risultato.configurazione_ollama(con_modello) == (
        "modello-risultato", "http://localhost:11434")
    senza_modello = {"ollama": {"indirizzo": "http://localhost:11434"}}
    assert canale_risultato.configurazione_ollama(senza_modello) == (
        "qwen3-vl:8b", "http://localhost:11434")


def test_scarta_schermo_che_non_resta_fermo_un_secondo():
    """A screen that keeps changing around the step end produces no candidate."""
    stabile = _frame(True)
    mosso = _frame(True, rumore=True)
    maschera = canale_risultato._maschera_centrale(320, 240, _ocr3())
    assert canale_risultato._differenza(stabile, mosso, maschera) > canale_risultato.SOGLIA_FERMO
    frames = {1.0: _frame(False), 2.0: _frame(False), 4.0: stabile, 5.0: mosso}

    def leggi(_video, t):
        return frames[round(t, 1)].copy()

    fasi = [{"t0": 2.0, "t1": 4.0, "mods": 1, "ist": 0, "bbox": None}]
    assert canale_risultato.candidati("finto.mp4", _mov(fasi), _ocr3(), leggi) == []


def test_errore_ollama_resta_locale_e_main_esce_zero():
    """A failing model is reported in the output file and main still exits with code 0."""
    coppia = {
        "t": 10.0, "area": 100, "rif": "F1", "prima": _frame(False), "dopo": _frame(True),
        "scatola": [120, 80, 221, 171], "forza": 0.8, "dimensioni": [320, 240], "cursore": None,
    }

    def chiedi(*_args, **_kwargs):
        raise ConnectionError("offline")

    calcolati, errori = canale_risultato.interroga([coppia], chiedi=chiedi)
    assert calcolati == []
    assert errori == ["ConnectionError"]
    originali = (canale_risultato.candidati, canale_risultato.interroga, canale_risultato.comune.config)
    try:
        canale_risultato.candidati = lambda _video, _movimento, _ocr: [coppia]
        canale_risultato.interroga = lambda _coppie, modello, server: ([], ["ConnectionError"])
        canale_risultato.comune.config = lambda: {
            "ollama": {"indirizzo": "http://127.0.0.1:11434", "modello_risultato": "qwen3-vl:8b"}}
        with open(os.path.join(TMP, "mov.json"), "w", encoding="utf-8") as file:
            json.dump({"W": 320, "H": 240, "cur": [], "fasi": []}, file)
        with open(os.path.join(TMP, "ocr3.json"), "w", encoding="utf-8") as file:
            json.dump({}, file)
        codice = canale_risultato.main(["canale_risultato.py", "finto.mp4", TMP])
    finally:
        canale_risultato.candidati, canale_risultato.interroga, canale_risultato.comune.config = originali
    with open(os.path.join(TMP, "eventi_risultato.json"), encoding="utf-8") as file:
        scritto = json.load(file)
    assert codice == 0
    assert scritto["eventi"] == []
    assert scritto["stat"]["motivo"] == "Ollama non raggiungibile o risposta non valida"
