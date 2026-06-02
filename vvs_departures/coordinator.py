"""DataUpdateCoordinator for VVS Departures."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import VVSApiClient
from .const import (
    CONF_DEPARTURE_COUNT,
    CONF_LINE_FILTER,
    CONF_STOP_ID,
    DEFAULT_DEPARTURE_COUNT,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class VVSDeparturesCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator that fetches departures + disruptions in a single API call."""

    def __init__(
        self,
        hass: HomeAssistant,
        session: aiohttp.ClientSession,
        config_entry_data: dict,
        update_interval: int = DEFAULT_UPDATE_INTERVAL,
    ) -> None:
        self._client = VVSApiClient(session)
        self._stop_id: str = config_entry_data[CONF_STOP_ID]
        self._line_filter: list[str] = config_entry_data.get(CONF_LINE_FILTER, [])
        self._departure_count: int = config_entry_data.get(
            CONF_DEPARTURE_COUNT, DEFAULT_DEPARTURE_COUNT
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
            # Request more than needed so filtering still yields enough results
            fetch_limit = max(self._departure_count * 3, 15)
            result = await self._client.get_departures(
                stop_id=self._stop_id,
                limit=fetch_limit,
                line_filter=self._line_filter if self._line_filter else None,
            )
        except Exception as exc:
            raise UpdateFailed(f"Error communicating with EFA API: {exc}") from exc

        return result
