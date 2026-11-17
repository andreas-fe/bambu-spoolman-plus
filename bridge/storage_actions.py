"""Read-only background archive inspection and single-use reviewed print starts."""
import json
import secrets
import socket
import ssl
import tempfile
import threading
import time
from flask import request,jsonify
from .storage_metadata import inspect_archive,MAX_ARCHIVE,METADATA_VERSION


def register_actions(app,storage,decode,cache,refresh_idle,blocked,handlers,history_names=None):
    jobs={};reviews={};cancelled_views={};lock=threading.Lock()
    with cache.lock:
        cache.db.execute('CREATE TABLE IF NOT EXISTS storage_metadata(key TEXT PRIMARY KEY,data TEXT,created REAL)');cache.db.commit()
    def cached(entry):
        with cache.lock:
            row=cache.db.execute('SELECT data FROM storage_metadata WHERE key=? AND created>?',(cache.key(entry),time.time()-30*86400)).fetchone()
        data=json.loads(row[0]) if row else None
        return data if data and data.get('metadata_version')==METADATA_VERSION else None
    class Cancelled(Exception):pass
    def check(job):
        if job['_cancel'].is_set():raise Cancelled()
    def interrupt(job):
        job['_cancel'].set()
        # Shut down the sockets, not the buffered FTP file object: closing that
        # object from another thread could wait for the reader's I/O lock.
        for stream in (job.get('_stream'),getattr(job.get('_ftp'),'sock',None)):
            if stream:
                try:stream.shutdown(socket.SHUT_RDWR)
                except (OSError,ValueError):pass
                try:stream.close()
                except OSError:pass
    def worker(ticket,entry):
        job=jobs[ticket];ftp=None;draft=None;acquired=False
        try:
            check(job)
            if not refresh_idle():raise ValueError('Dateidetails bitte im bestätigten Leerlauf laden.')
            check(job)
            if not 0<entry['size']<=MAX_ARCHIVE:raise ValueError('Dateidetails unterstützen Druckarchive bis 128 MiB.')
            # Status refresh and connection setup must not own the filesystem lock.
            ftp=storage.factory();job['_ftp']=ftp;check(job)
            until=time.monotonic()+5
            while not storage.lock.acquire(timeout=.05):
                check(job)
                if time.monotonic()>until:raise ValueError('Dateizugriff läuft bereits. Bitte erneut laden.')
            acquired=True;check(job)
            with tempfile.TemporaryFile() as data:
                storage.checked(ftp,entry);check(job)
                deadline=time.monotonic()+240;next_idle=time.monotonic()+15
                from .metadata_draft import DraftCollector
                draft=DraftCollector(lambda result:job.update(draft_metadata=result))
                ftp.voidcmd('TYPE I');check(job)
                with ftp.transfercmd('RETR '+entry['path']) as stream:
                    job['_stream']=stream;check(job)
                    while True:
                        check(job)
                        block=stream.recv(65536)
                        if not block:break
                        check(job)
                        if time.monotonic()>deadline:raise ValueError('Zeitlimit beim Laden der Dateidetails erreicht.')
                        if time.monotonic()>next_idle:
                            if not refresh_idle():raise ValueError('Drucker nicht mehr im Leerlauf. Übertragung gestoppt.')
                            next_idle=time.monotonic()+15;check(job)
                        if data.tell()+len(block)>min(MAX_ARCHIVE,entry['size']):raise ValueError('Dateigröße geändert oder Größenlimit erreicht.')
                        data.write(block);job['received']=data.tell();draft.feed(block)
                    check(job)
                    if isinstance(stream,ssl.SSLSocket):stream.unwrap()
                    job.pop('_stream',None)
                check(job);ftp.voidresp();draft.close();draft=None;check(job)
                if data.tell()!=entry['size']:raise ValueError('Druckarchiv unvollständig übertragen.')
                storage.checked(ftp,entry);check(job);data.seek(0);result=inspect_archive(data);check(job)
            with lock:
                check(job)
                with cache.lock:
                    cache.db.execute('INSERT OR REPLACE INTO storage_metadata VALUES (?,?,?)',(cache.key(entry),json.dumps(result),time.time()))
                    cache.db.execute('DELETE FROM storage_metadata WHERE created<?',(time.time()-30*86400,))
                    while True:
                        count,size=cache.db.execute('SELECT COUNT(*),COALESCE(SUM(length(data)),0) FROM storage_metadata').fetchone()
                        if count<=100 and size<=16*1024*1024:break
                        cache.db.execute('DELETE FROM storage_metadata WHERE key=(SELECT key FROM storage_metadata ORDER BY created LIMIT 1)')
                    cache.db.commit()
                job.update(state='done',metadata=result)
        except Cancelled:job.update(state='cancelled')
        except ValueError as e:job.update(state='cancelled' if job['_cancel'].is_set() else 'error',error=str(e))
        except Exception:job.update(state='cancelled' if job['_cancel'].is_set() else 'error',error='Dateidetails konnten nicht gelesen werden. Verbindung und geslicte 3MF prüfen.')
        finally:
            if draft:
                if job['_cancel'].is_set():draft.abort()
                else:draft.close()
            if ftp:
                try:ftp.close()
                except OSError:pass
            job.pop('_stream',None);job.pop('_ftp',None)
            if acquired:storage.lock.release()
            job['_stopped'].set()
    @app.post('/storage/details/cancel')
    def cancel_details():
        viewer=request.form.get('viewer','');ticket=request.form.get('ticket')
        if len(viewer)>128:return jsonify(error='Ungültige Dialogkennung.'),400
        try:
            entry=decode(request.form.get('file')) if request.form.get('file') else None
            key=cache.key(entry) if entry else None
            with lock:
                if viewer:
                    for old in list(cancelled_views):
                        if time.monotonic()-cancelled_views[old]>900:cancelled_views.pop(old)
                    if len(cancelled_views)>=256:cancelled_views.pop(next(iter(cancelled_views)))
                    cancelled_views[viewer]=time.monotonic()
                selected=[j for t,j in jobs.items() if (t==ticket or key and j['key']==key) and j['state']=='loading']
                stop=[]
                for job in selected:
                    if viewer:job['_viewers'].discard(viewer)
                    if not viewer or not job['_viewers']:
                        job['_cancel'].set();stop.append(job)
            for job in stop:interrupt(job)
            return jsonify(state='cancelled')
        except ValueError as e:return jsonify(error=str(e)),400
    @app.post('/storage/details')
    def storage_details():
        try:
            entry=decode(request.form.get('file'))
            viewer=request.form.get('viewer','')
            if len(viewer)>128:raise ValueError('Ungültige Dialogkennung.')
            if viewer and viewer in cancelled_views:return jsonify(state='cancelled')
            if not entry['name'].lower().endswith('.3mf'):raise ValueError('Detaillierter Druckstart benötigt eine geslicte 3MF-Datei.')
            data=cached(entry)
            if data:return jsonify(state='done',metadata=data)
            with lock:
                for key in list(jobs):
                    if time.monotonic()-jobs[key]['created']>900:jobs.pop(key)
                if viewer and viewer in cancelled_views:return jsonify(state='cancelled')
                key=cache.key(entry)
                old=next((t for t,j in jobs.items() if j['key']==key and j['state']=='loading' and not j['_cancel'].is_set()),None)
                if old:
                    jobs[old]['_viewers'].add(viewer)
                    return jsonify(state='loading',ticket=old),202
                if any(j['state']=='loading' and not j['_cancel'].is_set() for j in jobs.values()):raise ValueError('Eine Datei wird bereits untersucht. Bitte kurz warten.')
                ticket=secrets.token_urlsafe(24);jobs[ticket]=dict(state='loading',key=key,created=time.monotonic(),received=0,total=entry['size'],_cancel=threading.Event(),_stopped=threading.Event(),_viewers={viewer})
                threading.Thread(target=worker,args=(ticket,entry),daemon=True).start()
            return jsonify(state='loading',ticket=ticket),202
        except ValueError as e:return jsonify(error=str(e)),400
    @app.get('/api/storage/details')
    def details_progress():
        with lock:job=jobs.get(request.args.get('ticket'))
        if not job or time.monotonic()-job['created']>900:return jsonify(error='Dateiprüfung abgelaufen. Bitte erneut öffnen.'),404
        return jsonify(state=job['state'],received=job.get('received',0),total=job.get('total'),metadata=job.get('metadata'),draft_metadata=job.get('draft_metadata'),error=job.get('error'))
    @app.get('/api/storage/history')
    def storage_history():
        try:
            entry=decode(request.args.get('file'))
            names=history_names(entry) if history_names else [entry['name']]
            rows={row.get('id',json.dumps(row,sort_keys=True)):row for name in names for row in handlers['history'](name)} if handlers else {}
            return jsonify(entries=sorted(rows.values(),key=lambda r:r.get('created_at',''),reverse=True)[:30])
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
