"""Render stored 24-hour time strings as 12-hour AM/PM for display. Storage
stays 24-hour (curfew_times.time as "HH:MM", attendance timestamps as
"YYYY-MM-DD HH:MM:SS") since that's what lexicographic comparison in the
curfew-schedule and dedup logic depends on — these helpers are
presentation-only, used by templates/JSON responses, never by storage or
comparisons."""

from datetime import datetime


def format_hm_12h(value: str | None) -> str:
    """'09:00' -> '09:00 AM'."""
    if not value:
        return "-"
    try:
        return datetime.strptime(value, "%H:%M").strftime("%I:%M %p")
    except ValueError:
        return value


def format_datetime_time_12h(value: str | None) -> str:
    """'2026-07-12 19:22:55' -> '07:22:55 PM'."""
    if not value:
        return "-"
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").strftime("%I:%M:%S %p")
    except ValueError:
        return value
