# -*- coding: utf-8 -*-
"""Synthetic tests of channel C (text style): generated images and readings, no real video."""
import json
import os
import shutil
import sys

import cv2
import numpy as np

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, SRC)
import canale_stile
import eventi

TMP = os.path.join(os.path.dirname(__file__), "_tmp", "canale_stile")

HSV = {"nero": (0, 0, 30), "grigio": (0, 0, 140), "bianco": (0, 0, 240), "rosso": (2, 200, 200),
       "arancio": (12, 200, 200), "giallo": (28, 200, 200), "verde": (60, 200, 200),
       "azzurro": (95, 200, 200), "blu": (115, 200, 200), "viola": (150, 200, 200)}


def immagine_testo(colore_testo, colore_fondo=(245, 245, 245), testo="Testo"):
    """A 240x70 image with `testo` drawn in `colore_testo` (BGR) on a flat `colore_fondo` background."""
    immagine = np.full((70, 240, 3), colore_fondo, dtype=np.uint8)
    cv2.putText(immagine, testo, (10, 47), cv2.FONT_HERSHEY_SIMPLEX, 1.0, colore_testo, 2, cv2.LINE_AA)
    return immagine


def voce(istante, testo, colore, zona, fonte="ocr3", fondo="bianco", contrasto=None, valido=True, hsv=None):
    """Build an already measured reading; `zona` = [x0, y0, x1, y1] in pixels of a 1280x720 frame."""
    hsv_testo = list(hsv or HSV[colore])
    hsv_fondo = list(HSV[fondo])
    if contrasto is None:
        contrasto = abs(hsv_testo[2] - hsv_fondo[2]) / 255.0
    altezza = float(zona[3] - zona[1])
    return {"t": float(istante), "testo": testo, "forza_ocr": .99, "fonte": fonte, "valido": valido,
            "misurabile": True, "zona_px_frame": [float(x) for x in zona],
            "zona_norm": [zona[0] / 1280., zona[1] / 720., zona[2] / 1280., zona[3] / 720.],
            "altezza": altezza, "colore": colore, "colore_fondo": fondo, "hsv": hsv_testo,
            "hsv_fondo": hsv_fondo, "saturazione": float(hsv_testo[1]),
            "saturazione_fondo": float(hsv_fondo[1]), "contrasto": float(contrasto),
            "rilievo": float(contrasto),
            "disattivato": contrasto < canale_stile.SOGLIA_CONTRASTO_BASSO}


def serie(testo, zona, colori, t0=10.0, passo=5.0, **opzioni):
    """Readings of one text, one colour per instant: returns [(time, [reading])]."""
    return [(t0 + passo * indice, [voce(t0 + passo * indice, testo, colore, zona, **opzioni)])
            for indice, colore in enumerate(colori)]


def dati(*pezzi, parole=None):
    """Merge several series and per-instant readings into one readings document."""
    per_istante = {}
    for pezzo in pezzi:
        for istante, voci in pezzo:
            per_istante.setdefault(istante, []).extend(voci)
    return {"parole": parole or [],
            "letture": [{"t": istante, "voci": per_istante[istante]} for istante in sorted(per_istante)]}


def tipi(eventi_calcolati, tipo):
    """The events of a given type."""
    return [evento for evento in eventi_calcolati if evento["tipo"] == tipo]


# ---- measure and colours
def test_misura_stile_legge_testo_blu_e_fondo_bianco():
    """Blue text on white is read as blue on white, with contrast above the 'almost invisible' threshold."""
    misura = canale_stile.misura_stile(immagine_testo((255, 0, 0)), [0, 0, 240, 70])
    assert misura["colore"] == "blu"
    assert misura["colore_fondo"] == "bianco"
    assert misura["contrasto"] > canale_stile.SOGLIA_CONTRASTO_BASSO


