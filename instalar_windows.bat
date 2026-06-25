@echo off
cd /d %~dp0
echo Instalando dependencias do HEPTA Jornada...
python -m venv .venv
call .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
echo.
echo Instalacao concluida. Para iniciar, execute iniciar_windows.bat
pause
