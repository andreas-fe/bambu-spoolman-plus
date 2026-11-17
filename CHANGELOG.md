# Änderungen

## 2026-11-17 — Eindeutige Modellplatten und Druckbettoberflächen

- Schließen bleibt auch bei sehr langen Dateinamen und in der Mobilansicht oben rechts; Dateititel darf umbrechen.
- Modellplattennummer und optionaler Bambu-Studio-Plattenname werden getrennt von der Druckbettoberfläche angezeigt. Oberflächenwerte stammen aus `curr_bed_type` beziehungsweise der platteneigenen `bed_type`-Einstellung; bekannte Werte erhalten die Bambu-Bezeichnungen einschließlich Cool Plate SuperTack. Unbekannte Werte bleiben lesbar, fehlende Werte werden nicht geraten.
- Oberflächenhinweis bereits in vorläufigen Dateidaten, falls vorhanden, und in der Startbestätigung. Die Anzeige beschreibt die gespeicherte Datei und erkennt keine physische Platte; Startbefehl und geslicte Datei bleiben unverändert. Ältere Metadaten-Caches werden beim nächsten Öffnen einmal neu gelesen.
- 225 Tests lokal und im NAS-Container sowie Browserprüfung für lange Titel, Desktop/Mobilansicht, Modellplattennamen und Oberflächenanzeige erfolgreich. Nach gesicherter Aktivierung am NAS mit einer echten 3MF einschließlich Cool Plate SuperTack und anschließendem Cachetreffer bestätigt; Bestände, AMS-Bindungen und Journal unverändert.

## 2026-11-17 — Abbrechbare Dateiprüfung und sichtbare Drucksteuerung

- Schließen, Escape und Seitenwechsel brechen laufende Detaildownloads ab. Daten-/Steuerkanäle werden geschlossen, unvollständige Dateien verworfen und Dateizugriffe wieder freigegeben. Schneller Modellwechsel und verspätete Antworten werden abgefangen; weitere offene Ansichten derselben Datei bleiben berücksichtigt.
- Profil und gemeldeter K-Faktor des gewählten AMS-Schachts direkt im Druckdialog, mit Verknüpfung zur vorhandenen AMS-Profilwahl und Aktualisierung der Druckerangaben. Keine versteckte Profil-/K-Änderung durch bloßes Zuordnen. Geänderte Drucksteuerung macht eine alte Startprüfung ungültig.
- 223 Tests lokal und im NAS-Container bestanden, einschließlich unterbrochenem blockierendem Datenkanal, Freigabe des Dateizugriffs, fehlendem Teilcache, Abbruch vor Registrierung, mehreren Ansichten und Profil-/K-Fingerabdruck. Browserprüfung für Schließen/ESC, verspätete Antworten, schnellen Modellwechsel und Profilanzeige. Am A1 wurde ein laufender 21,5-MB-Transfer nach 8 KiB abgebrochen; die Dateiliste war nach rund 0,9 Sekunden wieder erreichbar. Ein anderes Modell wurde anschließend vollständig geprüft. Vollsicherung, verlustfreier Testimport und unveränderte Bestände/Zuordnungen/Journal bestätigt.

## 2026-11-17 — Direkte lokale Einstellungen und flüssigere Dateiverwaltung

- Details-Dialog mit sofort bedienbaren Optionen und früher Anzeige vorläufiger Platten-/Filamentdaten. Vollständige Prüfung bleibt Startvoraussetzung; bereits gewählte Optionen und Rollen werden erhalten. Kein 3D-Neurendering, vorhandener Metadatencache bleibt wirksam.
- Veraltete Bestandsverwaltungs-Karte und Inventar-Verbindungsanzeige entfernt; tote Vorlagenfelder, alte Bezeichnungen und ungenutzte interne Bestandsabfragen bereinigt.
- Dateiumbenennung im bisherigen Verzeichnis mit Kollisions-/Auftragsschutz, Übernahme vorhandener Cachebilder und Metadaten sowie Historie über frühere Namen.
- Drucker- und Tapo-Verbindungen direkt in den lokalen Einstellungen, ohne zusätzliche Anmeldung. Getrennte private Speicherung, keine Ausgabe gespeicherter Passwörter und keine Aufnahme in Filamentexporte; kontrollierte Übernahme ohne laufenden Druck. Bestehender Betriebsmodus bleibt erhalten.
- 220 Tests lokal und im NAS-Container bestanden. Browserprüfungen für frühe Optionen, gesperrten vorläufigen Start, erhaltene Auswahl, Umbenennen, lokale Verbindungseinstellungen, Passwortmaskierung und Mobilansicht. FTPS-Umbenennen am A1 mit eigenen temporären Dateien geprüft.

