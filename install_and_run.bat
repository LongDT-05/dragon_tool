@echo off
cd /d "%~dp0"
title Dao Rong Auto - Lai Rong & Hoa Bui
echo ====================================================
echo   DAO RONG AUTO - CAI DAT VA KHOI CHAY
echo   Thu muc: %CD%
echo ====================================================
echo.

if not exist "main.py" goto :NO_MAIN

set PY_CMD=
py -3 --version >nul 2>&1
if %errorlevel%==0 set PY_CMD=py -3
if defined PY_CMD goto :FOUND_PY

python --version >nul 2>&1
if %errorlevel%==0 set PY_CMD=python
if defined PY_CMD goto :FOUND_PY

echo [LOI] Khong tim thay Python tren may!
echo Hay tai va cai Python 3.10+ tu https://www.python.org/downloads/
echo Nho tich chon "Add Python to PATH" khi cai dat.
echo.
pause
exit /b 1

:FOUND_PY
echo [OK] Phat hien Python:
%PY_CMD% --version
echo.

if exist ".venv\Scripts\python.exe" goto :INSTALL_DEPS
echo [1/2] Dang tao moi truong ao (.venv)...
%PY_CMD% -m venv .venv
if errorlevel 1 goto :RUN_DIRECT

:INSTALL_DEPS
echo [2/2] Dang cai dat thu vien (lan dau mat 1-2 phut)...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :PIP_FAIL

echo.
echo [OK] Dang mo giao dien Dao Rong Auto...
".venv\Scripts\python.exe" main.py
echo.
pause
exit /b 0

:RUN_DIRECT
echo [LUU Y] Khong tao duoc .venv, dang cai truc tiep...
%PY_CMD% -m pip install -r requirements.txt
if errorlevel 1 goto :PIP_FAIL
%PY_CMD% main.py
echo.
pause
exit /b 0

:NO_MAIN
echo [LOI] Khong tim thay file main.py trong thu muc hien tai!
echo Hay giai nen TOAN BO file trong DaoRongAuto.zip ra mot thu muc rieng roi chay lai.
echo.
pause
exit /b 1

:PIP_FAIL
echo.
echo [LOI] Cai thu vien that bai. Kiem tra ket noi mang roi thu lai.
echo.
pause
exit /b 1
