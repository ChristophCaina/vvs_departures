# EFA Departures

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/release/ChristophCaina/vvs_departures.svg)](https://github.com/ChristophCaina/vvs_departures/releases)

Home Assistant Custom Integration für Echtzeit-Abfahrtsdaten via **EFA-Schnittstelle** (Elektronische Fahrplanauskunft).

Unterstützt mehrere Regionen in Deutschland — nicht mehr nur VVS/Stuttgart.

> **v2 Pre-Release:** Diese Version bringt einen grundlegend überarbeiteten Config Flow und Multi-Region-Support. Bestehende `vvs_departures`-Einträge werden automatisch migriert. Bitte als Pre-Release behandeln und [Feedback geben](https://github.com/ChristophCaina/vvs_departures/issues).

## Unterstützte Regionen

| Region | Abgedeckte Verbünde |
|---|---|
| EFA-BW / NVBW (Baden-Württemberg) | VVS, KVV, DING, naldo, AVV, TBO, SBG, bwegt, … |
| DEFAS Bayern | MVV, VGN, VVM, RVO, … |
| VRR (Rhein-Ruhr, NRW) | VRR, VRS, NWL, Ruhrbahn, … |
| VRN (Rhein-Neckar) | VRN (Mannheim, Heidelberg, …) |
| Benutzerdefiniert | Eigener EFA-Endpunkt |

> Die Daten werden über die EFA-JSON-API der jeweiligen Instanz abgerufen. Für die EFA-BW-Instanz gelten die [Nutzungsbedingungen der NVBW / MobiData BW](https://www.mobidata-bw.de/data/Nutzungsbedingungen_Trias.pdf). Die Nutzung ist kostenlos; für produktiven Einsatz wird eine Registrierung unter [mobidata-bw@nvbw.de](mailto:mobidata-bw@nvbw.de) empfohlen.

## Features

- 🗺️ **Multi-Region** – EFA-BW, Bayern, Rhein-Ruhr, Rhein-Neckar oder eigener Endpunkt
- 🔍 **Haltestellensuche** direkt im Config Flow mit Stadt-Filter
- 🚆 **Dynamische Icons** nach Verkehrsmittel (S-Bahn, Bus, Fähre, Tram, …)
- 📋 **Linienfilter** – nur gewünschte Linien überwachen (via `XML_SERVINGLINES_REQUEST`)
- ⏱️ **Echtzeit-Daten** mit Verspätungsanzeige
- ⚠️ **Störungsmeldungen** als separater Sensor (gefiltert nach Priorität und Typ)
- 🔔 **Notices pro Abfahrt** – Meldungen direkt an der jeweiligen Abfahrt
- 🔧 **Optionen nachträglich änderbar**

## Installation via HACS

1. HACS öffnen → Integrationen → ⋮ → Benutzerdefinierte Repositories
2. URL: `https://github.com/ChristophCaina/vvs_departures/`
3. Kategorie: **Integration**
4. Hinzufügen → Suche nach "EFA Departures" → Installieren
5. Home Assistant neu starten

## Manuelle Installation

1. Den Ordner `custom_components/vvs_departures` in dein HA-Konfigurationsverzeichnis kopieren
2. Home Assistant neu starten

## Einrichtung

1. **Einstellungen → Geräte & Dienste → Integration hinzufügen → EFA Departures**
2. Region wählen (z.B. `EFA-BW / NVBW (Baden-Württemberg)`)
3. Stadt / Ort eingeben (z.B. `Stuttgart` oder `Freiburg im Breisgau`)
4. Haltestelle suchen und auswählen
5. Linien auswählen (oder „Alle Linien" für keinen Filter)
6. Fertig – Sensoren erscheinen unter einem Gerät

> **Hinweis zur Ortssuche:** Manche Städte müssen mit vollem Namen eingegeben werden (z.B. `Freiburg im Breisgau` statt `Freiburg`). Kurzformen werden automatisch als Fallback versucht.

## Migration von v1 (VVS Departures)

Bestehende Einträge werden beim Neustart **automatisch migriert** — kein manuelles Eingreifen nötig. Der EFA-BW-Endpunkt (efa-bw.de) bleibt unverändert.

## Sensoren

Pro konfigurierter Haltestelle entstehen folgende Sensoren:

### Abfahrt 1–N (`sensor.<stop>_departure_<n>`)

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
| `notices` | Liste von Meldungen direkt zu dieser Abfahrt |
| `notice_title` | Titel der ersten Meldung (Kurzform) |
| `notice_text` | Text der ersten Meldung |

### Störungsmeldungen (`sensor.<stop>_disruptions`)

State = Anzahl aktiver Meldungen (max. 10)

| Attribut | Beschreibung |
|---|---|
| `disruptions` | Liste der Meldungen (max. 10) |
| `total_count` | Gesamtzahl der Meldungen |
| `highest_priority` | Höchste Priorität (`veryHigh`, `high`, `normal`, `low`) |
| `top_disruption_title` | Titel der wichtigsten Meldung |
| `top_disruption_text` | Text der wichtigsten Meldung (max. 1500 Zeichen) |

Jede Meldung in der `disruptions`-Liste enthält:
```yaml
- id: "VVS-ems-25404"
  title: "Renningen - Weil der Stadt: Zugausfälle wegen Brückenprüfungen"
  text: "Wegen Brückenprüfungen kommt es ..."
  priority: "high"
  type: "lineInfo"
  line: "S6"
  created: "2026-05-27T07:55:00Z"
```

## Optionen

Nach der Einrichtung über **Konfigurieren** am Integrations-Eintrag änderbar:

- Anzahl Abfahrten (1–10)
- Aktualisierungsintervall (30–300 Sekunden)
- Linienfilter (Linien werden frisch via SERVINGLINES geladen)
- Störungsmeldungen: Prioritätsfilter (`veryHigh`, `high`, `normal`, `low`)
- Störungsmeldungen: Typfilter (`lineInfo`, `stationInfo`, `stopInfo`, `network`)

## Dashboard-Beispiele

Standard mit Tile Card:  
<img width="236" height="60" alt="grafik" src="https://github.com/user-attachments/assets/9f6f5099-4357-4122-9674-1adbc36e726a" />

Card-Mod – Farbänderung bei Verspätung:  
<img width="223" height="64" alt="grafik" src="https://github.com/user-attachments/assets/84ecca51-6387-4f66-9e09-99510f431cd2" />

```yaml
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

Card-Mod – Badge bei Verspätung oder Meldung:  
<img width="225" height="62" alt="grafik" src="https://github.com/user-attachments/assets/0e2e9622-7f5f-41f3-b28c-2aa7c56826b4" />

```yaml
card_mod:
  style: |
    ha-card {
      {% set delay = state_attr(config.entity, 'delay_minutes') | int(0) %}
      {% if delay >= 10 %}
        --tile-color: var(--red-color) !important;
      {% elif delay >= 3 %}
        --tile-color: var(--amber-color) !important;
      {% else %}
        --tile-color: var(--green-color) !important;
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

Störungsmeldungen als Markdown-Card:

```yaml
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

[EFA Departures Card](https://github.com/ChristophCaina/VVS-Departure-Card)  
<img width="456" height="419" alt="grafik" src="https://github.com/user-attachments/assets/e5d59f9c-5615-4a8e-8c7f-d6ece7e0b8d7" />

## Lizenz

MIT License
