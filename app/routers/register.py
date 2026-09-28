"""Public self-registration: residents enter their Name and Room Number ahead of time.
An auto-increment Resident ID (RES-001, RES-002...) is automatically assigned.
Warden/staff later capture their face photos in the admin portal."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.templating import templates
from db.connection import get_connection
from enrollment.enroll import get_next_resident_id

router = APIRouter()


@router.get("/register")
def register_form(request: Request):
    conn = get_connection()
    try:
        suggested_id = get_next_resident_id(conn)
    finally:
        conn.close()

    return templates.TemplateResponse(request, "register.html", {
        "suggested_id": suggested_id,
        "success": request.query_params.get("success"),
        "error": request.query_params.get("error"),
    })


@router.get("/register/next-id")
def register_next_id(request: Request):
    conn = get_connection()
    try:
        next_id = get_next_resident_id(conn)
    finally:
        conn.close()
    return JSONResponse({"next_id": next_id})


@router.post("/register")
async def register_submit(request: Request):
    form = await request.form()
    name = form.get("name", "").strip()
    room_no = form.get("room_no", "").strip()

    if not name:
        return RedirectResponse("/register?error=Name is required", status_code=302)
    if not room_no:
        return RedirectResponse("/register?error=Room number is required", status_code=302)

    conn = get_connection()
    try:
        resident_id = get_next_resident_id(conn)
        conn.execute(
            "INSERT INTO residents (resident_id, name, room_no) VALUES (?,?,?)",
            (resident_id, name, room_no),
        )
        conn.commit()
    finally:
        conn.close()

    return RedirectResponse(
        f"/register?success=Registered successfully! Your Resident ID is {resident_id}. "
        "Please visit the warden's office to complete your face capture.",
        status_code=302,
    )
