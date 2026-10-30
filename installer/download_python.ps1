# download_python.ps1 - Download Python embedded runtime + install PySide6
# Called by Inno [Run] first entry, before installui (which depends on PySide6).
# Pure ASCII only - PowerShell 5.1 reads no-BOM files as GBK, Chinese comments break parsing.
# All steps logged to %PROGRAMDATA%\Rheia\download.log for diagnosis.
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File download_python.ps1 -AppDir "C:\...\Rheia"
#
# Python runtime lives in C:\Program Files\PythonSide6 (shared by Rheia and
# OpenKeyPersonalityCreator), NOT inside {app}. Uninstall never touches it; reinstall
# skips download when the existing runtime is present AND version >= official latest.

param(
    [Parameter(Mandatory=$true)]
    [string]$AppDir
)

# Shared runtime dir (independent of {app}). Both projects point here.
$runtimeDir = Join-Path $env:ProgramFiles "PythonSide6"

$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

# ---- log to file (Write-Host invisible under runhidden) ----
$logDir = Join-Path $env:ProgramData "Rheia"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$logFile = Join-Path $logDir "download.log"

function Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $msg"
    Add-Content -Path $logFile -Value $line -Encoding UTF8
    Write-Host $line
}

# ---- signal unified log: append "text | pct | complete/done" to %TEMP%\okoc_signal.txt ----
$signalFile = Join-Path $env:TEMP "okoc_signal.txt"
function WriteSignal($text, $pct, $mark = "complete") {
    try {
        $dir = Split-Path $signalFile
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        Add-Content -Path $signalFile -Value ("{0} | {1} | {2}" -f $text, $pct, $mark) -Encoding UTF8
    } catch {}
}

Log "=== download_python.ps1 start, AppDir=$AppDir ==="
WriteSignal "download Python runtime" 6

# ---- mirror choice: by reachability, NOT by system language ----
# Language only picks UI strings; it says nothing about the network. A German
# locale on a Chinese network used to fall through to python.org / pypi.org
# (~45 kB/s; PySide6 alone is ~270 MB) and looked like a dead installer.
# Same rule as install.py's npm strategy: probe, order by reachability,
# official source is the last resort.
$isChinese = $false
try {
    $culture = (Get-Culture).Name
    if ($culture -match '^zh') { $isChinese = $true }
} catch { }
Log "isChinese=$isChinese (UI language only; mirrors are picked by reachability)"

try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

function Test-Reachable($url, $timeoutSec = 6) {
    try {
        $req = [System.Net.HttpWebRequest]::Create($url)
        $req.Method = "HEAD"
        $req.Timeout = $timeoutSec * 1000
        $req.UserAgent = "Rheia-Installer"
        $resp = $req.GetResponse()
        $code = [int]$resp.StatusCode
        $resp.Close()
        if ($code -ge 200 -and $code -lt 500) { return $true }
    } catch {
        if ($_.Exception.Response) { return $true }
    }
    return $false
}

# ---- source candidates for every download on this path ----
# One probe pass measures them, but it no longer runs at startup: the probe waits
# for the PySide6 wheels to finish (handshake below). PySide6 itself keeps a fixed
# candidate order, and the source that served it is excluded from the probe.
$pyCandidates = @(
    "https://mirrors.huaweicloud.com/python/",
    "https://npmmirror.com/mirrors/python/",
    "https://www.python.org/ftp/python/"
)
$pipCandidates = @(
    "https://pypi.tuna.tsinghua.edu.cn/simple",
    "https://mirrors.aliyun.com/pypi/simple/",
    "https://repo.huaweicloud.com/repository/pypi/simple",
    "https://pypi.org/simple"
)
$getPipCandidates = @(
    # 2 MB, not worth ranking. Official bootstrap first: mirror copies go stale,
    # and a stale get-pip bundles a pip that imports distutils, which Python 3.12+
    # no longer ships (2026-10-28 run died right there: No module named 'distutils'
    # -> pip never installed -> PySide6 never landed).
    "https://bootstrap.pypa.io/get-pip.py",
    "https://pypi.tuna.tsinghua.edu.cn/get-pip.py",
    "https://mirrors.aliyun.com/pypi/get-pip.py"
)
$nodeCandidates = @(
    "https://npmmirror.com/mirrors/node/",
    "https://mirrors.ustc.edu.cn/node/",
    "https://nodejs.org/dist/"
)
$npmCandidates = @(
    "https://registry.npmmirror.com/",
    "https://mirrors.cloud.tencent.com/npm/",
    "https://repo.huaweicloud.com/repository/npm/",
    "https://registry.npmjs.org/"
)
$PIP_DEAD_KBS = 256
$PIP_USABLE_KBS = 512
$PIP_SWITCH_RATIO = 2.0

