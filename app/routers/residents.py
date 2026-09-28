"""Residents Info list, Add/Edit/Delete with face-photo capture."""

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
from enrollment.enroll import get_next_resident_id, upsert_resident, validate_resident_id

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


def _save_embeddings(conn, resident_id: str, data_urls: list[str]) -> int:
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
            "INSERT INTO embeddings (resident_id, embedding, angle_label) VALUES (?,?,?)",
            (resident_id, embedding.tobytes(), f"recapture_{saved + 1}"),
        )
        saved += 1
    return saved


def _rename_resident_id(conn, old_resident_id: str, new_resident_id: str) -> None:
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute(
            "UPDATE residents SET resident_id=? WHERE resident_id=?", (new_resident_id, old_resident_id)
        )
        conn.execute(
            "UPDATE embeddings SET resident_id=? WHERE resident_id=?", (new_resident_id, old_resident_id)
        )
        conn.execute(
            "UPDATE attendance SET resident_id=? WHERE resident_id=?", (new_resident_id, old_resident_id)
        )
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def _residents_list_response(request: Request, user, intent: str | None, active_nav: str, list_path: str):
    q = request.query_params.get("q", "").strip()

    sql = "SELECT * FROM residents WHERE archived_at IS NULL"
    params: list = []
    if q:
        sql += " AND (resident_id LIKE ? OR name LIKE ? OR room_no LIKE ?)"
        params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
    sql += " ORDER BY room_no, resident_id"

    conn = get_connection()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    context = {
        **admin_template_context(user),
        "active_nav": active_nav,
        "residents": rows,
        "q": q,
        "intent": intent,
        "list_path": list_path,
        "success": request.query_params.get("success"),
        "error": request.query_params.get("error"),
        "resident_count": len(rows),
    }
    return templates.TemplateResponse(request, "residents_list.html", context)


@router.get("/residents/check-id")
def check_resident_id(request: Request):
    user, redirect = require_admin(request)
    if redirect:
        return JSONResponse({"exists": False}, status_code=403)

    resident_id = request.query_params.get("resident_id", "").strip()
    if not resident_id:
        return {"exists": False}

    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM residents WHERE resident_id=?", (resident_id,)).fetchone()
        embedding_count = 0
        if row is not None:
            embedding_count = conn.execute(
                "SELECT COUNT(*) c FROM embeddings WHERE resident_id=?", (resident_id,)
            ).fetchone()["c"]
    finally:
        conn.close()

    if row is None:
        return {"exists": False}
    return {
        "exists": True,
        "name": row["name"],
        "archived": row["archived_at"] is not None,
        "room_no": row["room_no"] or "",
        "embedding_count": embedding_count,
        "has_embeddings": embedding_count > 0,
    }


@router.get("/residents")
def residents_list(request: Request):
    user, redirect = require_admin(request)
    if redirect:
        return redirect
    return _residents_list_response(request, user, None, "residents", "/residents")


@router.get("/residents/update")
def resident_update_form(request: Request):
    user, redirect = require_admin(request)
    if redirect:
        return redirect
    context = {
        **admin_template_context(user),
        "active_nav": "resident_update",
        "mode": "update",
        "resident": None,
        "embedding_count": 0,
    }
    return templates.TemplateResponse(request, "resident_form.html", context)


@router.get("/residents/delete")
def residents_list_delete(request: Request):
    user, redirect = require_admin(request)
    if redirect:
        return redirect
    return _residents_list_response(request, user, "delete", "resident_delete", "/residents/delete")


@router.get("/residents/add")
def resident_add_form(request: Request):
    user, redirect = require_admin(request)
    if redirect:
        return redirect

    conn = get_connection()
    try:
        suggested_id = get_next_resident_id(conn)
    finally:
        conn.close()

    context = {
        **admin_template_context(user),
        "active_nav": "resident_add",
        "mode": "add",
        "suggested_id": suggested_id,
        "resident": None,
    }
    return templates.TemplateResponse(request, "resident_form.html", context)


@router.post("/residents/add")
async def resident_add_submit(request: Request):
    user, redirect = require_admin(request)
    if redirect:
        return redirect

    form = await request.form()
    conn = get_connection()
    try:
        raw_id = form.get("resident_id", "").strip()
        if raw_id:
            try:
                resident_id = validate_resident_id(raw_id)
            except ValueError as e:
                return RedirectResponse(f"/residents/add?error={e}", status_code=302)
        else:
            resident_id = get_next_resident_id(conn)

        name = form.get("name", "").strip()
        room_no = form.get("room_no", "").strip()

        if not name:
            return RedirectResponse("/residents/add?error=Name is required", status_code=302)
        if not room_no:
            return RedirectResponse("/residents/add?error=Room number is required", status_code=302)

        photo_urls = _collect_photo_data_urls(form)
        if len(photo_urls) < 5:
            return RedirectResponse("/residents/add?error=5 face photos are required", status_code=302)

        existing_embeddings = conn.execute(
            "SELECT COUNT(*) c FROM embeddings WHERE resident_id=?", (resident_id,)
        ).fetchone()["c"]
        if existing_embeddings > 0:
            return RedirectResponse(
                f"/residents/add?error={resident_id} already has face encodings on file — "
                "use Edit to recapture instead.",
                status_code=302,
            )

        upsert_resident(conn, resident_id, name, room_no, "")
        saved = _save_embeddings(conn, resident_id, photo_urls)
        conn.commit()
    finally:
        conn.close()

    state.get_recognizer().reload_embeddings()
    return RedirectResponse(
        f"/residents?success=Added resident {name} ({resident_id}) with {saved} face photos.", status_code=302
    )


