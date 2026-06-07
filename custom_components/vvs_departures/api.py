"""EFA API client for EFA Departures integration."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

from .const import (
    EFA_DM_PATH,
    EFA_SERVINGLINES_PATH,
    EFA_STOPFINDER_PATH,
)

_LOGGER = logging.getLogger(__name__)


def _strip_html(text: str) -> str:
    """Remove HTML tags and clean up whitespace."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _mot_from_line_full(line_full: str, line: str) -> int:
    """
    Derive EFA motType integer from the line_full string prefix.

    EFA includes the vehicle type as a human-readable prefix in line_full,
    e.g. "Bus 54", "S-Bahn S6", "Stadtbahn U2", "Schiff 3820".
    This is more reliable than reading motType from the API response,
    whose field location varies between EFA instances and versions.

    Returned integers match the EFA motType convention used in sensor.py:
      0=Zug/ICE/RE, 1=S-Bahn, 2=U-Bahn, 3=Stadtbahn, 4=Straßenbahn,
      5=Stadtbus, 6=Regionalbus, 7=Schnellbus, 8=Nachtbus,
      9=Fähre/Schiff, 10=Seilbahn, -1=unbekannt
    """
    prefix = line_full.lower().strip()

    # Ferry / ship — check before generic "bus" to catch "Der Katamaran" etc.
    if any(k in prefix for k in ("schiff", "fähre", "fahre", "katamaran", "ferry")):
        return 9

    if prefix.startswith("s-bahn") or prefix.startswith("sbahn"):
        return 1

    if prefix.startswith("u-bahn") or prefix.startswith("ubahn"):
        return 2

    # Stadtbahn covers U-lines in Stuttgart (U1–U15) and similar systems
    if prefix.startswith("stadtbahn"):
        return 3

    if prefix.startswith("straßenbahn") or prefix.startswith("strassenbahn") or prefix.startswith("tram"):
        return 4

    if prefix.startswith("nachtbus") or prefix.startswith("nacht-bus"):
        return 8

    if prefix.startswith("schnellbus") or prefix.startswith("expressbus"):
        return 7

    if prefix.startswith("bus"):
        # Distinguish city vs regional by line number pattern:
        # numeric-only or short codes → city bus; longer codes → regional
        line_stripped = line.strip()
        if line_stripped.isdigit() and int(line_stripped) < 200:
            return 5  # Stadtbus
        return 6  # Regionalbus

    if any(k in prefix for k in ("zug", "ice", "ic ", " ic", "ec ", " ec", "rb", "re ", " re", "mex", "irre")):
        return 0

    if prefix.startswith("seilbahn") or prefix.startswith("luftseil"):
        return 10

    # Fallback: try to infer from line name alone
    line_lower = line.lower()
    if line_lower.startswith("s") and line[1:].isdigit():
        return 1  # S1, S6 etc.
    if line_lower.startswith("u") and line[1:].isdigit():
        return 2  # U1, U2 etc.

    return -1  # unknown → sensor.py uses default icon


