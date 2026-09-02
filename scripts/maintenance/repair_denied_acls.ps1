<#
.SYNOPSIS
  Repair directories under this project (and the pytest temp root) that deny
  access to the current user, typically left behind by an earlier agent sandbox.

.DESCRIPTION
  Scans the given roots for directories the current user cannot list, then for
  each one: takes ownership (takeown /R), resets the ACL to inherit from its
  parent (icacls /reset /T), and re-verifies access. Nothing is deleted.
  Requires an elevated (Administrator) PowerShell.

  -WhatIf lists the directories without changing anything.

.EXAMPLE
  # dry run (no elevation needed)
  .\scripts\maintenance\repair_denied_acls.ps1 -WhatIf

  # real repair, launched from a normal PowerShell; one UAC prompt appears
  Start-Process pwsh -Verb RunAs -ArgumentList '-NoExit','-ExecutionPolicy','Bypass','-File',"$PWD\scripts\maintenance\repair_denied_acls.ps1"
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string[]]$Roots = @(
        (Join-Path $PSScriptRoot '..\..\data'),
        (Join-Path $PSScriptRoot '..\..\artifacts'),
        (Join-Path $PSScriptRoot '..\..\.tmp'),
        (Join-Path $PSScriptRoot '..\..\.unlazy'),
        (Join-Path $env:LOCALAPPDATA 'Temp')
    ),
    [int]$MaxDepth = 4,
    [string]$ReportPath = (Join-Path $PSScriptRoot '..\..\artifacts\recovery\acl_repair_report.json')
)

$ErrorActionPreference = 'Continue'
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

function Test-DirDenied([string]$Path) {
    try { [System.IO.Directory]::EnumerateFileSystemEntries($Path) | Select-Object -First 1 | Out-Null; return $false }
    catch [System.UnauthorizedAccessException] { return $true }
    catch { return $false }
}

function Find-DeniedDirs([string]$Root, [int]$Depth) {
    if (-not (Test-Path -LiteralPath $Root)) { return }
    if (Test-DirDenied $Root) { $Root; return }
    if ($Depth -le 0) { return }
    $children = @()
    try { $children = [System.IO.Directory]::EnumerateDirectories($Root) } catch { return }
    foreach ($child in $children) {
        if (Test-DirDenied $child) { $child }
        else { Find-DeniedDirs $child ($Depth - 1) }
    }
}

# An elevated token can read the broken directories, so an elevated scan finds
# nothing. Prefer the list produced by a normal (non-admin) scan.
$listPath = Join-Path $PSScriptRoot '..\..\artifacts\recovery\acl_denied_dirs.txt'
if (Test-Path -LiteralPath $listPath) {
    $denied = @(Get-Content -LiteralPath $listPath | Where-Object { $_.Trim() } | Where-Object { Test-Path -LiteralPath $_ })
    Write-Host "Using scan list: $listPath"
}
else {
    if ($isAdmin) { Write-Warning 'Elevated scan may find nothing; run once without elevation first to produce the list.' }
    # The Temp root itself is huge; only inspect its immediate children.
    $denied = foreach ($root in $Roots) {
        $resolved = [System.IO.Path]::GetFullPath($root)
        $depth = if ($resolved -ieq (Join-Path $env:LOCALAPPDATA 'Temp')) { 1 } else { $MaxDepth }
        Find-DeniedDirs $resolved $depth
    }
    $denied = @($denied | Sort-Object -Unique)
    if (-not $isAdmin -and $denied.Count -gt 0) {
        New-Item -ItemType Directory -Force (Split-Path $listPath) | Out-Null
        $denied | Set-Content -LiteralPath $listPath -Encoding UTF8
        Write-Host "Scan list written: $listPath"
    }
}

Write-Host ("Found {0} access-denied directories." -f $denied.Count)
$denied | ForEach-Object { Write-Host "  $_" }

if ($denied.Count -eq 0) { return }
if (-not $isAdmin -and -not $WhatIfPreference) {
    Write-Warning 'Not elevated. Re-run from an Administrator PowerShell (see .EXAMPLE) or use -WhatIf.'
    exit 3
}

$results = foreach ($dir in $denied) {
    if (-not $PSCmdlet.ShouldProcess($dir, 'takeown + icacls /reset')) { continue }
    $takeown = & takeown.exe /F $dir /R /D Y 2>&1 | Out-String
    $reset   = & icacls.exe $dir /reset /T /C /Q 2>&1 | Out-String
    $grant   = & icacls.exe $dir /grant "${env:USERNAME}:(OI)(CI)F" /T /C /Q 2>&1 | Out-String
    # Under elevation, listing always succeeds, so verify by ACL content instead.
    $userHasAce = $false
    try {
        $userHasAce = [bool]((Get-Acl -LiteralPath $dir).Access | Where-Object {
            $_.IdentityReference.Value -ieq "$env:USERDOMAIN\$env:USERNAME" -and $_.AccessControlType -eq 'Allow'
        })
    } catch { }
    [pscustomobject]@{
        path        = $dir
        repaired    = $userHasAce
        takeown_ok  = ($LASTEXITCODE -eq 0)
        icacls_tail = ($reset + $grant).Trim().Split("`n")[-1]
    }
}

if ($results) {
    $ok = @($results | Where-Object repaired).Count
    Write-Host ("Repaired {0} / {1}" -f $ok, $results.Count)
    $results | Where-Object { -not $_.repaired } | ForEach-Object { Write-Warning "STILL DENIED: $($_.path)" }
    New-Item -ItemType Directory -Force (Split-Path $ReportPath) | Out-Null
    [pscustomobject]@{
        observed_at = (Get-Date).ToString('o')
        elevated    = $isAdmin
        repaired    = $ok
        total       = $results.Count
        entries     = $results
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $ReportPath -Encoding UTF8
    Write-Host "Report: $ReportPath"
    if ($ok -ne $results.Count) { exit 1 }
}