# Candidate order is valid before the probe runs: PySide6 and pip bootstrap both
# happen ahead of the handshake, so they must not depend on probe output.
$pyMirrors = @($pyCandidates)
$pipMirrors = @($pipCandidates)
$getPipMirrors = @($getPipCandidates)
$nodeMirrors = @($nodeCandidates)
$npmMirrors = @($npmCandidates)

$pythonDir = $runtimeDir
if (-not (Test-Path $pythonDir)) { New-Item -ItemType Directory -Path $pythonDir -Force | Out-Null }
Log "pythonDir=$pythonDir"

$pythonExe = Join-Path $pythonDir "python.exe"
$pysideInit = Join-Path $pythonDir "Lib\site-packages\PySide6\__init__.py"
$pysidePlatforms = Join-Path $pythonDir "Lib\site-packages\PySide6\plugins\platforms"
# ---- the single probe pass: measure every source once, then hand out the fastest ----
function Invoke-SourceProbe($targets, $sampleSec = 2, $maxKb = 512, $inventory = $null) {
    $jobs = @()
    foreach ($t in $targets) {
        $jobs += Start-Job -ScriptBlock {
            param($group, $url, $probeUrl, $sampleSec, $maxKb)
            $kbs = 0
            try {
                if (-not $probeUrl) { $probeUrl = $url }
                $req = [System.Net.HttpWebRequest]::Create($probeUrl)
                $req.Timeout = 6000
                $req.ReadWriteTimeout = 6000
                $req.UserAgent = "Rheia-Probe"
                $req.AddRange(0, ($maxKb * 1024) - 1)
                $sw = [Diagnostics.Stopwatch]::StartNew()
                $resp = $req.GetResponse()
                $st = $resp.GetResponseStream()
                $buf = New-Object byte[] 32768
                $total = 0
                while ($sw.Elapsed.TotalSeconds -lt $sampleSec -and $total -lt ($maxKb * 1024)) {
                    $n = $st.Read($buf, 0, $buf.Length)
                    if ($n -le 0) { break }
                    $total += $n
                }
                $secs = $sw.Elapsed.TotalSeconds
                $st.Close()
                $resp.Close()
                if ($secs -gt 0) { $kbs = [int]($total / 1KB / $secs) }
            } catch { $kbs = 0 }
            [pscustomobject]@{ group = $group; url = $url; kbs = $kbs }
        } -ArgumentList $t.group, $t.url, $t.probe, $sampleSec, $maxKb
    }
    $rows = @()
    if ($jobs.Count -gt 0) {
        # hard cap: a probe that cannot answer in 8s loses to the candidate order
        [void](Wait-Job -Job $jobs -Timeout 8)
        foreach ($j in $jobs) {
            if ($j.State -eq "Completed") { $rows += Receive-Job $j }
            Remove-Job $j -Force -ErrorAction SilentlyContinue
        }
    }
    $table = @{}
    foreach ($r in $rows) {
        if (-not $table.ContainsKey($r.group)) { $table[$r.group] = @() }
        $table[$r.group] += ,@($r.url, $r.kbs)
    }
    $doc = @{ generated_at = (Get-Date -Format "s"); sources = @(); inventory = $inventory }
    foreach ($r in $rows) { $doc.sources += @{ group = $r.group; url = $r.url; kbs = $r.kbs } }
    try { ($doc | ConvertTo-Json -Depth 5) | Set-Content -Path (Join-Path $logDir "source_map.json") -Encoding UTF8 } catch {}
    return $table
}

function Get-FastestUrls($table, $group, $fallback) {
    $out = New-Object System.Collections.ArrayList
    if ($table.ContainsKey($group)) {
        # build objects first: piping arrays of arrays would flatten them
        $rows = @()
        foreach ($it in $table[$group]) {
            $rows += [pscustomobject]@{ Url = $it[0]; Kbs = $it[1] }
        }
        foreach ($row in ($rows | Sort-Object -Property Kbs -Descending)) {
            [void]$out.Add($row.Url)
            Log ("probe: {0} {1} = {2} KB/s" -f $group, $row.Url, $row.Kbs)
        }
    }
    foreach ($f in $fallback) { if (-not $out.Contains($f)) { [void]$out.Add($f) } }
    return $out
}