## 2026-11-16 — Druckstart, Dateidetails und Flusserkennung

- Geprüfter 3MF-Druckstart mit tatsächlicher Druckplatte, expliziter AMS-/Extern-Zuordnung, Optionen und Bestätigung der freien Druckplatte. Einmalige kurze Startprüfung, erneute Datei-/Zustandsprüfung, Fingerabdruck und Sperre bei unklarem Start; keine automatischen Wiederholungen.
- Hintergrund-Dateidetails mit Dauer, Bedarf, Modell/Düse, Objekten, Hinweisen und plattenspezifischem Bild; begrenzter persistenter Metadatencache. Dateibezogene Druck-/Verbrauchshistorie, Listen-/Kachelansicht, Alters-/Größenfilter und Platzberechnung für bewusst ausgewählte Löschungen.
- Frisch gemeldete dynamische Flusskalibrierung wird dauerhaft je Auftrag erfasst und im Journal von bloßer Anforderung beziehungsweise unbekanntem Zustand unterschieden. Zuschlag wahlweise pauschal oder nur bei positivem Nachweis; Regel bleibt pro Auftrag eingefroren. Keine rückwirkende Umbuchung.
- MQTT-Wiederanlauf schützt Bestandsbindungen eine Minute lang vor vorübergehend leeren AMS-Startmeldungen. Vollständige Material-/Farbänderungen und später bestätigte Leerbelegung bleiben wirksam.
- 211 Tests lokal und im NAS-Container bestanden; Browserprüfung mit simuliertem Druckstart, Zuordnung, Plattenwahl, Optionsänderungen, Einmalbestätigung, Historie, Filtern, Kachelpräferenz und Mobilansicht. Echten Druckstart nicht zu Testzwecken ausgelöst.

## 2026-11-16 — Große 3MF-Vorschauen und umschaltbares Design

- Vorschaubilder werden direkt während des Lesens der ZIP-Einträge erkannt. Nach dem Bild und dessen erfolgreicher Prüfsummenprüfung wird der Transfer beendet; der nachfolgende G-Code muss nicht heruntergeladen werden. Keine REST-Unterstützung am Drucker erforderlich.
- Stream-ZIPs mit Datenbeschreibungen, gespeicherten oder Deflate-Einträgen und ZIP64 werden unterstützt. Die bisherige 24-MiB-Grenze der Gesamtdatei entfällt. Stattdessen gelten Grenzen für gelesenen Archivanfang, entpackte Daten, Bildgröße und Laufzeit. Verbindungsfehler werden nicht mehr als vermeintlich fehlendes Bild erklärt.
- Helles und dunkles Design mit browserseitig gespeicherter Auswahl in allen Bereichen; angepasste Eingaben, Karten, Tabellen, Hinweise und AMS-Grafik. Tatsächliche Filamentfarben bleiben erhalten.
- 192 Tests lokal und im NAS-Container bestanden; Browserprüfung für sechs Bereiche, Speicherung, Mobilansicht und unverändertes Vorschau-Caching. Die drei betroffenen realen Vorschauen konnten vor dem Deployment mit dem neuen Leser in etwa einer Sekunde geladen werden; nach dem Deployment wurden alle drei über die App samt Cachetreffer und Inline-Bild verifiziert. Vollständige Sicherung, verlustfreier Testimport, Health und unveränderte Bestände/Zuordnungen/Journal geprüft. Keine Druckerdateien verändert.

## 2026-11-16 — Persistente Modellminiaturen im Druckerspeicher

