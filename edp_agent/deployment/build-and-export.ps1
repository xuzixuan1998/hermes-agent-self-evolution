<#
.SYNOPSIS
  Windows PowerShell packager -- equivalent to build.sh + export-bundle.sh.

.DESCRIPTION
  On Windows (PowerShell 5.1+ or PowerShell 7) with Docker Desktop
  (Linux containers mode), this script:
    1. Assembles the docker build context from agent-runtime and
       agent-store (robocopy replaces rsync).
    2. docker build -t edpagent:latest
    3. docker save + tar -czf into bundle\edpagent-offline-<stamp>.tar.gz

  The resulting tar.gz is bit-compatible with the bash-produced bundle
  and can be deployed on offline Linux servers via the same
  import-bundle.sh / run.sh flow.

  All messages are ASCII-only on purpose -- PowerShell 5.1 on non-UTF-8
  locales (e.g. zh-CN GBK) mis-decodes BOM-less UTF-8 scripts and fails
  to parse. Keep this file ASCII.

.PARAMETER AgentRuntime
  agent-runtime repo root. Default: $HOME\EDPAgent\agent-runtime

.PARAMETER AgentStore
  agent-store repo root. Default: $HOME\EDPAgent\agent-store

.PARAMETER ImageTag
  Built image tag. Default: edpagent:latest

.PARAMETER SkipBuild
  Skip docker build (re-pack an existing image).

.PARAMETER SkipExport
  Skip export stage (only build, no bundle).

.EXAMPLE
  .\build-and-export.ps1

.EXAMPLE
  .\build-and-export.ps1 -AgentRuntime D:\repos\agent-runtime -AgentStore D:\repos\agent-store

.EXAMPLE
  .\build-and-export.ps1 -SkipBuild     # re-pack only
#>

[CmdletBinding()]
param(
    [string]$AgentRuntime = (Join-Path $HOME 'EDPAgent\agent-runtime'),
    [string]$AgentStore   = (Join-Path $HOME 'EDPAgent\agent-store'),
    [string]$ImageTag     = 'edpagent:latest',
    [switch]$SkipBuild,
    [switch]$SkipExport
)

$ErrorActionPreference = 'Stop'
$BuildDir   = $PSScriptRoot
$ContextDir = Join-Path $BuildDir '.build-context'
$BundleDir  = Join-Path $BuildDir 'bundle'
$ConfigDir  = Join-Path $BuildDir 'config'
$A2AEnvSrc  = Join-Path $AgentRuntime 'applications\a2a_service\.env.example'
$VAEnvSrc   = Join-Path $AgentRuntime 'applications\versatile_adapter\.env.example'
$A2AEnvDst  = Join-Path $ConfigDir 'a2a_service.env'
$VAEnvDst   = Join-Path $ConfigDir 'versatile_adapter.env'

function Write-Section($msg) {
    Write-Host ""
    Write-Host "==========================================================="
    Write-Host $msg
    Write-Host "==========================================================="
}

function Invoke-Robocopy {
    param([string]$Src, [string]$Dst, [string[]]$ExtraArgs = @())
    $baseArgs = @($Src, $Dst, '/E', '/NFL', '/NDL', '/NJH', '/NJS', '/NC', '/NS', '/NP', '/R:2', '/W:2')
    $allArgs  = $baseArgs + $ExtraArgs
    robocopy @allArgs | Out-Null
    # robocopy exit codes 0..7 are success; >=8 is error
    if ($LASTEXITCODE -ge 8) {
        throw "robocopy failed: $Src -> $Dst (exit $LASTEXITCODE)"
    }
    $global:LASTEXITCODE = 0
}

function Copy-RuntimeEnv {
  param([string]$Src, [string]$Dst)
  if ((Test-Path $Dst) -and ($env:EDPAGENT_REFRESH_ENV -ne '1')) {
    Write-Host "[ok] keep existing env: $Dst"
    return
  }
  Copy-Item $Src $Dst -Force
}

# ======================================================================
# Preflight checks
# ======================================================================

Write-Section "[0/4] Preflight"

# docker
try { docker version --format '{{.Server.Os}}' | Out-Null } catch {
    throw "docker CLI unavailable. Start Docker Desktop and switch to Linux containers."
}