function Get-LocalInventory {
    # The probe looks inward as well as outward: what is already on this machine
    # decides both what to fetch and what to skip. One process launch per module,
    # no downloads involved.
    $inv = @{
        python = $false
        python_version = ""
        pip = ""
        pyside = ""
        cryptography = ""
        setuptools = ""
        wheel = ""
        zstandard = ""
    }
    $exe = Join-Path $pythonDir "python.exe"
    if (-not (Test-Path $exe)) { return $inv }
    $inv.python = $true
    # one process, one pass: six launches would cost more than the whole probe
    $probePy = Join-Path $env:TEMP "rheia_inventory.py"
    try {
        Set-Content -Path $probePy -Encoding ASCII -Value @'
import sys, json, importlib
out = {"python": sys.version.split()[0], "mods": {}}
for m in ("PySide6", "cryptography", "setuptools", "wheel", "zstandard"):
    try:
        out["mods"][m] = str(getattr(importlib.import_module(m), "__version__", "?"))
    except Exception:
        pass
try:
    import pip
    out["pip"] = str(getattr(pip, "__version__", "?"))
except Exception:
    out["pip"] = ""
print(json.dumps(out))
'@
        $raw = & $exe $probePy 2>$null
        if ($LASTEXITCODE -eq 0 -and $raw) {
            $doc = $raw | ConvertFrom-Json
            $inv.python_version = [string]$doc.python
            $inv.pip = [string]$doc.pip
            $map = @{ "PySide6" = "pyside"; "cryptography" = "cryptography"; "setuptools" = "setuptools"; "wheel" = "wheel"; "zstandard" = "zstandard" }
            foreach ($k in $map.Keys) {
                if ($doc.mods.PSObject.Properties.Name -contains $k) { $inv[$map[$k]] = [string]$doc.mods.$k }
            }
        }
    } catch {
        Log "inventory probe failed: $_"
    }
    return $inv
}

function Initialize-SourceTable {
    param([string]$ExcludeUrl = "")
    # The list itself is conditional: probe only what a remaining step needs.
    # A step that is already satisfied has nothing to rank, so it contributes
    # no targets and costs no time.
    $script:pyMirrors = @($pyCandidates)
    $script:pipMirrors = @($pipCandidates)
    $script:getPipMirrors = @($getPipCandidates)
    $script:nodeMirrors = @($nodeCandidates)
    $script:npmMirrors = @($npmCandidates)

    $inv = Get-LocalInventory
    $needRuntime = -not $inv.python
    $needPip = -not ($inv.pip -as [string])
    $needPyside = -not ($inv.pyside -as [string])
    $have = @()
    foreach ($k in @("pyside", "cryptography", "setuptools", "wheel", "zstandard")) {
        if ($inv[$k]) { $have += ($k + "=" + $inv[$k]) }
    }
    Log ("inventory: python=" + $inv.python_version + " pip=" + $inv.pip + " | have: " + ($have -join ", "))
    # install.py's own steps (5 Ubuntu / 6 Node / 7 npm) consume the same table,
    # so their hosts are measured only while those steps are still pending
    $doneSteps = @()
    try {
        $progFile = Join-Path $logDir "installprogress.json"
        if (Test-Path $progFile) {
            $doneSteps = @(Get-Content -Path $progFile -Raw -Encoding UTF8 | ConvertFrom-Json)
        }
    } catch { }
    $needInstallHosts = (-not ($doneSteps -contains 5)) -or (-not ($doneSteps -contains 6)) -or (-not ($doneSteps -contains 7))
    Log ("probe needs: runtime=$needRuntime pip=$needPip pyside=$needPyside installHosts=$needInstallHosts")

    $targets = @()
    # python zip (13.7 MB) and get-pip (2 MB) are too small to be worth ranking.
    # PySide6 is not ranked either: it runs on the fixed candidate order, and the
    # source it used is excluded from this measurement ($ExcludeUrl).
    $needOtherPip = (-not ($inv.cryptography -as [string])) -or (-not ($inv.setuptools -as [string])) -or (-not ($inv.wheel -as [string])) -or (-not ($inv.zstandard -as [string]))
    if ($needOtherPip) {
        $excl = ($ExcludeUrl -as [string])
        if ($excl) { $excl = $excl.TrimEnd('/') }
        foreach ($u in $pipCandidates) {
            if ($excl -and ($u.TrimEnd('/') -eq $excl)) {
                Log "probe skips the PySide6 source: $u"
                continue
            }
            $targets += @{ group = "pip"; url = $u }
        }
    }
    if ($needInstallHosts) {
        foreach ($u in $nodeCandidates) { $targets += @{ group = "node"; url = $u } }
        foreach ($u in $npmCandidates) { $targets += @{ group = "npm"; url = $u } }
        foreach ($u in @("https://mirrors.tuna.tsinghua.edu.cn/", "https://mirrors.huaweicloud.com/", "https://mirrors.ustc.edu.cn/", "https://mirrors.aliyun.com/", "https://cloud-images.ubuntu.com/")) {
            $targets += @{ group = "host"; url = $u }
        }
    }
    if ($targets.Count -eq 0) {
        Log "probe skipped: every step on this path is already satisfied"
        return
    }
    Log "probe pass: $($targets.Count) targets, one shot"
    $table = @{}
    try { $table = Invoke-SourceProbe $targets 2 512 $inv } catch { Log "probe pass failed, using candidate order: $_" }
    $script:pyMirrors = Get-FastestUrls $table "python" $pyCandidates
    $script:pipMirrors = Get-FastestUrls $table "pip" $pipCandidates
    if ($ExcludeUrl) {
        $exclNorm = ($ExcludeUrl -as [string]).TrimEnd('/')
        $script:pipMirrors = @($script:pipMirrors | Where-Object { $_.TrimEnd('/') -ne $exclNorm })
        Log ("pip sources for the remaining packages (PySide6 source excluded): " + ($script:pipMirrors -join " -> "))
    }
    $script:getPipMirrors = Get-FastestUrls $table "get-pip" $getPipCandidates
    $script:nodeMirrors = Get-FastestUrls $table "node" $nodeCandidates
    $script:npmMirrors = Get-FastestUrls $table "npm" $npmCandidates
    Log ("fastest per step: python=" + $pyMirrors[0] + " | pip=" + $pipMirrors[0] + " | get-pip=" + $getPipMirrors[0] + " | node=" + $nodeMirrors[0] + " | npm=" + $npmMirrors[0])
}

