# -*- coding: utf-8 -*-
"""Schema, persistenza e indice spazio-temporale degli eventi DemoBrief."""
import bisect
import json
import os

from testi import pulisci_ocr

SCHEMA = 1
STAMPE = frozenset(('sezione', 'voce', 'riga', 'nessuna'))
CAMPI_RADICE = ('schema', 'canale', 'lettera', 'versione', 'sezione', 'legenda', 'formati', 'stat', 'eventi')
CAMPI_EVENTO = ('id', 't', 'durata', 'canale', 'tipo', 'zona', 'punto', 'testo', 'forza', 'stampa', 'rif', 'dati')

def _numero(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)

def _frazione(v):
    return _numero(v) and 0.0 <= float(v) <= 1.0

def valida(doc):
    if not isinstance(doc, dict): raise ValueError('radice non oggetto')
    mancanti = [k for k in CAMPI_RADICE if k not in doc]
    if mancanti: raise ValueError('campi radice mancanti: ' + ','.join(mancanti))
    if doc['schema'] != SCHEMA: raise ValueError('schema diverso da 1')
    for k in ('canale', 'lettera', 'versione', 'sezione', 'legenda'):
        if not isinstance(doc[k], str) or not doc[k].strip(): raise ValueError(k + ' non valido')
    if not isinstance(doc['formati'], dict): raise ValueError('formati non oggetto')
    if not isinstance(doc['stat'], dict): raise ValueError('stat non oggetto')
    if not isinstance(doc['eventi'], list): raise ValueError('eventi non lista')
    ids = set()
    for n, e in enumerate(doc['eventi']):
        if not isinstance(e, dict): raise ValueError('evento %d non oggetto' % n)
        mancanti = [k for k in CAMPI_EVENTO if k not in e]
        if mancanti: raise ValueError('evento %d: campi mancanti: %s' % (n, ','.join(mancanti)))
        if not isinstance(e['id'], str) or not e['id'] or e['id'] in ids: raise ValueError('evento %d: id non valido o duplicato' % n)
        ids.add(e['id'])
        if not _numero(e['t']) or float(e['t']) < 0: raise ValueError('evento %d: t non valido' % n)
        if not _numero(e['durata']) or float(e['durata']) < 0: raise ValueError('evento %d: durata non valida' % n)
        if e['canale'] != doc['canale']: raise ValueError('evento %d: canale incoerente' % n)
        if not isinstance(e['tipo'], str) or e['tipo'] not in doc['formati']: raise ValueError('evento %d: tipo senza formato' % n)
        if e['zona'] is not None and (not isinstance(e['zona'], list) or len(e['zona']) != 4 or not all(_frazione(x) for x in e['zona'])): raise ValueError('evento %d: zona non valida' % n)
        if e['punto'] is not None and (not isinstance(e['punto'], list) or len(e['punto']) != 2 or not all(_frazione(x) for x in e['punto'])): raise ValueError('evento %d: punto non valido' % n)
        if not isinstance(e['testo'], str) or len(e['testo']) > 240: raise ValueError('evento %d: testo non valido' % n)
        if not _frazione(e['forza']): raise ValueError('evento %d: forza non valida' % n)
        if e['stampa'] not in STAMPE: raise ValueError('evento %d: stampa non valida' % n)
        if not isinstance(e['rif'], list) or not all(isinstance(x, str) for x in e['rif']): raise ValueError('evento %d: rif non valido' % n)
        if not isinstance(e['dati'], dict): raise ValueError('evento %d: dati non oggetto' % n)
        e['t'] = round(float(e['t']), 2); e['durata'] = round(float(e['durata']), 2); e['forza'] = round(float(e['forza']), 4)
    return doc

def scrivi(percorso, doc):
    valida(doc); tmp = percorso + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(doc, f, ensure_ascii=False, separators=(',', ':')); f.write('\n')
    os.replace(tmp, percorso)

def carica(percorso):
    try:
        with open(percorso, encoding='utf-8') as f: return valida(json.load(f)), ''
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc: return None, str(exc)

def dove(zona):
    if not zona: return ''
    if isinstance(zona, str): return zona          # le schermate arrivano gia' col nome della zona
    x0, y0, x1, y1 = zona; cx = (x0+x1)/2.0; cy = (y0+y1)/2.0; area = max(0.0,x1-x0)*max(0.0,y1-y0)
    if area >= 0.5: return 'pagina'
    if 0.3 <= cx <= 0.7 and 0.25 <= cy <= 0.75: return 'finestra al centro'
    if cx > 0.7: return 'pannello a destra'
    if cx < 0.3: return 'pannello a sinistra'
    if cy < 0.25: return 'pannello in alto'
    return 'pannello in basso'

