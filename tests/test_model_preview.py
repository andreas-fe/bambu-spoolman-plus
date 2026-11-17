import base64,io,json,struct,time,zipfile,threading
import pytest
from flask import Flask
from bridge.model_preview import build_model
from bridge.printer_storage import register_storage
from tests.test_printer_storage import FTP,FTPStream


def archive(gcode,plate=3):
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w') as z:
        z.writestr(f'Metadata/plate_{plate}.gcode',gcode)
        z.writestr('Metadata/slice_info.config',f'<config><plate><metadata key="index" value="{plate}"/><filament id="1" type="PLA" color="#FFFFFF" used_g="1"/><filament id="2" type="PLA" color="#C59B27" used_g="1"/></plate></config>')
    return data.getvalue()


def metadata():return dict(plates=[dict(index=3,filaments=[dict(id=1,color='#FFFFFF'),dict(id=2,color='#C59B27')])])


def test_extruded_paths_modes_retractions_and_colors():
    data=archive('G90\nM82\nG1 X10 E1\nG1 X20 E0\nG92 E0\nT1\nG1 Y10 E2\nM83\nG91\nG1 X5 E1\nG1 Z1 E-1\nG1 Y5 E1\n')
    result=build_model(io.BytesIO(data),3,metadata())
    assert result['segments']==4 and result['bounds']==[[0.,0.,0.],[25.,15.,1.]]
    positions=struct.unpack('<24f',base64.b64decode(result['positions']));assert positions[:6]==(0,0,0,10,0,0)
    colors=base64.b64decode(result['colors']);assert colors[:6]==bytes.fromhex('FFFFFF')*2 and colors[6:12]==bytes.fromhex('C59B27')*2
    assert len(base64.b64decode(result['dimensions']))==result['segments']*8


def test_collinear_merge_preserves_paths_and_limits(monkeypatch):
    import bridge.model_preview as module
    monkeypatch.setattr(module,'MAX_SEGMENTS',8)
    result=build_model(io.BytesIO(archive('M83\n'+''.join(f'G1 X{i} Z{i} E1\n' for i in range(1,101)))),3,metadata())
    assert result['segments']<=8 and result['total_segments']==100 and result['bounds'][1]==[100.,0.,100.]
    assert result['segments']==1
    with pytest.raises(ValueError,match='Geometriegrenze'):
        build_model(io.BytesIO(archive('M83\n'+''.join(f'G1 X{i} Y{i%2} E1\n' for i in range(30)))),3,metadata())
    for text in ['G20\nG1 X2 E1','G1 X10001 E1','G1 X10\n']:
        with pytest.raises(ValueError):build_model(io.BytesIO(archive(text)),3,metadata())
    def cancel():raise RuntimeError('cancel')
    with pytest.raises(RuntimeError,match='cancel'):build_model(io.BytesIO(archive('G1 X1 E1\n'*200)),3,metadata(),cancel)
    with pytest.raises(ValueError):build_model(io.BytesIO(archive('G1 X1 E1')),2,metadata())


def wait(client,ticket):
    for _ in range(300):
        data=client.get('/api/storage/details',query_string=dict(ticket=ticket)).json
        if data['state']!='loading':return data
        time.sleep(.01)
    raise AssertionError('worker timeout')


def test_background_model_and_inspected_metadata_are_cached_separately(tmp_path):
    data=archive('M83\nG1 X20 E1\nG1 Y20 E1\n');ftp=FTP();ftp.size=len(data);transfers=[]
    ftp.transfercmd=lambda cmd:(transfers.append(cmd),FTPStream(data))[1]
    app=Flask(__name__);app.secret_key='test';register_storage(app,lambda:ftp,lambda n:None,lambda:True,lambda s:None,cache_path=tmp_path/'previews.sqlite3')
    client=app.test_client();assert client.get('/storage').status_code==200
    entry=client.get('/api/storage?folder=/').json['files'][0]
    assert client.post('/storage/model',data=dict(file=entry['token'],plate=0)).status_code==400
    started=client.post('/storage/model',data=dict(file=entry['token'],plate=3,viewer='model-one')).json
    result=wait(client,started['ticket']);assert result['state']=='done' and result['model']['segments']==2 and result['metadata'] is None
    assert client.post('/storage/model',data=dict(file=entry['token'],plate=3,viewer='model-two')).json['state']=='done' and len(transfers)==1
    normal=client.post('/storage/details',data=dict(file=entry['token'],viewer='normal')).json
    assert normal['state']=='done' and normal['metadata']['plates'][0]['index']==3 and len(transfers)==1
    assert client.post('/storage/preview-cache/clear',data={'confirm':'clear-previews'}).status_code==200
    last=client.post('/storage/model',data=dict(file=entry['token'],plate=3,viewer='model-three')).json;assert last['state']=='loading'
    assert wait(client,last['ticket'])['state']=='done'


