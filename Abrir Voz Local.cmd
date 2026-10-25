@echo off
set "HF_HOME=%~dp0data\hf"
start "Voz Local" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0app.py"
