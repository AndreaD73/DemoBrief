# -*- coding: utf-8 -*-
"""Test sintetici del canale C (stile dei testi): immagini e letture generate, nessun video vero."""
import json
import os
import shutil
import sys
import cv2
import numpy as np

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")); sys.path.insert(0, SRC)
import canale_stile
import eventi
TMP = os.path.join(os.path.dirname(__file__), "_tmp", "canale_stile")

HSV = {"nero": (0, 0, 30), "grigio": (0, 0, 140), "bianco": (0, 0, 240), "rosso": (2, 200, 200), "arancio": (12, 200, 200),
       "giallo": (28, 200, 200), "verde": (60, 200, 200), "azzurro": (95, 200, 200), "blu": (115, 200, 200), "viola": (150, 200, 200)}


def immagine_testo(colore_testo, colore_fondo=(245, 245, 245), testo="Testo"):
    immagine = np.full((70, 240, 3), colore_fondo, dtype=np.uint8)
    cv2.putText(immagine, testo, (10, 47), cv2.FONT_HERSHEY_SIMPLEX, 1.0, colore_testo, 2, cv2.LINE_AA)
    return immagine


def voce(t, testo, colore, zona, fonte="ocr3", fondo="bianco", contrasto=None, valido=True, hsv=None):
    """Voce gia' misurata. zona = [x0,y0,x1,y1] in pixel di un fotogramma 1280x720."""
    h = list(hsv or HSV[colore]); hf = list(HSV[fondo])
    if contrasto is None: contrasto = abs(h[2] - hf[2]) / 255.0
    altezza = float(zona[3] - zona[1])
    return {"t": float(t), "testo": testo, "forza_ocr": .99, "fonte": fonte, "valido": valido, "misurabile": True,
            "zona_px_frame": [float(x) for x in zona], "zona_norm": [zona[0] / 1280., zona[1] / 720., zona[2] / 1280., zona[3] / 720.],
            "altezza": altezza, "colore": colore, "colore_fondo": fondo, "hsv": h, "hsv_fondo": hf, "saturazione": float(h[1]),
            "saturazione_fondo": float(hf[1]), "contrasto": float(contrasto), "rilievo": float(contrasto),
            "disattivato": contrasto < canale_stile.SOGLIA_CONTRASTO_BASSO}


def serie(testo, zona, colori, t0=10.0, passo=5.0, **kw):
    """Letture dello stesso testo: un colore per istante -> [(t, [voce])]."""
    return [(t0 + passo * i, [voce(t0 + passo * i, testo, c, zona, **kw)]) for i, c in enumerate(colori)]


def dati(*pezzi, parole=None):
    """Unisce piu' serie e voci per istante in un documento di letture."""
    per_t = {}
    for pezzo in pezzi:
        for t, voci in pezzo: per_t.setdefault(t, []).extend(voci)
    return {"parole": parole or [], "letture": [{"t": t, "voci": per_t[t]} for t in sorted(per_t)]}


def tipi(eventi_calcolati, tipo):
    return [e for e in eventi_calcolati if e["tipo"] == tipo]


# ---- misura e colori

def test_misura_stile_legge_testo_blu_e_fondo_bianco():
    m = canale_stile.misura_stile(immagine_testo((255, 0, 0)), [0, 0, 240, 70])
    assert m["colore"] == "blu"; assert m["colore_fondo"] == "bianco"; assert m["contrasto"] > canale_stile.SOGLIA_CONTRASTO_BASSO


def test_misura_stile_nero_e_grigio_si_distinguono_per_luminosita():
    nero = canale_stile.misura_stile(immagine_testo((0, 0, 0)), [0, 0, 240, 70])
    grigio = canale_stile.misura_stile(immagine_testo((150, 150, 150)), [0, 0, 240, 70])
    assert nero["colore"] == "nero"; assert grigio["colore"] == "grigio"
    assert nero["hsv"][2] < 60 and 120 < grigio["hsv"][2] < 180
    assert canale_stile.differenza_netta(nero["hsv"], grigio["hsv"])


