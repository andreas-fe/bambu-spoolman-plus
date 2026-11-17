"""Frozen EUR material prices and sampled, explicitly estimated per-job energy."""
import json
import math
import time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime
from .inventory_db import connection


def decimal_input(value,maximum='100000',places=2):
    text=str(value or '').strip().replace(',','.')
    if not text:return None
    try:n=Decimal(text)
    except InvalidOperation:raise ValueError('Bitte einen gültigen Preis eingeben.')
    if not n.is_finite() or n<0 or n>Decimal(maximum) or n!=n.quantize(Decimal(10)**-places):
        raise ValueError(f'Preis muss zwischen 0 und {maximum} liegen, mit höchstens {places} Nachkommastellen.')
    return n


def eur(value):
    return '—' if value is None else f'{Decimal(str(value)).quantize(Decimal("0.01"),rounding=ROUND_HALF_UP):.2f}'.replace('.',',')+' €'


def freeze_price(db,spool_id):
    row=db.execute('SELECT s.purchase_price_minor,s.currency,s.price_weight_g,f.net_weight_g FROM spools s LEFT JOIN filaments f ON f.id=s.filament_id WHERE s.id=?',(spool_id,)).fetchone()
    weight=row['price_weight_g'] if row['price_weight_g'] is not None else row['net_weight_g']
    return row['purchase_price_minor'],weight,row['currency']


