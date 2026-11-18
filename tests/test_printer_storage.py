import io,zipfile
import pytest
from flask import Flask
from bridge.printer_storage import safe_file,parse_listing,register_storage
from bridge.archive_preview import streamed_thumbnail


def test_paths_listing_and_thumbnail():
    for name in ['../a.3mf','a\nDELE b.3mf','a/b.3mf','config.ini','a\\b.gcode']:
        with pytest.raises(ValueError):safe_file('/cache',name)
    with pytest.raises(ValueError):safe_file('/etc','a.3mf')
    rows=parse_listing(['-rw-r--r-- 1 owner group 12 Nov 16 10:00 My file.3mf','drwxrwxrwx 1 a b 0 Nov 16 10:00 dir.3mf','lrwxrwxrwx 1 a b 5 Nov 16 10:00 link.3mf -> private'], '/cache')
    assert len(rows)==1 and rows[0]['path']=='/cache/My file.3mf'
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w') as z:z.writestr('Metadata/plate_1.png',b'\x89PNG\r\n\x1a\nexample')
    assert streamed_thumbnail(io.BytesIO(data.getvalue()).read)[1]=='image/png'
    with pytest.raises(Exception):streamed_thumbnail(io.BytesIO(b'invalid').read)


class FTPStream(io.BytesIO):
    def recv(self,size):return self.read(size)
    def shutdown(self,how):self.close()


class FTP:
    def __init__(self):self.deleted=[];self.closed=False;self.size=12
    def retrlines(self,cmd,callback):callback('-rw-r--r-- 1 a b '+str(self.size)+' Nov 16 10:00 Example.3mf')
    def voidcmd(self,command):pass
    def voidresp(self):pass
    def delete(self,path):self.deleted.append(path)
    def quit(self):self.closed=True
    def close(self):self.closed=True


def context():
    app=Flask(__name__);app.secret_key='test';ftp=FTP();blocked=[None];idle=[True]
    register_storage(app,lambda:ftp,lambda name:blocked[0],lambda:idle[0],lambda s:None)
    c=app.test_client();c.get('/storage')
    token=c.get('/api/storage?folder=/cache').json['files'][0]['token']
    return c,ftp,blocked,idle,token


def test_delete_rechecks_selection_idle_and_blocking():
    c,ftp,blocked,idle,token=context()
    payload=dict(files=token,confirm='delete-selected')
    idle[0]=False;assert c.post('/storage/delete',data=payload).status_code==409 and not ftp.deleted
    idle[0]=True;blocked[0]='Open job';assert c.post('/storage/delete',data=payload).status_code==409 and not ftp.deleted
    blocked[0]=None;ftp.size=13;assert c.post('/storage/delete',data=payload).status_code==409 and not ftp.deleted
    ftp.size=12;assert c.post('/storage/delete',data=payload).status_code==200
    assert ftp.deleted==['/cache/Example.3mf'] and ftp.closed


def test_invalid_confirm_token_and_folder_never_delete():
    c,ftp,blocked,idle,token=context()
    assert c.post('/storage/delete',data=dict(files=token)).status_code==400
    assert c.post('/storage/delete',data=dict(files='bad',confirm='delete-selected')).status_code==409
    assert c.get('/api/storage?folder=/etc').status_code==400
    assert not ftp.deleted


def test_preview_cache_persists_isolates_and_evicts(tmp_path,monkeypatch):
    from bridge.preview_cache import PreviewCache
    import bridge.preview_cache as module
    clock=[1000];monkeypatch.setattr(module.time,'time',lambda:clock[0])
    path=tmp_path/'cache.db';c=PreviewCache(path,'printer',max_bytes=5,max_entries=2,ttl=100)
    e=lambda name:dict(path='/'+name,name=name,size=10,modified='Nov 16 10:00')
    c.put(e('a.3mf'),b'aa','image/png');clock[0]+=1;c.put(e('b.3mf'),b'bb','image/png')
    clock[0]+=1;assert c.get(e('a.3mf'))[0]==b'aa'
    clock[0]+=1;c.put(e('c.3mf'),b'cc','image/png')
    assert not c.contains(e('b.3mf')) and c.contains(e('a.3mf'))
    assert PreviewCache(path,'printer').get(e('a.3mf'))[0]==b'aa'
    assert not PreviewCache(path,'other-printer').contains(e('a.3mf'))
    assert not c.contains(dict(e('a.3mf'),size=11))
    clock[0]+=101;assert not c.get(e('a.3mf'))
    c.clear();assert c.db.execute('SELECT COUNT(*) FROM previews').fetchone()[0]==0


