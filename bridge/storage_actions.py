"""Read-only background archive inspection and single-use reviewed print starts."""
import json
import gzip
import secrets
import socket
import ssl
import tempfile
import threading
import time
from flask import request,jsonify
from .storage_metadata import inspect_archive,MAX_ARCHIVE,METADATA_VERSION
from .model_preview import build_model,cached_model,save_model
from .archive_cache import ArchiveCache


def register_actions(app,storage,decode,cache,refresh_idle,blocked,handlers,history_names=None,read_state=None):
    jobs={};reviews={};cancelled_views={};lock=threading.Lock();archives=ArchiveCache()
    with cache.lock:
        cache.db.execute('CREATE TABLE IF NOT EXISTS storage_models(key TEXT PRIMARY KEY,path_key TEXT,data BLOB,created REAL)')
        cache.db.execute('CREATE TABLE IF NOT EXISTS storage_metadata(key TEXT PRIMARY KEY,data TEXT,created REAL)');cache.db.commit()
    @app.after_request
    def compress_storage_model(response):
        if request.path in ('/storage/model','/api/storage/details') and response.mimetype=='application/json':
            response.vary.add('Accept-Encoding')
            if request.accept_encodings['gzip'] and not response.headers.get('Content-Encoding'):
                raw=response.get_data()
                if len(raw)>256*1024:
                    response.set_data(gzip.compress(raw,compresslevel=1));response.headers['Content-Encoding']='gzip'
        return response
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
    def require_idle(job,continuing=False):
        check(job)
        state=read_state() if continuing and read_state else None
        if state=='idle':return
        if state in ('active','offline'):
            raise ValueError('Drucker meldet Aktivität. Übertragung gestoppt.' if state=='active' else 'Druckerverbindung unterbrochen. Übertragung gestoppt.')
        if refresh_idle():return
        state=read_state() if read_state else 'unconfirmed'
        if state=='idle':state='unconfirmed'  # New reads still require the strict fresh check.
        # Retry only absent confirmation. Known activity/offline must stop at once.
        if state=='unconfirmed':
            job['phase']='waiting_status';check(job)
            if refresh_idle():job['phase']='reading';return
            state=read_state() if read_state else 'unconfirmed'
        message={'active':'Drucker meldet Aktivität. Übertragung gestoppt.',
                 'offline':'Druckerverbindung unterbrochen. Übertragung gestoppt.'}.get(state,
                 'Frische Leerlaufbestätigung fehlt trotz erneuter Abfrage. Übertragung gestoppt; das bedeutet nicht, dass ein Druck läuft. Bitte erneut laden.')
        raise ValueError(message)

    def worker(ticket,entry):
        job=jobs[ticket];ftp=None;draft=None;acquired=False;plate=job.get('plate');data=None
        try:
            check(job)
            generation=archives.generation;key=cache.key(entry);data=archives.get(key)
            job['archive_reused']=data is not None
            if data is None:
                require_idle(job)
                if not 0<entry['size']<=MAX_ARCHIVE:raise ValueError('Dateidetails unterstützen Druckarchive bis 128 MiB.')
                ftp=storage.factory();job['_ftp']=ftp;check(job)
                until=time.monotonic()+5
                while not storage.lock.acquire(timeout=.05):
                    check(job)
                    if time.monotonic()>until:raise ValueError('Dateizugriff läuft bereits. Bitte erneut laden.')
                acquired=True;check(job);data=tempfile.TemporaryFile()
                storage.checked(ftp,entry);check(job)
                deadline=time.monotonic()+240;next_idle=time.monotonic()+15
                from .metadata_draft import DraftCollector
                if plate is None:draft=DraftCollector(lambda result:job.update(draft_metadata=result))
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
                            require_idle(job,continuing=True)
                            next_idle=time.monotonic()+15;check(job)
                        if data.tell()+len(block)>min(MAX_ARCHIVE,entry['size']):raise ValueError('Dateigröße geändert oder Größenlimit erreicht.')
                        data.write(block);job['received']=data.tell()
                        if draft:draft.feed(block)
                    check(job)
                    if isinstance(stream,ssl.SSLSocket):stream.unwrap()
                    job.pop('_stream',None)
                check(job);ftp.voidresp()
                if draft:draft.close();draft=None
                check(job)
                if data.tell()!=entry['size']:raise ValueError('Druckarchiv unvollständig übertragen.')
                storage.checked(ftp,entry);check(job);ftp.close();ftp=None;job.pop('_ftp',None);storage.lock.release();acquired=False
            else:job['received']=entry['size']
            job['phase']='metadata';data.seek(0);metadata=inspect_archive(data);check(job)
            if not job['archive_reused']:
                try:archives.put(key,data,lambda:check(job),generation)
                except OSError:pass  # Optional speedup; unavailable temp space must not fail inspection.
            if plate is not None:
                job['phase']='building';data.seek(0);result=build_model(data,plate,metadata,lambda:check(job))
            check(job)
            with lock:
                check(job)
                with cache.lock:
                    cache.db.execute('INSERT OR REPLACE INTO storage_metadata VALUES (?,?,?)',(cache.key(entry),json.dumps(metadata),time.time()))
                    cache.db.execute('DELETE FROM storage_metadata WHERE created<?',(time.time()-30*86400,))
                    while True:
                        count,size=cache.db.execute('SELECT COUNT(*),COALESCE(SUM(length(data)),0) FROM storage_metadata').fetchone()
                        if count<=100 and size<=16*1024*1024:break
                        cache.db.execute('DELETE FROM storage_metadata WHERE key=(SELECT key FROM storage_metadata ORDER BY created LIMIT 1)')
                    cache.db.commit()
                if plate is not None:
                    save_model(cache,entry,plate,result);job.update(state='done',model_entry=entry)
                else:job.update(state='done',metadata=metadata)
        except Cancelled:job.update(state='cancelled')
        except ValueError as e:job.update(state='cancelled' if job['_cancel'].is_set() else 'error',error=str(e))
        except Exception:job.update(state='cancelled' if job['_cancel'].is_set() else 'error',error='Dateidetails konnten nicht gelesen werden. Verbindung und geslicte 3MF prüfen.')
        finally:
            if data:data.close()
            if draft:
                if job['_cancel'].is_set():draft.abort()
                else:draft.close()
            if ftp:
                try:ftp.close()
                except OSError:pass
            job.pop('_stream',None);job.pop('_ftp',None)
            if acquired:storage.lock.release()
            job['_stopped'].set()

    def clear_inspections():
        with lock:
            running=[j for j in jobs.values() if j['state']=='loading']
            for job in running:job['_cancel'].set()
            archives.clear()
        for job in running:interrupt(job)

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
    @app.post('/storage/model')
    @app.post('/storage/details')
    def storage_details():
        try:
            entry=decode(request.form.get('file'))
            viewer=request.form.get('viewer','')
            if len(viewer)>128:raise ValueError('Ungültige Dialogkennung.')
            if viewer and viewer in cancelled_views:return jsonify(state='cancelled')
            if not entry['name'].lower().endswith('.3mf'):raise ValueError('Detaillierter Druckstart benötigt eine geslicte 3MF-Datei.')
            plate=None
            if request.path=='/storage/model':
                try:plate=int(request.form.get('plate',''))
                except ValueError:raise ValueError('Modellplatte für die 3D-Vorschau auswählen.')
                if not 1<=plate<=64:raise ValueError('Ungültige Modellplatte.')
            data=cached_model(cache,entry,plate) if plate is not None else cached(entry)
            if data:return jsonify(state='done',**({'model':data} if plate is not None else {'metadata':data}))
            with lock:
                for key in list(jobs):
                    if time.monotonic()-jobs[key]['created']>900:jobs.pop(key)
                if viewer and viewer in cancelled_views:return jsonify(state='cancelled')
                key=cache.key(entry)
                old=next((t for t,j in jobs.items() if j['key']==key and j.get('plate')==plate and j['state']=='loading' and not j['_cancel'].is_set()),None)
                if old:
                    jobs[old]['_viewers'].add(viewer)
                    return jsonify(state='loading',ticket=old),202
                completed=sorted((t for t,j in jobs.items() if j['state']!='loading'),key=lambda t:jobs[t]['created'])
                while len(jobs)>=32 and completed:jobs.pop(completed.pop(0),None)
                if any(j['state']=='loading' and not j['_cancel'].is_set() for j in jobs.values()):raise ValueError('Eine Datei wird bereits untersucht. Bitte kurz warten.')
                ticket=secrets.token_urlsafe(24);jobs[ticket]=dict(state='loading',key=key,plate=plate,created=time.monotonic(),received=0,total=entry['size'],_cancel=threading.Event(),_stopped=threading.Event(),_viewers={viewer})
                threading.Thread(target=worker,args=(ticket,entry),daemon=True).start()
            return jsonify(state='loading',ticket=ticket),202
        except ValueError as e:return jsonify(error=str(e)),400
    @app.get('/api/storage/details')
    def details_progress():
        with lock:job=jobs.get(request.args.get('ticket'))
        if not job or time.monotonic()-job['created']>900:return jsonify(error='Dateiprüfung abgelaufen. Bitte erneut öffnen.'),404
        model=cached_model(cache,job['model_entry'],job['plate']) if job['state']=='done' and 'model_entry' in job else None
        if job['state']=='done' and 'model_entry' in job and model is None:return jsonify(state='error',error='3D-Cache wurde geleert. Bitte Vorschau erneut laden.')
        return jsonify(state=job['state'],received=job.get('received',0),total=job.get('total'),metadata=job.get('metadata'),draft_metadata=job.get('draft_metadata'),model=model,archive_reused=job.get('archive_reused',False),phase=job.get('phase'),error=job.get('error'))
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
    def preflight_from_cached(file, plate, mapping=None):
        if not handlers or 'preflight' not in handlers:
            raise ValueError('Druckvorprüfung nicht verfügbar.')
        entry = decode(file)
        if not entry['name'].lower().endswith('.3mf'):
            raise ValueError('Nur geslicte 3MF-Dateien unterstützt.')
        metadata = cached(entry)
        if metadata is None:
            raise ValueError('Datei noch nicht vollständig untersucht. Erst Dateidetails in der Weboberfläche öffnen.')
        return handlers['preflight'](metadata, plate, mapping)

    app.extensions['assistant_preflight'] = preflight_from_cached

    @app.post('/storage/preflight')
    def storage_preflight():
        try:
            mapping = json.loads(request.form.get('mapping','{}'))
            plate = int(request.form.get('plate',''))
            return jsonify(preflight_from_cached(request.form.get('file'), plate, mapping))
        except (ValueError, KeyError, TypeError) as exc:
            return jsonify(error=str(exc)), 409

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

    return clear_inspections