# tar (bundled with Windows 10 1803+)
if (-not $SkipExport) {
    try { tar --version | Out-Null } catch {
        throw "tar.exe not found. Windows 10 1803+ ships bsdtar; on older versions install Git for Windows or 7-Zip and adjust the packaging step."
    }
}

# input dirs
if (-not (Test-Path (Join-Path $AgentRuntime 'applications\a2a_service'))) {
    throw "agent-runtime/applications/a2a_service not found. Check -AgentRuntime: $AgentRuntime"
}
if (-not (Test-Path (Join-Path $AgentRuntime 'applications\versatile_adapter'))) {
    throw "agent-runtime/applications/versatile_adapter not found. Check -AgentRuntime: $AgentRuntime"
}
if (-not (Test-Path (Join-Path $AgentStore 'community\EDPAgent'))) {
    throw "agent-store/community/EDPAgent not found. Check -AgentStore: $AgentStore"
}
if (-not (Test-Path $A2AEnvSrc)) {
  throw "a2a_service env template not found: $A2AEnvSrc"
}
if (-not (Test-Path $VAEnvSrc)) {
  throw "versatile_adapter env template not found: $VAEnvSrc"
}

# Generate runnable env files from runtime templates. Existing files are kept
# so operator edits are not overwritten unless EDPAGENT_REFRESH_ENV=1 is set.
New-Item -ItemType Directory -Path $ConfigDir -Force | Out-Null
Copy-RuntimeEnv $A2AEnvSrc $A2AEnvDst
Copy-RuntimeEnv $VAEnvSrc $VAEnvDst

# CRLF guard: scan entrypoint.sh for stray \r which would break bash in the container
$EntrypointSh = Join-Path $BuildDir 'entrypoint.sh'
if (Test-Path $EntrypointSh) {
    $firstBytes = [System.IO.File]::ReadAllBytes($EntrypointSh) | Select-Object -First 200
    if ($firstBytes -contains 13) {
        Write-Warning "entrypoint.sh contains CRLF. Inside the image bash will fail with 'env: bash\r: No such file'."
        Write-Warning "Fix: git config --global core.autocrlf false, then delete and re-clone the repo."
        Write-Warning "Or quick-fix in PowerShell:"
        Write-Warning "  (Get-Content `$EntrypointSh -Raw).Replace(\"``r``n\",\"``n\") | Set-Content -NoNewline `$EntrypointSh"
        $reply = Read-Host "Continue anyway? (y/N)"
        if ($reply -ne 'y' -and $reply -ne 'Y') { throw "Aborted." }
    }
}

Write-Host "[ok] agent-runtime:    $AgentRuntime"
Write-Host "[ok] agent-store:      $AgentStore"
Write-Host "[ok] build context:    $ContextDir"
Write-Host "[ok] image tag:        $ImageTag"
Write-Host "[ok] a2a env:          $A2AEnvDst"
Write-Host "[ok] versatile env:    $VAEnvDst"

# ======================================================================
# 1. Assemble build context
# ======================================================================

if (-not $SkipBuild) {
    Write-Section "[1/4] Prepare build context"

    if (Test-Path $ContextDir) { Remove-Item -Recurse -Force $ContextDir }
    New-Item -ItemType Directory -Path $ContextDir | Out-Null

    $excludeCommon = @('/XD', '__pycache__', 'logs', '.venv', '/XF', '*.pyc')

    Invoke-Robocopy (Join-Path $AgentRuntime 'applications\a2a_service') `
                    (Join-Path $ContextDir  'a2a_service') `
                    $excludeCommon

    Invoke-Robocopy (Join-Path $AgentRuntime 'applications\versatile_adapter') `
                    (Join-Path $ContextDir  'versatile_adapter') `
                    $excludeCommon

    # Merge EDPAgent business code (also excludes docs/ and deployment/)
    $EDPAgentDst = Join-Path $ContextDir 'a2a_service\agents\EDPAgent'
    if (Test-Path $EDPAgentDst) { Remove-Item -Recurse -Force $EDPAgentDst }
    Invoke-Robocopy (Join-Path $AgentStore 'community\EDPAgent') $EDPAgentDst `
                    @('/XD', 'docs', 'deployment', '__pycache__', '/XF', '*.pyc')

    if (-not (Test-Path (Join-Path $EDPAgentDst 'agent.py'))) {
        throw "Bad layout: $EDPAgentDst\agent.py not found. Check robocopy output."
    }

    # Strip any dev .env files so credentials don't leak into the image
    Get-ChildItem -Path $ContextDir -Filter '.env' -File -Recurse -Force `
        -ErrorAction SilentlyContinue | Remove-Item -Force

    Copy-Item (Join-Path $BuildDir 'Dockerfile')    (Join-Path $ContextDir 'Dockerfile')    -Force
    Copy-Item (Join-Path $BuildDir 'entrypoint.sh') (Join-Path $ContextDir 'entrypoint.sh') -Force

    Write-Host "[ok] context ready"

    # ==================================================================
    # 2. docker build
    # ==================================================================
    Write-Section "[2/4] docker build -t $ImageTag"

    docker build -t $ImageTag $ContextDir
    if ($LASTEXITCODE -ne 0) { throw "docker build failed" }

    Write-Host ""
    Write-Host "[ok] image built: $ImageTag"
    docker images $ImageTag --format "   {{.Repository}}:{{.Tag}}   {{.Size}}   {{.CreatedSince}}"
} else {
    Write-Section "[1-2/4] skipped (-SkipBuild)"
    docker image inspect $ImageTag | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Image $ImageTag not found. Drop -SkipBuild to build first." }
}

