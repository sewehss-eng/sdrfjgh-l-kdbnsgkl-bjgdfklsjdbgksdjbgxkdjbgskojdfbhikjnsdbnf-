"""Запуск бота: Windows-скрипт.

Использование:
    run.bat
"""

@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ==========================================
echo   Bot de dostup k kanalam - zapusk
echo ==========================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [!] Python ne najden. Ustanovite Python 3.10+ s python.org
    pause
    exit /b 1
)

if not exist venv (
    echo [1/3] Sozdanie virtualnogo okruzheniya...
    python -m venv venv
)

call venv\Scripts\activate.bat

if not exist "venv\Scripts\python.exe" (
    echo [!] Ne udalos sozdat venv
    pause
    exit /b 1
)

echo [2/3] Ustanovka zavisimostej...
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt

if not exist .env (
    echo [!] Fajl .env ne najden. Skopirujte .env.example v .env i zapolnite.
    pause
    exit /b 1
)

echo [3/3] Zapusk bota...
python main.py

pause
