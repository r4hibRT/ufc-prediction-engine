@echo off
REM Local viewing: builds the frontend if needed, then serves the whole site
REM from a single process. No Node running, one URL.

setlocal
set "ROOT=%~dp0.."
pushd "%ROOT%" || exit /b 1
set "ROOT=%CD%"
if not defined UFC_PYTHON set "UFC_PYTHON=C:\Users\Hp\anaconda3\python.exe"

if "%~1"=="--build" goto build
if not exist "%ROOT%\frontend\dist\index.html" goto build
goto serve

:build
echo Building frontend...
pushd "%ROOT%\frontend"
call npm run build || (echo Build failed & popd & popd & exit /b 1)
popd

:serve
set "PYTHONPATH=%ROOT%"
echo.
echo   Open http://localhost:8000
echo.
"%UFC_PYTHON%" -m uvicorn src.api.main:app --port 8000
popd
