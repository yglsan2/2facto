@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
set FOLIO_OPEN=1
set FOLIO_PORT=8765

py -c "import flask,fpdf,facturx" 2>nul || py -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo 2facto n'a pas pu demarrer. Fermez cette fenetre, puis reessayez.
  pause
  exit /b 1
)

py -c "import socket; s=socket.create_connection(('127.0.0.1',8765),1); s.close()" 2>nul
if not errorlevel 1 (
  start "" "http://127.0.0.1:8765/"
  echo.
  echo 2facto est deja ouvert dans le navigateur.
  echo.
  pause
  exit /b 0
)

echo.
echo 2facto s'ouvre dans le navigateur.
echo Laissez cette fenetre ouverte. Fermez-la pour quitter.
echo.
py -m folio
echo.
echo 2facto s'est arrete.
pause