class Scena:
    def __init__(self, out, scr=None):
        self.voci=[]; self.inizi=[]; self._carica_ocr3(out); self._carica_ocr1(out); self._carica_ocr2(out)
        for t, zona, testo in scr or []: self._aggiungi(float(t), float(t)+2.0, testo, None, zona, 1.0)
        self.voci.sort(key=lambda x:(x[0],x[1],x[2])); self.inizi=[x[0] for x in self.voci]
    def _aggiungi(self,t0,t1,testo,punto,zona,forza):
        testo=pulisci_ocr(str(testo))[:80]
        if testo: self.voci.append((float(t0),float(t1),testo,punto,zona,float(forza)))
    def _json(self,out,nome):
        percorso=os.path.join(out,nome)
        if not os.path.exists(percorso): return None
        try:
            with open(percorso,encoding='utf-8') as f: return json.load(f)
        except (OSError,ValueError,TypeError): return None
    def _carica_ocr3(self,out):
        doc=self._json(out,'ocr3.json')
        if not isinstance(doc,dict): return
        w=float(doc.get('W') or 1); h=float(doc.get('H') or 1)
        for lettura in doc.get('letture',[]):
            if len(lettura)!=4: continue
            t,x0,y0,valori=lettura
            for voce in valori:
                if len(voce)<6: continue
                testo,forza,ax,ay,bx,by=voce[:6]; zona=[(x0+ax)/w,(y0+ay)/h,(x0+bx)/w,(y0+by)/h]; punto=[(zona[0]+zona[2])/2.0,(zona[1]+zona[3])/2.0]; self._aggiungi(t,float(t)+2.0,testo,punto,zona,forza)
    def _carica_ocr1(self,out):
        doc=self._json(out,'ocr1.json')
        if not isinstance(doc,dict): return
        for zona,valori in doc.get('stato',{}).items():
            for t,celle in valori:
                testo=' '.join(str(x[0]) for x in celle if len(x)>=2 and x[1]>0.5); forza=max([float(x[1]) for x in celle if len(x)>=2] or [0.0]); self._aggiungi(t,float(t)+1.5,testo,None,None,forza)
    def _carica_ocr2(self,out):
        doc=self._json(out,'ocr2.json')
        if not isinstance(doc,dict): return
        for lettura in doc.get('finestre',[]):
            if len(lettura)!=4: continue
            t,x0,y0,valori=lettura
            for voce in valori:
                if len(voce)<4: continue
                testo,forza,x,y=voce[:4]; punto=[(float(x0)+float(x))/1280.0,(float(y0)+float(y))/720.0]; self._aggiungi(t,float(t)+1.0,testo,punto,None,float(forza)*0.7)
    def testo_a(self,t,punto=None,r=0.03):
        limite=bisect.bisect_right(self.inizi,float(t)); candidati=[]
        for voce in self.voci[max(0,limite-300):limite]:
            t0,t1,testo,vp,zona,forza=voce
            if not(t0<=t<=t1): continue
            distanza=0.0
            if punto is not None and vp is not None:
                distanza=((punto[0]-vp[0])**2+(punto[1]-vp[1])**2)**0.5
                if distanza>r: continue
            candidati.append((forza,-distanza,t0,testo,zona))
        if not candidati: return '',''
        _,_,_,testo,zona=max(candidati); return testo,dove(zona)

def cursore_da_mov(mov):
    traccia=[]
    for elemento in mov or []:
        if isinstance(elemento,dict): t=elemento.get('t',elemento.get('tempo',0)); x=elemento.get('x'); y=elemento.get('y'); forma=elemento.get('forma',''); forza=elemento.get('forza',elemento.get('conf',0))
        elif isinstance(elemento,(list,tuple)) and len(elemento)>=5: t,x,y,forma,forza=elemento[:5]
        else: continue
        if _numero(t) and _frazione(x) and _frazione(y) and _frazione(forza): traccia.append([round(float(t),2),round(float(x),4),round(float(y),4),str(forma),round(float(forza),4)])
    return sorted(traccia,key=lambda x:x[0])