# Runtime already complete: nothing to download, but install.py still needs the
# source table for Ubuntu/Node/npm. This must sit BELOW the function definitions -
# PowerShell creates a function only when its definition line runs, so calling it
# earlier in the same script would throw "not recognized".
if ((Test-Path $pythonExe) -and (Test-Path $pysideInit) -and (Test-Path $pysidePlatforms)) {
    Log "python + PySide6 already present, skip download"
    # still refresh the table: install.py needs it for Ubuntu/Node/npm even when
    # the Python runtime is already in place
    Initialize-SourceTable
    WriteSignal "download Python runtime" 6
    WriteSignal "get-pip bootstrap" 8
    WriteSignal "download PySide6" 12
    WriteSignal "download PySide6 done" 25
    exit 0
}

# (the probe no longer runs here: it fires from the PySide6 handshake, further down)

# runtime dir incomplete (missing python.exe / PySide6 / plugins\platforms) -> wipe and reinstall
if (Test-Path $pythonDir) {
    Log "runtime incomplete, removing $pythonDir before reinstall"
    Remove-Item -Path $pythonDir -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path $pythonDir -Force | Out-Null
}

$downloaded = $false
foreach ($base in $pyCandidates) {
    $base = $base.TrimEnd('/')
    Log "probing mirror: $base"
    try {
        $html = (Invoke-WebRequest -Uri "$base/" -TimeoutSec 30 -UseBasicParsing).Content
        $versions = [regex]::Matches($html, 'href="(\d+\.\d+\.\d+)/"') | ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique -Descending { [version]$_ }
        Log "found $($versions.Count) versions, newest $($versions[0])"

        foreach ($ver in $versions) {
            $zipUrl = "$base/$ver/python-$ver-embed-amd64.zip"
            try {
                $resp = Invoke-WebRequest -Uri $zipUrl -Method Head -TimeoutSec 20 -UseBasicParsing
                if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 400) {
                    Log "selected Python $ver, downloading $zipUrl"
                    $zipPath = Join-Path $pythonDir "python-embed.zip"
                    Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath -TimeoutSec 600 -UseBasicParsing
                    Log "downloaded, size=$((Get-Item $zipPath).Length) bytes"
                    # Extraction stays inline: the probe must not fire before the
                    # PySide6 wheels have landed (handshake below), so there is no
                    # network work to overlap with this archive unpack any more.
                    Expand-Archive -Path $zipPath -DestinationPath $pythonDir -Force
                    Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
                    Log "extracted to $pythonDir"
                    $downloaded = $true
                    break
                }
            } catch {
                Log "  $ver probe failed: $_"
            }
        }
    } catch {
        Log "mirror $base failed: $_"
    }
    if ($downloaded) { break }
}

if (-not $downloaded) {
    Log "ERROR: all mirrors failed to download Python runtime"
    exit 1
}
WriteSignal "get-pip bootstrap" 8

