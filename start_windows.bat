@echo off
chcp 65001 > nul
echo ===================================================
echo Data Intel PRO - Windows 원클릭 실행 스크립트
echo ===================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [오류] 파이썬(Python)이 설치되어 있지 않거나 환경변수에 등록되지 않았습니다.
    echo 파이썬을 설치하신 후 다시 실행해주세요. (설치시 "Add Python to PATH" 체크 필수)
    pause
    exit /b
)

if not exist venv (
    echo [안내] 최초 실행 환경을 구성합니다. (가상환경 생성 중...)
    python -m venv venv
)

echo [안내] 가상환경 활성화 및 필수 패키지 점검...
call venv\Scripts\activate.bat

echo [안내] 패키지 설치를 진행합니다...
pip install -r requirements.txt > nul 2>&1

echo.
echo [안내] Data Intel PRO (GUI) 를 실행합니다!
set PYTHONUTF8=1
python gui_app.py

pause
