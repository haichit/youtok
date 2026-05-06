@echo off
REM Start Huey worker on Windows with 10-thread hard ceiling.
REM Actual concurrent jobs gated by `concurrent_jobs` setting (1..10) in /settings UI.

cd /d "%~dp0\.."

if not exist data\logs mkdir data\logs

if "%WORKERS%"=="" set WORKERS=10
echo Starting Huey worker with %WORKERS% thread ceiling.
echo Adjust concurrent jobs in /settings (default: 1).

uv run huey_consumer youtok.queue.huey_app.huey --workers %WORKERS%