- Vorschaubilder werden unabhängig vom Inventar im NAS-Datenverzeichnis zwischengespeichert und neben dem Vorschau-Button angezeigt. Bereits vorhandene Bilder benötigen beim Öffnen keinen neuen FTPS-Transfer.
- Optionales serielles Nachladen für sichtbare 3MF-Zeilen; standardmäßig aus. Neue Downloads nur im bestätigten Leerlauf; fehlender Leerlauf oder Verbindungsprobleme pausieren die Automatik.
- Cache-Gültigkeit 30 Tage, maximal 200 Bilder / 64 MiB Bilddaten mit Verdrängung. Dateipfad, Größe, Datum und Druckeridentität trennen Einträge. Erfolgreiches Löschen einer Druckdatei entfernt deren Bildcache. Eigene bestätigte Aktion zum Leeren ausschließlich des App-Bildcaches.
- 179 Tests lokal und im NAS-Container bestanden, einschließlich Persistenz, Grenzen, Ablauf und Cachetreffer ohne Druckerverbindung. Browserprüfung für Inline-Miniaturen, sichtbares Nachladen, Wiederverwendung, getrenntes Leeren und Mobilansicht. Dokumentation unterscheidet App-Bildcache von Druckdateien im Drucker-Verzeichnis /cache; keine automatische Druckerbereinigung.

## 2026-11-16 — Eigener Reiter Druckerspeicher

- Lokale FTPS-Dateiverwaltung für Hauptverzeichnis, Druckcache und Modelle: Suche, Größe, Datum, Sortierung und explizite Mehrfachauswahl für reguläre Druckdateien.
- Bedarfsgesteuerte Vorschau aus eingebetteten Bambu-Plattenbildern beziehungsweise Thumbnails; begrenzte Archive/Bilder und Laufzeiten. Kein interaktives 3D-Rendering und keine automatischen Massendownloads.
- Signierte Dateiauswahl, erneute Prüfung von Größe/Datum, Schutz laufender/offener Aufträge und explizite Löschbestätigung. Löschung nur bei bestätigtem Leerlauf, keine automatische Bereinigung und kein Zugriff auf beliebige Systempfade. Teilfehler werden mit bereits bestätigten Löschungen angezeigt; keine automatische Wiederholung.
- 177 Tests lokal und im NAS-Container bestanden; Browserprüfung mit künstlichen Dateien. Journal und Bestände werden durch Speicheraktionen nicht verändert. Produktive Dateien wurden nicht zu Testzwecken gelöscht.

## 2026-11-16 — AMS-Lite-Grafik näher am Original

- Schlankere, höhere Spulen mit ausgeprägten grauen Flanschen, direkter Materialbeschriftung und Bearbeiten-Symbol. Kürzere Arme rücken die Rollen deutlich näher an den zentralen Ständer.
- Schachtbezeichnungen A1–A4, korrekte Reihenfolge, anklickbare Auswahl und aktive Filamentführung bleiben erhalten. Materialtext und Symbol wechseln passend zur Spulenhelligkeit zwischen heller und dunkler Darstellung. Externe Spule verwendet dieselbe Formensprache.
- Browserprüfung für Auswahl, Filamentweg und mobile Darstellung; aktueller Beispielscreenshot ergänzt.

## 2026-11-16 — Metadatenlose Aufträge ausdrücklich schließen

- Für beendete, ungebuchte PRINT-Aufträge in WAITING_METADATA ohne gespeicherte Verbrauchsmengen gibt es „Auftrag ohne Buchung schließen“ mit ausdrücklicher Bestätigung.
- Neuer Endzustand CLOSED_NO_USAGE entfernt den Auftrag aus offenen Aufgaben und Wiederholungsversuchen. Historie bleibt erhalten; das gemeinsame Journal zeigt „Ohne Buchung geschlossen“ mit Menge „—“ statt erfundener Null- oder Verbrauchswerte.
- Laufende/gerade verarbeitete Aufträge, vorhandene Mengen, Buchungspositionen und gespeicherte Verbrauchspläne sind geschützt. Keine Bestandsänderung, keine Geräteaktion und kein automatisches Ausschalten durch diesen Abschluss.
- 174 Tests lokal und im NAS-Container einschließlich Einmalabschluss, Bestätigungsschutz, Verbrauchsschutz, Journalnachweis und Abschaltsperre bestanden.

