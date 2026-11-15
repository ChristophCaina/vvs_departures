# Changelog

## [3.0.0-beta3] - 2026-11-15

### Changed
- Warnung „fand keine Übereinstimmung“ wird nicht mehr bei jedem Abruf geloggt, sondern einmal pro Ausfall-Episode einer Richtung; erneut nur, wenn neue Live-Ziele auftauchen. Sobald die Richtung wieder Abfahrten findet, erscheint eine Info-Meldung. Die Meldung enthält jetzt die Haltestellen-ID.
- Fehlgeschlagene Abrufe werden nicht mehr doppelt geloggt (API + Coordinator); der Coordinator meldet den ersten Fehler und die Wiederherstellung.

### Fixed
- Leere Fehlermeldungen bei Timeouts („Error communicating with EFA API: “) zeigen jetzt Typ und Ursache.

## [1.0.0] - 2026-06-02

### Added
- Initiale Version
- Config Flow mit Haltestellensuche (EFA StopFinder)
- Automatisches Laden der verfügbaren Linien pro Haltestelle
- Linienfilter (Mehrfachauswahl)
- Abfahrts-Sensoren mit Echtzeit-Daten und Verspätungsanzeige
- Störungsmeldungs-Sensor (bereinigt, de-dupliziert)
- Options Flow für nachträgliche Anpassungen
- Deutsche und englische Übersetzungen