def test_preview_cache_avoids_transfer_and_works_without_idle():
    archive=io.BytesIO()
    with zipfile.ZipFile(archive,'w') as z:z.writestr('Metadata/plate_1.png',b'\x89PNG\r\n\x1a\nexample')
    data=archive.getvalue();ftp=FTP();ftp.size=len(data);calls=[]
    class Stream(io.BytesIO):
        def recv(self,size):return self.read(size)
    ftp.voidcmd=lambda command:None
    ftp.transfercmd=lambda command:(calls.append(command),Stream(data))[1]
    app=Flask(__name__);app.secret_key='cache-test';idle=[True]
    register_storage(app,lambda:ftp,lambda n:None,lambda:idle[0],lambda s:None)
    client=app.test_client();entry=client.get('/api/storage?folder=/cache').json['files'][0]
    assert entry['preview_url'] is None
    assert client.post('/storage/preview',data={'file':entry['token']}).status_code==200
    idle[0]=False
    r=client.post('/storage/preview',data={'file':entry['token']});assert r.status_code==200 and r.headers['X-Preview-Cache']=='hit'
    assert len(calls)==1
    cached=client.get('/api/storage?folder=/cache').json['files'][0]
    assert client.get(cached['preview_url']).status_code==200
    ftp.size+=1
    assert client.get('/api/storage?folder=/cache').json['files'][0]['preview_url'] is None
    assert client.post('/storage/preview-cache/clear',data={'confirm':'wrong'}).status_code==400
    assert client.get(cached['preview_url']).status_code==200
    assert client.post('/storage/preview-cache/clear',data={'confirm':'clear-previews'}).status_code==200
    assert client.get(cached['preview_url']).status_code==404
    assert not ftp.deleted


def test_large_archive_preview_stops_before_gcode_and_rechecks_idle():
    from tests.test_archive_preview import archive
    data=archive(stream=True);ftp=FTP();ftp.size=60*1024*1024
    reads=[];streams=[]
    class Stream(io.BytesIO):
        def recv(self,size):
            data=self.read(size);reads.append(len(data));return data
    def transfer(command):
        stream=Stream(data);streams.append(stream);return stream
    ftp.voidcmd=lambda command:None;ftp.transfercmd=transfer
    app=Flask(__name__);app.secret_key='large-test';idle=[False]
    register_storage(app,lambda:ftp,lambda n:None,lambda:idle[0],lambda s:None)
    client=app.test_client();entry=client.get('/api/storage?folder=/cache').json['files'][0]
    assert client.post('/storage/preview',data={'file':entry['token']}).status_code==409 and not reads
    idle[0]=True;ftp.size+=1
    assert client.post('/storage/preview',data={'file':entry['token']}).status_code==400 and not reads
    ftp.size-=1
    r=client.post('/storage/preview',data={'file':entry['token']})
    assert r.status_code==200 and r.mimetype=='image/png'
    assert sum(reads)<=65536 and all(s.closed for s in streams) and ftp.closed and not ftp.deleted


def test_storage_metadata_real_plate_indices_and_bounds():
    from bridge.storage_metadata import inspect_archive
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w') as z:
        z.writestr('Metadata/plate_3.gcode','G1 X0')
        z.writestr('Metadata/slice_info.config','<config><plate><metadata key="index" value="3"/><metadata key="prediction" value="120"/><filament id="2" type="PLA" color="#FFFFFF" used_g="9.61"/></plate></config>')
        z.writestr('Metadata/project_settings.config','{"printer_model":"Bambu Lab A1"}')
    result=inspect_archive(data)
    assert result['plates'][0]['index']==3 and result['plates'][0]['filaments'][0]['id']==2
    assert result['plates'][0]['seconds']==120
    bad=io.BytesIO()
    with zipfile.ZipFile(bad,'w') as z:z.writestr('Metadata/slice_info.config','<!DOCTYPE a><config/>')
    with pytest.raises(ValueError):inspect_archive(bad)