# ---- Activate embedded Python: enable site + add Lib/site-packages to ._pth ----
$pth = Get-ChildItem -Path $pythonDir -Filter "python*._pth" | Select-Object -First 1
if ($pth) {
    $zipName = ($pth.Name -replace '\._pth$', '.zip')
    $content = "$zipName`n.`nLib/site-packages`nimport site`n"
    Set-Content -Path $pth.FullName -Value $content -Encoding ASCII -NoNewline
    Log "embedded Python activated (._pth patched: $($pth.Name))"
} else {
    Log "WARNING: no ._pth found"
}

$pythonExe = Join-Path $pythonDir "python.exe"

function Test-PipInstalled {
    param([string]$PyExe)
    $out = & $PyExe -m pip --version 2>&1
    return ($LASTEXITCODE -eq 0 -and $out -match 'pip \d+')
}

# One place to try a get-pip source: download, run, verify pip answers. Used by
# both the fixed order and the probed order, so both behave identically.
function Invoke-GetPipSource {
    param([string]$Url, [string]$Dest)
    try {
        Invoke-WebRequest -Uri $Url -OutFile $Dest -TimeoutSec 60 -UseBasicParsing
    } catch {
        Log "get-pip download $Url failed: $_"
        return $false
    }
    Log "get-pip.py downloaded from $Url"
    & $pythonExe $Dest --no-warn-script-location 2>&1 | ForEach-Object { Log "get-pip: $_" }
    Remove-Item $Dest -Force -ErrorAction SilentlyContinue
    if (Test-PipInstalled -PyExe $pythonExe) {
        Log "pip bootstrapped via get-pip.py ($Url)"
        return $true
    }
    Log "get-pip from $Url produced no working pip"
    return $false
}

if (Test-PipInstalled -PyExe $pythonExe) {
    Log "pip already installed, skip bootstrap"
} else {
    Log "pip not installed, bootstrap via get-pip.py mirrors"
    $getpip = Join-Path $pythonDir "get-pip.py"
    $bootOk = $false
    foreach ($u in $getPipMirrors) {
        if (Invoke-GetPipSource -Url $u -Dest $getpip) { $bootOk = $true; break }
    }
    if (-not $bootOk) {
        # Fixed order exhausted: measure the bootstrap sources and walk them in
        # measured order instead of guessing. Safe to probe here - PySide6 has not
        # started, so nothing is competing for the line.
        Log "every fixed get-pip source failed; probing the bootstrap sources"
        $bootTargets = @()
        foreach ($u in $getPipCandidates) { $bootTargets += @{ group = "get-pip"; url = $u } }
        $bootTable = @{}
        try { $bootTable = Invoke-SourceProbe $bootTargets 2 512 $null } catch { Log "bootstrap probe failed: $_" }
        $rankedBootstrap = Get-FastestUrls $bootTable "get-pip" $getPipCandidates
        Log ("bootstrap probe order: " + ($rankedBootstrap -join " -> "))
        foreach ($u in $rankedBootstrap) {
            if (Invoke-GetPipSource -Url $u -Dest $getpip) { $bootOk = $true; break }
        }
    }
    if (-not $bootOk) {
        # Last resort: a wheel is a zip. Drop pip straight into site-packages and
        # skip get-pip entirely (this is the path that survives a stale mirror and
        # a Python that no longer ships distutils).
        Log "get-pip failed everywhere, falling back to unzipping a pip wheel"
        $site = Join-Path $pythonDir "Lib\site-packages"
        if (-not (Test-Path $site)) { New-Item -ItemType Directory -Path $site -Force | Out-Null }
        $whl = Join-Path $env:TEMP "pip-bootstrap.whl"
        $pipIndexes = @("https://pypi.tuna.tsinghua.edu.cn/simple/pip/", "https://mirrors.aliyun.com/pypi/simple/pip/", "https://pypi.org/simple/pip/")
        $whlGot = $false
        foreach ($idx in $pipIndexes) {
            try {
                $html = (Invoke-WebRequest -Uri $idx -TimeoutSec 15 -UseBasicParsing).Content
                $m = [regex]::Match($html, 'href="([^"]+/pip-[0-9][^"]*py3-none-any\.whl[^"]*)"')
                if (-not $m.Success) { $m = [regex]::Match($html, 'href="([^"]+pip-[0-9][^"]*py3-none-any\.whl[^"]*)"') }
                if (-not $m.Success) { Log "pip wheel not listed at $idx"; continue }
                $link = $m.Groups[1].Value
                if ($link -notmatch '^https?://') { $link = ([uri]$idx).GetLeftPart([System.UriPartial]::Authority) + $link }
                Invoke-WebRequest -Uri $link -OutFile $whl -TimeoutSec 120 -UseBasicParsing
                Log "pip wheel fetched: $link"
                $whlGot = $true
                break
            } catch {
                Log "pip wheel from $idx failed: $_"
            }
        }
        if ($whlGot) {
            try {
                Add-Type -AssemblyName System.IO.Compression.FileSystem
                [System.IO.Compression.ZipFile]::ExtractToDirectory($whl, $site)
                Log "pip wheel unzipped into site-packages"
            } catch {
                Log "pip wheel unzip failed: $_"
            }
            Remove-Item $whl -Force -ErrorAction SilentlyContinue
        }
        if (-not (Test-PipInstalled -PyExe $pythonExe)) {
            Log "ERROR: pip still unavailable after bootstrap"
            exit 1
        }
        Log "pip bootstrapped via wheel unzip"
    }
}
WriteSignal "download PySide6" 12

