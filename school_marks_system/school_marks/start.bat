@echo off
cd /d "%~dp0"
echo Checking required packages...
python -m pip install -q -r requirements.txt
if errorlevel 1 (
  echo.
  echo Could not install the packages. Check that Python is installed and the internet works.
  pause
  exit /b 1
)
python app.py
pause
