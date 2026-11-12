# Plus-Betrieb, Umschaltung und Wiederherstellung

## Bestandssystem und Sicherungen

`INVENTORY_BACKEND=spoolman` erhält den bisherigen REST-Buchungsweg. `internal` verwendet ausschließlich SQLite; es gibt keinen automatischen Rückfall auf Spoolman. Die interne Betriebsart startet nur mit einem offline angelegten Umschaltmarker. Dieser enthält die höchste alte Job-Zeilennummer. Der interne Buchungsservice weist sämtliche älteren Jobs zurück, einschließlich UNKNOWN, Dry-Runs und alter erfolgreicher Buchungen.

Vor jeder Umschaltung: vollständige Archive beider Anwendungsverzeichnisse einschließlich privater Konfigurationen, Daten und Zusatzdateien; konsistente SQLite-Backups über die SQLite-Backup-API; vollständiger Spoolman-API-Export mit Herstellern, Produkten und `allow_archived=true`; Container-Inspect und Sicherung des alten Images. Prüfsummen und SQLite-Integrität prüfen. Backups enthalten Geheimnisse und gehören in ein geschütztes Verzeichnis, außerhalb von Git. Mindestens eine zweite Kopie auf einem anderen System aufbewahren.

## Testimport

Eine Kopie des Bridge-Backups verwenden. Im Plus-Image ausführen:

```sh
python -m bridge.migrate_inventory --database /check/candidate.sqlite3 \
  --snapshot /check/spoolman-snapshot.json --mapping /check/mapping.json \
  --printer "$BAMBU_SERIAL" --activate
```

`--activate` bestätigt, dass die schreibende Bridge gestoppt beziehungsweise dies ausschließlich eine Testkopie ist. Ein Import überschreibt keine vorhandenen Bestände. Derselbe unveränderte Snapshot ist wiederholbar; ein geänderter Snapshot benötigt eine neue leere Inventardatenbank. Hersteller-/Produktreferenzen, doppelte IDs, nichtendliche und negative Gewichte werden vor dem Schreiben geprüft. Unbekannte Gewichte bleiben NULL. Rohdaten und unbekannte Felder bleiben im Importlauf und je Quelldatensatz erhalten. Es werden keine historischen Verbräuche erfunden oder erneut gebucht.

Danach Hersteller-/Produkt-/Spulenzahl, archivierte Spulen, bekannte Gesamtgewichte und Anzahl unbekannter Gewichte vergleichen. Die vollständigen alten `jobs`-/`deductions`-Zeilen vor/nach dem Test müssen identisch sein. `PRAGMA integrity_check` muss `ok` und `foreign_key_check` keine Fehler liefern. AMS-Zuordnungen müssen über dauerhafte Spoolman-Quellreferenzen auf interne IDs abgebildet sein. Alle Webansichten und den JSON-Export am Kandidaten testen.

## Finale Umschaltung

1. Wartungsfenster: Bridge stoppen, offene Jobs und Buchungen ausschließen. UNKNOWN bleibt historische Referenz und wird nicht wiederholt. Ein gerade laufender Druck ist kein geeigneter Umschaltzeitpunkt.
2. Finalen API-Snapshot erzeugen; Spoolman stoppen. Vollsicherungen aktualisieren. Den Snapshot mit der nun ruhenden SQLite-Quelle vergleichen, damit Änderungen zwischen API-Export und Stopp auffallen.
3. Bridge-Datenbank erneut kopieren und denselben geprüften Import-/Aktivierungslauf am Kandidaten ausführen. Bestehende Journalzeilen und Mengen erneut vergleichen. Originaldatenbank und eventuelle WAL-/SHM-Dateien sichern; nur den geprüften Kandidaten einsetzen.
4. Plus-Image über Compose mit `INVENTORY_BACKEND=internal` starten. Für den ersten Funktionstest Simulation in den SQLite-Einstellungen aktivieren. Health, Inventar, Zuordnungen, Einstellungen und Export prüfen.
5. Gewünschten Simulationsmodus über `/settings` setzen. Historische Dry-Runs bleiben Simulationen. Spoolman bleibt gestoppt; die interne Datenbank ist das einzige schreibende Bestandssystem.

`BRIDGE_IMAGE` kann das freigegebene Image festlegen. Das vorhandene externe Docker-Netzwerk darf erhalten bleiben, auch wenn der interne Backendweg keine Spoolman-HTTP-Anfragen mehr ausführt.

## Rollback

Vor einem Rollback beide schreibenden Anwendungen stoppen und den aktuellen Plus-Stand vollständig sichern. Zuerst `consumption_entries`, `inventory_movements` und die seit `settings.cutover_at` angelegten Jobs exportieren.

