@echo off
REM Wrapper for the weekly refresh, invoked by Task Scheduler.
REM Resolves the repo from this script's own location so the task keeps
REM working if the project moves.

setlocal

set "ROOT=%~dp0.."
pushd "%ROOT%" || exit /b 1
set "ROOT=%CD%"

if not defined UFC_PYTHON set "UFC_PYTHON=C:\Users\Hp\anaconda3\python.exe"

if not exist "%UFC_PYTHON%" (
    echo [refresh.cmd] Python not found at "%UFC_PYTHON%".
    echo [refresh.cmd] Set the UFC_PYTHON environment variable to override.
    popd
    exit /b 1
)

set "PYTHONPATH=%ROOT%"
set "PYTHONUTF8=1"

"%UFC_PYTHON%" -m src.automation.refresh %*
set "RC=%ERRORLEVEL%"

popd
exit /b %RC%
