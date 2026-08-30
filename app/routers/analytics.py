"""Analytics: classify attendance into "scheduled lab class" vs
"individual/project" based on headcount, and summarize by period/lab for
charting. Purely a read-side view over existing attendance data — no new
tables, nothing persisted.

Classification: for a given date, period, and lab, group student attendance
by (year, section). Any group with >= threshold distinct students is a
detected class for that year/section; everyone else in that same
date+period+lab bucket (any group under threshold, or a lone scan) is
individual/project attendance. Faculty are summarized separately since
section/year don't apply to them."""

import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi import APIRouter, Request

from app.deps import admin_template_context, get_developers_context, require_main_admin
from app.templating import templates
from config import ANALYTICS_SESSION_GAP_MINUTES, LAB_CLASS_MIN_STUDENTS
from db.connection import get_connection

router = APIRouter()


def _build_filters(request: Request):
    from_date = request.query_params.get("from_date") or date.today().isoformat()
    to_date = request.query_params.get("to_date") or date.today().isoformat()
    lab_id_param = request.query_params.get("lab_id")
    lab_id = int(lab_id_param) if lab_id_param else None
    threshold_param = request.query_params.get("threshold")
    try:
        threshold = int(threshold_param) if threshold_param else LAB_CLASS_MIN_STUDENTS
    except ValueError:
        threshold = LAB_CLASS_MIN_STUDENTS
    if threshold < 1:
        threshold = LAB_CLASS_MIN_STUDENTS
    return from_date, to_date, lab_id, threshold


