# Zuverlässiger Farb- und Spulenabgleich im Alltag

Für die aktuelle Bridge ist der einfachste zuverlässige Weg: **AMS-Material und AMS-Farbe als gemeinsame Referenz für das Slicer-Projekt verwenden; die reale Spule separat über ihre Spoolman-ID zuweisen.** Eine optisch ähnliche Farbe reicht für den automatischen Vergleich nicht aus. Eine dauerhafte Farbfreigabe ist für diesen Ablauf nicht erforderlich.

## Was gehört in welches System?

| System | Dort pflegen | Bedeutung für die Bridge |
|---|---|---|
| Drucker / AMS Lite | Tatsächlich eingelegtes Material und dessen ausgewählte Farbe | Referenz für den Vergleich mit den Slicer-Daten. Der gespeicherte RGB-Wert zählt, nicht nur der Farbname. |
| Bambu Studio: aktuelles Projekt | Dasselbe Material und denselben RGB-Wert wie beim tatsächlich gewählten AMS-Filament | Daraus werden die Filament-Metadaten in der geslicten 3MF-Datei erzeugt. |
| Spoolman | Hersteller, reales Filament, reale Farbe, einzelne Spulen und Restgewichte | Die primäre Buchung verwendet die zugewiesene Spulen-ID. Die Farbe muss dafür nicht mit dem AMS identisch sein. Bei Reservespulen werden Spoolman-Farbe und -Material untereinander verglichen. |
| Bridge-Weboberfläche | AMS-Slot → richtige Spoolman-Spule | Verbindet den physischen Slot mit dem tatsächlichen Bestand. Beim Spulenwechsel vor dem nächsten Druck aktualisieren. |

Es gibt damit zwei getrennte Zwecke für die Farbe: **Slicer/AMS-Abgleich** und **Spoolman-Bestandsbeschreibung**. Eine begrenzte Farbpalette im Drucker ist kein Hindernis: Der Slicer kann den vom Drucker verwendeten Wert übernehmen. Man muss keine frei gewählte Slicer-Farbe im AMS nachbilden.

## Einmalige Einrichtung einer Spule

1. **In Spoolman anlegen:** Für das reale Produkt einen Filament-Eintrag mit Material und sinnvoller realer bzw. Herstellerfarbe verwenden. Jede physische Rolle erhält einen eigenen Spulen-Eintrag und ein eigenes Restgewicht. Reservespulen desselben Produkts möglichst demselben Filament-Eintrag zuordnen, damit ihre Bestandsdaten zusammenpassen.
2. **In den AMS-Slot einlegen:** Material und Farbe am Drucker oder auf der Geräte-Seite in Bambu Studio prüfen. Bei Filamenten ohne passende automatisch gelesene Informationen diese Angaben manuell pflegen.
3. **In der Bridge zuweisen:** Für diesen AMS-Slot die entsprechende Spoolman-Spule auswählen und speichern. Die Bridge zeigt unter dem Slot auch den empfangenen Materialtyp und RGB-Farbcode. Bei fehlenden Daten steht dort „—“; erst mit vorhandenen Druckerdaten abgleichen. Nach Änderungen am Drucker in der Bridge **Aktualisieren** drücken, damit die Slot-Angaben neu angezeigt werden.

Die Bridge verändert weder AMS-Farben noch Spoolman-Filamentdaten automatisch.

## Vor jedem neuen oder importierten Projekt

1. In Bambu Studio unter **Gerät / Device** den richtigen Drucker auswählen und die eingelegten Filamente prüfen.
2. Unter **Vorbereiten / Prepare** die Funktion zum Synchronisieren der Filamentliste mit dem AMS verwenden. Bezeichnung und Dialog unterscheiden sich je nach Studio-Version: etwa **Sync/Resync**, **Synchronisieren**, **Mapping** oder **Überschreiben / Direct Override**. Ein bloß ähnlicher Farbeindruck ist keine Kontrolle des RGB-Wertes.
3. Bei einem neuen, noch nicht eingefärbten Projekt kann die Übernahme der AMS-Liste sinnvoll sein. Bei einem bereits mehrfarbig aufgebauten Projekt gezielt zuordnen und die Modellvorschau kontrollieren, statt die Liste blind zu überschreiben. Eigene Materialprofile danach ebenfalls prüfen.
4. Sicherstellen, dass die Projektfilamente nun den tatsächlich verwendeten AMS-Farben und Materialtypen entsprechen. Bei Unsicherheit den in der Bridge sichtbaren Farbcode im **aktuellen Projekt** am Farbfeld des betreffenden Filaments übernehmen. Der genaue Farbdialog ist versionsabhängig.
5. Die Modellteile bzw. bemalten Bereiche den gewünschten Projektfilamenten zuordnen. Für einen Farbtausch die Filamentzuordnung der Bereiche ändern; keine beliebigen Ersatz-Grüntöne als reine Vorschaufarbe verwenden.
6. **Nach der Änderung neu slicen.** Bereits erzeugte G-Code-/3MF-Dateien enthalten noch die zuvor gespeicherten Metadaten.
7. Beim Senden des Drucks die Zuordnung zu den physischen AMS-Slots kontrollieren. Für den farbbasierten Bridge-Abgleich keine absichtlich andere AMS-Farbe auswählen, als im geslicten Projekt hinterlegt ist.

