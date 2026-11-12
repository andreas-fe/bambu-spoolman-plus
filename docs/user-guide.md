# Bedienungsanleitung: Spulen, Kamera, Licht und Tapo

## Orientierung

| Seite | Aufgabe |
|---|---|
| `/` – Drucker & Verbrauch | Druckerstatus, AMS-Zuordnung, Aufträge und Buchungsjournal |
| `/inventory` – Filamente & Spulen | Spulen finden, Produkte pflegen und Bestände korrigieren |
| `/camera` – Kamera | Livebild und Druckerlicht |
| `/devices` – Steckdose & Energie | Tapo P110 schalten und Messwerte anzeigen |
| `/settings` – Einstellungen | Simulation und automatische Material-/Farbzuordnung |

Die Oberfläche verwendet eine gemeinsame Navigation und passt sich kleineren Bildschirmen an. Technische Logs sind in der Druckübersicht unter Diagnose & Live-Logs erreichbar.

## Spulenverwaltung im Alltag

Ein **Filamentprodukt** beschreibt Hersteller, Material, Produktreihe und Farbe. Eine **physische Spule** ist eine konkrete Rolle dieses Produkts mit eigenem Restbestand, Lagerort und optionaler Charge. Mehrere gleiche Rollen verwenden dasselbe Produkt, besitzen aber getrennte Bestände.

1. Unter Filamente & Spulen nach Produkt, Farbe, Hersteller oder Lagerort suchen. Material und Hersteller lassen sich zusätzlich filtern.
2. Fehlt das Produkt, im Reiter Filamentprodukte ein neues Produkt anlegen. Material, Farbe, Gewicht einer vollen Rolle und Hersteller prüfen. Über „Anderes Material …“ können beispielsweise PLA+ oder besondere Mischungen eingetragen werden. Erweiterte Angaben enthalten Variante, Durchmesser, Dichte und Artikelnummer.
3. Optional im öffentlichen Katalog suchen, einen Treffer als Vorlage übernehmen, Angaben prüfen und speichern. Ein noch fehlender Kataloghersteller wird beim Speichern angelegt. Ein bereits importiertes Katalogprodukt nicht erneut anlegen, sondern für die nächste Rolle wiederverwenden.
4. Über „Spule hinzufügen“ das passende Produkt und gegebenenfalls den Lagerort wählen. Den tatsächlichen Eröffnungsbestand in Gramm eintragen. Das Produktgewicht einer vollen Rolle ist kein gemessener Restbestand. Fehlende Gewichte bleiben unbekannt.
5. Charge, Leergewicht und Notizen bei Bedarf ergänzen. Leere oder ausgemusterte Rollen können archiviert werden.
6. Auf der Druckübersicht die konkrete Rolle dem AMS-Schacht oder der externen Spule zuordnen und speichern. Nach jedem Rollenwechsel die Zuordnung aktualisieren.

Hersteller und Lagerorte werden in eigenen Reitern gepflegt. Für die Auswahl werden verständliche Namen statt bloßer Datenbanknummern angezeigt.

### Katalogdaten und Kalibrierung

Die Anwendung verwendet derzeit **SpoolmanDB**, nicht 3dfilamentprofiles.com. Die Suche lädt eine feste öffentliche JSON-Datei vom SpoolmanDB-Server; Suchbegriffe werden auf dem eigenen Anwendungsserver ausgewertet. Der Katalog wird sechs Stunden im Arbeitsspeicher zwischengespeichert. Bei Ausfällen kann ein vorhandener älterer Stand verwendet werden; ohne verfügbaren Stand bleibt die manuelle Anlage möglich.

Übernommen werden Produktstammdaten wie Material, Farbe, Hersteller, Gewicht, Dichte und Durchmesser. Quelle und Originaldatensatz bleiben beim Produkt gespeichert. Es erfolgt keine automatische Aktualisierung bestehender Produkte und keine Übertragung von Druckprofilen an Bambu Studio.

Ein Datenbankeintrag ersetzt keine Kalibrierung von Temperatur, Flussfaktor, K-Faktor oder maximalem Volumenstrom auf dem eigenen Drucker. Bügelparameter sind zusätzlich vom Druckprozess abhängig. Diese Druckparameter werden mit dieser Funktion weder kalibriert noch als aktive Slicer-Einstellungen verwaltet.

### Bestandskorrekturen und Export

Für eine Korrektur die konkrete Rolle auswählen, „Hinzufügen“ oder „Abziehen“ wählen und eine positive Grammmenge mit Grund eingeben. Beispiel: Zeigt der Bestand 620 g, die Waage aber 600 g reines Filament, 20 g abziehen. Beim Wiegen zuerst das Gewicht der leeren Spule abziehen. Korrekturen werden journalisiert; ein Bearbeiten des Produktgewichts korrigiert keinen Restbestand.

„Filamentprodukte“ exportiert Hersteller und Produkte; „Gesamtes Inventar“ enthält zusätzlich Spulen, Zuordnungen und Journal. Exporte können eigene Notizen und Bestandsdaten enthalten und sind für private Sicherungen gedacht. Nicht in ein öffentliches Repository hochladen. Wiederherstellung und Migration beschreibt [Plus-Betrieb](plus-operations.md).

Bearbeiten ist nur im aktivierten internen Backend verfügbar. Im Spoolman-Modus ist die interne Ansicht eine Vorschau. Die Umschaltung verlangt eine kontrollierte Offline-Migration; das bloße Ändern einer Umgebungsvariable reicht nicht.

## Kamera und Licht

