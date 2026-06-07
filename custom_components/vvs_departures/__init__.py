"""EFA Departures - Home Assistant Custom Integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_CITY_NAME,
    CONF_EFA_BASE_URL,
    CONF_REGION_NAME,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import EFADeparturesCoordinator

_LOGGER = logging.getLogger(__name__)

_LEGACY_BASE_URL    = "https://www.efa-bw.de/bvb3"
_LEGACY_REGION      = "EFA-BW / NVBW (Baden-Württemberg)"
_LEGACY_CITY        = ""   # unknown for old entries — leave empty


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Migrate config entries to the current schema version (v3).

    v1  vvs_departures:        no base_url / region / city fields
    v2  vvs_departures early:  has base_url + provider_name
    v3  vvs_departures current: has base_url + region_name + city_name
                                (network_name removed)
    """
    _LOGGER.debug("Migrating EFA Departures entry from version %s", entry.version)
    new_data = {**entry.data}

    if entry.version == 1:
        new_data.setdefault(CONF_EFA_BASE_URL, _LEGACY_BASE_URL)
        new_data.setdefault(CONF_REGION_NAME,  _LEGACY_REGION)
        new_data.setdefault(CONF_CITY_NAME,    _LEGACY_CITY)
        new_data.pop("network_name", None)
        hass.config_entries.async_update_entry(entry, data=new_data, version=3)
        _LOGGER.info("Migrated '%s': v1 → v3", entry.title)

    elif entry.version == 2:
        provider = new_data.pop("provider_name", _LEGACY_REGION)
        new_data.setdefault(CONF_REGION_NAME, provider)
        new_data.setdefault(CONF_CITY_NAME,   _LEGACY_CITY)
        new_data.pop("network_name", None)
        hass.config_entries.async_update_entry(entry, data=new_data, version=3)
        _LOGGER.info("Migrated '%s': v2 → v3", entry.title)

    elif entry.version == 3 and "network_name" in new_data:
        # Clean up network_name if present from an intermediate v3 build
        new_data.pop("network_name", None)
        new_data.setdefault(CONF_CITY_NAME, _LEGACY_CITY)
        hass.config_entries.async_update_entry(entry, data=new_data, version=3)
        _LOGGER.info("Cleaned '%s': removed network_name", entry.title)

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