def test_nome_colore_copre_la_tabella_deterministica():
    c = [([0, 0, 20], "nero"), ([0, 0, 240], "bianco"), ([0, 20, 130], "grigio"), ([2, 200, 200], "rosso"), ([12, 200, 200], "arancio"),
         ([28, 200, 200], "giallo"), ([60, 200, 200], "verde"), ([95, 200, 200], "azzurro"), ([115, 200, 200], "blu"), ([150, 200, 200], "viola")]
    assert [canale_stile.nome_colore(x) for x, _ in c] == [x[1] for x in c]


def test_differenza_netta_ignora_antialiasing_e_compressione():
    n = canale_stile.differenza_netta
    assert not n([0, 0, 30], [0, 5, 85])          # nero poco piu' chiaro: compressione
    assert n([0, 0, 30], [0, 0, 150])             # nero -> grigio chiaro
    assert n([0, 0, 30], [115, 200, 200])         # nero -> blu
    assert not n([115, 200, 200], [120, 190, 210])  # stesso blu, piccole variazioni
    assert n([115, 200, 200], [150, 200, 200])    # blu -> viola
    assert not n([2, 200, 200], [178, 200, 200])  # rosso a cavallo dello zero: stessa tinta


# ---- testo valido

def test_testo_valido_forma():
    f = canale_stile.forma_valida
    assert f("Invalid Operation"); assert f("Loop: 8"); assert f("Gardening gloves")
    assert not f("New s"); assert not f("S00008"); assert not f("1"); assert not f("?!#")


def test_testi_validi_chiede_parola_detta_o_lettura_ripetuta():
    ripetuto = [{"t": 1., "testo": "Quotations"}, {"t": 5., "testo": "Quotations"}]
    una_volta = [{"t": 1., "testo": "Cofimn Prevew Canel"}]
    detto = [{"t": 2., "testo": "Customer invoices"}]
    storpi = [{"t": 1., "testo": "Cofim Prevew Canel"}, {"t": 2., "testo": "Cofimn Prevew Canel"}, {"t": 3., "testo": "Coffm Prevew Canel"}]
    assert canale_stile.testi_validi(ripetuto, [])[id(ripetuto[0])]
    assert not canale_stile.testi_validi(una_volta, [])[id(una_volta[0])]
    parole = [[0., 1., "creating"], [1., 2., "customer"], [2., 3., "invoices."]]
    assert canale_stile.testi_validi(detto, parole)[id(detto[0])]
    assert not any(canale_stile.testi_validi(storpi, []).values())   # varianti diverse: nessuna e' stabile


def test_una_parola_corta_detta_non_rende_valido_il_testo():
    v = [{"t": 1., "testo": "Xqzvbn in"}]
    assert not canale_stile.testi_validi(v, [[0., 1., "in"], [1., 2., "so"]])[id(v[0])]


# ---- cambio colore

Z = [100, 200, 200, 216]


def test_cambio_colore_vero_resta_per_due_letture():
    e = tipi(canale_stile.calcola(dati(serie("Succulent", Z, ["nero", "nero", "blu", "blu"]))), "cambio_colore")
    assert len(e) == 1 and e[0]["dati"]["prima"] == "nero" and e[0]["dati"]["dopo"] == "blu"
    assert e[0]["t"] == 20.0
    assert canale_stile.FORMATI["cambio_colore"].format(testo=e[0]["testo"], **e[0]["dati"]) == "Succulent nero -> blu"


def test_passaggio_del_mouse_o_ultima_lettura_non_sono_un_cambio():
    assert not tipi(canale_stile.calcola(dati(serie("Succulent", Z, ["nero", "blu", "nero", "nero"]))), "cambio_colore")
    assert not tipi(canale_stile.calcola(dati(serie("Succulent", Z, ["nero", "nero", "nero", "blu"]))), "cambio_colore")


def test_nero_grigio_serve_una_grande_differenza_di_luminosita():
    sfumato = [(t, [voce(t, "Succulent", "nero", Z, hsv=(0, 0, 30 if t < 20 else 85))]) for t in (10., 15., 20., 25.)]
    assert not tipi(canale_stile.calcola(dati(sfumato)), "cambio_colore")
    chiaro = [(t, [voce(t, "Succulent", "nero", Z, hsv=(0, 0, 30 if t < 20 else 150))]) for t in (10., 15., 20., 25.)]
    e = tipi(canale_stile.calcola(dati(chiaro)), "cambio_colore")
    assert len(e) == 1 and e[0]["dati"]["dopo"] == "grigio"


