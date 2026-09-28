"""Enroll a resident: capture several angles via webcam, validate each capture,
generate embeddings, and store them in the DB.

Usage:
    python enrollment/enroll.py --resident_id RES-001 --name "Jane Doe" --room_no 101

Controls during capture:
    SPACE - attempt a capture for the current angle
    ESC   - cancel enrollment (nothing is written to the DB)
"""

import argparse
import re
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import (
    CAMERA_INDEX,
    ENROLLMENT_ANGLE_LABELS,
    ENROLLMENT_PHOTOS_DIR,
    FRAME_HEIGHT,
    FRAME_WIDTH,
)
from core.face_engine import get_face_app
from db.connection import get_connection
from enrollment.validation import validate_capture

RESIDENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,30}$")
WINDOW_NAME = "Enrollment - SPACE=capture  ESC=cancel"


def get_next_resident_id(conn) -> str:
    """Generate the next sequential resident ID in the format RES-001, RES-002, etc."""
    rows = conn.execute("SELECT resident_id FROM residents").fetchall()
    max_num = 0
    for r in rows:
        rid = (r["resident_id"] or "").strip().upper()
        match = re.match(r"^RES-(\d+)$", rid, re.IGNORECASE)
        if match:
            num = int(match.group(1))
            if num > max_num:
                max_num = num
        else:
            digits = re.findall(r"\d+", rid)
            if digits:
                num = int(digits[-1])
                if num > max_num:
                    max_num = num
    return f"RES-{max_num + 1:03d}"


def validate_resident_id(resident_id: str) -> str:
    resident_id = resident_id.strip()
    if not RESIDENT_ID_PATTERN.match(resident_id):
        raise ValueError(
            f"resident_id '{resident_id}' doesn't match expected format "
            "(letters/digits/hyphens - e.g. RES-001 or r101)"
        )
    return resident_id


def upsert_resident(conn, resident_id: str, name: str, room_no: str, floor: str = "") -> None:
    """Insert or update a resident. Also clears archived_at — (re-)enrolling
    someone is always meant to make them active, including restoring a
    previously vacated resident_id."""
    existing = conn.execute(
        "SELECT resident_id FROM residents WHERE resident_id=?", (resident_id,)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE residents SET name=?, room_no=?, floor=?, archived_at=NULL WHERE resident_id=?",
            (name, room_no, floor, resident_id),
        )
    else:
        conn.execute(
            "INSERT INTO residents (resident_id, name, room_no, floor) VALUES (?,?,?,?)",
            (resident_id, name, room_no, floor),
        )
    conn.commit()


def run_enrollment(resident_id: str, name: str, room_no: str, floor: str = "") -> bool:
    app = get_face_app()

    cap = cv2.VideoCapture(CAMERA_INDEX)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
    if not cap.isOpened():
        print(f"ERROR: could not open camera index {CAMERA_INDEX}")
        return False

    captured = []  # (angle_label, embedding, photo_path)
    angle_idx = 0

    try:
        while angle_idx < len(ENROLLMENT_ANGLE_LABELS):
            angle_label = ENROLLMENT_ANGLE_LABELS[angle_idx]
            ret, frame = cap.read()
            if not ret:
                print("ERROR: camera read failed")
                break

            display = frame.copy()
            cv2.putText(
                display,
                f"Angle: {angle_label} ({angle_idx + 1}/{len(ENROLLMENT_ANGLE_LABELS)})  SPACE=capture  ESC=cancel",
                (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 255, 0),
                2,
            )
            if captured:
                cv2.putText(
                    display,
                    f"Last: OK ({captured[-1][0]}, sharpness={captured[-1][3]:.0f})",
                    (10, 60),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    (0, 200, 0),
                    1,
                )
            cv2.imshow(WINDOW_NAME, display)
            key = cv2.waitKey(1) & 0xFF

            if key == 27:  # ESC
                print("Cancelled by user — nothing saved.")
                return False

            if key == 32:  # SPACE
                faces = app.get(frame)
                result = validate_capture(frame, faces)
                if not result.ok:
                    print(f"[REJECTED] {angle_label}: {result.reason}")
                    continue

                embedding = result.face.normed_embedding.astype(np.float32)
                photo_path = ENROLLMENT_PHOTOS_DIR / f"{resident_id}_{angle_label}_{int(time.time())}.jpg"
                cv2.imwrite(str(photo_path), frame)
                captured.append((angle_label, embedding, str(photo_path), result.sharpness))
                print(
                    f"[OK] Captured '{angle_label}' "
                    f"({len(captured)}/{len(ENROLLMENT_ANGLE_LABELS)}, sharpness={result.sharpness:.0f})"
                )
                angle_idx += 1
    finally:
        cap.release()
        cv2.destroyAllWindows()

    if not captured:
        print("No captures — aborting enrollment.")
        return False

    conn = get_connection()
    try:
        upsert_resident(conn, resident_id, name, room_no, floor)
        for angle_label, embedding, _photo_path, _sharpness in captured:
            conn.execute(
                "INSERT INTO embeddings (resident_id, embedding, angle_label) VALUES (?,?,?)",
                (resident_id, embedding.tobytes(), angle_label),
            )
        conn.commit()
    finally:
        conn.close()

    print(f"Enrolled {resident_id} ({name}) with {len(captured)} embeddings.")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Enroll a resident for face recognition attendance")
    parser.add_argument("--resident_id", help="e.g. RES-001 (auto-generated if omitted)")
    parser.add_argument("--name", required=True)
    parser.add_argument("--room_no", required=True)
    parser.add_argument("--floor", default="")
    args = parser.parse_args()

    conn = get_connection()
    try:
        if args.resident_id:
            resident_id = validate_resident_id(args.resident_id)
        else:
            resident_id = get_next_resident_id(conn)
    finally:
        conn.close()

    run_enrollment(resident_id, args.name.strip(), args.room_no.strip(), args.floor.strip())


if __name__ == "__main__":
    main()
