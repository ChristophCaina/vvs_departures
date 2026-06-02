"""VVS Departures - Home Assistant Custom Integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import VVSDeparturesCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up VVS Departures from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    merged = {**entry.data, **entry.options}
    update_interval = merged.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)

    session = async_get_clientsession(hass)
    coordinator = VVSDeparturesCoordinator(
        hass=hass,
        session=session,
        config_entry_data=merged,
        update_interval=update_interval,
    )

    # Initial data fetch
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Re-setup on options change
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update – reload the entry."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
