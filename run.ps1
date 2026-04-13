# HpAgent 启动脚本

Write-Host "正在启动 HpAgent..." -ForegroundColor Green

# 检查 Python 是否安装
try {
    python --version | Out-Null
} catch {
    Write-Host "错误: 未找到 Python" -ForegroundColor Red
    Write-Host "请先安装 Python 3.11 或更高版本" -ForegroundColor Yellow
    exit 1
}

# 检查虚拟环境
$venvPath = ".venv"
$pythonExe = "python"

if (Test-Path $venvPath) {
    Write-Host "检测到虚拟环境，正在激活..." -ForegroundColor Cyan
    $pythonExe = ".venv\Scripts\python.exe"

    # 检查虚拟环境 Python 是否可用
    try {
        & $pythonExe --version | Out-Null
    } catch {
        Write-Host "虚拟环境损坏，正在重新创建..." -ForegroundColor Yellow
        Remove-Item -Recurse -Force $venvPath
        $venvPath = $null
        $pythonExe = "python"
    }
}

# 如果没有虚拟环境，创建新的
if (-not $venvPath -or -not (Test-Path $venvPath)) {
    Write-Host "正在创建虚拟环境..." -ForegroundColor Cyan
    python -m venv $venvPath
    $pythonExe = ".venv\Scripts\python.exe"
}

# 激活虚拟环境并安装依赖
Write-Host "正在安装依赖..." -ForegroundColor Cyan
& $pythonExe -m pip install --upgrade pip | Out-Null
& $pythonExe -m pip install httpx pyyaml pytest | Out-Null

# 检查配置文件
if (-not (Test-Path "config.yaml")) {
    Write-Host "警告: 未找到 config.yaml" -ForegroundColor Yellow
    if (Test-Path "config.yaml.example") {
        Write-Host "正在从 config.yaml.example 创建 config.yaml..." -ForegroundColor Yellow
        Copy-Item "config.yaml.example" "config.yaml"
        Write-Host "请编辑 config.yaml 填入你的 API 密钥" -ForegroundColor Red
        exit 1
    }
}

# 检查 API 密钥
try {
    $config = Get-Content "config.yaml" -Raw
    if ($config -match 'api_key:\s*"your-api-key-here"' -or $config -match 'api_key:\s*""') {
        Write-Host "错误: 请先在 config.yaml 中配置你的 API 密钥" -ForegroundColor Red
        exit 1
    }
} catch {
    Write-Host "错误: 无法读取 config.yaml" -ForegroundColor Red
    exit 1
}

# 启动应用
Write-Host "`n开始对话（输入 'exit' 退出）..." -ForegroundColor Green
& $pythonExe -m src.main
