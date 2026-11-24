@echo off
rem Zapasowy start agenta, gdyby AIMusicAgent.exe nie dal sie uruchomic.
rem Uzycie: Uruchom-Agenta.cmd [--dry-run | --auth | --check]
setlocal
set "WS=%AIMUSIC_HOME%"
if "%WS%"=="" for /f "tokens=2,*" %%a in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v AIMUSIC_HOME 2^>nul ^| find "AIMUSIC_HOME"') do set "WS=%%b"
if "%WS%"=="" (
    echo Nie znaleziono AIMUSIC_HOME - uruchom najpierw Uruchom-Setup.cmd.
    pause
    exit /b 1
)
set PYTHONUTF8=1
"%WS%\.venv\Scripts\python.exe" "%WS%\app\run.py" %*
set RC=%errorlevel%
echo.
echo Kod zakonczenia: %RC%
pause
exit /b %RC%
