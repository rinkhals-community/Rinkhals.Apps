ANYCUBIC ORIGINAL CAMERA - WEBSOCKET BRIDGE 1.0.0 RC2
Zielplattform: Kobra S1/S1 Combo, Modell 20025, Firmware 2.7.2.7,
Rinkhals (bisheriger Hardwaretest: 20261101_01, App-Versionen bis 0.2.9).

WICHTIG
Nur zwischen Druckaufträgen installieren. Nicht während Druck, Pause, Aufheizen
oder Kalibrierung. Die App verlangt eine bestätigte Inaktivität durch Moonraker.
Keine Internetfreigabe der Webcam, MQTT oder SSH. Verwendung nur im eigenen,
vertrauenswürdigen LAN; die Videoendpunkte sind nicht passwortgeschützt.
Die bestehende Anycubic-Kontoverknüpfung muss vorhanden sein.
Beta: 0.2.4 wurde am Kobra S1 mit WebSocket getestet. Der Nutzer meldet
0.2.7: Installationstöne, Druckschutz und Rückbau erfolgreich.
0.2.8: Rückbautöne ergänzt; bei Neuinstallation fehlte der sofortige Dienststart.
0.2.9: Kamera nach Einrichtung explizit starten und lokalen Diagnose-Endpunkt prüfen.

USB-INSTALLATION / UPDATE
1. ZIP entpacken. Ordner install/aGVscF9zb3Nf auf FAT32-USB-Stick kopieren.
   Nur den Ordner mit update.swu, nicht gleichzeitig das rollback-Paket.
2. Drucker im Leerlauf, Rinkhals vollständig gestartet; Stick einstecken.
3. Nach Ende der Installation Stick entfernen und Ergebnisdatei/Log prüfen.
4. Der Installer startet die aktivierte Kamera-App direkt. Falls die Bridge
   einen Neustart benötigt, USB entfernen und im Leerlauf manuell neu starten.
   Kein automatischer Druckerneustart.
5. Mainsail öffnen, Browser neu laden. Unter Rinkhals Manage Apps erscheint
   Anycubic Original Camera - WebSocket Bridge. WebSocket (Low Latency) ist Standard; HLS bleibt Rückfallmodus.
   Eine explizit gespeicherte HLS-Auswahl (auch nach Rückfall) bleibt bei
   Updates erhalten. Ohne gespeicherte Auswahl gilt WebSocket.

UPDATES: 0.2.0 bis 0.2.4 sowie 0.2.6 bis 0.2.9 mit vollständigem ready-Zustand werden erkannt.
Die ursprüngliche Sicherung bleibt erhalten, alte App-Dateien werden außerhalb
von apps gesichert. Eine erneute Installation von 1.0.0-rc2 prüft und startet ggf. den aktivierten Dienst.
Bewusst deaktivierte Apps bleiben bei Updates deaktiviert.
Bei abweichender, von Hand geänderter Webcam-Konfiguration oder unbekanntem
Zustand stoppt das Update. Keine erzwungene Überschreibung. Die Sicherung einer
alten App liegt unter /useremain/home/rinkhals/original-camera-hls-upgrade-*.
Bei Stromausfall während Dateitausch ist ggf. manuelle Wiederherstellung nötig.

USB-ERGEBNISTÖNE (RÜCKBAU NEU IN 0.2.8)
Der originale Rinkhals-Erkennungston beim Einstecken bleibt unverändert.
Nach dem Installergebnis: 2 x 150 ms = Erfolg; 3 x 150 ms = Fehler;
2 x 600 ms = vorab durch Druckstatusprüfung gesperrt (auch unbekannter Status);
150 ms + 600 ms = installiert, Moonraker-Neustart oder Kamerastart nicht bestätigt.
USB-Ergebnisdatei und Log bleiben maßgeblich; ein Signalfehler ändert den
Installationsstatus nicht. Keine Veränderung der PWM-Frequenz/Lautstärke.
Signale werden bei fehlendem, aktivem oder stumm konfiguriertem PWM übersprungen.
Manage Apps -> Configure -> USB install/update/rollback result sounds: On/Off.
Die separate App-Option ist standardmäßig On. Ob die Anycubic-Einstellung
Key Sound auch die Rinkhals-Beep-Funktion sperrt, ist nicht bestätigt.
Die Töne gelten für Installation, Update und Rückbau. Beim Rückbau wird die
bisherige Toneinstellung vor Wiederherstellung der Konfiguration gesichert.
Keine Änderung an HLS, WebSocket, Wiederverbindung oder Rückbaulogik.

LOW LATENCY (EXPERIMENTELL)
Rinkhals -> Manage Apps -> Anycubic Original Camera - WebSocket Bridge -> Configure.
Streaming mode: HLS oder Low Latency. Speichern und Kamera-App neu starten,
ausschließlich im Leerlauf. Nicht Klipper oder den gesamten Drucker neu starten.
Falls die Oberfläche den Dienst beim Speichern automatisch neu startet, genügt
Speichern. Der Dienst übernimmt geänderte Auswahl auch im laufenden Betrieb
sobald die Druckstatusprüfung Leerlauf meldet.
Die App schreibt den verwalteten Webcam-Abschnitt und startet Moonraker neu.
In Mainsail Seite neu laden. HLS: /webcam/live.m3u8.
Low Latency: ws://<automatisch erkannte Drucker-IP>:8086/h264, jmuxer-stream.
Öffne Mainsail für Low Latency über die LAN-IP des Druckers per HTTP, nicht
über einen Hostnamen, HTTPS-Proxy oder Remotezugriff. Origin-Prüfung akzeptiert
Browser nur mit der Drucker-IP; native Clients ohne Origin sind erlaubt.
Kein zusätzliches Programm. H264-NAL-Einheiten werden aus dem Original-FLV
extrahiert und über WebSocket übertragen. Keine Neukodierung. Max. 3 Zuschauer,
begrenzte Warteschlangen, Synchronisation an IDR/SPS/PPS. Bildfrequenz-Vorgabe
5 FPS; die reale Kamerafrequenz kann abweichen. Verzögerung nicht gemessen.

