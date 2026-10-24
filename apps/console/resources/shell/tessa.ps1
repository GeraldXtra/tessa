[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

$global:__TessaE = [string][char]27
$global:__TessaA = [string][char]7
$global:__TessaFirst = $true
$global:__TessaRan = $false
$global:__TessaLec = $null
$global:__TessaErr0 = $null
$global:__TessaHooked = $false
$global:__TessaMode = if ($env:TESSA_PROMPT -eq 'shell') { 'shell' } else { 'tessa' }
$global:__TessaOwn = $function:prompt
$global:__TessaFolderSgr = if ($env:TESSA_SGR_FOLDER) { $env:TESSA_SGR_FOLDER } else { '0' }
$global:__TessaDimSgr = if ($env:TESSA_SGR_DIM) { $env:TESSA_SGR_DIM } else { '0' }
$global:__TessaInputSgr = if ($env:TESSA_SGR_INPUT) { $env:TESSA_SGR_INPUT } else { '0' }
$global:__TessaBanner = $env:TESSA_BANNER
Get-ChildItem Env: | Where-Object { $_.Name -like 'TESSA_*' } | ForEach-Object { Remove-Item -LiteralPath ("Env:\" + $_.Name) -ErrorAction SilentlyContinue }

function global:__TessaHead([string]$start) {
    $d = $start
    while ($d) {
        $g = [System.IO.Path]::Combine($d, '.git')
        if ([System.IO.Directory]::Exists($g)) { return [System.IO.Path]::Combine($g, 'HEAD') }
        if ([System.IO.File]::Exists($g)) {
            $line = [System.IO.File]::ReadAllText($g).Trim()
            if ($line.StartsWith('gitdir:')) {
                $gd = $line.Substring(7).Trim()
                if (-not [System.IO.Path]::IsPathRooted($gd)) { $gd = [System.IO.Path]::Combine($d, $gd) }
                return [System.IO.Path]::Combine($gd, 'HEAD')
            }
            return $null
        }
        $p = [System.IO.Path]::GetDirectoryName($d)
        if (-not $p -or $p -eq $d) { return $null }
        $d = $p
    }
    return $null
}

function global:__TessaBranch([string]$dir) {
    foreach ($k in 'OneDrive', 'OneDriveConsumer', 'OneDriveCommercial') {
        $r = [Environment]::GetEnvironmentVariable($k)
        if ($r -and ($dir -eq $r -or $dir.StartsWith($r.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase))) { return '' }
    }
    try {
        $h = __TessaHead $dir
        if (-not $h -or -not [System.IO.File]::Exists($h)) { return '' }
        $t = [System.IO.File]::ReadAllText($h).Trim()
        if ($t.StartsWith('ref: refs/heads/')) { return $t.Substring(16) }
        if ($t -match '^[0-9a-fA-F]{7,}$') { return $t.Substring(0, 7) }
    } catch { }
    return ''
}

function global:__TessaUri([string]$p) {
    $s = $p.Replace('\', '/').Replace('%', '%25').Replace(' ', '%20').Replace('#', '%23').Replace('?', '%3F').Replace(';', '%3B')
    return 'file://localhost/' + $s
}

function global:__TessaHook {
    $global:__TessaHooked = $true
    $e = $global:__TessaE
    if ($global:__TessaMode -eq 'tessa' -and (Get-Command Set-PSReadLineOption -ErrorAction SilentlyContinue)) {
        $w = $e + '[' + $global:__TessaInputSgr + 'm'
        foreach ($k in 'Default', 'Command', 'Parameter', 'String', 'Operator', 'Variable', 'Number', 'Member', 'Type', 'Keyword', 'Comment', 'Emphasis', 'Error') {
            try { Set-PSReadLineOption -Colors @{ $k = $w } } catch { }
        }
        try { Set-PSReadLineOption -Colors @{ ContinuationPrompt = ($e + '[' + $global:__TessaDimSgr + 'm') } } catch { }
    }
    if (Test-Path Function:\PSConsoleHostReadLine) {
        $global:__TessaReadLine = $function:PSConsoleHostReadLine
        function global:PSConsoleHostReadLine {
            $line = & $global:__TessaReadLine
            if ($null -ne $line -and $line.Trim().Length -gt 0) {
                $global:__TessaRan = $true
                $global:__TessaLec = $global:LASTEXITCODE
                $global:__TessaErr0 = if ($global:Error.Count -gt 0) { $global:Error[0] } else { $null }
                [Console]::Write($global:__TessaE + ']133;C' + $global:__TessaA)
            }
            $line
        }
    }
}

function global:prompt {
    $ok = $global:?
    $lec = $global:LASTEXITCODE
    $e = $global:__TessaE
    $a = $global:__TessaA
    if (-not $global:__TessaHooked) { __TessaHook }
    $s = ''
    if ($global:__TessaRan) {
        $global:__TessaRan = $false
        $errGrew = ($global:Error.Count -gt 0) -and -not [object]::ReferenceEquals($global:Error[0], $global:__TessaErr0)
        $code = 0
        if ($lec -ne $global:__TessaLec -and $null -ne $lec -and $lec -ne 0) { $code = [int]$lec }
        elseif (-not $ok) {
            if (-not $errGrew -and $null -ne $lec -and $lec -ne 0) { $code = [int]$lec } else { $code = 1 }
        }
        $s += $e + ']133;D;' + $code + $a
    }
    $loc = $ExecutionContext.SessionState.Path.CurrentLocation
    $fs = $loc.Provider.Name -eq 'FileSystem'
    $dir = if ($fs) { $loc.ProviderPath } else { '' }
    if ($fs) { $s += $e + ']7;' + (__TessaUri $dir) + $a }
    if ($global:__TessaFirst) { $global:__TessaFirst = $false } else { $s += "`n" }
    $s += $e + ']133;A' + $a
    if ($global:__TessaMode -eq 'tessa') {
        $leaf = if ($fs) { Split-Path -Leaf $dir } else { $loc.Path }
        if (-not $leaf) { $leaf = if ($fs) { $dir } else { $loc.Path } }
        $br = if ($fs) { __TessaBranch $dir } else { '' }
        $s += $e + '[' + $global:__TessaFolderSgr + 'm' + $leaf + $e + '[0m'
        if ($br) { $s += '  ' + $e + '[' + $global:__TessaDimSgr + 'm' + $br + $e + '[0m' }
        $s += '  ' + $e + '[' + $global:__TessaDimSgr + 'm>' + $e + '[0m '
    } else {
        $own = ''
        try { $own = (& $global:__TessaOwn) -join '' } catch { $own = 'PS ' + $loc.Path + '> ' }
        if (-not $own) { $own = 'PS ' + $loc.Path + '> ' }
        $s += $own
    }
    $s += $e + ']133;B' + $a
    return $s
}

if ($global:__TessaBanner) {
    [Console]::Write($global:__TessaE + '[' + $global:__TessaDimSgr + 'm' + $global:__TessaBanner + $global:__TessaE + "[0m`r`n")
    $global:__TessaFirst = $false
}
