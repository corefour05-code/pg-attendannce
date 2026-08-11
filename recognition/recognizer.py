"""Core recognition: load embeddings into an in-memory matrix, and match faces
detected in a frame against it via cosine similarity.

Frame-skipping / motion-trigger is a live-loop concern (Step 4) — this module
is stateless per call: give it a frame, it detects + matches every face in it.
"""

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import COSINE_MATCH_THRESHOLD, MATCH_MARGIN
from core.face_engine import get_face_app
from db.connection import get_connection

EMBEDDING_DIM = 512


class FaceRecognizer:
    def __init__(
        self,
        match_threshold: float = COSINE_MATCH_THRESHOLD,
        match_margin: float = MATCH_MARGIN,
    ):
        self.app = get_face_app()
        self.match_threshold = match_threshold
        self.match_margin = match_margin
        self.identities: list[tuple[str, str, str | None]] = []  # (kind, id, role); role only set for faculty
        self.identity_key_arr = np.array([], dtype=object)
        self.matrix = np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
        self.reload_embeddings()

    def reload_embeddings(self) -> int:
        """(Re)load all student + faculty embeddings from the DB into the in-memory
        matrix. Returns the number of embeddings loaded."""
        conn = get_connection()
        try:
            student_rows = conn.execute("SELECT roll_no AS id, embedding FROM embeddings").fetchall()
            faculty_rows = conn.execute(
                "SELECT fe.faculty_id AS id, fe.embedding AS embedding, f.role AS role "
                "FROM faculty_embeddings fe JOIN faculty f ON f.faculty_id = fe.faculty_id"
            ).fetchall()
        finally:
            conn.close()

        rows = [("student", r, None) for r in student_rows] + [("faculty", r, r["role"]) for r in faculty_rows]

        if not rows:
            self.identities = []
            self.identity_key_arr = np.array([], dtype=object)
            self.matrix = np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
            return 0

        self.identities = [(kind, r["id"], role) for kind, r, role in rows]
        self.identity_key_arr = np.array([f"{kind}:{r['id']}" for kind, r, _ in rows], dtype=object)
        self.matrix = np.vstack(
            [np.frombuffer(r["embedding"], dtype=np.float32) for _, r, _ in rows]
        ).astype(np.float32)
        return len(self.identities)

    def match_embedding(self, embedding: np.ndarray) -> tuple[str | None, str | None, str | None, float]:
        """Cosine similarity via dot product (embeddings are L2-normalized by insightface).

        A match is only accepted if the best-scoring identity clears the absolute
        threshold AND beats the best score among all *other* identities by at least
        match_margin. Each person has several enrollment shots, so the runner-up
        row is almost always the same person's own shot — the margin is computed
        against the best score belonging to a different identity, not just the
        second-highest row. This keeps false accepts from creeping up as the
        enrolled pool grows (more identities = more chances for a look-alike to
        clear a flat threshold).

        Returns (kind, id, role, best_score) where kind/id/role are None on no-match.
        role is only set when kind is 'faculty'.
        """
        if self.matrix.shape[0] == 0:
            return None, None, None, 0.0

        scores = self.matrix @ embedding
        best_idx = int(np.argmax(scores))
        best_score = float(scores[best_idx])
        best_kind, best_id, best_role = self.identities[best_idx]
        best_key = f"{best_kind}:{best_id}"

        other_mask = self.identity_key_arr != best_key
        runner_up_score = float(scores[other_mask].max()) if other_mask.any() else -1.0

        if (
            best_score >= self.match_threshold
            and (best_score - runner_up_score) >= self.match_margin
        ):
            return best_kind, best_id, best_role, best_score
        return None, None, None, best_score

    def recognize(self, frame: np.ndarray) -> tuple[list[dict], dict]:
        """Detect all faces in frame, match each against the embedding matrix.

        Returns (results, timing_ms) where each result is:
            {'bbox': (x1,y1,x2,y2), 'kind': 'student'|'faculty'|None, 'role': 'faculty'|'technician'|None,
             'identity': str, 'confidence': float}
        and timing_ms has 'detect_embed_ms', 'match_ms', 'total_ms'.
        """
        t0 = time.perf_counter()
        faces = self.app.get(frame)
        t1 = time.perf_counter()

        results = []
        for face in faces:
            kind, identity, role, score = self.match_embedding(face.normed_embedding.astype(np.float32))
            results.append(
                {
                    "bbox": tuple(face.bbox.tolist()),
                    "kind": kind,
                    "role": role,
                    "identity": identity if identity else "unknown",
                    "confidence": score,
                }
            )
        t2 = time.perf_counter()

        timing = {
            "detect_embed_ms": (t1 - t0) * 1000,
            "match_ms": (t2 - t1) * 1000,
            "total_ms": (t2 - t0) * 1000,
        }
        return results, timing


def _run_benchmark(num_frames: int = 30) -> None:
    import cv2

    from config import CAMERA_INDEX, FRAME_HEIGHT, FRAME_WIDTH

    recognizer = FaceRecognizer()
    print(f"Loaded {len(recognizer.identities)} embeddings across "
          f"{len(set(recognizer.identities))} identities.")

    cap = cv2.VideoCapture(CAMERA_INDEX)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
    if not cap.isOpened():
        print(f"ERROR: could not open camera index {CAMERA_INDEX}")
        return

    timings = []
    try:
        print(f"Benchmarking {num_frames} frames (look at the camera)...")
        for i in range(num_frames):
            ret, frame = cap.read()
            if not ret:
                print("Camera read failed, stopping early.")
                break
            results, timing = recognizer.recognize(frame)
            timings.append(timing)
            names = [f"{r['identity']}({r['confidence']:.2f})" for r in results]
            print(
                f"[{i + 1:02d}/{num_frames}] faces={len(results)} {names} "
                f"detect+embed={timing['detect_embed_ms']:.1f}ms "
                f"match={timing['match_ms']:.2f}ms "
                f"total={timing['total_ms']:.1f}ms"
            )
    finally:
        cap.release()

    if not timings:
        print("No frames captured — nothing to report.")
        return

    detect_vals = [t["detect_embed_ms"] for t in timings]
    match_vals = [t["match_ms"] for t in timings]
    total_vals = [t["total_ms"] for t in timings]

    def stats(vals):
        return f"avg={np.mean(vals):.1f}ms min={np.min(vals):.1f}ms max={np.max(vals):.1f}ms"

    print("\n--- Benchmark summary ---")
    print(f"detect+embed: {stats(detect_vals)}")
    print(f"match:        {stats(match_vals)}")
    print(f"total:        {stats(total_vals)}")
    print(f"implied max FPS if run every frame: {1000 / np.mean(total_vals):.1f}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Benchmark detection+matching speed")
    parser.add_argument("--frames", type=int, default=30)
    args = parser.parse_args()
    _run_benchmark(args.frames)