def test_background_details_and_single_use_review_guard_file_change():
    import json,time
    from bridge.print_start import prepare_start
    from tests.test_print_start import inputs
    _,metadata,choice,ctx=inputs()
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w') as z:
        z.writestr('Metadata/plate_3.gcode','G1 X0')
        z.writestr('Metadata/slice_info.config','<config><plate><metadata key="index" value="3"/><metadata key="nozzle_diameters" value="0.4"/><filament id="2" type="PLA" color="#FFFFFF" used_g="2"/></plate></config>')
        z.writestr('Metadata/project_settings.config','{"printer_model":"Bambu Lab A1","filament_type":["PLA","PLA"]}')
    data=data.getvalue();ftp=FTP();ftp.size=len(data);transfers=[];starts=[]
    def transfer(command,write,blocksize=8192):transfers.append(command);write(data)
    ftp.transfercmd=lambda command:(transfers.append(command),FTPStream(data))[1]
    handlers=dict(context=lambda:ctx,history=lambda n:[dict(state='DONE')],prepare=lambda e,m,c:prepare_start(e,m,c,ctx),start=lambda r:(starts.append(r),dict(confirmed=True))[1])
    app=Flask(__name__);app.secret_key='actions-test'
    register_storage(app,lambda:ftp,lambda n:None,lambda:True,lambda s:None,handlers=handlers)
    c=app.test_client();entry=c.get('/api/storage?folder=/cache').json['files'][0]
    r=c.post('/storage/details',data=dict(file=entry['token']));assert r.status_code==202
    for _ in range(100):
        d=c.get('/api/storage/details',query_string=dict(ticket=r.json['ticket'])).json
        if d['state']!='loading':break
        time.sleep(.01)
    assert d['state']=='done' and d['metadata']['plates'][0]['index']==3
    assert c.post('/storage/details',data=dict(file=entry['token'])).json['state']=='done' and len(transfers)==1
    assert c.get('/api/storage/history',query_string=dict(file=entry['token'])).json['entries'][0]['state']=='DONE'
    def review():
        return c.post('/storage/start-review',data=dict(file=entry['token'],choice=json.dumps(choice))).json['token']
    token=review()
    assert c.post('/storage/start',data=dict(review=token)).status_code==400 and not starts
    ftp.size+=1
    assert c.post('/storage/start',data=dict(review=token,confirm='start-print',bed_clear='yes')).status_code==409 and not starts
    ftp.size-=1;token=review()
    assert c.post('/storage/start',data=dict(review=token,confirm='start-print',bed_clear='yes')).json['confirmed']
    assert c.post('/storage/start',data=dict(review=token,confirm='start-print',bed_clear='yes')).status_code==409 and len(starts)==1


def test_rename_preserves_cache_and_history_and_refuses_overwrite():
    import json,time
    source=io.BytesIO()
    with zipfile.ZipFile(source,'w') as z:
        z.writestr('Metadata/plate_1.png',b'\x89PNG\r\n\x1a\nexample')
        z.writestr('Metadata/plate_1.gcode','G1 X0')
        z.writestr('Metadata/slice_info.config','<config><plate><metadata key="index" value="1"/><filament id="1" type="PLA" used_g="1"/></plate></config>')
    data=source.getvalue()
    class Files:
        def __init__(self):self.files={'Original.gcode.3mf':data,'Exists.gcode.3mf':data};self.renamed=[];self.transfers=0
        def retrlines(self,command,callback):
            for name,content in self.files.items():callback('-rw-r--r-- 1 a b '+str(len(content))+' Nov 16 10:00 '+name)
        def voidcmd(self,command):pass
        def voidresp(self):pass
        def transfercmd(self,command):self.transfers+=1;return FTPStream(self.files[command[5:].split('/')[-1]])
        def rename(self,a,b):self.files[b.split('/')[-1]]=self.files.pop(a.split('/')[-1]);self.renamed.append((a,b))
        def quit(self):pass
        def close(self):pass
    ftp=Files();idle=[True];blocked=[False];app=Flask(__name__);app.secret_key='rename-test'
    handlers=dict(history=lambda name:[dict(id='old-job',created_at='2026-01-01',state='DONE')] if name=='Original.gcode.3mf' else [],context=lambda:{},prepare=lambda *a:{},start=lambda *a:{})
    register_storage(app,lambda:ftp,lambda name:'Protected' if blocked[0] else None,lambda:idle[0],lambda s:None,handlers=handlers)
    c=app.test_client();entry=next(x for x in c.get('/api/storage?folder=/cache').json['files'] if x['name']=='Original.gcode.3mf')
    ticket=c.post('/storage/details',data=dict(file=entry['token'])).json['ticket']
    for _ in range(100):
        response=c.get('/api/storage/details',query_string=dict(ticket=ticket)).json
        if response['state']!='loading':break
        time.sleep(.01)
    assert response['state']=='done'
    def rename(name):return c.post('/storage/rename',data=dict(file=entry['token'],name=name))
    assert rename('Exists.gcode.3mf').status_code==409 and not ftp.renamed
    for name in ('../bad.gcode.3mf','bad.gcode','bad\nname.gcode.3mf'):assert rename(name).status_code==409
    idle[0]=False;assert rename('New.gcode.3mf').status_code==409
    idle[0]=True;blocked[0]=True;assert rename('New.gcode.3mf').status_code==409
    blocked[0]=False;assert rename('New.gcode.3mf').status_code==200
    new=next(x for x in c.get('/api/storage?folder=/cache').json['files'] if x['name']=='New.gcode.3mf')
    assert c.post('/storage/details',data=dict(file=new['token'])).json['state']=='done' and ftp.transfers==1
    assert c.get('/api/storage/history',query_string=dict(file=new['token'])).json['entries'][0]['id']=='old-job'
    assert len(ftp.renamed)==1


