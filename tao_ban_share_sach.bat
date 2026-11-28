@echo off
cd /d "%~dp0"
title Tao Ban Chia Se DaoRong_tool.zip
echo ============================================================
echo   TAO BAN CHIA SE: DaoRong_tool.zip (CO SAN NUT GAME)
echo   Nguoi nhan giai nen ra thu muc DaoRong_tool la dung ngay!
echo ============================================================
echo.

if not exist "dist\DaoRong_tool\DaoRong_tool.exe" goto :NO_EXE
if not exist "release" mkdir "release"
if exist "release\DaoRong_tool" rmdir /s /q "release\DaoRong_tool"
if exist "DaoRong_tool.zip" del /f /q "DaoRong_tool.zip"

echo [1/3] Dang sao chep bo app DaoRong_tool...
xcopy /E /I /Y "dist\DaoRong_tool" "release\DaoRong_tool" >nul
if errorlevel 1 goto :ERR

echo [2/3] Dang giu nguyen templates/ui va don sach anh rong ca nhan...
".venv\Scripts\python.exe" prepare_share.py "release\DaoRong_tool"
if errorlevel 1 goto :ERR

echo [3/3] Dang nen thanh DaoRong_tool.zip...
powershell -NoProfile -Command "Compress-Archive -Path 'release\DaoRong_tool' -DestinationPath 'DaoRong_tool.zip' -Force"
if errorlevel 1 goto :ERR
rmdir /s /q "release\DaoRong_tool"

echo.
echo ============================================================
echo   HOAN TAT! File chia se cua ban nam tai:
echo   %CD%\DaoRong_tool.zip
echo   (Giai nen ra thu muc DaoRong_tool -> mo DaoRong_tool.exe!)
echo ============================================================
echo.
pause
exit /b 0

:ERR
echo [LOI] Khong the tao ban chia se. Kiem tra buoc bao loi o tren.
pause
exit /b 1

:NO_EXE
echo [LOI] Chua co dist\DaoRong_tool\DaoRong_tool.exe!
echo Hay chay tao_file_exe.bat truoc de dong goi app.
pause
exit /b 1
