"""Utils for EVC-net."""

from datetime import datetime
import logging
from typing import Any

from .const import LOG_ROW_LIMIT

_LOGGER = logging.getLogger(__name__)


def parse_locale_number(value: Any, default: float = 0.0) -> float:
    """Parseert getallen en gaat slim om met Europese/Engelse notaties."""
    if value is None or value == "":
        return default

    if isinstance(value, (int, float)):
        return float(value)

    # Delete currency signs or extra spaces
    clean_value = str(value).strip().replace("€", "").replace("%", "")

    try:
        # Is it a standard float-string? (1.234)
        return float(clean_value)
    except ValueError:
        try:
            # Is the European format? (1.234,56)
            # Delete periods (thousands separator) and replace comma with dot
            normalized = clean_value.replace(".", "").replace(",", ".")
            return float(normalized)
        except ValueError:
            _LOGGER.warning("Unexpected number format for 50five: '%s'", value)
            return default


def convert_time_to_minutes(time_str: str) -> int:
    """Convert HH:mm to total minutes."""
    try:
        parts = time_str.split(":")
        return (int(parts[0]) * 60) + int(parts[1])
    except (ValueError, IndexError):
        return 0


def convert_energy_to_kwh(value: float, unit: str) -> float:
    """Convert energy value from various units to kWh.

    Supports: Wh, kWh, MWh, GWh (case-insensitive).
    Returns the value in kWh.
    """
    unit_upper = unit.strip().upper()

    # Conversion factors to kWh
    conversion_factors = {
        "WH": 0.001,  # 1 Wh = 0.001 kWh
        "KWH": 1.0,  # 1 kWh = 1 kWh
        "MWH": 1000.0,  # 1 MWh = 1000 kWh
        "GWH": 1000000.0,  # 1 GWh = 1000000 kWh
    }

    factor = conversion_factors.get(unit_upper, 1.0)
    if unit_upper not in conversion_factors:
        _LOGGER.warning("Unknown energy unit '%s', assuming kWh", unit)

    try:
        return float(value) * factor
    except (ValueError, TypeError) as err:
        _LOGGER.warning(
            "Error converting energy value '%s' with unit '%s': %s", value, unit, err
        )
        return 0.0


def get_total_energy_usage_kwh(data: dict) -> float:
    """Extract total energy usage and convert to kWh, handling dynamic units."""
    number = data.get("number", 0.0)
    unit = data.get("unit", "kWh")

    # Parse the number if it's a string
    if isinstance(number, str):
        number = parse_locale_number(number, default=0.0)
    elif not isinstance(number, (int, float)):
        number = 0.0

    # Convert to kWh
    return convert_energy_to_kwh(number, unit)


def format_logging_to_markdown(log_list: list[dict]) -> str:
    """Convert the API logging data to a Markdown table."""
    if not log_list:
        return "No logging data available."

    target_keys = [
        "LOG_DATE",
        "NOTIFICATION",
        "MOM_POWER_KW",
        "TRANS_ENERGY_DELIVERED_KWH",
        "TRANSACTION_TIME_H_M",
    ]
    header_keys = ["Date", "Message", "Power (kW)", "Energy (kWh)", "Time"]

    # Remove duplicates and format dates
    seen = set()
    unique_rows = []

    for item in log_list:
        date_val = item.get("LOG_DATE", "")
        # Remove seconds for deduplication
        date_to_minute = date_val[:-3] if len(date_val) > 3 else date_val

        identifier = (
            f"{date_to_minute}|{item.get('NOTIFICATION')}|{item.get('MOM_POWER_KW')}"
        )

        if identifier not in seen:
            seen.add(identifier)

            # Format the row for Markdown output
            row = []
            for key in target_keys:
                val = item.get(key, "-")
                if key == "LOG_DATE" and val != "-":
                    val = format_date(val)
                row.append(str(val).replace("|", "\\|"))
            unique_rows.append(f"| {' | '.join(row)} |")

    # Build the table
    header = f"| {' | '.join(header_keys)} |"
    separator = f"| {' | '.join(['---'] * len(header_keys))} |"

    now = datetime.now().strftime("%d-%m-%y %H:%M")
    timestamp_md = f"> 🕒 **Last Update:** {now}\n\n"

    return timestamp_md + "\n".join([header, separator, *unique_rows[:LOG_ROW_LIMIT]])


def format_date(date_str: str) -> str:
    """Convert LMS date strings to a universally readable format."""
    if not date_str or date_str == "-":
        return "-"

    try:
        parts = date_str.split("-")
        if len(parts) < 3:
            return date_str

        day = parts[0].zfill(2)
        month_raw = parts[1].replace(".", "").strip().lower()
        year_time_parts = parts[2].split(" ")
        year_short = year_time_parts[0]
        year_full = f"20{year_short}" if len(year_short) == 2 else year_short
        time_full = year_time_parts[1]
        time_parts = time_full.split(":")
        time_hm = f"{time_parts[0].zfill(2)}:{time_parts[1].zfill(2)}"
        return f"{day} {month_raw.capitalize()} {year_full}, {time_hm}"

    except (IndexError, ValueError) as err:
        _LOGGER.debug("Parsing error for date string '%s': %s", date_str, err)
        return date_str


def format_dutch_date(date_str: str) -> str:
    """Convert LMS date strings to a universally readable format."""
    try:
        months = {
            "jan.": "01",
            "feb.": "02",
            "mrt.": "03",
            "apr.": "04",
            "mei": "05",
            "jun.": "06",
            "jul.": "07",
            "aug.": "08",
            "sep.": "09",
            "okt.": "10",
            "nov.": "11",
            "dec.": "12",
        }
        parts = date_str.split("-")
        day = parts[0].zfill(2)
        month_abbr = parts[1].lower()
        year_time = parts[2].split(" ")
        year = year_time[0][-2:]
        time = ":".join(year_time[1].split(":")[:2])  # Alleen HH:mm
        return f"{day}-{months.get(month_abbr, '01')}-{year} {time}"

    except (IndexError, KeyError) as err:
        _LOGGER.debug("Error formatting Dutch date: %s", err)
        return date_str
