# Updates Windows and the developer tools in the signed-in user's session.
# Omachines copies this file into the guest and runs it as a scheduled task:
# winget, the mise and rustup user installs and Aside only work properly from
# that session. It reports counts and versions only, in a small JSON file the
# host polls. Package titles stay in the guest's log. It never restarts Windows.
param([string]$RunId = 'manual', [switch]$SkipCleanup)
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$root = 'C:\OEM\Omachines'
New-Item -ItemType Directory -Force $root | Out-Null
$statePath = Join-Path $root 'maintenance.json'
$logPath = Join-Path $root 'maintenance.log'
$state = [ordered]@{
  schemaVersion = 1; runId = $RunId; status = 'running'; stage = 'starting'
  startedAt = (Get-Date).ToUniversalTime().ToString('o'); finishedAt = ''
  windowsUpdates = 0; windowsFailed = 0; apps = 0; appsFailed = 0
  rebootRequired = $false; problems = @(); versions = @{}
}
$env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')

function Save-State {
  $temp = "$statePath.tmp"
  $state | ConvertTo-Json -Depth 4 -Compress | Set-Content -Encoding UTF8 $temp
  Move-Item -Force $temp $statePath
}
function Log([string]$text) { "$((Get-Date).ToString('s')) $text" | Add-Content -Encoding UTF8 $logPath }
function Stage([string]$name) { $state.stage = $name; Log "== $name"; Save-State }
function Problem([string]$text) { $state.problems += $text; Log "PROBLEM $text"; Save-State }
# Output of native tools goes to the log; only their exit code comes back.
# Through Add-Content: a redirection would append UTF-16 to this UTF-8 log.
function Invoke-Logged([string]$file, [string[]]$arguments) {
  Log "> $file $($arguments -join ' ')"
  # Stays -1 if the program cannot even start, which sets no exit code.
  $global:LASTEXITCODE = -1
  & $file @arguments 2>&1 | ForEach-Object { "$_" } | Add-Content -Encoding UTF8 $logPath
  return $LASTEXITCODE
}
function Reboot-Pending {
  $paths = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired',
           'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending'
  return [bool]($paths | Where-Object { Test-Path $_ })
}

function Update-Windows {
  Stage 'windows-update'
  try {
    $session = New-Object -ComObject Microsoft.Update.Session
    $session.ClientApplicationID = 'Omachines'
    $found = $session.CreateUpdateSearcher().Search("IsInstalled=0 and IsHidden=0 and Type='Software'")
    $updates = New-Object -ComObject Microsoft.Update.UpdateColl
    foreach ($update in $found.Updates) {
      # Optional previews and feature upgrades stay a choice in Windows Settings.
      if ($update.BrowseOnly) { Log "skip optional: $($update.Title)"; continue }
      if (-not $update.EulaAccepted) { $update.AcceptEula() }
      Log "update: $($update.Title)"
      [void]$updates.Add($update)
    }
    if ($updates.Count -eq 0) { return }
    $downloader = $session.CreateUpdateDownloader(); $downloader.Updates = $updates
    [void]$downloader.Download()
    $installer = $session.CreateUpdateInstaller(); $installer.Updates = $updates
    $result = $installer.Install()
    for ($i = 0; $i -lt $updates.Count; $i++) {
      if ([int]$result.GetUpdateResult($i).ResultCode -eq 2) { $state.windowsUpdates++ } else { $state.windowsFailed++ }
    }
    if ($result.RebootRequired) { $state.rebootRequired = $true }
  } catch { Problem 'Windows Update did not finish' ; Log $_.Exception.Message }
}

function Update-Apps {
  Stage 'apps'
  $winget = Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\winget.exe'
  if (-not (Test-Path $winget)) { Problem 'winget is not available'; return }
  $common = '--accept-package-agreements', '--accept-source-agreements', '--disable-interactivity', '--silent'
  [void](Invoke-Logged $winget (@('source', 'update', '--disable-interactivity')))
  # winget updates itself first: replacing App Installer ends the running copy.
  [void](Invoke-Logged $winget (@('upgrade', '--id', 'Microsoft.AppInstaller', '--exact', '--source', 'winget') + $common))
  # Right after that, the first call can fail with "Unrecognized command".
  foreach ($attempt in 1, 2) {
    $before = (Get-Item $logPath).Length
    $code = Invoke-Logged $winget (@('upgrade', '--all') + $common)
    $reader = [IO.File]::Open($logPath, 'Open', 'Read', 'ReadWrite')
    try {
      [void]$reader.Seek($before, 'Begin')
      $text = (New-Object IO.StreamReader($reader)).ReadToEnd()
    } finally { $reader.Close() }
    if ($text -notmatch 'Unrecognized command') { break }
    Start-Sleep 10
  }
  $state.apps = ([regex]::Matches($text, 'Successfully installed')).Count
  $state.appsFailed = ([regex]::Matches($text, 'Installer failed|Installation failed|failed to upgrade')).Count
  # -1978335189: no applicable upgrade.
  if ($code -ne 0 -and $code -ne -1978335189 -and $state.appsFailed -eq 0) { $state.appsFailed = 1 }
  $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
}