def test_draft_does_not_enable_server_start_until_full_inspection():
    import json,time,threading
    source=io.BytesIO()
    with zipfile.ZipFile(source,'w') as z:
        z.writestr('Metadata/plate_3.json','{"filament_ids":[0]}')
        z.writestr('Metadata/project_settings.config','{"printer_model":"Bambu Lab A1","filament_type":["PLA"],"filament_colour":["#FFFFFF"],"nozzle_diameter":["0.4"]}')
        z.writestr('Metadata/plate_3.gcode','G1 X0\n'*20000)
        z.writestr('Metadata/slice_info.config','<config><plate><metadata key="index" value="3"/><filament id="1" type="PLA" used_g="1"/></plate></config>')
    content=source.getvalue();release=threading.Event();ftp=FTP();ftp.size=len(content);calls=[]
    def transfer(command,write,blocksize=8192):write(content[:65536]);release.wait(3);write(content[65536:])
    class Paused(FTPStream):
        def recv(self,size):
            if self.tell()>=65536:release.wait(3)
            return super().recv(size)
    ftp.transfercmd=lambda command:Paused(content)
    app=Flask(__name__);app.secret_key='draft-guard'
    register_storage(app,lambda:ftp,lambda name:None,lambda:True,lambda s:None,handlers=dict(history=lambda n:[],context=lambda:{},prepare=lambda *a:(calls.append(True),dict(summary=['OK']))[1],start=lambda *a:{}))
    c=app.test_client();entry=c.get('/api/storage?folder=/cache').json['files'][0];ticket=c.post('/storage/details',data=dict(file=entry['token'])).json['ticket']
    try:
        for _ in range(100):
            d=c.get('/api/storage/details',query_string=dict(ticket=ticket)).json
            if d.get('draft_metadata'):break
            time.sleep(.01)
        assert d['state']=='loading' and d['draft_metadata']['draft']
        assert c.post('/storage/start-review',data=dict(file=entry['token'],choice='{}')).status_code==409 and not calls
    finally:release.set()


