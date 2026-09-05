"""Constants for Home + Security integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "home_plus_security"
NAME = "Home + Security"

CONF_ACCESS_TOKEN = "access_token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_CLIENT_ID = "client_id"
CONF_CLIENT_SECRET = "client_secret"
CONF_SCOPE = "scope"
CONF_HOME_ID = "home_id"
CONF_HOME_NAME = "home_name"

CONF_TOKEN_URL = "token_url"
CONF_APP_API_BASE_URL = "app_api_base_url"
CONF_SYNC_API_BASE_URL = "sync_api_base_url"
CONF_TURN_API_BASE_URL = "turn_api_base_url"

DEFAULT_CLIENT_ID = "na_client_android_welcome"
DEFAULT_CLIENT_SECRET = "8ab584d62ca2a77e37ccc6b2c7e4f29e"
DEFAULT_APP_TYPE = "app_security"
DEFAULT_SCOPE = "security_scopes"
REQUIRED_SECURITY_SCOPE = DEFAULT_SCOPE

DEFAULT_TOKEN_URL = "https://app.netatmo.net/oauth2/token"
DEFAULT_APP_API_BASE_URL = "https://app.netatmo.net/api"
DEFAULT_SYNC_API_BASE_URL = "https://app.netatmo.net/syncapi/v1"
DEFAULT_TURN_API_BASE_URL = "https://app-turn.netatmo.net/api"

DEFAULT_APP_VERSION = "26.4.0.3"
DEFAULT_APP_PLATFORM = "android"
DEFAULT_APP_CAMERA_TYPE = "app_camera"

PUSH_WS_URL = "wss://app-ws.netatmo.net/ws/"
SIGNALING_WS_URL = "wss://app-ws.netatmo.net/appws/"

TOKEN_REFRESH_MARGIN = timedelta(minutes=2)
COORDINATOR_UPDATE_INTERVAL = timedelta(minutes=1)

COMMAND_TIMEOUT_SECONDS = 8.0
COMMAND_COOLDOWN_SECONDS = 1.5
CALL_STALE_THRESHOLD_SECONDS = 300
WS_STALE_THRESHOLD_SECONDS = 600
WS_RESUBSCRIBE_INTERVAL_SECONDS = 3600
WS_BOOT_RETRY_DELAYS = (5, 10, 30, 30, 30, 60)
WS_RUNTIME_RETRY_DELAYS = (5, 15, 30, 60, 120)
IMAGE_CACHE_SECONDS = 300
HISTORY_STORAGE_VERSION = 1
HISTORY_RETENTION_DAYS = 30
HISTORY_MAX_EVENTS = 100
HISTORY_MAX_IMAGE_BYTES = 10 * 1024 * 1024

OPT_HISTORY_ENABLED = "history_enabled"
OPT_HISTORY_RETENTION_DAYS = "history_retention_days"
OPT_HISTORY_MAX_EVENTS = "history_max_events"
OPT_DIAGNOSTICS = "diagnostics"
OPT_DIAGNOSTICS_CONFIGURED = "diagnostics_configured"
OPT_EXPOSE_UNLOCK = "expose_unlock"
OPT_EXPOSE_CAMERAS = "expose_cameras"

DIAGNOSTIC_DEVICE_REACHABLE = "device_reachable"
DIAGNOSTIC_CONNECTION_TYPE = "connection_type"
DIAGNOSTIC_WIFI_STRENGTH = "wifi_strength"
DIAGNOSTIC_UPTIME = "uptime"
DIAGNOSTIC_DEVICE_STATUS_UPDATED = "device_status_updated"
DIAGNOSTIC_DEVICE_WEBSOCKET = "device_websocket"
DIAGNOSTIC_LOCAL_IP = "local_ip"
DIAGNOSTIC_PUSH_WEBSOCKET = "push_websocket"
DIAGNOSTIC_WEBSOCKET_STALE = "websocket_stale"
DIAGNOSTIC_WEBSOCKET_LAST_MESSAGE = "websocket_last_message"
DIAGNOSTIC_LAST_COMMAND_ERROR = "last_command_error"
DEFAULT_DIAGNOSTICS = (
    DIAGNOSTIC_DEVICE_REACHABLE,
    DIAGNOSTIC_CONNECTION_TYPE,
    DIAGNOSTIC_WIFI_STRENGTH,
    DIAGNOSTIC_UPTIME,
)

DATA_CLIENT = "client"
DATA_COORDINATOR = "coordinator"
DATA_WS_MANAGER = "ws_manager"
DATA_SIGNALING_CLIENT = "signaling_client"
