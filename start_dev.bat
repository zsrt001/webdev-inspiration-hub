@echo off
REM ============================================================
REM AI Wedding Photo - Development Start Script (Windows)
REM ============================================================

echo.
echo ============================================================
echo  AI Wedding Photo - Starting Development Environment
echo ============================================================
echo.

REM Get the directory where the script is located
cd /d "%~dp0"

REM Step 1: Start Docker Compose (Database, Redis, MinIO)
echo [1/3] Starting Docker Compose services...
docker-compose up -d
if errorlevel 1 (
    echo [WARNING] Docker Compose failed. Make sure Docker Desktop is running.
    echo           Continuing without Docker services...
) else (
    echo [OK] Docker services started.
)
echo.

REM Step 2: Start Backend in new terminal
echo [2/4] Starting Backend API server...
start "Backend API" cmd /k "cd /d %~dp0backend && echo Starting FastAPI server on http://localhost:8001 && python run_api.py --host 0.0.0.0 --port 8001"
echo [OK] Backend terminal opened.
echo.

REM Step 3: Start Worker in new terminal
echo [3/4] Starting ARQ worker...
start "ARQ Worker" cmd /k "cd /d %~dp0backend && echo Starting ARQ worker (ComfyUI queue) && python run_worker.py"
echo [OK] Worker terminal opened.
echo.

REM Step 4: Start Frontend H5 in new terminal
echo [4/4] Starting Frontend H5 dev server...
start "Frontend H5" cmd /k "cd /d %~dp0frontend && echo Starting Uni-app H5 on http://localhost:3000 && npm run dev:h5"
echo [OK] Frontend terminal opened.
echo.

echo ============================================================
echo  Development servers starting...
echo.
echo  Backend API:  http://localhost:8001
echo  Frontend H5:  http://localhost:3000
echo  API Docs:     http://localhost:8001/docs
echo ============================================================
echo.
echo Press any key to close this window (servers will keep running)...
pause > nul
