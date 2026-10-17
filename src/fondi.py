# -*- coding: utf-8 -*-
"""Fusione generica degli eventi; nessuna conoscenza dei singoli canali."""
import glob, os, string
import eventi
from testi import stessi

SOGLIE={'default':0.50}; DOTI={'default':60.0}
ORDINE=('VOCE','TESTO','SCHERMO','CURSORE','STATO','SEGNI','MOTO','VISTA','DINAMICA','STRUTTURA','SUONO')
DEITTICI=frozenset(('this','here','that','these','those','there','questo','questa','questi','queste','qui','qua','quello','quella',"li'","la'"))
PRECEDENZA={'segno':5,'evidenziazione':4,'comparsa':4,'cambio':3,'clic':2,'sosta':1}

class Fusione:
    def __init__(self,out,parole,frasi,testi,schermate,movimenti,durata):
        self.out=out; self.parole=parole; self.frasi=frasi; self.testi=testi; self.schermate=schermate; self.movimenti=movimenti; self.durata=durata
        # sezioni spente: la variabile d'ambiente (anche vuota) vince; altrimenti config.json 'sezioni_spente'
        # TESTO e' disattivato nella configurazione predefinita per contenere la dimensione del racconto.
        v=os.environ.get('AD_MOVIOLA_SENZA')
        if v is None:
            try:
                import comune; v=','.join(comune.config().get('sezioni_spente',[]))
            except Exception: v=''
        self.senza=set(x.strip().upper() for x in v.split(',') if x.strip())
        self.stat={}; self.documenti=[]; self.eventi=[]; self.per_frase={i:[] for i in range(len(frasi))}; self.righe={i:[] for i in range(len(frasi))}; self.sciolti=[]; self.legende={}; self.formati={}; self.scene=eventi.Scena(out,schermate); self._carica()
        if self.documenti: self._filtra(); self._nomina(); self._doppioni(); self._assegna()
    def attiva(self,sezione): return sezione.upper() not in self.senza
    def tieni(self,sezione,valori): return valori if self.attiva(sezione) else []
    def marca(self,t,testo): return testo
    def voce(self,indice,testo):
        if not self.attiva('VOCE'): return ''
        if not self.attiva('DEITTICI') or indice not in self.per_frase: return testo
        candidati=[e for e in self.per_frase[indice] if e.get('_deittico') and e.get('testo')]
        parole=testo.split(); usati=set(); aggiunti=0
        for pos,parola in enumerate(parole):
            nuda=parola.lower().strip(string.punctuation)
            if nuda not in DEITTICI: continue
            validi=[e for e in candidati if e['testo'].lower() not in testo.lower() and e['testo'].lower() not in usati and (nuda not in ('that','there') or e['tipo'] in ('clic','segno','evidenziazione'))]
            if not validi: continue
            scelto=max(validi,key=lambda e:(PRECEDENZA.get(e['tipo'],0),e['forza'],-abs(e['t']-self._tempo_parola(indice,pos))))
            parole[pos]=parola+' [→'+scelto['testo']+']'; usati.add(scelto['testo'].lower()); aggiunti+=1
            if aggiunti==2: break
        return ' '.join(parole)
    def sezioni(self,indice):
        gruppi={}
        for e in self.per_frase.get(indice,[]):
            if e['stampa']=='sezione' and self.attiva(e['_sezione']): gruppi.setdefault(e['_sezione'],[]).append(e)
        risultato=[]
        for sezione in self._ordine_sezioni(gruppi):
            parti=[self._formatta(e) for e in gruppi[sezione]]; parti=[x for x in parti if x]
            if parti: risultato.append(sezione+' '+'; '.join(parti)); self.stat[e_key(sezione,'stampati')]=len(parti)
        return risultato
    def righe_prima(self,indice):
        if not self.per_frase.get(indice): return []
        t=self.frasi[indice][0][0]; risultato=[]
        for e in self.per_frase[indice]:
            if e['stampa']=='riga' and self.attiva(e['_sezione']):
                testo=self._formatta(e)
                if testo: risultato.append('[%s] %s %s'%(mmss(t),e['_sezione'],testo))
        return risultato
    def isolati(self):
        risultato=[]
        for e in self.sciolti:
            if e['stampa'] in ('sezione','riga') and self.attiva(e['_sezione']):
                testo=self._formatta(e)
                if testo: risultato.append((e['t'],None,('fx',e['t'],'[%s] %s %s'%(mmss(e['t']),e['_sezione'],testo))))
        return risultato
    def usate(self):
        return ['%s (%d eventi)'%(doc['canale'],sum(1 for e in self.eventi if e['_canale']==doc['canale'])) for doc in self.documenti if sum(1 for e in self.eventi if e['_canale']==doc['canale'])]
    def legenda(self):
        return '; '.join(self.legende[s] for s in self._ordine_sezioni({x:[] for x in set(e['_sezione'] for e in self.eventi)}) if self.attiva(s) and self.legende.get(s))
    def _carica(self):
        invalidi=0
        for percorso in sorted(glob.glob(os.path.join(self.out,'eventi_*.json'))):
            doc,errore=eventi.carica(percorso)
            if doc is None: invalidi+=1; continue
            self.documenti.append(doc); self.legende[doc['sezione'].upper()]=doc['legenda']
            for tipo,formato in doc['formati'].items(): self.formati[(doc['canale'],tipo)]=formato
            for originale in doc['eventi']:
                e=dict(originale); e['rif']=list(originale['rif']); e['dati']=dict(originale['dati']); e['_canale']=doc['canale']; e['_sezione']=doc['sezione'].upper(); self.eventi.append(e)
        if invalidi: self.stat['eventi_file_invalidi']=invalidi
        if self.documenti: self.stat['eventi_file_validi']=len(self.documenti); self.stat['eventi_letti']=len(self.eventi)
    def _filtra(self):
        tenuti=[]; soglia_scartati=0; dote_scartati=0; per_canale={}
        for e in self.eventi:
            if e['forza']<SOGLIE.get(e['tipo'],SOGLIE['default']): soglia_scartati+=1; continue
            per_canale.setdefault(e['_canale'],[]).append(e)
        for canale,valori in per_canale.items():
            limite=max(1,int(DOTI.get(canale,DOTI['default'])*max(float(self.durata)/60.0,1.0/60.0))); valori.sort(key=lambda e:(-e['forza'],e['t'],e['id'])); tenuti.extend(valori[:limite]); dote_scartati+=max(0,len(valori)-limite)
        self.eventi=sorted(tenuti,key=lambda e:(e['t'],e['id'])); self.stat['eventi_scartati_soglia']=soglia_scartati; self.stat['eventi_scartati_dote']=dote_scartati
    def _nomina(self):
        for e in self.eventi:
            if not e['testo']:
                testo,posizione=self.scene.testo_a(max(0.0,e['t']-0.3),e['punto'],0.03); e['testo']=testo[:80]
                if posizione: e['dati']['dove']=posizione
    def _doppioni(self):
        risultato=[]
        for e in self.eventi:
            unito=None
            for precedente in reversed(risultato):
                if e['t']-precedente['t']>1.0: break
                if famiglia(e['tipo'])==famiglia(precedente['tipo']) and (not e['testo'] or not precedente['testo'] or stessi(e['testo'],precedente['testo'])): unito=precedente; break
            if unito is None: risultato.append(e); continue
            unito['forza']=round(1-(1-unito['forza'])*(1-e['forza']),4); unito['rif']=sorted(set(unito['rif']+e['rif']+[unito['id'],e['id']])); unito['testo']=unito['testo'] or e['testo']; self.stat['eventi_doppioni']=self.stat.get('eventi_doppioni',0)+1
        self.eventi=risultato
    def _assegna(self):
        for e in self.eventi:
            frase=self._frase_vicina(e['t']); e['_deittico']=e['forza']>=0.5 and bool(e['testo']) and e['tipo'] in PRECEDENZA and (e['tipo']!='sosta' or e['durata']>=0.5)
            (self.sciolti if frase is None else self.per_frase[frase]).append(e)
    def _frase_vicina(self,t):
        migliore=None
        for i,frase in enumerate(self.frasi):
            a=frase[0][0]; b=frase[-1][1]; distanza=0.0 if a<=t<=b else min(abs(t-a),abs(t-b))
            if migliore is None or distanza<migliore[0]: migliore=(distanza,i)
        return migliore[1] if migliore and migliore[0]<=1.5 else None
    def _tempo_parola(self,indice,posizione): return self.frasi[indice][min(posizione,len(self.frasi[indice])-1)][0]
    def _formatta(self,e):
        dati=dict(e['dati']); dati.setdefault('ogg',e['testo']); dati.setdefault('testo',e['testo'])
        try: testo=self.formati.get((e['_canale'],e['tipo']),'').format(**dati)
        except (KeyError,ValueError,IndexError): return ''
        if e['testo']: testo=testo.replace(e['testo'],'"%s"'%e['testo'],1)
        if e['dati'].get('marca'): testo=str(e['dati']['marca'])+testo
        return mmss(e['t'])+' '+testo
    def _ordine_sezioni(self,gruppi): return [x for x in ORDINE if x in gruppi]+sorted(x for x in gruppi if x not in ORDINE)

def famiglia(tipo):
    tipo=tipo.lower()
    if tipo in ('clic','click','pressione'): return 'clic'
    if tipo in ('cambio','valore','stato'): return 'cambio'
    if tipo in ('segno','deittico'): return 'segno'
    return tipo
def mmss(t): return '%02d:%02d'%(int(t//60),int(t%60))
def e_key(sezione,nome): return 'eventi_%s_%s'%(sezione.lower(),nome)
