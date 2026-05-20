$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$electronGuiDir = (Resolve-Path (Join-Path $scriptDir "..")).Path
$repoRoot = (Resolve-Path (Join-Path $electronGuiDir "..")).Path

$venvPython = Join-Path $repoRoot "fyp-venv\Scripts\python.exe"
$pythonExe = if (Test-Path $venvPython) { $venvPython } else { "python" }

$serverScript = Join-Path $repoRoot "server.py"
$backendDistRoot = Join-Path $electronGuiDir "backend-dist"
$backendBuildRoot = Join-Path $electronGuiDir "backend-build"
$backendName = "conf-comparison-server"

Write-Host "[installer] Using Python executable: $pythonExe"

& $pythonExe -m pip install --upgrade pyinstaller
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install/upgrade pyinstaller"
}

if (Test-Path (Join-Path $backendDistRoot $backendName)) {
    Remove-Item -Recurse -Force (Join-Path $backendDistRoot $backendName)
}

if (-not (Test-Path $backendDistRoot)) {
    New-Item -ItemType Directory -Path $backendDistRoot | Out-Null
}

if (-not (Test-Path $backendBuildRoot)) {
    New-Item -ItemType Directory -Path $backendBuildRoot | Out-Null
}

$pyInstallerArgs = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onedir",
    "--name", $backendName,
    "--distpath", $backendDistRoot,
    "--workpath", $backendBuildRoot,
    "--specpath", $backendBuildRoot,
    "--add-data", "$repoRoot\config;config",
    "--add-data", "$repoRoot\comparison_engine\templates;comparison_engine\templates",
    "--add-data", "$repoRoot\schemes;schemes",
    "--add-data", "$repoRoot\rubrics;rubrics",
    $serverScript
)

Write-Host "[installer] Building backend executable with PyInstaller..."
& $pythonExe @pyInstallerArgs
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller backend build failed"
}

$builtExe = Join-Path $backendDistRoot "$backendName\$backendName.exe"
if (-not (Test-Path $builtExe)) {
    throw "Backend executable was not created at expected path: $builtExe"
}

Write-Host "[installer] Backend build complete: $builtExe"
