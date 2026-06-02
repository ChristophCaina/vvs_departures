"""Constants for the VVS Departures integration."""

DOMAIN = "vvs_departures"
PLATFORMS = ["sensor"]

# EFA API endpoints (efa-bw.de / VVS)
EFA_BASE_URL = "https://www.efa-bw.de/bvb3"
EFA_STOPFINDER_URL = f"{EFA_BASE_URL}/XML_STOPFINDER_REQUEST"
EFA_DM_URL = f"{EFA_BASE_URL}/XML_DM_REQUEST"
EFA_SERVINGLINES_URL = f"{EFA_BASE_URL}/XML_SERVINGLINES_REQUEST"

# Config entry keys
CONF_STOP_ID = "stop_id"
CONF_STOP_NAME = "stop_name"
CONF_LINE_FILTER = "line_filter"
CONF_DEPARTURE_COUNT = "departure_count"
CONF_UPDATE_INTERVAL = "update_interval"
CONF_DISRUPTION_UPDATE_INTERVAL = "disruption_update_interval"

# Defaults
DEFAULT_DEPARTURE_COUNT = 4
DEFAULT_UPDATE_INTERVAL = 60       # seconds
DEFAULT_DISRUPTION_INTERVAL = 3600 # seconds

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