# (no probe has run yet: the probe fires after PySide6 finishes, see the handshake)

# ---- install PySide6 (fixed order: domestic first, official last) ----
$pipCandidates = @(
    "https://pypi.tuna.tsinghua.edu.cn/simple",
    "https://mirrors.aliyun.com/pypi/simple/",
    "https://repo.huaweicloud.com/repository/pypi/simple",
    "https://pypi.org/simple"
)
# Reachable is not enough: files.pythonhosted.org accepted the connection and then
# handed over 0 bytes in 8s (the old run got 44.9 kB/s, i.e. 96 min for 259 MB).
# Pick sources by measured throughput, with explicit lines instead of a hunch:
#   259 MB / 45 kB/s  = 96 min    259 MB / 256 kB/s = 17.3 min
#   259 MB / 512 kB/s = 8.6 min   259 MB / 1 MB/s   = 4.3 min
# So: below 256 kB/s = dead, 512 kB/s = usable, switch only when 2x faster.
$PIP_DEAD_KBS = 256
$PIP_USABLE_KBS = 512
$PIP_SWITCH_RATIO = 2.0
$PIP_SAMPLE_SEC = 5
$PIP_SAMPLE_MAX_KB = 2048

function Measure-Speed($url, $sampleSec = $PIP_SAMPLE_SEC, $timeoutSec = 8, $maxKb = $PIP_SAMPLE_MAX_KB) {
    try {
        $req = [System.Net.HttpWebRequest]::Create($url)
        $req.Timeout = $timeoutSec * 1000
        $req.ReadWriteTimeout = $timeoutSec * 1000
        $req.UserAgent = "Rheia-Installer"
        # sample the first 512 KB with a Range request instead of pulling the file
        if ($maxKb -gt 0) { $req.AddRange(0, ($maxKb * 1024) - 1) }
        $sw = [Diagnostics.Stopwatch]::StartNew()
        $resp = $req.GetResponse()
        $stream = $resp.GetResponseStream()
        $buf = New-Object byte[] 32768
        $total = 0
        while ($sw.Elapsed.TotalSeconds -lt $sampleSec -and ($total / 1KB) -lt $maxKb) {
            $n = $stream.Read($buf, 0, $buf.Length)
            if ($n -le 0) { break }
            $total += $n
        }
        $secs = $sw.Elapsed.TotalSeconds
        $stream.Close()
        $resp.Close()
        if ($secs -le 0) { return 0 }
        return [int]($total / 1KB / $secs)
    } catch {
        return 0
    }
}

# Probing the index page measures the API host, not the CDN that actually ships
# the wheels. Two steps: pull the index, take the first wheel URL, measure that.
function Get-FirstWheelUrl($indexUrl) {
    try {
        $html = (Invoke-WebRequest -Uri $indexUrl -TimeoutSec 10 -UseBasicParsing).Content
        $m = [regex]::Match($html, 'href="([^"]+\.whl[^"]*)"')
        if ($m.Success) {
            $u = $m.Groups[1].Value
            if ($u -notmatch '^https?://') {
                $u = ([uri]$indexUrl).GetLeftPart([System.UriPartial]::Authority) + $u
            }
            return $u
        }
    } catch {}
    return $null
}

# PySide6 runs on a fixed order (domestic first, official last) and is not part of
# the probe. pip's own throughput printout demotes a slow mirror live, so the
# measurement still happens - it just happens on real wheel bytes, not on a sample.
$pysideMirrors = New-Object System.Collections.ArrayList
foreach ($u in $pipCandidates) { [void]$pysideMirrors.Add($u) }
Log ("PySide6 source order (fixed, not probed): " + (($pysideMirrors | ForEach-Object { $_ }) -join " -> "))