## 2026-11-16 — Kompaktes, einklappbares Buchungsjournal

- Journal startet mit zehn Einträgen; „10 weitere Einträge laden“ ergänzt die nächste Seite bis zum Ende. Die gespeicherte Historie wird weder gelöscht noch gekürzt.
- Verbrauch und manuelle Korrekturen werden gemeinsam datenbankseitig paginiert. Signierte Positionscursor, eindeutige Sortierung auch bei gleichen Zeitstempeln und passende Zeitindizes vermeiden übersprungene oder doppelte Einträge beim Nachladen.
- Journal lässt sich wie Diagnose & Live-Logs einklappen; der Zustand wird im Browser gespeichert. Bereits nachgeladene Historie bleibt bei Live-Updates stehen, neue Einträge sind über einen ausdrücklichen Rücksprung erreichbar. Fehler und doppelte Klicks verändern geladene Seiten nicht.
- Statusantworten und initiale Ansicht liefern nur die neueste Zehnerseite. Tests für gemischte Historie, identische Zeitstempel, neue Buchungen während Pagination, ungültige Cursor und leere Historie ergänzt. 170 Tests lokal und im NAS-Container bestanden; Browserprüfung für Nachladen, Fehlerwiederholung, Einklappen und mobile Darstellung erfolgreich.
- Dokumentation präzisiert Flusskontrolle: Eine beobachtete dynamische Kalibrierungsphase kann die Ausführung belegen, fehlende Meldung jedoch kein sicheres Nein. Aktuell keine dauerhafte pro-Auftrag-Erkennung und keine automatische Änderung des manuellen 2-g-Zuschlags.

## 2026-11-16 — Dynamische Drucksteuerung und gerichteter AMS-Abgleich

- Neuer AMS-Unterbereich: Drucker mit Farbe, Material, Profil und K-Faktor links; Bestandsspule und gewünschte Drucksteuerung rechts; explizite Übertragungsrichtungen in der Mitte. Mobil steht die Auswahl vor der Aktion. Der Filter für passende Rollen ist dauerhaft sichtbar.
- „← An Drucker senden“ übernimmt Material, Farbe und eine bewusst gewählte dynamische Drucksteuerung. Standard löst das gespeicherte Profil, alternativ werden vorhandene Druckerprofile mit Namen, K-Faktor und passender Düse angeboten. Kein automatisches Erben des alten Rollenprofils, keine geratenen K-Werte und keine automatische Kalibrierung.
- Druckerprofile werden gelesen und vor gespeicherter Profilauswahl erneut validiert. Materialvorlage, Düse und Inhalt gehören zur bestätigten Vorschau. Zwei getrennte Befehle mit separater Rückbestätigung; neue Bestandsbindung erst nach vollständigem Erfolg. Teilfehler werden ausdrücklich angezeigt und nicht automatisch wiederholt.
- „Automapping →“ bereitet ausschließlich die Bestandszuordnung vor. Eindeutige Treffer verlangen Bestätigung; mehrere passende Rollen werden zur Auswahl angezeigt, ohne automatisch eine physische Rollenidentität zu behaupten.
- Aktive und angeforderte Schächte bleiben geschützt. Weitere Regressionstests für Profilkontext, veraltete Listen, neue Schachtmeldungen, Ablehnungen und Teilfehler; 167 Tests lokal und im NAS-Container bestanden; Browserprüfung für Richtungen, Profile, Mehrdeutigkeit und mobile Ansicht. Hardware: Profilliste gelesen und vorhandenes Profil auf inaktivem Schacht während Druck frisch bestätigt; kein Wechsel auf ein anderes Profil, keine Bewegung und keine Bestandsänderung.

## 2026-11-16 — Nachträgliche Auftragszuordnung und Datenbanksperren

