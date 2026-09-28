"""FastAPI app: session auth + Jinja2 admin UI for the PG hostel attendance system."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from config import SECRET_KEY

from app import state
from app.routers import auth as auth_router
from app.routers import capture as capture_router
from app.routers import curfew as curfew_router
from app.routers import register as register_router
from app.routers import report as report_router
from app.routers import residents as residents_router
from app.routers import scanner as scanner_router
from app.routers import settings as settings_router
from app.routers import staff as staff_router
from app.routers import users as users_router

STATIC_DIR = Path(__file__).resolve().parent / "static"
CURFEW_CHECK_INTERVAL_SECONDS = 20

app = FastAPI(title="PG Hostel Attendance System")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)


@app.on_event("startup")
def startup() -> None:
    recognizer = state.init_recognizer()
    print(f"Loaded {len(recognizer.identities)} embeddings "
          f"across {len(set(recognizer.identities))} identities.")


@app.on_event("startup")
async def start_curfew_scheduler() -> None:
    async def _loop():
        while True:
            try:
                curfew_router.run_due_curfews()
            except Exception as e:
                print(f"[curfew] scheduler error: {e}")
            await asyncio.sleep(CURFEW_CHECK_INTERVAL_SECONDS)

    app.state.curfew_scheduler_task = asyncio.create_task(_loop())


app.include_router(auth_router.router)
app.include_router(capture_router.router)
app.include_router(residents_router.router)
app.include_router(staff_router.router)
app.include_router(curfew_router.router)
app.include_router(settings_router.router)
app.include_router(users_router.router)
app.include_router(scanner_router.router)
app.include_router(report_router.router)
app.include_router(register_router.router)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
