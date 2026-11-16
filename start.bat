@echo off
cd /d "%~dp0"
if not exist seen.json python bot.py --init
rem Lager-Suche in eigenem minimiertem Fenster
start "Lagerbot" /min python bot.py --profile lager --loop 300
rem Wohnungs-Suche in diesem Fenster
python bot.py --loop 300