def test_misura_stile_nero_e_grigio_si_distinguono_per_luminosita():
    """Black and grey text differ by brightness, and the difference counts as clear."""
    nero = canale_stile.misura_stile(immagine_testo((0, 0, 0)), [0, 0, 240, 70])
    grigio = canale_stile.misura_stile(immagine_testo((150, 150, 150)), [0, 0, 240, 70])
    assert nero["colore"] == "nero"
    assert grigio["colore"] == "grigio"
    assert nero["hsv"][2] < 60 and 120 < grigio["hsv"][2] < 180
    assert canale_stile.differenza_netta(nero["hsv"], grigio["hsv"])


def test_nome_colore_copre_la_tabella_deterministica():
    """Each reference HSV value maps to its expected colour name."""
    casi = [([0, 0, 20], "nero"), ([0, 0, 240], "bianco"), ([0, 20, 130], "grigio"),
            ([2, 200, 200], "rosso"), ([12, 200, 200], "arancio"), ([28, 200, 200], "giallo"),
            ([60, 200, 200], "verde"), ([95, 200, 200], "azzurro"), ([115, 200, 200], "blu"),
            ([150, 200, 200], "viola")]
    assert [canale_stile.nome_colore(hsv) for hsv, _ in casi] == [nome for _, nome in casi]


def test_differenza_netta_ignora_antialiasing_e_compressione():
    """Small brightness or hue shifts are noise; big ones, or a hue change between shades, are real."""
    netta = canale_stile.differenza_netta
    assert not netta([0, 0, 30], [0, 5, 85])          # black slightly lighter: compression
    assert netta([0, 0, 30], [0, 0, 150])             # black -> light grey
    assert netta([0, 0, 30], [115, 200, 200])         # black -> blue
    assert not netta([115, 200, 200], [120, 190, 210])  # same blue, small variations
    assert netta([115, 200, 200], [150, 200, 200])    # blue -> violet
    assert not netta([2, 200, 200], [178, 200, 200])  # red across the zero point: same hue


# ---- valid text
def test_testo_valido_forma():
    """Texts with letters and no lone character pass; codes, fragments and symbols fail."""
    valida = canale_stile.forma_valida
    assert valida("Invalid Operation")
    assert valida("Loop: 8")
    assert valida("Gardening gloves")
    assert not valida("New s")
    assert not valida("S00008")
    assert not valida("1")
    assert not valida("?!#")


def test_testi_validi_chiede_parola_detta_o_lettura_ripetuta():
    """A text is valid if it is read at 2 instants identically or contains a spoken word."""
    ripetuto = [{"t": 1., "testo": "Quotations"}, {"t": 5., "testo": "Quotations"}]
    una_volta = [{"t": 1., "testo": "Cofimn Prevew Canel"}]
    detto = [{"t": 2., "testo": "Customer invoices"}]
    storpi = [{"t": 1., "testo": "Cofim Prevew Canel"}, {"t": 2., "testo": "Cofimn Prevew Canel"},
              {"t": 3., "testo": "Coffm Prevew Canel"}]
    assert canale_stile.testi_validi(ripetuto, [])[id(ripetuto[0])]
    assert not canale_stile.testi_validi(una_volta, [])[id(una_volta[0])]
    parole = [[0., 1., "creating"], [1., 2., "customer"], [2., 3., "invoices."]]
    assert canale_stile.testi_validi(detto, parole)[id(detto[0])]
    # Different variants: none of them is stable.
    assert not any(canale_stile.testi_validi(storpi, []).values())


def test_una_parola_corta_detta_non_rende_valido_il_testo():
    """A short spoken word ('in') does not make a mangled text valid."""
    letture = [{"t": 1., "testo": "Xqzvbn in"}]
    assert not canale_stile.testi_validi(letture, [[0., 1., "in"], [1., 2., "so"]])[id(letture[0])]


# ---- colour change
ZONA_BASE = [100, 200, 200, 216]


def test_cambio_colore_vero_resta_per_due_letture():
    """A new colour that holds for two readings is one colour change, with the right line format."""
    calcolati = canale_stile.calcola(dati(serie("Succulent", ZONA_BASE, ["nero", "nero", "blu", "blu"])))
    cambi = tipi(calcolati, "cambio_colore")
    assert len(cambi) == 1 and cambi[0]["dati"]["prima"] == "nero" and cambi[0]["dati"]["dopo"] == "blu"
    assert cambi[0]["t"] == 20.0
    riga = canale_stile.FORMATI["cambio_colore"].format(testo=cambi[0]["testo"], **cambi[0]["dati"])
    assert riga == "Succulent nero -> blu"


