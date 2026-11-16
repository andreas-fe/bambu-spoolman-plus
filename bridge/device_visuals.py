"""Small original SVG device diagrams driven by confirmed printer telemetry."""
A1_VISUAL = r'''
<div class="printer-illustration" aria-label="Schematische Darstellung Bambu Lab A1">
<svg viewBox="0 0 310 230" role="img" aria-labelledby="a1-visual-title"><title id="a1-visual-title">Bambu Lab A1 – Druckerstatus</title>
<defs><linearGradient id="a1-metal" x2="1" y2="1"><stop stop-color="#8e9caa"/><stop offset="1" stop-color="#344757"/></linearGradient></defs>
<rect x="45" y="194" width="220" height="20" rx="7" fill="#354653"/><rect x="56" y="212" width="22" height="7" rx="3" fill="#71818d"/><rect x="232" y="212" width="22" height="7" rx="3" fill="#71818d"/>
<path d="M76 190V38Q76 27 88 27H232Q244 27 244 40V191" fill="none" stroke="url(#a1-metal)" stroke-width="17"/>
<path d="M87 45V183M234 45V183" stroke="#b9c6ce" stroke-width="3"/><path d="M84 101H235" stroke="#8fa3b0" stroke-width="12"/><path d="M87 95H235" stroke="#d3dce0" stroke-width="2"/>
<path d="M66 167H240L266 191H43Z" fill="#475863" stroke="#8b9aa5" stroke-width="2"/><path d="M81 174H229M74 181H238" stroke="#657985"/>
<rect x="151" y="81" width="43" height="49" rx="7" fill="#bbc7cd"/><rect x="160" y="91" width="25" height="25" rx="4" fill="#273d48"/><circle cx="172" cy="104" r="8" fill="none" stroke="#00ae42" stroke-width="3"/><path d="M167 130H178L173 140Z" fill="#e0b45d"/>
<path d="M172 81V55Q172 47 160 47H115" fill="none" stroke="#a6b7c1" stroke-width="3"/><rect x="54" y="192" width="42" height="15" rx="3" fill="#132b32"/><circle cx="65" cy="200" r="3" fill="#00ae42"/>
<path class="printer-print-line" d="M173 140V167" stroke="#00ae42" stroke-width="3" stroke-dasharray="4 4"/>
<text x="156" y="226" text-anchor="middle" fill="#a6b6ca" font-size="12" font-family="sans-serif">A1 · AMS Lite</text></svg></div>
'''

AMS_VISUAL = r'''
<div class="ams-visual-wrap">
<svg class="ams-visual" viewBox="0 0 900 350" role="group" aria-label="AMS Lite: Rolle anklicken, um den Schacht zu bearbeiten">
<rect x="25" y="15" width="560" height="300" rx="20" fill="#10212a" stroke="#344957"/>
<rect x="620" y="15" width="255" height="300" rx="20" fill="#12232c" stroke="#344957"/>
<path d="M306 55V247" stroke="#aeb8bd" stroke-width="27" stroke-linecap="round"/><path d="M268 83H344M268 221H344" stroke="#929fa7" stroke-width="11"/>
<g class="ams-filament-paths" fill="none" stroke-linecap="round" stroke-linejoin="round">
<path data-thread="0" d="M240 127V155H515V320H575"/><path data-thread="1" d="M240 265V283H505V325H575"/><path data-thread="2" d="M372 265V293H495V330H575"/><path data-thread="3" d="M372 127V146H525V315H575"/><path data-thread="external" d="M745 211V285H610V325H575"/>
</g>
{% for slot,x,y in [('0',240,83),('1',240,221),('2',372,221),('3',372,83)] %}
<g data-visual-slot="{{slot}}" role="button" tabindex="0" onclick="focusAMSSlot('{{slot}}')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();focusAMSSlot('{{slot}}')}" aria-label="AMS Slot {{slot|int+1}} auswählen" transform="translate({{x}},{{y}})">
<title data-visual-title>AMS Slot {{slot|int+1}}</title><rect class="spool-choice" x="-51" y="-62" width="102" height="130" rx="14" fill="transparent"/>
<ellipse cx="28" cy="0" rx="12" ry="54" fill="#c8d0d5" stroke="#87979f" stroke-width="2"/>
<rect class="spool-body" data-visual-color x="-28" y="-44" width="56" height="88" fill="#71818c"/>
<path d="M-23 -40H23M-23 40H23" stroke="#ffffff30" stroke-width="2"/>
<ellipse cx="-28" cy="0" rx="12" ry="54" fill="#d5dce0" stroke="#87979f" stroke-width="2"/><ellipse cx="-28" cy="0" rx="2.5" ry="9" fill="#8d9da5"/>
<text data-visual-material data-visual-ink text-anchor="middle" y="-6" fill="white" font-size="13" font-family="sans-serif"></text>
<path data-visual-ink d="M-9 20L5 6L10 11L-4 25H-9ZM-9 29H10V32H-9Z" fill="white"/>
<text x="{{ -65 if slot in ['0','1'] else 65}}" y="5" text-anchor="{{'end' if slot in ['0','1'] else 'start'}}" fill="#c5cfd8" font-size="14" font-family="sans-serif">A{{slot|int+1}}</text><circle data-visual-binding cx="0" cy="61" r="4" fill="#80919d"/>

</g>{% endfor %}
<g data-visual-slot="external" role="button" tabindex="0" onclick="focusAMSSlot('external')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();focusAMSSlot('external')}" aria-label="Externe Spule auswählen" transform="translate(745,145)">
<title data-visual-title>Externe Spule</title><rect class="spool-choice" x="-90" y="-78" width="180" height="160" rx="15" fill="transparent"/><ellipse cx="28" cy="0" rx="12" ry="54" fill="#c8d0d5" stroke="#87979f" stroke-width="2"/><rect class="spool-body" data-visual-color x="-28" y="-44" width="56" height="88" fill="#71818c"/><ellipse cx="-28" cy="0" rx="12" ry="54" fill="#d5dce0" stroke="#87979f" stroke-width="2"/><ellipse cx="-28" cy="0" rx="2.5" ry="9" fill="#8d9da5"/><text data-visual-material data-visual-ink y="-6" text-anchor="middle" fill="white" font-size="13" font-family="sans-serif"></text><path data-visual-ink d="M-9 20L5 6L10 11L-4 25H-9ZM-9 29H10V32H-9Z" fill="white"/><text y="-60" text-anchor="middle" fill="#edf2f8" font-size="15" font-family="sans-serif">Extern</text><circle data-visual-binding cy="62" r="4" fill="#80919d"/>
</g>
<g transform="translate(575,323)"><rect x="-15" y="-17" width="30" height="30" rx="5" fill="#b9c8d0"/><circle r="8" fill="#172e36" stroke="#00ae42" stroke-width="3"/><path d="M-7 13H7L0 24Z" fill="#dcaf58"/></g>
</svg>
<div class="ams-visual-caption"><span id="ams-path-caption">Warte auf Druckerdaten</span><span class="muted small">Farbpunkt: Bestand zugeordnet · Rahmen: ausgewählter Schacht</span></div>
</div>
<div class="ams-slot-tabs" role="tablist" aria-label="Schacht auswählen">{% for slot in ['0','1','2','3','external'] %}<button type="button" role="tab" id="ams-tab-{{slot}}" aria-controls="ams-detail-{{slot}}" onclick="focusAMSSlot('{{slot}}')">{{'Extern' if slot=='external' else 'Slot '+((slot|int)+1)|string}}</button>{% endfor %}</div>
'''