def test_cancel_interrupts_blocked_socket_frees_storage_and_does_not_cache_partial():
    import threading,time
    from tests.test_archive_preview import archive
    data=archive(stream=True);sockets=[];reads=[]
    class Blocking(FTPStream):
        def __init__(self):super().__init__(data);self.release=threading.Event();self.entered=threading.Event();self.aborted=False
        def recv(self,size):
            if self.tell()>=65536:
                self.entered.set();self.release.wait(3)
                if self.aborted:return b''
            reads.append(True);return super().recv(size)
        def shutdown(self,how):self.aborted=True;self.release.set()
    ftp=FTP();ftp.size=len(data)
    def transfer(command):
        stream=Blocking();sockets.append(stream);return stream
    ftp.transfercmd=transfer
    app=Flask(__name__);app.secret_key='cancel-test'
    register_storage(app,lambda:ftp,lambda name:None,lambda:True,lambda s:None)
    c=app.test_client();entry=c.get('/api/storage?folder=/cache').json['files'][0]
    response=c.post('/storage/details',data=dict(file=entry['token'],viewer='viewer-one'));ticket=response.json['ticket']
    until=time.monotonic()+2
    while not sockets and time.monotonic()<until:time.sleep(.01)
    assert sockets[0].entered.wait(1)
    start=time.monotonic()
    assert c.post('/storage/details/cancel',data=dict(ticket=ticket,file=entry['token'],viewer='viewer-one')).status_code==200
    while time.monotonic()-start<1:
        progress=c.get('/api/storage/details',query_string=dict(ticket=ticket)).json
        if progress['state']=='cancelled' and c.get('/api/storage?folder=/cache').status_code==200:break
        time.sleep(.01)
    assert progress['state']=='cancelled' and time.monotonic()-start<1 and sockets[0].aborted
    assert progress['received']<len(data)
    # A new view starts a new transfer; no partially parsed result was cached.
    again=c.post('/storage/details',data=dict(file=entry['token'],viewer='viewer-two'))
    assert again.status_code==202 and again.json['ticket']!=ticket
    c.post('/storage/details/cancel',data=dict(ticket=again.json['ticket'],viewer='viewer-two'))


def test_close_before_start_is_idempotent_and_shared_view_is_not_cancelled():
    import threading,time
    from tests.test_archive_preview import archive
    data=archive(stream=True);hold=threading.Event();streams=[]
    class Held(FTPStream):
        def recv(self,size):hold.wait(2);return b''
        def shutdown(self,how):hold.set()
    ftp=FTP();ftp.size=len(data)
    def transfer(command):s=Held(data);streams.append(s);return s
    ftp.transfercmd=transfer;app=Flask(__name__);app.secret_key='lease-test'
    register_storage(app,lambda:ftp,lambda name:None,lambda:True,lambda s:None)
    c=app.test_client();entry=c.get('/api/storage?folder=/cache').json['files'][0]
    c.post('/storage/details/cancel',data=dict(file=entry['token'],viewer='closed-before-response'))
    assert c.post('/storage/details',data=dict(file=entry['token'],viewer='closed-before-response')).json['state']=='cancelled' and not streams
    ticket=c.post('/storage/details',data=dict(file=entry['token'],viewer='view-a')).json['ticket']
    assert c.post('/storage/details',data=dict(file=entry['token'],viewer='view-b')).json['ticket']==ticket
    c.post('/storage/details/cancel',data=dict(ticket=ticket,viewer='view-a'))
    assert not hold.is_set() and c.get('/api/storage/details',query_string=dict(ticket=ticket)).json['state']=='loading'
    c.post('/storage/details/cancel',data=dict(ticket=ticket,viewer='view-b'))
    until=time.monotonic()+1
    while c.get('/api/storage/details',query_string=dict(ticket=ticket)).json['state']=='loading' and time.monotonic()<until:time.sleep(.01)
    assert c.get('/api/storage/details',query_string=dict(ticket=ticket)).json['state']=='cancelled'


def test_model_plate_names_and_bed_surfaces_are_separate_from_indices():
    from bridge.storage_metadata import inspect_archive,plate_settings,describe_plate
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w') as z:
        for index in (2,4,5):z.writestr(f'Metadata/plate_{index}.gcode','G1 X0')
        z.writestr('Metadata/slice_info.config','<config>'+''.join(f'<plate><metadata key="index" value="{i}"/></plate>' for i in (2,4,5))+'</config>')
        z.writestr('Metadata/project_settings.config','{"curr_bed_type":"Cool Plate"}')
        z.writestr('Metadata/model_settings.config','<config><plate><metadata key="plater_id" value="2"/><metadata key="plater_name" value="Räder &amp; Achsen"/><metadata key="bed_type" value="Supertack Plate"/></plate><plate><metadata key="plater_id" value="4"/><metadata key="bed_type" value="Textured PEI Plate"/></plate></config>')
    result=inspect_archive(data);a,b,c=result['plates']
    assert result['metadata_version']==2
    assert (a['index'],a['name'],a['bed_label'])==(2,'Räder & Achsen','Bambu Cool Plate SuperTack')
    assert b['bed_label']=='Textured PEI Plate' and c['bed_label']=='Cool Plate'
    assert describe_plate(2,{2:{'bed_type':'Default Plate'}},{'curr_bed_type':'High Temp Plate'})['bed_label']=='Smooth PEI Plate / High Temp Plate'
    assert describe_plate(2,{2:{'bed_type':'Future Plate'}},{})['bed_label']=='Future Plate'
    assert describe_plate(2,{}, {})['bed_label']==''
    for invalid in (b'<config>',b'<!DOCTYPE config><config/>',b'<config>'+b' '*2097152):assert plate_settings(invalid)=={}
    assert plate_settings(b'<config><plate><metadata key="plater_id" value="2"/></plate><plate><metadata key="plater_id" value="2"/><metadata key="plater_name" value="Wrong"/></plate></config>')[2]=={}


