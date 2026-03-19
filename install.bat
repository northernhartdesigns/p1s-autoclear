@echo off
cd /d "%~dp0"
echo ========================================
echo  P1S Auto-Clear - Auto Install
echo ========================================
echo.
echo Upgrading pip...
python -m pip install --upgrade pip
echo.
echo Installing P1S Auto-Clear (with preview + run-loop)...
pip install -e ".[preview,run_loop]"
if errorlevel 1 (
    echo.
    echo Install failed. Trying without extras...
    pip install -e .
)
echo.
echo ========================================
echo  Done.
echo  Run: python -m p1s_autoclear
echo  Or double-click: Launch P1S Auto-Clear.vbs (no console) or Launch P1S Auto-Clear.bat
echo ========================================
pause
