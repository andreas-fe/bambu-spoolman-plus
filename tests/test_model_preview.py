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
    assert any('kein CAD' in w for w in result['warnings'])


def test_sampling_preserves_full_bounds_and_limits(monkeypatch):
    import bridge.model_preview as module
    monkeypatch.setattr(module,'MAX_SEGMENTS',8)
    result=build_model(io.BytesIO(archive('M83\n'+''.join(f'G1 X{i} Z{i} E1\n' for i in range(1,101)))),3,metadata())
    assert result['segments']<=8 and result['total_segments']==100 and result['bounds'][1]==[100.,0.,100.]
    assert any('vereinfacht' in w for w in result['warnings'])
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


def test_background_model_is_cached_separately_and_does_not_enable_print_metadata(tmp_path):
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
    assert normal['state']=='loading' and wait(client,normal['ticket'])['metadata']['plates'][0]['index']==3 and len(transfers)==2
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
