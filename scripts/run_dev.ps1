# 一键启动开发服务
# 用法: powershell -ExecutionPolicy Bypass -File scripts/run_dev.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "[run_dev] 检查数据库迁移..."
conda run -n langchain python scripts/init_db.py
conda run -n langchain alembic upgrade head

Write-Host "[run_dev] 启动 uvicorn: http://localhost:8000/docs"
conda run -n langchain uvicorn app.main:app --host 0.0.0.0 --port 8000
