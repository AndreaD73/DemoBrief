# -*- coding: utf-8 -*-
import copy, json, os, sys, tempfile
SRC=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','src')); sys.path.insert(0,SRC)
import eventi
def documento():
    return {'schema':1,'canale':'cursore','lettera':'D','versione':'0.1','sezione':'CURSORE','legenda':'CURSORE = clic e soste','formati':{'clic':'clic {ogg}'},'stat':{'copertura':0.82},'eventi':[{'id':'D17','t':47.6,'durata':0.3,'canale':'cursore','tipo':'clic','zona':[0.812,0.420,0.861,0.447],'punto':[0.830,0.433],'testo':'','forza':0.8,'stampa':'sezione','rif':[],'dati':{'forma':'freccia'}}]}
def test_valida_schema_completo(): assert eventi.valida(documento())['eventi'][0]['t']==47.6
def test_valida_rifiuta_schema_errato():
    doc=documento(); doc['schema']=2
    try: eventi.valida(doc)
    except ValueError as exc: assert str(exc)=='schema diverso da 1'
    else: raise AssertionError('schema 2 accettato')
def test_valida_rifiuta_coordinate_fuori_scala():
    doc=documento(); doc['eventi'][0]['punto']=[1.1,0.2]
    try: eventi.valida(doc)
    except ValueError as exc: assert str(exc)=='evento 0: punto non valido'
    else: raise AssertionError('punto fuori scala accettato')
def test_valida_rifiuta_id_duplicato():
    doc=documento(); doc['eventi'].append(copy.deepcopy(doc['eventi'][0]))
    try: eventi.valida(doc)
    except ValueError as exc: assert str(exc)=='evento 1: id non valido o duplicato'
    else: raise AssertionError('id duplicato accettato')
def test_scrivi_e_carica():
    with tempfile.TemporaryDirectory() as td:
        p=os.path.join(td,'eventi_cursore.json'); eventi.scrivi(p,documento()); doc,errore=eventi.carica(p); assert errore==''; assert doc['canale']=='cursore'; assert not os.path.exists(p+'.tmp')
def test_dove(): assert eventi.dove([0.75,0.2,0.95,0.8])=='pannello a destra'; assert eventi.dove([0.0,0.0,1.0,1.0])=='pagina'
def test_cursore_da_mov():
    assert eventi.cursore_da_mov([[2.0,0.2,0.3,'freccia',0.8],[1.0,0.1,0.2,'mano',0.9]])==[[1.0,0.1,0.2,'mano',0.9],[2.0,0.2,0.3,'freccia',0.8]]
