# Ladezeit der Druckerspeichervorschau

Die Ladezeit besteht aus unterschiedlichen Schritten. Eine schnelle Cacheantwort sagt deshalb nichts über die Geschwindigkeit des ersten Druckerdownloads aus.

## Was beim ersten Aufruf passiert

1. Die App liest die Druckdatei über die verschlüsselte FTPS-Verbindung vom Drucker. Drucker, Speicherkarte und Netzwerk bestimmen die tatsächlich verfügbare Rate.
2. Die lokale Dateiprüfung liest Platten, Materialien und vorhandene Bilder. Vorläufige Informationen können früher angezeigt werden; sie ersetzen keine vollständige Prüfung vor einem Druckstart.
3. Für die ausdrücklich gewählte 3D-Vorschau bereitet das NAS die Druckbahnen auf. Die Dateigröße allein beschreibt den Aufwand schlecht: Kreisbögen, sehr viele kleine Bewegungen und viele Schichten können eine große Geometrie erzeugen.
4. Der Browser übernimmt und zeichnet die Geometrie. Große Antworten werden bei ausgehandelter Unterstützung komprimiert übertragen.

Ein reales Archiv mit 18,08 MiB enthielt rund 3,39 Millionen G-Code-Zeilen. Nach konturerhaltender Vereinfachung blieben rund 1,17 Millionen dargestellte Segmente. Die einmalige Aufbereitung benötigte am Referenz-NAS etwa 117,6 Sekunden; die bereits aufbereitete Geometrie kam anschließend über die produktive Schnittstelle in rund 2,44 Sekunden aus dem Cache. Das sind verschiedene Vorgänge. Die Zeit für den ersten Netzwerktransfer kommt zusätzlich hinzu.

## Bereits umgesetzte Beschleunigungen

- Dateidetails und 3D-Vorschau verwenden denselben vollständig geladenen und geprüften Archivtransfer. Der direkt anschließende zweite Druckerdownload entfällt.
- Die private temporäre Archivablage hält höchstens zwei Archive mit zusammen 128 MiB. Jede Kopie wird spätestens nach fünf Minuten automatisch gelöscht; auch eine andere Modellplatte kann sie währenddessen verwenden.
- Fertige 3D-Geometrie bleibt bis zu 30 Tage im begrenzten Cache: höchstens 50 Modelle / zusammen 128 MiB komprimiert. Kompatible ältere Ergebnisse bleiben gültig.
- Schnelle Kompression, ausgehandelte gzip-Antworten und speichersparendes Base64-Decoding reduzieren Zusatzaufwand und Browserübertragung.
- Sehr große Szenen verwenden einfachere Bahnquerschnitte. Die App lässt keine Bahnen pauschal aus; zusammenhängende Konturen werden mit begrenzter zusätzlicher Abweichung vereinfacht.

Die vorhandene Funktion **App-Vorschaudaten leeren** entfernt diese löschbaren Kopien und Ergebnisse. Danach können der nächste echte Transfer und die erste Aufbereitung wieder erforderlich sein. Bestände, Buchungen und Originaldateien auf dem Drucker werden dabei nicht gelöscht.

## Vergleichsmessungen am Referenzsystem

Getestet wurden nur Lesezugriffe im Leerlauf. WLAN- und Druckerbetriebsmodus wurden durch die Tests nicht umgestellt. Vergleichsdaten wurden per SHA-256 geprüft; Zugangsdaten und Modellarchive werden nicht veröffentlicht.

| Vergleich | Ergebnis |
| --- | --- |
| Python-FTPS, identischer 2-MiB-Dateianfang | etwa 0,193 MiB/s |
| Unmittelbare TCP-Bestätigungen (`TCP_QUICKACK`) | etwa 0,192 MiB/s |
| Anderer Empfangspuffer | etwa 0,191 MiB/s |
| Beides kombiniert | etwa 0,193 MiB/s |
| Wiederholung der Basismessung | etwa 0,194 MiB/s |
| AES-256-GCM, ganze 2,65-MiB-Vergleichsdatei | etwa 13,72 Sekunden reine Übertragung |
| AES-128-GCM, dieselbe Datei | etwa 13,68 Sekunden reine Übertragung |
| curl statt Python, dieselbe Datei | etwa 15,22 Sekunden inklusive Verbindungsaufbau |

Der vollständige erneute App-Download des 18,08-MiB-Archivs benötigte 94,81 Sekunden einschließlich Verbindung und Prüfung (0,191 MiB/s). Seine SHA-256-Prüfsumme stimmte mit der vorherigen Dateikopie überein. Er lief mit der getrennten Leseprüfung vollständig durch, obwohl die Statusmeldung zwischenzeitlich bis zu 72,7 Sekunden alt war; anschließend kam wieder eine neue IDLE-Meldung. Die nativen Inventar- und Zuordnungsdaten blieben unverändert.

Die getesteten Transportänderungen brachten keinen relevanten Geschwindigkeitsgewinn und wurden deshalb nicht in die Anwendung übernommen. ChaCha20 wurde vom getesteten Drucker nicht ausgehandelt. Die verschlüsselte Standardverbindung bleibt erhalten. Es wurden keine globalen TCP-Einstellungen verändert.

Die gemessene TCP-Laufzeit lag bei den meisten Stichproben bei ungefähr vier bis fünf Millisekunden. Ein näherer WLAN-Zugangspunkt führte bei den gelesenen Stichproben weiterhin zu ungefähr derselben Nutzdatenrate. Das spricht gegen die Funkentfernung als alleinige Erklärung; welcher Anteil auf Druckerfirmware, FTPS-Implementierung oder Speicherkarte entfällt, ist damit nicht bestimmt. Die Ergebnisse sind kein allgemeiner Leistungswert für andere Geräte oder Installationen.

## Statusprüfung und Abbruch

Ein aktiver Druck oder eine unterbrochene Verbindung bleiben gesperrt. Ein neuer Transfer braucht weiterhin die strenge frische Leerlaufbestätigung. Bereits laufende reine Lesevorgänge dürfen bei bestehender MQTT-Verbindung und ohne aktiven Auftrag ein explizites IDLE/FINISH bis höchstens 90 Sekunden Alter verwenden; die Prüfung erfolgt alle 15 Sekunden. Fehlt danach eine Bestätigung, wird einmal erneut abgefragt. Ohne Bestätigung stoppt die Übertragung mit einer entsprechenden Meldung. Das bedeutet nicht automatisch, dass ein Druck läuft. Druckstart, Löschen, Umbenennen und Steckdosenschaltung behalten ihre strengeren Prüfungen. In einem realen Test war der Drucker weiterhin IDLE, seine Statusmeldung aber 48,6 beziehungsweise 54,6 Sekunden alt; die zuvor wiederverwendete Abschaltfrist hatte deshalb den Download abgebrochen. Das begrenzte Fortsetzungsfenster behebt diese Vermischung der Regeln.

Schließen oder Abbrechen verwirft eine noch unvollständige Übertragung. Eine vollständig geprüfte lokale Kopie oder fertige Geometrie kann dagegen weiterverwendet werden. Die [Bedienungsanleitung](user-guide.md#3d-druckwegvorschau) beschreibt Bedienung, Cachegrenzen und Hintergrundwahl.

Technische Grundlagen der getesteten Optionen: [Linux-TCP-Dokumentation](https://man7.org/linux/man-pages/man7/tcp.7.html) und [curl-Dokumentation](https://curl.se/docs/manpage.html). Die Messwerte oben stammen aus eigenen Tests und nicht aus diesen Quellen.
