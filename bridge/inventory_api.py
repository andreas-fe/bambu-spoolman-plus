"""Native inventory views, validated editing, and lossless offline exports."""
from __future__ import annotations

import json
import math
import secrets
import sqlite3
from flask import Blueprint, Response, jsonify, redirect, request, session
from .inventory_db import connection, _now, SCHEMA_VERSION
from .inventory_service import InventoryService, InventoryError
from .inventory_exchange import export_inventory
from .inventory_ui import PAGE, present_inventory
from .ui import render_ui, render_form_error
from .filament_catalog import FilamentCatalog, CatalogUnavailable

# Explicit field allowlists prevent dynamic SQL from accepting arbitrary names.
FIELDS = {
    'vendors': ('name',),
    'filaments': ('vendor_id', 'name', 'material', 'subtype', 'color_name', 'color_json', 'diameter_mm', 'density_g_cm3', 'net_weight_g', 'article_number', 'empty_spool_weight_g', 'notes'),
    'spools': ('filament_id', 'location_id', 'archived', 'notes', 'batch', 'empty_spool_weight_g'),
    'locations': ('name', 'notes'),
}
TABLES = ('vendors', 'locations', 'filaments', 'spools', 'spool_tags', 'ams_assignments', 'inventory_movements', 'consumption_entries', 'import_runs', 'settings', 'inventory_job_plans')


def spoolman_view(path):
    """Compatibility boundary for the unchanged MQTT/3MF planner and UI."""
    with connection(path) as db:
        result = []
        for row in db.execute('SELECT s.*,f.name,f.material,f.color_json,v.name AS vendor_name FROM spools s LEFT JOIN filaments f ON f.id=s.filament_id LEFT JOIN vendors v ON v.id=f.vendor_id WHERE s.archived=0 ORDER BY s.id'):
            r = dict(row)
            color = json.loads(r['color_json'] or 'null') or '888888'
            if not isinstance(color, str):
                color = '888888'
            result.append(dict(id=r['id'], remaining_weight=r['current_weight_g'], archived=False,
                               filament=dict(id=r['filament_id'], name=r['name'] or '', material=r['material'] or '', color_hex=color, vendor=dict(name=r['vendor_name'] or '')),
                               clean_color='#'+color.removeprefix('#')[:6], material=r['material'] or '-'))
        return result