**Ohne neue Buchungen/Korrekturen:** das alte Image und die gesicherte private Konfiguration sowie Bridge-/Spoolman-Daten zurücksetzen. WAL-/SHM-Dateien müssen zum selben Sicherungssatz gehören. Zunächst die alte Bridge in Simulation starten, Integrität/Journal prüfen und erst dann den vorherigen Betriebsmodus wieder aktivieren.

**Mit neuen Buchungen/Korrekturen:** keine automatische Rücksetzung und kein Wiederholen alter HTTP-Verbrauchsaufrufe. Für jede Spule über `(source_system, source_id)` den eingefrorenen Spoolman-Restbestand, Plus-Verbrauch, Korrekturen und aktuellen Plus-Restbestand abgleichen. Neue interne Spulen benötigen zunächst eine explizite externe Zuordnung. Die finalen Restbestände in Spoolman unter Wartung als absolute Bestandskorrekturen übernehmen und dokumentieren; unklare HTTP-Ergebnisse vor Wiederholung anhand des tatsächlichen Bestands prüfen. Plus-Jobs und Buchungsjournal dauerhaft archivieren. Die alte Bridge darf diese Jobs nicht erneut verarbeiten. Erst nach vollständigem Mengenabgleich genau ein System wieder schreibend starten. Eine alte DB-Kopie allein würde neue Verbräuche verlieren und ist deshalb kein zulässiger Rollback.

## Inventar, Einstellungen und Austausch

`/inventory` trennt Hersteller, Filamentprodukte, physische Spulen und Lagerorte. Bearbeiten lädt den gewählten Datensatz in das Formular. Bekannte Bestände werden ausschließlich durch den Buchungs-/Korrekturservice geändert. Produkt-Export unter `/inventory/export/products` benötigt keine Auswahl einzelner Spulen. `/inventory/export` liefert ein versioniertes vollständiges JSON mit Journal; Sitzungsschlüssel und Laufzeit-Geheimnisse sind ausgeschlossen. `bridge.inventory_exchange.restore_inventory` stellt diesen Export nur in eine leere, initialisierte Datenbank wieder her; Import und Fremdschlüsselprüfung sind atomar. Vollständige Notfallwiederherstellung erfolgt bevorzugt aus dem SQLite-/Dateibackup.

Simulation und automatische Farb-/Materialzuordnung werden unter `/settings` in SQLite gespeichert und unmittelbar angewendet. Drucker-/Tapo-Zugangsdaten bleiben in geschützter Laufzeitkonfiguration. Tapo unterstützt `TAPO_HOST_FILE`, `TAPO_USERNAME_FILE` und `TAPO_PASSWORD_FILE` beziehungsweise entsprechende Umgebungsvariablen.

Eine ausführliche Anleitung für den Alltag steht in der [Bedienungsanleitung](user-guide.md).

## Geräte und Grenzen

Die A1/P1-Kamera wird unter `/camera` bei Bedarf als MJPEG angezeigt. Schließen, Seitenwechsel und Ausblenden der Seite beenden die Verbindung. Eine aktive Anzeige ist gleichzeitig möglich; Fehler betreffen nur den Kamerarequest. Die eigenständige Implementierung basiert auf der dokumentierten [OpenBambuAPI-Videoprotokollspezifikation](https://github.com/Doridian/OpenBambuAPI/blob/main/video.md); es wurde kein Bambuddy-Frontend übernommen.

Tapo läuft einschließlich nativer Bibliothek in einem kurzlebigen Hilfsprozess und liest vor jedem Schalten den Gerätezustand. Bibliotheksabstürze und Zeitüberschreitungen bleiben dadurch von Flask und Inventarworker getrennt. Verwendet wird [tapo 0.12.2](https://github.com/mihai-dinculescu/tapo/releases/tag/v0.12.2); die bisherige Version 0.8.2 konnte die vorhandene Geräteantwort nicht lesen. Der Hilfsprozess beendet sich nach vollständig abgeschlossener Anfrage ohne die auf dem NAS fehlerhafte native Interpreter-Finalisierung. Abschalten benötigt einen innerhalb von 30 Sekunden frisch empfangenen MQTT-Zustand IDLE oder FINISH; OFFLINE, RUNNING, PAUSE und veraltete Daten sperren die Aktion. Keine automatische Abschaltung. Legacy-Clients unter `integrations/tapo` verwenden dieselbe externe Konfiguration und prüfen für das Abschalten den Plus-Druckerstatus.

Die bisherige 3MF-Gewichtsauswertung, Abbruchschätzung und beobachteten AMS-Wechsel bleiben erhalten. Eine präzise layerbasierte Extrusionsaufteilung für physische Spulenwechsel ist damit noch nicht nachgewiesen. Dafür fehlen verifizierte reale Layer-/Wechseldaten; solche Mengen werden weiterhin ausdrücklich als Schätzungen behandelt. X1/RTSP-Kameras sind nicht Bestandteil des A1/P1-Kameraclients. Geräteprüfungen müssen bei erreichbarem Drucker nachgeholt werden.
