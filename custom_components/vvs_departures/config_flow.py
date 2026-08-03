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
    ALL_LINES_SENTINEL_KEY,
    CONF_CITY_NAME,
    CONF_DISRUPTION_PRIORITIES,
    CONF_DISRUPTION_TYPES,
    CONF_EFA_BASE_URL,
    CONF_LINE_DIRECTIONS,
    CONF_REGION_NAME,
    CONF_STOP_ID,
    CONF_STOP_NAME,
    CONF_UPDATE_INTERVAL,
    CUSTOM_PROVIDER_KEY,
    DEFAULT_DIRECTION_COUNT,
    DEFAULT_DISRUPTION_PRIORITIES,
    DEFAULT_DISRUPTION_TYPES,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    EFA_REGIONS,
)

_LOGGER = logging.getLogger(__name__)

ALL_LINES_KEY = "__all__"  # selector value for the legacy "alle Linien" sentinel


def _encode_line_value(global_id: str, destination: str) -> str:
    """Encode a (line, direction) pair as a single selector value."""
    return f"{global_id}|{destination}"


def _decode_line_value(value: str) -> tuple[str, str]:
    """Decode a selector value back into (global_id, destination)."""
    global_id, _, destination = value.partition("|")
    return global_id, destination


def _build_direction_entries(
    selected_values: list[str],
    available_lines: list[dict],
    fallback_entries: list[dict] | None = None,
) -> list[dict]:
    """
    Turn selected selector values into CONF_LINE_DIRECTIONS entries (without counts).

    fallback_entries (typically the entries already stored in the config
    entry) are used when a selected value isn't present in the freshly
    fetched available_lines — e.g. a direction that was confirmed on a
    previous options-flow run (possibly via an inferred alias) but simply
    wasn't observed in *this* run's live sampling window. Without this, a
    perfectly valid existing selection would silently be dropped just
    because it didn't happen to show up again right now.
    """
    fallback_by_value: dict[str, dict] = {}
    for e in fallback_entries or []:
        val = ALL_LINES_KEY if e["line_global_id"] is None else e["key"]
        fallback_by_value[val] = e

    by_value = {
        _encode_line_value(line["global_id"], line.get("destination", "")): line
        for line in available_lines
    }
    entries: list[dict] = []
    for value in selected_values:
        if value == ALL_LINES_KEY:
            entries.append(
                {
                    "key": ALL_LINES_SENTINEL_KEY,
                    "line_global_id": None,
                    "line_name": "Alle Linien (kein Filter)",
                    "destination": None,
                }
            )
            continue
        line = by_value.get(value)
        if line is not None:
            destination = line.get("destination") or None
            entry: dict[str, Any] = {
                "key": value,
                "line_global_id": line["global_id"],
                "line_name": line["name"],
                "destination": destination,
            }
            aliases = line.get("destination_aliases")
            if aliases:
                entry["destination_aliases"] = aliases
            entries.append(entry)
            continue
        fallback = fallback_by_value.get(value)
        if fallback is not None:
            entries.append(dict(fallback))
            continue
        # Genuinely unknown value with no fallback — nothing sensible to
        # store, skip it.
        _LOGGER.warning("EFA Departures: unbekannter Auswahlwert '%s' übersprungen", value)
    return entries


def _counts_schema(entries: list[dict], current_counts: dict[str, int] | None = None) -> vol.Schema:
    """Build a dynamic schema with one NumberSelector per direction entry."""
    current_counts = current_counts or {}
    field_names = _count_field_names(entries)
    schema_dict: dict[Any, Any] = {}
    for e, field_name in zip(entries, field_names):
        default = current_counts.get(e["key"], DEFAULT_DIRECTION_COUNT)
        schema_dict[vol.Required(field_name, default=default)] = NumberSelector(
            NumberSelectorConfig(min=1, max=10, step=1, mode=NumberSelectorMode.BOX)
        )
    return vol.Schema(schema_dict)