function Update-Runtimes {
  Stage 'runtimes'
  # Rustup and mise are themselves winget packages, updated in the apps stage.
  $rustup = Join-Path $env:USERPROFILE '.cargo\bin\rustup.exe'
  if (Test-Path $rustup) {
    if ((Invoke-Logged $rustup @('update', 'stable', '--no-self-update')) -ne 0) { Problem 'Rust did not update' }
    $state.versions.rust = ((& (Join-Path $env:USERPROFILE '.cargo\bin\rustc.exe') --version) -split ' ')[1]
  }
  $mise = Get-Command mise -ErrorAction SilentlyContinue
  if ($mise) {
    if ((Invoke-Logged $mise.Source @('upgrade', '--yes')) -ne 0) { Problem 'mise tools did not update' }
    $state.versions.mise = ((& $mise.Source --version) -split ' ')[0]
    $node = Get-Command node -ErrorAction SilentlyContinue
    if ($node) { $state.versions.node = (& $node.Source --version).TrimStart('v') }
  }
}

function Update-Cua {
  Stage 'cua'
  $cua = Join-Path $env:LOCALAPPDATA 'Programs\Cua\cua-driver\bin\cua-driver.exe'
  if (-not (Test-Path $cua)) { return }
  try {
    $check = (& $cua check-update --json --no-cache) -join "`n" | ConvertFrom-Json
    if ($check.update_available) {
      # The canonical installer verifies checksums and Authenticode signatures
      # and retargets a junction, so the running daemon has to be restarted.
      if ((Invoke-Logged $cua @('update', '--apply')) -ne 0) { Problem 'Cua Driver did not update' }
      [void](Invoke-Logged $cua @('stop'))
      Start-Sleep 2
      [void](Invoke-Logged $cua @('autostart', 'enable'))
      [void](Invoke-Logged $cua @('autostart', 'kick'))
    }
  } catch { Problem 'Cua Driver update check failed'; Log $_.Exception.Message }
  $state.versions.cua = ((& $cua --version) -split ' ')[-1]
}

function Update-Aside {
  Stage 'aside'
  $base = Join-Path $env:LOCALAPPDATA 'Aside'
  $updater = Get-ChildItem (Join-Path $base 'AsideUpdater') -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match '^[0-9.]+$' } | Sort-Object { [version]$_.Name } | Select-Object -Last 1
  if ($updater) {
    # A running browser finishes its update when it is next opened.
    $process = Start-Process (Join-Path $updater.FullName 'updater.exe') -ArgumentList '--wake' -PassThru -WindowStyle Hidden
    if (-not $process.WaitForExit(300000)) { Log 'Aside updater still working after 5 minutes' }
  }
  $browser = Join-Path $base 'Application\aside.exe'
  if (Test-Path $browser) { $state.versions.aside = (Get-Item $browser).VersionInfo.ProductVersion }
  $cli = Join-Path $base 'CLI\current\aside.exe'
  if (Test-Path $cli) {
    # This session can follow the updater's junction, unlike elevated SSH.
    # The CLI asks before installing, and has no flag to skip the question.
    Log "> $cli update"
    $global:LASTEXITCODE = -1
    'y' | & $cli update 2>&1 | ForEach-Object { "$_" } | Add-Content -Encoding UTF8 $logPath
    if ($LASTEXITCODE -ne 0) { Problem 'Aside CLI did not update' }
    $state.versions.asideCli = ((& $cli --version) -join ' ').Trim()
  }
}

function Clean-Up {
  Stage 'cleanup'
  # Superseded components and trimmed free space shrink the host's sparse disk
  # image, and every later backup with it.
  [void](Invoke-Logged "$env:WINDIR\System32\Dism.exe" @('/Online', '/Cleanup-Image', '/StartComponentCleanup', '/Quiet', '/NoRestart'))
  try { Optimize-Volume -DriveLetter C -ReTrim -ErrorAction Stop | Out-Null } catch { Log $_.Exception.Message }
}

try {
  Update-Windows
  Update-Apps
  Update-Runtimes
  Update-Cua
  Update-Aside
  if (-not $SkipCleanup) { Clean-Up }
  if (Reboot-Pending) { $state.rebootRequired = $true }
  $state.status = if ($state.problems.Count -or $state.windowsFailed -or $state.appsFailed) { 'partial' } else { 'complete' }
} catch {
  Problem 'Maintenance stopped early'
  Log $_.Exception.Message
  $state.status = 'failed'
} finally {
  $state.stage = 'finished'
  $state.finishedAt = (Get-Date).ToUniversalTime().ToString('o')
  Save-State
}