def test_passaggio_del_mouse_o_ultima_lettura_non_sono_un_cambio():
    """A hover that lasts one reading, or a change only in the last reading, is not a change."""
    passaggio = dati(serie("Succulent", ZONA_BASE, ["nero", "blu", "nero", "nero"]))
    assert not tipi(canale_stile.calcola(passaggio), "cambio_colore")
    ultima = dati(serie("Succulent", ZONA_BASE, ["nero", "nero", "nero", "blu"]))
    assert not tipi(canale_stile.calcola(ultima), "cambio_colore")


def test_nero_grigio_serve_una_grande_differenza_di_luminosita():
    """Black to dark grey is not enough; black to light grey is a change."""
    sfumato = [(t, [voce(t, "Succulent", "nero", ZONA_BASE, hsv=(0, 0, 30 if t < 20 else 85))])
               for t in (10., 15., 20., 25.)]
    assert not tipi(canale_stile.calcola(dati(sfumato)), "cambio_colore")
    chiaro = [(t, [voce(t, "Succulent", "nero", ZONA_BASE, hsv=(0, 0, 30 if t < 20 else 150))])
              for t in (10., 15., 20., 25.)]
    cambi = tipi(canale_stile.calcola(dati(chiaro)), "cambio_colore")
    assert len(cambi) == 1 and cambi[0]["dati"]["dopo"] == "grigio"


def test_cambio_con_fondo_che_cambia_resta_un_cambio_con_il_fondo_come_prova():
    """A window darkening the page changes text and background together: still a change."""
    def lettura(istante, testo_hsv, fondo):
        voce_letta = voce(istante, "Succulent", "nero", ZONA_BASE, hsv=testo_hsv)
        voce_letta["hsv_fondo"] = list(HSV[fondo])
        return (istante, [voce_letta])

    letture = [lettura(10., (0, 0, 200), "bianco"), lettura(15., (0, 0, 200), "bianco"),
               lettura(20., (0, 0, 30), "grigio"), lettura(25., (0, 0, 30), "grigio")]
    cambi = tipi(canale_stile.calcola(dati(letture)), "cambio_colore")
    assert len(cambi) == 1 and cambi[0]["dati"]["dopo"] == "nero"
    assert cambi[0]["dati"]["fondo"] == ["bianco", "grigio"]


def test_righe_della_stessa_colonna_che_cambiano_insieme_sono_una_riga():
    """Three aligned lines changing together become one group line with the three names."""
    nomi = ["Succulent", "Gardening gloves", "Flower pots"]
    pezzi = [serie(nome, [100, 200 + 40 * i, 260, 216 + 40 * i], ["nero", "nero", "blu", "blu"])
             for i, nome in enumerate(nomi)]
    calcolati = canale_stile.calcola(dati(*pezzi))
    assert not tipi(calcolati, "cambio_colore")
    gruppi = tipi(calcolati, "cambio_gruppo")
    assert len(gruppi) == 1 and gruppi[0]["dati"]["n"] == 3 and gruppi[0]["dati"]["cosa"] == "righe"
    riga = canale_stile.FORMATI["cambio_gruppo"].format(testo=gruppi[0]["testo"], **gruppi[0]["dati"])
    assert riga == "3 righe nero -> blu: Succulent, Gardening gloves, Flower pots"


def test_gruppo_grande_mostra_al_massimo_quattro_nomi():
    """A group of seven shows at most four names, then '...', within 80 characters."""
    nomi = ["Succulent", "Gardening gloves", "Flower pots", "Garden gnome", "Watering can", "Plant food",
            "Seed packet"]
    pezzi = [serie(nome, [100, 100 + 40 * i, 300, 116 + 40 * i], ["nero", "nero", "blu", "blu"])
             for i, nome in enumerate(nomi)]
    gruppi = tipi(canale_stile.calcola(dati(*pezzi)), "cambio_gruppo")
    assert len(gruppi) == 1 and gruppi[0]["dati"]["n"] == 7
    assert gruppi[0]["testo"].count(",") == 4 and gruppi[0]["testo"].endswith("...")
    assert len(gruppi[0]["testo"]) <= 80


