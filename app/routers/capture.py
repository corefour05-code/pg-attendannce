"""Shared face-capture validation endpoint used by Add/Edit Resident and
Add/Edit Staff forms. Validates a single frame (face count/size/blur) but
does NOT write to the DB — the final form submit re-validates and persists.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import cv2
import numpy as np
from fastapi import APIRouter, File, HTTPException, UploadFile

from app import state
from enrollment.validation import validate_capture

router = APIRouter()


@router.post("/api/capture/validate")
async def capture_validate(image: UploadFile = File(...)):
    recognizer = state.get_recognizer()
    data = await image.read()
    arr = np.frombuffer(data, dtype=np.uint8)
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if frame is None:
        raise HTTPException(400, "Could not decode image")

    faces = recognizer.app.get(frame)
    result = validate_capture(frame, faces)
    if not result.ok:
        return {"ok": False, "reason": result.reason}
    return {"ok": True, "sharpness": result.sharpness}
