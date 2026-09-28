PRAGMA foreign_keys = ON;

-- Single row (id=1) holding hostel-wide settings: display name and the
-- password required to bulk-mark everyone OUT from the scanner page.
CREATE TABLE IF NOT EXISTS settings (
    id             INTEGER PRIMARY KEY CHECK (id = 1),
    hostel_name    TEXT NOT NULL DEFAULT 'Vaagai Womens Hostel',
    gate_password  TEXT NOT NULL DEFAULT 'clear123'
);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('admin', 'user')),
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS residents (
    resident_id TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    room_no     TEXT NOT NULL,
    floor       TEXT,
    phone       TEXT,
    course      TEXT,
    sex         TEXT,
    archived_at TEXT
);

CREATE TABLE IF NOT EXISTS embeddings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    resident_id TEXT NOT NULL REFERENCES residents(resident_id) ON DELETE CASCADE,
    embedding   BLOB NOT NULL,
    angle_label TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_embeddings_resident_id ON embeddings(resident_id);

CREATE TABLE IF NOT EXISTS staff (
    staff_id    TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    phone       TEXT,
    designation TEXT,
    role        TEXT NOT NULL DEFAULT 'warden' CHECK (role IN ('warden', 'security'))
);

CREATE TABLE IF NOT EXISTS staff_embeddings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    staff_id    TEXT NOT NULL REFERENCES staff(staff_id) ON DELETE CASCADE,
    embedding   BLOB NOT NULL,
    angle_label TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_staff_embeddings_staff_id ON staff_embeddings(staff_id);

CREATE TABLE IF NOT EXISTS attendance (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    resident_id  TEXT NOT NULL REFERENCES residents(resident_id) ON DELETE CASCADE,
    direction    TEXT NOT NULL CHECK (direction IN ('OUT', 'IN')),
    punch_time   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    punch_date   TEXT NOT NULL DEFAULT (date('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_attendance_resident_time ON attendance(resident_id, punch_time);
CREATE INDEX IF NOT EXISTS idx_attendance_punch_date ON attendance(punch_date);

CREATE TABLE IF NOT EXISTS staff_attendance (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    staff_id     TEXT NOT NULL REFERENCES staff(staff_id) ON DELETE CASCADE,
    direction    TEXT NOT NULL CHECK (direction IN ('OUT', 'IN')),
    punch_time   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    punch_date   TEXT NOT NULL DEFAULT (date('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_staff_attendance_staff_time ON staff_attendance(staff_id, punch_time);
CREATE INDEX IF NOT EXISTS idx_staff_attendance_punch_date ON staff_attendance(punch_date);

-- Global curfew schedule: at each configured clock time, every resident/staff
-- whose latest record is not OUT is automatically logged as OUT.
CREATE TABLE IF NOT EXISTS curfew_times (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    time                 TEXT NOT NULL UNIQUE,
    last_triggered_date  TEXT,
    created_at           TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
