"""Constants for the EVC-net integration."""

DOMAIN = "evcnet"

# Configuration
CONF_BASE_URL = "base_url"

# Default values
DEFAULT_BASE_URL = "https://50five-snl.evc-net.com"
DEFAULT_SCAN_INTERVAL = 60  # seconds
LOG_ROW_LIMIT = 100  # Max number of log entries to process/display

# API endpoints
LOGIN_ENDPOINT = "/Login/Login"
AJAX_ENDPOINT = "/api/ajax"

# Key names
KEY_ID = "id"
KEY_TEXT = "text"
KEY_CUSTOMERS_IDX = "CUSTOMERS_IDX"
KEY_CUSTOMER_NAME = "CUSTOMER_NAME"
KEY_CARDS_IDX = "CARDS_IDX"
KEY_CARDID = "CARDID"


class EvcNetException(Exception):
    """Base exception for EVC-net."""
