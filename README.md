# VVS Departures

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/release/ChristophCaina/vvs_departures.svg)](https://github.com/ChristophCaina/vvs_departures/releases)

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
2. URL: `https://github.com/ChristophCaina/vvs_departures/`
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

Standard mit Tile Card:  
<img width="236" height="60" alt="grafik" src="https://github.com/user-attachments/assets/9f6f5099-4357-4122-9674-1adbc36e726a" />

Card-Mod Modifikation - Farbänderung bei Verspätung:  
<img width="223" height="64" alt="grafik" src="https://github.com/user-attachments/assets/84ecca51-6387-4f66-9e09-99510f431cd2" />

```
card_mod:
  style: |
    ha-card {
      {% if states(config.entity) in ['unknown', 'unavailable'] %}
      {% else %}
        {% set delay = state_attr(config.entity, 'delay_minutes') | int(0) %}
        {% if delay >= 10 %}
          --tile-color: var(--red-color) !important;
        {% elif delay >= 3 %}
          --tile-color: var(--amber-color) !important;
        {% else %}
          --tile-color: var(--green-color) !important;
        {% endif %}
      {% endif %}
    }
```

Card-Mod Modifikation mit Anzeige, wenn eine Notification vorliegt:  
<img width="225" height="62" alt="grafik" src="https://github.com/user-attachments/assets/0e2e9622-7f5f-41f3-b28c-2aa7c56826b4" />

```
card_mod:
  style: |
    ha-card {
      {% if states(config.entity) in ['unknown', 'unavailable'] %}
      {% else %}
        {% set delay = state_attr(config.entity, 'delay_minutes') | int(0) %}
        {% if delay >= 10 %}
          --tile-color: var(--red-color) !important;
        {% elif delay >= 3 %}
          --tile-color: var(--amber-color) !important;
        {% else %}
          --tile-color: var(--green-color) !important;
        {% endif %}
      {% endif %}
    }
    ha-tile-icon::after {
      {% set delay = state_attr(config.entity, 'delay_minutes') | int(0) %}
      {% set notices = state_attr(config.entity, 'notices') %}
      {% set has_notice = notices is not none and notices | length > 0 %}
      {% if states(config.entity) in ['unknown', 'unavailable'] or (delay < 3 and not has_notice) %}
        display: none;
      {% else %}
        content: '';
        display: block;
        position: absolute;
        top: 0px;
        right: 0px;
        width: 12px;
        height: 12px;
        border-radius: 50%;
        background-color: {% if delay >= 10 %}var(--red-color){% elif delay >= 3 %}var(--amber-color){% else %}var(--info-color){% endif %} !important;
        border: 2px solid var(--card-background-color);
      {% endif %}
    }
```

Ausgabe als Fahrplan-Info mit allen Meldungen pro Sensor:  
<img width="462" height="1173" alt="grafik" src="https://github.com/user-attachments/assets/c6b9e595-0673-4361-b2ce-c53c4af6e7fd" />

```
type: markdown
content: |-
  {% set dep = state_attr('sensor.your_sensor_id', 'notices') %}
  🚆 **S6 → Nächste Abfahrt**
  {{ state_attr('sensor.your_sensor_id', 'label') }}

  {% if dep and dep | length > 0 %}
  ---
  {% for notice in dep %}
  ⚠️ **{{ notice.title }}**
  {{ notice.text }}
  {% endfor %}
  {% endif %}

```
[VSS Departures Card](https://github.com/ChristophCaina/VVS-Departure-Card)  
<img width="456" height="419" alt="grafik" src="https://github.com/user-attachments/assets/e5d59f9c-5615-4a8e-8c7f-d6ece7e0b8d7" />


## Optionen

Nach der Einrichtung über **Konfigurieren** am Integrations-Eintrag änderbar:

- Anzahl Abfahrten (1–10)
- Aktualisierungsintervall (30–300 Sekunden)
- Linienfilter

## Unterstützte Haltestellen

Alle Haltestellen im VVS-Netz (Stuttgart und Umgebung) über die EFA-Schnittstelle `efa-bw.de`.

## Lizenz

MIT License