def test_cambi_in_istanti_lontani_non_si_raggruppano():
    """Changes far apart in time stay separate events."""
    prima = serie("Succulent", [100, 200, 260, 216], ["nero", "nero", "blu", "blu"], t0=10.)
    dopo = serie("Gardening gloves", [100, 240, 260, 256], ["nero", "nero", "blu", "blu"], t0=100.)
    calcolati = canale_stile.calcola(dati(prima, dopo))
    assert len(tipi(calcolati, "cambio_colore")) == 2 and not tipi(calcolati, "cambio_gruppo")


# ---- deactivated, warning, label, title
def test_disattivato_nel_gruppo_allineato_una_volta_sola():
    """A grey item in an aligned list of black ones is reported as deactivated, once."""
    def lettura(t):
        voci = [voce(t, "Uno due", "nero", [100, 100, 220, 116]),
                voce(t, "Regular invoice", "grigio", [100, 140, 220, 156]),
                voce(t, "Tre quattro", "nero", [100, 180, 220, 196]),
                voce(t, "Cinque sei", "nero", [100, 220, 220, 236])]
        return (t, voci)

    spenti = tipi(canale_stile.calcola(dati([lettura(2.), lettura(4.), lettura(6.)])), "disattivato")
    assert len(spenti) == 1 and spenti[0]["testo"] == "Regular invoice" and spenti[0]["dati"]["marca"] == "~"


def test_avviso_rosso_isolato_ma_non_una_colonna_di_rosso():
    """An isolated red text is a warning; a column of red items is data, not a warning."""
    avviso = [voce(t, "Invalid Operation", "rosso", [400, 300, 700, 330]) for t in (1., 2.)]
    colonna = []
    for t in (1., 2.):
        for i, nome in enumerate(("alfa", "beta", "gamma")):
            colonna.append(voce(t, "Stato %s" % nome, "rosso", [900, 100 + 40 * i, 1000, 116 + 40 * i]))
    calcolati = canale_stile.calcola(dati([(1., [avviso[0]] + colonna[:3]), (2., [avviso[1]] + colonna[3:])]))
    avvisi = tipi(calcolati, "avviso")
    assert [evento["testo"] for evento in avvisi] == ["Invalid Operation"]
    assert avvisi[0]["dati"]["marca"] == "!"


def test_avviso_serve_in_due_istanti_e_non_se_e_fermo_per_tutto_il_video():
    """A warning needs two instants and must not sit still for the whole video."""
    def arancio(tempi, nome="Edit mode"):
        return [(t, [voce(t, nome, "arancio", [400, 300, 520, 316])]) for t in tempi]

    def con_durata(tempi):
        return dict(dati(arancio(tempi)), durata=300.0)

    # A single reading: noise.
    assert not tipi(canale_stile.calcola(con_durata([10.])), "avviso")
    # Still in the same spot: the look of the interface.
    assert not tipi(canale_stile.calcola(con_durata([10., 100., 200.])), "avviso")
    # Appears and disappears.
    avvisi = tipi(canale_stile.calcola(con_durata([10., 14., 16.])), "avviso")
    assert len(avvisi) == 1 and avvisi[0]["t"] == 10.0


def test_etichetta_gialla_che_si_sposta_col_contenuto_e_una_sola_riga():
    """A yellow label that floats with the content gives a single line."""
    letture = []
    for t in (5., 40., 200., 290.):
        zona = [100 + 3 * int(t), 300 + int(t), 160 + 3 * int(t), 316 + int(t)]
        letture.append((t, [voce(t, "Loop:8", "giallo", zona, fondo="nero")]))
    etichette = tipi(canale_stile.calcola(dict(dati(letture), durata=300.0)), "etichetta")
    assert len(etichette) == 1 and etichette[0]["testo"] == "Loop:8"
    assert etichette[0]["dati"]["colore_f"] == "gialla" and etichette[0]["t"] == 5.0


