# Deployment und Prüfung

Der Quellstand enthält native Inventarverwaltung, Kamera und Lichtsteuerung sowie Tapo-Anbindung. Individuelle Betriebsprotokolle, Bestandszahlen, Sicherungsnamen und Imagekennungen werden nicht veröffentlicht.

Für neue Installationen mit der Beispieldatei starten und ausschließlich lokale Zugangsdaten verwenden. Der Standard bleibt Spoolman mit aktivierter Simulation. Interne Inventarverwaltung erfordert eine kontrollierte Offline-Migration; keine bestehende Datenbank durch eine leere ersetzen.

- [Bedienungsanleitung](docs/user-guide.md)
- [Migration, Sicherungen und Wiederherstellung](docs/plus-operations.md)
- [Einrichtung und Deployment-Werkzeug](README.md#einrichtung)

Vor jedem Update laufende Drucke und offene Buchungen ausschließen, konsistente private Sicherungen erstellen und nach dem Neustart Healthcheck, Journal und Bestände prüfen. Ein Hardwaretest der eigenen Installation bleibt erforderlich. Automatisierte Tests verwenden temporäre Daten und simulierte Geräte.
