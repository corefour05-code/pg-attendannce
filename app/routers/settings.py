"""Manage Settings: hostel name + the gate password required to bulk-mark
everyone OUT from the Scanner page. Single row (id=1) in the settings table —
replaces the old per-lab Lab Security Configuration page now that there's
only one gate. Admin only."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from db.connection import get_connection

from app.deps import admin_template_context, require_admin
from app.templating import templates

router = APIRouter()


def get_settings(conn):
    return conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()


@router.get("/settings")
def settings_page(request: Request):
    user, redirect = require_admin(request)
    if redirect:
        return redirect
    conn = get_connection()
    try:
        settings = get_settings(conn)
    finally:
        conn.close()
    context = {**admin_template_context(user), "active_nav": "settings", "settings": settings}
    return templates.TemplateResponse(request, "settings.html", context)


@router.post("/settings")
def settings_save(
    request: Request,
    hostel_name: str = Form(...),
    gate_password: str = Form(...),
):
    user, redirect = require_admin(request)
    if redirect:
        return redirect
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE settings SET hostel_name=?, gate_password=? WHERE id=1",
            (hostel_name.strip(), gate_password.strip()),
        )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse("/settings?success=Saved", status_code=302)