- Eine bestätigte Auftragszuordnung ergänzt nun auch ursprünglich fehlende Schacht→Bestandsrollen im Auftragssnapshot. Bereits erfasste Rollen bleiben erhalten; ergänzt werden ausschließlich ausdrücklich gewählte Schächte. Neue Rollen werden bei Bedarf in den Bestandssnapshot aufgenommen.
- Vorhandene 3MF-Verbrauchsdaten bleiben verwendbar, auch wenn die Druckdatei nicht mehr erreichbar ist. Bereits versuchte oder abgeschlossene Buchungen können weiterhin nicht neu zugeordnet werden.
- Unveränderte AMS-Statusmeldungen benötigen keine SQLite-Schreibsperre mehr. WAL wird bei der Datenbankinitialisierung statt beim Öffnen jeder Verbindung gesetzt. Änderungen werden unter einer Schreibtransaktion erneut geprüft.
- Verständlicher Hinweis bei fehlender Bestandsrolle im Auftrag. Regressionstests für nachträgliche Zuordnung, Snapshot-Erhaltung, Wiederholungsschutz und Statusabgleich bei belegter Schreibsperre ergänzt. 161 Tests lokal und im NAS-Container bestanden. Ein gesicherter betroffener Auftrag wurde auf einer isolierten Kopie ohne erneuten Dateidownload exakt einmal abgerechnet.

## 2026-11-16 — Gewichtsrundung, Flusszuschlag und vollständige Dokumentation

- AMS-Rollenauswahl zeigt eine Nachkommastelle mit deutschem Dezimalkomma statt binärer Fließkommareste. Neue Restbestände aus Verbrauch, Teilkorrektur und Wiegekorrektur werden auf zwei Nachkommastellen gerundet; historische Journalmengen bleiben unverändert.
- Abschaltbare Option „Flusskontrolle berücksichtigen · 2 g je Farbe“: einmalig 2 g je Slicer-Filamentposition mit positivem geplantem Verbrauch. Einstellung wird beim Erkennen des neuen Auftrags festgeschrieben; Zuschlag gehört zum eingefrorenen Plan und erscheint als Schätzung. Standardmäßig aus; keine automatische Aktivierung oder Erkennung der Druckerkalibrierung.
- Bei Abbruch bleiben Farben und Grundmengen geschätzt; jede positive Position erhält den vollen Zuschlag, null geschätzter Verbrauch keinen Zuschlag. Getrennte gleichfarbige Slicer-Positionen werden getrennt behandelt.
- README, Bedienungs-, Architektur-, Betriebs- und Zuordnungsdokumentation aktualisiert. LAN-only plus Entwicklermodus als Voraussetzung für AMS-Schreibaktionen hervorgehoben; Grenzen der Firmware-/Kalibrierungserkennung beschrieben.
- Zehn aktuelle Bereichsscreenshots aus der echten Oberfläche mit ausschließlich künstlichen Daten ergänzt.
- 158 Tests lokal und im NAS-Container bestanden; Health und tatsächliche Gewichtsanzeige sowie verfügbare, deaktivierte Zuschlagsoption geprüft.

## 2026-11-15 — Grafisches AMS, bestätigte Rollenübernahme und Abschaltautomatik

