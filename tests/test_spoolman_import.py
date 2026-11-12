import copy
import pytest
from bridge.inventory_db import apply_migrations, connection
from bridge.spoolman_import import apply_snapshot, preview, ImportValidationError


def sample():
    return {'vendors': [{'id': 9, 'name': 'Example'}],
            'filaments': [{'id': 21, 'name': 'PLA', 'vendor': {'id': 9}, 'future': {'a': 1}}],
            'spools': [{'id': 132, 'filament': {'id': 21}, 'remaining_weight': 123.5, 'location': 'Shelf'},
                       {'id': 133, 'filament': {'id': 21}, 'archived': True}]}


def test_import_preserves_unknowns_raw_data_and_repetition(tmp_path):
    path = tmp_path / 'test.db'
    apply_migrations(path)
    snapshot = sample()
    result = apply_snapshot(path, snapshot)
    assert result['known_weight_g'] == 123.5
    assert result['unknown_weights'] == 1
    assert result['archived'] == 1
    with connection(path) as db:
        assert db.execute('SELECT current_weight_g FROM spools WHERE source_id=\'133\'').fetchone()[0] is None
        assert 'future' in db.execute('SELECT source_payload_json FROM filaments').fetchone()[0]
        db.execute('UPDATE spools SET current_weight_g=100 WHERE source_id=\'132\'')
    assert apply_snapshot(path, snapshot)['applied'] is False
    with connection(path) as db:
        assert db.execute('SELECT current_weight_g FROM spools WHERE source_id=\'132\'').fetchone()[0] == 100
        assert db.execute('SELECT COUNT(*) FROM inventory_movements').fetchone()[0] == 0
    changed = copy.deepcopy(snapshot)
    changed['spools'][0]['remaining_weight'] = 10
    with pytest.raises(ImportValidationError):
        apply_snapshot(path, changed)


@pytest.mark.parametrize('mutation', ['duplicate', 'reference', 'negative', 'nan', 'bool'])
def test_invalid_snapshot_never_imports(tmp_path, mutation):
    snapshot = sample()
    if mutation == 'duplicate':
        snapshot['spools'].append(snapshot['spools'][0])
    elif mutation == 'reference':
        snapshot['spools'][0]['filament']['id'] = 99
    else:
        snapshot['spools'][0]['remaining_weight'] = {'negative': -1, 'nan': float('nan'), 'bool': True}[mutation]
    assert not preview(snapshot)['valid']
    path = tmp_path / 'test.db'
    apply_migrations(path)
    with pytest.raises(ImportValidationError):
        apply_snapshot(path, snapshot)
    with connection(path) as db:
        assert db.execute('SELECT COUNT(*) FROM spools').fetchone()[0] == 0
