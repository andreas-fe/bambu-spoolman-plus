"""Bounded, explicit SD file management over the existing printer FTPS client."""
import io
import re
import threading
import time
import zipfile
from contextlib import contextmanager
from pathlib import PurePosixPath
from flask import jsonify,request,session,Response,url_for
from itsdangerous import URLSafeTimedSerializer,BadSignature
from .ui import render_ui
from .preview_cache import PreviewCache

FOLDERS=('/', '/cache', '/model')
EXTENSIONS=('.3mf','.gcode')
MAX_PREVIEW_ARCHIVE=24*1024*1024
MAX_IMAGE=2*1024*1024


def safe_file(folder,name):
    if folder not in FOLDERS or not name or name in ('.','..') or any(c in name for c in '/\\\r\n\x00') or not name.lower().endswith(EXTENSIONS):
        raise ValueError('Ungültige Druckdatei.')
    return folder.rstrip('/')+'/'+name


def parse_listing(lines,folder):
    rows=[]
    for line in lines:
        parts=line.split(None,8)
        if len(parts)!=9 or not parts[0].startswith('-'):continue
        try:
            path=safe_file(folder,parts[8]);size=int(parts[4])
            if size<0:continue
        except (ValueError,TypeError):continue
        rows.append(dict(path=path,name=parts[8],size=size,modified=' '.join(parts[5:8])))
    return sorted(rows,key=lambda r:r['name'].casefold())


