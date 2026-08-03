"""EFA Departures - Home Assistant Custom Integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EFAApiClient
from .const import (
    ALL_LINES_SENTINEL_KEY,
    CONF_CITY_NAME,
    CONF_DEPARTURE_COUNT,
    CONF_EFA_BASE_URL,
    CONF_LINE_DIRECTIONS,
    CONF_LINE_FILTER,
    CONF_REGION_NAME,
    CONF_STOP_ID,
    CONF_UPDATE_INTERVAL,
    DEFAULT_DEPARTURE_COUNT,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import EFADeparturesCoordinator

_LOGGER = logging.getLogger(__name__)

_LEGACY_BASE_URL    = "https://www.efa-bw.de/bvb3"
_LEGACY_REGION      = "EFA-BW / NVBW (Baden-Württemberg)"
_LEGACY_CITY        = ""   # unknown for old entries — leave empty


async def _lookup_line_names(hass: HomeAssistant, data: dict, global_ids: list[str]) -> dict[str, str]:
    """Best-effort lookup of human-readable line names for migration.

    Falls back to using the raw global_id as the name if the API can't be
    reached at migration time — the user can rename/adjust in Options later.
    """
    names: dict[str, str] = {}
    try:
        session = async_get_clientsession(hass)
        client = EFAApiClient(session, data[CONF_EFA_BASE_URL])
        lines = await client.get_serving_lines(data[CONF_STOP_ID])
        for line in lines:
            names.setdefault(line["global_id"], line["name"])
    except Exception as exc:
        _LOGGER.warning("Could not resolve line names during migration: %s", exc)
    return names


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Migrate config entries to the current schema version (v4).

    v1  vvs_departures:        no base_url / region / city fields
    v2  vvs_departures early:  has base_url + provider_name
    v3  vvs_departures:        has base_url + region_name + city_name;
                                filtering via flat line_filter + departure_count
    v4  vvs_departures current: filtering via line_directions
                                (Linie + Richtung + eigene Sensor-Anzahl je Eintrag)
    """
    _LOGGER.debug("Migrating EFA Departures entry from version %s", entry.version)
    new_data = {**entry.data}
    version = entry.version

    if version == 1:
        new_data.setdefault(CONF_EFA_BASE_URL, _LEGACY_BASE_URL)
        new_data.setdefault(CONF_REGION_NAME,  _LEGACY_REGION)
        new_data.setdefault(CONF_CITY_NAME,    _LEGACY_CITY)
        new_data.pop("network_name", None)
        version = 3
        _LOGGER.info("Migrating '%s': v1 → v3", entry.title)

    elif version == 2:
        provider = new_data.pop("provider_name", _LEGACY_REGION)
        new_data.setdefault(CONF_REGION_NAME, provider)
        new_data.setdefault(CONF_CITY_NAME,   _LEGACY_CITY)
        new_data.pop("network_name", None)
        version = 3
        _LOGGER.info("Migrating '%s': v2 → v3", entry.title)

    elif version == 3 and "network_name" in new_data:
        # Clean up network_name if present from an intermediate v3 build
        new_data.pop("network_name", None)
        new_data.setdefault(CONF_CITY_NAME, _LEGACY_CITY)
        _LOGGER.info("Cleaned '%s': removed network_name", entry.title)

    if version == 3:
        # v3 → v4: flat line_filter + one global departure_count becomes a
        # list of per-"Linie + Richtung" entries, each with its own count.
        # Existing selections keep matching BOTH directions of their line
        # (destination=None) until the user narrows them down in Options —
        # exactly preserving old behaviour, just in the new shape.
        old_line_filter: list[str] = new_data.pop(CONF_LINE_FILTER, [])
        old_count: int = new_data.pop(CONF_DEPARTURE_COUNT, DEFAULT_DEPARTURE_COUNT)

        if old_line_filter:
            line_names = await _lookup_line_names(hass, new_data, old_line_filter)
            new_data[CONF_LINE_DIRECTIONS] = [
                {
                    "key": f"{gid}|",
                    "line_global_id": gid,
                    "line_name": line_names.get(gid, gid),
                    "destination": None,
                    "count": old_count,
                }
                for gid in old_line_filter
            ]
        else:
            new_data[CONF_LINE_DIRECTIONS] = [
                {
                    "key": ALL_LINES_SENTINEL_KEY,
                    "line_global_id": None,
                    "line_name": "Alle Linien (kein Filter, altes Verhalten)",
                    "destination": None,
                    "count": old_count,
                }
            ]
        version = 4
        _LOGGER.info("Migrated '%s': v3 → v4 (line_directions)", entry.title)

    hass.config_entries.async_update_entry(entry, data=new_data, version=version)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up EFA Departures from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    merged = {**entry.data, **entry.options}
    update_interval = merged.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)

    session = async_get_clientsession(hass)
    coordinator = EFADeparturesCoordinator(
        hass=hass,
        session=session,
        config_entry_data=merged,
        update_interval=update_interval,
    )

    await coordinator.async_config_entry_first_refresh()
    hass.data[DOMAIN][entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