RÜCKFALL HLS
Bei 3 aufeinanderfolgenden Low-Latency-Server-/Quellfehlern versucht der Dienst
im Leerlauf auf HLS zurückzustellen und Moonraker neu zu laden. Bei aktivem
Druck wird die Umschaltung verschoben, der Stream versucht erneut zu starten.
Ein ausschließlich im Browser auftretender Wiedergabefehler ist serverseitig
nicht sicher erkennbar: dann HLS manuell unter Configure auswählen, Kamera-App
neu starten, Mainsail neu laden. Es laufen NICHT zwei Streams gleichzeitig.
Der funktionierende HLS-Parametersatz der Version 0.2.3 ist unverändert.

DIAGNOSE: http://<drucker-ip>/webcam/status.html oder /webcam/status.json
Zeigt Version, Streamzustand, Modus, CPU (gesamtes System; erster Wert kann
fehlen), verfügbaren RAM, letzte H264-Daten, WebSocket-Zuschauer und ob
Moonraker den erwarteten Webcam-Eintrag meldet. Portwerte sind das letzte
Ergebnis der Dienstprüfung und können bei Fehlern unbekannt sein.
USB-Dateien: original-camera-hls-result.txt und original-camera-hls-install.log.
BLOCKED: Druckstatus nicht sicher inaktiv; keine Kameraänderung, später erneut versuchen.
SUCCESS: Installer erfolgreich, bei aktivierter App Dienst und Diagnose bestätigt.
   Der laufende Dienst bedeutet noch nicht zwingend, dass Videodaten eintreffen.
INSTALLED: Konfiguration gespeichert, Moonraker-Neustart oder Kamerastart nicht bestätigt;
USB entfernen, in Ruhe manuell neu starten. FAILED: Log prüfen, nicht blind
wiederholen. Keine Zugangsdaten veröffentlichen.

CLOUD TO LAN BRIDGE
Wie bisher: offizielle Bridge ab 1.2 erforderlich. Bei fehlender Bridge wird
fest hinterlegtes Upstream-Paket per HTTPS mit SHA256 geprüft oder alternativ
app-cloud2lan-bridge-ks1.swu im Stammverzeichnis des USB-Sticks genutzt.
Vorhandene veraltete Bridge nicht automatisch überschreiben. LAN-Modus wird
über Bridge aktiviert, nicht über normale Anycubic-Netzwerkeinstellungen.
Installationslogik für fehlende Bridge lokal mit Testarchiven geprüft; echter
Download und erstmalige Installation auf diesem Drucker noch nicht bestätigt.

RÜCKBAU
Für 0.2.7 bis 0.2.9 sowie 1.0.0-rc2 dieses Rückbaupaket verwenden; ältere Versionen benötigen
ihr passendes Paket. Nur im Leerlauf den Ordner rollback/aGVscF9zb3Nf auf USB kopieren, einstecken,
Log prüfen, USB entfernen und manuell neu starten. Stellt ursprüngliche
verwaltete Webcam-Konfiguration, Bridge-Zustand und MJPEG-Flags soweit sicher
möglich wieder her. Fremde Konfigurationsänderungen können Rückbau blockieren.
Eine neu installierte offizielle Bridge bleibt installiert.

TESTS / GRENZEN
Lokal: Python/Shell-Syntax, bisherige HLS-Tests, Modellerkennung, Druckschutz,
Moonraker-Neustart (Mock), Bridge-Prüfsummen/Archive (Mock), echter synthetischer
FLV-H264 -> WebSocket -> FFmpeg-Dekodierung, Ping/Pong, Queue-Grenzen,
Moduswechsel und Rückfall (Mock), Update/Fehler-Rückbau (simulierte Dateien),
Rückbau-Signaltöne mit simuliertem Summer (Erfolg, Fehler, Sperre, stumm).
Zusätzlich lokal: sofortiger Dienststart, bereits laufender Dienst, deaktivierte
App bei Update, fehlender Dienst und wiederholte Installation.
0.2.9: Screenshot zeigt aktivierten und laufenden Dienst; ob ohne manuellen Start nicht explizit bestätigt.
1.0.0-rc2: Versionsstand eingefroren; Streaminglogik unverändert. Update und Rückbau
für die neue Versionsnummer ergänzt. Hardwaretest des RC2 und der Rückbautöne offen.
Weiterhin offen: Dauerbetrieb, DHCP-Wechsel während laufendem Druck,
Stromausfall, erzwungener HLS-Rückfall und parallele Nutzung im Dauertest.
Keine Veröffentlichung im offiziellen App Store erfolgt.

RC2: Einheitlicher Anzeigename (WebSocket Bridge). Update von RC1 unterstützt.
Interne App-ID, Logdateien, URLs und Moonraker-Abschnitt [webcam Webcam]
bleiben absichtlich unverändert, damit keine zweite Kamera entsteht.