def test_model_transfer_cancels_and_releases_filesystem():
    data=archive('M83\nG1 X20 E1\n');entered=threading.Event();stopped=threading.Event()
    class Blocked(FTPStream):
        def recv(self,size):entered.set();stopped.wait(2);return super().recv(size)
        def shutdown(self,how):stopped.set();super().shutdown(how)
    ftp=FTP();ftp.size=len(data);ftp.transfercmd=lambda cmd:Blocked(data)
    app=Flask(__name__);app.secret_key='test';register_storage(app,lambda:ftp,lambda n:None,lambda:True,lambda s:None)
    client=app.test_client();entry=client.get('/api/storage?folder=/').json['files'][0]
    started=client.post('/storage/model',data=dict(file=entry['token'],plate=3,viewer='cancel-model')).json
    assert entered.wait(2)
    assert client.post('/storage/details/cancel',data=dict(ticket=started['ticket'],viewer='cancel-model')).status_code==200
    assert wait(client,started['ticket'])['state']=='cancelled'
    assert client.get('/api/storage?folder=/').status_code==200


def test_long_bambu_configuration_comments_are_skipped_without_losing_commands():
    text='; filament_start_gcode = '+('x'*150000)+'\nM83\nG1 X10 E1; '+('y'*50000)+'\nG1 Y10 E1\n'
    result=build_model(io.BytesIO(archive(text)),3,metadata())
    assert result['segments']==2 and result['bounds'][1]==[10.,10.,0.]
    with pytest.raises(ValueError,match='4 KiB'):build_model(io.BytesIO(archive('G1 '+('X1 '*2000)+'E1\n')),3,metadata())


def test_arcs_full_circle_radius_helix_and_plane():
    from bridge.model_preview import arc_points
    import math
    for clockwise in (False,True):
        points=list(arc_points((10,0,0),(10,0,2),{'I':-10,'J':0},clockwise))
        assert len(points)>30 and points[-1]==(10,0,2)
        assert all(abs(math.hypot(p[0],p[1])-10)<1e-7 for p in points)
        assert (points[0][1]<0)==clockwise
    short=list(arc_points((0,0,0),(10,0,0),{'R':10},False))
    long=list(arc_points((0,0,0),(10,0,0),{'R':-10},False))
    assert len(long)>len(short)*3
    absolute=list(arc_points((10,0,0),(0,10,0),{'I':0,'J':0},False,absolute_center=True))
    assert absolute[-1]==(0,10,0) and all(p[0]>=-1e-8 and p[1]>=0 for p in absolute)
    yz=list(arc_points((0,10,0),(0,0,10),{'J':-10,'K':0},False,plane=19))
    assert yz[-1]==(0,0,10) and all(p[0]==0 for p in yz)


def test_bambu_layers_remove_start_and_custom_paths_without_resetting_machine_state():
    code='M83\nG1 X250 Y250 E2\n; CHANGE_LAYER\nG0 X10 Y10 Z0.2\n; FEATURE: Outer wall\n; LINE_WIDTH: 0.45\n; LAYER_HEIGHT: 0.2\nG1 X20 E1\nG3 X10 Y20 I-10 J0 E1\n; FEATURE: Custom\nG1 X240 E1\n; FEATURE: Inner wall\nG0 X10 Y10\nG1 Y20 E1\n'
    result=build_model(io.BytesIO(archive(code)),3,metadata())
    assert result['arc_commands']==1 and result['removed_segments']==2
    assert result['bounds']==[[10.,10.,.2],[20.,20.,.2]]
    assert result['segments']>4
    dims=struct.unpack('<2f',base64.b64decode(result['dimensions'])[:8])
    assert dims==pytest.approx((.45,.2))


def test_model_cache_cleared_before_completed_poll_is_explicit(tmp_path):
    data=archive('M83\nG1 X20 E1\n');ftp=FTP();ftp.size=len(data);ftp.transfercmd=lambda cmd:FTPStream(data)
    app=Flask(__name__);app.secret_key='test';register_storage(app,lambda:ftp,lambda n:None,lambda:True,lambda s:None,cache_path=tmp_path/'cache.sqlite3')
    client=app.test_client();entry=client.get('/api/storage?folder=/').json['files'][0]
    start=client.post('/storage/model',data=dict(file=entry['token'],plate=3,viewer='clear')).json
    assert wait(client,start['ticket'])['state']=='done'
    client.post('/storage/preview-cache/clear',data={'confirm':'clear-previews'})
    result=wait(client,start['ticket']);assert result['state']=='error' and 'Cache' in result['error']


def test_details_then_models_reuse_one_fully_verified_download(tmp_path):
    data=archive('M83\nG1 X20 E1\nG1 Y20 E1\n');ftp=FTP();ftp.size=len(data);transfers=[]
    ftp.transfercmd=lambda cmd:(transfers.append(cmd),FTPStream(data))[1]
    app=Flask(__name__);app.secret_key='test';register_storage(app,lambda:ftp,lambda n:None,lambda:True,lambda s:None,cache_path=tmp_path/'cache.sqlite3')
    c=app.test_client();entry=c.get('/api/storage?folder=/').json['files'][0]
    detail=c.post('/storage/details',data=dict(file=entry['token'],viewer='detail')).json
    assert wait(c,detail['ticket'])['state']=='done'
    model=c.post('/storage/model',data=dict(file=entry['token'],plate=3,viewer='model')).json
    result=wait(c,model['ticket']);assert result['state']=='done' and result['archive_reused'] and len(transfers)==1
    c.post('/storage/preview-cache/clear',data={'confirm':'clear-previews'})
    again=c.post('/storage/model',data=dict(file=entry['token'],plate=3,viewer='new')).json
    assert wait(c,again['ticket'])['state']=='done' and len(transfers)==2


