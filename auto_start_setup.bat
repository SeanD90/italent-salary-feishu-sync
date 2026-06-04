@echo off
chcp 65001 >nul
title 薪酬同步工具 - 开机自启动设置

echo ============================================================
echo   薪酬同步工具 - 开机自启动设置
echo ============================================================
echo.

:: 获取项目路径
set "PROJECT_DIR=%~dp0"
set "TARGET=%PROJECT_DIR%start_service.bat"
set "STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "SHORTCUT=%STARTUP_DIR%\薪酬同步服务.lnk"

:: 检查 start_service.bat
if not exist "%TARGET%" (
    echo ❌ 未找到 start_service.bat
    echo    请确认该文件与本脚本在同一目录下
    pause
    exit /b 1
)

:: 检测当前状态
if exist "%SHORTCUT%" (
    echo 当前状态：✅ 已设置开机自启动
    echo.
    echo   [1] 重新设置
    echo   [2] 移除开机自启动
    echo   [3] 退出
    echo.
    choice /c 123 /m "请选择操作"
    if errorlevel 3 goto :eof
    if errorlevel 2 goto :remove
    if errorlevel 1 goto :setup
) else (
    echo 当前状态：❌ 未设置开机自启动
    echo.
    echo   [1] 设置开机自启动
    echo   [2] 退出
    echo.
    choice /c 12 /m "请选择操作"
    if errorlevel 2 goto :eof
    if errorlevel 1 goto :setup
)

:setup
echo.
echo 正在设置开机自启动...

:: 如果已存在先删除
if exist "%SHORTCUT%" del "%SHORTCUT%" >nul 2>&1

:: 使用 PowerShell 创建快捷方式（窗口最小化启动）
powershell -NoProfile -Command ^
    "$ws = New-Object -ComObject WScript.Shell; ^
     $sc = $ws.CreateShortcut('%SHORTCUT%'); ^
     $sc.TargetPath = '%TARGET%'; ^
     $sc.WorkingDirectory = '%PROJECT_DIR%'; ^
     $sc.Description = '薪酬数据自动同步服务'; ^
     $sc.WindowStyle = 7; ^
     $sc.Save()"

if exist "%SHORTCUT%" (
    echo.
    echo ✅ 开机自启动设置成功！
    echo.
    echo    下次开机登录后，同步服务会自动在后台启动。
    echo    窗口会最小化运行，不影响日常使用。
    echo    如需取消，再次运行本脚本选择「移除」即可。
) else (
    echo.
    echo ❌ 设置失败，请尝试手动操作：
    echo    1. 按 Win+R，输入 shell:startup，回车
    echo    2. 右键 start_service.bat → 发送到 → 桌面快捷方式
    echo    3. 把桌面上生成的快捷方式剪切到刚才打开的文件夹中
)
echo.
pause
goto :eof

:remove
echo.
echo 正在移除开机自启动...
del "%SHORTCUT%" >nul 2>&1
if not exist "%SHORTCUT%" (
    echo.
    echo ✅ 已移除开机自启动
    echo    下次开机后不会自动启动同步服务。
) else (
    echo.
    echo ❌ 移除失败，请手动操作：
    echo    按 Win+R，输入 shell:startup，回车
    echo    删除「薪酬同步服务」快捷方式
)
echo.
pause
goto :eof
