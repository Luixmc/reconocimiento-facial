@echo off
setlocal enabledelayedexpansion
title BioFace - Reconocimiento Facial (Dev)
cd /d "%~dp0"

echo ========================================
echo   BioFace - Modo Desarrollo
echo ========================================
echo.

REM Detectar Python automaticamente
set PYTHON_CMD=

REM Primero buscar Python real (no el stub de Microsoft Store)
set PYTHON_CMD=
for /f "delims=" %%i in ('where python 2^>nul') do (
    set "_test=%%i"
    REM Ignorar el stub de WindowsApps que redirige a Microsoft Store
    echo %%i | findstr /I /C:"WindowsApps" >nul 2>&1
    if errorlevel 1 (
        REM Verificar que realmente funciona
        "%%i" --version >nul 2>&1
        if not errorlevel 1 (
            set PYTHON_CMD=%%i
            goto :found_python
        )
    )
)

REM Buscar en rutas comunes de Windows (todas las versiones)
for %%p in (
    "%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python39\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python38\python.exe"
    "%ProgramFiles%\Python\Python314\python.exe"
    "%ProgramFiles%\Python\Python313\python.exe"
    "%ProgramFiles%\Python\Python312\python.exe"
    "%ProgramFiles%\Python\Python311\python.exe"
    "%ProgramFiles%\Python\Python310\python.exe"
    "%ProgramFiles%\Python\Python39\python.exe"
    "%ProgramFiles%\Python\Python38\python.exe"
    "C:\Python314\python.exe"
    "C:\Python313\python.exe"
    "C:\Python312\python.exe"
    "C:\Python311\python.exe"
    "C:\Python310\python.exe"
) do (
    if exist %%p (
        set PYTHON_CMD=%%p
        goto :found_python
    )
)

goto :no_python

:found_python
echo [OK] Python detectado: %PYTHON_CMD%
"%PYTHON_CMD%" --version
echo.

REM Verificar dependencias
echo Verificando dependencias...
"%PYTHON_CMD%" -c "import cv2, insightface, flask, requests" >nul 2>&1
if %errorlevel% neq 0 (
    echo Instalando dependencias...
    "%PYTHON_CMD%" -m pip install -r backend\requirements.txt
)
echo.

echo ========================================
echo   Iniciando BioFace...
echo   Flutter arranca el backend automaticamente
echo   Sin ventanas OpenCV - Todo en Flutter
echo ========================================
echo.

REM Iniciar Flutter (el BackendManager de Flutter inicia el backend solo)
flutter run

REM Al cerrar Flutter, matar cualquier proceso Python que haya quedado
echo Cerrando backend...

REM 1. Matar por PID file (backend.pid) si existe
if exist backend\backend.pid (
    set /p BACKEND_PID=<backend\backend.pid
    if not "!BACKEND_PID!" == "" (
        echo [PID file] Matando PID: !BACKEND_PID!
        taskkill /F /PID !BACKEND_PID! /T >nul 2>&1
    )
)

REM 2. Matar por nombre de imagen (python.exe + backend.exe)
echo [NUCLEAR] Matando procesos python.exe y backend.exe...
taskkill /F /IM python.exe /T >nul 2>&1
taskkill /F /IM backend.exe /T >nul 2>&1

REM 3. Matar por puerto 5050 (respaldo)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5050" ^| findstr "LISTENING" 2^>nul') do (
    echo [PUERTO] Matando PID: %%a
    taskkill /F /PID %%a >nul 2>&1
)

REM 4. Limpiar archivos residuales
echo [CLEANUP] Eliminando archivos residuales...

REM PID file
if exist backend\backend.pid del /f /q backend\backend.pid >nul 2>&1

REM Log file
if exist backend\backend.log del /f /q backend\backend.log >nul 2>&1

REM Cache de Python
if exist backend\__pycache__ rmdir /s /q backend\__pycache__ >nul 2>&1

REM Archivos de PyInstaller
if exist backend\build rmdir /s /q backend\build >nul 2>&1
if exist backend\dist rmdir /s /q backend\dist >nul 2>&1
if exist backend\backend.spec del /f /q backend\backend.spec >nul 2>&1

echo [CLEANUP] Archivos temporales eliminados.
echo Hecho.
exit /b 0

:no_python
echo [ERROR] Python no encontrado.
echo Por favor instala Python desde https://www.python.org/downloads/
echo Asegurate de marcar "Add Python to PATH" durante la instalacion.
pause
exit /b 1
