@echo off
start "Backend" cmd /k "cd /d %~dp0 && uv run uvicorn backend.app:app --reload --port 8000"
start "Frontend" cmd /k "cd /d %~dp0\frontend && npm run dev"