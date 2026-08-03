"""Sensor platform for EFA Departures."""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_CITY_NAME,
    CONF_LINE_DIRECTIONS,
    CONF_STOP_ID,
    CONF_STOP_NAME,
    DOMAIN,
)
from .coordinator import EFADeparturesCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EFA sensor entities from a config entry."""
    coordinator: EFADeparturesCoordinator = hass.data[DOMAIN][entry.entry_id]

    merged = {**entry.data, **entry.options}
    line_directions = merged.get(CONF_LINE_DIRECTIONS, [])
    stop_id = merged[CONF_STOP_ID]
    stop_name = merged.get(CONF_STOP_NAME, stop_id)
    provider_name = merged.get(CONF_CITY_NAME, "EFA")

    entities: list[SensorEntity] = []

    for direction_entry in line_directions:
        count = max(int(direction_entry.get("count", 0)), 0)
        for idx in range(count):
            entities.append(
                EFADepartureSensor(
                    coordinator=coordinator,
                    entry=entry,
                    stop_id=stop_id,
                    stop_name=stop_name,
                    provider_name=provider_name,
                    bucket_key=direction_entry["key"],
                    line_name=direction_entry.get("line_name", ""),
                    destination_name=direction_entry.get("destination"),
                    index=idx,
                )
            )

    entities.append(
        EFADisruptionSensor(
            coordinator=coordinator,
            entry=entry,
            stop_id=stop_id,
            stop_name=stop_name,
            provider_name=provider_name,
        )
    )

    async_add_entities(entities)


def _minutes_label(minutes: int) -> str:
    if minutes < 0:
        return "Verpasst"
    if minutes == 0:
        return "Jetzt"
    if minutes == 1:
        return "1 Min"
    if minutes > 120:
        return "Später"
    return f"{minutes} Min"


class EFADepartureSensor(CoordinatorEntity[EFADeparturesCoordinator], SensorEntity):
    """Represents a single departure slot at a stop."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    # EFA motType → MDI icon
    # 0=Fernzug/ICE, 1=S-Bahn, 2=U-Bahn, 3=Stadtbahn, 4=Straßenbahn,
    # 5=Stadtbus, 6=Regionalbus, 7=Schnellbus, 8=Nachtbus,
    # 9=Fähre/Schiff, 10=Seilbahn, 11=Schwebebahn, 17=AST/Rufbus
    _MOT_ICONS: dict[int, str] = {
        0:  "mdi:train",
        1:  "mdi:train-variant",
        2:  "mdi:subway-variant",
        3:  "mdi:tram",
        4:  "mdi:tram",
        5:  "mdi:bus",
        6:  "mdi:bus",
        7:  "mdi:bus-express",
        8:  "mdi:bus-clock",
        9:  "mdi:ferry",
        10: "mdi:gondola",
        11: "mdi:gondola",
        17: "mdi:bus-stop",
    }
    _DEFAULT_ICON = "mdi:transit-connection-variant"

    def __init__(
        self,
        coordinator: EFADeparturesCoordinator,
        entry: ConfigEntry,
        stop_id: str,
        stop_name: str,
        provider_name: str,
        bucket_key: str,
        line_name: str,
        destination_name: str | None,
        index: int,
    ) -> None:
        super().__init__(coordinator)
        self._index = index
        self._bucket_key = bucket_key
        self._stop_id = stop_id
        self._stop_name = stop_name
        self._provider_name = provider_name
        self._entry = entry
        # unique_id is derived from a hash of the bucket key rather than the
        # raw key itself, since the key can contain EFA globalId characters
        # that aren't guaranteed to be safe/stable as an entity_id fragment.
        bucket_hash = hashlib.sha1(bucket_key.encode("utf-8")).hexdigest()[:10]
        self._attr_unique_id = f"{entry.entry_id}_{bucket_hash}_{index}"
        if destination_name:
            self._fallback_name = f"{line_name} → {destination_name} · Abfahrt {index + 1}"
        elif line_name:
            self._fallback_name = f"{line_name} · Abfahrt {index + 1}"
        else:
            self._fallback_name = f"Abfahrt {index + 1}"

    @property
    def name(self) -> str:
        dep = self._departure
        if dep is None:
            return self._fallback_name
        line = dep.get("line", "")
        dest = dep.get("destination", "")
        if line and dest:
            return f"{line} → {dest}"
        return self._fallback_name

    @property
    def icon(self) -> str:
        """Return icon based on EFA mode of transport (motType)."""
        dep = self._departure
        if dep is None:
            return self._DEFAULT_ICON
        return self._MOT_ICONS.get(dep.get("mot_type", -1), self._DEFAULT_ICON)

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._stop_id)},
            name=self._stop_name,
            manufacturer=self._provider_name,
            model="Abfahrtstafel",
            entry_type="service",
        )

    @property
    def _departure(self) -> dict | None:
        if not self.coordinator.data:
            return None
        bucket = self.coordinator.data.get("buckets", {}).get(self._bucket_key, [])
        if self._index < len(bucket):
            return bucket[self._index]
        return None

    @property
    def native_value(self) -> datetime | None:
        dep = self._departure
        if dep is None:
            return None
        estimated_str = dep.get("estimated") or dep.get("planned")
        if not estimated_str:
            return None
        try:
            return datetime.fromisoformat(estimated_str.replace("Z", "+00:00"))
        except Exception:
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        dep = self._departure
        if dep is None:
            return {
                "line": None,
                "destination": None,
                "planned": None,
                "estimated": None,
                "delay_minutes": 0,
                "minutes_until": -1,
                "label": "Keine Daten",
                "platform": None,
                "realtime": False,
                "notices": [],
                "notice_title": None,
                "notice_text": None,
            }

        estimated_str = dep.get("estimated") or dep.get("planned")
        minutes_until = -1
        if estimated_str:
            try:
                est_dt = datetime.fromisoformat(estimated_str.replace("Z", "+00:00"))
                now = datetime.now(tz=timezone.utc)
                minutes_until = int((est_dt - now).total_seconds() // 60)
            except Exception:
                pass

        delay = dep.get("delay_minutes", 0)
        delay_str = f" (+{delay})" if delay > 0 else ""
        label = f"{dep['line']}{delay_str} · {_minutes_label(minutes_until)}"

        return {
            "line": dep.get("line"),
            "line_full": dep.get("line_full"),
            "destination": dep.get("destination"),
            "planned": dep.get("planned"),
            "estimated": dep.get("estimated"),
            "delay_minutes": dep.get("delay_minutes", 0),
            "minutes_until": minutes_until,
            "label": label,
            "platform": dep.get("platform"),
            "realtime": dep.get("realtime", False),
            "notices": dep.get("notices", []),
            "notice_title": dep["notices"][0]["title"] if dep.get("notices") else None,
            "notice_text": dep["notices"][0]["text"] if dep.get("notices") else None,
        }


class EFADisruptionSensor(CoordinatorEntity[EFADeparturesCoordinator], SensorEntity):
    """Represents disruption messages for a stop."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EFADeparturesCoordinator,
        entry: ConfigEntry,
        stop_id: str,
        stop_name: str,
        provider_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._stop_id = stop_id
        self._stop_name = stop_name
        self._provider_name = provider_name
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_disruptions"
        self._attr_name = "Störungsmeldungen"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._stop_id)},
            name=self._stop_name,
            manufacturer=self._provider_name,
            model="Abfahrtstafel",
            entry_type="service",
        )

    @property
    def native_value(self) -> int:
        if not self.coordinator.data:
            return 0
        return len(self.coordinator.data.get("disruptions", []))

    @property
    def icon(self) -> str:
        return "mdi:check-circle-outline" if self.native_value == 0 else "mdi:alert-circle-outline"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not self.coordinator.data:
            return {"disruptions": [], "total_count": 0, "highest_priority": None, "top_disruption_title": None, "top_disruption_text": None}
        disruptions = self.coordinator.data.get("disruptions", [])

        # Cap list at 10 entries to stay safely under HA's 16KB attribute limit.
        # Worst case: 10 × (200 title + 500 text + ~100 meta) ≈ 8KB — well within limits.
        capped = disruptions[:10]

        # Top disruption: full text of the highest-priority entry (up to 1500 chars)
        top = disruptions[0] if disruptions else None

        return {
            "disruptions": capped,
            "total_count": len(disruptions),
            "highest_priority": top["priority"] if top else None,
            "top_disruption_title": top["title"] if top else None,
            "top_disruption_text": top["text"][:1500] if top else None,
        }