def test_cambio_con_fondo_che_cambia_resta_un_cambio_con_il_fondo_come_prova():
    """Una finestra che oscura la pagina cambia testo e fondo insieme: e' un cambio vero, il fondo e' nei dati."""
    def lettura(t, testo_hsv, fondo):
        v = voce(t, "Succulent", "nero", Z, hsv=testo_hsv); v["hsv_fondo"] = list(HSV[fondo]); return (t, [v])
    letture = [lettura(10., (0, 0, 200), "bianco"), lettura(15., (0, 0, 200), "bianco"), lettura(20., (0, 0, 30), "grigio"), lettura(25., (0, 0, 30), "grigio")]
    e = tipi(canale_stile.calcola(dati(letture)), "cambio_colore")
    assert len(e) == 1 and e[0]["dati"]["dopo"] == "nero" and e[0]["dati"]["fondo"] == ["bianco", "grigio"]


def test_righe_della_stessa_colonna_che_cambiano_insieme_sono_una_riga():
    nomi = ["Succulent", "Gardening gloves", "Flower pots"]
    pezzi = [serie(n, [100, 200 + 40 * i, 260, 216 + 40 * i], ["nero", "nero", "blu", "blu"]) for i, n in enumerate(nomi)]
    c = canale_stile.calcola(dati(*pezzi))
    assert not tipi(c, "cambio_colore"); g = tipi(c, "cambio_gruppo")
    assert len(g) == 1 and g[0]["dati"]["n"] == 3 and g[0]["dati"]["cosa"] == "righe"
    riga = canale_stile.FORMATI["cambio_gruppo"].format(testo=g[0]["testo"], **g[0]["dati"])
    assert riga == "3 righe nero -> blu: Succulent, Gardening gloves, Flower pots"


def test_gruppo_grande_mostra_al_massimo_quattro_nomi():
    nomi = ["Succulent", "Gardening gloves", "Flower pots", "Garden gnome", "Watering can", "Plant food", "Seed packet"]
    pezzi = [serie(n, [100, 100 + 40 * i, 300, 116 + 40 * i], ["nero", "nero", "blu", "blu"]) for i, n in enumerate(nomi)]
    g = tipi(canale_stile.calcola(dati(*pezzi)), "cambio_gruppo")
    assert len(g) == 1 and g[0]["dati"]["n"] == 7 and g[0]["testo"].count(",") == 4 and g[0]["testo"].endswith("...")
    assert len(g[0]["testo"]) <= 80


def test_cambi_in_istanti_lontani_non_si_raggruppano():
    a = serie("Succulent", [100, 200, 260, 216], ["nero", "nero", "blu", "blu"], t0=10.)
    b = serie("Gardening gloves", [100, 240, 260, 256], ["nero", "nero", "blu", "blu"], t0=100.)
    c = canale_stile.calcola(dati(a, b))
    assert len(tipi(c, "cambio_colore")) == 2 and not tipi(c, "cambio_gruppo")


# ---- disattivato, avviso, etichetta, titolo

def test_disattivato_nel_gruppo_allineato_una_volta_sola():
    def lettura(t):
        v = [voce(t, "Uno due", "nero", [100, 100, 220, 116]), voce(t, "Regular invoice", "grigio", [100, 140, 220, 156]),
             voce(t, "Tre quattro", "nero", [100, 180, 220, 196]), voce(t, "Cinque sei", "nero", [100, 220, 220, 236])]
        return (t, v)
    e = tipi(canale_stile.calcola(dati([lettura(2.), lettura(4.), lettura(6.)])), "disattivato")
    assert len(e) == 1 and e[0]["testo"] == "Regular invoice" and e[0]["dati"]["marca"] == "~"


