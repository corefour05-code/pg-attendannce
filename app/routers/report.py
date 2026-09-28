"""Filterable Hostel Attendance Report (All movement records) + PDF export."""

import io
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi import APIRouter, Request
from fastapi.responses import Response
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

from app.deps import admin_template_context, require_admin
from app.templating import templates
from core.time_format import format_datetime_time_12h
from db.connection import get_connection

router = APIRouter()

REPORT_COLUMNS = [
    "S.No", "Type", "ID", "Name", "Room No", "Movement", "Time", "Date",
]


def _build_filters(request: Request):
    from_date = request.query_params.get("from_date") or date.today().isoformat()
    to_date = request.query_params.get("to_date") or date.today().isoformat()
    q = request.query_params.get("q", "").strip()
    room_no = request.query_params.get("room_no", "").strip()
    direction = request.query_params.get("direction", "all").strip().upper()
    if direction not in ("ALL", "OUT", "IN"):
        direction = "ALL"

    kind = request.query_params.get("kind", "all").strip().lower()
    if kind not in ("all", "resident", "staff"):
        kind = "all"

    return from_date, to_date, room_no, q, kind, direction


def _fetch_rows(conn, from_date, to_date, room_no, q, kind, direction):
    """Unified resident + staff movement rows."""
    resident_sql = (
        "SELECT 'resident' AS kind, r.resident_id AS person_id, r.name AS name, "
        "r.room_no AS room_no, a.direction AS direction, a.punch_time, a.punch_date "
        "FROM attendance a "
        "JOIN residents r ON r.resident_id = a.resident_id "
        "WHERE a.punch_date BETWEEN ? AND ?"
    )
    resident_params: list = [from_date, to_date]
    if room_no:
        resident_sql += " AND r.room_no = ?"
        resident_params.append(room_no)
    if q:
        resident_sql += " AND (r.name LIKE ? OR r.resident_id LIKE ?)"
        resident_params.extend([f"%{q}%", f"%{q}%"])
    if direction != "ALL":
        resident_sql += " AND UPPER(a.direction) = ?"
        resident_params.append(direction)

    staff_sql = (
        "SELECT 'staff' AS kind, s.staff_id AS person_id, s.name AS name, "
        "s.designation AS room_no, sa.direction AS direction, sa.punch_time, sa.punch_date "
        "FROM staff_attendance sa "
        "JOIN staff s ON s.staff_id = sa.staff_id "
        "WHERE sa.punch_date BETWEEN ? AND ?"
    )
    staff_params: list = [from_date, to_date]
    if q:
        staff_sql += " AND (s.name LIKE ? OR s.staff_id LIKE ?)"
        staff_params.extend([f"%{q}%", f"%{q}%"])
    if direction != "ALL":
        staff_sql += " AND UPPER(sa.direction) = ?"
        staff_params.append(direction)

    if kind == "resident":
        sql, params = resident_sql, resident_params
    elif kind == "staff":
        sql, params = staff_sql, staff_params
    else:
        sql = f"{resident_sql} UNION ALL {staff_sql}"
        params = resident_params + staff_params

    sql += " ORDER BY punch_time DESC"
    return conn.execute(sql, params).fetchall()


@router.get("/report")
def attendance_report(request: Request):
    user, redirect = require_admin(request)
    if redirect:
        return redirect

    from_date, to_date, room_no, q, kind, direction = _build_filters(request)

    conn = get_connection()
    try:
        rows = _fetch_rows(conn, from_date, to_date, room_no, q, kind, direction)
    finally:
        conn.close()

    unique_residents = len({r["person_id"] for r in rows if r["kind"] == "resident"})
    unique_staff = len({r["person_id"] for r in rows if r["kind"] == "staff"})

    context = {
        **admin_template_context(user),
        "active_nav": "report",
        "rows": rows,
        "from_date": from_date,
        "to_date": to_date,
        "room_no": room_no,
        "q": q,
        "kind": kind,
        "direction": direction,
        "query_string": request.url.query,
        "total_records": len(rows),
        "unique_residents": unique_residents,
        "unique_staff": unique_staff,
    }
    return templates.TemplateResponse(request, "report.html", context)


@router.get("/report/pdf")
def attendance_report_pdf(request: Request):
    user, redirect = require_admin(request)
    if redirect:
        return redirect

    from_date, to_date, room_no, q, kind, direction = _build_filters(request)

    conn = get_connection()
    try:
        rows = _fetch_rows(conn, from_date, to_date, room_no, q, kind, direction)
    finally:
        conn.close()

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4))
    styles = getSampleStyleSheet()

    data = [REPORT_COLUMNS]
    for i, r in enumerate(rows, start=1):
        data.append([
            str(i),
            r["kind"].capitalize(),
            r["person_id"],
            r["name"],
            r["room_no"] or "-",
            r["direction"].upper(),
            format_datetime_time_12h(r["punch_time"]),
            r["punch_date"] or "-",
        ])

    elements = [
        Paragraph(f"Hostel Movement & Attendance Report ({from_date} to {to_date})", styles["Title"]),
    ]
    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#077c3c")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8F9FA")]),
    ]))
    elements.append(table)
    doc.build(elements)

    pdf_bytes = buffer.getvalue()
    buffer.close()
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=hostel_movement_report_{from_date}_to_{to_date}.pdf"},
    )