def test_read_only_continuation_window_keeps_poweroff_strict():
    from bridge.printer_storage import transfer_state
    from bridge.devices import safe_to_power_off
    import time
    now=time.time();status=dict(state='IDLE',state_seen_at=now-55)
    assert transfer_state(status,True,now=now)=='idle' and not safe_to_power_off(status)
    for state in ('IDLE','FINISH'):
        assert transfer_state(dict(state=state,state_seen_at=now-90),True,now=now)=='idle'
        assert transfer_state(dict(state=state,state_seen_at=now-90.1),True,now=now)=='unconfirmed'
    assert transfer_state(dict(state='IDLE',state_seen_at=now+1),True,now=now)=='unconfirmed'
    assert transfer_state(status,False,now=now)=='offline'
    assert transfer_state(status,True,active_job=True,now=now)=='active'
    for state in ('RUNNING','PREPARE','PAUSE'):
        assert transfer_state(dict(state=state,state_seen_at=now),True,now=now)=='active'
    assert transfer_state(dict(state='UNKNOWN',state_seen_at=now),True,now=now)=='unconfirmed'


def test_legacy_wrong_thumbnail_is_repaired_from_verified_plate_without_transfer(tmp_path):
    import base64,json,time
    from bridge.preview_cache import PreviewCache
    from bridge.storage_metadata import METADATA_VERSION
    ftp=FTP();app=Flask(__name__);app.secret_key='repair';db_path=tmp_path/'preview.sqlite3'
    register_storage(app,lambda:ftp,lambda name:None,lambda:False,lambda s:None,cache_path=db_path)
    cache=PreviewCache(db_path);entry=dict(name='Example.3mf',path='/Example.3mf',size=ftp.size,modified='Nov 16 10:00')
    # Date rendering is authoritative: get the real signed listing before seeding.
    client=app.test_client();row=client.get('/api/storage?folder=/').json['files'][0]
    entry['modified']=row['modified']
    wrong=b'\x89PNG\r\n\x1a\nproject';correct=b'\x89PNG\r\n\x1a\nplate-three'
    cache.db.execute('INSERT INTO previews VALUES (?,?,?,?,?,0)',(cache.key(entry),cache.path_key(entry),'image/png',wrong,time.time()))
    metadata=dict(metadata_version=METADATA_VERSION,plates=[dict(index=3,preview='data:image/png;base64,'+base64.b64encode(correct).decode())])
    cache.db.execute('INSERT INTO storage_metadata VALUES (?,?,?)',(cache.key(entry),json.dumps(metadata),time.time()));cache.db.commit()
    updated=client.get('/api/storage?folder=/').json['files'][0]
    assert 'v=2' in updated['preview_url']
    assert client.get(updated['preview_url']).data==correct
    assert client.post('/storage/preview',data=dict(file=updated['token'])).data==correct
    assert cache.db.execute('SELECT COUNT(*) FROM storage_metadata').fetchone()[0]==1


def test_default_plate_order_matches_streamed_first_executable_plate():
    from bridge.storage_metadata import inspect_archive
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w') as z:
        z.writestr('Metadata/plate_1.png',b'\x89PNG\r\n\x1a\none')
        z.writestr('Metadata/plate_3.png',b'\x89PNG\r\n\x1a\nthree')
        z.writestr('Metadata/plate_3.gcode','G1 X0')
        z.writestr('Metadata/plate_1.gcode','G1 X0')
        z.writestr('Metadata/slice_info.config','<config><plate><metadata key="index" value="1"/></plate><plate><metadata key="index" value="3"/></plate></config>')
    blob=data.getvalue();metadata=inspect_archive(io.BytesIO(blob))
    assert [p['index'] for p in metadata['plates']]==[3,1]
    assert streamed_thumbnail(io.BytesIO(blob).read)[0].endswith(b'three')
