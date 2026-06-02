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


def _parse_disruptions(events: list[dict], line_filter: list[str]) -> list[dict]:
    """
    Extract unique disruption messages from stopEvents.
    De-duplicates by info ID and optionally filters by line global ID.
    """
    seen_ids: set[str] = set()
    disruptions: list[dict] = []

    for event in events:
        transport = event.get("transportation", {})
        line_global_id = transport.get("globalId", "")

        # If a line filter is active, skip events not matching
        if line_filter and line_global_id not in line_filter:
            continue

        line_name = transport.get("disassembledName", transport.get("number", "?"))

        for info in event.get("infos", []):
            info_id = info.get("id", "")
            if info_id in seen_ids:
                continue
            seen_ids.add(info_id)

            priority = info.get("priority", "normal")
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
                    "title": title,
                    "text": plain_text,
                    "priority": priority,
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
        Derive serving lines from a real departure fetch (limit=30).
        More reliable than XML_SERVINGLINES_REQUEST which has inconsistent
        parameter handling across different stop types.
        """
        try:
            result = await self.get_departures(stop_id, limit=30, line_filter=None)
        except Exception as exc:
            _LOGGER.error("Serving lines fetch failed for stop %s: %s", stop_id, exc)
            return []

        seen: set[str] = set()
        lines = []

        for dep in result.get("departures", []):
            global_id = dep.get("global_id", "")
            if not global_id or global_id in seen:
                continue
            seen.add(global_id)

            name = dep.get("line", "")
            dest = dep.get("destination", "")
            line_full = dep.get("line_full", name)

            # Build a readable label e.g. "S1 (S-Bahn) → Plochingen"
            type_part = line_full.replace(name, "").strip() if line_full != name else ""
            label = f"{name} ({type_part}) → {dest}" if type_part else f"{name} → {dest}"

            lines.append(
                {
                    "global_id": global_id,
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
            if active_filter and gid not in active_filter:
                continue
            parsed = _parse_departure(event)
            if parsed:
                departures.append(parsed)

        # Parse disruptions (de-duplicated, filtered)
        disruptions = _parse_disruptions(raw_events, active_filter)

        return {
            "departures": departures,
            "disruptions": disruptions,
        }
