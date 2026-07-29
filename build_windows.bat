@echo off
chcp 65001 > nul
echo ===================================================
echo Data Intel PRO - 윈도우용 실행파일 빌드 스크립트
echo ===================================================
echo.
echo 이 스크립트는 PyInstaller를 사용하여 gui_app.py를
echo 단일 실행 파일(.exe)로 변환합니다.
echo 파이썬과 관련 패키지(pandas, openpyxl, pyinstaller 등)가
echo 윈도우에 설치되어 있어야 합니다.
echo.
pause

echo.
echo PyInstaller로 빌드 시작...
pyinstaller --noconfirm --onedir --windowed --add-data "app;app"  gui_app.py

echo.
echo 빌드가 완료되었습니다!
echo 실행 파일은 'dist/gui_app/' 폴더 내에 있습니다.
echo.
pause
