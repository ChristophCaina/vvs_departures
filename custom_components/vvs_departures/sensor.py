"""Sensor platform for VVS Departures."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_DEPARTURE_COUNT,
    CONF_STOP_ID,
    CONF_STOP_NAME,
    DEFAULT_DEPARTURE_COUNT,
    DOMAIN,
)
from .coordinator import VVSDeparturesCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up VVS sensor entities from a config entry."""
    coordinator: VVSDeparturesCoordinator = hass.data[DOMAIN][entry.entry_id]

    merged = {**entry.data, **entry.options}
    departure_count = merged.get(CONF_DEPARTURE_COUNT, DEFAULT_DEPARTURE_COUNT)
    stop_id = merged[CONF_STOP_ID]
    stop_name = merged.get(CONF_STOP_NAME, stop_id)

    entities: list[SensorEntity] = []

    # One sensor per departure slot
    for idx in range(departure_count):
        entities.append(
            VVSDepartureSensor(
                coordinator=coordinator,
                entry=entry,
                stop_id=stop_id,
                stop_name=stop_name,
                index=idx,
            )
        )

    # One disruptions sensor
    entities.append(
        VVSDisruptionSensor(
            coordinator=coordinator,
            entry=entry,
            stop_id=stop_id,
            stop_name=stop_name,
        )
    )

    async_add_entities(entities)


def _minutes_label(minutes: int) -> str:
    """Human readable departure time label."""
    if minutes < 0:
        return "Verpasst"
    if minutes == 0:
        return "Jetzt"
    if minutes == 1:
        return "1 Min"
    if minutes > 120:
        return "Später"
    return f"{minutes} Min"


class VVSDepartureSensor(CoordinatorEntity[VVSDeparturesCoordinator], SensorEntity):
    """Represents a single departure slot at a stop."""

    _attr_icon = "mdi:train"
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(
        self,
        coordinator: VVSDeparturesCoordinator,
        entry: ConfigEntry,
        stop_id: str,
        stop_name: str,
        index: int,
    ) -> None:
        super().__init__(coordinator)
        self._index = index
        self._stop_id = stop_id
        self._stop_name = stop_name
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_departure_{index}"
        self._fallback_name = f"Abfahrt {index + 1}"

    @property
    def name(self) -> str:
        """Dynamic name: 'S6 → Weil der Stadt' or fallback 'Abfahrt N'."""
        dep = self._departure
        if dep is None:
            return self._fallback_name
        line = dep.get("line", "")
        dest = dep.get("destination", "")
        if line and dest:
            return f"{line} → {dest}"
        return self._fallback_name

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._stop_id)},
            name=self._stop_name,
            manufacturer="VVS / EFA",
            model="Abfahrtstafel",
            entry_type="service",
        )

    @property
    def _departure(self) -> dict | None:
        """Return departure data for this slot, or None."""
        if not self.coordinator.data:
            return None
        departures = self.coordinator.data.get("departures", [])
        if self._index < len(departures):
            return departures[self._index]
        return None

    @property
    def native_value(self) -> datetime | None:
        """State: datetime of estimated departure (device_class=timestamp)."""
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

        # Calculate minutes until departure
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


class VVSDisruptionSensor(CoordinatorEntity[VVSDeparturesCoordinator], SensorEntity):
    """Represents disruption messages for a stop."""

    _attr_icon = "mdi:alert-circle-outline"
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: VVSDeparturesCoordinator,
        entry: ConfigEntry,
        stop_id: str,
        stop_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._stop_id = stop_id
        self._stop_name = stop_name
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_disruptions"
        self._attr_name = "Störungsmeldungen"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._stop_id)},
            name=self._stop_name,
            manufacturer="VVS / EFA",
            model="Abfahrtstafel",
            entry_type="service",
        )

    @property
    def native_value(self) -> int:
        """State: number of active disruptions."""
        if not self.coordinator.data:
            return 0
        return len(self.coordinator.data.get("disruptions", []))

    @property
    def icon(self) -> str:
        count = self.native_value
        if count == 0:
            return "mdi:check-circle-outline"
        return "mdi:alert-circle-outline"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not self.coordinator.data:
            return {"disruptions": []}
        disruptions = self.coordinator.data.get("disruptions", [])
        # Cap at 20 entries to stay within HA's 16KB attribute limit
        capped = disruptions[:20]
        return {
            "disruptions": capped,
            "total_count": len(disruptions),
            "highest_priority": disruptions[0]["priority"] if disruptions else None,
        }
