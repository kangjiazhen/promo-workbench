# 启动工作台（后端 8861 + 前端 5188）
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$module = Split-Path -Parent $here            # 仓库根

# 不假定 venv 在哪：用 PATH 上能跑的 python（先激活自己的 venv 再跑本脚本）。
$py = (Get-Command python -ErrorAction Stop).Source

# 后端 http://127.0.0.1:8861
Start-Process -FilePath $py -ArgumentList "-m","uvicorn","property_promo.api.app:app","--port","8861" `
  -WorkingDirectory $module -WindowStyle Minimized `
  -Environment @{ PYTHONPATH = "src" }

# 前端 http://localhost:5188
Push-Location (Join-Path $module "web")
if (-not (Test-Path "node_modules")) { pnpm install }
Start-Process -FilePath "pnpm" -ArgumentList "dev" -WorkingDirectory (Get-Location) -WindowStyle Minimized
Pop-Location

Start-Sleep 4
Start-Process "http://localhost:5188"