$target = Join-Path $pythonDir "Lib\site-packages"
$pysideInstalled = $false
$wheelNo = 0
$pysideSlow = $false
# iterate over a snapshot: the demotion below edits $pipMirrors, and editing a
# collection while foreach is walking it throws "Collection was modified".
$mirrorQueue = @($pysideMirrors)
$pysideSourceUsed = ""
foreach ($pm in $mirrorQueue) {
    $pysideSlow = $false
    Log "pip install PySide6 from $pm"
    # PySide6 is ~270 MB. Between wheels pip prints nothing for minutes, so the
    # bar used to sit at 12% and look dead. Heartbeat the signal file (text moves,
    # pct keeps the last real value) and bump once per wheel start.
    $hb = $null
    try {
        $hb = Start-Job -ScriptBlock {
            param($sig, $label)
            $n = 0
            while ($true) {
                Start-Sleep -Seconds 5
                $n += 5
                $pct = 16
                try {
                    $last = Get-Content -Path $sig -Tail 1 -ErrorAction SilentlyContinue
                    if ($last -match '\|\s*(\d+)\s*\|') { $pct = $Matches[1] }
                } catch {}
                try {
                    Add-Content -Path $sig -Value ("{0} | {1} | running" -f $label, $pct) -Encoding UTF8
                } catch {}
            }
        } -ArgumentList $signalFile, "PySide6 downloading"
    } catch {
        Log "heartbeat unavailable: $_"
    }
    try {
        & $pythonExe -m pip install PySide6 --upgrade --force-reinstall -i $pm --target $target --no-warn-script-location --retries 2 --timeout 15 2>&1 | ForEach-Object {
            $line = [string]$_
            Log "pyside6: $line"
            if ($line -match 'Downloading (\S+\.whl) \(([\d.]+) (kB|MB)\)') {
                $wheelNo++
                $pct = [Math]::Min(24, 16 + $wheelNo)
                WriteSignal ("downloading " + $Matches[1] + " (" + $Matches[2] + " " + $Matches[3] + ")") $pct "running"
            }
            # pip prints "---- 40.5/40.5 MB 44.9 kB/s 0:15:03" when a wheel lands.
            # That is the only real throughput reading we get; use it as evidence.
            if ($line -match '([\d.]+)\s*(kB|MB)/s') {
                $spKbs = [double]$Matches[1]
                if ($Matches[2] -eq 'MB') { $spKbs = $spKbs * 1024 }
                Log ("wheel throughput: " + [int]$spKbs + " KB/s (dead line " + $PIP_DEAD_KBS + ")")
                if ($spKbs -lt $PIP_DEAD_KBS) {
                    $pysideSlow = $true
                    Log "source below dead line for this wheel: $pm"
                }
            }
        }
    } finally {
        if ($hb) { Stop-Job $hb -ErrorAction SilentlyContinue; Remove-Job $hb -Force -ErrorAction SilentlyContinue }
    }
    if ($pysideSlow -and $pysideMirrors.Count -gt 1) {
        [void]$pysideMirrors.Remove($pm)
        [void]$pysideMirrors.Add($pm)
        Log "demoted slow pip source to the back: $pm"
    }
    if ($LASTEXITCODE -eq 0) {
        # "pip said 0" is not proof. A truncated wheel leaves an importable-looking
        # directory that dies at startup with a bare ModuleNotFoundError, which is
        # exactly what the 2026-10-28 build shipped. Verify the tree before trusting
        # the exit code: import it, and require the platform plugin dir to be there.
        $psi = Join-Path $target "PySide6\__init__.py"
        $psp = Join-Path $target "PySide6\plugins\platforms"
        $ver = (& $pythonExe -c "import PySide6, sys; sys.stdout.write(str(getattr(PySide6, '__version__', '?')))" 2>&1)
        $impRc = $LASTEXITCODE
        if (($impRc -eq 0) -and (Test-Path $psi) -and (Test-Path $psp)) {
            Log ("PySide6 verified: version " + ([string]$ver).Trim() + ", plugins\platforms present (source " + $pm + ")")
            $pysideInstalled = $true
            $pysideSourceUsed = [string]$pm
            break
        }
        Log ("PySide6 from " + $pm + " is incomplete (import rc=" + $impRc + ", __init__=" + (Test-Path $psi) + ", platforms=" + (Test-Path $psp) + ") -> removing the partial tree and trying the next source")
        foreach ($pkg in @("PySide6", "PySide6_Addons", "PySide6_Essentials", "shiboken6")) {
            Remove-Item -Path (Join-Path $target $pkg) -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    Log "pip install PySide6 failed rc=$LASTEXITCODE, try next mirror"
}

if (-not $pysideInstalled) {
    Log "ERROR: PySide6 install failed on all mirrors"
    exit 1
}
WriteSignal "download PySide6 done" 25

# ---- handshake: PySide6 is on disk, now measure the rest ----
# The probe fires here, not at startup: the PySide6 wheels had to land first.
# Everything below (pip sources for cryptography/setuptools/wheel/zstandard) and
# everything install.py does afterwards (Ubuntu image / Node / npm) reads the
# table this pass writes. The source that served PySide6 is never measured and is
# never offered to the other steps.
Log "handshake: PySide6 done -> probe the remaining sources"
$script:pipMirrors = @($pipCandidates)
try {
    Initialize-SourceTable -ExcludeUrl $pysideSourceUsed
} catch {
    Log "probe after PySide6 failed, keeping candidate order: $_"
}

$cryptoInstalled = $false
$cryptoSrc = $pipMirrors
& $pythonExe -c "import cryptography" 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) { $cryptoInstalled = $true; $cryptoSrc = @(); Log "cryptography already present, skip" }
foreach ($pm in $cryptoSrc) {
    Log "pip install cryptography from $pm"
    & $pythonExe -m pip install cryptography -i $pm --target $target --no-warn-script-location 2>&1 | ForEach-Object { Log "crypto: $_" }
    if ($LASTEXITCODE -eq 0) {
        & $pythonExe -c "import cryptography" 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Log "cryptography installed and imports"
            $cryptoInstalled = $true
            break
        }
        Log "cryptography from $pm installed but does not import, trying next mirror"
        continue
    }
    Log "pip install cryptography failed rc=$LASTEXITCODE, try next mirror"
}
if (-not $cryptoInstalled) {
    Log "WARNING: cryptography install failed on all mirrors (fd_unlock will degrade)"
}