def test_avviso_rosso_isolato_ma_non_una_colonna_di_rosso():
    avviso = [voce(t, "Invalid Operation", "rosso", [400, 300, 700, 330]) for t in (1., 2.)]
    colonna = [voce(t, "Stato %s" % n, "rosso", [900, 100 + 40 * i, 1000, 116 + 40 * i]) for t in (1., 2.) for i, n in enumerate(("alfa", "beta", "gamma"))]
    c = canale_stile.calcola(dati([(1., [avviso[0]] + colonna[:3]), (2., [avviso[1]] + colonna[3:])]))
    a = tipi(c, "avviso")
    assert [x["testo"] for x in a] == ["Invalid Operation"] and a[0]["dati"]["marca"] == "!"


def test_avviso_serve_in_due_istanti_e_non_se_e_fermo_per_tutto_il_video():
    def arancio(tempi, nome="Edit mode"): return [(t, [voce(t, nome, "arancio", [400, 300, 520, 316])]) for t in tempi]
    d = lambda tempi: dict(dati(arancio(tempi)), durata=300.0)
    assert not tipi(canale_stile.calcola(d([10.])), "avviso")                 # una lettura sola: disturbo
    assert not tipi(canale_stile.calcola(d([10., 100., 200.])), "avviso")   # fermo nello stesso punto: aspetto dell'interfaccia
    e = tipi(canale_stile.calcola(d([10., 14., 16.])), "avviso")              # compare e sparisce
    assert len(e) == 1 and e[0]["t"] == 10.0


def test_etichetta_gialla_che_si_sposta_col_contenuto_e_una_sola_riga():
    letture = [(t, [voce(t, "Loop:8", "giallo", [100 + 3 * int(t), 300 + int(t), 160 + 3 * int(t), 316 + int(t)], fondo="nero")]) for t in (5., 40., 200., 290.)]
    e = tipi(canale_stile.calcola(dict(dati(letture), durata=300.0)), "etichetta")
    assert len(e) == 1 and e[0]["testo"] == "Loop:8" and e[0]["dati"]["colore_f"] == "gialla" and e[0]["t"] == 5.0


def test_testo_e_fondo_scambiati_sono_una_misura_capovolta_non_un_cambio():
    def lettura(t, testo, fondo):
        v = voce(t, "New", "bianco", [20, 20, 60, 36]); v["hsv"] = list(HSV[testo]); v["hsv_fondo"] = list(HSV[fondo]); return (t, [v])
    letture = [lettura(1., "bianco", "viola"), lettura(2., "bianco", "viola"), lettura(3., "viola", "bianco"), lettura(4., "viola", "bianco")]
    c = canale_stile.calcola(dati(letture))
    assert not tipi(c, "cambio_colore")


def test_disattivato_chiede_una_lista_a_passo_regolare():
    def lettura(t, ys):
        nomi = ("Uno due", "Regular invoice", "Tre quattro", "Cinque sei")
        return (t, [voce(t, n, "grigio" if i == 1 else "nero", [100, y, 220, y + 16]) for i, (n, y) in enumerate(zip(nomi, ys))])
    assert tipi(canale_stile.calcola(dati([lettura(2., (100, 140, 180, 220)), lettura(4., (100, 140, 180, 220))])), "disattivato")
    assert not tipi(canale_stile.calcola(dati([lettura(2., (100, 140, 300, 420)), lettura(4., (100, 140, 300, 420))])), "disattivato")


def test_etichetta_colorata_isolata_e_titolo_grande():
    def lettura(t):
        return (t, [voce(t, "Loop: 8", "nero", [560, 400, 700, 416], fondo="giallo"), voce(t, "Parametro uno", "nero", [100, 600, 250, 616]),
                    voce(t, "Valore due", "nero", [700, 640, 820, 656]), voce(t, "Gran Titolo", "nero", [300, 30, 600, 70])])
    c = canale_stile.calcola(dati([lettura(1.), lettura(2.)]))
    e = tipi(c, "etichetta"); assert len(e) == 1 and e[0]["testo"] == "Loop: 8" and e[0]["dati"]["colore_f"] == "gialla"
    t = tipi(c, "titolo"); assert len(t) == 1 and t[0]["testo"] == "Gran Titolo" and t[0]["dati"]["marca"] == "#"


