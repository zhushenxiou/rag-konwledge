# 安装 pgvector 到本机 PostgreSQL（预编译包，需以管理员运行）
# 用法:  powershell -ExecutionPolicy Bypass -File scripts\install_pgvector.ps1
$ErrorActionPreference = "Stop"

$PG_VERSION = "18"
$PG_HOME    = "C:\Program Files\PostgreSQL\$PG_VERSION"
$RELEASE    = "0.8.6_18"
$ASSET_ID   = "494742910"   # vector.v0.8.6-pg18.zip 的 GitHub asset id
$ZIP        = Join-Path $env:TEMP "vector.pg$PG_VERSION.zip"
$EXTRACT    = Join-Path $env:TEMP "vector.pg$PG_VERSION.extract"

if (-not (Test-Path $PG_HOME)) {
    throw "找不到 PostgreSQL 安装目录: $PG_HOME"
}

Write-Host ">> 下载 pgvector $RELEASE ..."
# 通过 api.github.com 的 asset url 下载（browser_download_url 的 CDN 跳转在国内可能超时）
curl.exe -sL --max-time 120 -A "Mozilla/5.0" -H "Accept: application/octet-stream" `
    -o $ZIP "https://api.github.com/repos/andreiramani/pgvector_pgsql_windows/releases/assets/$ASSET_ID"
if ((Get-Item $ZIP).Length -lt 50000) { throw "下载失败，文件过小: $((Get-Item $ZIP).Length) bytes" }

Write-Host ">> 解压 ..."
if (Test-Path $EXTRACT) { Remove-Item -Recurse -Force $EXTRACT }
Expand-Archive -Path $ZIP -DestinationPath $EXTRACT

Write-Host ">> 拷贝 vector.dll -> $PG_HOME\lib\"
Copy-Item -Force (Join-Path $EXTRACT "lib\vector.dll") (Join-Path $PG_HOME "lib\")
Write-Host ">> 拷贝扩展 SQL -> $PG_HOME\share\extension\"
Copy-Item -Force (Join-Path $EXTRACT "share\extension\vector*") (Join-Path $PG_HOME "share\extension\")

Write-Host ">> 验证 CREATE EXTENSION vector ..."
$psql = Join-Path $PG_HOME "bin\psql.exe"
$env:PGPASSWORD = Read-Host "postgres 用户密码" -AsSecureString | ForEach-Object { [Net.NetworkCredential]::new('', $_).Password }
& $psql -U postgres -h localhost -p 5432 -c "CREATE EXTENSION IF NOT EXISTS vector;" | Out-Host
& $psql -U postgres -h localhost -p 5432 -t -c "SELECT 'pgvector OK, dims='||vector_dims('[1,2,3]'::vector);" | Out-Host

Write-Host ">> 完成。如需卸载: 在 lib 和 share\extension 中删除 vector.dll / vector* 文件。"
