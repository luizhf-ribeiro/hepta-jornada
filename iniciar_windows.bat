@echo off
cd /d %~dp0
echo Iniciando HEPTA Jornada...
if exist .venv\Scripts\activate (
  call .venv\Scripts\activate
)
python run.py
pause
