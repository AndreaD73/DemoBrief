# -*- coding: utf-8 -*-
"""Tests of the interface-state channel (src/canale_stato.py).

Everything runs on synthetic images and hand-made readings: no video and no real OCR engine.
"""
import collections
import json
import os
import sys
import tempfile

import cv2
import numpy as np

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, SRC)
import canale_stato
import eventi


def voce(testo, x0, y0, x1, y1, lab):
    """Build a reading of the ocr3 kind: text, box (pixels and normalised by 100) and background Lab."""
    return {"testo": testo, "forza": 0.99, "zona": [x0, y0, x1, y1],
            "zona_norm": [x0/100.0, y0/100.0, x1/100.0, y1/100.0], "lab": np.asarray(lab, dtype=float)}


def gruppo(nome="Beta"):
    """Three aligned items in a column; the middle one (`nome`) has a more coloured background."""
    return [voce("Alfa", 10, 10, 30, 20, [50, 128, 128]),
            voce(nome, 10, 30, 30, 40, [80, 160, 128]),
            voce("Gamma", 10, 50, 30, 60, [51, 128, 128])]


def test_colore_anello_usa_il_fondo_esterno():
    """The ring colour is taken outside the box: a red box on green gives the green Lab value."""
    immagine = np.full((30, 30, 3), (0, 255, 0), dtype=np.uint8)
    immagine[10:20, 10:20] = (0, 0, 255)
    atteso = cv2.cvtColor(np.uint8([[[0, 255, 0]]]), cv2.COLOR_BGR2LAB)[0, 0]
    ottenuto = canale_stato.colore_anello(immagine, [10, 10, 20, 20])
    assert np.max(np.abs(ottenuto-atteso)) == 0


def test_scelto_trova_l_unico_fondo_diverso():
    """In a group where one item has a different background, that item is the chosen one."""
    assert canale_stato.scelto(gruppo())["testo"] == "Beta"


def test_scelto_rifiuta_un_gruppo_uniforme():
    """When all the items share the same background, nothing is chosen."""
    voci = gruppo()
    for elemento in voci:
        elemento["lab"] = np.asarray([50, 128, 128], dtype=float)
    assert canale_stato.scelto(voci) is None


def test_calcola_emette_scelto_dopo_un_secondo_fuori_dal_cursore():
    """A chosen item that is still chosen 1 s later, with the cursor away and spoken aloud, is reported."""
    dati = {"parole": [[0.0, 999.0, "beta"]],
            "letture": [{"t": 2.0, "voci": gruppo()}, {"t": 3.1, "voci": gruppo()}],
            "cambi": [], "cursori": []}
    ottenuti = canale_stato.calcola(dati)
    assert len(ottenuti) == 1
    assert ottenuti[0]["tipo"] == "scelto"
    assert ottenuti[0]["testo"] == "Beta"
    assert ottenuti[0]["dati"]["dove"] == "pannello a sinistra"


def test_calcola_scarta_la_scelta_col_cursore_dentro():
    """A highlighted item under the cursor is only hovering, so no event comes out."""
    dati = {"letture": [{"t": 2.0, "voci": gruppo()}, {"t": 3.1, "voci": gruppo()}],
            "cambi": [], "cursori": [[2.0, 0.2, 0.35, "", 1.0], [3.1, 0.2, 0.35, "", 1.0]]}
    assert canale_stato.calcola(dati) == []


def test_cambio_e_documento_rispettano_lo_schema_unico():
    """A change becomes a schema-valid event, and the document written to disk has the STATO section."""
    dati = {"parole": [[0.0, 999.0, "voce"]], "letture": [], "cursori": [],
            "cambi": [{"t": 4.5, "testo": "Alfa Beta", "zona_norm": [0.7, 0.4, 0.9, 0.5], "delta": 24.0,
                       "stato": "acceso", "rif": ["D2"]}]}
    calcolati = canale_stato.calcola(dati)
    doc = canale_stato.documento(calcolati, 0.25, 2)
    assert eventi.valida(doc)["eventi"][0]["dati"]["stato"] == "acceso"
    with tempfile.TemporaryDirectory() as cartella:
        percorso = os.path.join(cartella, "eventi_stato.json")
        eventi.scrivi(percorso, doc)
        with open(percorso, encoding="utf-8") as f:
            scritto = json.load(f)
        assert scritto["sezione"] == "STATO"