- Eigene schematische A1- und anklickbare AMS-Lite-Grafik mit aktuellen Farben, Materialkategorien, aktivem Filamentweg und tastaturbedienbarer Schachtauswahl.
- Primärer Rollenwechsel über die App: Rolle wählen, Änderung prüfen und bestätigen. Bereits passende Angaben benötigen nur die Bestandszuordnung; sonst werden Material/Farbe an den Drucker übertragen und erst nach bestätigtem Zustand gebunden.
- Einzelaktionen schützen aktive und bereits angeforderte Schächte, erlauben eindeutig inaktive Schächte auch während eines Drucks und verändern keine anderen Bindungen. Fallback für Änderungen in Bambu Studio oder direkt am Drucker mit bewusst bestätigbarer Abweichung.
- Geprüfte Vorschau, kurze Gültigkeit, Einmalversuch und Schutz gegen parallele/überholte Änderungen. Keine automatische Wiederholung unklarer AMS-Befehle. Wiederverwendung ausschließlich aktueller bekannter Druckerprofile samt Temperaturbereich.
- Materialfamilien ermöglichen etwa PLA→PLA+/PLA Meta und PETG→PETG-CF bei gleichem Farbcode; detaillierte Bestandsbezeichnungen und Slicer-Profile bleiben erhalten.
- Firmware-Ablehnungen werden ausdrücklich ausgewertet. Erfolg erfordert passende neue und stabile Schachtmeldungen; vorübergehend leere/repopulierte AMS-Daten führen nicht mehr zu verfrühter Bestandsbindung.
- Druckrelevante Rollenwechsel verlangen vor der späteren Buchung eine Auftragsprüfung; historische Zuordnungssnapshots werden nicht unbemerkt ersetzt.
- Gemeinsames Journal zeigt auch manuelle Bestands- und Wiegekorrekturen, ohne sie als Druckverbrauch zu zählen.
- Auftragsbezogene automatische Tapo-Abschaltung nach normalem Ende, vollständig geklärter Buchung, 1–3 Minuten Verzögerung und erneuter Leerlaufprüfung; standardmäßig aus. Neuer Druck, Abbruch oder ungeklärter Ausgang verhindern unbeabsichtigte Wiederholungen. Keine Temperaturprüfung.
- Druckerrestzeit aus Telemetrie ergänzt; alte/unbekannte Angaben werden nicht als aktuelle Restzeit weiterverwendet.
- 155 Tests für den stabilisierten AMS-Stand bestanden. Echter Farbwechsel an Slot 3 im Leerlauf nach Aktivierung von LAN-/Entwicklermodus bestätigt; ursprüngliche Farbe und Bindung wiederhergestellt. Farbwechsel während laufenden Drucks und vollständiger automatischer Ausschaltzyklus bleiben als Hardwareprüfungen offen.

## 2026-11-14 bis 2026-11-15 — Filamentpflege und nachvollziehbare Korrekturen

- Separates Leerspulengewicht und freies Feld „Sonstiges“ am Produkt; Bezeichnung und genauer Filamenttyp ersetzen zusätzliche Pflichtpflege von Farbname, Artikelnummer und Variante. Frühere Daten bleiben gespeichert.
- Wiegekorrektur setzt Restbestand aus Gesamtgewicht minus Leergewicht, einschließlich zuvor unbekannter Bestände. Produkt-Leergewicht dient als Standard, individuelle Rollenwerte können abweichen.
- Löschen unbenutzter Einträge und historienerhaltendes Entfernen archivierter Rollen ergänzt. Aktuelle Zuordnungen und offene Aufträge bleiben geschützt; identische andere Rollen werden nicht entfernt.
- Sortierbare Tabellen, Such-/Statusfilter, Farbvergleich und kompaktere Produktspalten mit erreichbaren Aktionen. Hersteller direkt beim Produktanlegen ergänzbar.
- Journal und Logs mit Datum und Uhrzeit; bei Abbruch ausdrücklicher Hinweis „Teilmenge bis Abbruch gebucht“ statt unklarer Standarddetails.

## 2026-11-12 — Korrektur Lichtsteuerung und Abschaltprüfung

- MQTT-Anfragen verwenden QoS 0 und begrenzte, threadsicher erzeugte Sequenznummern statt Nanosekunden-Zeitstempeln für Lichtbefehle.
- Lichtaktionen werden erst nach einer neuen passenden Zustandsmeldung als erfolgreich angezeigt; fehlende Bestätigung ergibt eine Fehlermeldung.
- Bereits frische explizite Druckerzustände gelten für die Abschaltprüfung; nur veraltete Zustände benötigen eine neue Statusantwort. Offline- und Aktivzustände bleiben gesperrt, finale Prüfung bleibt erhalten.
- 105 lokale Tests erfolgreich. Hardwareprüfung und Deployment dieser Korrektur stehen aus.

## 2026-11-12 — Oberfläche, Geräte und Filamentkatalog

- Gemeinsame responsive Navigation und modernisierte Druckübersicht.
- Spulenkarten mit Suche und Filtern, lesbare Produktauswahl, erweiterte Angaben und gerichtete Bestandskorrekturen.
- Öffentliche SpoolmanDB-Suche als Vorlage für neue Produkte; Herkunft wird gespeichert, Hersteller bei Bedarf angelegt.
- Eigene Kameraseite mit explizitem Öffnen/Schließen und MQTT-Lichtsteuerung.
- Tapo-Leistung, Tages-/Monatsenergie und Laufzeit mit optionalen Live-Abfragen; erneute Leerlaufbestätigung vor Abschalten.
- Ausführliche Bedienungsanleitung; erweiterte Ausschlüsse privater Dateien.
- Validierung: 101 lokale Tests erfolgreich. Hardware-/Drucknachweis und Simulation sind an der jeweiligen Installation gesondert zu prüfen.

