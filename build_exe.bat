@echo off
setlocal
cd /d "%~dp0"
echo ========================================
echo Building ShortLinkGenerator.exe
echo ========================================
where python >nul 2>nul
if errorlevel 1 (
  echo ERROR: Python was not found. Install Python 3.10+ and enable "Add Python to PATH".
  pause
  exit /b 1
)
python -m pip install --upgrade pip
if errorlevel 1 goto :fail
python -m pip install --upgrade pyinstaller pystray pillow
if errorlevel 1 goto :fail
python -m PyInstaller --noconfirm --clean --onefile --windowed --name ShortLinkGenerator shortlink_generator.py
if errorlevel 1 goto :fail
echo.
echo BUILD SUCCEEDED. Output: dist\ShortLinkGenerator.exe
echo Copy this single EXE wherever you want to use it.
pause
exit /b 0
:fail
echo.
echo BUILD FAILED. Check the error messages above and verify your Internet connection.
pause
exit /b 1