def test_testo_e_fondo_scambiati_sono_una_misura_capovolta_non_un_cambio():
    """Text and background swapping colours is a flipped measurement, not a change."""
    def lettura(t, testo, fondo):
        voce_letta = voce(t, "New", "bianco", [20, 20, 60, 36])
        voce_letta["hsv"] = list(HSV[testo])
        voce_letta["hsv_fondo"] = list(HSV[fondo])
        return (t, [voce_letta])

    letture = [lettura(1., "bianco", "viola"), lettura(2., "bianco", "viola"),
               lettura(3., "viola", "bianco"), lettura(4., "viola", "bianco")]
    calcolati = canale_stile.calcola(dati(letture))
    assert not tipi(calcolati, "cambio_colore")


def test_disattivato_chiede_una_lista_a_passo_regolare():
    """The deactivated mark needs a list with a regular pitch; scattered items do not qualify."""
    def lettura(t, ys):
        nomi = ("Uno due", "Regular invoice", "Tre quattro", "Cinque sei")
        voci = [voce(t, nome, "grigio" if i == 1 else "nero", [100, y, 220, y + 16])
                for i, (nome, y) in enumerate(zip(nomi, ys))]
        return (t, voci)

    regolare = dati([lettura(2., (100, 140, 180, 220)), lettura(4., (100, 140, 180, 220))])
    assert tipi(canale_stile.calcola(regolare), "disattivato")
    sparsa = dati([lettura(2., (100, 140, 300, 420)), lettura(4., (100, 140, 300, 420))])
    assert not tipi(canale_stile.calcola(sparsa), "disattivato")


def test_etichetta_colorata_isolata_e_titolo_grande():
    """An isolated tag on a coloured background is a label; the big text on top is a title."""
    def lettura(t):
        return (t, [voce(t, "Loop: 8", "nero", [560, 400, 700, 416], fondo="giallo"),
                    voce(t, "Parametro uno", "nero", [100, 600, 250, 616]),
                    voce(t, "Valore due", "nero", [700, 640, 820, 656]),
                    voce(t, "Gran Titolo", "nero", [300, 30, 600, 70])])

    calcolati = canale_stile.calcola(dati([lettura(1.), lettura(2.)]))
    etichette = tipi(calcolati, "etichetta")
    assert len(etichette) == 1 and etichette[0]["testo"] == "Loop: 8"
    assert etichette[0]["dati"]["colore_f"] == "gialla"
    titoli = tipi(calcolati, "titolo")
    assert len(titoli) == 1 and titoli[0]["testo"] == "Gran Titolo" and titoli[0]["dati"]["marca"] == "#"


def test_titolo_gia_detto_non_si_stampa():
    """A title whose words are spoken in the video is not reported."""
    def lettura(t):
        return (t, [voce(t, "Gran Titolo", "nero", [300, 30, 600, 70]),
                    voce(t, "Parametro uno", "nero", [100, 600, 250, 616]),
                    voce(t, "Valore due", "nero", [700, 640, 820, 656])])

    una_parola = dati([lettura(1.), lettura(2.)], parole=[[0., 1., "titolo"]])
    assert not tipi(canale_stile.calcola(una_parola), "titolo")
    due_parole = dati([lettura(1.), lettura(2.)], parole=[[0., 1., "gran"], [1., 2., "titolo"]])
    assert not tipi(canale_stile.calcola(due_parole), "titolo")


def test_testo_non_valido_non_genera_eventi():
    """A reading marked as invalid text produces no printed event."""
    letture = [voce(1., "Cofimn Prevew", "rosso", [400, 300, 700, 330], valido=False)]
    calcolati = canale_stile.calcola(dati([(1., letture)]))
    assert not [evento for evento in calcolati if evento["stampa"] != "nessuna"]