## v3.0.0-plus — 2026-11-11

- Native Hersteller-, Produkt-, Spulen- und Lagerortverwaltung mit AMS-Zuordnung, SQLite-Einstellungen und JSON-/Produktexport.
- Validierter, verlustarm archivierter Spoolman-Import mit unabhängigen internen IDs und gesichertem Offline-Umschaltpunkt.
- Bestand, Verbrauch und bisheriges Journal werden intern atomar und idempotent gebucht; Altbuchungen bleiben gesperrt.
- Kamera bei Bedarf, fehlertolerante Druckertelemetrie und isolierter Tapo-Client mit Abschaltsperre bei unbekanntem Druckerstatus.
- 92 Regressionstests einschließlich Nebenläufigkeit, tatsächlichem Prozessabbruch und JSON-Wiederherstellung. Sicherungs-/Rollback-Verfahren dokumentiert.

## v2.4.3 — 2026-11-07

- 3MF-Filamentdaten, Extruder-Mapping und gesonderte AMS-Druckstart-Zuordnung dokumentiert; keine feste Farbcodeliste der Bridge, sondern Vergleich mit den tatsächlich gemeldeten AMS-RGB-Werten.

- Alltagstauglichen Ablauf für AMS-Farben, aktuelle Slicer-Projektfarben und Spoolman-Bestandsdaten dokumentiert, einschließlich Synchronisierung, Filamentprofilen und importierten Projekten.
- Tatsächlich empfangenen AMS-Materialtyp und RGB-Farbcode direkt unter jedem Slot angezeigt, damit der Slicer-Abgleich ohne Farbraten möglich ist.
- Keine dauerhaften Farbfreigaben angelegt und keine Farben oder Materialdaten automatisch verändert.

## v2.4.2 — 2026-11-07

- Nicht zugeordnete Filamente nennen jetzt die konkrete Slicer-Farbe und das Material beziehungsweise eine fehlende Spoolman-Zuweisung oder fehlende AMS-Daten.
- Zuordnungsformulare zeigen Slicer-Farben und Material sowie die zum Druckbeginn gespeicherten AMS-Farben. Bereits eindeutig geplante Slots sind vorausgewählt; der Bearbeitungsstand wird als Anzahl zugeordneter Filamente dargestellt.
- Regressionstest für ein zweifarbiges Modell mit abweichendem Slicer-Grün ergänzt. Die exakte Farblogik bleibt erhalten und unvollständige Aufträge werden nicht teilweise abgebucht.

## v2.4.1 — 2026-11-07

- Kommunikations- und Verarbeitungsflow in getrennte Diagrammabschnitte und eigene Einzelansichten für unabhängiges Zoomen aufgeteilt.

- Erfolgreiche Buchungen werden als „Buchung durch Spoolman bestätigt“ angezeigt. Historische erfolgreiche HTTP-Details werden lesbar dargestellt, ohne gespeicherte Audit-Daten zu verändern.
- Repository-Kurzbeschreibung ergänzt; README um Funktionsübersicht, Kommunikations- und Verarbeitungsdiagramme sowie einen aktuellen GUI-Screenshot mit anonymisierten Beispieldaten erweitert.
- Detailliertes Konzept für MQTT, FTPS/3MF, Slicer-Verbrauchsdaten, Zuordnung, Spulenaufteilung, Reserven, Abbruchschätzung und sichere Wiederaufnahme dokumentiert.

## v2.4.0 — 2026-11-07