if ($SkipExport) {
    Write-Section "[3-4/4] skipped (-SkipExport)"
    Write-Host "[ok] done (no bundle exported)"
    return
}

# ======================================================================
# 3. docker save + stage dir
# ======================================================================

Write-Section "[3/4] docker save + stage bundle contents"

$Stamp      = Get-Date -Format 'yyyyMMdd-HHmmss'
$BundleName = "edpagent-offline-$Stamp"
$StageDir   = Join-Path $BundleDir $BundleName

if (Test-Path $StageDir) { Remove-Item -Recurse -Force $StageDir }
New-Item -ItemType Directory -Path (Join-Path $StageDir 'config') -Force | Out-Null

Write-Host "[3.1] docker save -> edpagent.image.tar"
docker save $ImageTag -o (Join-Path $StageDir 'edpagent.image.tar')
if ($LASTEXITCODE -ne 0) { throw "docker save failed" }
$ImgSizeMB = [math]::Round((Get-Item (Join-Path $StageDir 'edpagent.image.tar')).Length / 1MB, 1)
Write-Host "      $ImgSizeMB MB"

Write-Host "[3.2] copy deployment files"
Copy-Item $A2AEnvSrc (Join-Path $StageDir 'config\a2a_service.env') -Force
Copy-Item $VAEnvSrc  (Join-Path $StageDir 'config\versatile_adapter.env') -Force
Copy-Item (Join-Path $BuildDir 'import-bundle.sh') $StageDir -Force
Copy-Item (Join-Path $BuildDir 'run.sh')           $StageDir -Force
Copy-Item (Join-Path $BuildDir 'stop.sh')          $StageDir -Force

$DeployMd = Join-Path $AgentStore 'community\EDPAgent\docs\deployment.md'
if (-not (Test-Path $DeployMd)) {
    throw "Deployment guide not found: $DeployMd"
}
Copy-Item $DeployMd (Join-Path $StageDir 'README.md') -Force
Write-Host "      copied docs/deployment.md -> README.md"

# ======================================================================
# 4. tar.gz
# ======================================================================

Write-Section "[4/4] tar -czf"

Push-Location $BundleDir
try {
    $TarGz = "$BundleName.tar.gz"
    tar -czf $TarGz $BundleName
    if ($LASTEXITCODE -ne 0) { throw "tar failed" }

    $FinalPath = Join-Path $BundleDir $TarGz
    $SizeMB    = [math]::Round((Get-Item $FinalPath).Length / 1MB, 1)

    Write-Host ""
    Write-Host "[ok] bundle ready"
    Write-Host "   path: $FinalPath"
    Write-Host "   size: $SizeMB MB"
    Write-Host ""
    Write-Host "Next steps:"
    Write-Host "   1. Transfer to the offline Linux server (scp / WinSCP / USB)."
    Write-Host "   2. On the server:  tar xzf $TarGz"
    Write-Host "   3. cd $BundleName && chmod +x *.sh && ./import-bundle.sh"
    Write-Host "   4. Review/edit config/*.env then ./run.sh"
    Write-Host "   (See bundled README.md sections C.2 / E for details.)"
} finally {
    Pop-Location
}
