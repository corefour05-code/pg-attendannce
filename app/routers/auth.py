"""Authentication routes — root redirect gate, the three login entry points
(admin login, staff login, attendance passcode gate), logout, and the
optional landing chooser page.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from config import ATTENDANCE_PASSCODE
from core.security import verify_password
from db.connection import get_connection

from app.deps import get_developers_context, get_session_user, is_admin
from app.templating import templates

router = APIRouter()


def _dispatch_redirect(user: dict) -> RedirectResponse:
    target = "/residents" if user["role"] == "admin" else "/scanner"
    return RedirectResponse(target, status_code=302)


@router.get("/")
def root(request: Request):
    user = get_session_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=302)
    return _dispatch_redirect(user)


@router.get("/welcome")
def welcome(request: Request):
    return templates.TemplateResponse(request, "welcome.html", {})


@router.get("/login")
def login_get(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None, "developers": get_developers_context()})


@router.post("/login")
def login_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    finally:
        conn.close()

    def error(msg: str):
        return templates.TemplateResponse(
            request, "login.html", {"error": msg, "developers": get_developers_context()}, status_code=400
        )

    if row is None or not verify_password(password, row["password_hash"]):
        return error("Invalid Credentials")

    user = {"user_id": row["id"], "username": row["username"], "role": row["role"]}

    request.session["user_id"] = user["user_id"]
    request.session["username"] = user["username"]
    request.session["role"] = user["role"]
    return _dispatch_redirect(user)


@router.get("/admin_login")
def admin_login_get(request: Request):
    return templates.TemplateResponse(request, "admin_login.html", {"error": None})


@router.post("/admin_login")
def admin_login_post(request: Request, username: str = Form(...), password: str = Form(...)):
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    finally:
        conn.close()

    if row is None or row["role"] != "admin" or not verify_password(password, row["password_hash"]):
        return templates.TemplateResponse(
            request, "admin_login.html", {"error": "Invalid Credentials"}, status_code=400
        )

    user = {"user_id": row["id"], "username": row["username"], "role": row["role"]}
    request.session["user_id"] = user["user_id"]
    request.session["username"] = user["username"]
    request.session["role"] = user["role"]
    return _dispatch_redirect(user)


@router.get("/attendance_login")
def attendance_login_get(request: Request):
    return templates.TemplateResponse(request, "attendance_login.html", {"error": None})


@router.post("/attendance_login")
def attendance_login_post(request: Request, passcode: str = Form(...)):
    if passcode != ATTENDANCE_PASSCODE:
        return templates.TemplateResponse(
            request, "attendance_login.html", {"error": "Incorrect passcode"}, status_code=400
        )

    request.session["user_id"] = 0
    request.session["username"] = "kiosk"
    request.session["role"] = "user"
    return RedirectResponse("/scanner", status_code=302)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)
