@echo off
REM Development mode: API on :8420 with reload, Vite on :5180 with hot reload.
REM Opens two windows. For a single-process view of the built site use serve.cmd.

setlocal
set "ROOT=%~dp0.."
pushd "%ROOT%" || exit /b 1
set "ROOT=%CD%"
if not defined UFC_PYTHON set "UFC_PYTHON=C:\Users\Hp\anaconda3\python.exe"

start "UFC API" cmd /k "cd /d "%ROOT%" && set PYTHONPATH=%ROOT% && "%UFC_PYTHON%" -m uvicorn src.api.main:app --port 8420 --reload"
start "UFC Frontend" cmd /k "cd /d "%ROOT%\frontend" && npm run dev"

echo.
echo   API      http://127.0.0.1:8420/docs
echo   Frontend http://localhost:5180      ^<-- open this one
echo.
echo   Note: use localhost, not 127.0.0.1, for the frontend.
echo   Close both windows to stop.
echo.
popd
