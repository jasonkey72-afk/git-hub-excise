@echo off
REM ============================================================
REM jobScenario - Windows 실행파일(exe) 빌드 스크립트
REM
REM 사전 준비:
REM   1) Windows PC에 Python 3.9 이상 설치
REM   2) Microsoft Edge (또는 Chrome) 설치
REM
REM 사용법:
REM   1) 이 폴더에서 build_jobscenario.bat 파일을 더블클릭
REM   2) 빌드가 끝나면 dist\jobScenario.exe 파일이 생성됩니다
REM   3) 그 exe 파일 하나만 부서원들에게 복사해서 배포하면 됩니다
REM      (부서원 PC에는 Python 설치가 필요 없습니다)
REM
REM 참고: 기본 연결 방식은 드라이버(msedgedriver.exe)가 필요 없습니다.
REM       설치된 Edge 에 직접 연결하므로 브라우저가 업데이트돼도 그대로 동작합니다.
REM       예비용 selenium 방식을 쓸 때만 드라이버가 필요합니다.
REM ============================================================

setlocal

echo [1/2] 필요한 패키지를 설치합니다...
python -m pip install --upgrade pip
python -m pip install selenium websocket-client pyinstaller
if errorlevel 1 (
    echo 패키지 설치에 실패했습니다. Python이 설치되어 있는지 확인해 주세요.
    pause
    exit /b 1
)

echo [2/2] 실행파일을 빌드합니다...
python -m PyInstaller --noconfirm --onefile --windowed ^
    --name "jobScenario" ^
    --collect-all selenium ^
    --collect-all websocket ^
    jobscenario_main.py

if errorlevel 1 (
    echo 빌드에 실패했습니다. 위의 오류 메시지를 확인해 주세요.
    pause
    exit /b 1
)

echo.
echo 빌드가 완료되었습니다.
echo   결과물: dist\jobScenario.exe
echo   이 파일만 부서원들에게 배포하면 됩니다.
echo.
pause
