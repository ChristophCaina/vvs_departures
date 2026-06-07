"""Config flow for VVS/EFA Departures integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import EFAApiClient
from .const import (
    CONF_CITY_NAME,
    CONF_DEPARTURE_COUNT,
    CONF_DISRUPTION_PRIORITIES,
    CONF_DISRUPTION_TYPES,
    CONF_EFA_BASE_URL,
    CONF_LINE_FILTER,
    CONF_REGION_NAME,
    CONF_STOP_ID,
    CONF_STOP_NAME,
    CONF_UPDATE_INTERVAL,
    CUSTOM_PROVIDER_KEY,
    DEFAULT_DEPARTURE_COUNT,
    DEFAULT_DISRUPTION_PRIORITIES,
    DEFAULT_DISRUPTION_TYPES,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    EFA_REGIONS,
)

_LOGGER = logging.getLogger(__name__)

ALL_LINES_KEY = "__all__"


def _region_selector() -> SelectSelector:
    options = [
        SelectOptionDict(
            value=url if url != CUSTOM_PROVIDER_KEY else CUSTOM_PROVIDER_KEY,
            label=name,
        )
        for name, url in EFA_REGIONS.items()
    ]
    return SelectSelector(
        SelectSelectorConfig(options=options, multiple=False, mode=SelectSelectorMode.LIST)
    )


def _stop_select_selector(stops: list[dict]) -> SelectSelector:
    options = [SelectOptionDict(value=s["id"], label=s["name"]) for s in stops]
    return SelectSelector(
        SelectSelectorConfig(options=options, multiple=False, mode=SelectSelectorMode.LIST)
    )


def _line_select_selector(lines: list[dict], include_all: bool = True) -> SelectSelector:
    options: list[SelectOptionDict] = []
    if include_all:
        options.append(SelectOptionDict(value=ALL_LINES_KEY, label="Alle Linien (kein Filter)"))
    for line in lines:
        options.append(SelectOptionDict(value=line["global_id"], label=line["label"]))
    return SelectSelector(
        SelectSelectorConfig(options=options, multiple=True, mode=SelectSelectorMode.LIST)
    )


class VVSDeparturesConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the config flow for VVS/EFA Departures."""

    VERSION = 3

    def __init__(self) -> None:
        self._region_name: str = ""
        self._efa_base_url: str = ""
        self._city_name: str = ""
        self._found_stops: list[dict] = []
        self._selected_stop: dict = {}
        self._available_lines: list[dict] = []

    # ── Step 1: Region ───────────────────────────────────────────────────────
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            selected_url = user_input.get("efa_provider", "")
            if selected_url == CUSTOM_PROVIDER_KEY:
                return await self.async_step_custom_url()
            for name, url in EFA_REGIONS.items():
                if url == selected_url:
                    self._region_name = name
                    self._efa_base_url = url
                    break
            if not self._efa_base_url or self._efa_base_url == CUSTOM_PROVIDER_KEY:
                errors["efa_provider"] = "invalid_region"
            else:
                return await self.async_step_enter_city()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required("efa_provider"): _region_selector()}),
            errors=errors,
        )

    # ── Step 1b: Custom URL ──────────────────────────────────────────────────
    async def async_step_custom_url(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input.get("custom_url", "").strip().rstrip("/")
            if not url.startswith("http"):
                errors["custom_url"] = "invalid_url"
            else:
                self._efa_base_url = url
                self._region_name = "Custom"
                return await self.async_step_enter_city()

        return self.async_show_form(
            step_id="custom_url",
            data_schema=vol.Schema(
                {vol.Required("custom_url"): TextSelector(TextSelectorConfig(type=TextSelectorType.URL))}
            ),
            errors=errors,
        )

    # ── Step 2: City / place (free text) ────────────────────────────────────
    async def async_step_enter_city(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 2: Enter city name. Used as place_sf in stop search."""
        errors: dict[str, str] = {}

        if user_input is not None:
            city = user_input.get("city_name", "").strip()
            if not city:
                errors["city_name"] = "empty_query"
            else:
                self._city_name = city
                return await self.async_step_search_stop()

        return self.async_show_form(
            step_id="enter_city",
            data_schema=vol.Schema({vol.Required("city_name"): str}),
            errors=errors,
            description_placeholders={"region": self._region_name},
        )

    # ── Step 3a: Stop search ─────────────────────────────────────────────────
    async def async_step_search_stop(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            query = user_input.get("stop_search", "").strip()
            if not query:
                errors["stop_search"] = "empty_query"
            else:
                session = async_get_clientsession(self.hass)
                client = EFAApiClient(session, self._efa_base_url)
                try:
                    stops = await client.search_stops(query, place=self._city_name)
                except Exception as exc:
                    _LOGGER.error("Stop search failed: %s", exc)
                    errors["base"] = "cannot_connect"
                    stops = None

                if stops is None:
                    pass
                elif not stops:
                    errors["stop_search"] = "no_results_check_city"
                else:
                    self._found_stops = stops
                    return await self.async_step_select_stop()

        return self.async_show_form(
            step_id="search_stop",
            data_schema=vol.Schema({vol.Required("stop_search"): str}),
            errors=errors,
            description_placeholders={
                "city": self._city_name,
                "region": self._region_name,
            },
        )

    # ── Step 3b: Stop selection ──────────────────────────────────────────────
    async def async_step_select_stop(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
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
                await self.async_set_unique_id(f"{self._efa_base_url}|{selected_id}")
                self._abort_if_unique_id_configured()
                session = async_get_clientsession(self.hass)
                client = EFAApiClient(session, self._efa_base_url)
                try:
                    self._available_lines = await client.get_serving_lines(selected_id)
                except Exception:
                    _LOGGER.warning("Could not load serving lines for %s", selected_id)
                    self._available_lines = []
                if self._available_lines:
                    return await self.async_step_select_lines()
                return self._create_entry(line_filter=[])

        return self.async_show_form(
            step_id="select_stop",
            data_schema=vol.Schema(
                {vol.Required("stop_id"): _stop_select_selector(self._found_stops)}
            ),
            errors=errors,
            description_placeholders={
                "count": str(len(self._found_stops)),
                "city": self._city_name,
            },
        )

    # ── Step 4: Line filter ──────────────────────────────────────────────────
    async def async_step_select_lines(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        if user_input is not None:
            selected = user_input.get("line_filter", [])
            line_filter = [] if (ALL_LINES_KEY in selected or not selected) else selected
            return self._create_entry(line_filter=line_filter)

        return self.async_show_form(
            step_id="select_lines",
            data_schema=vol.Schema(
                {
                    vol.Optional("line_filter", default=[ALL_LINES_KEY]): (
                        _line_select_selector(self._available_lines, include_all=True)
                    )
                }
            ),
            description_placeholders={"stop_name": self._selected_stop.get("name", "")},
        )

    def _create_entry(self, line_filter: list[str]) -> config_entries.FlowResult:
        stop = self._selected_stop
        title = f"{stop.get('name', stop.get('id', 'EFA Stop'))} · {self._city_name}"
        return self.async_create_entry(
            title=title,
            data={
                CONF_EFA_BASE_URL: self._efa_base_url,
                CONF_REGION_NAME: self._region_name,
                CONF_CITY_NAME: self._city_name,
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
        return VVSDeparturesOptionsFlow(config_entry)


class VVSDeparturesOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._available_lines: list[dict] = []

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        current_data = {**self.config_entry.data, **self.config_entry.options}
        current_line_filter: list[str] = current_data.get(CONF_LINE_FILTER, [])

        if not self._available_lines:
            session = async_get_clientsession(self.hass)
            client = EFAApiClient(session, current_data[CONF_EFA_BASE_URL])
            try:
                self._available_lines = await client.get_serving_lines(current_data[CONF_STOP_ID])
            except Exception as exc:
                _LOGGER.warning("Could not load lines for options flow: %s", exc)

        current_line_selection = current_line_filter if current_line_filter else [ALL_LINES_KEY]

        if user_input is not None:
            selected_lines = user_input.get("line_filter", [])
            line_filter = [] if (ALL_LINES_KEY in selected_lines or not selected_lines) else selected_lines
            return self.async_create_entry(
                title="",
                data={
                    CONF_DEPARTURE_COUNT: int(user_input[CONF_DEPARTURE_COUNT]),
                    CONF_UPDATE_INTERVAL: int(user_input[CONF_UPDATE_INTERVAL]),
                    CONF_LINE_FILTER: line_filter,
                    CONF_DISRUPTION_PRIORITIES: user_input.get(CONF_DISRUPTION_PRIORITIES, DEFAULT_DISRUPTION_PRIORITIES),
                    CONF_DISRUPTION_TYPES: user_input.get(CONF_DISRUPTION_TYPES, DEFAULT_DISRUPTION_TYPES),
                },
            )

        priority_options = [
            SelectOptionDict(value="veryHigh", label="Sehr hoch (veryHigh)"),
            SelectOptionDict(value="high",     label="Hoch (high)"),
            SelectOptionDict(value="normal",   label="Normal"),
            SelectOptionDict(value="low",      label="Niedrig (low)"),
        ]
        type_options = [
            SelectOptionDict(value="lineInfo",    label="Linieninfo (Störungen, Bauarbeiten)"),
            SelectOptionDict(value="stationInfo", label="Haltestelleninfo"),
            SelectOptionDict(value="stopInfo",    label="Stopinfo"),
            SelectOptionDict(value="network",     label="Netzweit"),
        ]

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(CONF_DEPARTURE_COUNT, default=current_data.get(CONF_DEPARTURE_COUNT, DEFAULT_DEPARTURE_COUNT)):
                    NumberSelector(NumberSelectorConfig(min=1, max=10, step=1, mode=NumberSelectorMode.BOX)),
                vol.Optional(CONF_UPDATE_INTERVAL, default=current_data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)):
                    NumberSelector(NumberSelectorConfig(min=30, max=300, step=10, mode=NumberSelectorMode.SLIDER)),
                vol.Optional("line_filter", default=current_line_selection):
                    _line_select_selector(self._available_lines, include_all=True),
                vol.Optional(CONF_DISRUPTION_PRIORITIES, default=current_data.get(CONF_DISRUPTION_PRIORITIES, DEFAULT_DISRUPTION_PRIORITIES)):
                    SelectSelector(SelectSelectorConfig(options=priority_options, multiple=True, mode=SelectSelectorMode.LIST)),
                vol.Optional(CONF_DISRUPTION_TYPES, default=current_data.get(CONF_DISRUPTION_TYPES, DEFAULT_DISRUPTION_TYPES)):
                    SelectSelector(SelectSelectorConfig(options=type_options, multiple=True, mode=SelectSelectorMode.LIST)),
            }),
        )

