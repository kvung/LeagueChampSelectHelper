@echo off
REM Build the Windows executable. Run this on Windows (not macOS) — PyInstaller
REM cannot cross-compile a .exe from another OS.

python -m pip install -r requirements.txt
if errorlevel 1 goto :error

python -m PyInstaller --onefile --windowed --name LeagueChampSelectHelper main.py
if errorlevel 1 goto :error

echo.
echo Build complete: dist\LeagueChampSelectHelper.exe
goto :eof

:error
echo.
echo Build failed. If it was a psutil/AccessDenied error, try:
echo     python -m pip install -U psutil
pause
exit /b 1