def test_titolo_gia_detto_non_si_stampa():
    def lettura(t):
        return (t, [voce(t, "Gran Titolo", "nero", [300, 30, 600, 70]), voce(t, "Parametro uno", "nero", [100, 600, 250, 616]), voce(t, "Valore due", "nero", [700, 640, 820, 656])])
    assert not tipi(canale_stile.calcola(dati([lettura(1.), lettura(2.)], parole=[[0., 1., "titolo"]])), "titolo")
    assert not tipi(canale_stile.calcola(dati([lettura(1.), lettura(2.)], parole=[[0., 1., "gran"], [1., 2., "titolo"]])), "titolo")


def test_testo_non_valido_non_genera_eventi():
    v = [voce(1., "Cofimn Prevew", "rosso", [400, 300, 700, 330], valido=False)]
    assert not [e for e in canale_stile.calcola(dati([(1., v)])) if e["stampa"] != "nessuna"]


def test_senza_marca_valido_la_validita_si_calcola_dalle_letture():
    letture = [(t, [voce(t, "Invalid Operation", "rosso", [400, 300, 700, 330])]) for t in (1., 2.)]
    for _, vv in letture:
        for x in vv: del x["valido"]
    assert [e["testo"] for e in tipi(canale_stile.calcola(dati(letture)), "avviso")] == ["Invalid Operation"]
    solo_una = [(1., [voce(1., "Invalid Operation", "rosso", [400, 300, 700, 330])])]
    del solo_una[0][1][0]["valido"]
    assert not tipi(canale_stile.calcola(dati(solo_una)), "avviso")


def test_potatura_segna_solo_ocr1_debole_fuori_dalla_voce_e_senza_cifre():
    v = [voce(5, "Rumore forte", "grigio", [100, 100, 200, 114], fonte="ocr1", contrasto=.1), voce(5, "Detto proprio", "nero", [300, 100, 400, 114], fonte="ocr1", contrasto=.8),
         voce(5, "Valore 8", "grigio", [500, 100, 600, 114], fonte="ocr1", contrasto=.15), voce(5, "Forte altro", "nero", [700, 100, 800, 114], fonte="ocr1", contrasto=.9)]
    p = [e for e in canale_stile.calcola(dati([(5., v)], parole=[[4., 6., "proprio"]])) if e["dati"].get("pota")]
    assert [e["testo"] for e in p] == ["Rumore forte"]; assert p[0]["stampa"] == "nessuna"


# ---- tetto

