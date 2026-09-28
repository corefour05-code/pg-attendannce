@echo off
cd /d "%~dp0"
if exist .venv\Scripts\activate.bat (
    call .venv\Scripts\activate.bat
) else if exist venv\Scripts\activate.bat (
    call venv\Scripts\activate.bat
)
uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
pause
