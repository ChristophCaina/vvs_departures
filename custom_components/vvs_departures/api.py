"""EFA API client for VVS Departures integration."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

import aiohttp

from .const import (
    EFA_DM_URL,
    EFA_STOPFINDER_URL,
)

_LOGGER = logging.getLogger(__name__)


def _strip_html(text: str) -> str:
    """Remove HTML tags and clean up whitespace."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


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
            planned_dt = datetime.fromisoformat(
                planned_str.replace("Z", "+00:00")
            )
        if estimated_str:
            estimated_dt = datetime.fromisoformat(
                estimated_str.replace("Z", "+00:00")
            )

        if planned_dt and estimated_dt:
            delta = estimated_dt - planned_dt
            delay_minutes = int(delta.total_seconds() // 60)

        platform = (
            event.get("location", {})
            .get("properties", {})
            .get("platformName", "")
        )

        realtime = "MONITORED" in event.get("realtimeStatus", [])

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
    """
    Extract unique disruption messages from stopEvents.
    Filters by line global ID, priority, and info type.
    """
    seen_ids: set[str] = set()
    disruptions: list[dict] = []

    for event in events:
        transport = event.get("transportation", {})
        line_global_id = transport.get("globalId", "")
        line_name_fb = transport.get("disassembledName") or transport.get("number", "")
        line_filter_key = line_global_id if line_global_id else line_name_fb

        # Line filter
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

            # Priority filter
            if priority_filter and priority not in priority_filter:
                continue

            # Type filter
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
                    "title": title[:120] if title else "",
                    "text": plain_text[:300] if plain_text else "",
                    "priority": priority,
                    "type": info_type,
                    "line": line_name,
                    "created": created_str,
                }
            )

    # Sort: high priority first, then by creation date descending
    priority_order = {"veryHigh": 0, "high": 1, "normal": 2, "low": 3}
    disruptions.sort(
        key=lambda d: (
            priority_order.get(d["priority"], 9),
            d["created"] or "",
        ),
        reverse=False,
    )
    # secondary sort by created desc within same priority
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


class VVSApiClient:
    """Async API client for EFA/VVS."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def search_stops(self, query: str) -> list[dict]:
        """Search for stops by name. Returns list of {id, name, type}."""
        params = {
            "language": "de",
            "outputFormat": "rapidJSON",
            "type_sf": "any",
            "name_sf": query,
            "locationServerActive": "1",
            "anyObjFilter_sf": "2",  # stops only
        }
        try:
            async with self._session.get(
                EFA_STOPFINDER_URL, params=params, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        except Exception as exc:
            _LOGGER.error("Stop search failed: %s", exc)
            return []

        results = []
        for loc in data.get("locations", []):
            if loc.get("type") not in ("stop", "platform"):
                continue
            stop_id = loc.get("id", "")
            name = loc.get("name", "")
            parent_name = loc.get("parent", {}).get("name", "")
            display = f"{name} ({parent_name})" if parent_name and parent_name != name else name
            results.append(
                {
                    "id": stop_id,
                    "name": display,
                    "raw_name": name,
                }
            )
        return results

    async def get_serving_lines(self, stop_id: str) -> list[dict]:
        """
        Derive serving lines by fetching departures across multiple time windows.
        This catches infrequent lines (e.g. S60) that may not appear in a single
        30-departure snapshot.
        """
        import asyncio as _asyncio
        from datetime import datetime, timezone, timedelta

        all_departures: list[dict] = []

        # Fetch departures now + in 2h + in 4h to catch infrequent lines
        offsets_minutes = [0, 120, 240]
        fetch_tasks = []

        async def _fetch_at_offset(offset_min: int) -> list[dict]:
            params = {
                "language": "de",
                "outputFormat": "rapidJSON",
                "typeInfo_dm": "stopID",
                "nameInfo_dm": stop_id,
                "deleteAssignedStopps_dm": "1",
                "useRealtime": "0",  # no realtime needed for line discovery
                "mode": "direct",
                "limit": "30",
                "version": "10.5.17.3",
            }
            if offset_min > 0:
                target = datetime.now(tz=timezone.utc) + timedelta(minutes=offset_min)
                params["itdDate"] = target.strftime("%Y%m%d")
                params["itdTime"] = target.strftime("%H%M")
            try:
                async with self._session.get(
                    EFA_DM_URL, params=params, timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    resp.raise_for_status()
                    data = await resp.json(content_type=None)
                    return [_parse_departure(e) for e in data.get("stopEvents", []) if _parse_departure(e)]
            except Exception as exc:
                _LOGGER.debug("Serving lines fetch at offset %d failed: %s", offset_min, exc)
                return []

        results = await _asyncio.gather(*[_fetch_at_offset(o) for o in offsets_minutes])
        for r in results:
            all_departures.extend(r)

        seen: set[str] = set()
        lines = []

        for dep in all_departures:
            name = dep.get("line", "")
            global_id = dep.get("global_id", "")
            dedup_key = global_id if global_id else name
            if not dedup_key or dedup_key in seen:
                continue
            seen.add(dedup_key)

            dest = dep.get("destination", "")
            line_full = dep.get("line_full", name)

            # Build a readable label e.g. "S1 (S-Bahn) → Plochingen"
            type_part = line_full.replace(name, "").strip() if line_full != name else ""
            label = f"{name} ({type_part})" if type_part else name

            lines.append(
                {
                    "global_id": dedup_key,
                    "name": name,
                    "line_full": line_full,
                    "destination": dest,
                    "label": label,
                }
            )

        # Sort: S-Bahn first, then U/Stadtbahn, then MEX/RE/RB, then Bus
        def sort_key(l: dict) -> tuple:
            n = l["name"]
            if n.startswith("S") and (len(n) <= 3 or n[1:].isdigit()):
                return (0, n.zfill(5))
            if n.startswith("U"):
                return (1, n.zfill(5))
            if any(n.startswith(p) for p in ("MEX", "RE", "RB", "IC", "EC")):
                return (2, n)
            return (3, n.zfill(5))

        lines.sort(key=sort_key)
        return lines

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
                EFA_DM_URL, params=params, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        except Exception as exc:
            _LOGGER.error("Departure fetch failed for stop %s: %s", stop_id, exc)
            raise

        raw_events = data.get("stopEvents", [])
        active_filter = line_filter or []

        # Parse departures (apply line filter)
        departures = []
        for event in raw_events:
            transport = event.get("transportation", {})
            gid = transport.get("globalId", "")
            # Fallback: use line name if globalId is empty
            line_name = transport.get("disassembledName") or transport.get("number", "")
            filter_key = gid if gid else line_name
            if active_filter and filter_key not in active_filter:
                continue
            parsed = _parse_departure(event)
            if parsed:
                departures.append(parsed)

        # Parse disruptions (de-duplicated, filtered)
        disruptions = _parse_disruptions(raw_events, active_filter, priority_filter, type_filter)

        return {
            "departures": departures,
            "disruptions": disruptions,
        }
