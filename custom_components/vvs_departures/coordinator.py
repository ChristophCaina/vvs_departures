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
    CONF_DISRUPTION_PRIORITIES,
    CONF_DISRUPTION_TYPES,
    CONF_LINE_FILTER,
    CONF_STOP_ID,
    DEFAULT_DEPARTURE_COUNT,
    DEFAULT_DISRUPTION_PRIORITIES,
    DEFAULT_DISRUPTION_TYPES,
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
            # When a line filter is active, fetch many more raw departures so
            # that after filtering enough remain to fill all slots.
            # At a busy hub like Stuttgart Hbf, 15 raw departures may all be
            # other lines; 60 gives a comfortable margin.
            if self._line_filter:
                fetch_limit = max(self._departure_count * 10, 60)
            else:
                fetch_limit = max(self._departure_count * 3, 15)
            result = await self._client.get_departures(
                stop_id=self._stop_id,
                limit=fetch_limit,
                line_filter=self._line_filter if self._line_filter else None,
                priority_filter=self._priority_filter if self._priority_filter else None,
                type_filter=self._type_filter if self._type_filter else None,
            )
        except Exception as exc:
            raise UpdateFailed(f"Error communicating with EFA API: {exc}") from exc

        return result
