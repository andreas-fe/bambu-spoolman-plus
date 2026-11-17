"""Explicit, idempotent financial examples; never mutate inventory or measured costs."""
import json
import math
import statistics
from datetime import datetime,timezone
from decimal import Decimal,ROUND_HALF_UP
from .inventory_db import connection

CLOSED=('DONE','CLOSED_NO_USAGE','CANCELLED_ZERO','FAILED','INTERRUPTED')


def number(value):
    try:value=float(value)
    except (ValueError,TypeError):return None
    return value if math.isfinite(value) and value>=0 else None


def price(row):
    if not row or row['currency']!='EUR' or row['purchase_price_minor'] is None:return None
    weight=number(row['price_weight_g']) or number(row['net_weight_g']) or 1000
    amount=number(row['purchase_price_minor'])
    return Decimal(str(amount))/100/Decimal(str(weight)) if amount is not None else None


def timestamp(value):
    try:return datetime.fromisoformat(value).timestamp()
    except (ValueError,TypeError):return None


def estimate_job(db,job,hints):
    from .print_costs import summarize
    if summarize(db,job['id'],datetime.now(timezone.utc).timestamp())['complete']:return None
    notes=[];spools={r['id']:dict(r) for r in db.execute('SELECT s.*,f.net_weight_g,f.material FROM spools s LEFT JOIN filaments f ON f.id=s.filament_id')}
    candidates=[p for row in spools.values() if (p:=price(row)) is not None]
    entries=[dict(r) for r in db.execute('SELECT * FROM consumption_entries WHERE job_id=?',(job['id'],))]
    if entries:notes.append('Verbrauchsmenge aus vorhandenen internen Verbrauchsbuchungen; kein erneuter Materialabzug.')
    if not entries:
        entries=[dict(r) for r in db.execute("SELECT spool_id,grams FROM deductions WHERE job_id=? AND status='DONE' AND grams>0",(job['id'],))]
        if entries:notes.append('Mengen aus vorhandenen abgeschlossenen Buchungspositionen.')
    if not entries:
        try:weights=json.loads(job.get('weights_json') or '{}')
        except (ValueError,TypeError):weights={}
        amounts=[number(v) for v in weights.values()] if isinstance(weights,dict) else []
        amounts=[v for v in amounts if v is not None and v>0]
        if amounts:
            factor=(min(100,number(job.get('progress')) or 0)/100) if job.get('cancelled') else 1
            entries=[dict(spool_id=None,grams=sum(amounts)*factor)]
            notes.append('Menge aus gespeicherten Slicerwerten'+(' anteilig nach Abbruchfortschritt.' if job.get('cancelled') else '.'))
        else:
            calibration='cali' in (job.get('filename','')+' '+job.get('subtask_name','')).casefold()
            entries=[dict(spool_id=None,grams=2 if calibration else 10)]
            notes.append('Keine Verbrauchsmenge gespeichert: '+('2 g für Kalibrierung' if calibration else '10 g Material')+' als Beispiel angenommen; keine Bestandsbuchung.')
    material=Decimal(0);grams=0
    for entry in entries:
        amount=number(entry.get('grams')) or 0;grams+=amount
        frozen=dict(purchase_price_minor=entry.get('purchase_price_minor'),currency=entry.get('price_currency'),price_weight_g=entry.get('price_weight_g'),net_weight_g=None)
        unit=price(frozen)
        if unit is not None and not number(frozen['price_weight_g']):notes.append('Bezugsmenge des gespeicherten Preises fehlt: 1.000 g angenommen.')
        if unit is None:
            spool=spools.get(entry.get('spool_id'));unit=price(spool)
            if unit is not None:
                notes.append(f"Aktuell gepflegter Rollenpreis verwendet ({float(unit)*1000:.2f} €/kg); historischer Kaufpreis nicht belegt.")
                if not (number(spool.get('price_weight_g')) or number(spool.get('net_weight_g'))):notes.append('Preis-Bezugsmenge fehlt: 1.000 g angenommen.')
            else:
                same=[p for row in spools.values() if spool and row['filament_id']==spool['filament_id'] and (p:=price(row)) is not None]
                if not same:same=[p for row in spools.values() if spool and row.get('material') and row['material']==spool.get('material') and (p:=price(row)) is not None]
                unit=statistics.median(same or candidates) if same or candidates else Decimal('.02')
                notes.append(f"Rollenpreis fehlt: {'Vergleichsrollen' if same else 'Median gepflegter Europreise' if candidates else 'Standardannahme'} mit {float(unit)*1000:.2f} €/kg verwendet.")
        material+=Decimal(str(amount))*unit
    track=db.execute('SELECT * FROM job_costs WHERE job_id=?',(job['id'],)).fetchone()
    hint=hints.get(job['id'],{});duration=number(hint.get('seconds'))
    if track and track['ended_at'] is not None:
        duration=max(0,track['ended_at']-track['started_at']);notes.append('Auftragsdauer aus gespeicherter Stromerfassung.')
    elif duration and duration<=8*3600:
        if job.get('cancelled'):duration*=min(100,number(job.get('progress')) or 0)/100
        notes.append('Dauer aus '+str(hint.get('source','Slicer-Dateicache'))+'; historische Dateiidentität nicht sicher belegt.')
    else:
        start,end=timestamp(job.get('created_at')),timestamp(job.get('updated_at'))
        elapsed=end-start if start is not None and end is not None else None
        calibration='cali' in (job.get('filename','')+' '+job.get('subtask_name','')).casefold()
        if calibration:duration=300;notes.append('Kalibrierungsdauer beispielhaft mit 5 Minuten angesetzt.')
        elif elapsed is not None and 60<=elapsed<=8*3600:
            duration=elapsed;notes.append('Dauer aus Erkennung bis letzter Auftragsaktualisierung; kann Wartezeit bis zur Zuordnung/Buchung enthalten.')
        else:duration=1800;notes.append('Keine plausible Dauer: beispielhaft 30 Minuten angenommen.')
    measured_wh=number(track['energy_wh']) if track and track['sample_count'] else 0
    covered=min(duration,number(track['covered_seconds']) or 0) if track else 0
    missing=max(0,duration-covered)
    energy_wh=(measured_wh or 0)+missing*80/3600
    if missing:notes.append(f'Strom ohne Messdaten: 80 W mittlere Leistung für {missing/60:.1f} Minuten angenommen; vorhandene Messenergie bleibt berücksichtigt.')
    rate=number(track['electricity_rate']) if track else None
    if rate is None:
        row=db.execute("SELECT value_json FROM settings WHERE key='electricity_eur_per_kwh'").fetchone()
        try:rate=number(json.loads(row[0])) if row else None
        except (ValueError,TypeError):rate=None
        notes.append('Aktuell gepflegter Stromtarif nachträglich verwendet.' if rate is not None else 'Stromtarif fehlt: 0,35 €/kWh angenommen.')
        if rate is None:rate=.35
    electricity=Decimal(str(energy_wh))/1000*Decimal(str(rate))
    rounded=lambda n:str(n.quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
    return dict(version=1,material_eur=rounded(material),electricity_eur=rounded(electricity),total_eur=rounded(Decimal(rounded(material))+Decimal(rounded(electricity))),
                energy_kwh=energy_wh/1000,rate=str(rate),duration_seconds=duration,grams=grams,assumed_power_w=80 if missing else None,
                notes=list(dict.fromkeys(notes)),created_at=datetime.now(timezone.utc).isoformat(timespec='seconds'))


def backfill_history(path,*,apply=False,duration_hints=None):
    """Explicit operator action. Preview by default; insert once and preserve all source rows."""
    proposals=[]
    with connection(path) as db:
        db.execute('BEGIN IMMEDIATE' if apply else 'BEGIN')
        for row in db.execute("SELECT * FROM jobs WHERE kind='PRINT' AND COALESCE(dry_run,0)=0 ORDER BY created_at"):
            job=dict(row)
            if job['state'] not in CLOSED or db.execute('SELECT 1 FROM job_cost_estimates WHERE job_id=?',(job['id'],)).fetchone():continue
            if db.execute("SELECT 1 FROM runtime WHERE key='active_job_id' AND value=?",(job['id'],)).fetchone():continue
            estimate=estimate_job(db,job,duration_hints or {})
            if estimate is None:continue
            proposals.append(dict(job_id=job['id'],estimate=estimate))
            if apply:db.execute('INSERT INTO job_cost_estimates VALUES (?,?,?)',(job['id'],json.dumps(estimate,allow_nan=False),estimate['created_at']))
        if apply:db.commit()
    return proposals
