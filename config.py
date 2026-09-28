"""Central configuration for the hostel attendance system. Tune these without touching logic."""

import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "attendance.db"
ENROLLMENT_PHOTOS_DIR = DATA_DIR / "enrollment_photos"


def _load_or_create_secret_key() -> str:
    key_path = DATA_DIR / ".secret_key"
    if key_path.exists():
        return key_path.read_text().strip()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    key = secrets.token_hex(32)
    key_path.write_text(key)
    return key


SECRET_KEY = _load_or_create_secret_key()

# --- Model ---
INSIGHTFACE_MODEL_NAME = "buffalo_s"   # SCRFD-500MF detector + lightweight recognition model
INSIGHTFACE_CTX_ID = -1                # -1 = CPU
DETECTION_SIZE = (640, 640)            # insightface det_size, input to SCRFD

# --- Camera / frame pipeline ---
CAMERA_INDEX = 0
FRAME_WIDTH = 640
FRAME_HEIGHT = 480
DETECT_EVERY_N_FRAMES = 3              # run detection every Nth frame; reuse last result otherwise
MOTION_TRIGGER_ENABLED = True
MOTION_DIFF_THRESHOLD = 25             # per-pixel intensity diff threshold for motion trigger
MOTION_MIN_CHANGED_PIXELS_RATIO = 0.02 # fraction of pixels that must change to count as motion

# --- Matching ---
COSINE_MATCH_THRESHOLD = 0.55          # configurable per requirement 5 (~0.5-0.6)
MATCH_MARGIN = 0.05                    # best match must beat the best *other* identity by
                                        # at least this much, else treated as unknown — guards
                                        # against false accepts as the enrolled pool grows
MIN_FACE_SIZE_PX = 60                  # reject tiny/far-away detections

# --- Analytics ---
# A resident's same-day attendance rows are merged into one session when the
# gap between an OUT and the next IN is within this many minutes — treated as
# a short break (stepping out and back in), not a separate visit.
ANALYTICS_SESSION_GAP_MINUTES = 15

# Hour (24h) after which an IN scan counts as a "late entry" on the Analytics
# page — the hostel-relevant curfew-violation signal. Overridable per-request.
LATE_ENTRY_CUTOFF_HOUR = 22

# --- Enrollment ---
ENROLLMENT_SHOTS_PER_STUDENT = 5
ENROLLMENT_ANGLE_LABELS = ["center", "left", "right", "up", "down"]
BLUR_LAPLACIAN_VAR_THRESHOLD = 80.0    # below this variance, image is considered too blurry

# --- Attendance dedup ---
# A rescan within this window is treated as accidental and just re-confirms current
# status; a rescan after this window toggles IN->OUT (or starts a fresh IN if they
# were already OUT).
DEDUP_WINDOW_MINUTES = 1

# --- Web app auth ---
ATTENDANCE_PASSCODE = "attendance123"  # gate before the scanner login (kiosk)
DEFAULT_GATE_PASSWORD = "clear123"     # default "Clear Gate" password, stored in settings.gate_password
EDIT_RECAPTURE_SHOTS = 2               # photos required when re-capturing faces on Edit pages

# --- Seed (db/seed.py) ---
HOSTEL_NAME = "PG Hostel"
SEED_ADMIN_USERNAME = "admin"
SEED_ADMIN_PASSWORD = "admin123"       # change via Manage Users after first login