def test_senza_marca_valido_la_validita_si_calcola_dalle_letture():
    """Without the 'valido' mark, validity is computed from all the readings."""
    letture = [(t, [voce(t, "Invalid Operation", "rosso", [400, 300, 700, 330])]) for t in (1., 2.)]
    for _, voci in letture:
        for voce_letta in voci:
            del voce_letta["valido"]
    avvisi = tipi(canale_stile.calcola(dati(letture)), "avviso")
    assert [evento["testo"] for evento in avvisi] == ["Invalid Operation"]
    solo_una = [(1., [voce(1., "Invalid Operation", "rosso", [400, 300, 700, 330])])]
    del solo_una[0][1][0]["valido"]
    assert not tipi(canale_stile.calcola(dati(solo_una)), "avviso")


def test_potatura_segna_solo_ocr1_debole_fuori_dalla_voce_e_senza_cifre():
    """Only weak ocr1 cells outside the spoken vocabulary and without digits are marked (silently)."""
    letture = [voce(5, "Rumore forte", "grigio", [100, 100, 200, 114], fonte="ocr1", contrasto=.1),
               voce(5, "Detto proprio", "nero", [300, 100, 400, 114], fonte="ocr1", contrasto=.8),
               voce(5, "Valore 8", "grigio", [500, 100, 600, 114], fonte="ocr1", contrasto=.15),
               voce(5, "Forte altro", "nero", [700, 100, 800, 114], fonte="ocr1", contrasto=.9)]
    calcolati = canale_stile.calcola(dati([(5., letture)], parole=[[4., 6., "proprio"]]))
    potati = [evento for evento in calcolati if evento["dati"].get("pota")]
    assert [evento["testo"] for evento in potati] == ["Rumore forte"]
    assert potati[0]["stampa"] == "nessuna"


# ---- cap
def test_tetto_quattro_righe_al_minuto_con_priorita():
    """At most four lines per minute are printed, and warnings win over colour changes."""
    nomi = ("Alfa", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel")
    avvisi = []
    for i in range(8):
        for t in (1. + i, 2. + i):
            avvisi.append(voce(t, "%s fault" % nomi[i], "rosso",
                               [20 + 130 * i, 100 + 60 * i, 120 + 130 * i, 116 + 60 * i]))
    cambio = serie("Succulent", [100, 500, 200, 516], ["nero", "nero", "blu", "blu"], t0=10.)
    per_istante = {}
    for avviso in avvisi:
        per_istante.setdefault(avviso["t"], []).append(avviso)
    calcolati = canale_stile.calcola(dati(sorted(per_istante.items()), cambio))
    stampate = [evento for evento in calcolati if evento["stampa"] != "nessuna"]
    assert len(stampate) == 4 and all(evento["tipo"] == "avviso" for evento in stampate)


def test_tetto_si_applica_a_finestre_scorrevoli_di_un_minuto():
    """The cap works on sliding 60 s windows, not on fixed minutes."""
    def avviso(t, nome):
        return ((t, [voce(t, nome, "rosso", [400, 300, 700, 316])]),
                (t + 1, [voce(t + 1, nome, "rosso", [400, 300, 700, 316])]))

    pezzi = []
    for t, nome in ((50., "Avviso alfa"), (55., "Avviso beta"), (58., "Avviso gamma"), (62., "Avviso delta"),
                    (65., "Avviso omega")):
        pezzi.extend(avviso(t, nome))
    stampate = [evento for evento in canale_stile.calcola(dati(pezzi)) if evento["stampa"] != "nessuna"]
    # The fifth falls in the same 60 s window.
    assert len(stampate) == 4


def test_documento_e_scrittura_rispettano_lo_schema_unico():
    """The document written to disk follows the common schema (section, channel, letter, statistics)."""
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP)
    evento = canale_stile._evento(1., "avviso", voce(1, "Errore", "rosso", [200, 200, 400, 216]), .9,
                                  {"marca": "!"})
    evento["id"] = "C1"
    doc = canale_stile.documento([evento], .2, 1, 10.)
    percorso = os.path.join(TMP, "eventi_stile.json")
    eventi.scrivi(percorso, doc)
    with open(percorso, encoding="utf-8") as f:
        scritto = json.load(f)
    assert eventi.valida(scritto)["sezione"] == "RILIEVO"
    assert scritto["canale"] == "stile"
    assert scritto["lettera"] == "C"
    assert scritto["stat"]["eventi_per_tipo"] == {"avviso": 1}
    assert "cpu_secondi" in scritto["stat"]


