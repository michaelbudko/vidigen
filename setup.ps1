# Vidigen setup for Windows (PowerShell). Run from the repo root:
#   powershell -ExecutionPolicy Bypass -File .\setup.ps1
# Add -Mock to skip the GPU/ML packages (pipeline runs with procedural art only).
param([switch]$Mock, [switch]$NoLLM)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

function Need($cmd, $hint) {
  if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
    Write-Host "Missing '$cmd'. $hint" -ForegroundColor Yellow
    return $false
  }
  return $true
}

if (-not (Need "ffmpeg" "Installing with winget...")) {
  winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
  Write-Host "Restart this terminal after the ffmpeg install so it is on PATH, then re-run setup." -ForegroundColor Yellow
  exit 1
}
if (-not (Need "node" "Install Node.js 22 LTS: winget install OpenJS.NodeJS.LTS")) { exit 1 }
if (-not (Need "py" "Install Python 3.12: winget install Python.Python.3.12")) { exit 1 }

Write-Host "== Python engine ==" -ForegroundColor Cyan
Push-Location "$root\engine"
if (-not (Test-Path ".venv")) { py -3.12 -m venv .venv }
$py = ".\.venv\Scripts\python.exe"
& $py -m pip install --upgrade pip
if (-not $Mock) {
  # RTX 50-series (Blackwell) needs CUDA 12.8+ builds of PyTorch.
  & $py -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
  & $py -m pip install -e ".[ml,dev]"
} else {
  & $py -m pip install -e ".[dev]"
}
Pop-Location

Write-Host "== Desktop app ==" -ForegroundColor Cyan
Push-Location "$root\app"
npm install
Pop-Location

if (-not $NoLLM) {
  Write-Host "== Local LLM (shot director) ==" -ForegroundColor Cyan
  if (-not (Get-Command "ollama" -ErrorAction SilentlyContinue)) {
    winget install --id Ollama.Ollama -e --accept-source-agreements --accept-package-agreements
  }
  ollama pull qwen3:8b
}

Write-Host ""
Write-Host "Done. Start the app with:  cd app; npm run dev" -ForegroundColor Green
Write-Host "Models download from Hugging Face on first render (several GB)."
