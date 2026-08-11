"""Create/migrate the SQLite database to the current schema. Safe to re-run.

Table creation uses IF NOT EXISTS. For tables that predate a schema change
(students gaining batch/section/sex; attendance gaining lab_id-as-FK + IN/OUT
columns), this adds the missing pieces in place rather than dropping data.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DATA_DIR, DB_PATH, ENROLLMENT_PHOTOS_DIR
from db.connection import get_connection

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def _table_columns(conn, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _migrate_pre(conn) -> None:
    """Handle changes that must happen before schema.sql runs (e.g. a table
    whose shape changed incompatibly with CREATE TABLE IF NOT EXISTS)."""
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    if "attendance" in tables:
        cols = _table_columns(conn, "attendance")
        if "in_time" not in cols:
            count = conn.execute("SELECT COUNT(*) c FROM attendance").fetchone()["c"]
            if count == 0:
                conn.execute("DROP TABLE attendance")
            else:
                raise RuntimeError(
                    "attendance table uses the old schema and has existing rows — "
                    "manual migration required (not auto-dropping non-empty data)"
                )

    if "periods" in tables and "lab_id" in _table_columns(conn, "periods"):
        _migrate_periods_to_global(conn)


def _migrate_periods_to_global(conn) -> None:
    """periods used to be scoped per-lab; collapse duplicate (name, start, end)
    slots across labs into one global row, repointing any attendance rows that
    referenced a removed period id at the surviving row for the same slot.

    Note: we never rename the live "periods" table — SQLite auto-rewrites
    other tables' FK clauses (e.g. attendance's REFERENCES periods(id)) to
    follow a renamed table, which would leave them dangling once the old
    table is dropped. Instead we build the replacement under a temp name,
    drop "periods", then rename the replacement into that exact name so
    attendance's untouched FK clause resolves correctly again.
    """
    conn.execute("PRAGMA foreign_keys = OFF")

    rows = conn.execute("SELECT id, period_name, start_time, end_time FROM periods").fetchall()

    conn.execute(
        "CREATE TABLE periods_new ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "period_name TEXT NOT NULL,"
        "start_time TEXT NOT NULL,"
        "end_time TEXT NOT NULL)"
    )

    new_id_by_key: dict[tuple, int] = {}
    for r in rows:
        key = (r["period_name"], r["start_time"], r["end_time"])
        if key not in new_id_by_key:
            cur = conn.execute(
                "INSERT INTO periods_new (period_name, start_time, end_time) VALUES (?,?,?)", key
            )
            new_id_by_key[key] = cur.lastrowid

    for r in rows:
        key = (r["period_name"], r["start_time"], r["end_time"])
        new_id = new_id_by_key[key]
        conn.execute("UPDATE attendance SET in_period_id=? WHERE in_period_id=?", (new_id, r["id"]))
        conn.execute("UPDATE attendance SET out_period_id=? WHERE out_period_id=?", (new_id, r["id"]))

    conn.execute("DROP TABLE periods")
    conn.execute("ALTER TABLE periods_new RENAME TO periods")
    conn.execute("PRAGMA foreign_keys = ON")


def _migrate_post(conn) -> None:
    """Add columns to tables that already existed under the old schema."""
    cols = _table_columns(conn, "students")
    for col in ("batch", "section", "sex", "archived_at"):
        if col not in cols:
            conn.execute(f"ALTER TABLE students ADD COLUMN {col} TEXT")

    faculty_cols = _table_columns(conn, "faculty")
    if "role" not in faculty_cols:
        conn.execute("ALTER TABLE faculty ADD COLUMN role TEXT NOT NULL DEFAULT 'faculty'")


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ENROLLMENT_PHOTOS_DIR.mkdir(parents=True, exist_ok=True)

    schema_sql = SCHEMA_PATH.read_text()
    conn = get_connection()
    try:
        _migrate_pre(conn)
        conn.executescript(schema_sql)
        _migrate_post(conn)
        conn.commit()
    finally:
        conn.close()

    print(f"Database initialized/migrated at {DB_PATH}")


if __name__ == "__main__":
    init_db()