@router.get("/residents/{resident_id}/edit")
def resident_edit_form(request: Request, resident_id: str):
    user, redirect = require_admin(request)
    if redirect:
        return redirect

    conn = get_connection()
    try:
        resident = conn.execute("SELECT * FROM residents WHERE resident_id=?", (resident_id,)).fetchone()
        embedding_count = conn.execute(
            "SELECT COUNT(*) c FROM embeddings WHERE resident_id=?", (resident_id,)
        ).fetchone()["c"]
    finally:
        conn.close()

    if resident is None:
        return RedirectResponse("/residents?error=Resident not found", status_code=302)

    context = {
        **admin_template_context(user),
        "active_nav": "resident_update",
        "mode": "edit",
        "resident": resident,
        "embedding_count": embedding_count,
    }
    return templates.TemplateResponse(request, "resident_form.html", context)


@router.post("/residents/{resident_id}/edit")
async def resident_edit_submit(request: Request, resident_id: str):
    user, redirect = require_admin(request)
    if redirect:
        return redirect

    form = await request.form()
    name = form.get("name", "").strip()
    room_no = form.get("room_no", "").strip()

    if not name or not room_no:
        return RedirectResponse(f"/residents/{resident_id}/edit?error=Name and Room No are required", status_code=302)

    try:
        new_resident_id = validate_resident_id(form.get("resident_id", resident_id))
    except ValueError as e:
        return RedirectResponse(f"/residents/{resident_id}/edit?error={e}", status_code=302)

    photo_urls = _collect_photo_data_urls(form)

    conn = get_connection()
    try:
        if new_resident_id != resident_id:
            collision = conn.execute(
                "SELECT resident_id FROM residents WHERE resident_id=?", (new_resident_id,)
            ).fetchone()
            if collision:
                return RedirectResponse(
                    f"/residents/{resident_id}/edit?error=ID {new_resident_id} is already used by "
                    "another resident.",
                    status_code=302,
                )
            _rename_resident_id(conn, resident_id, new_resident_id)

        conn.execute(
            "UPDATE residents SET name=?, room_no=? WHERE resident_id=?",
            (name, room_no, new_resident_id),
        )
        saved = 0
        if photo_urls:
            saved = _save_embeddings(conn, new_resident_id, photo_urls)
        conn.commit()
    finally:
        conn.close()

    if photo_urls or new_resident_id != resident_id:
        state.get_recognizer().reload_embeddings()

    msg = f"Updated resident {new_resident_id} ({name})."
    msg += f" Added {saved} new face photos." if photo_urls else ""
    return RedirectResponse(f"/residents?success={msg}", status_code=302)


@router.post("/residents/{resident_id}/vacate")
def resident_vacate(request: Request, resident_id: str):
    user, redirect = require_admin(request)
    if redirect:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=403)

    conn = get_connection()
    try:
        conn.execute(
            "UPDATE residents SET archived_at=datetime('now','localtime') WHERE resident_id=?",
            (resident_id,),
        )
        conn.execute("DELETE FROM embeddings WHERE resident_id=?", (resident_id,))
        conn.commit()
    finally:
        conn.close()

    state.get_recognizer().reload_embeddings()
    return {"ok": True}


@router.delete("/residents/{resident_id}")
def resident_delete(request: Request, resident_id: str):
    user, redirect = require_admin(request)
    if redirect:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=403)

    conn = get_connection()
    try:
        conn.execute("DELETE FROM residents WHERE resident_id=?", (resident_id,))
        conn.commit()
    finally:
        conn.close()

    state.get_recognizer().reload_embeddings()
    return {"ok": True}


@router.get("/vacated-residents")
def vacated_residents_list(request: Request):
    user, redirect = require_admin(request)
    if redirect:
        return redirect

    q = request.query_params.get("q", "").strip()
    sql = "SELECT * FROM residents WHERE archived_at IS NOT NULL"
    params: list = []
    if q:
        sql += " AND (resident_id LIKE ? OR name LIKE ?)"
        params.extend([f"%{q}%", f"%{q}%"])
    sql += " ORDER BY archived_at DESC"

    conn = get_connection()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    context = {
        **admin_template_context(user),
        "active_nav": "vacated_residents",
        "residents": rows,
        "q": q,
        "success": request.query_params.get("success"),
        "error": request.query_params.get("error"),
    }
    return templates.TemplateResponse(request, "vacated_residents.html", context)


@router.post("/vacated-residents/delete")
async def vacated_residents_delete(request: Request):
    user, redirect = require_admin(request)
    if redirect:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=403)

    from core.security import verify_password

    body = await request.json()
    password = body.get("admin_password", "")
    mode = body.get("mode")
    resident_ids = body.get("resident_ids") or []

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT password_hash FROM users WHERE id=?", (user["user_id"],)
        ).fetchone()
        if row is None or not verify_password(password, row["password_hash"]):
            return JSONResponse(
                {"ok": False, "error": "Incorrect admin password."}, status_code=403
            )

        if mode == "all":
            cur = conn.execute("DELETE FROM residents WHERE archived_at IS NOT NULL")
        elif mode == "selected" and resident_ids:
            placeholders = ",".join("?" * len(resident_ids))
            cur = conn.execute(
                f"DELETE FROM residents WHERE archived_at IS NOT NULL AND resident_id IN ({placeholders})",
                resident_ids,
            )
        else:
            return JSONResponse(
                {"ok": False, "error": "Nothing selected to delete."}, status_code=400
            )

        deleted = cur.rowcount
        conn.commit()
    finally:
        conn.close()

    if deleted:
        state.get_recognizer().reload_embeddings()

    return {"ok": True, "deleted": deleted}