class PrintCosts:
    def __init__(self,path,clock=time.time):self.path=path;self.clock=clock

    def start(self,job_id):
        with connection(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            job=db.execute('SELECT kind,created_at,dry_run FROM jobs WHERE id=?',(job_id,)).fetchone()
            if not job or job['kind']!='PRINT' or job['dry_run']:return
            rate=db.execute("SELECT value_json FROM settings WHERE key='electricity_eur_per_kwh'").fetchone()
            rate=json.loads(rate[0]) if rate else None
            started=datetime.fromisoformat(job['created_at']).timestamp()
            db.execute('INSERT OR IGNORE INTO job_costs(job_id,started_at,electricity_rate) VALUES (?,?,?)',(job_id,started,rate));db.commit()

    def stop(self,job_id,at=None):
        at=self.clock() if at is None else at
        with connection(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM job_costs WHERE job_id=?',(job_id,)).fetchone()
            if not row or row['ended_at'] is not None:return
            covered=0;energy=0
            if row['last_sample_at'] is not None and row['last_power_w'] is not None and 0<=at-row['last_sample_at']<=45:
                covered=at-row['last_sample_at'];energy=covered*row['last_power_w']/3600
            db.execute('UPDATE job_costs SET ended_at=?,covered_seconds=covered_seconds+?,energy_wh=energy_wh+?,last_power_w=NULL WHERE job_id=?',(max(at,row['started_at']),covered,energy,job_id));db.commit()

    def sample(self,job_id,measurement):
        now=self.clock();stamp=measurement.get('observed_at');power=measurement.get('power_w')
        valid=all(type(v) in (int,float) and math.isfinite(v) for v in (stamp,power)) and 0<=power<=4000 and 0<=now-stamp<=40
        with connection(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM job_costs WHERE job_id=?',(job_id,)).fetchone()
            if not row or row['ended_at'] is not None:return
            if not valid:
                db.execute('UPDATE job_costs SET last_power_w=NULL WHERE job_id=?',(job_id,));db.commit();return
            if stamp<row['started_at'] or row['last_sample_at'] is not None and stamp<=row['last_sample_at']:return
            previous=row['last_sample_at'];old_power=row['last_power_w'];covered=0;energy=0
            if previous is None and 0<=stamp-row['started_at']<=45:
                covered=stamp-row['started_at'];energy=covered*power/3600
            elif previous is not None and old_power is not None and 0<stamp-previous<=90:
                covered=stamp-previous;energy=covered*(old_power+power)/2/3600
            db.execute('UPDATE job_costs SET last_sample_at=?,last_power_w=?,covered_seconds=covered_seconds+?,energy_wh=energy_wh+?,sample_count=sample_count+1 WHERE job_id=?',(stamp,power,covered,energy,job_id));db.commit()

    def reconcile(self,active_id):
        """Resume active jobs; never charge an outage as continuously measured energy."""
        if active_id:self.start(active_id)
        with connection(self.path) as db:
            old=[r['job_id'] for r in db.execute('SELECT job_id FROM job_costs WHERE ended_at IS NULL') if r['job_id']!=active_id]
        for job_id in old:self.stop(job_id)

    def summary(self,job_id):
        with connection(self.path) as db:return summarize(db,job_id,self.clock())


def summarize(db,job_id,now):
    job=db.execute('SELECT state,kind,filename,subtask_name,created_at,dry_run FROM jobs WHERE id=?',(job_id,)).fetchone()
    if not job:return None
    entries=db.execute('SELECT grams,purchase_price_minor,price_weight_g,price_currency FROM consumption_entries WHERE job_id=?',(job_id,)).fetchall()
    material=Decimal(0);priced=0
    for row in entries:
        if row['purchase_price_minor'] is not None and row['price_weight_g'] and row['price_currency']=='EUR':
            material+=Decimal(str(row['grams']))*Decimal(row['purchase_price_minor'])/Decimal(str(row['price_weight_g']))/100;priced+=1
    material_complete=bool(entries) and priced==len(entries) and job['state']=='DONE'
    track=db.execute('SELECT * FROM job_costs WHERE job_id=?',(job_id,)).fetchone()
    energy=Decimal(str(track['energy_wh']))/1000 if track and track['sample_count'] else None
    electricity=energy*Decimal(track['electricity_rate']) if energy is not None and track['electricity_rate'] is not None else None
    duration=max(0,(track['ended_at'] if track['ended_at'] is not None else now)-track['started_at']) if track else 0
    coverage=min(1,track['covered_seconds']/duration) if duration else 0
    complete=material_complete and electricity is not None and coverage>=1-1e-6 and track['ended_at'] is not None
    return dict(job_id=job_id,name=job['subtask_name'] or job['filename'] or job_id[:8],state=job['state'],created_at=job['created_at'],
                material_label=eur(material) if priced else 'Nicht erfasst',material_complete=material_complete,priced_entries=priced,entries=len(entries),
                energy_kwh=round(float(energy),5) if energy is not None else None,energy_label=(f'{float(energy):.3f}'.replace('.',',')+' kWh') if energy is not None else 'Nicht erfasst',
                electricity_label=eur(electricity),rate=track['electricity_rate'] if track else None,coverage_percent=round(coverage*100,1),
                total_label=eur(material.quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)+electricity.quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)) if complete else 'Unvollständig',complete=complete,active=bool(track and track['ended_at'] is None),
                note='Simulation · keine gebuchten Materialkosten' if job['dry_run'] else 'Stromkosten aus Leistungsmessungen geschätzt; fehlende Zeitabschnitte werden nicht hochgerechnet. Materialpreise gelten zum Buchungszeitpunkt.')


COST_PANEL=r'''<section class="card"><details class="diagnostics" id="cost-details"><summary>Druckkosten <span class="muted small">Material & Strom</span></summary><p class="muted small">Letzte 10 Druckaufträge. Preise je Rolle unter Filamente → Physische Spulen bearbeiten; Strompreis unter Einstellungen. Fehlende Preise oder Messungen bleiben unbekannt. Stromkosten sind eine Schätzung aus Leistungsmessungen.</p><div id="cost-rows">Wird beim Öffnen geladen …</div><button type="button" class="quiet" onclick="loadCosts()">Kosten aktualisieren</button></details></section><script>
async function loadCosts(){const target=document.getElementById('cost-rows');try{const response=await fetch('/api/print-costs');const data=await response.json();if(!response.ok)throw Error('Kosten konnten nicht geladen werden.');target.replaceChildren();for(const row of data.entries){const article=document.createElement('article');article.className='summary-box';const title=document.createElement('strong');title.textContent=new Date(row.created_at).toLocaleString('de-DE')+' · '+row.name+' · '+row.state;const line=document.createElement('p');line.textContent='Material: '+row.material_label+(row.material_complete?'':' (Preise unvollständig)')+' · Strom: '+row.electricity_label+' · '+row.energy_label+' · Messabdeckung: '+row.coverage_percent+' %';const total=document.createElement('p');total.textContent='Gesamt: '+row.total_label+(row.complete?' (Schätzung)':'')+(row.active?' · Druck läuft':'');article.append(title,line,total);target.append(article)}if(!data.entries.length)target.textContent='Noch keine Druckaufträge.'}catch(e){target.textContent=e.message}}
document.getElementById('cost-details').addEventListener('toggle',()=>{if(document.getElementById('cost-details').open)loadCosts()});</script>'''


COST_SETTINGS=r'''<section class="panel settings-width"><h2>Druckkosten</h2><form method="post" action="/settings/print-costs"><input type="hidden" name="csrf_token" value="{{token}}"><label>Strompreis (€/kWh)<input type="number" name="electricity_eur_per_kwh" min="0" max="100" step="0.0001" value="{{electricity_rate or ''}}" placeholder="Nicht hinterlegt"><small>Gesamter Arbeitspreis inklusive Abgaben. Leer bedeutet unbekannt; 0 bedeutet kostenlos. Gilt ab dem nächsten neu erkannten Druckauftrag.</small></label><p class="field-help">Materialpreise je Rolle unter Filamente → Physische Spulen bearbeiten. Während eines Drucks werden Tapo-Leistungsmessungen automatisch etwa alle 30 Sekunden erfasst, auch ohne geöffneten Browser. Stromkosten bleiben eine Schätzung; Messlücken werden ausgewiesen. Vorhandene historische Drucke werden nicht rückwirkend mit heutigen Preisen bewertet.</p><button>Strompreis speichern</button></form></section>'''
