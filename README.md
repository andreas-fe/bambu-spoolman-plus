# Bambu → Spoolman Bridge

Lokale Docker-Bridge für Bambu Lab A1/A1 mini mit AMS Lite und eine **bereits laufende Spoolman-Instanz**. Nach `FINISH` werden die Slicer-Verbrauchswerte aus der gedruckten 3MF-Datei pro Filament der zugeordneten Spoolman-Spule belastet. Abgebrochene Drucke werden zur manuellen Korrektur protokolliert.

## Einrichtung

Voraussetzungen: Docker Compose v2, erreichbarer Drucker mit LAN-Zugangscode (MQTT TLS 8883, implizites FTPS 990 sowie passive Datenports), vorhandene Spulen in Spoolman. Die Compose-Datei benötigt den Namen eines bereits bestehenden Docker-Netzwerks, an dem Spoolman angeschlossen ist (`SPOOLMAN_NETWORK`). Diesen Namen zeigt `docker inspect spoolman --format '{{json .NetworkSettings.Networks}}'`.

```sh
cp .env.example .env
# .env mit lokalen Werten bearbeiten
mkdir -p data
docker compose up -d --build
```

Weboberfläche: `http://<NAS-IP>:7913`. Dort die vier AMS-Slots und optional die externe Spule zuweisen. Der Dienst startet standardmäßig mit `DRY_RUN=true`: Das Journal zeigt simulierte Verbräuche; Spoolman bleibt unverändert. Nach einem erfolgreich geprüften Druck `DRY_RUN=false` setzen und den Container neu erstellen:

```sh
docker compose up -d --force-recreate bambu-bridge
```

Historische Simulationen werden **nicht** nachgebucht. Manuelle Buchungen unterliegen ebenfalls dem Dry-Run-Modus.

## Spoolman-Verbindung

`SPOOLMAN_URL` muss aus dem Bridge-Container erreichbar sein, beispielsweise `http://<NAS-IP>:7912`. Der Name `http://spoolman:8000` funktioniert nur, wenn beide Container ein gemeinsames Docker-Netzwerk mit diesem DNS-Namen verwenden. Diese Compose-Datei verbindet die Bridge zusätzlich mit `SPOOLMAN_NETWORK`, betreibt ausschließlich die Bridge und verändert den vorhandenen Spoolman nicht. Auf QNAP kann die Verbindung zur eigenen NAS-IP aus dem Container blockiert sein; dann die direkte interne Adresse im gemeinsamen Netzwerk verwenden. `SPOOLMAN_BROWSER_URL` legt unabhängig davon den erreichbaren Browser-Link fest.

