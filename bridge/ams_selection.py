"""Stable fingerprints for an explicitly reviewed AMS selection."""
from .materials import materials_match


def selection_fingerprint(slot, spool, tray, mapping, payload=None):
    return dict(slot=str(slot), spool_id=str(spool['id']) if spool else '',
                spool_material=spool.get('material') if spool else None,
                spool_color=spool.get('clean_color') if spool else None,
                tray=[bool(tray.get('known')),bool(tray.get('available')),tray.get('material'),tray.get('color'),tray.get('cali_idx'),tray.get('k')],
                previous=mapping.get(str(slot)), payload=payload)


def spool_matches(spool, tray):
    return bool(spool and tray.get('known') and tray.get('available')
                and materials_match(spool.get('material'),tray.get('material'))
                and str(spool.get('clean_color') or '').upper()==str(tray.get('color') or '').upper())
