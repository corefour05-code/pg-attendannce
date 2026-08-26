"""Phase 2: authentication routes — root redirect gate, the three login
entry points (lab login, admin login, attendance passcode gate), logout, and
the optional landing chooser page.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from config import ATTENDANCE_PASSCODE
from core.security import verify_password
from db.connection import get_connection

from app.deps import get_session_user, is_main_admin
from app.templating import templates

router = APIRouter()

DEVELOPERS_DIR = Path(__file__).resolve().parent.parent / "static" / "images" / "developers"

# Credits shown from the login page's "Developers" card. Photo is optional —
# a dev without a file yet just gets an initials placeholder in the template.
DEVELOPERS = [
    {"name": "Nivesh Varun M", "batch": "2023-2027", "photo": "nivesh.png", "linkedin": "https://www.linkedin.com/in/nivesh-varun"},
    {"name": "Santhosh B", "batch": "2023-2027", "photo": "santhosh.jpg", "linkedin": "https://www.linkedin.com/in/santhosh-balaji-371a0429a"},
    {"name": "Rohith R", "batch": "2023-2027", "photo": "rohith.jpg", "linkedin": "https://www.linkedin.com/in/rohith-r-it-student"},
]


def _developers_context() -> list[dict]:
    devs = []
    for d in DEVELOPERS:
        has_photo = (DEVELOPERS_DIR / d["photo"]).is_file()
        devs.append({**d, "photo_url": f"/static/images/developers/{d['photo']}" if has_photo else None})
    return devs


def _dispatch_redirect(user: dict) -> RedirectResponse:
    if user["role"] == "admin":
        target = "/students" if is_main_admin(user) else "/report"
    else:
        target = "/scanner"
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
    return templates.TemplateResponse(request, "login.html", {"error": None, "developers": _developers_context()})


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
            request, "login.html", {"error": msg, "developers": _developers_context()}, status_code=400
        )

    if row is None or not verify_password(password, row["password_hash"]):
        return error("Invalid Credentials")

    user = {"user_id": row["id"], "username": row["username"], "role": row["role"], "lab_id": row["lab_id"]}

    request.session["user_id"] = user["user_id"]
    request.session["username"] = user["username"]
    request.session["role"] = user["role"]
    request.session["lab_id"] = user["lab_id"]
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

    user = {"user_id": row["id"], "username": row["username"], "role": row["role"], "lab_id": row["lab_id"]}
    request.session["user_id"] = user["user_id"]
    request.session["username"] = user["username"]
    request.session["role"] = user["role"]
    request.session["lab_id"] = user["lab_id"]
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

    conn = get_connection()
    try:
        lab = conn.execute("SELECT id FROM labs ORDER BY id LIMIT 1").fetchone()
    finally:
        conn.close()

    request.session["user_id"] = 0
    request.session["username"] = "kiosk"
    request.session["role"] = "user"
    request.session["lab_id"] = lab["id"] if lab else None
    return RedirectResponse("/scanner", status_code=302)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)