def test_tetto_quattro_righe_al_minuto_con_priorita():
    avvisi = [voce(t, "%s fault" % ("Alfa", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel")[i], "rosso", [20 + 130 * i, 100 + 60 * i, 120 + 130 * i, 116 + 60 * i]) for i in range(8) for t in (1. + i, 2. + i)]
    cambio = serie("Succulent", [100, 500, 200, 516], ["nero", "nero", "blu", "blu"], t0=10.)
    per_t = {}
    for v in avvisi: per_t.setdefault(v["t"], []).append(v)
    c = canale_stile.calcola(dati(sorted(per_t.items()), cambio))
    stampate = [x for x in c if x["stampa"] != "nessuna"]
    assert len(stampate) == 4 and all(x["tipo"] == "avviso" for x in stampate)


def test_tetto_si_applica_a_finestre_scorrevoli_di_un_minuto():
    def avviso(t, nome): return (t, [voce(t, nome, "rosso", [400, 300, 700, 316]), ]), (t + 1, [voce(t + 1, nome, "rosso", [400, 300, 700, 316])])
    pezzi = []
    for t, nome in ((50., "Avviso alfa"), (55., "Avviso beta"), (58., "Avviso gamma"), (62., "Avviso delta"), (65., "Avviso omega")):
        pezzi.extend(avviso(t, nome))
    s = [x for x in canale_stile.calcola(dati(pezzi)) if x["stampa"] != "nessuna"]
    assert len(s) == 4   # il quinto cade nella stessa finestra di 60 s


def test_documento_e_scrittura_rispettano_lo_schema_unico():
    shutil.rmtree(TMP, ignore_errors=True); os.makedirs(TMP)
    e = canale_stile._evento(1., "avviso", voce(1, "Errore", "rosso", [200, 200, 400, 216]), .9, {"marca": "!"}); e["id"] = "C1"
    d = canale_stile.documento([e], .2, 1, 10.); p = os.path.join(TMP, "eventi_stile.json"); eventi.scrivi(p, d)
    s = json.load(open(p, encoding="utf-8"))
    assert eventi.valida(s)["sezione"] == "RILIEVO"; assert s["canale"] == "stile"; assert s["lettera"] == "C"
    assert s["stat"]["eventi_per_tipo"] == {"avviso": 1}; assert "cpu_secondi" in s["stat"]


# ---- lettura del video sintetico (percorso intero: ocr -> decodifica -> eventi)

def test_leggi_e_calcola_su_un_video_sintetico():
    shutil.rmtree(TMP, ignore_errors=True); os.makedirs(TMP)
    video = os.path.join(TMP, "sintetico.avi"); w, h, fps = 320, 240, 5.0
    scrittore = cv2.VideoWriter(video, cv2.VideoWriter_fourcc(*"MJPG"), fps, (w, h))
    if not scrittore.isOpened(): return   # nessun codificatore disponibile: il resto e' coperto dagli altri test
    for n in range(int(fps * 20)):
        t = n / fps; fotogramma = np.full((h, w, 3), 245, np.uint8)
        colore = (0, 0, 0) if t < 10 else (255, 0, 0)
        cv2.putText(fotogramma, "Succulent", (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.8, colore, 2, cv2.LINE_AA)
        cv2.putText(fotogramma, "Quotations", (20, 140), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2, cv2.LINE_AA)
        scrittore.write(fotogramma)
    scrittore.release()
    letture = [[float(t), 0, 0, [["Succulent", 1.0, 15, 35, 165, 70], ["Quotations", 1.0, 15, 115, 165, 150]]] for t in range(0, 20, 2)]
    json.dump({"W": w, "H": h, "letture": letture}, open(os.path.join(TMP, "ocr3.json"), "w"))
    json.dump([[0., 1., "succulent"]], open(os.path.join(TMP, "parole.json"), "w"))
    d = canale_stile.leggi(video, TMP); c = canale_stile.calcola(d)
    e = tipi(c, "cambio_colore")
    assert len(e) == 1 and e[0]["testo"] == "Succulent" and e[0]["dati"]["prima"] == "nero" and e[0]["dati"]["dopo"] == "blu"
    assert d["conteggi"]["fotogrammi_decodificati"] == len(letture)


def test_nome_breve_toglie_il_frammento_di_parentesi_in_coda():
    assert canale_stile.nome_breve("[GAR-GNO] Garden gn[GA") == "[GAR-GNO] Garden gn"
    assert canale_stile.nome_breve("[GAR-GNO] Garden gnome[GAR-GNO]") == "[GAR-GNO] Garden gnome"
    assert canale_stile.nome_breve("  Succulent a ") == "Succulent a"
    assert canale_stile.nome_breve("Option B") == "Option B"


def test_i_titoli_si_segnano_ma_non_si_stampano_e_le_parole_attaccate_risultano_dette():
    def lettura(t):
        return (t, [voce(t, "Gardengnome", "nero", [300, 30, 600, 70]), voce(t, "Parametro uno", "nero", [100, 600, 250, 616]), voce(t, "Valore due", "nero", [700, 640, 820, 656])])
    t = tipi(canale_stile.calcola(dati([lettura(1.), lettura(2.)])), "titolo")
    assert len(t) == 1 and t[0]["stampa"] == canale_stile.STAMPA_TITOLI == "nessuna"
    assert not tipi(canale_stile.calcola(dati([lettura(1.), lettura(2.)], parole=[[0., 1., "garden"], [1., 2., "gnomes"]])), "titolo")


def test_senza_letture_non_c_e_niente_da_dire():
    assert canale_stile.calcola({}) == []
    assert canale_stile.calcola({"letture": [{"t": 1., "voci": []}], "parole": []}) == []