def _parse_dt(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _merge_into_sessions(rows):
    """Collapse a student's same-day, same-lab attendance rows into sessions.

    Consecutive visits are one continuous sitting (e.g. a short break) when
    the gap between an OUT and the next IN is within
    ANALYTICS_SESSION_GAP_MINUTES; anything longer (class ended, then a lone
    comeback periods later) starts a new, independently-classified session.
    Rows must already be sorted by (roll_no, lab_id, session_date, in_time).
    """
    sessions = []
    current = None
    current_key = None
    for r in rows:
        key = (r["roll_no"], r["lab_id"], r["session_date"])
        if current is not None and key == current_key:
            prev_out = _parse_dt(current["out_time"])
            next_in = _parse_dt(r["in_time"])
            gap_ok = (
                prev_out is not None
                and next_in is not None
                and (next_in - prev_out).total_seconds() / 60 <= ANALYTICS_SESSION_GAP_MINUTES
            )
            if gap_ok:
                current["out_time"] = r["out_time"]
                current["out_period_id"] = r["out_period_id"]
                continue
        current = dict(r)
        current_key = key
        sessions.append(current)
    return sessions


def _classify(conn, from_date, to_date, lab_id, threshold):
    sql = (
        "SELECT a.roll_no, a.session_date, a.in_period_id, a.out_period_id, a.lab_id, "
        "a.in_time, a.out_time, "
        "s.name AS name, s.year AS year, s.section AS section, "
        "p.period_name AS period_name, l.name AS lab_name "
        "FROM attendance a "
        "JOIN students s ON s.roll_no = a.roll_no "
        "JOIN labs l ON l.id = a.lab_id "
        "LEFT JOIN periods p ON p.id = a.in_period_id "
        "WHERE a.session_date BETWEEN ? AND ?"
    )
    params: list = [from_date, to_date]
    if lab_id is not None:
        sql += " AND a.lab_id = ?"
        params.append(lab_id)
    sql += " ORDER BY a.roll_no, a.lab_id, a.session_date, a.in_time"
    rows = conn.execute(sql, params).fetchall()
    sessions = _merge_into_sessions(rows)

    # bucket[(date, period_id, lab_id)] -> list of session dicts, deduped by roll_no
    buckets: dict = defaultdict(dict)
    for s in sessions:
        key = (s["session_date"], s["in_period_id"], s["lab_id"])
        buckets[key][s["roll_no"]] = s

    detected_classes = []
    individual_rows = []
    lab_totals: dict = defaultdict(int)
    total_class = 0
    total_individual = 0

    for (session_date, period_id, bucket_lab_id), students_in_bucket in buckets.items():
        sample = next(iter(students_in_bucket.values()))
        period_name = sample["period_name"] or "Unscheduled"
        lab_name = sample["lab_name"]

        by_group: dict = defaultdict(list)
        for row in students_in_bucket.values():
            by_group[(row["year"], row["section"])].append(row)

        classed_roll_nos = set()
        for (year, section), group_rows in by_group.items():
            if len(group_rows) >= threshold:
                detected_classes.append({
                    "date": session_date,
                    "period": period_name,
                    "lab": lab_name,
                    "lab_id": bucket_lab_id,
                    "year": year,
                    "section": section or "Unassigned",
                    "count": len(group_rows),
                })
                total_class += len(group_rows)
                lab_totals[lab_name] += len(group_rows)
                classed_roll_nos.update(r["roll_no"] for r in group_rows)

        for row in students_in_bucket.values():
            if row["roll_no"] in classed_roll_nos:
                continue
            individual_rows.append({
                "date": session_date,
                "period": period_name,
                "lab": lab_name,
                "lab_id": bucket_lab_id,
                "roll_no": row["roll_no"],
                "name": row["name"],
                "year": row["year"],
                "section": row["section"] or "Unassigned",
            })
            total_individual += 1
            lab_totals[lab_name] += 1

    detected_classes.sort(key=lambda c: (c["date"], c["period"], c["lab"]))
    individual_rows.sort(key=lambda r: (r["date"], r["period"], r["lab"]))

    return {
        "detected_classes": detected_classes,
        "individual_rows": individual_rows,
        "lab_totals": dict(lab_totals),
        "total_class": total_class,
        "total_individual": total_individual,
    }


def _class_strength(conn, from_date, to_date, lab_id):
    """Total enrolled strength vs. distinct students present, for each
    (year, section) that actually had at least one active student present
    in the filtered date range/lab — groups with zero attendance are
    omitted rather than listing the entire roster."""
    strength_rows = conn.execute(
        "SELECT year, section, COUNT(*) AS total FROM students "
        "WHERE archived_at IS NULL GROUP BY year, section"
    ).fetchall()
    strength: dict = {(r["year"], r["section"] or "Unassigned"): r["total"] for r in strength_rows}

    sql = (
        "SELECT DISTINCT a.roll_no, a.session_date, s.year AS year, s.section AS section, "
        "p.period_name AS period_name, p.start_time AS start_time "
        "FROM attendance a JOIN students s ON s.roll_no = a.roll_no "
        "LEFT JOIN periods p ON p.id = a.in_period_id "
        "WHERE s.archived_at IS NULL AND a.session_date BETWEEN ? AND ?"
    )
    params: list = [from_date, to_date]
    if lab_id is not None:
        sql += " AND a.lab_id = ?"
        params.append(lab_id)
    rows = conn.execute(sql, params).fetchall()

    present: dict = defaultdict(set)
    periods: dict = defaultdict(set)
    for r in rows:
        key = (r["year"], r["section"] or "Unassigned")
        present[key].add((r["roll_no"], r["session_date"]))
        if r["period_name"]:
            periods[key].add((r["start_time"] or "", r["period_name"]))

    # Only groups with at least one present student that day — not the
    # full roster of every year/section regardless of whether they had
    # anything scheduled in the filtered range/lab.
    result = []
    for (year, section), present_roll_dates in present.items():
        present_count = len(present_roll_dates)
        total = strength.get((year, section), 0)
        percent = round(present_count / total * 100, 1) if total else 0
        group_periods = sorted(periods.get((year, section), set()))
        result.append({
            "year": year,
            "section": section,
            "total": total,
            "present": present_count,
            "percent": percent,
            "periods": ", ".join(name for _, name in group_periods) or "-",
        })
    result.sort(key=lambda r: (r["year"], r["section"]))
    return result


def _faculty_summary(conn, from_date, to_date, lab_id):
    sql = (
        "SELECT fa.faculty_id, fa.session_date, l.name AS lab_name "
        "FROM faculty_attendance fa JOIN labs l ON l.id = fa.lab_id "
        "WHERE fa.session_date BETWEEN ? AND ?"
    )
    params: list = [from_date, to_date]
    if lab_id is not None:
        sql += " AND fa.lab_id = ?"
        params.append(lab_id)
    rows = conn.execute(sql, params).fetchall()
    unique = {(r["faculty_id"], r["session_date"]) for r in rows}
    return len(unique)


def _merge_faculty_sessions(rows):
    """Same continuous-sitting merge as _merge_into_sessions, keyed by
    faculty_id instead of roll_no (faculty rows have no roll_no)."""
    sessions = []
    current = None
    current_key = None
    for r in rows:
        key = (r["faculty_id"], r["lab_id"], r["session_date"])
        if current is not None and key == current_key:
            prev_out = _parse_dt(current["out_time"])
            next_in = _parse_dt(r["in_time"])
            gap_ok = (
                prev_out is not None
                and next_in is not None
                and (next_in - prev_out).total_seconds() / 60 <= ANALYTICS_SESSION_GAP_MINUTES
            )
            if gap_ok:
                current["out_time"] = r["out_time"]
                current["out_period_id"] = r["out_period_id"]
                continue
        current = dict(r)
        current_key = key
        sessions.append(current)
    return sessions


def _faculty_sessions(conn, from_date, to_date, lab_id):
    """Per-session faculty presence, so the Analytics page can answer "who
    was in which lab during which period" rather than just a headcount."""
    sql = (
        "SELECT fa.faculty_id, fa.session_date, fa.lab_id, fa.in_time, fa.out_time, "
        "fa.in_period_id, fa.out_period_id, "
        "f.name AS name, f.designation AS designation, f.role AS role, "
        "p.period_name AS period_name, l.name AS lab_name "
        "FROM faculty_attendance fa "
        "JOIN faculty f ON f.faculty_id = fa.faculty_id "
        "JOIN labs l ON l.id = fa.lab_id "
        "LEFT JOIN periods p ON p.id = fa.in_period_id "
        "WHERE fa.session_date BETWEEN ? AND ?"
    )
    params: list = [from_date, to_date]
    if lab_id is not None:
        sql += " AND fa.lab_id = ?"
        params.append(lab_id)
    sql += " ORDER BY fa.faculty_id, fa.lab_id, fa.session_date, fa.in_time"
    rows = conn.execute(sql, params).fetchall()
    sessions = _merge_faculty_sessions(rows)

    result = [{
        "date": s["session_date"],
        "period": s["period_name"] or "Unscheduled",
        "lab": s["lab_name"],
        "lab_id": s["lab_id"],
        "faculty_id": s["faculty_id"],
        "name": s["name"],
        "designation": s["designation"] or "-",
        "role": s["role"],
        "in_time": s["in_time"],
        "out_time": s["out_time"],
    } for s in sessions]
    result.sort(key=lambda r: (r["date"], r["period"], r["lab"], r["name"]))
    return result


@router.get("/analytics")
def analytics_page(request: Request):
    user, redirect = require_main_admin(request)
    if redirect:
        return redirect

    from_date, to_date, lab_id, threshold = _build_filters(request)

    conn = get_connection()
    try:
        labs = conn.execute("SELECT * FROM labs ORDER BY name").fetchall()
        result = _classify(conn, from_date, to_date, lab_id, threshold)
        faculty_count = _faculty_summary(conn, from_date, to_date, lab_id)
        faculty_sessions = _faculty_sessions(conn, from_date, to_date, lab_id)
        class_strength = _class_strength(conn, from_date, to_date, lab_id)
    finally:
        conn.close()

    # Zero-fill so the lab bar chart always shows every known lab, even with
    # no attendance, and stays in a stable alphabetical order.
    lab_labels = [l["name"] for l in labs]
    lab_values = [result["lab_totals"].get(name, 0) for name in lab_labels]

    # Pie breakdown: sum each detected class's headcount across every
    # date/period/lab it appeared in within the filtered range, so "Year 2 -
    # Section B" shows as one slice even if it recurred on several days.
    class_totals: dict = defaultdict(int)
    for c in result["detected_classes"]:
        class_totals[f"Year {c['year']} - {c['section']}"] += c["count"]
    class_breakdown = sorted(class_totals.items(), key=lambda kv: kv[1], reverse=True)
    pie_labels = [label for label, _ in class_breakdown] + ["Individual / Project"]
    pie_values = [count for _, count in class_breakdown] + [result["total_individual"]]

    context = {
        **admin_template_context(user),
        "active_nav": "analytics",
        "developers": get_developers_context(),
        "labs": labs,
        "from_date": from_date,
        "to_date": to_date,
        "selected_lab_id": lab_id,
        "threshold": threshold,
        "faculty_count": faculty_count,
        "faculty_sessions": faculty_sessions,
        "faculty_periods": sorted({s["period"] for s in faculty_sessions}),
        "class_strength": class_strength,
        "lab_labels": lab_labels,
        "lab_values": lab_values,
        "pie_labels": pie_labels,
        "pie_values": pie_values,
        **result,
    }
    return templates.TemplateResponse(request, "analytics.html", context)