# ---- reading the synthetic video (whole path: ocr -> decoding -> events)
def test_leggi_e_calcola_su_un_video_sintetico():
    """Write a small video, fake the OCR files, and run leggi + calcola: one black-to-blue change is found."""
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP)
    video = os.path.join(TMP, "sintetico.avi")
    w, h, fps = 320, 240, 5.0
    scrittore = cv2.VideoWriter(video, cv2.VideoWriter_fourcc(*"MJPG"), fps, (w, h))
    # No encoder available: the rest is covered by the other tests.
    if not scrittore.isOpened():
        return
    for n in range(int(fps * 20)):
        istante = n / fps
        fotogramma = np.full((h, w, 3), 245, np.uint8)
        colore = (0, 0, 0) if istante < 10 else (255, 0, 0)
        cv2.putText(fotogramma, "Succulent", (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.8, colore, 2, cv2.LINE_AA)
        cv2.putText(fotogramma, "Quotations", (20, 140), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2,
                    cv2.LINE_AA)
        scrittore.write(fotogramma)
    scrittore.release()
    letture = [[float(t), 0, 0, [["Succulent", 1.0, 15, 35, 165, 70], ["Quotations", 1.0, 15, 115, 165, 150]]]
               for t in range(0, 20, 2)]
    with open(os.path.join(TMP, "ocr3.json"), "w") as f:
        json.dump({"W": w, "H": h, "letture": letture}, f)
    with open(os.path.join(TMP, "parole.json"), "w") as f:
        json.dump([[0., 1., "succulent"]], f)
    letto = canale_stile.leggi(video, TMP)
    calcolati = canale_stile.calcola(letto)
    cambi = tipi(calcolati, "cambio_colore")
    assert len(cambi) == 1 and cambi[0]["testo"] == "Succulent"
    assert cambi[0]["dati"]["prima"] == "nero" and cambi[0]["dati"]["dopo"] == "blu"
    assert letto["conteggi"]["fotogrammi_decodificati"] == len(letture)


def test_nome_breve_toglie_il_frammento_di_parentesi_in_coda():
    """nome_breve() cuts a trailing open-bracket fragment and the spaces around the name."""
    assert canale_stile.nome_breve("[GAR-GNO] Garden gn[GA") == "[GAR-GNO] Garden gn"
    assert canale_stile.nome_breve("[GAR-GNO] Garden gnome[GAR-GNO]") == "[GAR-GNO] Garden gnome"
    assert canale_stile.nome_breve("  Succulent a ") == "Succulent a"
    assert canale_stile.nome_breve("Option B") == "Option B"


def test_i_titoli_si_segnano_ma_non_si_stampano_e_le_parole_attaccate_risultano_dette():
    """Titles are marked but not printed; glued words ('Gardengnome') count as spoken when both are heard."""
    def lettura(t):
        return (t, [voce(t, "Gardengnome", "nero", [300, 30, 600, 70]),
                    voce(t, "Parametro uno", "nero", [100, 600, 250, 616]),
                    voce(t, "Valore due", "nero", [700, 640, 820, 656])])

    titoli = tipi(canale_stile.calcola(dati([lettura(1.), lettura(2.)])), "titolo")
    assert len(titoli) == 1 and titoli[0]["stampa"] == canale_stile.STAMPA_TITOLI == "nessuna"
    dette = dati([lettura(1.), lettura(2.)], parole=[[0., 1., "garden"], [1., 2., "gnomes"]])
    assert not tipi(canale_stile.calcola(dette), "titolo")


def test_senza_letture_non_c_e_niente_da_dire():
    """With no readings the channel produces no events."""
    assert canale_stile.calcola({}) == []
    assert canale_stile.calcola({"letture": [{"t": 1., "voci": []}], "parole": []}) == []