def thumbnail(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        images=[i for i in archive.infolist() if i.filename.lower().startswith(('metadata/','auxiliaries/')) and i.filename.lower().endswith(('.png','.jpg','.jpeg')) and ('thumbnail' in i.filename.lower() or re.fullmatch(r'metadata/plate_[0-9]+(?:_small)?\.png',i.filename.lower()))]
        images.sort(key=lambda i:(i.filename.lower()!='metadata/plate_1.png','thumbnail.png' not in i.filename.lower(),i.file_size))
        for info in images:
            if not 0<info.file_size<=MAX_IMAGE or info.file_size>max(1,info.compress_size)*200:continue
            image=archive.read(info)
            if image.startswith(b'\x89PNG\r\n\x1a\n'):return image,'image/png'
            if image.startswith(b'\xff\xd8\xff'):return image,'image/jpeg'
    raise ValueError('Die Datei enthält kein unterstütztes Vorschaubild. Es wird kein 3D-Modell neu berechnet.')


class Storage:
    def __init__(self,factory):self.factory=factory;self.lock=threading.Lock()
    @contextmanager
    def connection(self):
        ftp=self.factory()
        try:yield ftp
        finally:
            try:ftp.quit()
            except Exception:ftp.close()
    def listing(self,ftp,folder):
        if folder not in FOLDERS:raise ValueError('Unbekannter Speicherbereich.')
        lines=[]
        def line(value):
            if len(lines)>=5000:raise ValueError('Zu viele Dateien. Liste auf dem Drucker eingrenzen.')
            lines.append(value)
        ftp.retrlines('LIST '+folder,line)
        return parse_listing(lines,folder)
    def checked(self,ftp,entry):
        folder=str(PurePosixPath(entry['path']).parent)
        safe_file(folder,entry['name'])
        actual=next((r for r in self.listing(ftp,folder) if r['path']==entry['path']),None)
        if actual!=entry:raise ValueError('Datei wurde geändert oder entfernt. Liste neu laden.')
        return actual


def register_storage(app,factory,blocked,refresh_idle,log,cache_path=None,namespace=""):
    cache=PreviewCache(cache_path,namespace)
    storage=Storage(factory)
    signer=lambda:URLSafeTimedSerializer(app.secret_key,salt='printer-file-v1')
    def decode(token):
        if not isinstance(token,str) or len(token)>2048:raise ValueError('Ungültige Dateiauswahl.')
        try:entry=signer().loads(token,max_age=900)
        except BadSignature:raise ValueError('Dateiauswahl abgelaufen. Liste neu laden.')
        if not isinstance(entry,dict) or set(entry)!=set(('path','name','size','modified')):raise ValueError('Ungültige Dateiauswahl.')
        folder=str(PurePosixPath(entry['path']).parent)
        if safe_file(folder,entry['name'])!=entry['path']:raise ValueError('Ungültiger Dateipfad.')
        return entry
    @app.get('/storage')
    def storage_page():
        import secrets
        session.setdefault('csrf_token',secrets.token_urlsafe(32))
        return render_ui(PAGE,active='storage',title='Druckerspeicher',token=session['csrf_token'])
    @app.get('/api/storage')
    def storage_list():
        if not storage.lock.acquire(False):return jsonify(error='Dateizugriff läuft bereits. Bitte kurz warten.'),409
        try:
            with storage.connection() as ftp:rows=storage.listing(ftp,request.args.get('folder','/'))
            for row in rows:
                entry=dict(row);row['token']=signer().dumps(entry);row['blocked']=blocked(row['name'])
                row['preview_url']=url_for('cached_storage_preview',file=row['token']) if cache.contains(entry) else None
            return jsonify(files=rows)
        except ValueError as exc:return jsonify(error=str(exc)),400
        except Exception:return jsonify(error='Druckerspeicher nicht erreichbar. LAN-Zugang und Speicherkarte prüfen.'),503
        finally:storage.lock.release()
    @app.get('/storage/thumbnail')
    def cached_storage_preview():
        try:
            entry=decode(request.args.get('file'));image=cache.get(entry)
            if not image:return jsonify(error='Vorschaubild noch nicht im App-Cache.'),404
            return Response(image[0],mimetype=image[1],headers={'Cache-Control':'private, max-age=300','X-Content-Type-Options':'nosniff'})
        except ValueError as exc:return jsonify(error=str(exc)),400
    @app.post('/storage/preview-cache/clear')
    def clear_storage_preview_cache():
        if request.form.get('confirm')!='clear-previews':return jsonify(error='Bildcache-Leerung bestätigen.'),400
        cache.clear()
        return jsonify(message='App-Bildcache geleert. Druckerdateien, Buchungen und Bestand unverändert.')
    @app.post('/storage/preview')
    def preview():
        try:
            entry=decode(request.form.get('file'));hit=cache.get(entry)
            if hit:return Response(hit[0],mimetype=hit[1],headers={'Cache-Control':'no-store','X-Preview-Cache':'hit','X-Content-Type-Options':'nosniff'})
        except ValueError as exc:return jsonify(error=str(exc)),400
        if not storage.lock.acquire(False):return jsonify(error='Dateizugriff läuft bereits.'),409
        try:
            entry=decode(request.form.get('file'))
            if not refresh_idle():return jsonify(error='Vorschauen bitte im bestätigten Leerlauf laden.'),409
            if not entry['name'].lower().endswith('.3mf'):raise ValueError('Für reine G-Code-Dateien liegt keine Modellvorschau vor.')
            if entry['size']>MAX_PREVIEW_ARCHIVE:raise ValueError('Datei für diese Vorschau zu groß (maximal 24 MiB).')
            with storage.connection() as ftp:
                storage.checked(ftp,entry);data=io.BytesIO();deadline=time.monotonic()+25
                def write(chunk):
                    if time.monotonic()>deadline:raise ValueError('Vorschau dauert zu lange. Erneut im Leerlauf versuchen.')
                    if data.tell()+len(chunk)>MAX_PREVIEW_ARCHIVE:raise ValueError('Vorschaudatei überschreitet die Größenbegrenzung.')
                    data.write(chunk)
                ftp.retrbinary('RETR '+entry['path'],write)
                if data.tell()!=entry['size']:raise ValueError('Datei nicht vollständig gelesen. Erneut versuchen.')
            image,mime=thumbnail(data.getvalue());cache.put(entry,image,mime)
            return Response(image,mimetype=mime,headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
        except ValueError as exc:return jsonify(error=str(exc)),400
        except Exception:return jsonify(error='Vorschau nicht verfügbar. Datei enthält eventuell kein passendes Vorschaubild.'),503
        finally:storage.lock.release()
    @app.post('/storage/delete')
    def delete():
        if request.form.get('confirm')!='delete-selected':return jsonify(error='Löschen ausdrücklich bestätigen.'),400
        tokens=request.form.getlist('files')
        if not 1<=len(tokens)<=50:return jsonify(error='Bitte 1 bis 50 Dateien auswählen.'),400
        if not storage.lock.acquire(False):return jsonify(error='Dateizugriff läuft bereits.'),409
        deleted=[];deadline=time.monotonic()+25
        def timely():
            if time.monotonic()>deadline:raise ValueError('Zeitlimit erreicht. Löschvorgang gestoppt; Dateiliste aktualisieren.')
        try:
            entries=[decode(t) for t in tokens]
            if len({e['path'] for e in entries})!=len(entries):raise ValueError('Doppelte Dateiauswahl.')
            with storage.connection() as ftp:
                # Validate the entire selection before the first irreversible command.
                for entry in entries:
                    timely()
                    storage.checked(ftp,entry)
                    if blocked(entry['name']):raise ValueError('Datei wird noch von einem laufenden oder offenen Auftrag benötigt: '+entry['name'])
                for entry in entries:
                    timely()
                    if not refresh_idle() or blocked(entry['name']):raise ValueError('Drucker oder Auftragsstatus geändert. Löschen gestoppt.')
                    storage.checked(ftp,entry)
                    timely()
                    ftp.delete(entry['path']);deleted.append(entry['name']);cache.remove(entry)
            log('Druckerspeicher: '+str(len(deleted))+' ausdrücklich ausgewählte Datei(en) gelöscht; Buchungshistorie bleibt erhalten.')
            return jsonify(deleted=deleted,message='Ausgewählte Dateien gelöscht.')
        except ValueError as exc:return jsonify(error=str(exc),deleted=deleted),409
        except Exception:return jsonify(error='Löschvorgang nicht vollständig bestätigt. Liste neu laden; nicht ungeprüft wiederholen.',deleted=deleted),503
        finally:storage.lock.release()
    return storage

PAGE=r'''
<div class="page-heading"><div><div class="eyebrow">Dein Drucker</div><h1>Druckerspeicher</h1><p>Druckdateien finden, Vorschau ansehen und gezielt aufräumen.</p></div><button id="refresh-files" onclick="loadFiles()">Dateien aktualisieren</button></div>
<section class="panel"><div class="storage-tools"><label>Speicherbereich<select id="file-folder" onchange="loadFiles()"><option value="/">Hauptverzeichnis</option><option value="/cache">Druckcache</option><option value="/model">Modelle</option></select></label><label>Datei suchen<input id="file-search" type="search" placeholder="Dateiname …" oninput="renderFiles()"></label><label>Sortieren<select id="file-sort" onchange="renderFiles()"><option value="name">Name A–Z</option><option value="size">Größte zuerst</option><option value="modified">Datum (Angabe des Druckers)</option></select></label></div><p class="muted small">Nur Druckdateien (.3mf / .gcode). Datum entspricht der Druckerangabe, ggf. ohne Jahr. Vorschauen werden einzeln im Leerlauf geladen; keine interaktive 3D-Neuberechnung.</p><label style="flex-direction:row;align-items:center"><input id="auto-previews" type="checkbox" onchange="queueVisiblePreviews()">Fehlende Bilder im sichtbaren Bereich automatisch laden (nur im Leerlauf)</label><button class="quiet" onclick="clearImageCache()">App-Bildcache leeren …</button><p class="muted small">Bereits geladene Bilder werden bis zu 30 Tage gespeichert (max. 200 Bilder / 64 MiB). Druckcache und App-Bildcache sind getrennt.</p><p id="file-status" role="status" aria-live="polite"></p><p id="file-count" class="muted small"></p><div class="scroll"><table><thead><tr><th>Auswahl</th><th>Datei</th><th>Größe</th><th>Datum</th><th>Vorschau / Status</th></tr></thead><tbody id="file-rows"></tbody></table></div><button type="button" class="danger" id="delete-files" onclick="reviewDelete()" disabled>Auswahl löschen …</button></section>
<section id="delete-review" class="panel" hidden><h2>Diese Dateien endgültig löschen?</h2><ul id="delete-names"></ul><p>Gelöscht werden die Dateien auf dem Drucker. Buchungsjournal und Filamentbestände bleiben erhalten.</p><label style="flex-direction:row;align-items:center"><input id="delete-ack" type="checkbox">Ich möchte genau diese Dateien endgültig löschen.</label><div class="actions"><button class="danger" id="delete-confirm" onclick="deleteFiles()">Endgültig löschen</button><button class="secondary" onclick="document.getElementById('delete-review').hidden=true">Abbrechen</button></div></section>
<dialog id="preview-dialog"><h2 id="preview-name"></h2><img id="preview-image" alt="Im Druckarchiv gespeicherte Modellvorschau" style="display:block;max-width:100%;max-height:65vh;margin:auto"><button onclick="closePreview()">Schließen</button></dialog>
<style>.file-preview{width:96px;height:72px;object-fit:contain;background:#111c29;border:1px solid var(--line);border-radius:8px}.file-preview-controls{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.storage-tools{display:grid;grid-template-columns:1fr 2fr 1fr;gap:16px}#preview-dialog{color:var(--text);background:var(--surface);border:1px solid var(--line);border-radius:16px;width:min(700px,95vw)}#preview-dialog::backdrop{background:#000a}#file-rows td:nth-child(2){overflow-wrap:anywhere;max-width:400px}@media(max-width:700px){.storage-tools{grid-template-columns:1fr}}</style>
<script>
const attemptedPreviews=new Set();let previewObserver=null;let files=[],selected=new Set(),reviewed=[],busy=false,previewURL=null;
const csrf={{token|tojson}},byId=id=>document.getElementById(id),sizeLabel=n=>n<1048576?(n/1024).toFixed(1)+' KiB':(n/1048576).toFixed(1)+' MiB';
function statusFiles(text){byId('file-status').textContent=text}
function setFileBusy(value){busy=value;for(const n of document.querySelectorAll('#refresh-files,#file-folder,#file-sort,#file-search,#file-rows button,#file-rows input,#delete-confirm'))n.disabled=value;byId('delete-files').disabled=value||!selected.size;}
async function fileFetch(path,options={}){const c=new AbortController(),timer=setTimeout(()=>c.abort(),45000);try{return await fetch(path,{...options,signal:c.signal})}finally{clearTimeout(timer)}}
async function loadFiles(){if(busy)return;setFileBusy(true);byId('delete-review').hidden=true;selected.clear();files=[];renderFiles();statusFiles('Druckerspeicher wird gelesen …');try{const r=await fileFetch('/api/storage?folder='+encodeURIComponent(byId('file-folder').value)),data=await r.json();if(!r.ok)throw Error(data.error);files=data.files;statusFiles('Dateiliste aktualisiert.')}catch(e){statusFiles(e.name==='AbortError'?'Abfrage abgelaufen. Bitte erneut versuchen.':e.message)}finally{setFileBusy(false);renderFiles()}}
function fileDate(text){const year=new Date().getFullYear();let date=new Date(text+(text.includes(':')?' '+year:''));if(text.includes(':')&&date>Date.now()+172800000)date.setFullYear(year-1);return date.getTime()||0}
function renderFiles(){const query=byId('file-search').value.toLocaleLowerCase(),rows=files.filter(f=>f.name.toLocaleLowerCase().includes(query));rows.sort((a,b)=>byId('file-sort').value==='size'?b.size-a.size:byId('file-sort').value==='modified'?fileDate(b.modified)-fileDate(a.modified):a.name.localeCompare(b.name));const body=byId('file-rows');body.replaceChildren();for(const file of rows){const tr=document.createElement('tr'),pick=document.createElement('td'),checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.checked=selected.has(file.token);checkbox.disabled=busy||!!file.blocked;checkbox.setAttribute('aria-label',file.name+' auswählen');checkbox.onchange=()=>{checkbox.checked?selected.add(file.token):selected.delete(file.token);byId('delete-review').hidden=true;byId('delete-files').disabled=busy||!selected.size};pick.append(checkbox);tr.append(pick);for(const value of [file.name,sizeLabel(file.size),file.modified]){const td=document.createElement('td');td.textContent=value;tr.append(td)}const action=document.createElement('td');action.className='file-preview-controls';action.dataset.fileToken=file.token;if(file.blocked){const hint=document.createElement('p');hint.textContent=file.blocked;hint.className='muted small';action.append(hint)}if(file.name.toLowerCase().endsWith('.3mf')){if(file.preview_url){const image=document.createElement('img');image.className='file-preview';image.loading='lazy';image.src=file.preview_url;image.alt='Modellvorschau: '+file.name;image.onclick=()=>previewFile(file);action.append(image)}const button=document.createElement('button');button.className='secondary';button.textContent=file.preview_url?'Vorschau öffnen':'Vorschau laden';button.disabled=busy;button.onclick=()=>previewFile(file);action.append(button)}else{const text=document.createElement('span');text.textContent='Keine Modellvorschau in reinem G-Code';text.className='muted small';action.append(text)}tr.append(action);body.append(tr)}byId('file-count').textContent=rows.length+' von '+files.length+' Dateien · '+selected.size+' ausgewählt';if(!rows.length){const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=5;td.textContent='Keine passenden Druckdateien in diesem Bereich.';tr.append(td);body.append(tr)}queueVisiblePreviews()}
async function previewFile(file,open=true){if(busy)return;setFileBusy(true);statusFiles('Vorschau wird geladen …');try{const r=await fileFetch('/storage/preview',{method:'POST',body:new URLSearchParams({file:file.token,csrf_token:csrf})});if(!r.ok){if(r.status===409||r.status===503)byId('auto-previews').checked=false;const d=await r.json();throw Error(d.error)}const blob=await r.blob();file.preview_url='/storage/thumbnail?file='+encodeURIComponent(file.token);if(open){closePreview();previewURL=URL.createObjectURL(blob);byId('preview-image').src=previewURL;byId('preview-name').textContent=file.name;byId('preview-dialog').showModal()}statusFiles('Modellvorschau im App-Cache gespeichert.')}catch(e){statusFiles(e.message)}finally{setFileBusy(false);renderFiles()}}
function queueVisiblePreviews(){if(previewObserver)previewObserver.disconnect();if(!byId('auto-previews').checked||busy)return;previewObserver=new IntersectionObserver(entries=>{for(const e of entries){if(!e.isIntersecting||busy)continue;const file=files.find(f=>f.token===e.target.dataset.fileToken);if(!file||file.preview_url||!file.name.toLowerCase().endsWith('.3mf'))continue;const key=[file.path,file.size,file.modified].join('|');if(attemptedPreviews.has(key))continue;attemptedPreviews.add(key);previewFile(file,false);break}});document.querySelectorAll('[data-file-token]').forEach(node=>previewObserver.observe(node))}
async function clearImageCache(){if(busy||!confirm('Nur den App-Bildcache leeren? Druckerdateien und Buchungen bleiben erhalten.'))return;byId('auto-previews').checked=false;setFileBusy(true);try{const r=await fileFetch('/storage/preview-cache/clear',{method:'POST',body:new URLSearchParams({csrf_token:csrf,confirm:'clear-previews'})}),d=await r.json();if(!r.ok)throw Error(d.error);closePreview();attemptedPreviews.clear();setFileBusy(false);await loadFiles();statusFiles(d.message)}catch(e){statusFiles(e.message)}finally{setFileBusy(false)}}
function closePreview(){byId('preview-dialog').close();byId('preview-image').removeAttribute('src');if(previewURL)URL.revokeObjectURL(previewURL);previewURL=null}
byId('preview-dialog').addEventListener('cancel',closePreview);
function reviewDelete(){if(busy)return;reviewed=files.filter(f=>selected.has(f.token)&&!f.blocked);const list=byId('delete-names');list.replaceChildren();for(const file of reviewed){const li=document.createElement('li');li.textContent=file.name+' · '+sizeLabel(file.size);list.append(li)}byId('delete-ack').checked=false;byId('delete-review').hidden=false;byId('delete-review').scrollIntoView({behavior:'smooth'})}
async function deleteFiles(){if(busy||!reviewed.length)return;let outcome='';if(!byId('delete-ack').checked){statusFiles('Bitte die endgültige Löschung der aufgeführten Dateien bestätigen.');return}setFileBusy(true);try{const body=new URLSearchParams({confirm:'delete-selected',csrf_token:csrf});reviewed.forEach(f=>body.append('files',f.token));const r=await fileFetch('/storage/delete',{method:'POST',body});const d=await r.json();if(!r.ok)throw Error((d.error||'Löschen fehlgeschlagen.')+(d.deleted?.length?' Bereits gelöscht: '+d.deleted.length+'.':''));byId('delete-review').hidden=true;outcome=d.message}catch(e){outcome=e.message+' Dateiliste wurde erneut gelesen.'}finally{setFileBusy(false);selected.clear();byId('delete-review').hidden=true;await loadFiles();statusFiles(outcome)}}
loadFiles();
</script>
'''
