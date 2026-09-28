"""Manage Curfew Times: a global schedule of clock times at which every
resident and staff member currently marked IN is auto-marked OUT."""

import re
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.deps import admin_template_context, require_admin
from app.templating import templates
from db.connection import get_connection

router = APIRouter()

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def run_due_curfews() -> None:
    """Check configured curfew_times against the current clock time and, for
    any that match and haven't already fired today, mark every still-IN
    resident and staff member as OUT."""
    now_hhmm = datetime.now().strftime("%H:%M")
    today = date.today().isoformat()
    now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = get_connection()
    try:
        due = conn.execute(
            "SELECT * FROM curfew_times WHERE time=? "
            "AND (last_triggered_date IS NULL OR last_triggered_date != ?)",
            (now_hhmm, today),
        ).fetchall()
        if not due:
            return

        for row in due:
            active_residents = conn.execute(
                "SELECT resident_id, direction FROM attendance a1 "
                "WHERE id = (SELECT MAX(id) FROM attendance a2 WHERE a2.resident_id = a1.resident_id)"
            ).fetchall()

            cleared_res = 0
            for ar in active_residents:
                if ar["direction"].upper() == "IN":
                    conn.execute(
                        "INSERT INTO attendance (resident_id, direction, punch_time, punch_date) VALUES (?,?,?,?)",
                        (ar["resident_id"], "OUT", now_iso, today),
                    )
                    cleared_res += 1

            active_staff = conn.execute(
                "SELECT staff_id, direction FROM staff_attendance sa1 "
                "WHERE id = (SELECT MAX(id) FROM staff_attendance sa2 WHERE sa2.staff_id = sa1.staff_id)"
            ).fetchall()

            cleared_staff = 0
            for st in active_staff:
                if st["direction"].upper() == "IN":
                    conn.execute(
                        "INSERT INTO staff_attendance (staff_id, direction, punch_time, punch_date) VALUES (?,?,?,?)",
                        (st["staff_id"], "OUT", now_iso, today),
                    )
                    cleared_staff += 1

            conn.execute(
                "UPDATE curfew_times SET last_triggered_date=? WHERE id=?", (today, row["id"])
            )
            conn.commit()
            print(
                f"[curfew] {row['time']} triggered: marked "
                f"{cleared_res} resident(s) and {cleared_staff} staff OUT."
            )
    finally:
        conn.close()


@router.get("/curfew-times")
def curfew_times_list(request: Request):
    user, redirect = require_admin(request)
    if redirect:
        return redirect

    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM curfew_times ORDER BY time").fetchall()
    finally:
        conn.close()

    context = {
        **admin_template_context(user),
        "active_nav": "curfew_times",
        "curfew_times": rows,
        "success": request.query_params.get("success"),
        "error": request.query_params.get("error"),
    }
    return templates.TemplateResponse(request, "curfew_times.html", context)


@router.post("/curfew-times/add")
def curfew_time_add(request: Request, time: str = Form(...)):
    user, redirect = require_admin(request)
    if redirect:
        return redirect

    if not _TIME_RE.match(time):
        return RedirectResponse("/curfew-times?error=Invalid time.", status_code=302)

    conn = get_connection()
    try:
        existing = conn.execute("SELECT 1 FROM curfew_times WHERE time=?", (time,)).fetchone()
        if existing:
            return RedirectResponse(
                f"/curfew-times?error={time} is already a configured curfew time.",
                status_code=302,
            )
        conn.execute("INSERT INTO curfew_times (time) VALUES (?)", (time,))
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(f"/curfew-times?success=Added curfew time {time}.", status_code=302)


@router.post("/curfew-times/{curfew_id}/delete")
def curfew_time_delete(request: Request, curfew_id: int):
    user, redirect = require_admin(request)
    if redirect:
        return redirect

    conn = get_connection()
    try:
        conn.execute("DELETE FROM curfew_times WHERE id=?", (curfew_id,))
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse("/curfew-times?success=Curfew time removed.", status_code=302)
