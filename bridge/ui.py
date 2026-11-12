"""Shared visual frame and accessible navigation for every application page."""
from flask import render_template_string
from markupsafe import Markup

NAVIGATION = (
    ('overview', '/', 'Übersicht', 'Drucker & Verbrauch'),
    ('inventory', '/inventory', 'Filamente', 'Spulen & Produkte'),
    ('camera', '/camera', 'Kamera', 'Livebild & Licht'),
    ('devices', '/devices', 'Steckdose', 'Leistung & Energie'),
    ('settings', '/settings', 'Einstellungen', 'Betrieb & Zuordnung'),
)

STYLE = r'''
:root{color-scheme:dark;--bg:#10161f;--surface:#192330;--raised:#202e3e;--line:#344357;--text:#edf2f8;--muted:#a6b6ca;--accent:#67dec0;--primary:#126a58}
*{box-sizing:border-box}
[hidden],input[type=hidden]{display:none!important}
body{margin:0;background:radial-gradient(ellipse at 0 0,#1e3448 0,transparent 55%),var(--bg);color:var(--text);font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif;min-height:100vh}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
button,input,select,textarea{font:inherit}
button,.button{display:inline-flex;align-items:center;justify-content:center;gap:8px;min-height:42px;padding:10px 16px;border:1px solid transparent;border-radius:10px;background:var(--primary);color:white;cursor:pointer;font-weight:650;line-height:1.3;text-decoration:none}
button:hover,.button:hover{background:#167d68;text-decoration:none}
button.secondary,.button.secondary{background:var(--raised);border-color:var(--line);color:var(--text)}
button.quiet,.button.quiet{background:transparent;color:var(--muted);border-color:var(--line)}
button.danger{background:#5a303c}
button:disabled{opacity:.5;cursor:wait}
a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible,summary:focus-visible{outline:3px solid var(--accent);outline-offset:3px}
input:not([type=hidden]):not([type=checkbox]):not([type=color]),select,textarea{display:block;width:100%;min-height:44px;border:1px solid #465970;border-radius:10px;padding:10px 12px;background:#111c29;color:var(--text)}
input::placeholder,textarea::placeholder{color:#8c9db2}
input[type=checkbox]{accent-color:var(--accent);width:20px;height:20px;flex-shrink:0}
input[type=color]{width:48px;min-width:48px;height:44px;padding:3px;border:1px solid var(--line);background:transparent;border-radius:10px}
label{display:flex;flex-direction:column;gap:7px;font-weight:550;min-width:0}
label small,small,.muted{color:var(--muted);font-weight:400}
small,.small{font-size:13px}
h1{font-size:clamp(25px,3vw,34px);letter-spacing:-.035em;line-height:1.2;margin:0 0 10px}
h2{font-size:20px;letter-spacing:-.02em;margin:0 0 10px}
h3{font-size:17px;margin:0 0 10px}
p{margin:8px 0 16px}
.app-header{max-width:1240px;margin:auto;padding:26px 24px 0}
.brand{display:flex;align-items:center;gap:13px;color:var(--text);width:fit-content;font-weight:750;font-size:18px;letter-spacing:-.025em}
.brand:hover{text-decoration:none}
.brand-mark{width:34px;height:34px;border:7px solid var(--accent);outline:1px solid #67dec050;border-radius:50%;box-shadow:inset 0 0 0 3px var(--bg)}
.brand small{display:block;font-size:12px;letter-spacing:0}
.app-nav{display:flex;gap:6px;border-bottom:1px solid var(--line);margin-top:24px;overflow-x:auto;scrollbar-width:thin}
.nav-item{display:flex;flex-direction:column;flex-shrink:0;color:var(--muted);padding:12px 20px;border-radius:12px 12px 0 0;border-bottom:3px solid transparent;white-space:nowrap;font-weight:650}
.nav-item small{font-size:12px;font-weight:400;color:inherit}
.nav-item:hover{background:#25344980;color:var(--text);text-decoration:none}
.nav-item.active{background:#25473f85;border-color:var(--accent);color:#aff4df}
main{max-width:1240px;margin:auto;padding:30px 24px 64px}
.page-heading{display:flex;align-items:center;justify-content:space-between;gap:24px;flex-wrap:wrap;margin:0 0 24px}
.page-heading p{margin:0;color:var(--muted)}
.eyebrow{color:var(--accent);font-size:12px;letter-spacing:.09em;text-transform:uppercase;font-weight:700;margin:0 0 7px}
.actions{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.card,.panel,section.card{padding:24px;background:var(--surface);border:1px solid var(--line);border-radius:18px;box-shadow:0 12px 35px #0002;margin:0 0 22px}
.section-heading{display:flex;gap:20px;align-items:center;justify-content:space-between;flex-wrap:wrap;margin-bottom:20px}
.section-heading p{margin:0;color:var(--muted)}
.form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}
.form-grid .wide{grid-column:1/-1}
.field-help{color:var(--muted);font-weight:400;font-size:13px}
.form-actions{display:flex;gap:10px;margin-top:22px;flex-wrap:wrap}
.editor{margin-top:22px;padding:22px;background:#111d2a;border:1px solid #44617a;border-radius:14px}
.editor h3{margin-bottom:20px}
.editor details{margin-top:20px}
.editor summary{color:var(--muted);font-size:14px}
.editor details[open] summary{margin-bottom:18px}
.subtabs{display:flex;gap:7px;flex-wrap:wrap;padding:6px;background:#111d2a;border:1px solid var(--line);border-radius:13px;margin:0 0 24px}
.subtabs button{background:transparent;color:var(--muted);min-height:40px;border-radius:8px}
.subtabs button[aria-selected=true]{background:var(--raised);color:var(--text);box-shadow:0 2px 8px #0003}
.subtabs .count{padding:1px 7px;border-radius:6px;background:#ffffff12;font-size:12px}
.scroll{overflow:auto}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{text-align:left;padding:13px 12px;border-bottom:1px solid var(--line);vertical-align:middle}
th{font-size:12px;font-weight:650;color:var(--muted);white-space:nowrap}
td{white-space:normal}
td:first-child{font-weight:550}
tbody tr:last-child td{border-bottom:0}
.table-action{padding:7px 11px;min-height:35px;font-size:13px;background:var(--raised);border:1px solid var(--line)}
.badge,.pill{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:650;border-radius:7px;padding:4px 9px;background:#ffffff10}
.badge.good{color:#9af0ce;background:#1d5b4350}
.notice,.warn-banner{padding:15px 18px;border:1px solid #746331;background:#66532240;border-radius:12px;color:#f0dca6;margin:0 0 22px}
.notice.success{color:#a8f0d7;border-color:#347760;background:#234b3b60}
.summary-box{padding:16px 18px;background:#0d1a26;border:1px solid var(--line);border-radius:12px;margin:18px 0;color:var(--muted)}
.summary-box strong{color:var(--text)}
.toolbar{display:grid;grid-template-columns:minmax(180px,2fr) repeat(3,minmax(130px,1fr));gap:14px;margin-bottom:16px}
.toolbar label{font-size:13px}
.spool-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(255px,1fr));gap:18px}
.spool-card{padding:22px;background:linear-gradient(145deg,#1a2b3b,#14212e);border:1px solid var(--line);border-radius:16px;display:flex;flex-direction:column}
.spool-card h3{font-size:23px;letter-spacing:-.025em;margin:12px 0 4px}
.spool-card .actions{margin-top:auto;padding-top:16px}
.spool-card .actions button{font-size:13px;padding:9px 12px}
.swatch-large{width:36px;height:36px;flex-shrink:0;border:2px solid #ffffff70;border-radius:50%;box-shadow:inset 0 0 0 5px #0001}
.card-top{display:flex;justify-content:space-between;align-items:center;gap:12px}
.weight{font-size:28px;letter-spacing:-.03em;font-weight:700;margin:20px 0 7px}
.spool-card progress{appearance:none;border-radius:5px;background:var(--line);width:100%;height:6px;accent-color:var(--accent);border:0;margin-bottom:14px}
.spool-card .location{font-size:13px;color:var(--muted);margin:9px 0 0}
.empty{padding:30px;text-align:center;color:var(--muted)}
.color-field{display:flex;gap:10px}
.color-field input:not([type=color]){min-width:0}
.two-columns{display:grid;grid-template-columns:minmax(0,1.8fr) minmax(260px,1fr);gap:22px}
.camera-image{width:100%;border:1px solid var(--line);border-radius:12px;margin:14px 0}
.control-group{display:flex;gap:8px;flex-wrap:wrap}
.device-status{min-height:24px;color:var(--muted);font-size:14px;margin:14px 0 0}
.setting{display:flex;flex-direction:row;gap:16px;align-items:start;padding:22px 0;border-bottom:1px solid var(--line)}
.setting input{margin-top:4px}
.setting strong{display:block}
.setting small{display:block;margin-top:4px;max-width:65ch}
.setting:last-of-type{border:0}
.status{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:20px;background:#111d2a;border:1px solid var(--line);border-radius:12px;padding:18px;margin-top:22px}
.status>div{display:flex;flex-wrap:wrap;align-items:center;gap:7px}
.status>div>.muted{display:block;width:100%;font-size:12px}
.top{display:flex;align-items:center;justify-content:space-between;gap:20px;flex-wrap:wrap}
.slot{display:grid;grid-template-columns:minmax(180px,.8fr) minmax(220px,1.5fr);gap:20px;align-items:center;padding:18px 0;border-bottom:1px solid var(--line)}
.slot:last-of-type{border:0}
.swatch{display:inline-block;width:15px;height:15px;border:1px solid #ffffff80;border-radius:50%;vertical-align:middle;margin-right:7px}
.manual-grid{display:grid;grid-template-columns:minmax(240px,1fr) 150px auto;gap:14px;align-items:end}
.logs{height:200px;overflow:auto;background:#0c1621;border:1px solid var(--line);border-radius:10px;padding:14px;font:12px/1.7 ui-monospace,monospace;white-space:pre-wrap}
.diagnostics summary{cursor:pointer;font-weight:650}
.diagnostics[open] summary{margin-bottom:18px}
.settings-width{max-width:850px}
button.link-button{padding:0;min-height:0;background:none;color:var(--accent);font-weight:500;justify-content:start;font-size:13px}
.tab-panel{scroll-margin-top:20px}
.app-footer{max-width:1240px;margin:auto;padding:0 24px 24px;color:var(--muted);font-size:12px}
#adjust-stock{scroll-margin-top:24px}
@media(max-width:850px){.toolbar{grid-template-columns:repeat(2,minmax(0,1fr))}
.two-columns{grid-template-columns:1fr}
.status{grid-template-columns:repeat(2,minmax(0,1fr))}
}
@media(max-width:600px){.app-header{padding:18px 14px 0}
main{padding:22px 14px 40px}
.app-nav{margin-top:18px}
.nav-item{padding:10px 14px}
.app-nav{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:4px;overflow:visible}
.nav-item{border-radius:9px;white-space:normal}
.nav-item small{display:none}
.panel,.card,section.card{padding:18px}
.form-grid{grid-template-columns:1fr}
.form-grid .wide{grid-column:auto}
.slot,.manual-grid{grid-template-columns:1fr;gap:12px}
.subtabs{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:3px}
.subtabs button{padding:9px 10px;font-size:13px}
.editor{padding:16px}
.spool-grid{grid-template-columns:1fr}
.toolbar{gap:12px}
.toolbar label:first-child{grid-column:1/-1}
.page-heading .actions{width:100%}
.page-heading .actions .button{flex:1}
.status{gap:14px;padding:15px}
th,td{padding:11px 9px}
.app-footer{padding-inline:14px}
}
.weight.unknown{font-size:20px}
.spool-card progress::-webkit-progress-bar{background:var(--line);border-radius:5px}
.spool-card progress::-webkit-progress-value{background:var(--accent);border-radius:5px}
.spool-card progress::-moz-progress-bar{background:var(--accent);border-radius:5px}
@media(max-width:600px){.inventory-table thead{display:none}
.inventory-table tbody{display:grid;gap:14px}
.inventory-table tr{display:block;border:1px solid var(--line);border-radius:12px;padding:8px 12px;background:#111d2a}
.inventory-table td{display:block;padding:10px 0}
.inventory-table td[data-label]{display:grid;grid-template-columns:95px minmax(0,1fr);gap:10px;word-break:break-word}
.inventory-table td[data-label]::before{content:attr(data-label);color:var(--muted);font-size:12px;font-weight:400}
.inventory-table td.row-actions{display:flex;flex-wrap:wrap;gap:8px}
.inventory-table td:last-child{border:0}
.inventory-table td.row-actions::before{display:none}
}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important}
}

'''

