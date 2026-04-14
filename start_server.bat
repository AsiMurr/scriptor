@echo off
cd /d "d:\AI_Projects\3. Voice_transmite\backend"
"C:\Users\Asi\AppData\Local\Programs\Python\Python310\python.exe" -m uvicorn main:app --host 127.0.0.1 --port 8000
pause