- Gleichartige AMS-Spulen werden nach beobachteter Nutzung und kleinstem positivem Restgewicht zugeordnet; rechnerischer Verbrauch kann auf mehrere Spulen verteilt werden.
- Passende Reservespulen aus Spoolman werden bei Bedarf einbezogen. Übersteigt der Verbrauch auch deren Bestand, wird die Differenz der letzten passenden Spule belastet und gekennzeichnet.
- Abgebrochene Drucke werden anhand des letzten bekannten Fortschritts geschätzt und automatisch abgerechnet. Schätzungen und ihre Berechnungsbasis sind im Journal sichtbar.
- Hintergrund-Worker sichert Slicer-Daten und Bestandsgewichte bereits zum Druckstart, ohne während des Druckens zu buchen.
- Buchungspläne bleiben nach dem ersten API-Versuch unverändert; erfolgreiche Teilbuchungen und historische Dry-Runs werden nicht wiederholt.
- Regressionstests für 30/70-Aufteilung, Reservespulen, Bestandsmangel, Abbruch, Fortschrittsreset und Wiederholungen ergänzt.

## v2.3.0 — 2026-11-07

- Automatische Slicer→AMS-Zuordnung bei exakt gleicher RGB-Farbe und Materialtyp ergänzt, wenn kein ausdrücklich bestätigtes oder vom Drucker geliefertes Mapping vorliegt.
- AMS-Bestand zum Druckstart und Filament-Metadaten pro Auftrag gespeichert; partielle MQTT-Meldungen überschreiben bekannte Attribute nicht mehr.
- Mehrdeutige oder unvollständige Aufträge warten vollständig auf Bestätigung, ohne vorab Teilpositionen zu buchen.
- Herkunft der Zuordnung im Journal dokumentiert; externe Spulen bleiben explizit zuzuordnen.
- Sitzungsschlüssel dauerhaft in SQLite gespeichert, damit Formulare Container-Updates überstehen. Ungültige Formulare zeigen eine verständliche Fehlerseite und buchen nichts.
- Aktualisieren lädt auch die auftragsbezogenen Zuordnungsformulare neu.
- Regressionstests für automatische Zuordnung, Mehrdeutigkeit, Inventar-Snapshots und Formularsitzungen ergänzt.

## v2.2.1 — 2026-11-07

- Auftragsbezogene Slicer→AMS-Zuordnung in der Oberfläche ergänzt, falls die Firmware kein Mapping liefert.
- Bereits simulierte, gebuchte oder gerade verarbeitete Positionen können nicht umgeleitet werden.
- Bestätigte Zuordnungen werden nicht auf nachfolgende Drucke übertragen.
- Weitere Regressionstests für die Zuordnungsformulare und parallele Verarbeitung ergänzt.

## v2.2.0 — 2026-11-07

- Aktuellen v2.1-Stand mit SQLite-Journal und Mehrmaterialunterstützung in das Repository übernommen.
- Separate bestehende Spoolman-Instanz über konfigurierbare URL angebunden; Compose startet keinen zweiten Spoolman.
- MQTT-Abhängigkeit auf Callback-API v2 abgestimmt, Abhängigkeiten festgelegt.
- Persistierte JSON-Filament-IDs normalisiert; Druckplatte eindeutig ausgewählt und Download-/XML-Größen begrenzt.
- Fehlgeschlagene Positionen werden nicht mehr pauschal als erledigt markiert. Sichere Wiederaufnahme, atomare Buchungsfreigabe und Schutz unklarer API-Anfragen vor automatischer Wiederholung.
- Historische Dry-Runs werden beim Moduswechsel nicht nachgebucht.
- MQTT-Deltas erhalten Slot und Fortschritt; AMS-Metadaten werden auch ohne Zustandsmeldung gespeichert.
- Spulenzuordnungen pro Auftrag gespeichert; unbekannte Filamentzuordnung blockiert die automatische Abbuchung.
- Web-Anmeldung und Formularprüfung aktiviert, ungültige manuelle Grammwerte abgefangen.
- Container-Healthcheck, wiederholbares SSH-Deployment mit Datensicherung, Pfadmigration und Rückfall auf den vorherigen Stack ergänzt.
- Hardcodierte Drucker-Zugangsdaten aus dem aktuellen Code entfernt. Historische Git-Commits wurden nicht umgeschrieben.
- Regressionstests für Buchungen, Mehrmaterial, Neustart, Dry-Run, API-Ausfälle und Webformulare ergänzt.
