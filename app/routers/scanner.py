"""Phase 6: standalone live scanner page + attendance marking (IN on
recognition, bulk OUT via Clear Lab). Matches both students and faculty
against the same live camera feed, marking each into its own attendance
table."""

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
from config import DEDUP_WINDOW_MINUTES
from core.time_format import format_datetime_time_12h
from db.connection import get_connection

router = APIRouter()


def _current_period_id(conn) -> int | None:
    now = datetime.now().strftime("%H:%M")
    row = conn.execute(
        "SELECT id FROM periods WHERE start_time<=? AND end_time>=? LIMIT 1",
        (now, now),
    ).fetchone()
    return row["id"] if row else None


def _toggle_attendance(
    conn, table: str, id_col: str, identity: str, lab_id: int, today: str, period_id: int | None
) -> tuple[str, bool]:
    """Shared IN/OUT dedup toggle, used for both the student `attendance`
    table and the `faculty_attendance` table (same shape, different owner
    column). `table`/`id_col` are always one of two hardcoded literals from
    call sites below, never user input."""
    last = conn.execute(
        f"SELECT * FROM {table} WHERE {id_col}=? AND lab_id=? AND session_date=? "
        "ORDER BY id DESC LIMIT 1",
        (identity, lab_id, today),
    ).fetchone()

    now = datetime.now()
    now_str = now.isoformat(sep=" ", timespec="seconds")

    if last is None:
        # first scan today -> mark IN
        conn.execute(
            f"INSERT INTO {table} ({id_col}, lab_id, session_date, in_time, in_period_id) "
            "VALUES (?,?,?,?,?)",
            (identity, lab_id, today, now_str, period_id),
        )
        conn.commit()
        return "in", True

    if last["out_time"] is None:
        # currently IN -> only flip to OUT once the dedup window has passed,
        # otherwise a rescan within the window is treated as accidental
        in_dt = datetime.fromisoformat(last["in_time"])
        if now - in_dt >= timedelta(minutes=DEDUP_WINDOW_MINUTES):
            conn.execute(
                f"UPDATE {table} SET out_time=?, out_date=?, out_period_id=? WHERE id=?",
                (now_str, today, period_id, last["id"]),
            )
            conn.commit()
            return "out", True
        return "in", False

    # currently OUT -> a rescan after the dedup window starts a fresh IN
    # session (re-entering the lab); within the window it's a no-op.
    out_dt = datetime.fromisoformat(last["out_time"])
    if now - out_dt >= timedelta(minutes=DEDUP_WINDOW_MINUTES):
        conn.execute(
            f"INSERT INTO {table} ({id_col}, lab_id, session_date, in_time, in_period_id) "
            "VALUES (?,?,?,?,?)",
            (identity, lab_id, today, now_str, period_id),
        )
        conn.commit()
        return "in", True
    return "out", False


@router.get("/scanner")
def scanner_page(request: Request):
    user, redirect = require_login(request)
    if redirect:
        return redirect

    lab_name = "All Labs"
    if user["lab_id"] is not None:
        conn = get_connection()
        try:
            row = conn.execute("SELECT name FROM labs WHERE id=?", (user["lab_id"],)).fetchone()
            lab_name = row["name"] if row else "Unknown Lab"
        finally:
            conn.close()

    return templates.TemplateResponse(request, "scanner.html", {"lab_name": lab_name})


@router.post("/api/scan")
async def scan(request: Request, image: UploadFile = File(...)):
    user, redirect = require_login(request)
    if redirect:
        return {"faces": [], "name": None, "error": "not logged in"}
    lab_id = user["lab_id"]

    recognizer = state.get_recognizer()
    data = await image.read()
    arr = np.frombuffer(data, dtype=np.uint8)
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if frame is None:
        return {"faces": [], "name": None}

    # Detection/recognition always runs so the box + name show up even without
    # a single lab context (e.g. a super-admin browsing with lab_id=None).
    results, _timing = recognizer.recognize(frame)
    matched = next((r for r in results if r["identity"] != "unknown"), None)

    if matched is None:
        return {"faces": results, "name": None}

    kind, identity = matched["kind"], matched["identity"]

    conn = get_connection()
    try:
        if kind == "student":
            person = conn.execute("SELECT name FROM students WHERE roll_no=?", (identity,)).fetchone()
        else:
            person = conn.execute(
                "SELECT name, role FROM faculty WHERE faculty_id=?", (identity,)
            ).fetchone()

        if person is None:
            return {"faces": results, "name": None}

        if kind == "student":
            display_name = person["name"]
        else:
            role_label = "Technician" if person["role"] == "technician" else "Faculty"
            display_name = f"{role_label} ({person['name']})"

        if lab_id is None:
            # No single lab to log attendance against — report who was recognized
            # but don't touch the attendance tables.
            return {
                "faces": results, "name": display_name, "identity": identity,
                "kind": kind, "marked": False,
            }

        today = date.today().isoformat()
        period_id = _current_period_id(conn)

        table, id_col = ("attendance", "roll_no") if kind == "student" else ("faculty_attendance", "faculty_id")
        status, changed = _toggle_attendance(conn, table, id_col, identity, lab_id, today, period_id)

        return {
            "faces": results,
            "name": display_name,
            "identity": identity,
            "kind": kind,
            "status": status,
            "marked": changed,
        }
    finally:
        conn.close()


