"""Standalone live scanner page + attendance marking (Consecutive OUT/IN movement events).
Matches both residents and staff against the same live camera feed, logging each punch
into its respective attendance table."""

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import cv2
import numpy as np
from fastapi import APIRouter, File, Form, Request, UploadFile

from app import state
from app.deps import require_login
from app.templating import templates
from core.time_format import format_datetime_time_12h
from db.connection import get_connection

router = APIRouter()

# 1-minute debounce window (in seconds) to prevent mistaken consecutive scans
SCAN_COOLDOWN_SECONDS = 60


def _parse_punch_datetime(dt_str: str) -> datetime:
    try:
        return datetime.fromisoformat(dt_str)
    except ValueError:
        return datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")


def _toggle_attendance(
    conn, table: str, id_col: str, identity: str, kind: str
) -> tuple[str, bool, bool, int]:
    """Toggle consecutive OUT/IN movement events with a 1-minute debounce cooldown.
    
    For residents (who live in PG):
    - First scan of any calendar day is ALWAYS 'OUT' (leaving the hostel).
    - If last scan today was 'OUT' -> next scan is 'IN' (returning).
    - If last scan today was 'IN' -> next scan is 'OUT' (leaving again).
    
    For staff:
    - First scan of day is 'IN' (reporting for duty).
    - If last scan today was 'IN' -> next is 'OUT'.
    
    Returns:
        (status, marked, cooldown_active, remaining_seconds)
    """
    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    today_str = now.strftime("%Y-%m-%d")

    last_today = conn.execute(
        f"SELECT direction, punch_time FROM {table} WHERE {id_col}=? AND punch_date=? ORDER BY id DESC LIMIT 1",
        (identity, today_str),
    ).fetchone()

    if last_today is None:
        # First scan of today: residents always step OUT; staff report IN
        next_direction = "OUT" if kind == "resident" else "IN"
    else:
        last_dt = _parse_punch_datetime(last_today["punch_time"])
        elapsed = (now - last_dt).total_seconds()
        if elapsed < SCAN_COOLDOWN_SECONDS:
            remaining = int(SCAN_COOLDOWN_SECONDS - elapsed)
            return last_today["direction"].lower(), False, True, max(1, remaining)

        last_dir = last_today["direction"].upper()
        next_direction = "IN" if last_dir == "OUT" else "OUT"

    conn.execute(
        f"INSERT INTO {table} ({id_col}, direction, punch_time, punch_date) VALUES (?,?,?,?)",
        (identity, next_direction, now_str, today_str),
    )
    conn.commit()
    return next_direction.lower(), True, False, 0


@router.get("/scanner")
def scanner_page(request: Request):
    user, redirect = require_login(request)
    if redirect:
        return redirect

    conn = get_connection()
    try:
        settings = conn.execute("SELECT hostel_name FROM settings WHERE id=1").fetchone()
    finally:
        conn.close()
    hostel_name = settings["hostel_name"] if settings else "Vaagai Womens Hostel"

    return templates.TemplateResponse(request, "scanner.html", {"hostel_name": hostel_name})


@router.post("/api/scan")
async def scan(request: Request, image: UploadFile = File(...)):
    user, redirect = require_login(request)
    if redirect:
        return {"faces": [], "name": None, "error": "not logged in"}

    recognizer = state.get_recognizer()
    data = await image.read()
    arr = np.frombuffer(data, dtype=np.uint8)
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if frame is None:
        return {"faces": [], "name": None}

    results, _timing = recognizer.recognize(frame)
    matched = next((r for r in results if r["identity"] != "unknown"), None)

    if matched is None:
        return {"faces": results, "name": None}

    kind, identity = matched["kind"], matched["identity"]

    conn = get_connection()
    try:
        if kind == "resident":
            person = conn.execute(
                "SELECT name FROM residents WHERE resident_id=?", (identity,)
            ).fetchone()
        else:
            person = conn.execute(
                "SELECT name, role FROM staff WHERE staff_id=?", (identity,)
            ).fetchone()

        if person is None:
            return {"faces": results, "name": None}

        if kind == "resident":
            display_name = person["name"]
        else:
            role_label = "Security" if person["role"] == "security" else "Warden"
            display_name = f"{role_label} ({person['name']})"

        table, id_col = ("attendance", "resident_id") if kind == "resident" else ("staff_attendance", "staff_id")
        status, marked, cooldown, remaining = _toggle_attendance(conn, table, id_col, identity, kind)

        return {
            "faces": results,
            "name": display_name,
            "identity": identity,
            "kind": kind,
            "status": status,
            "marked": marked,
            "cooldown": cooldown,
            "remaining": remaining,
        }
    finally:
        conn.close()