Die Zuordnung im Druckstart-Dialog allein ist für diese Bridge kein Ersatz für passende Datei-Metadaten: Der A1 hat in den untersuchten Statusmeldungen das vollständige Slicer→AMS-Mapping nicht geliefert. Deshalb muss der automatische Ersatzweg über Farbe und Material nachvollziehbar bleiben.

## Reicht eine Farbe im Filamentprofil?

**Als Standardwert ja, als alleinige Absicherung nein.** Ein eigenes Profil kann eine passende Standardfarbe haben. Ein geöffnetes oder importiertes 3MF-Projekt kann trotzdem eine andere Projektfarbe mitbringen. Entscheidend ist die aktive Projektfarbe vor dem erneuten Slicen. Profilnamen wie „Hersteller PLA+ Grün“ sind nicht der Vergleichsschlüssel der Bridge; relevant ist der gespeicherte Materialtyp, z. B. `PLA`.

Bambu Studio unterscheidet im Quellcode die Standardfarbe des Profils (`default_filament_colour`) von der Projektfarbe (`filament_colour`). Die AMS-Synchronisierung kann Projektfarben übernehmen bzw. ersetzen; die verwendete Synchronisierungsart und das Ergebnis sollten deshalb kontrolliert werden. Ein einmal gespeichertes Projekt mit korrekt zugewiesenen Filamenten kann als persönliche Vorlage dienen.

## Kann die 3MF den verwendeten AMS-Slot verraten?

**Aus den bisher untersuchten Druckdateien dieses A1 nicht zuverlässig.** Eine Bambu-3MF ist ein ZIP-Archiv mit Modellen, Projekt-/Slicer-Einstellungen und gegebenenfalls G-Code. Filamentnummern und Werkzeugwechsel darin sind nicht automatisch die beim Druckstart tatsächlich gewählten physischen AMS-Slots.

Bei der erneuten Prüfung einer zweifarbigen Druckdatei wurden diese Angaben gefunden:

| Angabe in der Datei | Was sie bedeutet | Physischer AMS-Slot eindeutig? |
|---|---|---|
| `filament.id="1"` bzw. `"2"` | Slicer-Filamentnummer | Nein |
| `color`, `type`, `used_g` | Slicer-Farbe, Material und Grammzahl dieses Filaments | Nein; diese Daten ermöglichen den Farb-/Materialvergleich |
| `tray_info_idx`, z. B. `GFL99` | Filament-/Materialprofilkennung | Nein; mehrere Slots können dieselbe Kennung haben |
| `T0`, `T1`, `M620 S0A`, `M620 S1A` | Logische Filament-/Werkzeugwechsel im betreffenden Bambu-Druckablauf | Nicht ohne die dazugehörige Druckstart-Zuordnung |
| `filament_map=[1,1]` bzw. `filament_maps="1 1"` | Beide Filamente sind demselben Extruder zugeordnet | Nein; der A1 hat einen Extruder, aber mehrere AMS-Slots |
| `ams_mapping` / `ams_mapping2` | Gesonderte Slicer→AMS-Zuordnung für den Druckstart | Wäre relevant; in den untersuchten Datei-Metadaten nicht enthalten |

Bambu Studio setzt die AMS-Zuordnung zusätzlich in den Parametern des Druckstart-Auftrags. Diese Auswahl kann anders sein, obwohl dieselbe Druckdatei verwendet wird. Ein gespeicherter Projektzustand wäre außerdem nicht automatisch ein Beleg für eine später abweichend gewählte Zuordnung oder einen tatsächlichen Auto-Refill-Wechsel.

