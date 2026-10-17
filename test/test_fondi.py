# -*- coding: utf-8 -*-
import json, os, sys, tempfile
SRC=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','src')); sys.path.insert(0,SRC)
import fondi
def evento_doc(forza=0.8):
    return {'schema':1,'canale':'cursore','lettera':'D','versione':'0.1','sezione':'CURSORE','legenda':'CURSORE = clic e soste','formati':{'clic':'clic {ogg}'},'stat':{'copertura':1.0},'eventi':[{'id':'D1','t':1.0,'durata':0.2,'canale':'cursore','tipo':'clic','zona':None,'punto':None,'testo':'Keep Groups','forza':forza,'stampa':'sezione','rif':[],'dati':{}}]}
def fusione(td): return fondi.Fusione(td,[[0.5,0.8,'click'],[0.9,1.2,'this']],[[[0.5,0.8,'click'],[0.9,1.2,'this']]],[],[],[],10.0)
def test_nessun_evento_e_noop(monkeypatch):
    monkeypatch.delenv('AD_MOVIOLA_SENZA',raising=False)
    with tempfile.TemporaryDirectory() as td:
        fx=fusione(td); valori=[1,2]; assert fx.stat=={}; assert fx.tieni('MOTO',valori) is valori; assert fx.sezioni(0)==[]; assert fx.righe_prima(0)==[]; assert fx.isolati()==[]; assert fx.usate()==[]; assert fx.legenda()==''
def test_sottrazione(monkeypatch):
    monkeypatch.setenv('AD_MOVIOLA_SENZA',' VOCE, testo ,MOTO ')
    with tempfile.TemporaryDirectory() as td:
        fx=fusione(td); assert fx.attiva('VOCE') is False; assert fx.tieni('TESTO',[1])==[]; assert fx.tieni('SCHERMO',[1])==[1]; assert fx.tieni('MOTO',[1])==[]
def test_evento_stampato(monkeypatch):
    monkeypatch.delenv('AD_MOVIOLA_SENZA',raising=False)
    with tempfile.TemporaryDirectory() as td:
        with open(os.path.join(td,'eventi_cursore.json'),'w',encoding='utf-8') as f: json.dump(evento_doc(),f)
        fx=fusione(td); assert fx.sezioni(0)==['CURSORE 00:01 clic "Keep Groups"']; assert fx.usate()==['cursore (1 eventi)']; assert fx.legenda()=='CURSORE = clic e soste'
def test_evento_sotto_soglia(monkeypatch):
    monkeypatch.delenv('AD_MOVIOLA_SENZA',raising=False)
    with tempfile.TemporaryDirectory() as td:
        with open(os.path.join(td,'eventi_cursore.json'),'w',encoding='utf-8') as f: json.dump(evento_doc(0.49),f)
        fx=fusione(td); assert fx.sezioni(0)==[]; assert fx.stat['eventi_scartati_soglia']==1
def test_canale_futuro_sottratto(monkeypatch):
    monkeypatch.setenv('AD_MOVIOLA_SENZA','CURSORE')
    with tempfile.TemporaryDirectory() as td:
        with open(os.path.join(td,'eventi_cursore.json'),'w',encoding='utf-8') as f: json.dump(evento_doc(),f)
        fx=fusione(td); assert fx.sezioni(0)==[]; assert fx.legenda()==''
def test_deittico_dopo_pulizia(monkeypatch):
    monkeypatch.delenv('AD_MOVIOLA_SENZA',raising=False)
    with tempfile.TemporaryDirectory() as td:
        with open(os.path.join(td,'eventi_cursore.json'),'w',encoding='utf-8') as f: json.dump(evento_doc(),f)
        fx=fusione(td); assert fx.voce(0,'click this')=='click this [→Keep Groups]'