Die Oberfläche zeigt den Verbindungsstatus. Die Buchung verwendet Spoolmans `PUT /api/v1/spool/{id}/use` mit `use_weight` in Gramm. API-Referenz: [Spoolman](https://github.com/Donkie/Spoolman/blob/master/spoolman/api/v1/spool.py).

## AMS- und Druckdaten

Slicer-Filament-IDs sind keine physischen Slot-IDs. Die Bridge verwendet `ams_mapping` bzw. `ams_mapping2` aus den MQTT-Druckdaten. Bei fehlender Zuordnung wird **kein Slot geraten**; der Auftrag wartet mit `WAITING_MAPPING`. Ob die Firmware diese Felder liefert, muss mit einem realen Druck geprüft werden. Mehrmaterialdruck und automatischer Spulenwechsel können ohne vollständige Zuordnung nicht zuverlässig abgerechnet werden.

Bei Dateien mit mehreren Druckplatten wird nur die durch `param=Metadata/plate_N.gcode` identifizierte Platte abgerechnet. Fehlt die Auswahl, wartet der Auftrag auf eindeutige Metadaten. Die heruntergeladene Datei wird nur im Arbeitsspeicher gehalten. Größenlimits: `MAX_3MF_BYTES` und `MAX_XML_BYTES`.

Die Slot→Spule-Zuordnung wird beim Druckstart gespeichert; spätere Spulenwechsel verändern bereits vorgemerkte Buchungen nicht. Fehlende Spulenzuordnungen können durch Speichern in der Oberfläche ergänzt werden. MQTT-Deltas erhalten bereits bekannte Werte. `FINISH` ohne zuvor erkannten Druck löst keine Buchung aus.

## Journal und Wiederaufnahme

SQLite speichert Drucke und jede einzelne Buchungsposition dauerhaft. Ein wiederholtes `FINISH` löst keine zweite Buchung aus. Bekannte Verbindungsfehler vor einer Buchung (`RETRY`) sowie fehlende Metadaten werden im Minutentakt erneut geprüft. Erfolgreiche Positionen werden dabei nicht wiederholt.

Nach einem unklaren Antwortverlust, HTTP 5xx oder Neustart während einer API-Anfrage lautet der Status `UNKNOWN`. Hier ist eine manuelle Bestandsprüfung in Spoolman erforderlich: Die Verbrauchs-API bietet keine Idempotenzkennung, daher wäre eine automatische Wiederholung möglicherweise eine Doppelbuchung. `REJECTED` bedeutet eine abgelehnte Anfrage. `/api/status` liefert zusätzlich die Auftragszustände und Fehlerdetails. `/health` prüft die Anwendung, unabhängig von der Drucker-Erreichbarkeit.

## QNAP-Pfade und Deployment

Linux unterscheidet Groß-/Kleinschreibung. `/share/Daten/Container/...` und `/share/daten/container/...` können verschiedene Verzeichnisse sein. `BRIDGE_DATA_DIR` bezeichnet den **NAS-seitigen** Datenpfad, beispielsweise `/share/Daten/Container/bambu-spoolman/data`. Ein Notebook-Mount wie `/mnt/NAS/...` gehört nicht in den NAS-Container-Mount.

Das Deployment läuft vom Notebook per SSH-Schlüssel:

```sh
python3 scripts/deploy.py \
  --host '<SSH-Benutzer>@<NAS-IP>' \
  --key ~/.ssh/bambu_spoolman_deploy \
  --target /share/Daten/Container/bambu-spoolman \
  --legacy /share/daten/container/bambu-spoolman \
  --spoolman-url 'http://spoolman:8000' \
  --spoolman-network '<vorhandenes-Spoolman-Netzwerk>' \
  --spoolman-browser-url 'http://<NAS-IP>:7912'
```

Nach der ersten Migration bei weiteren Updates `--legacy` weglassen. Ein mit Passphrase geschützter Schlüssel muss in einem SSH-Agenten geladen sein, dessen `SSH_AUTH_SOCK` im aufrufenden Terminal gesetzt ist.

Das Skript baut das Image vor dem Stoppen, verweigert Updates bei einem erkannten laufenden/pausierten Druck, stoppt ausschließlich die Bridge, sichert Konfiguration und Daten konsistent unter `backups/<Zeitstempel>/`, übernimmt die Daten und startet die neue Version. Abschließend werden Anwendung, Mount und Spoolman aus dem Container geprüft. Bei Fehlern wird der vorherige Stack wieder gestartet. Bei Pfadmigration bleibt das Quellverzeichnis als Rückfallbestand erhalten und wird nicht mehr verwendet.

Die Sicherung enthält Zugangsdaten und Betriebsdaten und gehört ausschließlich auf das private NAS. `.env`, Datenbanken und Backups sind von Git ausgeschlossen. Optionale Web-Anmeldung: `WEB_USERNAME` und `WEB_PASSWORD` gemeinsam setzen.

## Tests

```sh
python3 -m venv .venv
.venv/bin/pip install -r bridge/requirements.txt pytest
.venv/bin/python -m pytest -q
```

Die Tests verwenden temporäre SQLite-Datenbanken und simulierte API-Aufrufe. Sie verändern keine echten Spoolman-Bestände. Python 3.11 wird zusätzlich in GitHub Actions geprüft.

## Lizenz

MIT
