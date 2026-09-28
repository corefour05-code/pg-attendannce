"""Staff directory (wardens/security) CRUD + face photos on file."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import numpy as np
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app import state
from app.deps import admin_template_context, require_admin
from app.templating import templates
from core.image_utils import decode_data_url
from db.connection import get_connection

router = APIRouter()


def _collect_photo_data_urls(form) -> list[str]:
    urls = []
    i = 1
    while True:
        val = form.get(f"photo_{i}")
        if not val:
            break
        urls.append(val)
        i += 1
    return urls


def _save_staff_embeddings(conn, staff_id: str, data_urls: list[str]) -> int:
    """Each data URL already passed /api/capture/validate client-side, so we
    only re-detect the face here to extract its embedding — re-running the
    size/blur gate against an independently re-encoded JPEG would sometimes
    fail on borderline shots and silently drop an already-approved photo."""
    recognizer = state.get_recognizer()
    saved = 0
    for data_url in data_urls:
        try:
            frame = decode_data_url(data_url)
        except ValueError:
            continue
        faces = recognizer.app.get(frame)
        if not faces:
            continue
        face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
        embedding = face.normed_embedding.astype(np.float32)
        conn.execute(
            "INSERT INTO staff_embeddings (staff_id, embedding, angle_label) VALUES (?,?,?)",
            (staff_id, embedding.tobytes(), f"shot_{saved + 1}"),
        )
        saved += 1
    return saved


def _rename_staff_id(conn, old_staff_id: str, new_staff_id: str) -> None:
    """Change a staff member's primary key and repoint their embeddings/
    attendance rows to match — same PRAGMA foreign_keys=OFF technique used for
    resident_id renames (see residents.py's _rename_resident_id)."""
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("UPDATE staff SET staff_id=? WHERE staff_id=?", (new_staff_id, old_staff_id))
        conn.execute(
            "UPDATE staff_embeddings SET staff_id=? WHERE staff_id=?", (new_staff_id, old_staff_id)
        )
        conn.execute(
            "UPDATE staff_attendance SET staff_id=? WHERE staff_id=?", (new_staff_id, old_staff_id)
        )
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def _staff_list_response(request: Request, user, intent: str | None, active_nav: str, list_path: str):
    q = request.query_params.get("q", "").strip()
    conn = get_connection()
    try:
        if q:
            like = f"%{q}%"
            rows = conn.execute(
                "SELECT * FROM staff WHERE staff_id LIKE ? OR name LIKE ? ORDER BY staff_id",
                (like, like),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM staff ORDER BY staff_id").fetchall()
        total_staff = conn.execute("SELECT COUNT(*) c FROM staff").fetchone()["c"]
    finally:
        conn.close()

    role_counts: dict[str, int] = {}
    for r in rows:
        role = "Security" if r["role"] == "security" else "Warden"
        role_counts[role] = role_counts.get(role, 0) + 1

    context = {
        **admin_template_context(user),
        "active_nav": active_nav,
        "staff": rows,
        "q": q,
        "intent": intent,
        "list_path": list_path,
        "success": request.query_params.get("success"),
        "error": request.query_params.get("error"),
        "total_staff": total_staff,
        "filtered_count": len(rows),
        "role_counts": sorted(role_counts.items()),
    }
    return templates.TemplateResponse(request, "staff_list.html", context)


@router.get("/staff/check-id")
def check_staff_id(request: Request):
    """Live duplicate check used by the Add Staff form. Duplicates are hard-
    rejected server-side on submit (no upsert), so this just warns early
    instead of letting the admin capture 5 photos and only then discover the
    ID is taken."""
    user, redirect = require_admin(request)
    if redirect:
        return JSONResponse({"exists": False}, status_code=403)

    staff_id = request.query_params.get("staff_id", "").strip()
    if not staff_id:
        return {"exists": False}

    conn = get_connection()
    try:
        row = conn.execute("SELECT name FROM staff WHERE staff_id=?", (staff_id,)).fetchone()
    finally:
        conn.close()

    if row is None:
        return {"exists": False}
    return {"exists": True, "name": row["name"]}


@router.get("/staff")
def staff_list(request: Request):
    user, redirect = require_admin(request)
    if redirect:
        return redirect
    return _staff_list_response(request, user, None, "staff", "/staff")


@router.get("/staff/update")
def staff_list_update(request: Request):
    user, redirect = require_admin(request)
    if redirect:
        return redirect
    return _staff_list_response(request, user, "update", "staff_update", "/staff/update")


@router.get("/staff/delete")
def staff_list_delete(request: Request):
    user, redirect = require_admin(request)
    if redirect:
        return redirect
    return _staff_list_response(request, user, "delete", "staff_delete", "/staff/delete")


@router.get("/staff/add")
def staff_add_form(request: Request):
    user, redirect = require_admin(request)
    if redirect:
        return redirect
    context = {
        **admin_template_context(user),
        "active_nav": "staff_add",
        "mode": "add",
        "staff": None,
    }
    return templates.TemplateResponse(request, "staff_form.html", context)


@router.post("/staff/add")
async def staff_add_submit(request: Request):
    user, redirect = require_admin(request)
    if redirect:
        return redirect

    form = await request.form()
    staff_id = form.get("staff_id", "").strip()
    if not staff_id:
        return RedirectResponse("/staff/add?error=Staff ID is required", status_code=302)

    name = form.get("name", "").strip()
    phone = form.get("phone", "").strip() or None
    designation = form.get("designation", "").strip() or None
    role = form.get("role", "warden").strip()
    if role not in ("warden", "security"):
        role = "warden"
    photo_urls = _collect_photo_data_urls(form)

    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT staff_id FROM staff WHERE staff_id=?", (staff_id,)
        ).fetchone()
        if existing:
            return RedirectResponse(
                f"/staff/add?error=Staff ID '{staff_id}' already exists", status_code=302
            )
        conn.execute(
            "INSERT INTO staff (staff_id, name, phone, designation, role) VALUES (?,?,?,?,?)",
            (staff_id, name, phone, designation, role),
        )
        saved = _save_staff_embeddings(conn, staff_id, photo_urls)
        conn.commit()
    finally:
        conn.close()

    if saved:
        state.get_recognizer().reload_embeddings()

    return RedirectResponse(
        f"/staff?success=Added {staff_id} with {saved} face photos.", status_code=302
    )


@router.get("/staff/{staff_id}/edit")
def staff_edit_form(request: Request, staff_id: str):
    user, redirect = require_admin(request)
    if redirect:
        return redirect

    conn = get_connection()
    try:
        member = conn.execute("SELECT * FROM staff WHERE staff_id=?", (staff_id,)).fetchone()
        embedding_count = conn.execute(
            "SELECT COUNT(*) c FROM staff_embeddings WHERE staff_id=?", (staff_id,)
        ).fetchone()["c"]
    finally:
        conn.close()

    if member is None:
        return RedirectResponse("/staff?error=Staff member not found", status_code=302)

    context = {
        **admin_template_context(user),
        "active_nav": "staff_update",
        "mode": "edit",
        "staff": member,
        "embedding_count": embedding_count,
    }
    return templates.TemplateResponse(request, "staff_form.html", context)


@router.post("/staff/{staff_id}/edit")
async def staff_edit_submit(request: Request, staff_id: str):
    user, redirect = require_admin(request)
    if redirect:
        return redirect

    form = await request.form()
    name = form.get("name", "").strip()
    phone = form.get("phone", "").strip() or None
    designation = form.get("designation", "").strip() or None
    role = form.get("role", "warden").strip()
    if role not in ("warden", "security"):
        role = "warden"

    new_staff_id = form.get("staff_id", staff_id).strip()
    if not new_staff_id:
        return RedirectResponse(
            f"/staff/{staff_id}/edit?error=Staff ID is required", status_code=302
        )

    photo_urls = _collect_photo_data_urls(form)

    conn = get_connection()
    try:
        if new_staff_id != staff_id:
            collision = conn.execute(
                "SELECT staff_id FROM staff WHERE staff_id=?", (new_staff_id,)
            ).fetchone()
            if collision:
                return RedirectResponse(
                    f"/staff/{staff_id}/edit?error=ID {new_staff_id} is already used by "
                    "another staff member.",
                    status_code=302,
                )
            _rename_staff_id(conn, staff_id, new_staff_id)

        conn.execute(
            "UPDATE staff SET name=?, phone=?, designation=?, role=? WHERE staff_id=?",
            (name, phone, designation, role, new_staff_id),
        )
        saved = _save_staff_embeddings(conn, new_staff_id, photo_urls) if photo_urls else 0
        conn.commit()
    finally:
        conn.close()

    if photo_urls or new_staff_id != staff_id:
        state.get_recognizer().reload_embeddings()

    msg = f"Updated {new_staff_id}."
    if new_staff_id != staff_id:
        msg = f"Renamed {staff_id} to {new_staff_id}."
    msg += f" Added {saved} new face photos." if photo_urls else ""
    return RedirectResponse(f"/staff?success={msg}", status_code=302)


@router.delete("/staff/{staff_id}")
def staff_delete(request: Request, staff_id: str):
    user, redirect = require_admin(request)
    if redirect:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=403)

    conn = get_connection()
    try:
        conn.execute("DELETE FROM staff WHERE staff_id=?", (staff_id,))
        conn.commit()
    finally:
        conn.close()

    return {"ok": True}