@pytest.mark.parametrize('state,replies,expected,calls',[
    ('unconfirmed',[True,False,True],'done',3),
    ('unconfirmed',[True,False,False],'Leerlaufbestätigung fehlt',3),
    ('active',[True,False],'Aktivität',2),
    ('offline',[True,False],'unterbrochen',2),
])
def test_busy_transfer_retries_missing_confirmation_but_stops_activity_and_offline(monkeypatch,state,replies,expected,calls):
    from types import SimpleNamespace
    import bridge.storage_actions as actions
    clock=[100.];answers=iter(replies);checks=[]
    monkeypatch.setattr(actions,'time',SimpleNamespace(monotonic=lambda:clock[0],time=time.time))
    data=archive('M83\nG1 X20 E1\n');ftp=FTP();ftp.size=len(data)
    class Slow(FTPStream):
        def recv(self,size):
            if self.tell()==0:clock[0]+=16
            return super().recv(size)
    ftp.transfercmd=lambda cmd:Slow(data)
    def idle():checks.append(1);return next(answers)
    app=Flask(__name__);app.secret_key='test';register_storage(app,lambda:ftp,lambda n:None,idle,lambda s:None,read_state=lambda:state)
    c=app.test_client();entry=c.get('/api/storage?folder=/').json['files'][0]
    started=c.post('/storage/details',data=dict(file=entry['token'],viewer='slow')).json
    result=wait(c,started['ticket']);assert len(checks)==calls
    if expected=='done':assert result['state']=='done'
    else:assert result['state']=='error' and expected in result['error']


def test_contour_simplification_preserves_corners_closure_and_error_bound():
    import math
    from bridge.model_preview import simplify_path
    points=[(10*math.cos(i*.001),10*math.sin(i*.001),.2) for i in range(6284)]
    reduced=simplify_path(points)
    assert reduced[0]==points[0] and reduced[-1]==points[-1] and len(reduced)<len(points)//10
    for p in points:
        def distance(a,b):
            v=[b[i]-a[i] for i in range(3)];q=[p[i]-a[i] for i in range(3)];length=sum(n*n for n in v)
            t=max(0,min(1,sum(q[i]*v[i] for i in range(3))/length)) if length else 0
            return math.sqrt(sum((q[i]-t*v[i])**2 for i in range(3)))
        assert min(distance(a,b) for a,b in zip(reduced,reduced[1:]))<=.0350001
    square=[(0,0,0),(1,0,0),(1,1,0),(0,1,0),(0,0,0)]
    assert simplify_path(square)==square


def test_simplification_never_connects_separate_extrusions_or_different_colors():
    code='M83\nG1 X1 E1\nG1 X2 E1\nG0 X10\nG1 X11 E1\nT1\nG1 X12 E1\n'
    result=build_model(io.BytesIO(archive(code)),3,metadata())
    assert result['segments']==3
    positions=struct.unpack('<18f',base64.b64decode(result['positions']))
    assert positions[:6]==(0,0,0,2,0,0) and positions[6:12]==(10,0,0,11,0,0)
    assert base64.b64decode(result['colors'])[-6:]==bytes.fromhex('C59B27')*2


def test_large_model_http_compression_preserves_result_and_respects_negotiation():
    import gzip
    text='M83\n'+''.join(f'T{i%2}\nG1 X{i%2} Y{i//2} E1\n' for i in range(1,6001))
    data=archive(text);ftp=FTP();ftp.size=len(data);ftp.transfercmd=lambda cmd:FTPStream(data)
    app=Flask(__name__);app.secret_key='test';register_storage(app,lambda:ftp,lambda n:None,lambda:True,lambda s:None)
    client=app.test_client();entry=client.get('/api/storage?folder=/').json['files'][0]
    start=client.post('/storage/model',data=dict(file=entry['token'],plate=3,viewer='compressed')).json
    result=wait(client,start['ticket']);assert result['model']['segments']==6000
    response=client.get('/api/storage/details',query_string=dict(ticket=start['ticket']),headers={'Accept-Encoding':'gzip'})
    assert response.headers['Content-Encoding']=='gzip' and 'Accept-Encoding' in response.vary
    assert json.loads(gzip.decompress(response.data))==result
    plain=client.get('/api/storage/details',query_string=dict(ticket=start['ticket']),headers={'Accept-Encoding':'gzip;q=0'})
    assert 'Content-Encoding' not in plain.headers and plain.json==result
    assert len(response.data)<len(plain.data)/2
