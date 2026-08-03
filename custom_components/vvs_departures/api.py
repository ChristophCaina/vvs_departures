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


def _normalize_destination(text: str) -> str:
    """
    Normalize a destination/direction string for robust comparison.

    EFA instances can report slightly different destination text for the
    same physical direction depending on the endpoint (SERVINGLINES vs. the
    live departure monitor) — e.g. parenthetical suffixes like "(Fildertunnel)",
    a leading city name, or just different whitespace. We strip those
    variations away so a configured direction still matches the live trip.
    """
    if not text:
        return ""
    t = text.casefold().strip()
    t = re.sub(r"\([^)]*\)", " ", t)   # drop "(...)" annotations
    t = re.sub(r"[.\-–—,/]", " ", t)   # drop punctuation that varies between sources
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _destination_matches(configured: str, live: str) -> bool:
    """True if a configured direction should be considered the same as a live one."""
    if configured == live:
        return True
    norm_configured = _normalize_destination(configured)
    norm_live = _normalize_destination(live)
    if not norm_configured or not norm_live:
        return False
    if norm_configured == norm_live:
        return True
    # Substring fallback (either direction): handles cases like "Schwabstraße"
    # vs "Stuttgart Schwabstraße", or a live trip ending one stop short/long
    # of the line's official terminus (e.g. "Herrenberg" vs "Herrenberg Bahnhof").
    return norm_configured in norm_live or norm_live in norm_configured