Der A1 hat das vollständige Mapping in den bisher untersuchten MQTT-Statusmeldungen nicht geliefert. Deshalb verwendet die Bridge bei fehlendem ausdrücklichem Mapping den dokumentierten Farb-/Materialvergleich. Ein verlässlich empfangenes Auftragsmapping oder eine ausdrückliche Benutzerzuordnung hat Vorrang.

Das ist keine allgemeine Behauptung, dass ein 3MF-Archiv niemals zusätzliche Mapping-Metadaten enthalten könnte. Entscheidend ist, ob ein Feld nachweislich die tatsächlich verwendeten Slots **dieses Druckauftrags** beschreibt. Die bloße Existenz eines Feldes mit „map“ im Namen genügt nicht.

### Kann die Druckstart-Zuordnung separat ausgelesen werden?

Die vorhandene Verbindung liefert unterschiedliche Daten mit unterschiedlicher Aussagekraft:

| Information | Aktuelle Verfügbarkeit |
|---|---|
| Welches Material und welche Farbe in jedem AMS-Slot konfiguriert sind | Wird über MQTT gelesen |
| Welcher Slot aktuell aktiv ist und welche Slots während des Drucks beobachtet wurden | Wird über MQTT gelesen und für den Auftrag gespeichert |
| Vollständige Zuordnung jedes Slicer-Filaments zum beim Druckstart gewählten AMS-Slot | In den bisher untersuchten Statusmeldungen dieses A1 nicht geliefert |

Bambu Studio besitzt das vollständige Startmapping und setzt dafür unter anderem die gesonderten Parameter `ams_mapping` und `ams_mapping2`. Für die aktuelle Bridge ist das jedoch nicht automatisch ein zugänglicher Rückkanal vom Drucker.

Ein gezielter lokaler MQTT-Test am 07.11.2026 bestätigte das Abonnement des Status-/Report-Themas. Beim anschließenden Versuch, das Request-Thema mitzulesen, wurde die separate Testverbindung beendet, ohne ein erfolgreiches Request-Abonnement zu bestätigen. Es wurden dabei keine Druckbefehle gesendet. Daraus lässt sich kein funktionierender Mitleseweg für die Startzuordnung ableiten; die genaue Ursache des Verbindungsabbruchs wurde vom Broker nicht benannt.

Ein zusätzlicher Datenweg könnte die Zuordnung unmittelbar beim Senden eines Drucks an die Bridge übergeben und dort dem passenden Auftrag zuordnen. Damit wäre für diese Aufträge kein Farbvergleich zur Slot-Erkennung nötig. Ob eine solche Integration ohne Änderungen am verwendeten Slicer möglich ist und auch Drucke aus Bambu Handy oder von der SD-Karte erfasst, muss für den jeweiligen Startweg gesondert geprüft werden. Ein nachträglicher 3MF-Download ersetzt diesen Datenweg nicht.

## Welche Farbcodes darf ich im Slicer verwenden?

**Die Bridge hat keine feste AMS-Farbtabelle und keine auf vier Werte beschränkte Freigabeliste.** Für ihren automatischen Farbvergleich zählt der tatsächlich vom AMS gemeldete RGB-Wert. Ein anderer Wert kann ein gültiger Slicer-Farbcode sein und trotzdem nicht automatisch zugeordnet werden.

Beispiele für den Vergleich bei Materialtyp `PLA` und ohne separates Auftragsmapping:

| AMS meldet | Aktuelles Slicer-Projekt enthält | Automatische Farbzuordnung |
|---|---|---|
| `#104831` | `#104831` | Passender Farb-/Materialtreffer |
| `#104831` | `#80FF80` | Kein exakter Farbtreffer |
| `#FFFFFF` | `#FFFFFF` | Passender Farb-/Materialtreffer |
| Ein anderer gemeldeter RGB-Wert | Genau derselbe RGB-Wert | Ebenso möglich; keine feste Palette der Bridge |

Mehrere gleichartige Slots werden anschließend nach den beschriebenen Nutzungs-/Bestandsregeln aufgeteilt. Das ändert nicht die Voraussetzung des vorherigen Farb-/Materialvergleichs.

Die Bridge normalisiert Groß-/Kleinschreibung und das führende `#`. Bei einem vom Drucker gemeldeten RGBA-Wert wie `104831FF` wird der RGB-Anteil `#104831` verglichen. Es gibt keine automatische Toleranz für nur ähnliche Farbtöne.