SHELL = '''<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{{title}} · Bambu-Spoolman-Plus</title><style>{{style|safe}}</style></head><body><header class="app-header"><a class="brand" href="/" aria-label="Bambu-Spoolman-Plus Startseite"><span class="brand-mark" aria-hidden="true"></span><span>Bambu-Spoolman-Plus<small>Filamente im Blick. Drucker unter Kontrolle.</small></span></a><nav class="app-nav" aria-label="Hauptnavigation">{% for key,url,label,description in navigation %}<a class="nav-item{% if active==key %} active{% endif %}" href="{{url}}" {% if active==key %}aria-current="page"{% endif %}>{{label}}<small>{{description}}</small></a>{% endfor %}</nav></header><main id="main-content">{{body}}</main><footer class="app-footer">Bambu-Spoolman-Plus · Dein Filamentlager</footer></body></html>'''


def render_ui(template, *, active, title, **context):
    body = render_template_string(template, **context)
    return render_template_string(SHELL, body=Markup(body), style=STYLE,
                                  navigation=NAVIGATION, active=active, title=title)


SETTINGS_PAGE = '''<div class="page-heading"><div><div class="eyebrow">Deine Einstellungen</div><h1>Betrieb & Zuordnung</h1><p>Lege fest, wie Verbräuche gebucht und Spulen zugeordnet werden.</p></div><span class="badge {{'good' if not dry_run else ''}}">{{'Simulation' if dry_run else 'Echtbetrieb'}}</span></div>{% if saved %}<div class="notice success" role="status">Einstellungen gespeichert.</div>{% endif %}<section class="panel settings-width"><h2>Verbrauchserfassung</h2><form method="post"><input type="hidden" name="csrf_token" value="{{token}}"><label class="setting"><input type="checkbox" name="dry_run" role="switch" {% if dry_run %}checked{% endif %}><span><strong>Verbräuche nur simulieren</strong><small>Bei aktiver Simulation bleibt der Bestand unverändert. Bereits simulierte Drucke werden später nicht nachgebucht.</small></span></label><label class="setting"><input type="checkbox" name="auto_match" role="switch" {% if auto_match %}checked{% endif %}><span><strong>Passende Spulen automatisch finden</strong><small>Farbe und Material helfen dabei, die Spule einem Druck zuzuordnen. Die AMS-Zuordnung kannst du auf der Übersicht anpassen.</small></span></label><div class="form-actions"><button>Einstellungen speichern</button></div></form></section><section class="panel settings-width"><h2>Bestandsverwaltung</h2><p>{{'Dein Filamentbestand wird direkt in dieser App verwaltet.' if backend=='internal' else 'Dein Filamentbestand wird über Spoolman verwaltet.'}}</p><p class="muted small">Gerätezugänge werden über die geschützte Serverkonfiguration bereitgestellt.</p></section>'''