@router.get("/api/scanner/logs")
def scanner_logs(request: Request):
    user, redirect = require_login(request)
    if redirect:
        return {"rows": []}
    lab_id = user["lab_id"]
    if lab_id is None:
        return {"rows": []}

    today = date.today().isoformat()
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT s.name AS name, s.department AS department, s.section AS section, s.batch AS batch, "
            "a.in_time, pin.period_name AS period_in, a.session_date AS in_date, "
            "a.out_time, pout.period_name AS period_out, a.out_date "
            "FROM attendance a "
            "JOIN students s ON s.roll_no = a.roll_no "
            "LEFT JOIN periods pin ON pin.id = a.in_period_id "
            "LEFT JOIN periods pout ON pout.id = a.out_period_id "
            "WHERE a.lab_id=? AND a.session_date=? "
            "UNION ALL "
            "SELECT (CASE WHEN f.role='technician' THEN 'Technician (' ELSE 'Faculty (' END) || f.name || ')' "
            "AS name, f.department AS department, "
            "NULL AS section, f.designation AS batch, "
            "fa.in_time, pin.period_name AS period_in, fa.session_date AS in_date, "
            "fa.out_time, pout.period_name AS period_out, fa.out_date "
            "FROM faculty_attendance fa "
            "JOIN faculty f ON f.faculty_id = fa.faculty_id "
            "LEFT JOIN periods pin ON pin.id = fa.in_period_id "
            "LEFT JOIN periods pout ON pout.id = fa.out_period_id "
            "WHERE fa.lab_id=? AND fa.session_date=? "
            "ORDER BY in_time DESC",
            (lab_id, today, lab_id, today),
        ).fetchall()

        # Distinct headcount for today: unique students + unique faculty who
        # scanned in at least once, regardless of how many IN/OUT toggles
        # they triggered (re-entering the lab later doesn't inflate the count).
        count_row = conn.execute(
            "SELECT "
            "(SELECT COUNT(DISTINCT roll_no) FROM attendance WHERE lab_id=? AND session_date=?) + "
            "(SELECT COUNT(DISTINCT faculty_id) FROM faculty_attendance WHERE lab_id=? AND session_date=?) "
            "AS total",
            (lab_id, today, lab_id, today),
        ).fetchone()
    finally:
        conn.close()

    formatted = []
    for r in rows:
        row = dict(r)
        row["in_time"] = format_datetime_time_12h(row["in_time"])
        row["out_time"] = format_datetime_time_12h(row["out_time"])
        formatted.append(row)

    return {"rows": formatted, "count": count_row["total"] if count_row else 0}


@router.post("/api/scanner/clear")
def scanner_clear(request: Request, password: str = Form(...)):
    user, redirect = require_login(request)
    if redirect:
        return {"ok": False, "error": "not logged in"}
    lab_id = user["lab_id"]
    if lab_id is None:
        return {"ok": False, "error": "no lab context"}

    conn = get_connection()
    try:
        lab = conn.execute("SELECT clear_lab_password FROM labs WHERE id=?", (lab_id,)).fetchone()
        if lab is None or password != lab["clear_lab_password"]:
            return {"ok": False, "error": "Incorrect password"}

        today = date.today().isoformat()
        now_iso = datetime.now().isoformat(sep=" ", timespec="seconds")
        period_id = _current_period_id(conn)
        cur_students = conn.execute(
            "UPDATE attendance SET out_time=?, out_date=?, out_period_id=? "
            "WHERE lab_id=? AND session_date=? AND out_time IS NULL",
            (now_iso, today, period_id, lab_id, today),
        )
        cur_faculty = conn.execute(
            "UPDATE faculty_attendance SET out_time=?, out_date=?, out_period_id=? "
            "WHERE lab_id=? AND session_date=? AND out_time IS NULL",
            (now_iso, today, period_id, lab_id, today),
        )
        conn.commit()
        return {"ok": True, "cleared": cur_students.rowcount + cur_faculty.rowcount}
    finally:
        conn.close()
