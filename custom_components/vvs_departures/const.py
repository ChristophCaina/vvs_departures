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
CONF_LINE_FILTER = "line_filter"
CONF_DEPARTURE_COUNT = "departure_count"
CONF_UPDATE_INTERVAL = "update_interval"
CONF_DISRUPTION_UPDATE_INTERVAL = "disruption_update_interval"

# Defaults
DEFAULT_DEPARTURE_COUNT = 4
DEFAULT_UPDATE_INTERVAL = 60        # seconds
DEFAULT_DISRUPTION_INTERVAL = 3600  # seconds

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