# ---- E2: compact and persistent changes (synthetic images, no video)
def _sfondo():
    """A grey 640x360 frame with a dark button and a thin-stroke 'text' on it."""
    frame = np.full((360, 640, 3), 90, np.uint8)
    frame[40:56, 20:60] = (45, 45, 45)                                  # switched-off button
    for x in range(24, 56, 4):
        frame[44:52, x:x+1] = (230, 230, 230)                           # "text" made of thin strokes
    return frame


def _filmato(n, cambia):
    """Yield `n` frames of the background, each one modified by `cambia(index, frame)`."""
    for i in range(n):
        frame = _sfondo()
        cambia(i, frame)
        yield frame


def _corri(n, cambia, soglie=None):
    """Feed `n` synthetic frames to a Rivelatore; return it together with the candidates it finds."""
    rivelatore = canale_stato.Rivelatore(640, 360, soglie)
    for frame in _filmato(n, cambia):
        rivelatore.aggiungi(frame)
    return rivelatore, rivelatore.fine()


def test_rivelatore_vede_un_controllo_che_si_accende_e_resta():
    """A button that turns on at frame 4 and stays on is found once, with direction 'acceso'."""
    def cambia(i, frame):
        if i >= 4:
            frame[40:56, 20:60] = (0, 140, 255)
            for x in range(24, 56, 4):
                frame[44:52, x:x+1] = (230, 230, 230)

    _, candidati = _corri(16, cambia)
    assert len(candidati) == 1
    assert candidati[0]["scatola"][0] >= 19 and candidati[0]["scatola"][2] <= 62
    assert _verso(candidati[0]) == "acceso"
    assert abs(candidati[0]["t"] - 2.0) < 0.6


def test_rivelatore_vede_un_controllo_che_si_spegne():
    """A button that is lit for the first 6 frames and then turns off is found once, as 'spento'."""
    def cambia(i, frame):
        if i < 6:
            frame[40:56, 20:60] = (0, 140, 255)
            for x in range(24, 56, 4):
                frame[44:52, x:x+1] = (230, 230, 230)

    _, candidati = _corri(16, cambia)
    assert len(candidati) == 1
    assert _verso(candidati[0]) == "spento"