def register_inventory(app, path, internal, catalog=None):
    bp = Blueprint('inventory', __name__)
    catalog = catalog or FilamentCatalog()

    @bp.get('/inventory')
    def page():
        session.setdefault('csrf_token', secrets.token_urlsafe(32))
        with connection(path) as db:
            data = {t: [dict(r) for r in db.execute(f'SELECT * FROM {t} ORDER BY id')] for t in FIELDS}
            data['spools'] = [row for row in data['spools'] if row['removed_at'] is None]
        return render_ui(PAGE, active='inventory', title='Filamente & Spulen', labels=LABELS, data=data, token=session['csrf_token'], internal=internal, operation=secrets.token_urlsafe(24), adjusted=bool(request.args.get('adjusted')), saved_kind=request.args.get('saved'), selected_spool=request.args.get('spool', type=int), **present_inventory(data))

    @bp.get('/api/filament-catalog')
    def catalog_search():
        try:
            return jsonify(catalog.search(request.args.get('q', '')))
        except CatalogUnavailable as exc:
            return jsonify(error=str(exc)), 503
        except ValueError as exc:
            return jsonify(error=str(exc)), 400

    @bp.get('/api/inventory/<kind>')
    def listing(kind):
        if kind not in FIELDS:
            return jsonify(error='Unbekannte Liste'), 404
        with connection(path) as db:
            rows = [dict(r) for r in db.execute(f'SELECT * FROM {kind} ORDER BY id')]
        if kind == 'spools':
            rows = [row for row in rows if row['removed_at'] is None]
        query = request.args.get('q', '').casefold()
        if query:
            rows = [r for r in rows if query in json.dumps(r, ensure_ascii=False).casefold()]
        if kind == 'spools' and request.args.get('archived') in ('0', '1'):
            rows = [r for r in rows if r['archived'] == int(request.args['archived'])]
        return jsonify(rows)

    @bp.post('/inventory/<kind>/save')
    def edit(kind):
        if not internal:
            return Response('Bearbeiten erst nach interner Umschaltung möglich.', 409)
        if kind not in FIELDS:
            return Response('Unbekannte Liste', 404)
        try:
            catalog_record = None
            if kind == 'filaments' and request.form.get('catalog_id'):
                if request.form.get('id'):
                    raise ValueError('Katalogvorlagen können nur für neue Produkte übernommen werden.')
                catalog_record = catalog.get(request.form['catalog_id'])
            create_vendor = False
            values = {}
            for key in FIELDS[kind]:
                if key not in request.form:
                    continue
                value = request.form[key].strip()
                if key == 'vendor_id' and value == '__new__':
                    manufacturer_name = request.form.get('vendor_new', '').strip()
                    if not manufacturer_name:
                        raise ValueError('Bitte den Namen des neuen Herstellers eingeben.')
                    create_vendor = True
                    values[key] = None
                    continue
                if key == 'vendor_id' and value == '__catalog__':
                    if catalog_record is None:
                        raise ValueError('Bitte einen vorhandenen Hersteller auswählen.')
                    create_vendor = True
                    values[key] = None
                    continue
                if key.endswith('_id') or key == 'archived':
                    value = int(value) if value else None
                    if key == 'archived' and value not in (0, 1):
                        raise ValueError('Archivstatus muss 0 oder 1 sein.')
                elif key == 'material' and value == '__custom__':
                    value = request.form.get('material_custom', '').strip()
                    if not value:
                        raise ValueError('Bitte gib die Bezeichnung des Materials ein.')
                elif key == 'color_json':
                    if value:
                        import re
                        if not re.fullmatch(r'#?[0-9a-fA-F]{6}', value):
                            raise ValueError('Farbe muss ein sechsstelliger RGB-Wert sein.')
                        value = json.dumps(value.lstrip('#'))
                    else:
                        value = None
                elif key.endswith(('_g', '_mm', '_cm3')):
                    value = float(value) if value else None
                    if value is not None and (not math.isfinite(value) or value < 0):
                        raise ValueError('Gewicht und Maße müssen endlich und positiv sein.')
                values[key] = value
            if kind in ('vendors', 'filaments', 'locations') and not values.get('name'):
                raise ValueError('Name erforderlich.')
            if kind == 'spools' and not values.get('filament_id'):
                raise ValueError('Jede physische Spule benötigt ein Produkt.')
            now = _now()
            with connection(path) as db:
                db.execute('BEGIN IMMEDIATE')
                if catalog_record is not None:
                    values.update(source_system='spoolmandb', source_id=catalog_record['id'], source_payload_json=json.dumps(catalog_record, allow_nan=False), identifiers_json=json.dumps({'spoolmandb': catalog_record['id']}))
                    if db.execute("SELECT 1 FROM filaments WHERE source_system='spoolmandb' AND source_id=?", (catalog_record['id'],)).fetchone():
                        raise ValueError('Dieses Katalogprodukt ist bereits vorhanden. Wähle es beim Hinzufügen einer Spule aus.')
                if create_vendor:
                    manufacturer = manufacturer_name if request.form.get('vendor_id') == '__new__' else catalog_record['manufacturer'].strip()
                    existing = next((r for r in db.execute('SELECT id,name FROM vendors') if r['name'].casefold()==manufacturer.casefold()), None)
                    values['vendor_id'] = existing['id'] if existing else db.execute('INSERT INTO vendors(name,created_at,updated_at) VALUES (?,?,?)', (manufacturer, now, now)).lastrowid
                ident = request.form.get('id', '').strip()
                if ident:
                    if kind == 'spools' and db.execute('SELECT 1 FROM spools WHERE id=? AND removed_at IS NOT NULL', (int(ident),)).fetchone():
                        raise ValueError('Diese Spule wurde bereits aus dem Inventar entfernt.')
                    values['updated_at'] = now
                    result = db.execute(f'UPDATE {kind} SET '+','.join(f'{k}=?' for k in values)+' WHERE id=?', (*values.values(), int(ident)))
                    if not result.rowcount:
                        raise ValueError('Datensatz existiert nicht.')
                else:
                    if kind == 'spools':
                        if values.get('empty_spool_weight_g') is None:
                            product = db.execute('SELECT empty_spool_weight_g FROM filaments WHERE id=?', (values['filament_id'],)).fetchone()
                            if product is not None:
                                values['empty_spool_weight_g'] = product['empty_spool_weight_g']
                        weight = request.form.get('initial_weight_g', '').strip()
                        weight = float(weight) if weight else None
                        if weight is not None and (not math.isfinite(weight) or weight < 0):
                            raise ValueError('Ungültiger Eröffnungsbestand.')
                        values.update(initial_weight_g=weight, current_weight_g=weight)
                    values.update(created_at=now, updated_at=now)
                    db.execute(f'INSERT INTO {kind}('+','.join(values)+') VALUES ('+','.join('?' for _ in values)+')', tuple(values.values()))
                db.commit()
        except (ValueError, sqlite3.IntegrityError) as exc:
            message = 'Die Auswahl ist nicht mehr verfügbar. Bitte prüfe Hersteller, Filamentprodukt und Lagerort.' if isinstance(exc, sqlite3.IntegrityError) else str(exc)
            return render_form_error(message)
        return redirect('/inventory?saved='+kind+'#'+kind)

    @bp.post('/inventory/adjust')
    def adjust():
        if not internal:
            return Response('Interner Bestand nicht aktiv.', 409)
        try:
            if request.form.get('direction') == 'weigh':
                spool_id = int(request.form['spool_id'])
                tare = request.form.get('tare_grams', '').strip()
                InventoryService(path).weigh_stock(operation_key='manual:'+request.form['operation_key'], spool_id=spool_id,
                    gross_grams=float(request.form['gross_grams']), tare_grams=float(tare) if tare else None, note=request.form.get('note_detail', '').strip())
                return redirect('/inventory?adjusted=1&spool='+str(spool_id)+'#adjust-stock')
            amount = float(request.form['delta_grams'])
            direction = request.form.get('direction')
            if direction is not None:
                if direction not in ('add', 'remove') or not math.isfinite(amount) or amount <= 0:
                    raise ValueError('Wähle Hinzufügen oder Abziehen und gib eine positive Menge in Gramm ein.')
                amount = amount if direction == 'add' else -amount
            note = request.form.get('note', '').strip()
            detail = request.form.get('note_detail', '').strip()
            if detail:
                note = (note + ': ' if note else '') + detail
            spool_id = int(request.form['spool_id'])
            InventoryService(path).adjust_stock(operation_key='manual:'+request.form['operation_key'], spool_id=spool_id, delta_grams=amount, note=note)
        except (ValueError, KeyError) as exc:
            return render_form_error(str(exc))
        return redirect('/inventory?adjusted=1&spool='+str(spool_id)+'#adjust-stock')

    @bp.post('/inventory/<kind>/<int:ident>/delete')
    def delete(kind, ident):
        if not internal or kind not in ('spools', 'filaments'):
            return Response('Löschen nicht verfügbar.', 409)
        if request.form.get('confirm') != 'delete':
            return render_form_error('Bitte das Löschen ausdrücklich bestätigen.')
        try:
            with connection(path) as db:
                db.execute('BEGIN IMMEDIATE')
                if kind == 'spools':
                    spool = db.execute('SELECT archived,removed_at FROM spools WHERE id=?', (ident,)).fetchone()
                    if spool and spool['removed_at'] is not None:
                        return redirect('/inventory?removed=1#spools')
                    if spool and spool['archived']:
                        InventoryService(path).remove_archived_spool(ident, _db=db)
                        db.commit()
                        return redirect('/inventory?removed=1#spools')
                    # Legacy references and JSON snapshots have no foreign keys.
                    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if 'deductions' in tables and db.execute('SELECT 1 FROM deductions WHERE spool_id=?', (ident,)).fetchone():
                        raise ValueError('Spule besitzt Buchungen. Bitte archivieren.')
                    if 'jobs' in tables:
                        for row in db.execute('SELECT slot_mapping_json,spools_snapshot_json FROM jobs'):
                            if str(ident) in {str(v) for v in json.loads(row[0] or '{}').values()} or str(ident) in json.loads(row[1] or '{}'):
                                raise ValueError('Spule ist in einem Druckauftrag gespeichert. Bitte archivieren.')
                result = db.execute(f'DELETE FROM {kind} WHERE id=?', (ident,))
                if not result.rowcount:
                    raise ValueError('Datensatz existiert nicht mehr.')
                db.commit()
        except ValueError as exc:
            return render_form_error(str(exc))
        except sqlite3.IntegrityError:
            return render_form_error('Löschen nicht möglich: Eintrag wird noch von Spulen, Zuordnungen oder Buchungen verwendet. Physische Spulen können stattdessen archiviert werden.')
        return redirect('/inventory#'+kind)

    @bp.get('/inventory/export/products')
    def product_export():
        with connection(path) as db:
            db.execute('BEGIN')
            products = {t: [dict(r) for r in db.execute(f'SELECT * FROM {t}')] for t in ('vendors', 'filaments')}
        return Response(json.dumps(dict(format='bambu-spoolman-plus-products', version=1, tables=products), ensure_ascii=False, indent=2), mimetype='application/json', headers={'Content-Disposition': 'attachment; filename="filamentprodukte.json"'})

    @bp.get('/inventory/export')
    def export():
        payload = export_inventory(path)
        return Response(json.dumps(payload, ensure_ascii=False, indent=2), mimetype='application/json', headers={'Content-Disposition': 'attachment; filename="bambu-spoolman-plus.json"'})

    app.register_blueprint(bp)


LABELS = dict(name='Bezeichnung', vendor_id='Hersteller', material='Filamenttyp', subtype='Variante',
              color_name='Farbname', color_json='Farbton', diameter_mm='Durchmesser (mm)', density_g_cm3='Dichte (g/cm³)',
              net_weight_g='Filamentgewicht einer vollen Rolle (g)', article_number='Artikelnummer', filament_id='Filamentprodukt & Farbe',
              location_id='Lagerort', archived='Status', notes='Notizen', batch='Charge',
              empty_spool_weight_g='Gewicht der leeren Spule (g)')
