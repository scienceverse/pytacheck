# Installs the metacheck preview app into one folder and opens it.
#
#   powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/scienceverse/pytacheck/main/install.ps1 | iex"
#   .\install.ps1 [-NoLaunch] [-Uninstall]
#
# Everything goes into METACHECK_HOME (default: %LOCALAPPDATA%\metacheck):
# uv, its Python, the app and uv's cache. No PATH or registry entry is edited.
# "irm | iex" cannot pass parameters, so these environment variables work too:
#   METACHECK_NO_LAUNCH=1     same as -NoLaunch
#   METACHECK_UNINSTALL=1     same as -Uninstall
#   METACHECK_REF=<commit>    install this commit instead of the pinned one
#   METACHECK_SPEC=<spec>     install this requirement instead of the GitHub archive
#   METACHECK_CONSTRAINTS=<file>  use this constraints file instead of the pinned one
#
# The whole script is one function called on the last line, so a download that
# stops half way runs nothing. It has no param() block, because that does not
# work under iex; the function reads the switches from $args itself.

function Install-Metacheck {
    param([string[]]$Arguments)

    $ErrorActionPreference = 'Stop'

    # The commit that gets installed. Move it when the app moves.
    $Ref = '53d802517291a2be8b0a569da32b27ac3ccd1d99'

    $UvVersion = '0.12.20'
    $UvUrl = "https://github.com/astral-sh/uv/releases/download/$UvVersion"
    $IssuesUrl = 'https://github.com/scienceverse/pytacheck/issues'
    $Tool = 'pytacheck'

    # SHA-256 of each uv archive, from the .sha256 files of the uv release.
    $UvSha = @{
        'x86_64-pc-windows-msvc'  = '95f9bc30fbb3574d276e28ac4a6de932d25153645853d13da8c21eec3bc88d06'
        'aarch64-pc-windows-msvc' = 'b6bd9218855591742ffd3b00fcf2044b4d2fb6446b6a3545e0dfee447e950388'
    }

    function Say([string]$Text) { Write-Host $Text }
    function Fail([string]$Text) { throw "metacheck installer: $Text" }

    $launch = $true
    $uninstall = $false
    if ($env:METACHECK_NO_LAUNCH -eq '1') { $launch = $false }
    if ($env:METACHECK_UNINSTALL -eq '1') { $uninstall = $true }
    foreach ($arg in $Arguments) {
        switch -Regex ($arg) {
            '^-NoLaunch$' { $launch = $false }
            '^-Uninstall$' { $uninstall = $true }
            default { Fail "unknown option: $arg (use -NoLaunch or -Uninstall)" }
        }
    }

    # Windows does not keep an empty environment variable, so unset and empty
    # both mean the default.
    $root = $env:METACHECK_HOME
    if (-not $root) {
        if (-not $env:LOCALAPPDATA) { Fail 'LOCALAPPDATA is not set. Set METACHECK_HOME to the folder to use.' }
        $root = Join-Path $env:LOCALAPPDATA 'metacheck'
    }

    # Refuses any folder that is not clearly ours before anything is removed.
    if (-not $root.Trim()) { Fail 'METACHECK_HOME is empty; refusing to continue.' }
    if ($root.Length -gt [System.IO.Path]::GetPathRoot($root).Length) { $root = $root.TrimEnd('\', '/') }
    if (-not [System.IO.Path]::IsPathRooted($root)) { Fail "METACHECK_HOME must be an absolute path: $root" }
    # One folder, one spelling: this resolves "." and "..", and repeated slashes.
    $root = [System.IO.Path]::GetFullPath($root)
    if ($root.Length -gt [System.IO.Path]::GetPathRoot($root).Length) { $root = $root.TrimEnd('\', '/') }
    if ($root -eq [System.IO.Path]::GetPathRoot($root)) { Fail "METACHECK_HOME is a drive root; refusing to continue: $root" }
    if ($env:USERPROFILE -and ($root -ieq [System.IO.Path]::GetFullPath($env:USERPROFILE).TrimEnd('\', '/'))) {
        Fail 'METACHECK_HOME is your home folder; refusing to continue.'
    }
    if ((Split-Path -Leaf $root) -ine 'metacheck') { Fail "METACHECK_HOME must end in a folder named metacheck: $root" }
    if ((Test-Path -LiteralPath $root) -and ((Get-Item -LiteralPath $root -Force).Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
        Fail "METACHECK_HOME is a link; refusing to continue: $root"
    }

    $uvDir = Join-Path $root 'uv'
    $uvBin = Join-Path $uvDir 'uv.exe'
    $binDir = Join-Path $root 'bin'
    $app = Join-Path $binDir 'metacheck-app.exe'
    # Written at install time. Uninstall removes nothing from a folder without it.
    $marker = Join-Path $root '.metacheck-installer'

    # Every uv setting that could reach outside the folder is pinned here, for
    # this run only, and the ones that change how Python or the packages are
    # chosen are dropped ($null). The caller's own values come back in the
    # finally block.
    $uvEnv = [ordered]@{
        UV_CACHE_DIR                 = Join-Path $root 'cache'
        UV_PYTHON_INSTALL_DIR        = Join-Path $root 'python'
        UV_TOOL_DIR                  = Join-Path $root 'tools'
        UV_TOOL_BIN_DIR              = $binDir
        UV_PYTHON_INSTALL_BIN        = '0'
        UV_PYTHON_INSTALL_REGISTRY   = '0'
        UV_NO_MODIFY_PATH            = '1'
        UV_NO_CONFIG                 = '1'
        UV_PYTHON_DOWNLOADS          = 'automatic'
    }
    foreach ($name in 'UV_PYTHON', 'UV_PYTHON_PREFERENCE', 'UV_MANAGED_PYTHON', 'UV_NO_MANAGED_PYTHON',
        'UV_OFFLINE', 'UV_CONSTRAINT', 'UV_BUILD_CONSTRAINT', 'UV_OVERRIDE', 'UV_EXCLUDE_NEWER',
        'UV_PRERELEASE', 'UV_RESOLUTION', 'UV_NO_BUILD', 'UV_NO_BINARY', 'UV_FROZEN', 'UV_LOCKED') {
        $uvEnv[$name] = $null
    }
    $saved = @{}
    $savedPath = $env:PATH
    foreach ($name in $uvEnv.Keys) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
    $savedProgress = $ProgressPreference
    $savedProtocol = [Net.ServicePointManager]::SecurityProtocol
    $tmp = $null

    # Runs uv and returns its exit code. uv writes progress to stderr, which
    # Windows PowerShell 5.1 turns into errors under 'Stop'.
    function Invoke-Uv {
        $previous = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            & $uvBin @args | Out-Host
            return $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $previous
        }
    }

    try {
        foreach ($name in $uvEnv.Keys) { [Environment]::SetEnvironmentVariable($name, $uvEnv[$name], 'Process') }
        # The bin folder goes on PATH for uv only, so uv does not warn that it is missing.
        $env:PATH = "$binDir;$env:PATH"
        $ProgressPreference = 'SilentlyContinue'
        [Net.ServicePointManager]::SecurityProtocol = $savedProtocol -bor [Net.SecurityProtocolType]::Tls12

        if ($uninstall) {
            if (-not (Test-Path -LiteralPath $root)) {
                Say "Nothing to remove: $root does not exist."
            } elseif (-not (Test-Path -LiteralPath $marker)) {
                Fail "$root was not made by this installer (it has no .metacheck-installer file). Nothing was removed."
            } else {
                if (Test-Path -LiteralPath $uvBin) {
                    $null = Invoke-Uv tool uninstall $Tool 2>$null
                }
                foreach ($sub in 'uv', 'python', 'tools', 'bin', 'cache') {
                    $path = Join-Path $root $sub
                    if (Test-Path -LiteralPath $path) {
                        try {
                            Remove-Item -LiteralPath $path -Recurse -Force
                        } catch {
                            Fail "could not remove $path. Close metacheck if it is running, then try again."
                        }
                    }
                }
                Remove-Item -LiteralPath $marker -Force
                if (@(Get-ChildItem -LiteralPath $root -Force).Count -eq 0) {
                    Remove-Item -LiteralPath $root -Force
                    Say "Removed metacheck from $root."
                } else {
                    Say "Left $root in place: it holds files the installer did not create. Everything else is removed."
                }
            }
            Say "The app's own settings are kept in $(Join-Path $env:LOCALAPPDATA 'pytacheck\pytacheck')."
            Say 'Delete that folder too if you want them gone.'
            return
        }

        # Platform.
        $cpu = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
        switch ($cpu) {
            'AMD64' { $target = 'x86_64-pc-windows-msvc' }
            'ARM64' { $target = 'aarch64-pc-windows-msvc' }
            default { Fail "unsupported CPU: $cpu. Please tell us at $IssuesUrl" }
        }

        $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("metacheck." + [System.Guid]::NewGuid().ToString('N'))
        $null = New-Item -ItemType Directory -Path $tmp
        $null = New-Item -ItemType Directory -Force -Path $root, $uvDir, $binDir
        $null = New-Item -ItemType File -Force -Path $marker

        # uv: download and check it, unless the right version is already here.
        $haveUv = $false
        if (Test-Path -LiteralPath $uvBin) {
            $version = & $uvBin --version 2>$null
            if ($version -like "uv $UvVersion*") { $haveUv = $true }
        }
        if (-not $haveUv) {
            $archive = "uv-$target.zip"
            $zip = Join-Path $tmp $archive
            Say "Downloading uv $UvVersion ..."
            try {
                Invoke-WebRequest -UseBasicParsing -Uri "$UvUrl/$archive" -OutFile $zip
            } catch {
                Fail "could not download $UvUrl/$archive. Check your internet connection and any proxy settings."
            }
            $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $zip).Hash.ToLower()
            if ($actual -ne $UvSha[$target]) {
                Fail "checksum mismatch for $archive (expected $($UvSha[$target]), got $actual). Nothing was installed."
            }
            $unpacked = Join-Path $tmp 'uv'
            Expand-Archive -LiteralPath $zip -DestinationPath $unpacked -Force
            $found = Get-ChildItem -LiteralPath $unpacked -Recurse -Filter 'uv.exe' | Select-Object -First 1
            if (-not $found) { Fail "could not find uv.exe in $archive." }
            Copy-Item -LiteralPath $found.FullName -Destination "$uvBin.new" -Force
            Move-Item -LiteralPath "$uvBin.new" -Destination $uvBin -Force
        }

        # The app.
        $ref = if ($env:METACHECK_REF) { $env:METACHECK_REF } else { $Ref }
        $spec = if ($env:METACHECK_SPEC) { $env:METACHECK_SPEC } else { "${Tool}[app] @ https://github.com/scienceverse/pytacheck/archive/$ref.tar.gz" }
        $constraints = $env:METACHECK_CONSTRAINTS
        if (-not $constraints) {
            $constraints = Join-Path $tmp 'constraints-app.txt'
            try {
                Invoke-WebRequest -UseBasicParsing -Uri "https://raw.githubusercontent.com/scienceverse/pytacheck/$ref/install/constraints-app.txt" -OutFile $constraints
            } catch {
                Fail "could not download the constraints file for $ref. Check your internet connection."
            }
        } elseif (-not (Test-Path -LiteralPath $constraints -PathType Leaf)) {
            Fail "METACHECK_CONSTRAINTS is not a file: $constraints"
        }

        Say 'Installing metacheck (Python and the app, about 1 to 2 minutes the first time) ...'
        $code = Invoke-Uv tool install --managed-python --python 3.12 $spec -c $constraints
        if ($code -ne 0) { Fail "the install failed. If it keeps failing, please tell us at $IssuesUrl" }
        if (-not (Test-Path -LiteralPath $app)) { Fail "the install finished but $app is missing. Please tell us at $IssuesUrl" }
    } finally {
        foreach ($name in $uvEnv.Keys) { [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process') }
        $env:PATH = $savedPath
        $ProgressPreference = $savedProgress
        [Net.ServicePointManager]::SecurityProtocol = $savedProtocol
        if ($tmp -and (Test-Path -LiteralPath $tmp)) { Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue }
    }

    Say ''
    Say "metacheck is installed in $root"
    Say 'To open it again, run the same command again (it takes a few seconds),'
    Say "or run: $app"
    Say 'To remove it, run these three lines:'
    Say '  $env:METACHECK_UNINSTALL=1'
    Say '  irm https://raw.githubusercontent.com/scienceverse/pytacheck/main/install.ps1 | iex'
    Say '  Remove-Item Env:METACHECK_UNINSTALL'
    Say ''

    if ($launch) { & $app }
}

Install-Metacheck $args
