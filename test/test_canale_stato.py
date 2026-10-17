# -*- coding: utf-8 -*-
import collections, json, os, sys, tempfile
import cv2
import numpy as np
SRC=os.path.abspath(os.path.join(os.path.dirname(__file__),"..","src")); sys.path.insert(0,SRC)
import canale_stato
import eventi

def voce(testo,x0,y0,x1,y1,lab): return {"testo":testo,"forza":0.99,"zona":[x0,y0,x1,y1],"zona_norm":[x0/100.0,y0/100.0,x1/100.0,y1/100.0],"lab":np.asarray(lab,dtype=float)}
def gruppo(nome="Beta"): return [voce("Alfa",10,10,30,20,[50,128,128]),voce(nome,10,30,30,40,[80,160,128]),voce("Gamma",10,50,30,60,[51,128,128])]

def test_colore_anello_usa_il_fondo_esterno():
    immagine=np.full((30,30,3),(0,255,0),dtype=np.uint8); immagine[10:20,10:20]=(0,0,255); atteso=cv2.cvtColor(np.uint8([[[0,255,0]]]),cv2.COLOR_BGR2LAB)[0,0]; ottenuto=canale_stato.colore_anello(immagine,[10,10,20,20]); assert np.max(np.abs(ottenuto-atteso))==0
def test_scelto_trova_l_unico_fondo_diverso(): assert canale_stato.scelto(gruppo())["testo"]=="Beta"
def test_scelto_rifiuta_un_gruppo_uniforme():
    voci=gruppo()
    for elemento in voci: elemento["lab"]=np.asarray([50,128,128],dtype=float)
    assert canale_stato.scelto(voci) is None
def test_calcola_emette_scelto_dopo_un_secondo_fuori_dal_cursore():
    dati={"parole":[[0.0,999.0,"beta"]],"letture":[{"t":2.0,"voci":gruppo()},{"t":3.1,"voci":gruppo()}],"cambi":[],"cursori":[]}; ottenuti=canale_stato.calcola(dati); assert len(ottenuti)==1; assert ottenuti[0]["tipo"]=="scelto"; assert ottenuti[0]["testo"]=="Beta"; assert ottenuti[0]["dati"]["dove"]=="pannello a sinistra"
def test_calcola_scarta_la_scelta_col_cursore_dentro():
    dati={"letture":[{"t":2.0,"voci":gruppo()},{"t":3.1,"voci":gruppo()}],"cambi":[],"cursori":[[2.0,0.2,0.35,"",1.0],[3.1,0.2,0.35,"",1.0]]}; assert canale_stato.calcola(dati)==[]
def test_cambio_e_documento_rispettano_lo_schema_unico():
    dati={"parole":[[0.0,999.0,"voce"]],"letture":[],"cursori":[],"cambi":[{"t":4.5,"testo":"Alfa Beta","zona_norm":[0.7,0.4,0.9,0.5],"delta":24.0,"stato":"acceso","rif":["D2"]}]}; calcolati=canale_stato.calcola(dati); doc=canale_stato.documento(calcolati,0.25,2); assert eventi.valida(doc)["eventi"][0]["dati"]["stato"]=="acceso"
    with tempfile.TemporaryDirectory() as cartella:
        percorso=os.path.join(cartella,"eventi_stato.json"); eventi.scrivi(percorso,doc); scritto=json.load(open(percorso,encoding="utf-8")); assert scritto["sezione"]=="STATO"


# ---- E2: cambi compatti e persistenti (immagini sintetiche, nessun video)
def _sfondo():
    f = np.full((360, 640, 3), 90, np.uint8)
    f[40:56, 20:60] = (45, 45, 45)                                  # bottone spento
    for x in range(24, 56, 4): f[44:52, x:x+1] = (230, 230, 230)    # "testo" a tratti sottili
    return f

def _filmato(n, cambia):
    for i in range(n):
        f = _sfondo(); cambia(i, f); yield f

def _corri(n, cambia, soglie=None):
    r = canale_stato.Rivelatore(640, 360, soglie)
    for f in _filmato(n, cambia): r.aggiungi(f)
    return r, r.fine()

def test_rivelatore_vede_un_controllo_che_si_accende_e_resta():
    def cambia(i, f):
        if i >= 4:
            f[40:56, 20:60] = (0, 140, 255)
            for x in range(24, 56, 4): f[44:52, x:x+1] = (230, 230, 230)
    _, c = _corri(16, cambia); assert len(c) == 1; assert c[0]["scatola"][0] >= 19 and c[0]["scatola"][2] <= 62; assert _verso(c[0]) == "acceso"; assert abs(c[0]["t"] - 2.0) < 0.6

