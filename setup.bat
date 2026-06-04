@echo off
chcp 65001 >nul
title 薪酬同步工具 - 初始化安装

echo ============================================================
echo   薪酬同步工具 - 初始化安装
echo ============================================================
echo.

:: ── 检查 Python ──
echo [1/5] 检查 Python 环境...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo ❌ 未检测到 Python！
    echo    请先安装 Python 3.10 或以上版本：
    echo    下载地址：https://www.python.org/downloads/
    echo    安装时务必勾选「Add Python to PATH」
    echo.
    pause
    exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo    ✅ Python %PYVER%

:: ── 检查 pip ──
echo.
echo [2/5] 检查 pip...
python -m pip --version >nul 2>&1
if %errorlevel% neq 0 (
    echo    ❌ pip 不可用，尝试安装...
    python -m ensurepip --default-pip
)
echo    ✅ pip 可用

:: ── 安装依赖 ──
echo.
echo [3/5] 安装 Python 依赖包...
echo    正在安装 playwright, requests, schedule, openpyxl...
python -m pip install --upgrade pip >nul 2>&1
python -m pip install -r "%~dp0requirements.txt"
if %errorlevel% neq 0 (
    echo.
    echo ❌ 依赖安装失败，请检查网络连接后重试
    pause
    exit /b 1
)
echo    ✅ Python 依赖安装完成

:: ── 安装 Playwright 浏览器 ──
echo.
echo [4/5] 安装 Playwright Chromium 浏览器（首次安装约需 2-5 分钟）...
python -m playwright install chromium
if %errorlevel% neq 0 (
    echo.
    echo ❌ Playwright 浏览器安装失败
    echo    如果是网络问题，可以尝试设置代理后重试
    pause
    exit /b 1
)
echo    ✅ Playwright 浏览器安装完成

:: ── 创建配置文件 ──
echo.
echo [5/6] 检查配置文件...
if not exist "%~dp0config.py" (
    if exist "%~dp0config.example.py" (
        copy "%~dp0config.example.py" "%~dp0config.py" >nul
        echo    ✅ 已从 config.example.py 创建 config.py
        echo    ⚠️  请务必编辑 config.py 填入真实的账号密码和飞书凭证
    ) else (
        echo    ❌ 未找到 config.example.py，请手动创建 config.py
    )
) else (
    echo    ✅ config.py 已存在
)

:: ── 创建必要目录 ──
echo.
echo [6/6] 创建必要目录...
if not exist "%~dp0logs" mkdir "%~dp0logs"
if not exist "%~dp0screenshots" mkdir "%~dp0screenshots"
echo    ✅ logs/ 和 screenshots/ 目录已就绪

:: ── 验证配置 ──
echo.
echo ============================================================
echo   安装完成！
echo ============================================================
echo.
echo   接下来请完成以下配置：
echo.
echo   1. 用记事本打开 config.py，确认以下配置正确：
echo      - HR_USERNAME  （北森登录邮箱）
echo      - HR_PASSWORD  （北森登录密码）
echo      - FEISHU_APP_ID / FEISHU_APP_SECRET（飞书应用凭证）
echo      - FEISHU_BASE_ID（多维表格 ID）
echo      - FEISHU_NOTIFY_USER_IDS（接收通知的飞书用户ID）
echo      - SYNC_TIME（定时执行时间，默认 22:32）
echo.
echo   2. 测试运行（任选一个表先试）：
echo      python sync_wage_standard.py    （表1-工资标准）
echo      python sync_table1.py           （表2-工资数据）
echo      python sync_table3.py           （表3-调薪记录表）
echo.
echo   3. 启动定时服务：
echo      python main.py
echo.
echo   4. 后台常驻运行（推荐）：
echo      双击 start_service.bat
echo.
pause
