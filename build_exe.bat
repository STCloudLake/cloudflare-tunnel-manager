@echo off
echo ============================================
echo Cloudflare Tunnel Manager - 编译为 EXE
echo ============================================

cd /d "%~dp0"

echo.
echo [1/2] 清理旧构建...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build

echo.
echo [2/2] 编译中...
pyinstaller --onefile --windowed ^
    --name "CloudflareTunnelManager" ^
    --icon="cloudflare.ico" ^
    --add-data="cloudflare.ico;." ^
    --hidden-import=pystray ^
    --hidden-import=PIL ^
    --hidden-import=PIL.Image ^
    tunnel_gui.py

echo.
echo ============================================
echo 完成! EXE 位于 dist\CloudflareTunnelManager.exe
echo ============================================
pause