def test_rivelatore_ignora_lampo_cursore_e_comparsa():
    """A one-frame flash and a white cursor arrow are dropped; the tooltip that appears is kept."""
    def cambia(i, frame):
        if i == 5:
            frame[40:56, 20:60] = (0, 140, 255)                         # a single frame: it does not persist
        if i >= 3:
            frame[120:132, 200:208] = (255, 255, 255)                   # white arrow that stays: no outline
        if i >= 6:
            frame[100:130, 100:160] = (30, 30, 30)                      # tooltip that appeared
            frame[110:114, 105:150] = (240, 240, 240)

    _, candidati = _corri(16, cambia)
    # Flash and arrow are out; the appearance is stopped later by nomina() (it was not there before).
    assert [candidato["scatola"][0] // 10 for candidato in candidati] == [10]


def test_rivelatore_scarta_la_vista_che_cambia_sempre():
    """A zone that flips colour at every frame (a live view) is never a control."""
    def cambia(i, frame):
        frame[100:130, 100:160] = (30 + 40*(i % 2), 90, 200 - 60*(i % 2))
        frame[110:114, 105:150] = (240, 240, 240)

    _, candidati = _corri(20, cambia)
    assert candidati == []


def _verso(candidato):
    """Direction ('acceso'/'spento') that misura_stato reads on a candidate, or None if it does not change."""
    ox, oy = candidato["origine"]
    x0, y0, x1, y1 = candidato["scatola"]
    stato = canale_stato.misura_stato(candidato["prima"], candidato["dopo"], [x0-ox, y0-oy, x1-ox, y1-oy])
    return stato["verso"] if stato else None


def test_misura_stato_testo_che_sbiadisce_e_spento_e_senza_cambio_e_nessuno():
    """Fading text reads as off, the reverse as on, and an unchanged control gives no result."""
    prima = np.full((40, 120, 3), 255, np.uint8)
    prima[15:25, 20:100] = (30, 30, 30)
    dopo = prima.copy()
    dopo[15:25, 20:100] = (170, 170, 170)
    assert canale_stato.misura_stato(prima, dopo, [20, 15, 100, 25])["verso"] == "spento"
    assert canale_stato.misura_stato(dopo, prima, [20, 15, 100, 25])["verso"] == "acceso"
    assert canale_stato.misura_stato(prima, prima.copy(), [20, 15, 100, 25]) is None


def test_cerchio_disegnato_attorno_non_cambia_il_controllo():
    """A circle drawn around a control does not alter the control itself, so there is no change."""
    prima = np.full((60, 200, 3), 60, np.uint8)
    prima[20:36, 60:140] = (45, 45, 45)
    dopo = prima.copy()
    cv2.ellipse(dopo, (100, 28), (90, 22), 0, 0, 360, (130, 0, 255), 3)
    assert canale_stato.misura_stato(prima, dopo, [60, 20, 140, 36]) is None


class _Falso:
    """Fake reader: returns ready-made lines, one list per call (after, before)."""

    def __init__(self, *risposte):
        """Store the answers to hand out, in order, one per `leggi` call."""
        self.risposte = list(risposte)
        self.chiamate = 0

    def leggi(self, immagine, origine):
        """Count the call and return the next ready-made list of lines."""
        self.chiamate += 1
        return self.risposte.pop(0)


def _candidato(prima_colore, dopo_colore):
    """A candidate change: an 80x20 box that goes from `prima_colore` to `dopo_colore` on a grey frame."""
    prima = np.full((40, 200, 3), 60, np.uint8)
    prima[10:30, 20:100] = prima_colore
    dopo = prima.copy()
    dopo[10:30, 20:100] = dopo_colore
    return {"t": 4.0, "scatola": [20, 10, 100, 30], "misure": {"delta": 60.0}, "prima": prima, "dopo": dopo,
            "origine": [0, 0]}


def test_nomina_da_nome_e_verso_e_scarta_comparse_valori_e_senza_nome():
    """nomina() names the change and gives its direction; it drops appearances, values and unnamed boxes."""
    riga = ("Alfa Beta", 0.99, [25, 12, 95, 28])
    spento = (45, 45, 45)
    acceso = (0, 140, 255)
    stat = collections.Counter()
    cambi = canale_stato.nomina([_candidato(spento, acceso)], _Falso([riga], [riga]), 1280, 720, stat)
    assert [(cambio["testo"], cambio["stato"], cambio["relazione"]) for cambio in cambi] == [
        ("Alfa Beta", "acceso", "copre")]
    stat = collections.Counter()
    assert canale_stato.nomina([_candidato(spento, acceso)], _Falso([riga], []), 1280, 720, stat) == []
    # It was not there before: an appearance.
    assert sum(stat.values()) == 1
    stat = collections.Counter()
    assert canale_stato.nomina([_candidato(spento, acceso)], _Falso([]), 1280, 720, stat) == []
    assert stat["senza nome"] == 1
    stat = collections.Counter()
    decimale = _Falso([("0.005", 0.99, [25, 12, 95, 28])])
    assert canale_stato.nomina([_candidato(spento, acceso)], decimale, 1280, 720, stat) == []
    assert stat["non e una voce"] == 1
    stat = collections.Counter()
    numero = ("Valore Gamma 2.16", 0.99, [0, 0, 200, 40])
    assert canale_stato.nomina([_candidato(spento, acceso)], _Falso([numero]), 1280, 720, stat) == []
    assert stat["valore che cambia"] == 1


def test_e_voce_scarta_i_contatori():
    """Names with letters are items; counters and plain numbers are not."""
    assert canale_stato.e_voce("Alfa Beta")
    assert not canale_stato.e_voce("Valore Gamma 5")
    assert not canale_stato.e_voce("0.005")
    assert not canale_stato.e_voce("Contatore 952.942")


def test_parlato_e_voce_dice_il_nome_dell_etichetta():
    """parlato() is 1.0 when the label words are heard near the time, 0.0 when they are far away."""
    parole = [[10.0, 10.4, "alfa"], [10.5, 10.9, "beta,"], [90.0, 90.5, "delta"]]
    assert canale_stato.parlato("Alfa Beta", parole, 15.0) == 1.0
    assert canale_stato.parlato("Alfa Beta", parole, 60.0) == 0.0
    assert canale_stato.parlato("Delta", parole, 75.0) == 1.0


def test_frammenti_dello_stesso_bottone_restano_uno_e_il_parlato_decide():
    """Two fragments of one button merge into the one whose text is spoken."""
    cambi = [{"t": 5.0, "testo": "Beta", "zona_norm": [0.1, 0.1, 0.2, 0.15], "delta": 40.0,
              "stato": "acceso", "rif": []},
             {"t": 5.5, "testo": "Alfa Beta", "zona_norm": [0.09, 0.09, 0.22, 0.16], "delta": 50.0,
              "stato": "acceso", "rif": []}]
    parole = [[4.0, 4.5, "alfa"], [4.6, 5.0, "beta"]]
    ottenuti = canale_stato.calcola({"letture": [], "cursori": [], "cambi": cambi, "parole": parole})
    assert [evento["testo"] for evento in ottenuti] == ["Alfa Beta"]


def test_etichetta_sotto_il_cambio_e_accanto_al_pallino_e_numero_scartato():
    """The label is found under the change, beside a dot, or reported as 'dentro' for a number."""
    righe = [("Alfa Beta", 0.99, [100, 50, 160, 64]), ("Size 50", 0.97, [300, 50, 350, 64])]
    assert canale_stato.scegli_etichetta([98, 48, 162, 66], righe)[::2] == ("Alfa Beta", "copre")
    # Dot to the left of the text.
    assert canale_stato.scegli_etichetta([60, 52, 72, 64], righe)[::2] == ("Alfa Beta", "accanto")
    # A number inside the label.
    assert canale_stato.scegli_etichetta([330, 52, 345, 64], righe)[2] == "dentro"
    # No label nearby.
    assert canale_stato.scegli_etichetta([500, 300, 540, 316], righe) is None


def test_acceso_e_spento_fra_voci_allineate_sono_una_scelta_sola():
    """One item turning on while an aligned one turns off is a single choice, not two changes."""
    dati = {"parole": [[0.0, 999.0, "voce"]], "letture": [], "cursori": [], "cambi": [
        {"t": 10.0, "testo": "Alfa", "zona_norm": [0.1, 0.30, 0.2, 0.33], "delta": 30.0, "stato": "spento",
         "rif": []},
        {"t": 10.5, "testo": "Beta", "zona_norm": [0.1, 0.40, 0.2, 0.43], "delta": 30.0, "stato": "acceso",
         "rif": []}]}
    ottenuti = canale_stato.calcola(dati)
    assert [(evento["tipo"], evento["testo"]) for evento in ottenuti] == [("scelto", "Beta")]


def test_un_cambio_isolato_resta_acceso_o_spento():
    """A change with no aligned partner stays a plain 'cambio' with its on/off state."""
    dati = {"parole": [[0.0, 999.0, "voce"]], "letture": [], "cursori": [],
            "cambi": [{"t": 3.0, "testo": "Alfa", "zona_norm": [0.1, 0.30, 0.2, 0.33], "delta": 30.0,
                       "stato": "spento", "rif": []}]}
    ottenuti = canale_stato.calcola(dati)
    assert ottenuti[0]["tipo"] == "cambio" and ottenuti[0]["dati"]["stato"] == "spento"


def _schermo_con_barra():
    """A 300x200 screen with one solid coloured bar (with text), one thin line and one tall block."""
    frame = np.full((200, 300, 3), 70, np.uint8)
    frame[50:70, 10:110] = (200, 100, 30)
    cv2.putText(frame, "Tool", (30, 66), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    frame[120:123, 10:200] = (200, 100, 30)
    frame[150:190, 10:60] = (200, 100, 30)
    return frame


def test_barre_evidenziate_trova_solo_il_rettangolo_pieno_e_colorato():
    """Only the solid coloured rectangle is a bar; the thin line and the tall block are not."""
    barre = canale_stato.barre_evidenziate(_schermo_con_barra())
    assert len(barre) == 1
    assert barre[0][0] == [10, 50, 110, 70]


def test_nome_barra_prende_la_scritta_dentro_e_scarta_i_numeri():
    """The bar takes the name written inside it; numbers and empty readings give no name."""
    dentro = ("Strumento", 0.99, [20, 52, 80, 68])
    fuori = ("Altro", 0.99, [200, 52, 260, 68])
    assert canale_stato.nome_barra([dentro, fuori], [10, 50, 110, 70]) == "Strumento"
    assert canale_stato.nome_barra([("12.5", 0.99, [20, 52, 80, 68])], [10, 50, 110, 70]) is None
    assert canale_stato.nome_barra([], [10, 50, 110, 70]) is None


def test_barre_conferma_nomina_e_segnala_una_volta_sola():
    """A bar is confirmed after 1 s, named once and reported once; a bar that leaves and returns is new."""
    frame = _schermo_con_barra()
    vuoto = np.full_like(frame, 70)
    lettore = _Falso([("Tool", 0.99, [20, 52, 80, 68])])
    barre = canale_stato.Barre(lettore, 300, 200)
    barre.fotogramma_chiave(2, frame)
    barre.aggiungi(3, frame)
    assert barre.eventi == []
    barre.aggiungi(4, frame)
    assert [evento["testo"] for evento in barre.eventi] == ["Tool"]
    assert abs(barre.eventi[0]["t"] - 1.0) < 1e-6
    barre.fotogramma_chiave(40, frame)
    barre.aggiungi(42, frame)
    # Same still bar: no new event and no new reading.
    assert len(barre.eventi) == 1 and lettore.chiamate == 1
    barre.fotogramma_chiave(80, vuoto)
    barre.aggiungi(82, vuoto)
    barre.fotogramma_chiave(120, frame)
    barre.aggiungi(122, frame)
    # Gone and back: a new choice, but the still bar was already read (cache).
    assert len(barre.eventi) == 2 and lettore.chiamate == 1 and barre.stat["barra che non dura"] == 0


def test_barra_che_sparisce_in_un_secondo_non_e_una_scelta():
    """A bar that vanishes within a second is not confirmed, so it is no choice."""
    frame = _schermo_con_barra()
    vuoto = np.full_like(frame, 70)
    barre = canale_stato.Barre(_Falso([("Tool", 0.99, [20, 52, 80, 68])]), 300, 200)
    barre.fotogramma_chiave(2, frame)
    barre.aggiungi(4, vuoto)
    assert barre.eventi == [] and barre.stat["barra che non dura"] == 1


def test_scelte_ignora_voci_scure_finite_nel_gruppo_e_prende_la_colorata():
    """Dark items that joined the group by alignment do not count; the coloured one is the choice."""
    def crea(testo, y, lab):
        return {"testo": testo, "zona": [10, y, 60, y+10], "zona_norm": [0.1, y/100.0, 0.6, (y+10)/100.0],
                "lab": np.asarray(lab, float)}

    voci = [crea("A", 5, [89, 128, 128]), crea("B", 20, [90, 128, 128]), crea("C", 35, [88, 128, 128]),
            crea("D", 50, [89, 128, 128]), crea("Sel", 65, [120, 132, 94]), crea("Buio", 80, [21, 128, 128])]
    assert [trovata["testo"] for trovata in canale_stato.scelte(voci)] == ["Sel"]


def test_barre_dette_a_voce_diventano_scelto_e_le_mute_no():
    """A bar whose name is spoken becomes a 'scelto' event; a bar nobody mentions does not."""
    barre = [{"t": 1.0, "testo": "Strumento", "zona_norm": [0.0, 0.46, 0.08, 0.49]},
             {"t": 1.0, "testo": "Altro", "zona_norm": [0.0, 0.70, 0.08, 0.73]}]
    ottenuti = canale_stato.calcola({"letture": [], "cursori": [], "cambi": [], "barre": barre,
                                     "parole": [[0.0, 0.5, "Strumento"]]})
    assert [(evento["tipo"], evento["testo"], evento["dati"]["dove"]) for evento in ottenuti] == [
        ("scelto", "Strumento", "pannello a sinistra")]


def test_scelta_in_una_lettura_sola_se_dopo_un_secondo_e_ancora_li():
    """A choice in a single reading counts only if the same item is still chosen 1 s later (lab2)."""
    voci = gruppo()
    fermi = [dict(voce_corrente, lab2=voce_corrente["lab"]) for voce_corrente in voci]
    spenti = [dict(voce_corrente, lab2=np.asarray([50, 128, 128], float)) for voce_corrente in voci]
    dati = {"parole": [[0.0, 999.0, "beta"]], "cursori": [], "cambi": []}
    ottenuti = canale_stato.calcola(dict(dati, letture=[{"t": 20.0, "voci": fermi}]))
    assert [evento["testo"] for evento in ottenuti] == ["Beta"]
    assert canale_stato.calcola(dict(dati, letture=[{"t": 20.0, "voci": spenti}])) == []


def test_gli_eventi_lontani_da_ogni_parola_non_si_stampano_perche_resterebbero_isolati():
    """An event far from every spoken word is dropped (it would stay isolated); a near one is printed."""
    cambi = [{"t": 50.0, "testo": "Alfa", "zona_norm": [0.1, 0.30, 0.2, 0.33], "delta": 30.0,
              "stato": "acceso", "rif": []}]
    lontane = {"letture": [], "cursori": [], "cambi": cambi, "parole": [[0.0, 1.0, "voce"]]}
    assert canale_stato.calcola(lontane) == []
    ottenuti = canale_stato.calcola({"letture": [], "cursori": [], "cambi": cambi,
                                     "parole": [[49.0, 49.5, "voce"]]})
    assert len(ottenuti) == 1 and ottenuti[0]["stampa"] == "riga"


def test_le_voci_in_colonna_si_separano_alle_sezioni_del_menu():
    """Items in one column split into separate groups where a menu has a gap between sections."""
    def crea(y):
        return {"testo": "v%d" % y, "zona": [10, y, 60, y + 10],
                "zona_norm": [0.1, y / 400.0, 0.6, (y + 10) / 400.0],
                "lab": np.asarray([89, 128, 128], float)}

    # Two distant sections.
    voci = [crea(10), crea(25), crea(40), crea(200), crea(215), crea(230)]
    assert [len(indici) for indici in canale_stato._gruppi(voci)] == [3, 3]


def test_se_il_cambio_ricopre_due_scritte_vince_quella_che_cambia():
    """When the change covers two texts, the one whose control really changes wins."""
    prima = np.full((80, 200, 3), 60, np.uint8)
    prima[10:26, 20:100] = (45, 45, 45)
    prima[40:56, 20:100] = (45, 45, 45)
    dopo = prima.copy()
    # Only the second one turns on.
    dopo[40:56, 20:100] = (0, 140, 255)
    candidato = {"t": 4.0, "scatola": [15, 5, 105, 60], "misure": {"delta": 60.0}, "prima": prima,
                 "dopo": dopo, "origine": [0, 0]}
    righe = [("Alfa Uno", 0.99, [25, 12, 95, 24]), ("Beta Due", 0.99, [25, 42, 95, 54])]
    migliore = canale_stato._migliore_coperta(candidato, righe)
    assert migliore[0] == "Beta Due" and migliore[2]["verso"] == "acceso"
    stat = collections.Counter()
    cambi = canale_stato.nomina([candidato], _Falso(righe, [righe[1]]), 1280, 720, stat)
    assert [(cambio["testo"], cambio["stato"]) for cambio in cambi] == [("Beta Due", "acceso")]


def test_pallino_letto_insieme_alla_scritta_e_una_scelta_e_un_numero_in_coda_no():
    """A dot read as 'O' together with the label gives the clean label; a trailing number stays 'dentro'."""
    riga = ("O Alfa Beta (gamma)", 0.99, [100, 50, 260, 64])
    assert canale_stato.scegli_etichetta([98, 50, 110, 62], [riga]) == (
        "Alfa Beta (gamma)", [100, 50, 260, 64], "accanto")
    cifre = ("Alfa Beta 93", 0.99, [100, 50, 260, 64])
    assert canale_stato.scegli_etichetta([240, 50, 258, 62], [cifre])[2] == "dentro"


def test_pallino_in_testa_alla_riga_riletta_a_destra_da_un_nome_pulito():
    """A dot at the head of the line makes nomina() re-read to its right and get a clean name."""
    prima = np.full((40, 200, 3), 60, np.uint8)
    cv2.circle(prima, (26, 16), 6, (150, 150, 150), 1)
    dopo = prima.copy()
    cv2.circle(dopo, (26, 16), 5, (200, 60, 120), -1)
    candidato = {"t": 4.0, "scatola": [19, 9, 33, 23], "misure": {"delta": 80.0}, "prima": prima,
                 "dopo": dopo, "origine": [0, 0]}
    riga_unica = ("xx Alfa Beta", 0.99, [17, 8, 150, 24])
    nome = ("Alfa Beta", 0.99, [40, 8, 150, 24])
    stat = collections.Counter()
    cambi = canale_stato.nomina([candidato], _Falso([riga_unica], [nome], [riga_unica]), 1280, 720, stat)
    assert [(cambio["testo"], cambio["stato"], cambio["relazione"]) for cambio in cambi] == [
        ("Alfa Beta", "acceso", "accanto")]


def test_cursore_che_si_sposta_lascia_due_macchie_e_non_e_un_controllo():
    """A moving cursor leaves two small blobs (where it was, where it is) and is not a control."""
    def cambia(i, frame):
        frame[100:114, 200:212] = (255, 255, 255) if i < 6 else (90, 90, 90)      # where it was
        frame[150:164, 300:312] = (255, 255, 255) if i >= 6 else (90, 90, 90)     # where it is
        for x in range(24, 56, 4):
            frame[44:52, x:x+1] = (230, 230, 230)

    rivelatore, candidati = _corri(16, cambia)
    assert candidati == [] and rivelatore.scartati["cursore che si sposta"] >= 2


def test_un_numero_in_testa_o_in_coda_non_e_una_voce():
    """A number at the start or at the end of the text makes it a value, not an item."""
    assert not canale_stato.e_voce("50 Curve Forza")
    assert not canale_stato.e_voce("Curve Forza 50")
    assert canale_stato.e_voce("Curve Forza")


def test_tetto_di_sei_eventi_al_minuto_resta_il_piu_forte():
    """At most six events per minute are kept, and the strongest (V9) survives while the weakest (V0) goes."""
    cambi = [{"t": 2.0 + 5*i, "testo": "V%d" % i, "zona_norm": [0.1, 0.05*i, 0.2, 0.05*i+0.03],
              "delta": 20.0 + i, "stato": "acceso", "rif": []} for i in range(10)]
    ottenuti = canale_stato.calcola({"parole": [[0.0, 999.0, "voce"]], "letture": [], "cursori": [],
                                     "cambi": cambi})
    assert len(ottenuti) == 6
    assert "V9" in [evento["testo"] for evento in ottenuti]
    assert "V0" not in [evento["testo"] for evento in ottenuti]
