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
CONF_API_BASE_URL = "api_base_url"

DEFAULT_CLIENT_ID = "na_client_android_welcome"
DEFAULT_CLIENT_SECRET = "8ab584d62ca2a77e37ccc6b2c7e4f29e"
DEFAULT_APP_TYPE = "app_security"
DEFAULT_SCOPE = "security_scopes"
REQUIRED_SECURITY_SCOPE = DEFAULT_SCOPE

DEFAULT_TOKEN_URL = "https://app.netatmo.net/oauth2/token"
DEFAULT_API_BASE_URL = "https://app.netatmo.net/api"

TOKEN_REFRESH_MARGIN = timedelta(minutes=2)
COORDINATOR_UPDATE_INTERVAL = timedelta(minutes=1)

DATA_CLIENT = "client"
DATA_COORDINATOR = "coordinator"
