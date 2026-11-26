@echo off
cd /d "%~dp0"
title Tao Ban Va Cap Nhat Sieu Nhe (Patch ~3-6MB)
echo ============================================================
echo   TAO GOI CAP NHAT SIEU NHE (KHONG CHUA _internal)
echo   - Chi dong goi DaoRong_tool.exe + dragons_db + icon + UI
echo   - Dung luong chi ~3-6 MB thay vi >100 MB, update trong 2 giay!
echo   - Luu y: Chi dung khi KHONG cai them thu vien Python moi.
echo ============================================================
echo.

if not exist "dist\DaoRong_tool\DaoRong_tool.exe" goto :NO_EXE

set /p NEW_VER="Nhap so phien ban moi (vi du: 9.1): "
set /p NEW_NOTES="Nhap ghi chu ban cap nhat: "
set GH_REPO=LongDT-05/dragon_tool

if not exist "release" mkdir "release"
if exist "release\tmp_patch" rmdir /s /q "release\tmp_patch"
if exist "release\DaoRong_tool_Update.zip" del /f /q "release\DaoRong_tool_Update.zip"
mkdir "release\tmp_patch"

echo [1/2] Dang dong goi ban va sieu nhe...
copy /Y "dist\DaoRong_tool\DaoRong_tool.exe" "release\tmp_patch\DaoRong_tool.exe" >nul
if exist "dist\DaoRong_tool\dragons_db.json" copy /Y "dist\DaoRong_tool\dragons_db.json" "release\tmp_patch\dragons_db.json" >nul
if exist "dist\DaoRong_tool\app_icon.ico" copy /Y "dist\DaoRong_tool\app_icon.ico" "release\tmp_patch\app_icon.ico" >nul
if exist "dist\DaoRong_tool\app_icon.png" copy /Y "dist\DaoRong_tool\app_icon.png" "release\tmp_patch\app_icon.png" >nul
if exist "dist\DaoRong_tool\templates\ui" xcopy /E /I /Y "dist\DaoRong_tool\templates\ui" "release\tmp_patch\templates\ui" >nul
powershell -NoProfile -Command "Compress-Archive -Path 'release\tmp_patch\*' -DestinationPath 'release\DaoRong_tool_Update.zip' -Force"
rmdir /s /q "release\tmp_patch"

echo [2/2] Dang tao file release\version.json (patch=true)...
".venv\Scripts\python.exe" -c "import json, os, pathlib; data={'version': os.environ['NEW_VER'], 'patch': True, 'download_url': f'https://github.com/{os.environ[chr(71)+chr(72)+chr(95)+chr(82)+chr(69)+chr(80)+chr(79)]}/releases/latest/download/DaoRong_tool_Update.zip', 'notes': os.environ['NEW_NOTES']}; pathlib.Path('release/version.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')"

echo.
echo ============================================================
echo   HOAN TAT! Tai 2 file trong thu muc release\ len GitHub Releases:
echo   1. release\DaoRong_tool_Update.zip (sieu nhe!)
echo   2. release\version.json
echo ============================================================
echo.
pause
exit /b 0

:NO_EXE
echo [LOI] Hay chay tao_file_exe.bat truoc!
pause
exit /b 1
