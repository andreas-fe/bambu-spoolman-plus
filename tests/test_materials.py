import time
import pytest
from bridge.materials import material_family,materials_match
from bridge.ams_control import filament_payload


@pytest.mark.parametrize('left,right',[('PLA','PLA+'),('PLA','PLA Meta'),('PLA','PLA-CF'),('PETG','PETG CF'),('PETG','PETG-HF')])
def test_material_family_match(left,right):
    assert materials_match(left,right)
    assert materials_match(right,left)


@pytest.mark.parametrize('left,right',[('PLA','PETG'),('PLA','PLASTIC'),('PA','PA-CF'),('','')])
def test_unrelated_materials_do_not_match(left,right):
    assert not materials_match(left,right)


def test_ams_payload_reuses_live_family_profile_without_guessing():
    trays={0:dict(known=True,available=True,material='PLA',seen_at=time.time(),tray_info_idx='printer-profile',nozzle_temp_min='190',nozzle_temp_max='240')}
    payload=filament_payload(0,dict(material='PLA Meta',clean_color='#12AB34'),trays)
    assert payload['tray_type']=='PLA' and payload['tray_color']=='12AB34FF'
    assert payload['tray_info_idx']=='printer-profile' and payload['nozzle_temp_min']==190
    with pytest.raises(ValueError):filament_payload(0,dict(material='PETG-CF',clean_color='#12AB34'),trays)
    trays[0]['seen_at']=0
    with pytest.raises(ValueError):filament_payload(0,dict(material='PLA+',clean_color='#12AB34'),trays)


def test_slot_policy_allows_inactive_slots_during_print_and_rechecks_target():
    from bridge.ams_control import slot_write_reason
    p=dict(state='RUNNING',state_seen_at=100,active_slot=0,target_slot=2,target_slot_known=True)
    assert slot_write_reason(p,1,True,now=110) is None
    assert slot_write_reason(p,0,True,now=110)
    assert slot_write_reason(p,2,True,now=110)
    assert slot_write_reason(dict(p,active_slot=None),1,True,now=110)
    assert slot_write_reason(dict(p,target_slot_known=False),1,True,now=110)
    assert slot_write_reason(p,1,False,now=110)
    assert slot_write_reason(p,1,True,now=131)
