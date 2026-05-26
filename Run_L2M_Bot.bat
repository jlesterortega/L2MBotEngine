@echo off
:: Check for Administrator privileges
net session >nul 2>&1
if %errorLevel% == 0 (
    echo [✓] Running with Administrator privileges...
) else (
    echo [x] Requesting Administrator privileges...
    powershell -Command "Start-Process '%0' -Verb RunAs"
    exit /b
)

:: Navigate to your exact game bot directory
cd /d "D:\repo\L2MBotEngine"

:: Execute your main script using your system Python interpreter
python main.py

pause