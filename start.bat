@echo off
chcp 65001 >nul
echo ============================================
echo   桌游赛事计分系统 - 启动脚本
echo ============================================
echo.

echo [1/3] 检查依赖...
pip show fastapi >nul 2>&1
if errorlevel 1 (
    echo 正在安装依赖，请稍候...
    pip install -r requirements.txt
)

echo.
echo [2/3] 启动服务...
echo.
echo   服务地址: http://localhost:8000
echo   Swagger文档: http://localhost:8000/docs
echo   ReDoc文档:  http://localhost:8000/redoc
echo.
echo   按 Ctrl+C 停止服务
echo.

python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload

pause
