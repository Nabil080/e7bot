@echo off
rem Starts the bot from this folder without a console window. With STOVE, whose game runs as administrator,
rem the bot asks for administrator rights itself. Errors go to logs/bot.log or a message box.
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" main.py %*
