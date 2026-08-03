"""DataUpdateCoordinator for EFA Departures."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import EFAApiClient
from .const import (
    CONF_DISRUPTION_PRIORITIES,
    CONF_DISRUPTION_TYPES,
    CONF_EFA_BASE_URL,
    CONF_LINE_DIRECTIONS,
    CONF_STOP_ID,
    DEFAULT_DISRUPTION_PRIORITIES,
    DEFAULT_DISRUPTION_TYPES,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class EFADeparturesCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator that fetches departures + disruptions in a single API call."""

    def __init__(
        self,
        hass: HomeAssistant,
        session: aiohttp.ClientSession,
        config_entry_data: dict,
        update_interval: int = DEFAULT_UPDATE_INTERVAL,
    ) -> None:
        base_url: str = config_entry_data[CONF_EFA_BASE_URL]
        self._client = EFAApiClient(session, base_url)
        self._stop_id: str = config_entry_data[CONF_STOP_ID]
        self._direction_filters: list[dict] = config_entry_data.get(CONF_LINE_DIRECTIONS, [])
        self._priority_filter: list[str] = config_entry_data.get(
            CONF_DISRUPTION_PRIORITIES, DEFAULT_DISRUPTION_PRIORITIES
        )
        self._type_filter: list[str] = config_entry_data.get(
            CONF_DISRUPTION_TYPES, DEFAULT_DISRUPTION_TYPES
        )

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{self._stop_id}",
            update_interval=timedelta(seconds=update_interval),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from EFA API."""
        try:
            result = await self._client.get_departures(
                stop_id=self._stop_id,
                direction_filters=self._direction_filters,
                priority_filter=self._priority_filter if self._priority_filter else None,
                type_filter=self._type_filter if self._type_filter else None,
            )
        except Exception as exc:
            raise UpdateFailed(f"Error communicating with EFA API: {exc}") from exc

        return result