def render_form_error(message):
    return render_ui('''<div class="page-heading"><div><h1>Bitte Eingabe prüfen</h1><p>Die Änderung wurde nicht gespeichert.</p></div></div><section class="panel settings-width"><p role="alert">{{message}}</p><div class="form-actions"><button onclick="history.back()">Zurück zur Eingabe</button><a class="button quiet" href="/inventory">Zum Inventar</a></div></section>''', active='inventory', title='Eingabe prüfen', message=message), 400

STYLE += '''
.camera-toolbar{display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap;padding-bottom:16px;border-bottom:1px solid var(--line)}
.meter-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:18px;margin:20px 0}
.meter-card{display:flex;flex-direction:column;gap:8px;border:1px solid var(--line);border-radius:14px;background:#111d2a;padding:22px}
.meter-card>span,.meter-card>small{color:var(--muted);font-size:13px}.meter-card>strong{font-size:32px;letter-spacing:-.03em}.meter-card.primary{border-color:#3d8a75;background:#183b3350}.meter-card.primary>strong{color:var(--accent)}
.live-controls{display:flex;align-items:center;gap:24px;flex-wrap:wrap;padding:20px 0}.live-toggle{flex-direction:row;align-items:center;gap:10px}
@media(max-width:850px){.meter-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:600px){.meter-grid{gap:12px}.meter-card{padding:16px}.meter-card>strong{font-size:26px}.live-controls{align-items:stretch;gap:16px}.camera-toolbar .control-group{width:100%}.camera-toolbar button{flex:1}.app-nav .nav-item:last-child:nth-child(odd){grid-column:1/-1}}
'''
STYLE += '''
.catalog-lookup{background:#182c3850;border:1px solid var(--line);border-radius:12px;padding:18px;margin-bottom:22px}.catalog-search{display:flex;gap:10px}.catalog-search>input{flex:1;min-width:0}.catalog-results{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;max-height:360px;overflow:auto}.catalog-choice{display:flex;flex-direction:column;align-items:start;background:var(--raised);border:1px solid var(--line);font-weight:400;text-align:left}.catalog-choice>span{font-size:12px;color:var(--muted)}@media(max-width:600px){.catalog-search{flex-direction:column}.catalog-results{grid-template-columns:1fr}}
'''
