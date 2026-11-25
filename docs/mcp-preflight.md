# MCP und Druckvorbereitung (Stufen 1–3)

Stand: 25. November 2026. **Entwicklungsbranch, nicht produktiv freigegeben.**

## Sicherheitsmodell

MCP läuft optional als separater **stdio-Prozess** und bietet keine öffentliche HTTP-Schnittstelle an. Die Bridge erhält ausschließlich von Loopback akzeptierte, token-geschützte `/api/assistant/*`-Routen; alle anderen bisherigen Routen behalten Web-Anmeldung und CSRF-Prüfung. Die Bridge wird **niemals** direkt ins Internet freigegeben.

`MCP_BRIDGE_TOKEN` muss mindestens 32 zufällige Zeichen enthalten. Fehlt der Wert, sind alle Assistenzrouten gesperrt. Der Token ist ein Geheimnis; nicht in Git oder Chatnachrichten speichern. Er erhält nur Zugriff auf eine kuratierte Auswahl von Status, Filamentbestand, letztem Journal, Druckkosten, Dateiliste und reiner Druckvorbereitung sowie auf das **Anfordern**, niemals selbständige Bestätigen, einer Schaltaktion. Nicht verfügbar sind direkte Druckstarts, Heizbefehle, AMS-Schreiben, Löschungen und unbestätigte Abschaltungen.

**Zusätzliche Produktionssperre:** KI-Stromsteuerung bleibt deaktiviert, wenn `WEB_USERNAME` und `WEB_PASSWORD` nicht beide gesetzt sind. Die Web-Anmeldung muss über einen vertrauenswürdigen verschlüsselten Zugang erfolgen; HTTP-Basic ohne HTTPS ist über ein ungeschütztes Netzwerk nicht ausreichend. Die übrigen lesenden MCP-Werkzeuge bleiben separat per Token geschützt.

Die Freigabe einer angefragten Tapo-Ein-/Ausschaltung findet ausschließlich auf der vorhandenen Web-Seite **Steckdose & Energie** statt, im Bereich **KI-Schaltanfragen**. Der Benutzer bestätigt dort das konkrete Ein-/Ausschalten zusätzlich per Browserdialog. Es gibt keine Freigabe per MCP-Tool. Anfragen verfallen nach 120 Sekunden; eine Anfrage wird genau einmal bearbeitet. Nach Ausführungsfehler wird **nicht** automatisch wiederholt. Die bestehende Abschaltsicherheit (frischer bestätigter Druckerleerlauf unmittelbar vor dem Schalten, Tapo-Unterprozessprüfung) wird verwendet. Ein Einschaltbefehl ist ebenfalls freigabepflichtig.

**Hinweis:** Die Weboberfläche setzt nicht zwangsläufig eine Anmeldung voraus. In einem gemeinsam genutzten LAN für die produktive Einrichtung unbedingt `WEB_USERNAME` und `WEB_PASSWORD` konfigurieren und das Netz begrenzen. Web- und MCP-Geheimnisse getrennt halten. Ein Zugriff über ChatGPT/Claude erfordert außerdem einen kompatiblen MCP-Client; Internetfreigabe oder Cloud-Tunnel sind in dieser Änderung **nicht** enthalten.

## Installation / Start (optional)

Die Hauptanwendung bleibt mit ihren bisherigen Abhängigkeiten lauffähig. Für den MCP-Prozess separat in einer vertrauenswürdigen Python-3.11-Umgebung installieren:

```sh
python -m pip install -r bridge/requirements-mcp.txt
# Nur lokal eintragen / als geheime Umgebungsvariable übergeben:
export MCP_BRIDGE_TOKEN='<mindestens 32 zufällige Zeichen>'
export MCP_BRIDGE_URL='http://127.0.0.1:7913'
python -m bridge.mcp_server
```

### QNAP-/Docker-Betrieb mit getrenntem MCP-Image

Der MCP-Prozess wird nicht in der normalen Bridge automatisch gestartet. Ein optionales, ausschließlich für stdio gebautes Image ist vorhanden:

```sh
docker build -f bridge/Dockerfile.mcp -t bambu-spoolman-mcp:local bridge
# Eine separate private Datei (chmod 600) enthält ausschließlich
# MCP_BRIDGE_TOKEN=<derselbe starke Wert wie in der Bridge-Konfiguration>.
# Nur für die Dauer der MCP-Sitzung als stdio-Prozess starten:
docker run --rm -i --network container:bambu-spoolman-bridge \
  --env-file /GESCHUETZTER/PFAD/mcp.env bambu-spoolman-mcp:local
```

