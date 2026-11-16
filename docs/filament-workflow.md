# Filament- und Auftragszuordnung

## Drei unterschiedliche Objekte

- **Produkt:** Hersteller, Bezeichnung, genauer Filamenttyp, RGB-Farbe und Standard-Leergewicht.
- **Physische Rolle:** Bestimmter Artikel dieses Produkts mit unabhängigem Restbestand und Lagerort.
- **Slicer-Filamentposition:** Ein im Druckprojekt verwendetes Profil beziehungsweise eine Filamentposition. Diese Position ist weder eine physische Rolle noch automatisch ein bestimmter AMS-Slot.

## Neuer Bestand

Produkt anlegen oder Katalogvorlage übernehmen, Hersteller bei Bedarf im selben Dialog ergänzen, genaue Variante im Typ pflegen und Leergewicht getrennt speichern. Danach die konkrete Rolle anlegen. Unbekannten Restbestand leer lassen und später über Wiegekorrektur erfassen. [Screenshots und Schritte](user-guide.md#filamente-und-physische-spulen).

## Normaler AMS-Wechsel

**Voraussetzung für das Setzen von Farbe und Material: LAN-only und Entwicklermodus.** Rolle physisch wechseln, Schacht in der Grafik auswählen, Bestandsrolle wählen, Die gewünschte Drucksteuerung rechts wählen, „← An Drucker senden …“ öffnen, Vorschau prüfen und bestätigen. Die App setzt bei Bedarf Druckerangaben und bindet die Rolle nach stabiler neuer Bestätigung. Für eine reine Bestandsbindung „Automapping →“ oder „Auswahl nur im Bestand zuordnen …“ nutzen. Der Filter für passende Rollen bleibt dauerhaft sichtbar.

Aktiver und angeforderter Schacht bleiben während eines Drucks gesperrt; andere eindeutige Schächte dürfen geändert werden. Für den aktuellen Auftrag relevante Änderungen lösen eine Zuordnungsprüfung vor dessen späterer Buchung aus. Keine automatische Mengenmessung bei einem physischen Rollenwechsel.

## Fallback nach Änderungen außerhalb der App

Bei Änderungen in Bambu Studio oder direkt am Drucker aktuelle Rolle in der App neu zuordnen. Der dauerhaft sichtbare Filter kann auf passende Material-/Farbkombinationen einschränken; „Automapping →“ bereitet eine reine Bestandsbindung vor. Eine bewusste Abweichung für eine reine Bestandsbindung muss ausdrücklich bestätigt werden. Unterschiedliche Farben fallen im visuellen Vergleich auf. Identische Rollen bleiben allein anhand der Farbe ununterscheidbar.

## Slicer-Projekt vor dem Start

1. Aktuelle physische Belegung im AMS prüfen.
2. Im Slicer Drucker-/AMS-Daten synchronisieren und die gewünschten Projektfarben/-materialien prüfen. Eine alte Projektfarbe oder Profilstandardfarbe kann von der real gewählten Rolle abweichen.
3. Beim Senden die tatsächlichen AMS-Schächte auswählen.
4. Falls das Drucker-Mapping fehlt oder nicht eindeutig ist, den Auftrag in der App prüfen und seine Slicer-Filamentpositionen ausdrücklich zuordnen.

### Kann die 3MF den verwendeten AMS-Slot verraten?

Nicht verbindlich. Die 3MF beschreibt Filamentpositionen und Mengen; die endgültige physische Zuordnung kann erst beim Senden gewählt werden. T0/T1 ist keine garantierte Slotnummer. Die App bevorzugt die ausdrückliche Auftragszuordnung oder das vom Drucker gemeldete Mapping. Erst danach hilft der Materialfamilien-/RGB-Abgleich.

### Welche Farbcodes darf ich im Slicer verwenden?

Es gibt keine feste Farbliste der App. Für den automatischen Vergleich muss der konkrete RGB-Code zur gespeicherten AMS-Farbe passen; es gibt keine Ähnlichkeitstoleranz. Bei bewusst abweichender Slicerfarbe die Zuordnung für diesen Auftrag bestätigen. Materialfamilien verbinden etwa PLA mit PLA+/PLA Meta und PETG mit PETG-CF; sie ändern keine Druckparameter.

## Nach Druckende

Die App liest die Mengen der tatsächlich ausgewählten Platte aus `Metadata/slice_info.config`. Ohne eindeutige Platten-/Metadaten oder Zuordnung bleibt der Auftrag offen. Bei normalem Ende werden die Slicer-Mengen, bei Abbruch fortschrittsanteilige Schätzungen verwendet. Optionaler Flusszuschlag kommt einmalig pro positiver Slicer-Filamentposition hinzu. [Details und Beispiele](user-guide.md#einstellungen-und-flusskontrolle).

Die geplante Aufteilung auf Rollen wird eingefroren. Eine spätere Änderung der allgemeinen AMS-Belegung schreibt bereits versuchte Buchungen nicht um. Bei mehreren passenden Rollen oder Reserven bleiben rein rechnerische Teilmengen als Schätzung markiert. Reservespulen im Lager werden nicht automatisch physisch geladen.

## Typische Fälle

| Fall | Vorgehen |
|---|---|
| Rolle ohne bekannten Restbestand | Wiegekorrektur mit Gesamt- und Leergewicht verwenden. |
| Neue Rolle desselben Produkts | Eigene physische Spule anlegen und genau diese zuordnen. |
| AMS meldet Wechsel, Auswahl ist leer | Tatsächlich eingelegte Rolle prüfen und übernehmen. |
| Druckstart noch ohne aktiven Slot | Auftrag offenlassen; spätere eindeutige Meldung kann ergänzen. Bei WAITING_MAPPING ausdrücklich prüfen. |
| Farbe absichtlich anders als im Projekt | Auftragszuordnung bestätigen; Bestandsfarbe nicht allein für den Abgleich verfälschen. |
| Rolle während relevantem Druck gewechselt | Auftragsprüfung durchführen; Teilmengen bleiben geschätzt. |
| Archivierte Rolle soll verschwinden | Zuordnungen lösen, offene Aufträge klären, „Aus Inventar entfernen“. |

[Zur Bedienungsanleitung](user-guide.md) · [Architektur](architecture.md).