def _parse_departure(event: dict) -> dict | None:
    """Extract the minimal fields needed from a stopEvent."""
    try:
        transport = event.get("transportation", {})
        destination = transport.get("destination", {}).get("name", "?")
        line = transport.get("disassembledName", transport.get("number", "?"))
        line_full = transport.get("name", line)
        global_id = transport.get("globalId", "")

        planned_str = event.get("departureTimePlanned")
        estimated_str = event.get("departureTimeEstimated") or planned_str

        planned_dt = None
        estimated_dt = None
        delay_minutes = 0

        if planned_str:
            planned_dt = datetime.fromisoformat(planned_str.replace("Z", "+00:00"))
        if estimated_str:
            estimated_dt = datetime.fromisoformat(estimated_str.replace("Z", "+00:00"))

        if planned_dt and estimated_dt:
            delta = estimated_dt - planned_dt
            delay_minutes = int(delta.total_seconds() // 60)

        platform = (
            event.get("location", {})
            .get("properties", {})
            .get("platformName", "")
        )

        realtime = "MONITORED" in event.get("realtimeStatus", [])

        # Derive mode of transport from line_full prefix — more reliable than
        # reading motType from the API response (field location varies by EFA instance).
        # line_full examples: "Bus 54", "Stadtbahn U2", "S-Bahn S6",
        #                     "Schiff 3820", "Der Katamaran", "Nachtbus N1",
        #                     "Straßenbahn 1", "U-Bahn U7", "Zug RB76"
        mot_type = _mot_from_line_full(line_full, line)

        notices = []
        for info in event.get("infos", []):
            links = info.get("infoLinks", [])
            if not links:
                continue
            link = links[0]
            title = link.get("subtitle") or link.get("urlText", "")
            html_text = link.get("htmlText") or link.get("content", "")
            plain_text = _strip_html(html_text)
            notices.append(
                {
                    "title": title[:200] if title else "",
                    "text": plain_text[:500] if plain_text else "",
                    "priority": info.get("priority", "normal"),
                    "type": info.get("type", "lineInfo"),
                }
            )

        return {
            "line": line,
            "line_full": line_full,
            "global_id": global_id,
            "destination": destination,
            "planned": planned_str,
            "estimated": estimated_str,
            "delay_minutes": delay_minutes,
            "platform": platform,
            "realtime": realtime,
            "mot_type": mot_type,
            "notices": notices,
        }
    except Exception as exc:
        _LOGGER.debug("Failed to parse departure event: %s", exc)
        return None


def _parse_disruptions(
    events: list[dict],
    line_filter: list[str],
    priority_filter: list[str] | None = None,
    type_filter: list[str] | None = None,
) -> list[dict]:
    """Extract unique disruption messages from stopEvents."""
    seen_ids: set[str] = set()
    disruptions: list[dict] = []

    for event in events:
        transport = event.get("transportation", {})
        line_global_id = transport.get("globalId", "")
        line_name_fb = transport.get("disassembledName") or transport.get("number", "")
        line_filter_key = line_global_id if line_global_id else line_name_fb

        if line_filter and line_filter_key not in line_filter:
            continue

        line_name = transport.get("disassembledName", transport.get("number", "?"))

        for info in event.get("infos", []):
            info_id = info.get("id", "")
            if info_id in seen_ids:
                continue
            seen_ids.add(info_id)

            priority = info.get("priority", "normal")
            info_type = info.get("type", "lineInfo")

            if priority_filter and priority not in priority_filter:
                continue
            if type_filter and info_type not in type_filter:
                continue

            links = info.get("infoLinks", [])
            if not links:
                continue

            link = links[0]
            title = link.get("subtitle") or link.get("urlText", "")
            html_text = link.get("htmlText") or link.get("content", "")
            plain_text = _strip_html(html_text)
            created_str = info.get("timestamps", {}).get("creation")

            disruptions.append(
                {
                    "id": info_id,
                    "title": title[:200] if title else "",
                    "text": plain_text[:500] if plain_text else "",
                    "priority": priority,
                    "type": info_type,
                    "line": line_name,
                    "created": created_str,
                }
            )

    priority_order = {"veryHigh": 0, "high": 1, "normal": 2, "low": 3}
    disruptions.sort(
        key=lambda d: (
            priority_order.get(d["priority"], 9),
            -(
                datetime.fromisoformat(
                    d["created"].replace("Z", "+00:00")
                ).timestamp()
                if d["created"]
                else 0
            ),
        )
    )
    return disruptions


def _sort_lines(lines: list[dict]) -> list[dict]:
    """Sort lines: S-Bahn, U/Stadtbahn, MEX/RE/RB/IC, then Bus."""
    def sort_key(line: dict) -> tuple:
        n = line["name"]
        if n.startswith("S") and (len(n) <= 3 or n[1:].isdigit()):
            return (0, n.zfill(5))
        if n.startswith("U"):
            return (1, n.zfill(5))
        if any(n.startswith(p) for p in ("MEX", "RE", "RB", "IC", "EC")):
            return (2, n)
        return (3, n.zfill(5))

    lines.sort(key=sort_key)
    return lines


# Common ÖPNV abbreviations → expanded form used for matching
_STOP_ABBREVIATIONS: dict[str, str] = {
    "hbf": "hauptbahnhof",
    "bf":  "bahnhof",
    "str": "straße",
    "pl":  "platz",
    "br":  "brücke",
    "kr":  "kreis",
}

_SPLIT_RE = re.compile(r"[\s\-,()]+")


def _expand_query_words(query: str) -> tuple[str, list[str]]:
    """Normalise query and expand known abbreviations.

    Returns (normalised_query, list_of_match_words).
    E.g. "Karlsruhe Hbf" → ("karlsruhe hbf", ["karlsruhe", "hbf", "hauptbahnhof"])
    """
    norm = query.lower().strip()
    raw_words = [w for w in _SPLIT_RE.split(norm) if len(w) >= 2]
    expanded: list[str] = []
    for w in raw_words:
        expanded.append(w)
        if w in _STOP_ABBREVIATIONS:
            expanded.append(_STOP_ABBREVIATIONS[w])
    return norm, [w for w in expanded if len(w) >= 3]


def _filter_and_sort_stops(stops: list[dict], query: str) -> list[dict]:
    """
    Filter and sort stop results by relevance to the search query.

    EFA's Stopfinder returns broad fuzzy matches — e.g. searching "Renningen"
    may return "Gönningen", "Brenningen" or "Canach" due to phonetic/substring
    similarity.  We use word-boundary matching to keep only meaningful results,
    and expand common abbreviations (Hbf → Hauptbahnhof) before matching.

    Scoring (lower = better):
      0  exact match on primary stop name
      1  primary name starts with the full query string
      2  all query words appear as word-starts in the primary name
      3  any query word matches the parent location (e.g. Malmsheim (Renningen))
      99 no meaningful match → dropped
    """
    query_norm, query_words = _expand_query_words(query)

    def _words_match_all(text: str) -> bool:
        """All query words must match the start of at least one word in text."""
        text_words = _SPLIT_RE.split(text.lower())
        return all(any(tw.startswith(qw) for tw in text_words) for qw in query_words)

    def _words_match_any(text: str) -> bool:
        """Any query word matches the start of at least one word in text."""
        text_words = _SPLIT_RE.split(text.lower())
        return any(any(tw.startswith(qw) for tw in text_words) for qw in query_words)

    def _parent(display: str) -> str:
        """Extract the parent location name from 'Stop Name (Parent)'."""
        if "(" in display and display.endswith(")"):
            return display[display.rfind("(") + 1:-1]
        return ""

    def _score(stop: dict) -> int:
        primary = stop["raw_name"].lower()
        display = stop["name"].lower()

        if primary == query_norm:
            return 0
        if primary.startswith(query_norm):
            return 1
        if query_words and _words_match_all(primary):
            return 2
        parent = _parent(display)
        if parent and query_words and _words_match_any(parent):
            return 3
        return 99

    scored = [(_score(s), s) for s in stops]
    filtered = [(sc, s) for sc, s in scored if sc < 99]
    filtered.sort(key=lambda x: x[0])
    result = [s for _, s in filtered]

    # Safety fallback: if everything was filtered out return original list
    # (prevents blank results for very unusual stop names)
    if not result and stops:
        _LOGGER.debug(
            "Stop filter removed all %d results for %r — returning unfiltered",
            len(stops), query,
        )
        return stops

    _LOGGER.debug("Stop search %r: %d raw → %d after filter", query, len(stops), len(result))
    return result


class EFAApiClient:
    """Async API client for EFA providers."""

    def __init__(self, session: aiohttp.ClientSession, base_url: str) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")

    @property
    def stopfinder_url(self) -> str:
        return f"{self._base_url}{EFA_STOPFINDER_PATH}"

    @property
    def dm_url(self) -> str:
        return f"{self._base_url}{EFA_DM_PATH}"

    @property
    def servinglines_url(self) -> str:
        return f"{self._base_url}{EFA_SERVINGLINES_PATH}"

    async def validate_place(self, place: str) -> bool:
        """
        Validate that a place name works as place_sf by doing a test stop search.
        Returns True if EFA returns at least one result with this place name.
        """
        params = {
            "language": "de",
            "outputFormat": "rapidJSON",
            "type_sf": "any",
            "name_sf": "Bahnhof",   # generic term — almost every town has one
            "locationServerActive": "1",
            "anyObjFilter_sf": "2",
            "place_sf": place,
        }
        try:
            async with self._session.get(
                self.stopfinder_url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
                return len(data.get("locations", [])) > 0
        except Exception as exc:
            _LOGGER.debug("Place validation failed for %r: %s", place, exc)
            return False

    async def search_stops(self, query: str, place: str = "") -> list[dict]:
        """Search for stops by name, optionally restricted to a city/place.

        Strategy:
        1. Try name_sf=query + place_sf=place (exact city filter)
        2. If no results: retry with name_sf="place query" without place_sf
           This handles cases where EFA stores the city under a different name
           (e.g. user enters "Freiburg" but EFA knows "Freiburg im Breisgau")
        3. If place_sf causes a connection error: retry without it
        """
        async def _fetch(params: dict) -> dict | None:
            try:
                async with self._session.get(
                    self.stopfinder_url,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    resp.raise_for_status()
                    return await resp.json(content_type=None)
            except Exception as exc:
                _LOGGER.debug("Stop search request failed: %s", exc)
                return None

        def _parse_locations(data: dict) -> list[dict]:
            results = []
            for loc in data.get("locations", []):
                if loc.get("type") not in ("stop", "platform"):
                    continue
                stop_id = loc.get("id", "")
                name = loc.get("name", "")
                parent_name = loc.get("parent", {}).get("name", "")
                display = (
                    f"{name} ({parent_name})"
                    if parent_name and parent_name != name
                    else name
                )
                results.append({"id": stop_id, "name": display, "raw_name": name})
            return results

        base_params = {
            "language": "de",
            "outputFormat": "rapidJSON",
            "type_sf": "any",
            "locationServerActive": "1",
            "anyObjFilter_sf": "2",
        }

        # Attempt 1: query + place_sf
        if place:
            params1 = {**base_params, "name_sf": query, "place_sf": place}
            data = await _fetch(params1)
            if data is not None:
                results = _parse_locations(data)
                if results:
                    filtered = _filter_and_sort_stops(results, query)
                    _LOGGER.debug("Stop search (place_sf): %d raw → %d filtered", len(results), len(filtered))
                    return filtered
                _LOGGER.debug("place_sf=%r yielded no results, trying combined query", place)

                # Attempt 2: combined "place query" without place_sf
                combined = f"{place} {query}"
                params2 = {**base_params, "name_sf": combined}
                data2 = await _fetch(params2)
                if data2 is not None:
                    results2 = _parse_locations(data2)
                    if results2:
                        # Filter by original query words + place words
                        filtered2 = _filter_and_sort_stops(results2, query)
                        # additionally keep results whose display name contains place hint
                        place_lower = place.lower()
                        extra = [
                            r for r in filtered2
                            if place_lower in r["name"].lower() or place_lower in r["raw_name"].lower()
                        ]
                        final = extra if extra else filtered2
                        _LOGGER.debug("Stop search (combined %r): %d raw → %d", combined, len(results2), len(final))
                        return final
            else:
                # Connection error on attempt 1 → try without place_sf
                _LOGGER.debug("place_sf request failed, retrying without")

        # Attempt 3: plain query without place_sf (no place given, or all else failed)
        params3 = {**base_params, "name_sf": query}
        data3 = await _fetch(params3)
        if data3 is None:
            raise ConnectionError("EFA stop search failed")
        results3 = _parse_locations(data3)
        filtered3 = _filter_and_sort_stops(results3, query)
        _LOGGER.debug("Stop search (no place): %d raw → %d filtered", len(results3), len(filtered3))
        return filtered3

    async def get_serving_lines(self, stop_id: str) -> list[dict]:
        """
        Fetch all lines serving a stop via XML_SERVINGLINES_REQUEST.

        This is the authoritative source — no time-window guessing needed.
        Falls back to the DM-based approach if SERVINGLINES returns nothing
        (some older EFA instances don't support it fully).
        """
        lines = await self._serving_lines_via_api(stop_id)
        if lines:
            return lines
        _LOGGER.debug(
            "SERVINGLINES returned nothing for %s, falling back to DM sampling", stop_id
        )
        return await self._serving_lines_via_dm(stop_id)

    async def _serving_lines_via_api(self, stop_id: str) -> list[dict]:
        """Use XML_SERVINGLINES_REQUEST to get all lines at a stop."""
        params = {
            "language": "de",
            "outputFormat": "rapidJSON",
            "type_sl": "stopID",
            "name_sl": stop_id,
            "mode": "odv",
        }
        try:
            async with self._session.get(
                self.servinglines_url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        except Exception as exc:
            _LOGGER.debug("SERVINGLINES request failed: %s", exc)
            return []

        seen: set[str] = set()
        lines: list[dict] = []

        # rapidJSON SERVINGLINES response: list under "lines" or "transportation"
        raw_lines = data.get("lines", [])

        for entry in raw_lines:
            # Each entry has a "transportation" sub-object (same schema as DM)
            transport = entry.get("transportation") or entry
            name = transport.get("disassembledName") or transport.get("number", "")
            global_id = transport.get("globalId", "")
            line_full = transport.get("name", name)

            if not name:
                continue

            dedup_key = global_id if global_id else name
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            destination = transport.get("destination", {}).get("name", "")
            type_part = line_full.replace(name, "").strip() if line_full != name else ""
            label = f"{name} ({type_part})" if type_part else name

            lines.append(
                {
                    "global_id": dedup_key,
                    "name": name,
                    "line_full": line_full,
                    "destination": destination,
                    "label": label,
                }
            )

        return _sort_lines(lines)

    async def _serving_lines_via_dm(self, stop_id: str) -> list[dict]:
        """
        Fallback: derive serving lines from DM departure snapshots.
        Fetches departures at 0h, +2h, +4h, +6h offsets and deduplicates.
        """
        import asyncio as _asyncio

        offsets_minutes = [0, 120, 240, 360]

        async def _fetch_at_offset(offset_min: int) -> list[dict]:
            params = {
                "language": "de",
                "outputFormat": "rapidJSON",
                "typeInfo_dm": "stopID",
                "nameInfo_dm": stop_id,
                "deleteAssignedStopps_dm": "1",
                "useRealtime": "0",
                "mode": "direct",
                "limit": "40",
                "version": "10.5.17.3",
            }
            if offset_min > 0:
                target = datetime.now(tz=timezone.utc) + timedelta(minutes=offset_min)
                params["itdDate"] = target.strftime("%Y%m%d")
                params["itdTime"] = target.strftime("%H%M")
            try:
                async with self._session.get(
                    self.dm_url,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    resp.raise_for_status()
                    data = await resp.json(content_type=None)
                    return [
                        dep
                        for e in data.get("stopEvents", [])
                        if (dep := _parse_departure(e)) is not None
                    ]
            except Exception as exc:
                _LOGGER.debug("DM fallback fetch at +%dmin failed: %s", offset_min, exc)
                return []

        results = await _asyncio.gather(*[_fetch_at_offset(o) for o in offsets_minutes])

        seen: set[str] = set()
        lines: list[dict] = []
        for batch in results:
            for dep in batch:
                name = dep.get("line", "")
                global_id = dep.get("global_id", "")
                dedup_key = global_id if global_id else name
                if not dedup_key or dedup_key in seen:
                    continue
                seen.add(dedup_key)
                line_full = dep.get("line_full", name)
                type_part = line_full.replace(name, "").strip() if line_full != name else ""
                label = f"{name} ({type_part})" if type_part else name
                lines.append(
                    {
                        "global_id": dedup_key,
                        "name": name,
                        "line_full": line_full,
                        "destination": dep.get("destination", ""),
                        "label": label,
                    }
                )

        return _sort_lines(lines)

    async def get_departures(
        self,
        stop_id: str,
        limit: int = 10,
        line_filter: list[str] | None = None,
        priority_filter: list[str] | None = None,
        type_filter: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Fetch departures for a stop.
        Returns {departures: [...], disruptions: [...]}.
        """
        params = {
            "language": "de",
            "outputFormat": "rapidJSON",
            "typeInfo_dm": "stopID",
            "nameInfo_dm": stop_id,
            "deleteAssignedStopps_dm": "1",
            "useRealtime": "1",
            "mode": "direct",
            "limit": str(limit),
            "version": "10.5.17.3",
        }
        try:
            async with self._session.get(
                self.dm_url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        except Exception as exc:
            _LOGGER.error("Departure fetch failed for stop %s: %s", stop_id, exc)
            raise

        raw_events = data.get("stopEvents", [])
        active_filter = line_filter or []

        departures = []
        for event in raw_events:
            transport = event.get("transportation", {})
            gid = transport.get("globalId", "")
            line_name = transport.get("disassembledName") or transport.get("number", "")
            filter_key = gid if gid else line_name
            if active_filter and filter_key not in active_filter:
                continue
            parsed = _parse_departure(event)
            if parsed:
                departures.append(parsed)

        disruptions = _parse_disruptions(
            raw_events, active_filter, priority_filter, type_filter
        )

        return {"departures": departures, "disruptions": disruptions}