Damit läuft die MCP-Bridge auf demselben Loopback-Netzwerk wie der Hauptcontainer. Den vollständigen Gerätezugangs-/Inventar-Datenbestand **nicht** in den MCP-Container mounten und ihm keine weiteren Zugangsdaten oder Docker-Socket-Rechte geben. Der MCP-Client muss diesen stdio-Befehl direkt beziehungsweise über eine sicher konfigurierte SSH-Sitzung starten. Dieser Weg liefert keinen öffentlichen MCP-Endpunkt und aktiviert keine Cloud-Anbindung.

Die Bridge selbst muss mit demselben `MCP_BRIDGE_TOKEN` gestartet werden. Im Docker-/NAS-Betrieb muss der MCP-Prozess im **gleichen Netzwerk-Namespace wie die Bridge** laufen, wenn diese den Standard-Loopback-Endpunkt `127.0.0.1:7913` nutzt, beispielsweise über eine passende lokale Sidecar-/Container-Konfiguration. Das MCP-Programm kann nur auf Loopback-URLs zugreifen. `stdio` wird normalerweise vom MCP-Client gestartet, nicht manuell für die interaktive Eingabe verwendet. Die Compose-Datei startet keinen MCP-Server automatisch.

## MCP-Werkzeuge

- `printer_status`: bereinigter Drucker-/AMS-Status (keine Zugangsdaten oder Rohlogs)
- `tapo_status`: Steckdosen-Zustand und Leistungs-/Energiewerte, nur lesend
- `filament_stock`: physische Rollen, Material/Farbe, bekannte Restgewichte
- `print_journal`: letzte Verbrauchsbuchungen
- `print_costs`: bis zu zehn aktuelle Druckkosten-Zusammenfassungen
- `printer_files`: Liste der 3MF-Dateien im Druckerspeicher mit kurzlebigen, signierten Auswahl-Tokens
- `check_print_preparation(file, plate, mapping)`: **ausschließlich lesende Simulation** aus bereits vollständig im Dateidialog geprüften/cachierten 3MF-Metadaten
- `request_tapo_power(action)`: `on` oder `off` zur Freigabe im Web anmelden, **schaltet noch nicht**
- `tapo_power_request_result(request_id)`: Freigabe-/Ergebniszustand lesen

## Druckvorbereitung in der GUI

In **Druckerspeicher → Dateidetails** nach vollständig geladener geslicter 3MF: **Filamentbedarf & AMS prüfen (Simulation)**. Das Werkzeug vergleicht Platten-/Düsen-/Druckermodelldaten, Slicer-Filamentpositionen, exakt gepflegte Materialbezeichnungen und Hex-Farben, aktuelle AMS-Schachtmeldungen mit tatsächlich zugeordneten physischen Rollen und deren Bestandsgewichten. Gleichartige Positionen auf derselben Rolle werden aufsummiert. Ohne eindeutige Daten lautet der Status *Prüfen*, nicht *OK*. Ist eine Rolle zu knapp, wird dies ausdrücklich gemeldet. Alternative Lagerrollen werden nur bei exakter Material-/Farbbereinstimmung und bekannter ausreichender Menge als manuell zu prüfende Hinweise dargestellt.

Unvollständige 3MF, unbekannte Mengen oder Restbestände sowie offline/alte AMS-Meldungen erzeugen keine positiven Freigaben. Der gelesene Slicer-Grammwert enthält **keinen pauschalen Puffer** für Spülen/Kalibrierung, Druckfehler und andere Abweichungen. Ein grünes/plausibles Prüfergebnis ist **keine Druckfreigabe**. Die sichere vorhandene Start-Review samt einmaliger Bestätigung bleibt ein eigenständiger Vorgang; die Vorprüfung schreibt keine Bestände, löst keinen Druckstart und sendet keine MQTT-Schreibbefehle.

## Prüf- und Deploymentgrenzen

Erst automatisierte Tests und Testinstallationen mit temporären Daten ausführen. Danach Backup, Testimport, Sicherung der Produktivkonfiguration, Rollback-Plan, explizite Freigabe. Echte Druckstarts, Steckdosenschaltungen und AMS-Änderungen nur nach bewusster Hardwarefreigabe testen. MCP-Token nicht in Logs exportieren. Nach Aktivierung Rechte auf lokale Web-/MCP-Endpunkte, Session-CSRF, Freigabeablauf, Frischeprüfung und Gerätzustandsbestätigung kontrollieren. Produktions-Deployment wird in diesem Entwicklungszweig nicht vorgenommen.
