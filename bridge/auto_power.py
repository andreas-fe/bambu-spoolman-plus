"""Opt-in power-off for one print, after booking and a fresh safety challenge."""
import json
import time
import secrets
import threading
from .inventory_db import connection


class AutoPowerOff:
    KEY='auto_power_off'

    def __init__(self,path,printer_safe,switch_off,clock=time.time,log=lambda text:None,connected=lambda:True):
        self.path=path;self.printer_safe=printer_safe;self.switch_off=switch_off
        self.clock=clock;self.log=log;self.started=clock();self._poll_lock=threading.Lock()
        self.connected=connected

    def status(self):
        with connection(self.path) as db:
            row=db.execute('SELECT value FROM runtime WHERE key=?',(self.KEY,)).fetchone()
            return json.loads(row['value']) if row and row['value'] else dict(enabled=False,phase='off',message='Automatisches Ausschalten ist aus.',delay=120)

    def configure(self,enabled,job_id=None,delay=120):
        if delay not in (60,120,180):raise ValueError('Verzögerung muss 1, 2 oder 3 Minuten betragen.')
        with connection(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            if enabled:
                active=db.execute("SELECT value FROM runtime WHERE key='active_job_id'").fetchone()
                row=db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
                if not active or active['value']!=job_id or not row or row['kind']!='PRINT' or row['state']!='RUNNING':
                    raise ValueError('Automatik nur für den aktuell laufenden Druck aktivieren.')
            value=dict(enabled=bool(enabled),job_id=job_id if enabled else None,delay=delay,
                       generation=secrets.token_urlsafe(16),armed_at=self.clock(),due_at=None,
                       phase='waiting_finish' if enabled else 'off',
                       message='Nach diesem Druck: Buchung abwarten, dann ausschalten.' if enabled else 'Automatisches Ausschalten ist aus.')
            db.execute('INSERT INTO runtime(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(self.KEY,json.dumps(value)))
            db.commit()
        self.log(value['message']);return value

    def _update(self,old,**changes):
        if all(old.get(k)==v for k,v in changes.items()):return old
        with connection(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT value FROM runtime WHERE key=?',(self.KEY,)).fetchone()
            current=json.loads(row['value']) if row else {}
            if current.get('generation')!=old.get('generation') or not current.get('enabled'):return None
            current.update(changes)
            db.execute('UPDATE runtime SET value=? WHERE key=?',(json.dumps(current),self.KEY));db.commit()
        self.log(current['message']);return current

    def _job(self,job_id):
        with connection(self.path) as db:
            job=db.execute('SELECT rowid AS job_rowid,* FROM jobs WHERE id=?',(job_id,)).fetchone()
            active=db.execute("SELECT value FROM runtime WHERE key='active_job_id'").fetchone()
            newer=db.execute("SELECT 1 FROM jobs WHERE kind='PRINT' AND rowid>? LIMIT 1",(job['job_rowid'],)).fetchone() if job else True
            review=db.execute('SELECT 1 FROM runtime WHERE key=?',('ams-job-review:'+str(job_id),)).fetchone()
            return dict(job) if job else None,active['value'] if active else None,bool(newer),bool(review)

    def permitted(self,job_id):
        config=self.status()
        if not config.get('enabled') or config.get('job_id')!=job_id or config.get('phase')!='switching':return False
        job,active,newer,review=self._job(job_id)
        return bool(job and not active and not newer and not review and job['state']=='DONE' and not job['cancelled'] and self.printer_safe(False))

    def poll(self):
        if not self._poll_lock.acquire(blocking=False):return
        try:self._poll()
        finally:self._poll_lock.release()

    def _poll(self):
        config=self.status()
        if not config.get('enabled'):return
        if config.get('phase')=='switching':
            self._update(config,enabled=False,phase='error',message='Ausschaltversuch wurde unterbrochen. Ergebnis bitte manuell prüfen; kein automatischer Wiederholungsversuch.')
            return
        job,active,newer,review=self._job(config['job_id'])
        if not job or newer or (active and active!=config['job_id']):
            self._update(config,enabled=False,phase='cancelled',message='Automatik beendet: Neuer Druck oder Auftrag nicht mehr verfügbar.');return
        if job['cancelled'] or job['state'] in ('FAILED','REJECTED','UNKNOWN','INTERRUPTED','CANCELLED_ZERO'):
            self._update(config,enabled=False,phase='cancelled',message='Automatik beendet: Druck abgebrochen oder Buchung nicht sicher abgeschlossen.');return
        if self.clock()-config.get('armed_at',0)>86400:
            self._update(config,enabled=False,phase='cancelled',message='Automatik nach 24 Stunden ohne sicheren Abschluss abgelaufen.');return
        if job['state']!='DONE' or review or active:
            phase='waiting_finish' if job['state']=='RUNNING' else 'waiting_booking'
            message='Warte auf Druckende.' if phase=='waiting_finish' else 'Warte auf abgeschlossene Buchungen und geklärte Zuordnungen. Noch kein Ausschalten.'
            self._update(config,phase=phase,due_at=None,message=message);return
        if not self.connected() and self.clock()-self.started>60:
            self._update(config,enabled=False,phase='cancelled',due_at=None,message='Drucker nicht verbunden. Automatik beendet; bitte bei Bedarf erneut aktivieren.');return
        if not self.printer_safe(False):
            self._update(config,phase='waiting_status',due_at=None,message='Buchung abgeschlossen. Warte auf sicheren, verbundenen Druckerstatus.');return
        if config.get('due_at') is None:
            self._update(config,phase='countdown',due_at=self.clock()+config['delay'],message=f"Buchung abgeschlossen. Ausschalten nach {config['delay']//60} Minute(n) Wartezeit.");return
        if self.clock()<config['due_at']:return
        if not self.printer_safe(True):
            self._update(config,phase='waiting_status',due_at=None,message='Frische Leerlaufbestätigung fehlt. Ausschalten bleibt gesperrt.');return
        config=self._update(config,phase='switching',message='Leerlauf und Auftrag werden unmittelbar vor dem Ausschalten erneut geprüft.')
        if not config:return
        try:
            if not self.permitted(config['job_id']):raise ValueError('Safety changed')
            result=self.switch_off(config['job_id'])
            if result.get('on') is not False:raise ValueError('Outlet not off')
            self._update(config,enabled=False,phase='completed',message='Druck beendet, Buchung abgeschlossen, Steckdose ausgeschaltet.',due_at=None)
        except Exception:
            self._update(config,enabled=False,phase='error',due_at=None,message='Ausschalten nicht bestätigt. Bitte Steckdose und Druckerstatus prüfen; keine automatische Wiederholung.')
