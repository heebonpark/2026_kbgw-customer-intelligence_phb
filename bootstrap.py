"""
Windows 원클릭 실행기의 실제 작업(가상환경 생성, 패키지 설치, GUI 실행)은
전부 여기서 한다 -- start_windows.bat 자체는 파이썬을 찾아서 이 스크립트를
실행하는 것 말고는 아무것도 하지 않는다.

이렇게 나눈 이유: cmd.exe의 배치파일 파서는 한글(cp949 2바이트 문자)이
섞인 echo 줄을 다루다가 종종 줄을 엉뚱한 지점에서 잘라버리는 고질적인
버그가 있다 (2바이트 문자의 두 번째 바이트 값이 우연히 '&', '|', '<', '>'
같은 특수문자와 겹치면 그 지점에서 명령이 끊긴 것처럼 오동작한다). UTF-8
BOM + chcp 65001 조합도, 순수 cp949 인코딩도 실제 Windows 환경에서 이
문제를 완전히 피하지 못했다. 파이썬의 print()는 이런 문제가 없으므로,
한글이 들어가는 안내 메시지는 전부 이쪽으로 옮겼다.
"""
import os
import subprocess
import sys


def _make_streams_safe():
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


_make_streams_safe()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(BASE_DIR, "venv")
VENV_PYTHON = os.path.join(VENV_DIR, "Scripts", "python.exe") if os.name == "nt" \
    else os.path.join(VENV_DIR, "bin", "python")


def _pause(message="계속하려면 Enter 키를 누르세요..."):
    try:
        input(message)
    except (EOFError, KeyboardInterrupt):
        pass


def main():
    print()
    print("=" * 60)
    print("  Data Intel PRO - Windows 원클릭 실행 스크립트")
    print("=" * 60)
    print()

    print(f"[1/4] 파이썬 확인 완료: {sys.version.split()[0]} ({sys.executable})")
    print()

    print("[2/4] 가상환경 확인 중...")
    if not os.path.exists(VENV_DIR):
        print("      최초 실행 환경을 구성합니다 (가상환경 생성 중, 잠시 기다려주세요)...")
        result = subprocess.run([sys.executable, "-m", "venv", VENV_DIR], stdin=subprocess.DEVNULL)
        if result.returncode != 0:
            print("      [오류] 가상환경 생성에 실패했습니다.")
            _pause()
            sys.exit(1)
    else:
        print("      기존 가상환경을 사용합니다.")

    if not os.path.exists(VENV_PYTHON):
        print(f"      [오류] 가상환경의 파이썬을 찾을 수 없습니다: {VENV_PYTHON}")
        print("      venv 폴더를 삭제한 뒤 다시 실행해주세요.")
        _pause()
        sys.exit(1)

    tk_check = subprocess.run([VENV_PYTHON, "-c", "import tkinter"], capture_output=True, stdin=subprocess.DEVNULL)
    if tk_check.returncode != 0:
        print("      [오류] 이 파이썬에는 tkinter(GUI 모듈)가 없어서 창이 뜨지 않습니다.")
        print('      python.org 에서 파이썬을 다시 설치하면서 설치 화면에서')
        print('      "tcl/tk and IDLE" 항목을 체크해주세요 (기본 설치에는 보통 포함됩니다).')
        _pause()
        sys.exit(1)
    print()

    print("[3/4] 필수 패키지 설치 확인 중... (최초 실행 시 몇 분 걸릴 수 있습니다)")
    req_path = os.path.join(BASE_DIR, "requirements.txt")
    # stdin=DEVNULL: pip must never sit waiting for a confirmation prompt it
    # will never get when run this way. timeout: a stuck resolver/download
    # should fail loudly after 10 minutes instead of hanging the window
    # forever with no explanation.
    try:
        pip_result = subprocess.run([
            VENV_PYTHON, "-m", "pip", "install",
            "--disable-pip-version-check", "-q", "-r", req_path,
        ], stdin=subprocess.DEVNULL, timeout=600)
        ok = pip_result.returncode == 0
    except subprocess.TimeoutExpired:
        print("      [경고] 패키지 설치가 10분 넘게 끝나지 않아 건너뜁니다 (네트워크 확인 필요할 수 있음).")
        ok = False
    if not ok:
        print("      [경고] 일부 패키지 설치에 실패했을 수 있습니다. 계속 진행합니다...")
    else:
        print("      패키지 준비 완료.")
    print()

    print("[4/4] Data Intel PRO를 실행합니다...")
    print("=" * 60)
    print()
    run_result = subprocess.run([VENV_PYTHON, os.path.join(BASE_DIR, "gui_app.py")])
    if run_result.returncode != 0:
        print()
        print("[오류] 실행 중 오류가 발생했습니다. 위 로그를 확인해주세요.")

    print()
    _pause()


if __name__ == "__main__":
    main()
