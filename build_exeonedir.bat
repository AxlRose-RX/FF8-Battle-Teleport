@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

rem --- Set console color to bright cyan on black ---
color 0B

where py >nul 2>nul
if errorlevel 1 (
  color 0C
  echo Python launcher ^(py.exe^) not found.
  pause
  exit /b 1
)

rem --- Clean up previous build artifacts ---
echo ========================================
echo Cleaning up previous build artifacts...
echo ========================================

if exist "__pycache__" (
  echo   Removing __pycache__ folder...
  rmdir /s /q "__pycache__" 2>nul
)

if exist "build" (
  echo   Removing build folder...
  rmdir /s /q "build" 2>nul
)

if exist "dist" (
  echo   Removing dist folder...
  rmdir /s /q "dist" 2>nul
)

if exist "ff8_battle_moment.spec" (
  echo   Removing ff8_battle_moment.spec...
  del /f /q "ff8_battle_moment.spec" 2>nul
)

echo   Cleanup complete!
echo.

rem --- deps (ff8_battle_moment uses only pymem beyond the stdlib + tkinter) ---
echo ========================================
echo Installing/updating dependencies...
echo ========================================
py -3 -m pip install --upgrade pip pyinstaller pymem

set "MAIN_PY=ff8_battle_moment.py"
set "DIST_DIR=dist\ff8_battle_moment"

rem --- build ---
echo.
echo ========================================
echo Starting PyInstaller build...
echo ========================================
py -3 -m PyInstaller --noconfirm --clean --onedir --icon=icon.ico --name ff8_battle_moment ^
  --collect-all pymem ^
  --hidden-import tkinter ^
  --exclude-module pytest ^
  --windowed ^
  "%MAIN_PY%"

if errorlevel 1 (
  color 0C
  echo.
  echo ========================================
  echo Build failed!
  echo ========================================
  pause
  exit /b 1
)

if not exist "%DIST_DIR%\_internal" mkdir "%DIST_DIR%\_internal"

echo.
echo ========================================
echo Copying additional files into _internal...
echo ========================================
for %%F in ("icon.ico" "ff8_battle_labels.json") do (
  if exist "%%~fF" (
    copy /Y "%%~fF" "%DIST_DIR%\_internal\" >nul
    echo   [OK] Copied to _internal: %%~F
  ) else (
    echo   [WARNING] %%~F not found, skipping...
  )
)

echo.
echo Built to "%CD%\%DIST_DIR%\"

rem --- Create zip archive ---
echo.
echo ========================================
echo Creating zip archive...
echo ========================================
cd "%DIST_DIR%\.."
if exist "ff8_battle_moment.zip" del "ff8_battle_moment.zip"
powershell -Command "Compress-Archive -Path 'ff8_battle_moment\*' -DestinationPath 'ff8_battle_moment.zip' -CompressionLevel Optimal"
cd /d "%~dp0"

rem --- Success! Change to green ---
color 0A
echo.
echo ========================================
echo            BUILD COMPLETE!
echo ========================================
echo.
echo Executable: 
echo   %CD%\%DIST_DIR%\ff8_battle_moment.exe
echo.
echo Zip archive: 
echo   %CD%\dist\ff8_battle_moment.zip
echo.
echo ========================================
echo.

pause
color
