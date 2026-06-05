@echo off
cd /d "%~dp0"
"C:\Users\pmcuo\AppData\Local\Python\Pythoncore-3.14-64\python.exe" -m uvicorn webapp.main:app --host 127.0.0.1 --port 8765
