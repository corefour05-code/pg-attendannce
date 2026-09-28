"""Analytics: hostel occupancy dashboard.
Shows: total residents, currently inside count, today's movements, late entry logs,
daily movement count chart, and staff duty logs."""

import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi import APIRouter, Request

from app.deps import admin_template_context, get_developers_context, require_admin
from app.templating import templates
from config import LATE_ENTRY_CUTOFF_HOUR
from core.time_format import format_datetime_time_12h
from db.connection import get_connection

router = APIRouter()


def _build_filters(request: Request):
    from_date = request.query_params.get("from_date") or date.today().isoformat()
    to_date = request.query_params.get("to_date") or date.today().isoformat()
    cutoff_param = request.query_params.get("cutoff_hour")
    try:
        cutoff_hour = int(cutoff_param) if cutoff_param else LATE_ENTRY_CUTOFF_HOUR
    except ValueError:
        cutoff_hour = LATE_ENTRY_CUTOFF_HOUR
    if not (0 <= cutoff_hour <= 23):
        cutoff_hour = LATE_ENTRY_CUTOFF_HOUR
    return from_date, to_date, cutoff_hour


def _parse_dt(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None


def _resident_counts(conn):
    total = conn.execute("SELECT COUNT(*) c FROM residents WHERE archived_at IS NULL").fetchone()["c"]
    
    # In a PG hostel, residents are inside unless their latest movement state is OUT
    outside_count = conn.execute(
        "SELECT COUNT(DISTINCT resident_id) c FROM attendance a1 "
        "WHERE UPPER(direction) = 'OUT' AND id = (SELECT MAX(id) FROM attendance a2 WHERE a2.resident_id = a1.resident_id)"
    ).fetchone()["c"]
    
    currently_inside = max(0, total - outside_count)
    return total, currently_inside


def _today_movements(conn):
    today = date.today().isoformat()
    rows = conn.execute(
        "SELECT r.resident_id, r.name, r.room_no, a.direction, a.punch_time "
        "FROM attendance a JOIN residents r ON r.resident_id = a.resident_id "
        "WHERE a.punch_date=? ORDER BY a.punch_time DESC",
        (today,),
    ).fetchall()
    return [dict(r) for r in rows]


def _daily_movement_counts(conn, from_date, to_date):
    rows = conn.execute(
        "SELECT punch_date, COUNT(*) c FROM attendance "
        "WHERE punch_date BETWEEN ? AND ? GROUP BY punch_date ORDER BY punch_date",
        (from_date, to_date),
    ).fetchall()
    return {r["punch_date"]: r["c"] for r in rows}


def _late_entries(conn, from_date, to_date, cutoff_hour):
    rows = conn.execute(
        "SELECT r.resident_id, r.name, r.room_no, a.punch_date, a.punch_time "
        "FROM attendance a JOIN residents r ON r.resident_id = a.resident_id "
        "WHERE a.punch_date BETWEEN ? AND ? AND UPPER(a.direction) = 'IN' "
        "ORDER BY a.punch_time DESC",
        (from_date, to_date),
    ).fetchall()
    late = []
    for r in rows:
        dt = _parse_dt(r["punch_time"])
        if dt is not None and dt.hour >= cutoff_hour:
            row_dict = dict(r)
            row_dict["time_12"] = format_datetime_time_12h(r["punch_time"])
            late.append(row_dict)
    return late


def _staff_movements(conn, from_date, to_date):
    rows = conn.execute(
        "SELECT sa.staff_id, sa.punch_date, sa.punch_time, sa.direction, "
        "s.name AS name, s.designation AS designation, s.role AS role "
        "FROM staff_attendance sa JOIN staff s ON s.staff_id = sa.staff_id "
        "WHERE sa.punch_date BETWEEN ? AND ? "
        "ORDER BY sa.punch_time DESC",
        (from_date, to_date),
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/analytics")
def analytics_page(request: Request):
    user, redirect = require_admin(request)
    if redirect:
        return redirect

    from_date, to_date, cutoff_hour = _build_filters(request)

    conn = get_connection()
    try:
        total_residents, currently_inside = _resident_counts(conn)
        today_movements = _today_movements(conn)
        daily_counts = _daily_movement_counts(conn, from_date, to_date)
        late_entries = _late_entries(conn, from_date, to_date, cutoff_hour)
        staff_logs = _staff_movements(conn, from_date, to_date)
        total_staff_today = conn.execute(
            "SELECT COUNT(DISTINCT staff_id) c FROM staff_attendance WHERE punch_date=?",
            (date.today().isoformat(),),
        ).fetchone()["c"]
    finally:
        conn.close()

    day_labels = sorted(daily_counts.keys())
    day_values = [daily_counts[d] for d in day_labels]

    context = {
        **admin_template_context(user),
        "active_nav": "analytics",
        "developers": get_developers_context(),
        "from_date": from_date,
        "to_date": to_date,
        "cutoff_hour": cutoff_hour,
        "total_residents": total_residents,
        "currently_inside": currently_inside,
        "today_movements": today_movements,
        "late_entries": late_entries,
        "staff_logs": staff_logs,
        "total_staff_today": total_staff_today,
        "day_labels": day_labels,
        "day_values": day_values,
    }
    return templates.TemplateResponse(request, "analytics.html", context)
