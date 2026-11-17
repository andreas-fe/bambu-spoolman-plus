import copy
import pytest
from bridge.print_start import prepare_start


def inputs():
    entry=dict(path='/cache/Model + ä.3mf',name='Model + ä.3mf',size=100,modified='date')
    metadata=dict(model='Bambu Lab A1',filament_count=5,plates=[dict(index=3,nozzle='0.4',filaments=[dict(id=2,material='PLA',color='#FFFFFF')])])
    choice=dict(plate=3,mapping={'2':'3'},options=dict(bed_leveling=True,flow_cali=False,vibration_cali=False,timelapse=False),allow_mismatch=False)
    context=dict(model='Bambu Lab A1',nozzle='0.4',slots=[dict(slot='3',label='AMS Slot 4',available=True,spool_id=1,spool_name='White',material='PLA+',color='#FFFFFF')])
    return entry,metadata,choice,context


def test_mapping_uses_filament_index_and_actual_plate():
    result=prepare_start(*inputs());p=result['payload']
    assert p['param']=='Metadata/plate_3.gcode' and p['ams_mapping']==[-1,3,-1,-1,-1]
    assert p['url']=='ftp:///cache/Model%20%2B%20%C3%A4.3mf' and p['use_ams']
    assert not p['flow_cali'] and p['bed_leveling']


@pytest.mark.parametrize('change',[lambda m,c,x:m.update(model='Other'),lambda m,c,x:x.update(nozzle=None),lambda m,c,x:c.update(plate=1),lambda m,c,x:c['mapping'].clear(),lambda m,c,x:x['slots'][0].update(available=False),lambda m,c,x:x['slots'][0].update(spool_id=None),lambda m,c,x:x['slots'][0].update(color='#FF0000'),lambda m,c,x:x['slots'][0].update(spool_color='#000000'),lambda m,c,x:c['options'].update(flow_cali='false')])
def test_rejects_unverified_or_incompatible_start(change):
    e,m,c,x=inputs();change(m,c,x)
    with pytest.raises(ValueError):prepare_start(e,m,c,x)


def test_explicit_override_and_fingerprint_change():
    e,m,c,x=inputs();a=prepare_start(e,m,c,x);x['slots'][0]['color']='#FF0000';c['allow_mismatch']=True
    b=prepare_start(e,m,c,x);assert a['fingerprint']!=b['fingerprint'] and 'ABWEICHUNG' in str(b['summary'])


def test_external_role_is_single_filament_only():
    e,m,c,x=inputs();c['mapping']={'2':'external'};x['slots'][0]['slot']='external'
    assert not prepare_start(e,m,c,x)['payload']['use_ams']
    m['plates'][0]['filaments'].append(dict(id=1,material='PLA',color='#FFFFFF'));c['mapping']['1']='external'
    with pytest.raises(ValueError):prepare_start(e,m,c,x)


def test_reported_pressure_is_shown_and_changes_start_fingerprint():
    from bridge.print_start import pressure_status
    tray=dict(cali_idx=3,k='0.025',tray_info_idx='PLA-template')
    printer=dict(nozzle_diameter='0.4',calibration_profiles=[dict(cali_idx=3,k_value=.025,filament_id='PLA-template',nozzle_diameter='0.4',name='PLA Blue')])
    status=pressure_status(tray,printer)
    assert status['pressure_profile']=='PLA Blue' and status['pressure_k']==.025
    e,m,c,x=inputs();x['slots'][0].update(status);first=prepare_start(e,m,c,x)
    assert any('0.0250' in line for line in first['summary'])
    x['slots'][0]['pressure_k']=.03
    assert prepare_start(e,m,c,x)['fingerprint']!=first['fingerprint']
    assert pressure_status(dict(cali_idx=-1,k=None),printer)['pressure_profile'].startswith('Standard')
    assert pressure_status({},printer)['pressure_k'] is None