@router.get("/api/scanner/logs")
def scanner_logs(request: Request):
    user, redirect = require_login(request)
    if redirect:
        return {"rows": [], "count": 0}

    today = date.today().isoformat()
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT 'resident' AS kind, r.resident_id AS person_id, r.name AS name, "
            "r.room_no AS room_no, a.direction, a.punch_time, a.punch_date "
            "FROM attendance a "
            "JOIN residents r ON r.resident_id = a.resident_id "
            "WHERE a.punch_date=? "
            "UNION ALL "
            "SELECT 'staff' AS kind, s.staff_id AS person_id, "
            "(CASE WHEN s.role='security' THEN 'Security (' ELSE 'Warden (' END) || s.name || ')' AS name, "
            "s.designation AS room_no, "
            "sa.direction, sa.punch_time, sa.punch_date "
            "FROM staff_attendance sa "
            "JOIN staff s ON s.staff_id = sa.staff_id "
            "WHERE sa.punch_date=? "
            "ORDER BY punch_time DESC",
            (today, today),
        ).fetchall()

        total_count = len(rows)
    finally:
        conn.close()

    formatted = []
    for r in rows:
        row = dict(r)
        row["punch_time_12"] = format_datetime_time_12h(row["punch_time"])
        formatted.append(row)

    return {"rows": formatted, "count": total_count}


@router.post("/api/scanner/clear")
def scanner_clear(request: Request, password: str = Form(...)):
    user, redirect = require_admin(request)
    if redirect:
        return {"ok": False, "error": "not logged in"}

    conn = get_connection()
    try:
        settings = conn.execute("SELECT gate_password FROM settings WHERE id=1").fetchone()
        if settings is None or password != settings["gate_password"]:
            return {"ok": False, "error": "Incorrect password"}

        today = date.today().isoformat()
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Mark OUT any resident whose last movement today was IN
        active_residents = conn.execute(
            "SELECT resident_id, direction FROM attendance a1 "
            "WHERE punch_date=? AND id = (SELECT MAX(id) FROM attendance a2 WHERE a2.resident_id = a1.resident_id AND a2.punch_date=?)",
            (today, today),
        ).fetchall()

        cleared = 0
        for ar in active_residents:
            if ar["direction"].upper() == "IN":
                conn.execute(
                    "INSERT INTO attendance (resident_id, direction, punch_time, punch_date) VALUES (?,?,?,?)",
                    (ar["resident_id"], "OUT", now_iso, today),
                )
                cleared += 1

        active_staff = conn.execute(
            "SELECT staff_id, direction FROM staff_attendance sa1 "
            "WHERE punch_date=? AND id = (SELECT MAX(id) FROM staff_attendance sa2 WHERE sa2.staff_id = sa1.staff_id AND sa2.punch_date=?)",
            (today, today),
        ).fetchall()

        for st in active_staff:
            if st["direction"].upper() == "IN":
                conn.execute(
                    "INSERT INTO staff_attendance (staff_id, direction, punch_time, punch_date) VALUES (?,?,?,?)",
                    (st["staff_id"], "OUT", now_iso, today),
                )
                cleared += 1

        conn.commit()
        return {"ok": True, "cleared": cleared}
    finally:
        conn.close()
