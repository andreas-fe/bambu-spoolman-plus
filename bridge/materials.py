"""Shared material families for bookkeeping and AMS display, not print profiles."""
import re

AMS_MATERIAL_CATEGORIES = ('PLA','PETG','ABS','ASA','TPU','PA','PC','PVA','HIPS','PCTG')
MATERIAL_FAMILY_PATTERN = r'^(PLA|PETG)(?=$|[\s+_\-]|CF$|GF$)'


def material_family(value):
    value = str(value or '').strip().upper()
    match = re.match(MATERIAL_FAMILY_PATTERN, value)
    return match.group(1) if match else value


def materials_match(left, right):
    return bool(material_family(left)) and material_family(left) == material_family(right)
