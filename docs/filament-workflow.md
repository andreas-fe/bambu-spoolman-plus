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

- [Bambu-Studio-Konfiguration: Standardfarbe und aktive Filamentfarbe](https://github.com/bambulab/BambuStudio/blob/master/src/libslic3r/PrintConfig.cpp)
- [Bambu-Studio-Projektkonfiguration und Übernahme von AMS-Farben](https://github.com/bambulab/BambuStudio/blob/master/src/libslic3r/PresetBundle.cpp)
- [Bambu-Studio-Synchronisierungsdialog: Mapping und Überschreiben](https://github.com/bambulab/BambuStudio/blob/master/src/slic3r/GUI/SyncAmsDialog.cpp)
- [Erläuterung eines Bambu-Mitarbeiters zur getrennten Projekt- und AMS-Liste](https://github.com/bambulab/BambuStudio/issues/8100)

[Zurück zur README](../README.md) · [Verarbeitungskonzept](architecture.md)
