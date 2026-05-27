@echo off
title BIVIO — Comparatore Prezzi
cd /d D:\BIVIO\backend

set PYTHON=C:\Users\Luca\AppData\Local\Programs\Python\Python314\python.exe
set CLOUDFLARED=C:\ProgramData\chocolatey\bin\cloudflared.exe

echo [1/3] Avvio backend...
start "" /B "%PYTHON%" -m uvicorn main:app --port 8000

echo [2/3] Avvio tunnel Cloudflare...
timeout /t 3 /nobreak >nul
start "" /B "%CLOUDFLARED%" tunnel run bivio

echo [3/3] Apertura browser...
timeout /t 3 /nobreak >nul
start "" "D:\BIVIO\bivio.html"

echo.
echo BIVIO attivo su:
echo   Locale:   http://localhost:8000
echo   Pubblico: https://bivio.gianlucademontis.xyz
echo.
echo Chiudi questa finestra per fermare tutto.
echo.

:loop
timeout /t 60 /nobreak >nul
goto loop
