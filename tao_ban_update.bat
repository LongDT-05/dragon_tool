@echo off
cd /d "%~dp0"
title Tao Goi Update Cho GitHub Releases
echo ============================================================
echo   TAO GOI UPDATE TU DONG (KHONG XOA SETUP NGUOI DUNG)
echo ============================================================
echo.

if not exist "dist\DaoRong_tool\DaoRong_tool.exe" goto :NO_EXE

set /p NEW_VER="Nhap so phien ban moi (vi du: 6.1): "
set /p NEW_NOTES="Nhap ghi chu ban cap nhat (vi du: Sua loi chuc phuc): "
set GH_REPO=LongDT-05/dragon_tool

if not exist "release" mkdir "release"
if exist "release\tmp_upd" rmdir /s /q "release\tmp_upd"
if exist "release\DaoRong_tool_Update.zip" del /f /q "release\DaoRong_tool_Update.zip"
mkdir "release\tmp_upd"

echo [1/2] Dang dong goi DaoRong_tool_Update.zip (chi exe + _internal + icon)...
copy /Y "dist\DaoRong_tool\DaoRong_tool.exe" "release\tmp_upd\DaoRong_tool.exe" >nul
if exist "dist\DaoRong_tool\dragons_db.json" copy /Y "dist\DaoRong_tool\dragons_db.json" "release\tmp_upd\dragons_db.json" >nul
xcopy /E /I /Y "dist\DaoRong_tool\_internal" "release\tmp_upd\_internal" >nul
if exist "dist\DaoRong_tool\app_icon.ico" copy /Y "dist\DaoRong_tool\app_icon.ico" "release\tmp_upd\app_icon.ico" >nul
if exist "dist\DaoRong_tool\app_icon.png" copy /Y "dist\DaoRong_tool\app_icon.png" "release\tmp_upd\app_icon.png" >nul
powershell -NoProfile -Command "Compress-Archive -Path 'release\tmp_upd\*' -DestinationPath 'release\DaoRong_tool_Update.zip' -Force"
rmdir /s /q "release\tmp_upd"

echo [2/2] Dang tao file release\version.json...
".venv\Scripts\python.exe" -c "import json, os, pathlib; data={'version': os.environ['NEW_VER'], 'download_url': f'https://github.com/{os.environ[chr(71)+chr(72)+chr(95)+chr(82)+chr(69)+chr(80)+chr(79)]}/releases/latest/download/DaoRong_tool_Update.zip', 'notes': os.environ['NEW_NOTES']}; pathlib.Path('release/version.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')"

echo.
echo ============================================================
echo   HOAN TAT! Tai 2 file sau len GitHub Releases (New Release):
echo   1. release\DaoRong_tool_Update.zip
echo   2. release\version.json
echo   Nguoi dung chi can bam nut 'Cap nhat' trong app la tu len ban moi!
echo ============================================================
echo.
pause
exit /b 0

:NO_EXE
echo [LOI] Hay chay tao_file_exe.bat truoc de tao dist\DaoRong_tool\DaoRong_tool.exe!
pause
exit /b 1