Für ein eigenes Slicer-Filamentprofil ist die zum tatsächlich verwendeten AMS-Filament passende **Standardfarbe** sinnvoll. Bei importierten Projekten anschließend die **aktive Projektfarbe** kontrollieren und gegebenenfalls angleichen. Materialtyp und Farbcode müssen zum AMS passen; Profilname, Herstellername und Reihenfolge der Projektfilamente müssen nicht der AMS-Slotnummer entsprechen. Nach Änderungen neu slicen.

Die Bridge zeigt den aktuell empfangenen Materialtyp und Farbcode direkt unter jedem AMS-Slot. Diese Anzeige ist die praktische Referenz für den Slicer. Ist die AMS-Auswahl in einer Geräteansicht begrenzt, eine sinnvolle Farbe dort wählen und den gemeldeten Wert in den Slicer übernehmen. Ein frei gewählter Slicer-Farbton muss nicht umgekehrt im AMS erzwungen werden.

## Konkretes Beispiel

Angenommen, der AMS meldet **PLA `#104831`**, während das Projekt **PLA `#80FF80`** enthält. Für den exakten Vergleich sind das unterschiedliche Farben.

Der passende Ablauf ist:

- Den AMS-Wert als Referenz beibehalten.
- Die Farbe dieses Projektfilaments durch Synchronisierung oder manuelle Eingabe auf `#104831` setzen.
- Materialtyp `PLA` auf beiden Seiten prüfen, anschließend neu slicen.
- In der Bridge den betreffenden Slot mit der tatsächlich eingelegten Spoolman-Spule verknüpfen.

Die reale Herstellerfarbe des Filaments in Spoolman darf dabei beispielsweise `#B2BA81` bleiben. Sie wird für die erste Slicer→AMS-Zuordnung nicht herangezogen. Für Reservebuchungen müssen hingegen die beteiligten **Spoolman-Filamente untereinander** exakt passende Farben und Materialtypen haben. Unterschiedliche reale Produkte sollten nicht nur wegen einer groben gemeinsamen AMS-Palettenfarbe in Spoolman zusammengefasst werden.

## Kurze Kontrolle vor dem Druck

- [ ] Richtige Spule je AMS-Slot in der Bridge gespeichert.
- [ ] AMS und aktuelle Slicer-Projektfilamente haben gleiche Materialtypen und RGB-Werte.
- [ ] Nach Farbänderungen neu geslict und Modellvorschau geprüft.
- [ ] Druckstart-Zuordnung verwendet die vorgesehenen physischen Slots.
- [ ] Spoolman-Restgewichte und Reservespulen sind gepflegt.

Falls das Journal `UNMAPPED` zeigt, nennt es die konkret fehlende Übereinstimmung. Dann diese Angaben vergleichen, statt allein nach dem Farbnamen zu entscheiden. Bereits erfolgreich gebuchte Aufträge nicht noch einmal manuell belasten.

## Nachweise und Versionshinweise

Geprüft am 07.11.2026 anhand des offiziellen Bambu-Studio-Projekts. Die Dialogbezeichnungen können von der installierten Version abweichen. Maßgeblich bleibt das Ergebnis im aktuellen Projekt und in den Druckdatei-Metadaten.

- [Bambu-Studio-Druckstart: gesonderte AMS-Mappingparameter](https://github.com/bambulab/BambuStudio/blob/master/src/slic3r/GUI/Jobs/PrintJob.cpp)
- [Bambu-3MF-Metadaten und Import von Filament-/Extruderzuordnungen](https://github.com/bambulab/BambuStudio/blob/master/src/libslic3r/Format/bbs_3mf.cpp)
- [Bambu-Studio-Konfiguration: Standardfarbe und aktive Filamentfarbe](https://github.com/bambulab/BambuStudio/blob/master/src/libslic3r/PrintConfig.cpp)
- [Bambu-Studio-Projektkonfiguration und Übernahme von AMS-Farben](https://github.com/bambulab/BambuStudio/blob/master/src/libslic3r/PresetBundle.cpp)
- [Bambu-Studio-Synchronisierungsdialog: Mapping und Überschreiben](https://github.com/bambulab/BambuStudio/blob/master/src/slic3r/GUI/SyncAmsDialog.cpp)
- [Erläuterung eines Bambu-Mitarbeiters zur getrennten Projekt- und AMS-Liste](https://github.com/bambulab/BambuStudio/issues/8100)

[Zurück zur README](../README.md) · [Verarbeitungskonzept](architecture.md)
