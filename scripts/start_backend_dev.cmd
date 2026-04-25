@echo off
cd /d "%~dp0..\backend"
set DEBUG=true
set RELOAD=0
python run_api.py --host 127.0.0.1 --port 8001
