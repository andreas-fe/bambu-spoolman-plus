"""Read-only background archive inspection and single-use reviewed print starts."""
import json
import secrets
import tempfile
import threading
import time
from flask import request,jsonify
from .storage_metadata import inspect_archive,MAX_ARCHIVE


def register_actions(app,storage,decode,cache,refresh_idle,blocked,handlers):
    jobs={};reviews={};lock=threading.Lock()
    with cache.lock:
        cache.db.execute('CREATE TABLE IF NOT EXISTS storage_metadata(key TEXT PRIMARY KEY,data TEXT,created REAL)');cache.db.commit()
    def cached(entry):
        with cache.lock:
            row=cache.db.execute('SELECT data FROM storage_metadata WHERE key=? AND created>?',(cache.key(entry),time.time()-30*86400)).fetchone()
        return json.loads(row[0]) if row else None
    def worker(ticket,entry):
        job=jobs[ticket]
        try:
            if not storage.lock.acquire(False):raise ValueError('Dateizugriff läuft bereits. Bitte erneut laden.')
            try:
                if not refresh_idle():raise ValueError('Dateidetails bitte im bestätigten Leerlauf laden.')
                if not 0<entry['size']<=MAX_ARCHIVE:raise ValueError('Dateidetails unterstützen Druckarchive bis 128 MiB.')
                with storage.connection() as ftp,tempfile.TemporaryFile() as data:
                    storage.checked(ftp,entry);deadline=time.monotonic()+240;next_idle=time.monotonic()+15
                    def write(block):
                        nonlocal next_idle
                        if time.monotonic()>deadline:raise ValueError('Zeitlimit beim Laden der Dateidetails erreicht.')
                        if time.monotonic()>next_idle:
                            if not refresh_idle():raise ValueError('Drucker nicht mehr im Leerlauf. Übertragung gestoppt.')
                            next_idle=time.monotonic()+15
                        if data.tell()+len(block)>min(MAX_ARCHIVE,entry['size']):raise ValueError('Dateigröße geändert oder Größenlimit erreicht.')
                        data.write(block);job['received']=data.tell()
                    ftp.retrbinary('RETR '+entry['path'],write,blocksize=65536)
                    if data.tell()!=entry['size']:raise ValueError('Druckarchiv unvollständig übertragen.')
                    storage.checked(ftp,entry);data.seek(0);result=inspect_archive(data)
                with cache.lock:
                    cache.db.execute('INSERT OR REPLACE INTO storage_metadata VALUES (?,?,?)',(cache.key(entry),json.dumps(result),time.time()))
                    cache.db.execute('DELETE FROM storage_metadata WHERE created<?',(time.time()-30*86400,))
                    while True:
                        count,size=cache.db.execute('SELECT COUNT(*),COALESCE(SUM(length(data)),0) FROM storage_metadata').fetchone()
                        if count<=100 and size<=16*1024*1024:break
                        cache.db.execute('DELETE FROM storage_metadata WHERE key=(SELECT key FROM storage_metadata ORDER BY created LIMIT 1)')
                    cache.db.commit()
                job.update(state='done',metadata=result)
            finally:storage.lock.release()
        except ValueError as e:job.update(state='error',error=str(e))
        except Exception:job.update(state='error',error='Dateidetails konnten nicht gelesen werden. Verbindung und geslicte 3MF prüfen.')
    @app.post('/storage/details')
    def storage_details():
        try:
            entry=decode(request.form.get('file'))
            if not entry['name'].lower().endswith('.3mf'):raise ValueError('Detaillierter Druckstart benötigt eine geslicte 3MF-Datei.')
            data=cached(entry)
            if data:return jsonify(state='done',metadata=data)
            with lock:
                for key in list(jobs):
                    if time.monotonic()-jobs[key]['created']>900:jobs.pop(key)
                key=cache.key(entry)
                old=next((t for t,j in jobs.items() if j['key']==key and j['state']=='loading'),None)
                if old:return jsonify(state='loading',ticket=old),202
                if any(j['state']=='loading' for j in jobs.values()):raise ValueError('Eine Datei wird bereits untersucht. Bitte kurz warten.')
                ticket=secrets.token_urlsafe(24);jobs[ticket]=dict(state='loading',key=key,created=time.monotonic(),received=0,total=entry['size'])
                threading.Thread(target=worker,args=(ticket,entry),daemon=True).start()
            return jsonify(state='loading',ticket=ticket),202
        except ValueError as e:return jsonify(error=str(e)),400
    @app.get('/api/storage/details')
    def details_progress():
        with lock:job=jobs.get(request.args.get('ticket'))
        if not job or time.monotonic()-job['created']>900:return jsonify(error='Dateiprüfung abgelaufen. Bitte erneut öffnen.'),404
        return jsonify(state=job['state'],received=job.get('received',0),total=job.get('total'),metadata=job.get('metadata'),error=job.get('error'))
    @app.get('/api/storage/history')
    def storage_history():
        try:
            entry=decode(request.args.get('file'));return jsonify(entries=handlers['history'](entry['name']) if handlers else [])
        except ValueError as e:return jsonify(error=str(e)),400
    @app.get('/api/storage/start-context')
    def start_context():
        if not handlers:return jsonify(error='Druckstart nicht eingerichtet.'),503
        try:return jsonify(handlers['context']())
        except ValueError as e:return jsonify(error=str(e)),409
    @app.post('/storage/start-review')
    def start_review():
        try:
            if not handlers:raise ValueError('Druckstart nicht eingerichtet.')
            entry=decode(request.form.get('file'));metadata=cached(entry)
            if metadata is None:raise ValueError('Zuerst Dateidetails vollständig laden.')
            choice=json.loads(request.form.get('choice','null'))
            result=handlers['prepare'](entry,metadata,choice)
            with lock:
                for k in list(reviews):
                    if time.monotonic()-reviews[k]['created']>120:reviews.pop(k)
                if len(reviews)>=20:raise ValueError('Zu viele offene Startprüfungen.')
                token=secrets.token_urlsafe(32);reviews[token]=dict(entry=entry,metadata=metadata,choice=choice,result=result,created=time.monotonic())
            return jsonify(token=token,summary=result['summary'],expires_in=120)
        except ValueError as e:return jsonify(error=str(e)),409
        except (TypeError,KeyError):return jsonify(error='Startauswahl ungültig. Angaben und aktuellen Status prüfen.'),409
    @app.post('/storage/start')
    def storage_start():
        if request.form.get('confirm')!='start-print' or request.form.get('bed_clear')!='yes':return jsonify(error='Druckstart und freie Druckplatte ausdrücklich bestätigen.'),400
        with lock:review=reviews.pop(request.form.get('review'),None)
        if not review or time.monotonic()-review['created']>120:return jsonify(error='Startprüfung abgelaufen oder bereits verwendet. Neu prüfen.'),409
        if not storage.lock.acquire(False):return jsonify(error='Dateizugriff läuft bereits. Start bitte neu prüfen.'),409
        try:
            if blocked(review['entry']['name']):raise ValueError('Datei wird noch von einem offenen Auftrag benötigt.')
            with storage.connection() as ftp:storage.checked(ftp,review['entry'])
            result=handlers['start'](review)
            return jsonify(result)
        except ValueError as e:return jsonify(error=str(e)),409
        except Exception:return jsonify(error='Druckstart nicht eindeutig bestätigt. Druckerstatus prüfen; nicht ungeprüft erneut starten.'),503
        finally:storage.lock.release()
