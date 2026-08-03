"""Constants for the EFA Departures integration."""

DOMAIN = "vvs_departures"
PLATFORMS = ["sensor"]

# EFA API path suffixes (appended to provider base URL)
EFA_STOPFINDER_PATH = "/XML_STOPFINDER_REQUEST"
EFA_DM_PATH = "/XML_DM_REQUEST"
EFA_SERVINGLINES_PATH = "/XML_SERVINGLINES_REQUEST"

# Known EFA regions: display label → base URL
# Only regions with verified rapidJSON + XML_DM/STOPFINDER support are listed.
# Austria (VOR/Wiener Linien) and Switzerland (ZVV/SBB) primarily use HAFAS
# or proprietary APIs and are not supported as built-in regions.
EFA_REGIONS: dict[str, str] = {
    "EFA-BW / NVBW (Baden-Württemberg)": "https://www.efa-bw.de/bvb3",
    "DEFAS Bayern (MVV, VGN, u.a.)":     "https://efa.mvv-muenchen.de/mvv",
    "VRR (Rhein-Ruhr, NRW)":             "https://efa.vrr.de/vrr",
    "VRN (Rhein-Neckar, Mannheim)":      "https://www.vrn.de/mngvrn",
    "Benutzerdefiniert / Custom":         "__custom__",
}

CUSTOM_PROVIDER_KEY = "__custom__"

# Config entry keys
CONF_EFA_BASE_URL = "efa_base_url"
CONF_REGION_NAME = "region_name"
CONF_CITY_NAME = "city_name"
CONF_STOP_ID = "stop_id"
CONF_STOP_NAME = "stop_name"
CONF_LINE_FILTER = "line_filter"          # legacy (≤v3): list[str] of line global_ids
CONF_LINE_DIRECTIONS = "line_directions"  # v4+: list[dict] — see below
CONF_DEPARTURE_COUNT = "departure_count"
CONF_UPDATE_INTERVAL = "update_interval"
CONF_DISRUPTION_UPDATE_INTERVAL = "disruption_update_interval"

# Defaults
DEFAULT_DEPARTURE_COUNT = 4
DEFAULT_DIRECTION_COUNT = 2         # sensors per selected Linie+Richtung entry
DEFAULT_UPDATE_INTERVAL = 60        # seconds
DEFAULT_DISRUPTION_INTERVAL = 3600  # seconds

# CONF_LINE_DIRECTIONS holds a list of dicts, one per configured "Linie + Richtung":
#   {
#     "key":            str        stable id, used for unique_id + bucket lookup
#     "line_global_id": str|None   EFA globalId of the line; None = legacy "alle Linien" sentinel
#     "line_name":      str        display name, e.g. "54" or "S6"
#     "destination":    str|None   direction/destination text; None = beide Richtungen
#     "count":          int        number of departure sensors for this entry
#   }
#
# Special sentinel key for the legacy "no filter at all" behaviour, kept around
# so entries migrated from ≤v3 configs without an explicit line_filter keep
# working exactly as before until the user opts into the new per-line model.
ALL_LINES_SENTINEL_KEY = "__all_lines__"
NO_DIRECTION_FILTER = "__all__"  # used in the config-flow selector value, not stored as-is

# Coordinator keys
COORDINATOR_DEPARTURES = "departures"
COORDINATOR_DISRUPTIONS = "disruptions"

# Disruption filter config keys
CONF_DISRUPTION_PRIORITIES = "disruption_priorities"
CONF_DISRUPTION_TYPES = "disruption_types"

# Available values
DISRUPTION_PRIORITY_OPTIONS = ["veryHigh", "high", "normal", "low"]
DISRUPTION_TYPE_OPTIONS = ["lineInfo", "stationInfo", "stopInfo", "network"]

# Defaults
DEFAULT_DISRUPTION_PRIORITIES = ["veryHigh", "high"]
DEFAULT_DISRUPTION_TYPES = ["lineInfo", "stationInfo", "stopInfo", "network"]
