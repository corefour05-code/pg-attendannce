"""Phase 5: Faculty directory (CRUD + face photos on file). No attendance
wiring — faculty are not part of the scanner/recognition matching pool."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import numpy as np
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app import state
from app.deps import admin_template_context, require_main_admin
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


def _save_faculty_embeddings(conn, faculty_id: str, data_urls: list[str]) -> int:
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
            "INSERT INTO faculty_embeddings (faculty_id, embedding, angle_label) VALUES (?,?,?)",
            (faculty_id, embedding.tobytes(), f"shot_{saved + 1}"),
        )
        saved += 1
    return saved


def _rename_faculty_id(conn, old_faculty_id: str, new_faculty_id: str) -> None:
    """Change a faculty member's primary key and repoint their embeddings/
    attendance rows to match — same PRAGMA foreign_keys=OFF technique used for
    student roll_no renames (see students.py's _rename_roll_no)."""
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute(
            "UPDATE faculty SET faculty_id=? WHERE faculty_id=?", (new_faculty_id, old_faculty_id)
        )
        conn.execute(
            "UPDATE faculty_embeddings SET faculty_id=? WHERE faculty_id=?",
            (new_faculty_id, old_faculty_id),
        )
        conn.execute(
            "UPDATE faculty_attendance SET faculty_id=? WHERE faculty_id=?",
            (new_faculty_id, old_faculty_id),
        )
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def _faculty_list_response(request: Request, user, intent: str | None, active_nav: str, list_path: str):
    q = request.query_params.get("q", "").strip()
    conn = get_connection()
    try:
        if q:
            like = f"%{q}%"
            rows = conn.execute(
                "SELECT * FROM faculty WHERE faculty_id LIKE ? OR name LIKE ? ORDER BY faculty_id",
                (like, like),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM faculty ORDER BY faculty_id").fetchall()
        total_faculty = conn.execute("SELECT COUNT(*) c FROM faculty").fetchone()["c"]
    finally:
        conn.close()

    dept_counts: dict[str, int] = {}
    for r in rows:
        dept = r["department"] or "Unassigned"
        dept_counts[dept] = dept_counts.get(dept, 0) + 1

    context = {
        **admin_template_context(user),
        "active_nav": active_nav,
        "faculty": rows,
        "q": q,
        "intent": intent,
        "list_path": list_path,
        "success": request.query_params.get("success"),
        "error": request.query_params.get("error"),
        "total_faculty": total_faculty,
        "filtered_count": len(rows),
        "dept_counts": sorted(dept_counts.items()),
    }
    return templates.TemplateResponse(request, "faculty_list.html", context)


@router.get("/faculty/check-id")
def check_faculty_id(request: Request):
    """Live duplicate check used by the Add Faculty form. Unlike students,
    faculty_id duplicates are hard-rejected server-side on submit (no upsert),
    so this just warns early instead of letting the admin capture 5 photos
    and only then discover the ID is taken."""
    user, redirect = require_main_admin(request)
    if redirect:
        return JSONResponse({"exists": False}, status_code=403)

    faculty_id = request.query_params.get("faculty_id", "").strip()
    if not faculty_id:
        return {"exists": False}

    conn = get_connection()
    try:
        row = conn.execute("SELECT name FROM faculty WHERE faculty_id=?", (faculty_id,)).fetchone()
    finally:
        conn.close()

    if row is None:
        return {"exists": False}
    return {"exists": True, "name": row["name"]}


@router.get("/faculty")
def faculty_list(request: Request):
    user, redirect = require_main_admin(request)
    if redirect:
        return redirect
    return _faculty_list_response(request, user, None, "faculty", "/faculty")


@router.get("/faculty/update")
def faculty_list_update(request: Request):
    user, redirect = require_main_admin(request)
    if redirect:
        return redirect
    return _faculty_list_response(request, user, "update", "faculty_update", "/faculty/update")


@router.get("/faculty/delete")
def faculty_list_delete(request: Request):
    user, redirect = require_main_admin(request)
    if redirect:
        return redirect
    return _faculty_list_response(request, user, "delete", "faculty_delete", "/faculty/delete")


@router.get("/faculty/add")
def faculty_add_form(request: Request):
    user, redirect = require_main_admin(request)
    if redirect:
        return redirect
    context = {
        **admin_template_context(user),
        "active_nav": "faculty_add",
        "mode": "add",
        "faculty": None,
    }
    return templates.TemplateResponse(request, "faculty_form.html", context)


@router.post("/faculty/add")
async def faculty_add_submit(request: Request):
    user, redirect = require_main_admin(request)
    if redirect:
        return redirect

    form = await request.form()
    faculty_id = form.get("faculty_id", "").strip()
    if not faculty_id:
        return RedirectResponse("/faculty/add?error=Faculty ID is required", status_code=302)

    name = form.get("name", "").strip()
    department = form.get("department", "").strip() or None
    designation = form.get("designation", "").strip() or None
    role = form.get("role", "faculty").strip()
    if role not in ("faculty", "technician"):
        role = "faculty"
    photo_urls = _collect_photo_data_urls(form)

    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT faculty_id FROM faculty WHERE faculty_id=?", (faculty_id,)
        ).fetchone()
        if existing:
            return RedirectResponse(
                f"/faculty/add?error=Faculty ID '{faculty_id}' already exists", status_code=302
            )
        conn.execute(
            "INSERT INTO faculty (faculty_id, name, department, designation, role) VALUES (?,?,?,?,?)",
            (faculty_id, name, department or "", designation, role),
        )
        saved = _save_faculty_embeddings(conn, faculty_id, photo_urls)
        conn.commit()
    finally:
        conn.close()

    if saved:
        state.get_recognizer().reload_embeddings()

    return RedirectResponse(
        f"/faculty?success=Added {faculty_id} with {saved} face photos.", status_code=302
    )


@router.get("/faculty/{faculty_id}/edit")
def faculty_edit_form(request: Request, faculty_id: str):
    user, redirect = require_main_admin(request)
    if redirect:
        return redirect

    conn = get_connection()
    try:
        fac = conn.execute("SELECT * FROM faculty WHERE faculty_id=?", (faculty_id,)).fetchone()
        embedding_count = conn.execute(
            "SELECT COUNT(*) c FROM faculty_embeddings WHERE faculty_id=?", (faculty_id,)
        ).fetchone()["c"]
    finally:
        conn.close()

    if fac is None:
        return RedirectResponse("/faculty?error=Faculty not found", status_code=302)

    context = {
        **admin_template_context(user),
        "active_nav": "faculty_update",
        "mode": "edit",
        "faculty": fac,
        "embedding_count": embedding_count,
    }
    return templates.TemplateResponse(request, "faculty_form.html", context)


@router.post("/faculty/{faculty_id}/edit")
async def faculty_edit_submit(request: Request, faculty_id: str):
    user, redirect = require_main_admin(request)
    if redirect:
        return redirect

    form = await request.form()
    name = form.get("name", "").strip()
    department = form.get("department", "").strip() or None
    designation = form.get("designation", "").strip() or None
    role = form.get("role", "faculty").strip()
    if role not in ("faculty", "technician"):
        role = "faculty"

    new_faculty_id = form.get("faculty_id", faculty_id).strip()
    if not new_faculty_id:
        return RedirectResponse(
            f"/faculty/{faculty_id}/edit?error=Faculty ID is required", status_code=302
        )

    photo_urls = _collect_photo_data_urls(form)

    conn = get_connection()
    try:
        if new_faculty_id != faculty_id:
            collision = conn.execute(
                "SELECT faculty_id FROM faculty WHERE faculty_id=?", (new_faculty_id,)
            ).fetchone()
            if collision:
                return RedirectResponse(
                    f"/faculty/{faculty_id}/edit?error=ID {new_faculty_id} is already used by "
                    "another faculty member.",
                    status_code=302,
                )
            _rename_faculty_id(conn, faculty_id, new_faculty_id)

        conn.execute(
            "UPDATE faculty SET name=?, department=?, designation=?, role=? WHERE faculty_id=?",
            (name, department, designation, role, new_faculty_id),
        )
        saved = _save_faculty_embeddings(conn, new_faculty_id, photo_urls) if photo_urls else 0
        conn.commit()
    finally:
        conn.close()

    if photo_urls or new_faculty_id != faculty_id:
        state.get_recognizer().reload_embeddings()

    msg = f"Updated {new_faculty_id}."
    if new_faculty_id != faculty_id:
        msg = f"Renamed {faculty_id} to {new_faculty_id}."
    msg += f" Added {saved} new face photos." if photo_urls else ""
    return RedirectResponse(f"/faculty?success={msg}", status_code=302)


@router.delete("/faculty/{faculty_id}")
def faculty_delete(request: Request, faculty_id: str):
    user, redirect = require_main_admin(request)
    if redirect:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=403)

    conn = get_connection()
    try:
        conn.execute("DELETE FROM faculty WHERE faculty_id=?", (faculty_id,))
        conn.commit()
    finally:
        conn.close()

    return {"ok": True}
