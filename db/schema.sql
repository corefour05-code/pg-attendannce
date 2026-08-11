PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS labs (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    name               TEXT NOT NULL UNIQUE,
    clear_lab_password TEXT NOT NULL DEFAULT 'clear123'
);

CREATE TABLE IF NOT EXISTS periods (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    period_name TEXT NOT NULL,
    start_time  TEXT NOT NULL,
    end_time    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('admin', 'user')),
    lab_id        INTEGER REFERENCES labs(id) ON DELETE SET NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS students (
    roll_no     TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    year        INTEGER NOT NULL,
    department  TEXT NOT NULL,
    batch       TEXT,
    section     TEXT,
    sex         TEXT,
    archived_at TEXT
);

CREATE TABLE IF NOT EXISTS embeddings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    roll_no     TEXT NOT NULL REFERENCES students(roll_no) ON DELETE CASCADE,
    embedding   BLOB NOT NULL,
    angle_label TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_embeddings_roll_no ON embeddings(roll_no);

CREATE TABLE IF NOT EXISTS faculty (
    faculty_id  TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    department  TEXT NOT NULL,
    designation TEXT,
    role        TEXT NOT NULL DEFAULT 'faculty' CHECK (role IN ('faculty', 'technician'))
);

CREATE TABLE IF NOT EXISTS faculty_embeddings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    faculty_id  TEXT NOT NULL REFERENCES faculty(faculty_id) ON DELETE CASCADE,
    embedding   BLOB NOT NULL,
    angle_label TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_faculty_embeddings_faculty_id ON faculty_embeddings(faculty_id);

CREATE TABLE IF NOT EXISTS attendance (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    roll_no       TEXT NOT NULL REFERENCES students(roll_no) ON DELETE CASCADE,
    lab_id        INTEGER NOT NULL REFERENCES labs(id) ON DELETE CASCADE,
    session_date  TEXT NOT NULL,
    in_time       TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    in_period_id  INTEGER REFERENCES periods(id),
    out_time      TEXT,
    out_period_id INTEGER REFERENCES periods(id),
    out_date      TEXT
);

CREATE INDEX IF NOT EXISTS idx_attendance_dedup ON attendance(roll_no, session_date, lab_id);
CREATE INDEX IF NOT EXISTS idx_attendance_session_date ON attendance(session_date);

CREATE TABLE IF NOT EXISTS faculty_attendance (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    faculty_id    TEXT NOT NULL REFERENCES faculty(faculty_id) ON DELETE CASCADE,
    lab_id        INTEGER NOT NULL REFERENCES labs(id) ON DELETE CASCADE,
    session_date  TEXT NOT NULL,
    in_time       TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    in_period_id  INTEGER REFERENCES periods(id),
    out_time      TEXT,
    out_period_id INTEGER REFERENCES periods(id),
    out_date      TEXT
);

CREATE INDEX IF NOT EXISTS idx_faculty_attendance_dedup ON faculty_attendance(faculty_id, session_date, lab_id);
CREATE INDEX IF NOT EXISTS idx_faculty_attendance_session_date ON faculty_attendance(session_date);

-- Snapshot of each Batch Processing "Move to Next Year" run, so it can be
-- undone: prior year per student, and (for graduated students) a backup of
-- the embeddings that were deleted when they were archived into Old Students.
CREATE TABLE IF NOT EXISTS batch_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    promoted_count  INTEGER NOT NULL,
    graduated_count INTEGER NOT NULL,
    undone_at       TEXT
);

CREATE TABLE IF NOT EXISTS batch_run_students (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_run_id INTEGER NOT NULL REFERENCES batch_runs(id) ON DELETE CASCADE,
    roll_no      TEXT NOT NULL,
    prev_year    INTEGER NOT NULL,
    was_archived INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_batch_run_students_run ON batch_run_students(batch_run_id);

CREATE TABLE IF NOT EXISTS batch_run_embeddings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_run_id INTEGER NOT NULL REFERENCES batch_runs(id) ON DELETE CASCADE,
    roll_no      TEXT NOT NULL,
    embedding    BLOB NOT NULL,
    angle_label  TEXT
);

CREATE INDEX IF NOT EXISTS idx_batch_run_embeddings_run ON batch_run_embeddings(batch_run_id);

-- Global "Manage Shutdown Time" schedule: at each configured clock time, every
-- lab's currently-IN students/faculty are auto-marked OUT (like Clear Lab, but
-- scheduled and applied across all labs at once instead of per-lab/manual).
CREATE TABLE IF NOT EXISTS shutdown_times (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    time                 TEXT NOT NULL UNIQUE,  -- "HH:MM", 24h
    last_triggered_date  TEXT,                   -- 'YYYY-MM-DD' of the last day this fired, NULL until first fire
    created_at           TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
