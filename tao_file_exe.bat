@echo off
cd /d "%~dp0"
title Dong Goi & Toi Uu Dung Luong DaoRong_tool.exe (v10.0)
echo ============================================================
echo   DONG GOI & TOI UU DUNG LUONG: DaoRong_tool.exe v10.0
echo   - Ma hoa toan bo ma nguon bang PyArmor chong dich nguoc
echo   - Loai bo thu vien thua (giam ~40-50%% dung luong)
echo ============================================================
echo.

if not exist ".venv\Scripts\python.exe" goto :NO_VENV
echo [1/4] Dang cai dat PyInstaller va PyArmor...
".venv\Scripts\python.exe" -m pip install --quiet pyinstaller pyarmor
if errorlevel 1 goto :ERR

echo [2/4] Dang ma hoa ma nguon bang PyArmor...
if exist "build_obf" rmdir /s /q "build_obf"
".venv\Scripts\pyarmor.exe" gen -O build_obf main.py bot_engine.py vision.py actions.py capture_tool.py ocr_engine.py updater.py adb_controller.py smart_ai.py
if errorlevel 1 goto :FALLBACK_NO_OBF

if exist "build" rmdir /s /q "build"
if exist "dist\DaoRong_tool" rmdir /s /q "dist\DaoRong_tool"
echo [3/4] Dang dong goi DaoRong_tool.exe (da toi uu dung luong)...
copy /Y "app_icon.ico" "build_obf\app_icon.ico" >nul
pushd build_obf
"..\.venv\Scripts\python.exe" -m PyInstaller --noconfirm --onedir --windowed --uac-admin --name DaoRong_tool --icon=app_icon.ico --distpath ..\dist --workpath ..\build --add-data "pyarmor_runtime_000000;pyarmor_runtime_000000" --collect-all customtkinter --collect-all rapidocr_onnxruntime --exclude-module matplotlib --exclude-module scipy --exclude-module pandas --exclude-module pytest --exclude-module IPython --exclude-module PyQt5 --exclude-module PySide6 --exclude-module notebook --exclude-module sphinx main.py
set BUILD_ERR=%errorlevel%
popd
rmdir /s /q "build_obf"
if not "%BUILD_ERR%"=="0" goto :ERR
goto :COPY_ASSETS

:FALLBACK_NO_OBF
echo [LUU Y] PyArmor gap loi moi truong, dang dong goi truc tiep bang PyInstaller...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --onedir --windowed --uac-admin --name DaoRong_tool --icon=app_icon.ico --collect-all customtkinter --collect-all rapidocr_onnxruntime --exclude-module matplotlib --exclude-module scipy --exclude-module pandas --exclude-module pytest --exclude-module IPython --exclude-module PyQt5 --exclude-module PySide6 main.py
if errorlevel 1 goto :ERR

:COPY_ASSETS
echo [4/4] Dang chep config, templates, icon va don file thua...
copy /Y "config.json" "dist\DaoRong_tool\config.json" >nul
copy /Y "dragons_db.json" "dist\DaoRong_tool\dragons_db.json" >nul
copy /Y "app_icon.ico" "dist\DaoRong_tool\app_icon.ico" >nul
copy /Y "app_icon.png" "dist\DaoRong_tool\app_icon.png" >nul
xcopy /E /I /Y "templates" "dist\DaoRong_tool\templates" >nul

echo.
echo ============================================================
echo   HOAN TAT! App da toi uu nam tai:
echo   %CD%\dist\DaoRong_tool\DaoRong_tool.exe
echo   - Gui ban be lan dau: chay tao_ban_share_sach.bat
echo   - Day ban cap nhat Full: chay tao_ban_update.bat
echo   - Day ban va sieu nhe (~3-6MB): chay tao_ban_patch.bat
echo ============================================================
echo.
pause
exit /b 0

:NO_VENV
echo [LOI] Hay chay install_and_run.bat 1 lan truoc de tao .venv!
pause
exit /b 1

:ERR
echo [LOI] Dong goi that bai. Kiem tra ket noi mang roi thu lai.
pause
exit /b 1
