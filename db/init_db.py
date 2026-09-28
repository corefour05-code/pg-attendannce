"""Create the SQLite database from schema.sql. Safe to re-run (all
CREATE TABLE/INDEX statements use IF NOT EXISTS) and migrates older attendance schemas."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DATA_DIR, DB_PATH, ENROLLMENT_PHOTOS_DIR
from db.connection import get_connection

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def _migrate_attendance_if_needed(conn) -> None:
    # Check if attendance table has old columns (e.g. in_time instead of direction)
    try:
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(attendance)").fetchall()]
    except Exception:
        cols = []

    if cols and "direction" not in cols and "in_time" in cols:
        print("[init_db] Migrating legacy attendance table to consecutive event format...")
        old_rows = conn.execute("SELECT * FROM attendance ORDER BY id ASC").fetchall()
        conn.execute("DROP TABLE IF EXISTS attendance")
        conn.execute("""
            CREATE TABLE attendance (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                resident_id  TEXT NOT NULL REFERENCES residents(resident_id) ON DELETE CASCADE,
                direction    TEXT NOT NULL CHECK (direction IN ('OUT', 'IN')),
                punch_time   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                punch_date   TEXT NOT NULL DEFAULT (date('now', 'localtime'))
            )
        """)
        for r in old_rows:
            if r["in_time"]:
                conn.execute(
                    "INSERT INTO attendance (resident_id, direction, punch_time, punch_date) VALUES (?,?,?,?)",
                    (r["resident_id"], "IN", r["in_time"], r["session_date"]),
                )
            if r["out_time"]:
                conn.execute(
                    "INSERT INTO attendance (resident_id, direction, punch_time, punch_date) VALUES (?,?,?,?)",
                    (r["resident_id"], "OUT", r["out_time"], r["out_date"] or r["session_date"]),
                )
        conn.commit()

    # Check staff_attendance
    try:
        staff_cols = [r["name"] for r in conn.execute("PRAGMA table_info(staff_attendance)").fetchall()]
    except Exception:
        staff_cols = []

    if staff_cols and "direction" not in staff_cols and "in_time" in staff_cols:
        print("[init_db] Migrating legacy staff_attendance table to consecutive event format...")
        old_staff_rows = conn.execute("SELECT * FROM staff_attendance ORDER BY id ASC").fetchall()
        conn.execute("DROP TABLE IF EXISTS staff_attendance")
        conn.execute("""
            CREATE TABLE staff_attendance (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                staff_id     TEXT NOT NULL REFERENCES staff(staff_id) ON DELETE CASCADE,
                direction    TEXT NOT NULL CHECK (direction IN ('OUT', 'IN')),
                punch_time   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                punch_date   TEXT NOT NULL DEFAULT (date('now', 'localtime'))
            )
        """)
        for r in old_staff_rows:
            if r["in_time"]:
                conn.execute(
                    "INSERT INTO staff_attendance (staff_id, direction, punch_time, punch_date) VALUES (?,?,?,?)",
                    (r["staff_id"], "IN", r["in_time"], r["session_date"]),
                )
            if r["out_time"]:
                conn.execute(
                    "INSERT INTO staff_attendance (staff_id, direction, punch_time, punch_date) VALUES (?,?,?,?)",
                    (r["staff_id"], "OUT", r["out_time"], r["out_date"] or r["session_date"]),
                )
        conn.commit()


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ENROLLMENT_PHOTOS_DIR.mkdir(parents=True, exist_ok=True)

    conn = get_connection()
    try:
        _migrate_attendance_if_needed(conn)
        schema_sql = SCHEMA_PATH.read_text()
        conn.executescript(schema_sql)
        conn.commit()
    finally:
        conn.close()

    print(f"Database initialized at {DB_PATH}")


if __name__ == "__main__":
    init_db()