# zstandard sdist needs setuptools.build_meta; install build tools first
$buildToolsInstalled = $false
$buildSrc = $pipMirrors
& $pythonExe -c "import setuptools, wheel" 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) { $buildToolsInstalled = $true; $buildSrc = @(); Log "setuptools/wheel already present, skip" }
foreach ($pm in $buildSrc) {
    Log "pip install setuptools wheel from $pm"
    & $pythonExe -m pip install setuptools wheel -i $pm --target $target --no-warn-script-location 2>&1 | ForEach-Object { Log "buildtools: $_" }
    if ($LASTEXITCODE -eq 0) {
        & $pythonExe -c "import setuptools, wheel" 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Log "setuptools/wheel installed and import"
            $buildToolsInstalled = $true
            break
        }
        Log "setuptools/wheel from $pm installed but do not import, trying next mirror"
        continue
    }
}
if (-not $buildToolsInstalled) { Log "WARNING: setuptools/wheel install failed on all mirrors" }

$zstdInstalled = $false
$zstdSrc = $pipMirrors
& $pythonExe -c "import zstandard" 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) { $zstdInstalled = $true; $zstdSrc = @(); Log "zstandard already present, skip" }
foreach ($pm in $zstdSrc) {
    Log "pip install zstandard from $pm (binary wheels only: embedded Python has no C headers)"
    # No include/Python.h in the embed package, so any sdist build fails anyway.
    # Fail fast instead of burning ~20s of MSVC per mirror.
    & $pythonExe -m pip install zstandard --only-binary=:all: -i $pm --target $target --no-warn-script-location --retries 2 --timeout 15 2>&1 | ForEach-Object { Log "zstd: $_" }
    if ($LASTEXITCODE -eq 0) {
        & $pythonExe -c "import zstandard" 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Log "zstandard installed and imports"
            $zstdInstalled = $true
            break
        }
        Log "zstandard from $pm installed but does not import, trying next mirror"
        continue
    }
    Log "pip install zstandard failed rc=$LASTEXITCODE, try next mirror"
}
if (-not $zstdInstalled) {
    Log "WARNING: zstandard install failed on all mirrors (.indv/.indm import will degrade)"
}

# ---- fix ACL: pip --target into Program Files creates admin-only files ----
# The desktop shortcut runs as a normal user, so grant the Users group read+execute
# recursively over the whole python runtime, or the user cannot import PySide6.
Log "granting Users read+execute on pythonDir"
$usersSid = "*S-1-5-32-545"   # built-in Users group (language-independent)
& icacls $pythonDir /grant "${usersSid}:(OI)(CI)RX" /T /Q 2>&1 | ForEach-Object { Log "icacls: $_" }
Log "icacls done rc=$LASTEXITCODE"

Log "=== download_python.ps1 done ==="
exit 0
