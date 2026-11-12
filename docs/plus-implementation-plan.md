# Bambu-Spoolman-Plus: Implementierungsübersicht

## Datenmodell

Hersteller und Filamentprodukte bilden Stammdaten; jede physische Spule besitzt unabhängigen Bestand und Lagerort. Externe Quellkennungen bleiben von internen Primärschlüsseln getrennt. Unbekannte Gewichte bleiben unbekannt. Importierte Restbestände sind Eröffnungsbestände und keine historischen Verbrauchsbuchungen.

## Buchung und Migration

Bestand, Verbrauch und Journal werden in einer lokalen SQLite-Transaktion aktualisiert. Stabile Buchungskennungen verhindern Doppelbuchungen. Bestehende MQTT-/FTPS-/3MF-Verarbeitung bleibt erhalten. Die interne Betriebsart erfordert einen offline gesetzten Umschaltmarker; alte Jobs werden nicht erneut gebucht. Es gibt genau ein schreibendes Bestandssystem.

## Oberfläche und Geräte

Eine gemeinsame Navigation verbindet Druckübersicht, Spulenverwaltung, Kamera/Licht, Tapo und Einstellungen. Der öffentliche SpoolmanDB-Katalog liefert optionale Produktvorlagen. Kamera arbeitet bei Bedarf; die native Tapo-Bibliothek läuft in einem isolierten Hilfsprozess. Abschalten erfordert erneut bestätigten Druckerleerlauf.

## Validierung und Grenzen

Tests prüfen Migration, Fremdschlüssel, Transaktionen, Nebenläufigkeit, Prozessabbruch, JSON-Roundtrip, Webschutz, Telemetrie und Gerätefehler. Reale Hardwaretests und ein vollständiger Druck müssen an der jeweiligen Installation erfolgen. Layergenaue Mengenaufteilung bei physischen Rollenwechseln ist nicht nachgewiesen; Schätzungen bleiben gekennzeichnet.

Siehe [Bedienungsanleitung](user-guide.md) und [Betrieb und Wiederherstellung](plus-operations.md). Individuelle Infrastruktur, SSH-Zugänge und Betriebsprotokolle gehören nicht in diese Dokumentation.
