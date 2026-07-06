@echo off
setlocal enableextensions enabledelayedexpansion

cd /d "%~dp0"

echo === SilenceCut build ===
echo.

rem Prefer D:\Programs\Python if it exists, then fall back to PATH.
if exist "D:\Programs\Python\python.exe" (
  set "PATH=D:\Programs\Python;D:\Programs\Python\Scripts;%PATH%"
)

where python >nul 2>&1
if errorlevel 1 (
  echo [HATA] Python PATH'te bulunamadi.
  echo         D:\Programs\Python\python.exe konumuna Python 3.11+ yukleyin.
  exit /b 1
)

where npm >nul 2>&1
if errorlevel 1 (
  echo [HATA] npm PATH'te bulunamadi.
  exit /b 1
)

echo [1/4] Python sanal ortami hazirlaniyor...
if not exist .venv (
  python -m venv .venv || goto :fail
)
call .venv\Scripts\activate.bat || goto :fail

echo [2/4] Python bagimliliklari yukleniyor...
python -m pip install --upgrade pip wheel >nul
python -m pip install -r backend\requirements.txt || goto :fail
python -m pip install pyinstaller || goto :fail

echo [3/4] Frontend build ediliyor...
pushd frontend
rem Re-run npm install whenever package files have changed.
set "REINSTALL=0"
if not exist node_modules set "REINSTALL=1"
if exist package.json (
  if exist node_modules (
    for %%I in (package.json) do set "PKG_TS=%%~tI"
    for %%I in (node_modules) do set "NM_TS=%%~tI"
    if "!PKG_TS!" gtr "!NM_TS!" set "REINSTALL=1"
  )
)
if "!REINSTALL!"=="1" (
  call npm install || (popd & goto :fail)
)
call npm run build || (popd & goto :fail)
popd

echo [4/4] PyInstaller calistiriliyor...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
pyinstaller silencecut.spec --noconfirm || goto :fail

rem Whisper modeli daha once indirildiyse exe'nin yanina kopyala ki
rem kullanici ilk altyazi uretiminde tekrar 460 MB indirmek zorunda kalmasin.
if exist models (
  echo Whisper modeli dist\models icine kopyalaniyor...
  xcopy /e /i /q models dist\models >nul
)

echo.
echo === Tamamlandi ===
echo Cikti: dist\SilenceCut.exe
exit /b 0

:fail
echo.
echo *** Build basarisiz ***
exit /b 1
