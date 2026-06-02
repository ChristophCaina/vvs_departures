"""Config flow for VVS Departures integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    SelectOptionDict,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .api import VVSApiClient
from .const import (
    CONF_DEPARTURE_COUNT,
    CONF_LINE_FILTER,
    CONF_STOP_ID,
    CONF_STOP_NAME,
    CONF_UPDATE_INTERVAL,
    DEFAULT_DEPARTURE_COUNT,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

ALL_LINES_KEY = "__all__"


def _line_select_selector(lines: list[dict], include_all: bool = True) -> SelectSelector:
    """Build a SelectSelector for line multi-select."""
    options: list[SelectOptionDict] = []
    if include_all:
        options.append(SelectOptionDict(value=ALL_LINES_KEY, label="Alle Linien (kein Filter)"))
    for line in lines:
        options.append(SelectOptionDict(value=line["global_id"], label=line["label"]))
    return SelectSelector(
        SelectSelectorConfig(
            options=options,
            multiple=True,
            mode=SelectSelectorMode.LIST,
        )
    )


def _stop_select_selector(stops: list[dict]) -> SelectSelector:
    """Build a SelectSelector for stop single-select."""
    options = [SelectOptionDict(value=s["id"], label=s["name"]) for s in stops]
    return SelectSelector(
        SelectSelectorConfig(
            options=options,
            multiple=False,
            mode=SelectSelectorMode.LIST,
        )
    )


class VVSDeparturesConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the config flow for VVS Departures."""

    VERSION = 1

    def __init__(self) -> None:
        self._found_stops: list[dict] = []
        self._selected_stop: dict = {}
        self._available_lines: list[dict] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 1: Search for a stop by name."""
        errors: dict[str, str] = {}

        if user_input is not None:
            query = user_input.get("stop_search", "").strip()
            if not query:
                errors["stop_search"] = "empty_query"
            else:
                session = async_get_clientsession(self.hass)
                client = VVSApiClient(session)
                try:
                    stops = await client.search_stops(query)
                except Exception:
                    errors["base"] = "cannot_connect"
                    stops = []

                if not stops:
                    errors["stop_search"] = "no_results"
                else:
                    self._found_stops = stops
                    return await self.async_step_select_stop()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required("stop_search"): str}),
            errors=errors,
        )

    async def async_step_select_stop(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 2: Select stop from search results."""
        errors: dict[str, str] = {}

        if user_input is not None:
            selected_id = user_input.get("stop_id")
            for stop in self._found_stops:
                if stop["id"] == selected_id:
                    self._selected_stop = stop
                    break

            if not self._selected_stop:
                errors["stop_id"] = "invalid_stop"
            else:
                await self.async_set_unique_id(selected_id)
                self._abort_if_unique_id_configured()

                session = async_get_clientsession(self.hass)
                client = VVSApiClient(session)
                try:
                    self._available_lines = await client.get_serving_lines(selected_id)
                except Exception:
                    _LOGGER.warning("Could not load serving lines for %s", selected_id)
                    self._available_lines = []

                if self._available_lines:
                    return await self.async_step_select_lines()
                else:
                    return self._create_entry(line_filter=[])

        return self.async_show_form(
            step_id="select_stop",
            data_schema=vol.Schema(
                {vol.Required("stop_id"): _stop_select_selector(self._found_stops)}
            ),
            errors=errors,
            description_placeholders={"count": str(len(self._found_stops))},
        )

    async def async_step_select_lines(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 3: Select which lines to monitor (optional filter)."""
        if user_input is not None:
            selected = user_input.get("line_filter", [])
            if ALL_LINES_KEY in selected or not selected:
                line_filter = []
            else:
                line_filter = selected
            return self._create_entry(line_filter=line_filter)

        return self.async_show_form(
            step_id="select_lines",
            data_schema=vol.Schema(
                {
                    vol.Optional("line_filter", default=[ALL_LINES_KEY]): (
                        _line_select_selector(self._available_lines, include_all=True)
                    ),
                }
            ),
            description_placeholders={
                "stop_name": self._selected_stop.get("name", ""),
            },
        )

    def _create_entry(self, line_filter: list[str]) -> config_entries.FlowResult:
        """Create the config entry with collected data."""
        stop = self._selected_stop
        return self.async_create_entry(
            title=stop.get("name", stop.get("id", "VVS Stop")),
            data={
                CONF_STOP_ID: stop["id"],
                CONF_STOP_NAME: stop.get("raw_name", stop.get("name", "")),
                CONF_LINE_FILTER: line_filter,
                CONF_DEPARTURE_COUNT: DEFAULT_DEPARTURE_COUNT,
                CONF_UPDATE_INTERVAL: DEFAULT_UPDATE_INTERVAL,
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> VVSDeparturesOptionsFlow:
        """Return the options flow."""
        return VVSDeparturesOptionsFlow(config_entry)


class VVSDeparturesOptionsFlow(config_entries.OptionsFlow):
    """Handle options for an existing VVS Departures entry."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        # Store entry_id only; access full entry via self.config_entry (HA injects it)
        self._entry_id = config_entry.entry_id
        self._available_lines: list[dict] = []

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Manage options: departure count, update interval, line filter."""
        # Merge data + options so we always have all keys
        current_data = {**self.config_entry.data, **self.config_entry.options}
        stop_id: str = current_data[CONF_STOP_ID]
        current_line_filter: list[str] = current_data.get(CONF_LINE_FILTER, [])

        # Load available lines on first call
        if not self._available_lines:
            session = async_get_clientsession(self.hass)
            client = VVSApiClient(session)
            try:
                self._available_lines = await client.get_serving_lines(stop_id)
            except Exception:
                _LOGGER.warning("Could not reload serving lines for %s", stop_id)
                self._available_lines = []

        current_line_selection = current_line_filter if current_line_filter else [ALL_LINES_KEY]

        if user_input is not None:
            selected_lines = user_input.get("line_filter", [])
            if ALL_LINES_KEY in selected_lines or not selected_lines:
                line_filter = []
            else:
                line_filter = selected_lines

            return self.async_create_entry(
                title="",
                data={
                    CONF_DEPARTURE_COUNT: int(user_input[CONF_DEPARTURE_COUNT]),
                    CONF_UPDATE_INTERVAL: int(user_input[CONF_UPDATE_INTERVAL]),
                    CONF_LINE_FILTER: line_filter,
                },
            )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_DEPARTURE_COUNT,
                        default=current_data.get(CONF_DEPARTURE_COUNT, DEFAULT_DEPARTURE_COUNT),
                    ): NumberSelector(
                        NumberSelectorConfig(min=1, max=10, step=1, mode=NumberSelectorMode.BOX)
                    ),
                    vol.Optional(
                        CONF_UPDATE_INTERVAL,
                        default=current_data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
                    ): NumberSelector(
                        NumberSelectorConfig(min=30, max=300, step=10, mode=NumberSelectorMode.SLIDER)
                    ),
                    vol.Optional(
                        "line_filter",
                        default=current_line_selection,
                    ): _line_select_selector(self._available_lines, include_all=True),
                }
            ),
        )
