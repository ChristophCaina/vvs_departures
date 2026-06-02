# VVS Departures

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/release/yourusername/ha-vvs-departures.svg)](https://github.com/yourusername/ha-vvs-departures/releases)

Home Assistant Custom Integration für Echtzeit-Abfahrtsdaten des **Verkehrs- und Tarifverbunds Stuttgart (VVS)** via EFA-Schnittstelle.

## Features

- 🔍 **Haltestellensuche** direkt im Config Flow (kein manuelles Suchen von Stop-IDs)
- 🚆 **Linienfilter** – nur die gewünschten Linien überwachen
- ⏱️ **Echtzeit-Daten** mit Verspätungsanzeige
- ⚠️ **Störungsmeldungen** als separater Sensor (bereinigt, kein HTML)
- 🔧 **Optionen nachträglich änderbar** (Anzahl Abfahrten, Update-Intervall, Linienfilter)
- 📱 Optimiert für Dashboard-Karten

## Installation via HACS

1. HACS öffnen → Integrationen → ⋮ → Benutzerdefinierte Repositories
2. URL: `https://github.com/yourusername/ha-vvs-departures`
3. Kategorie: **Integration**
4. Hinzufügen → Suche nach "VVS Departures" → Installieren
5. Home Assistant neu starten

## Manuelle Installation

1. Den Ordner `custom_components/vvs_departures` in dein HA-Konfigurationsverzeichnis kopieren
2. Home Assistant neu starten

## Einrichtung

1. **Einstellungen → Geräte & Dienste → Integration hinzufügen → VVS Departures**
2. Haltestelle eingeben (z.B. `Renningen` oder `Stuttgart Hbf`)
3. Aus den Treffern auswählen
4. Linien auswählen (oder „Alle Linien" für keinen Filter)
5. Fertig – Sensoren erscheinen unter einem Gerät

## Sensoren

Pro konfigurierter Haltestelle entstehen folgende Sensoren:

### Abfahrt 1–N (`sensor.<stop>_abfahrt_<n>`)

| Attribut | Beschreibung |
|---|---|
| `line` | Kurzname der Linie (z.B. `S6`) |
| `line_full` | Vollname (z.B. `S-Bahn S6`) |
| `destination` | Ziel (z.B. `Weil der Stadt`) |
| `planned` | Geplante Abfahrt (ISO 8601, UTC) |
| `estimated` | Geschätzte Abfahrt mit Echtzeit |
| `delay_minutes` | Verspätung in Minuten |
| `minutes_until` | Minuten bis zur Abfahrt |
| `label` | Kurzanzeige, z.B. `S6 (+2) · 5 Min` |
| `platform` | Gleis / Bahnsteig |
| `realtime` | `true` wenn Echtzeitdaten vorhanden |

### Störungsmeldungen (`sensor.<stop>_storungsmeldungen`)

State = Anzahl aktiver Meldungen

| Attribut | Beschreibung |
|---|---|
| `disruptions` | Liste aller Meldungen (siehe unten) |
| `highest_priority` | Höchste Priorität (`high`, `normal`, `low`) |

Jede Meldung in der Liste enthält:
```yaml
- id: "VVS-ems-25404"
  title: "Renningen - Weil der Stadt: Zugausfälle wegen Brückenprüfungen"
  text: "Wegen Brückenprüfungen kommt es ..."
  priority: "high"
  line: "S6"
  created: "2026-05-27T07:55:00Z"
```

## Dashboard-Beispiel

```yaml
type: markdown
content: |
  {% set dep = state_attr('sensor.renningen_abfahrt_1', 'label') %}
  ## 🚆 Nächste Abfahrt
  {{ dep }}
```

## Optionen

Nach der Einrichtung über **Konfigurieren** am Integrations-Eintrag änderbar:

- Anzahl Abfahrten (1–10)
- Aktualisierungsintervall (30–300 Sekunden)
- Linienfilter

## Unterstützte Haltestellen

Alle Haltestellen im VVS-Netz (Stuttgart und Umgebung) über die EFA-Schnittstelle `efa-bw.de`.

## Lizenz

MIT License
