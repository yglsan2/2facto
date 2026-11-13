@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
py -c "import PySide6.QtWidgets" 2>nul || py -m pip install -r requirements-pc.txt
echo.
echo 2 facto s'ouvre dans une fenetre. Laissez cette fenetre noire ouverte.
echo Fermez-la pour quitter.
echo.
py -m folio.desk
pause
