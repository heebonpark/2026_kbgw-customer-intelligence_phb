@echo off
setlocal enabledelayedexpansion
chcp 65001 > nul
set PYTHONUTF8=1
title Data Intel PRO - 실행기

echo.
echo   ══════════════════════════════════════════════════════
echo     📊  Data Intel PRO — Windows 원클릭 실행 스크립트
echo   ══════════════════════════════════════════════════════
echo.

rem ---- [1/4] 파이썬 명령 자동 탐지 -- "python"이 없는 설치(python3/py 런처만
rem      있는 경우)에서도 자동으로 맞는 명령을 찾아 텍스트 자동보정한다.
echo   [1/4] 파이썬 설치 확인 중...
set "PYCMD="
for %%P in (python python3 py) do (
    if not defined PYCMD (
        %%P --version >nul 2>&1
        if not errorlevel 1 set "PYCMD=%%P"
    )
)
if not defined PYCMD (
    echo.
    echo   ❌ 파이썬(Python)을 찾을 수 없습니다.
    echo      python.org 에서 설치 후 다시 실행해주세요.
    echo      ^(설치 화면에서 "Add Python to PATH" 체크 필수^)
    echo.
    pause
    exit /b 1
)
for /f "tokens=*" %%V in ('%PYCMD% --version 2^>^&1') do echo         ✅ %%V ^(%PYCMD%^)
echo.

rem ---- [2/4] 가상환경 구성 ----
echo   [2/4] 가상환경 확인 중...
if not exist venv (
    echo         최초 실행 환경을 구성합니다 ^(가상환경 생성^)...
    %PYCMD% -m venv venv
    if errorlevel 1 (
        echo   ❌ 가상환경 생성에 실패했습니다.
        pause
        exit /b 1
    )
) else (
    echo         ✅ 기존 가상환경을 사용합니다.
)
call venv\Scripts\activate.bat
echo.

rem ---- [3/4] 패키지 설치 ----
echo   [3/4] 필수 패키지 설치 확인 중... ^(최초 실행 시 몇 분 걸릴 수 있습니다^)
python -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 (
    echo   ⚠️  일부 패키지 설치에 실패했을 수 있습니다. 계속 진행합니다...
) else (
    echo         ✅ 패키지 준비 완료.
)
echo.

rem ---- [4/4] 실행 ----
echo   [4/4] Data Intel PRO를 실행합니다...
echo   ══════════════════════════════════════════════════════
echo.
python gui_app.py
if errorlevel 1 (
    echo.
    echo   ❌ 실행 중 오류가 발생했습니다. 위 로그를 확인해주세요.
)

echo.
pause