def _destination_matches_any(configured: str, aliases: list[str], live: str) -> bool:
    """
    Like _destination_matches, but also accepts any configured alias.

    Aliases are destinations inferred by elimination when a line's official
    terminus is temporarily unreachable (e.g. a construction-related
    short-turn) — see _infer_destination_aliases below. Checking the
    canonical destination first means service returning to normal (the
    official terminus reappearing live) keeps matching without needing to
    reconfigure anything.
    """
    if _destination_matches(configured, live):
        return True
    return any(_destination_matches(alias, live) for alias in aliases)


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
        line = transport.get("disassembledName") or transport.get("number") or ""
        line_full = transport.get("name", line)
        global_id = transport.get("globalId", "")

        # If no line name: use line_full as-is (e.g. "Schiff", "Der Katamaran")
        # This handles ferry/ship entries where EFA only provides the vehicle type
        if not line:
            line = line_full if line_full else "?"

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
        Build the list of selectable (Linie, Richtung) entries for a stop.

        SERVINGLINES discovers the *complete* set of lines serving the stop
        (including rare/night lines), but its destination text is not always
        what live DM polls actually show at this specific stop — e.g. during
        construction-related short-turns, trains show an intermediate
        terminus instead of the line's official far end.

        For each line, official termini (from SERVINGLINES) are matched
        against live-observed destinations (from DM sampling). Directly
        matching pairs are used as-is. If exactly one official terminus and
        exactly one live destination remain unmatched, they're paired by
        elimination and the live text is stored as an *alias* alongside the
        official terminus — so the entry keeps working both during a
        disruption (matches the alias) and after service returns to normal
        (matches the official terminus directly), without reconfiguration.
        Anything left ambiguous (more than one unmatched on either side)
        falls back to a single unfiltered "beide Richtungen" entry for that
        line, rather than offering a direction filter that might never match.
        """
        api_lines = await self._serving_lines_via_api(stop_id)
        dm_lines = await self._serving_lines_via_dm(stop_id)

        if not api_lines:
            # No SERVINGLINES support at all — DM sampling is all we have.
            return dm_lines

        api_by_line: dict[str, list[dict]] = {}
        for line in api_lines:
            api_by_line.setdefault(line["global_id"], []).append(line)

        dm_by_line: dict[str, list[dict]] = {}
        for line in dm_lines:
            dm_by_line.setdefault(line["global_id"], []).append(line)

        result: list[dict] = []
        seen_keys: set[tuple[str, str]] = set()

        def _add(gid: str, name: str, line_full: str, destination: str, label: str, aliases: list[str] | None = None) -> None:
            key = (gid, destination)
            if key in seen_keys:
                return
            seen_keys.add(key)
            entry = {
                "global_id": gid,
                "name": name,
                "line_full": line_full,
                "destination": destination,
                "label": label,
            }
            if aliases:
                entry["destination_aliases"] = aliases
            result.append(entry)

        for gid, official_entries in api_by_line.items():
            name = official_entries[0]["name"]
            line_full = official_entries[0]["line_full"]
            official_dests = [e["destination"] for e in official_entries if e["destination"]]
            live_dests = [c["destination"] for c in dm_by_line.get(gid, []) if c["destination"]]

            matched_official: set[str] = set()
            matched_live: set[str] = set()
            for off in official_dests:
                for live in live_dests:
                    if _destination_matches(off, live):
                        matched_official.add(off)
                        matched_live.add(live)

            unmatched_official = [d for d in official_dests if d not in matched_official]
            unmatched_live = [d for d in live_dests if d not in matched_live]

            aliases_by_official: dict[str, list[str]] = {}
            if len(unmatched_official) == 1 and len(unmatched_live) == 1:
                aliases_by_official[unmatched_official[0]] = [unmatched_live[0]]
                matched_official.add(unmatched_official[0])
                unmatched_official = []
                unmatched_live = []

            for off in official_dests:
                if off in matched_official:
                    _add(
                        gid, name, line_full, off,
                        label=f"{name} → {off}",
                        aliases=aliases_by_official.get(off),
                    )

            if unmatched_official:
                # Ambiguous leftovers (more than one on either side) — offer a
                # single safe, non-direction-specific entry for this line
                # instead of a direction filter that might never match.
                _add(gid, name, line_full, "", label=f"{name} (beide Richtungen, unbestätigt)")

            # Live destinations that don't correspond to any official terminus
            # at all (e.g. a genuine extra branch/short-turn service) — these
            # were actually observed, so they're safe to offer directly.
            for live in unmatched_live:
                _add(gid, name, line_full, live, label=f"{name} → {live}")

        # Lines DM sampling found that SERVINGLINES didn't mention at all
        # (rare, but keeps the list complete).
        for gid, dm_entries in dm_by_line.items():
            if gid in api_by_line:
                continue
            for c in dm_entries:
                _add(gid, c["name"], c["line_full"], c["destination"], label=c["label"])

        return _sort_lines(result)

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

        seen: set[tuple[str, str]] = set()
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

            line_key = global_id if global_id else name
            destination = transport.get("destination", {}).get("name", "")

            # Dedup on (line, destination) — the same line can run in several
            # directions, and those are distinct, separately-selectable entries.
            dedup_key = (line_key, destination)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            type_part = line_full.replace(name, "").strip() if line_full != name else ""
            base_label = f"{name} ({type_part})" if type_part else name
            label = f"{base_label} → {destination}" if destination else base_label

            lines.append(
                {
                    "global_id": line_key,
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

        seen: set[tuple[str, str]] = set()
        lines: list[dict] = []
        for batch in results:
            for dep in batch:
                name = dep.get("line", "")
                global_id = dep.get("global_id", "")
                line_key = global_id if global_id else name
                destination = dep.get("destination", "")
                if not line_key:
                    continue
                dedup_key = (line_key, destination)
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)
                line_full = dep.get("line_full", name)
                type_part = line_full.replace(name, "").strip() if line_full != name else ""
                base_label = f"{name} ({type_part})" if type_part else name
                label = f"{base_label} → {destination}" if destination else base_label
                lines.append(
                    {
                        "global_id": line_key,
                        "name": name,
                        "line_full": line_full,
                        "destination": destination,
                        "label": label,
                    }
                )

        return _sort_lines(lines)

    async def get_departures(
        self,
        stop_id: str,
        direction_filters: list[dict],
        priority_filter: list[str] | None = None,
        type_filter: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Fetch departures for a stop and bucket them per configured
        "Linie + Richtung" entry, so every selection gets its own reserved
        slots instead of competing for spots in one shared chronological feed.

        direction_filters: list of entries as described in const.py —
            {"key", "line_global_id", "line_name", "destination", "count"}.
            An entry with line_global_id=None is the legacy "alle Linien"
            sentinel (no filtering at all, old behaviour).

        Returns {buckets: {key: [departure, ...]}, disruptions: [...]}.
        """
        entries = direction_filters or []
        total_requested = sum(max(int(e.get("count", 0)), 0) for e in entries) or 10
        has_specific_filter = any(e.get("line_global_id") for e in entries)

        # A single combined raw request underpins all buckets. When specific
        # lines are selected we look further ahead so that rarely-departing
        # lines still have a chance to show up in the raw window; unfiltered
        # ("alle Linien") setups keep the old, cheaper window size.
        if has_specific_filter:
            fetch_limit = min(max(total_requested * 15, 90), 300)
        else:
            fetch_limit = min(max(total_requested * 3, 15), 100)

        params = {
            "language": "de",
            "outputFormat": "rapidJSON",
            "typeInfo_dm": "stopID",
            "nameInfo_dm": stop_id,
            "deleteAssignedStopps_dm": "1",
            "useRealtime": "1",
            "mode": "direct",
            "limit": str(fetch_limit),
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

        buckets: dict[str, list[dict]] = {e["key"]: [] for e in entries}
        # For diagnostics: every live destination text seen per line globalId,
        # so we can log what was actually available if a direction filter
        # never finds a match.
        seen_destinations_by_line: dict[str, set[str]] = {}

        for event in raw_events:
            transport = event.get("transportation", {})
            gid = transport.get("globalId", "")
            line_name = transport.get("disassembledName") or transport.get("number", "")
            line_key = gid if gid else line_name
            destination = transport.get("destination", {}).get("name", "")

            if destination:
                seen_destinations_by_line.setdefault(line_key, set()).add(destination)

            parsed: dict | None = None  # lazily parsed, only if it matches something

            for entry in entries:
                bucket = buckets[entry["key"]]
                entry_count = max(int(entry.get("count", 0)), 0)
                if len(bucket) >= entry_count:
                    continue  # this bucket is already full

                entry_line = entry.get("line_global_id")
                entry_dest = entry.get("destination")
                entry_aliases = entry.get("destination_aliases", [])

                if entry_line is None:
                    matches = True  # legacy "alle Linien" sentinel — no filter
                elif entry_line != line_key:
                    matches = False
                elif entry_dest is None:
                    matches = True  # line selected, both directions allowed
                else:
                    matches = _destination_matches_any(entry_dest, entry_aliases, destination)

                if not matches:
                    continue

                if parsed is None:
                    parsed = _parse_departure(event)
                    if parsed is None:
                        break
                bucket.append(parsed)

        # Diagnostics: warn (once per update) about direction filters that
        # found nothing, showing what destinations *were* seen for that line —
        # this is the fastest way to spot a text mismatch between SERVINGLINES
        # and the live DM feed for a specific direction.
        for entry in entries:
            if entry.get("line_global_id") is None:
                continue
            if buckets[entry["key"]]:
                continue
            configured_dest = entry.get("destination")
            if configured_dest is None:
                continue  # "both directions" entries aren't direction-specific
            live_seen = seen_destinations_by_line.get(entry["line_global_id"])
            if live_seen:
                _LOGGER.warning(
                    "EFA Departures: '%s → %s' fand keine Übereinstimmung. "
                    "Live gesehene Ziele für diese Linie an dieser Haltestelle: %s",
                    entry.get("line_name", entry["line_global_id"]),
                    configured_dest,
                    sorted(live_seen),
                )
            else:
                _LOGGER.debug(
                    "EFA Departures: '%s → %s' — Linie kam im aktuellen "
                    "Abfrage-Fenster gar nicht vor (fetch_limit=%s).",
                    entry.get("line_name", entry["line_global_id"]),
                    configured_dest,
                    fetch_limit,
                )

        # Legacy line_filter shape for disruption matching: any specifically
        # selected line stays restricted; presence of the "alle Linien"
        # sentinel means no restriction at all (matches old empty-filter behaviour).
        if has_specific_filter and not any(e.get("line_global_id") is None for e in entries):
            active_filter = [e["line_global_id"] for e in entries if e.get("line_global_id")]
        else:
            active_filter = []

        disruptions = _parse_disruptions(
            raw_events, active_filter, priority_filter, type_filter
        )

        return {"buckets": buckets, "disruptions": disruptions}