def _apply_counts(entries: list[dict], user_input: dict[str, Any]) -> list[dict]:
    """Read the submitted counts form and attach 'count' to each entry."""
    field_names = _count_field_names(entries)
    result = []
    for e, field_name in zip(entries, field_names):
        count = int(user_input.get(field_name, DEFAULT_DIRECTION_COUNT))
        result.append({**e, "count": count})
    return result


def _entry_label(e: dict) -> str:
    if e["line_global_id"] is None:
        return e["line_name"]
    if e.get("destination"):
        return f"{e['line_name']} → {e['destination']}"
    return f"{e['line_name']} (beide Richtungen)"


def _count_field_names(entries: list[dict]) -> list[str]:
    """
    Human-readable, unique field names for the counts form — used directly
    as the visible label, since dynamic per-selection fields can't be
    pre-translated in strings.json/translations (their text only exists at
    runtime, once the user has picked specific lines/directions).
    """
    used: dict[str, int] = {}
    names: list[str] = []
    for e in entries:
        base = f"{_entry_label(e)} · Anzahl Abfahrten"
        n = used.get(base, 0)
        used[base] = n + 1
        names.append(base if n == 0 else f"{base} ({n + 1})")
    return names


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


def _line_select_selector(
    lines: list[dict],
    include_all: bool = True,
    extra_options: list[SelectOptionDict] | None = None,
) -> SelectSelector:
    """One selectable option per (Linie, Richtung) — not just per Linie.

    extra_options lets the caller keep a previously-selected value that
    isn't part of the freshly fetched `lines` as a valid, visible option —
    HA's SelectSelector otherwise rejects a default that isn't among its
    options, which would crash the options flow for any selection not
    re-observed in the latest live sample.
    """
    options: list[SelectOptionDict] = []
    seen_values: set[str] = set()
    if include_all:
        options.append(
            SelectOptionDict(value=ALL_LINES_KEY, label="Alle Linien (kein Filter, altes Verhalten)")
        )
        seen_values.add(ALL_LINES_KEY)
    for line in lines:
        value = _encode_line_value(line["global_id"], line.get("destination", ""))
        if value in seen_values:
            continue
        seen_values.add(value)
        options.append(SelectOptionDict(value=value, label=line["label"]))
    for extra in extra_options or []:
        if extra["value"] in seen_values:
            continue
        seen_values.add(extra["value"])
        options.append(extra)
    return SelectSelector(
        SelectSelectorConfig(options=options, multiple=True, mode=SelectSelectorMode.LIST)
    )


class VVSDeparturesConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the config flow for VVS/EFA Departures."""

    VERSION = 4

    def __init__(self) -> None:
        self._region_name: str = ""
        self._efa_base_url: str = ""
        self._city_name: str = ""
        self._found_stops: list[dict] = []
        self._selected_stop: dict = {}
        self._available_lines: list[dict] = []
        self._pending_entries: list[dict] = []

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
                # Provider doesn't support SERVINGLINES/DM sampling here — fall
                # back to a single unfiltered "alle Linien" entry.
                fallback_entry = {
                    "key": ALL_LINES_SENTINEL_KEY,
                    "line_global_id": None,
                    "line_name": "Alle Linien (kein Filter)",
                    "destination": None,
                    "count": DEFAULT_DIRECTION_COUNT * 2,
                }
                return self._create_entry(line_directions=[fallback_entry])

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

    # ── Step 4: Line + Richtung auswählen ───────────────────────────────────
    async def async_step_select_lines(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        if user_input is not None:
            selected = user_input.get("line_filter", [])
            if not selected:
                selected = [ALL_LINES_KEY]
            self._pending_entries = _build_direction_entries(selected, self._available_lines)
            return await self.async_step_set_counts()

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

    # ── Step 5: Anzahl Sensoren je Linie+Richtung ───────────────────────────
    async def async_step_set_counts(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        if user_input is not None:
            line_directions = _apply_counts(self._pending_entries, user_input)
            return self._create_entry(line_directions=line_directions)

        return self.async_show_form(
            step_id="set_counts",
            data_schema=_counts_schema(self._pending_entries),
            description_placeholders={
                "selection": ", ".join(_entry_label(e) for e in self._pending_entries)
            },
        )

    def _create_entry(self, line_directions: list[dict]) -> config_entries.FlowResult:
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
                CONF_LINE_DIRECTIONS: line_directions,
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
        self._pending_entries: list[dict] = []
        self._pending_other: dict[str, Any] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        current_data = {**self.config_entry.data, **self.config_entry.options}
        current_entries: list[dict] = current_data.get(CONF_LINE_DIRECTIONS, [])
        current_counts = {e["key"]: e.get("count", DEFAULT_DIRECTION_COUNT) for e in current_entries}

        if not self._available_lines:
            session = async_get_clientsession(self.hass)
            client = EFAApiClient(session, current_data[CONF_EFA_BASE_URL])
            try:
                self._available_lines = await client.get_serving_lines(current_data[CONF_STOP_ID])
            except Exception as exc:
                _LOGGER.warning("Could not load lines for options flow: %s", exc)

        # Preselect whatever is currently configured. Entries that reference a
        # line/direction not present in *this* run's freshly fetched lines
        # (e.g. not observed in the current live sampling window) are kept
        # selectable via extra_options below, instead of being silently
        # dropped or crashing the selector's default-value validation.
        current_selection = [
            ALL_LINES_KEY if e["line_global_id"] is None else e["key"]
            for e in current_entries
        ] or [ALL_LINES_KEY]

        fresh_values = {ALL_LINES_KEY} | {
            _encode_line_value(line["global_id"], line.get("destination", ""))
            for line in self._available_lines
        }
        stale_options = [
            SelectOptionDict(
                value=val,
                label=f"{_entry_label(e)} (aktuell nicht in Live-Daten bestätigt)",
            )
            for val, e in zip(current_selection, current_entries)
            if val not in fresh_values
        ]

        if user_input is not None:
            selected = user_input.get("line_filter", [])
            if not selected:
                selected = [ALL_LINES_KEY]
            self._pending_entries = _build_direction_entries(
                selected, self._available_lines, fallback_entries=current_entries
            )
            self._pending_other = {
                CONF_UPDATE_INTERVAL: int(user_input[CONF_UPDATE_INTERVAL]),
                CONF_DISRUPTION_PRIORITIES: user_input.get(CONF_DISRUPTION_PRIORITIES, DEFAULT_DISRUPTION_PRIORITIES),
                CONF_DISRUPTION_TYPES: user_input.get(CONF_DISRUPTION_TYPES, DEFAULT_DISRUPTION_TYPES),
            }
            return await self.async_step_set_counts()

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

        # Stash for use when pre-filling the counts step below.
        self._current_counts = current_counts

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(CONF_UPDATE_INTERVAL, default=current_data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)):
                    NumberSelector(NumberSelectorConfig(min=30, max=300, step=10, mode=NumberSelectorMode.SLIDER)),
                vol.Optional("line_filter", default=current_selection):
                    _line_select_selector(self._available_lines, include_all=True, extra_options=stale_options),
                vol.Optional(CONF_DISRUPTION_PRIORITIES, default=current_data.get(CONF_DISRUPTION_PRIORITIES, DEFAULT_DISRUPTION_PRIORITIES)):
                    SelectSelector(SelectSelectorConfig(options=priority_options, multiple=True, mode=SelectSelectorMode.LIST)),
                vol.Optional(CONF_DISRUPTION_TYPES, default=current_data.get(CONF_DISRUPTION_TYPES, DEFAULT_DISRUPTION_TYPES)):
                    SelectSelector(SelectSelectorConfig(options=type_options, multiple=True, mode=SelectSelectorMode.LIST)),
            }),
        )

    async def async_step_set_counts(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        if user_input is not None:
            line_directions = _apply_counts(self._pending_entries, user_input)
            return self.async_create_entry(
                title="",
                data={
                    **self._pending_other,
                    CONF_LINE_DIRECTIONS: line_directions,
                },
            )

        current_counts = getattr(self, "_current_counts", {})
        return self.async_show_form(
            step_id="set_counts",
            data_schema=_counts_schema(self._pending_entries, current_counts),
            description_placeholders={
                "selection": ", ".join(_entry_label(e) for e in self._pending_entries)
            },
        )

