import io,json,zipfile,time
from bridge.metadata_draft import DraftCollector


def test_draft_arrives_before_gcode_and_is_not_final_metadata():
    archive=io.BytesIO()
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr('Metadata/plate_3.json',json.dumps(dict(filament_ids=[1],bbox_objects=[dict(name='Demo') ])))
        z.writestr('Metadata/project_settings.config',json.dumps(dict(printer_model='Bambu Lab A1',filament_type=['PLA','PETG'],filament_colour=['#FFFFFF','#C59B27'],nozzle_diameter=['0.4'])))
        z.writestr('Metadata/plate_3.gcode',b'G1 X0\n'*1000000)
    data=archive.getvalue();seen=[];collector=DraftCollector(seen.append)
    collector.feed(data[:65536]);collector.close()
    assert seen and seen[0]['draft'] and seen[0]['plates'][0]['index']==3
    assert seen[0]['plates'][0]['filaments'][0]['id']==2 and seen[0]['plates'][0]['grams'] is None
    assert collector.finished.is_set()