1. Kamera öffnen und „Livebild öffnen“ wählen. Erst dann wird die Verbindung zum Drucker aufgebaut.
2. Über „Livebild schließen“ die Verbindung beenden. Seitenwechsel oder Ausblenden der Seite beendet sie ebenfalls; bei Bedarf erneut öffnen.
3. „Licht einschalten“ und „Licht ausschalten“ senden einen separaten MQTT-Befehl an das Druckerlicht. Dafür muss kein Livebild laufen.

Eine Erfolgsmeldung erscheint erst nach einer neuen passenden `lights_report`-Rückmeldung des Druckers. Bleibt diese aus, meldet die Oberfläche einen Fehler statt bloß erfolgreiches Senden. Die Rückmeldung ist der gemeldete Gerätezustand, keine optische Messung der Lampe. Das Druckerlicht ist unabhängig von der Tapo-Steckdose.

Benötigt werden ein erreichbarer kompatibler LAN-Kamerastream, korrekte Druckerzugänge und für das Licht eine MQTT-Verbindung. Der Client unterstützt das implementierte A1/P1-Protokoll; X1/RTSP wird nicht unterstützt. Es kann nur eine Kameraanzeige gleichzeitig laufen. Bei „Kamera wird bereits angezeigt“ die andere Ansicht schließen. Bei Verbindungsfehlern LAN-Livebild und Druckerzugang prüfen und erneut öffnen. Ein Kamerafehler stoppt keine Verbrauchsbuchung.

## Tapo P110: Schalten und Energie

### Einrichtung

Die Steckdose muss aus dem Container erreichbar sein. Konfiguration erfolgt privat über `TAPO_HOST`, `TAPO_USERNAME`, `TAPO_PASSWORD` oder bevorzugt über `TAPO_HOST_FILE`, `TAPO_USERNAME_FILE`, `TAPO_PASSWORD_FILE`. Die `_FILE`-Werte zeigen auf im Container lesbare, geschützte Dateien; die Dateien selbst enthalten jeweils den Wert. Beispiele für die Variablennamen stehen in `.env.example`. Zugangsdaten werden nicht in der Oberfläche gepflegt.

Die Seite unterstützt die implementierte P110-Anbindung. Verfügbarkeit einzelner Energiemesswerte hängt vom Gerät beziehungsweise seiner Antwort ab.

### Bedienung und Abschaltsperre

„Einschalten“ schaltet die Steckdose manuell ein. „Ausschalten“ prüft zunächst den letzten ausdrücklich gemeldeten Druckerzustand. Ist dieser älter als 30 Sekunden, wird ein neuer Status angefordert. Erlaubt ist nur bestätigtes `IDLE` oder `FINISH` mit höchstens 30 Sekunden altem Status. Der Hilfsprozess prüft Verbindung, Aktualität und Leerlauf unmittelbar vor dem Abschalten erneut. Laufender oder pausierter Druck, Offline-Zustand, veraltete Daten und fehlende Antwort sperren das Abschalten. Eingeschaltetes Druckerlicht blockiert es nicht.

Bei einer Sperre Druckerstatus und Verbindung prüfen und später erneut versuchen. Ein bereits ausgeschalteter Drucker kann keinen frischen Leerlauf bestätigen. Es gibt keine automatische Abschaltung nach Druckende. Die Sperre prüft den Druckerzustand, keine Abkühltemperatur; vor manuellem Ausschalten die erforderliche Abkühlung berücksichtigen.

### Messwerte und Live-Status

Die Seite zeigt aktuelle Leistung in Watt, Tages- und Monatsenergie in kWh sowie die heutige Laufzeit. Diese Werte stammen von der Steckdose und werden nicht einzelnen Druckaufträgen zugeordnet.

Beim Öffnen erfolgt eine Abfrage. „Status aktualisieren“ liest erneut. „Live-Status aktivieren“ erlaubt wiederkehrende Abfragen alle 5, 10 oder 30 Sekunden; bei unsichtbarem Reiter pausieren sie. Fehlende Werte erscheinen als „—“. Bei einem Fehler werden alte Werte nicht als neu gemessen ausgegeben. Nach einer Schaltaktion wird der Status erneut abgefragt.

Tapo läuft in einem getrennten kurzlebigen Prozess. Fehler oder Zeitüberschreitungen der Gerätebibliothek sollen dadurch die Webanwendung und Bestandsverarbeitung nicht mitreißen.

## Simulation und Verbrauchsbuchung

Unter Einstellungen lässt sich „Verbräuche simulieren“ aktivieren. Neue Druckverbräuche werden dann als Simulation protokolliert; frühere Simulationen werden nach Deaktivierung nicht nachgebucht. Simulation ersetzt keinen Test einer echten Buchung. Sie ist kein globaler Schutzschalter für Geräteaktionen oder manuelle Inventarkorrekturen.

Für die automatische Zuordnung müssen Material und RGB-Farbe im Slicer und im Druckerbestand übereinstimmen. Die Auswahl der tatsächlich eingelegten Rolle bleibt wichtig. Mengen bei Abbrüchen oder rechnerischer Verteilung auf mehrere Rollen können Schätzungen sein. Details: [Filament-Workflow](filament-workflow.md).

## Zugriff und private Daten

Die Anwendung gehört in ein vertrauenswürdiges lokales Netz. Vor Freigabe für andere Geräte `WEB_USERNAME` und `WEB_PASSWORD` setzen; keine ungeschützte Internetfreigabe einrichten. Schreibende Browseraktionen verwenden Sitzungstoken. Nach abgelaufener Sitzung die Seite neu öffnen und die Eingabe wiederholen.

Private `.env`-Dateien, Gerätezugänge, SSH-Schlüssel, Datenbanken, Logs, Exporte und Backups außerhalb von Git aufbewahren. Beispieladressen und Platzhalter vor Ort ersetzen. Die Bereitstellung auf GitHub ist unabhängig vom Deployment auf dem Druckersystem.
