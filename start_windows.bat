@echo off
setlocal enabledelayedexpansion
title Data Intel PRO

rem This file is intentionally kept in plain ASCII with no Korean text.
rem cmd.exe's batch parser has a long-standing bug with cp949 (Korean) text:
rem the second byte of some Hangul characters happens to match a shell
rem special character (&, |, <, >, ^), and the parser splits the line right
rem there as if that byte were real syntax. Every Korean status message
rem lives in bootstrap.py instead, where Python's own text handling doesn't
rem have this problem.

set "PYCMD="
for %%P in (python python3 py) do (
    if not defined PYCMD (
        %%P --version >nul 2>&1
        if not errorlevel 1 set "PYCMD=%%P"
    )
)

if not defined PYCMD (
    echo Python was not found on this PC.
    echo Please install it from python.org and check "Add Python to PATH" during setup.
    pause
    exit /b 1
)

%PYCMD% "%~dp0bootstrap.py"