def test_rivelatore_vede_un_controllo_che_si_spegne():
    def cambia(i, f):
        if i < 6:
            f[40:56, 20:60] = (0, 140, 255)
            for x in range(24, 56, 4): f[44:52, x:x+1] = (230, 230, 230)
    _, c = _corri(16, cambia); assert len(c) == 1; assert _verso(c[0]) == "spento"

def test_rivelatore_ignora_lampo_cursore_e_comparsa():
    def cambia(i, f):
        if i == 5: f[40:56, 20:60] = (0, 140, 255)                  # un fotogramma solo: non persiste
        if i >= 3: f[120:132, 200:208] = (255, 255, 255)            # freccia bianca che resta: nessun contorno prima
        if i >= 6: f[100:130, 100:160] = (30, 30, 30); f[110:114, 105:150] = (240, 240, 240)   # tooltip comparso
    _, c = _corri(16, cambia); assert [x["scatola"][0] // 10 for x in c] == [10]      # lampo e freccia fuori; la comparsa la ferma poi nomina() (prima non c'era)

def test_rivelatore_scarta_la_vista_che_cambia_sempre():
    def cambia(i, f): f[100:130, 100:160] = (30 + 40*(i % 2), 90, 200 - 60*(i % 2)); f[110:114, 105:150] = (240, 240, 240)
    r, c = _corri(20, cambia); assert c == []

def _verso(c):
    ox, oy = c["origine"]; x0, y0, x1, y1 = c["scatola"]; r = canale_stato.misura_stato(c["prima"], c["dopo"], [x0-ox, y0-oy, x1-ox, y1-oy]); return r["verso"] if r else None

def test_misura_stato_testo_che_sbiadisce_e_spento_e_senza_cambio_e_nessuno():
    prima = np.full((40, 120, 3), 255, np.uint8); prima[15:25, 20:100] = (30, 30, 30); dopo = prima.copy(); dopo[15:25, 20:100] = (170, 170, 170)
    assert canale_stato.misura_stato(prima, dopo, [20, 15, 100, 25])["verso"] == "spento"
    assert canale_stato.misura_stato(dopo, prima, [20, 15, 100, 25])["verso"] == "acceso"
    assert canale_stato.misura_stato(prima, prima.copy(), [20, 15, 100, 25]) is None

def test_cerchio_disegnato_attorno_non_cambia_il_controllo():
    prima = np.full((60, 200, 3), 60, np.uint8); prima[20:36, 60:140] = (45, 45, 45); dopo = prima.copy(); cv2.ellipse(dopo, (100, 28), (90, 22), 0, 0, 360, (130, 0, 255), 3)
    assert canale_stato.misura_stato(prima, dopo, [60, 20, 140, 36]) is None

class _Falso:
    """Lettore finto: restituisce righe gia' pronte, una lista per chiamata (dopo, prima)."""
    def __init__(self, *risposte): self.risposte = list(risposte); self.chiamate = 0
    def leggi(self, immagine, origine): self.chiamate += 1; return self.risposte.pop(0)

def _candidato(prima_colore, dopo_colore):
    prima = np.full((40, 200, 3), 60, np.uint8); prima[10:30, 20:100] = prima_colore; dopo = prima.copy(); dopo[10:30, 20:100] = dopo_colore
    return {"t": 4.0, "scatola": [20, 10, 100, 30], "misure": {"delta": 60.0}, "prima": prima, "dopo": dopo, "origine": [0, 0]}

def test_nomina_da_nome_e_verso_e_scarta_comparse_valori_e_senza_nome():
    riga = ("Alfa Beta", 0.99, [25, 12, 95, 28]); stat = collections.Counter()
    cambi = canale_stato.nomina([_candidato((45, 45, 45), (0, 140, 255))], _Falso([riga], [riga]), 1280, 720, stat)
    assert [(c["testo"], c["stato"], c["relazione"]) for c in cambi] == [("Alfa Beta", "acceso", "copre")]
    stat = collections.Counter(); assert canale_stato.nomina([_candidato((45, 45, 45), (0, 140, 255))], _Falso([riga], []), 1280, 720, stat) == []; assert sum(stat.values()) == 1      # prima non c'era: comparsa
    stat = collections.Counter(); assert canale_stato.nomina([_candidato((45, 45, 45), (0, 140, 255))], _Falso([]), 1280, 720, stat) == []; assert stat["senza nome"] == 1
    stat = collections.Counter(); assert canale_stato.nomina([_candidato((45, 45, 45), (0, 140, 255))], _Falso([("0.005", 0.99, [25, 12, 95, 28])]), 1280, 720, stat) == []; assert stat["non e una voce"] == 1
    stat = collections.Counter(); numero = ("Valore Gamma 2.16", 0.99, [0, 0, 200, 40]); assert canale_stato.nomina([_candidato((45, 45, 45), (0, 140, 255))], _Falso([numero]), 1280, 720, stat) == []; assert stat["valore che cambia"] == 1

def test_e_voce_scarta_i_contatori():
    assert canale_stato.e_voce("Alfa Beta") and not canale_stato.e_voce("Valore Gamma 5") and not canale_stato.e_voce("0.005") and not canale_stato.e_voce("Contatore 952.942")

def test_parlato_e_voce_dice_il_nome_dell_etichetta():
    parole = [[10.0, 10.4, "alfa"], [10.5, 10.9, "beta,"], [90.0, 90.5, "delta"]]
    assert canale_stato.parlato("Alfa Beta", parole, 15.0) == 1.0; assert canale_stato.parlato("Alfa Beta", parole, 60.0) == 0.0; assert canale_stato.parlato("Delta", parole, 75.0) == 1.0

def test_frammenti_dello_stesso_bottone_restano_uno_e_il_parlato_decide():
    cambi = [{"t": 5.0, "testo": "Beta", "zona_norm": [0.1, 0.1, 0.2, 0.15], "delta": 40.0, "stato": "acceso", "rif": []},
             {"t": 5.5, "testo": "Alfa Beta", "zona_norm": [0.09, 0.09, 0.22, 0.16], "delta": 50.0, "stato": "acceso", "rif": []}]
    parole = [[4.0, 4.5, "alfa"], [4.6, 5.0, "beta"]]
    ottenuti = canale_stato.calcola({"letture": [], "cursori": [], "cambi": cambi, "parole": parole}); assert [e["testo"] for e in ottenuti] == ["Alfa Beta"]

def test_etichetta_sotto_il_cambio_e_accanto_al_pallino_e_numero_scartato():
    righe = [("Alfa Beta", 0.99, [100, 50, 160, 64]), ("Size 50", 0.97, [300, 50, 350, 64])]
    assert canale_stato.scegli_etichetta([98, 48, 162, 66], righe)[::2] == ("Alfa Beta", "copre")
    assert canale_stato.scegli_etichetta([60, 52, 72, 64], righe)[::2] == ("Alfa Beta", "accanto")      # pallino a sinistra del testo
    assert canale_stato.scegli_etichetta([330, 52, 345, 64], righe)[2] == "dentro"                         # un numero dentro la scritta
    assert canale_stato.scegli_etichetta([500, 300, 540, 316], righe) is None                              # nessuna scritta vicina

def test_acceso_e_spento_fra_voci_allineate_sono_una_scelta_sola():
    dati = {"parole": [[0.0, 999.0, "voce"]], "letture": [], "cursori": [], "cambi": [
        {"t": 10.0, "testo": "Alfa", "zona_norm": [0.1, 0.30, 0.2, 0.33], "delta": 30.0, "stato": "spento", "rif": []},
        {"t": 10.5, "testo": "Beta", "zona_norm": [0.1, 0.40, 0.2, 0.43], "delta": 30.0, "stato": "acceso", "rif": []}]}
    ottenuti = canale_stato.calcola(dati); assert [(e["tipo"], e["testo"]) for e in ottenuti] == [("scelto", "Beta")]

def test_un_cambio_isolato_resta_acceso_o_spento():
    dati = {"parole": [[0.0, 999.0, "voce"]], "letture": [], "cursori": [], "cambi": [{"t": 3.0, "testo": "Alfa", "zona_norm": [0.1, 0.30, 0.2, 0.33], "delta": 30.0, "stato": "spento", "rif": []}]}
    ottenuti = canale_stato.calcola(dati); assert ottenuti[0]["tipo"] == "cambio" and ottenuti[0]["dati"]["stato"] == "spento"

def _schermo_con_barra():
    f = np.full((200, 300, 3), 70, np.uint8); f[50:70, 10:110] = (200, 100, 30); cv2.putText(f, "Tool", (30, 66), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    f[120:123, 10:200] = (200, 100, 30); f[150:190, 10:60] = (200, 100, 30); return f

def test_barre_evidenziate_trova_solo_il_rettangolo_pieno_e_colorato():
    b = canale_stato.barre_evidenziate(_schermo_con_barra()); assert len(b) == 1; assert b[0][0] == [10, 50, 110, 70]

def test_nome_barra_prende_la_scritta_dentro_e_scarta_i_numeri():
    assert canale_stato.nome_barra([("Strumento", 0.99, [20, 52, 80, 68]), ("Altro", 0.99, [200, 52, 260, 68])], [10, 50, 110, 70]) == "Strumento"
    assert canale_stato.nome_barra([("12.5", 0.99, [20, 52, 80, 68])], [10, 50, 110, 70]) is None
    assert canale_stato.nome_barra([], [10, 50, 110, 70]) is None

def test_barre_conferma_nomina_e_segnala_una_volta_sola():
    f = _schermo_con_barra(); vuoto = np.full_like(f, 70); lettore = _Falso([("Tool", 0.99, [20, 52, 80, 68])]); b = canale_stato.Barre(lettore, 300, 200)
    b.fotogramma_chiave(2, f); b.aggiungi(3, f); assert b.eventi == []; b.aggiungi(4, f); assert [e["testo"] for e in b.eventi] == ["Tool"]; assert abs(b.eventi[0]["t"] - 1.0) < 1e-6
    b.fotogramma_chiave(40, f); b.aggiungi(42, f); assert len(b.eventi) == 1 and lettore.chiamate == 1      # stessa barra ferma: ne' evento nuovo ne' nuova lettura
    b.fotogramma_chiave(80, vuoto); b.aggiungi(82, vuoto); b.fotogramma_chiave(120, f); b.aggiungi(122, f)
    assert len(b.eventi) == 2 and lettore.chiamate == 1 and b.stat["barra che non dura"] == 0      # sparita e tornata: scelta nuova, ma la barra ferma e' gia' letta (cache)

def test_barra_che_sparisce_in_un_secondo_non_e_una_scelta():
    f = _schermo_con_barra(); vuoto = np.full_like(f, 70); b = canale_stato.Barre(_Falso([("Tool", 0.99, [20, 52, 80, 68])]), 300, 200)
    b.fotogramma_chiave(2, f); b.aggiungi(4, vuoto); assert b.eventi == [] and b.stat["barra che non dura"] == 1

def test_scelte_ignora_voci_scure_finite_nel_gruppo_e_prende_la_colorata():
    def v(t, y, lab): return {"testo": t, "zona": [10, y, 60, y+10], "zona_norm": [0.1, y/100.0, 0.6, (y+10)/100.0], "lab": np.asarray(lab, float)}
    voci = [v("A", 5, [89, 128, 128]), v("B", 20, [90, 128, 128]), v("C", 35, [88, 128, 128]), v("D", 50, [89, 128, 128]), v("Sel", 65, [120, 132, 94]), v("Buio", 80, [21, 128, 128])]
    assert [x["testo"] for x in canale_stato.scelte(voci)] == ["Sel"]

def test_barre_dette_a_voce_diventano_scelto_e_le_mute_no():
    barre = [{"t": 1.0, "testo": "Strumento", "zona_norm": [0.0, 0.46, 0.08, 0.49]}, {"t": 1.0, "testo": "Altro", "zona_norm": [0.0, 0.70, 0.08, 0.73]}]
    ottenuti = canale_stato.calcola({"letture": [], "cursori": [], "cambi": [], "barre": barre, "parole": [[0.0, 0.5, "Strumento"]]}); assert [(e["tipo"], e["testo"], e["dati"]["dove"]) for e in ottenuti] == [("scelto", "Strumento", "pannello a sinistra")]

def test_scelta_in_una_lettura_sola_se_dopo_un_secondo_e_ancora_li():
    voci = gruppo(); fermi = [dict(v, lab2=v["lab"]) for v in voci]; spenti = [dict(v, lab2=np.asarray([50, 128, 128], float)) for v in voci]
    dati = {"parole": [[0.0, 999.0, "beta"]], "cursori": [], "cambi": []}
    assert [e["testo"] for e in canale_stato.calcola(dict(dati, letture=[{"t": 20.0, "voci": fermi}]))] == ["Beta"]
    assert canale_stato.calcola(dict(dati, letture=[{"t": 20.0, "voci": spenti}])) == []

def test_gli_eventi_lontani_da_ogni_parola_non_si_stampano_perche_resterebbero_isolati():
    cambi = [{"t": 50.0, "testo": "Alfa", "zona_norm": [0.1, 0.30, 0.2, 0.33], "delta": 30.0, "stato": "acceso", "rif": []}]
    assert canale_stato.calcola({"letture": [], "cursori": [], "cambi": cambi, "parole": [[0.0, 1.0, "voce"]]}) == []
    ottenuti = canale_stato.calcola({"letture": [], "cursori": [], "cambi": cambi, "parole": [[49.0, 49.5, "voce"]]}); assert len(ottenuti) == 1 and ottenuti[0]["stampa"] == "riga"

def test_le_voci_in_colonna_si_separano_alle_sezioni_del_menu():
    def v(y): return {"testo": "v%d" % y, "zona": [10, y, 60, y + 10], "zona_norm": [0.1, y / 400.0, 0.6, (y + 10) / 400.0], "lab": np.asarray([89, 128, 128], float)}
    voci = [v(10), v(25), v(40), v(200), v(215), v(230)]      # due sezioni distanti
    assert [len(g) for g in canale_stato._gruppi(voci)] == [3, 3]

def test_se_il_cambio_ricopre_due_scritte_vince_quella_che_cambia():
    prima = np.full((80, 200, 3), 60, np.uint8); prima[10:26, 20:100] = (45, 45, 45); prima[40:56, 20:100] = (45, 45, 45)
    dopo = prima.copy(); dopo[40:56, 20:100] = (0, 140, 255)                    # si accende solo la seconda
    c = {"t": 4.0, "scatola": [15, 5, 105, 60], "misure": {"delta": 60.0}, "prima": prima, "dopo": dopo, "origine": [0, 0]}
    righe = [("Alfa Uno", 0.99, [25, 12, 95, 24]), ("Beta Due", 0.99, [25, 42, 95, 54])]
    migliore = canale_stato._migliore_coperta(c, righe); assert migliore[0] == "Beta Due" and migliore[2]["verso"] == "acceso"
    stat = collections.Counter(); cambi = canale_stato.nomina([c], _Falso(righe, [righe[1]]), 1280, 720, stat); assert [(x["testo"], x["stato"]) for x in cambi] == [("Beta Due", "acceso")]

def test_pallino_letto_insieme_alla_scritta_e_una_scelta_e_un_numero_in_coda_no():
    riga = ("O Alfa Beta (gamma)", 0.99, [100, 50, 260, 64])
    assert canale_stato.scegli_etichetta([98, 50, 110, 62], [riga]) == ("Alfa Beta (gamma)", [100, 50, 260, 64], "accanto")
    cifre = ("Alfa Beta 93", 0.99, [100, 50, 260, 64]); assert canale_stato.scegli_etichetta([240, 50, 258, 62], [cifre])[2] == "dentro"

def test_pallino_in_testa_alla_riga_riletta_a_destra_da_un_nome_pulito():
    prima = np.full((40, 200, 3), 60, np.uint8); cv2.circle(prima, (26, 16), 6, (150, 150, 150), 1); dopo = prima.copy(); cv2.circle(dopo, (26, 16), 5, (200, 60, 120), -1)
    c = {"t": 4.0, "scatola": [19, 9, 33, 23], "misure": {"delta": 80.0}, "prima": prima, "dopo": dopo, "origine": [0, 0]}
    riga_unica = ("xx Alfa Beta", 0.99, [17, 8, 150, 24]); nome = ("Alfa Beta", 0.99, [40, 8, 150, 24])
    stat = collections.Counter(); cambi = canale_stato.nomina([c], _Falso([riga_unica], [nome], [riga_unica]), 1280, 720, stat)
    assert [(x["testo"], x["stato"], x["relazione"]) for x in cambi] == [("Alfa Beta", "acceso", "accanto")]

def test_cursore_che_si_sposta_lascia_due_macchie_e_non_e_un_controllo():
    def cambia(i, f):
        f[100:114, 200:212] = (255, 255, 255) if i < 6 else (90, 90, 90)      # dove era
        f[150:164, 300:312] = (255, 255, 255) if i >= 6 else (90, 90, 90)     # dove e'
        for x in range(24, 56, 4): f[44:52, x:x+1] = (230, 230, 230)
    r, c = _corri(16, cambia); assert c == [] and r.scartati["cursore che si sposta"] >= 2

def test_un_numero_in_testa_o_in_coda_non_e_una_voce():
    assert not canale_stato.e_voce("50 Curve Forza") and not canale_stato.e_voce("Curve Forza 50") and canale_stato.e_voce("Curve Forza")

def test_tetto_di_sei_eventi_al_minuto_resta_il_piu_forte():
    cambi = [{"t": 2.0 + 5*i, "testo": "V%d" % i, "zona_norm": [0.1, 0.05*i, 0.2, 0.05*i+0.03], "delta": 20.0 + i, "stato": "acceso", "rif": []} for i in range(10)]
    ottenuti = canale_stato.calcola({"parole": [[0.0, 999.0, "voce"]], "letture": [], "cursori": [], "cambi": cambi}); assert len(ottenuti) == 6; assert "V9" in [e["testo"] for e in ottenuti]; assert "V0" not in [e["testo"] for e in ottenuti]
