param([ValidateSet('status','mcp','version')][string]$Mode='status')
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$root=Join-Path $env:LOCALAPPDATA 'Aside\CLI'
$cli=Join-Path $root 'current\aside.exe'
if (-not (Test-Path -LiteralPath $cli)) {
  # Windows may refuse traversing an updater-created junction from an elevated
  # SSH session. Resolve only Aside's own version target, never arbitrary links.
  $link=Get-Item -LiteralPath (Join-Path $root 'current') -Force -ErrorAction SilentlyContinue
  $target=@($link.Target) | Select-Object -First 1
  $prefix=(Join-Path $root 'versions')+'\'
  if ($target -and $target.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase) -and
      $target.Substring($prefix.Length) -match '^(win-x64\\)?[0-9]+(\.[0-9]+){2,3}$') {
    $cli=Join-Path $target 'aside.exe'
  }
}
if (-not (Test-Path -LiteralPath $cli)) {
  if($Mode -eq 'status'){ @{installed=$false;version='';authentication='unknown'}|ConvertTo-Json -Compress;exit 0 }
  throw 'Aside CLI is unavailable. Update or repair it in Aside Developer settings.'
}
if ($Mode -eq 'mcp') { & $cli mcp; exit $LASTEXITCODE }
$version=(& $cli --version 2>$null | Out-String).Trim()
if ($Mode -eq 'version') { $version;exit $LASTEXITCODE }
$auth='unknown'
if (Get-Process -Name Aside -ErrorAction SilentlyContinue) {
  try {
    $status=(& $cli account status 2>$null | Out-String)
    $code=$LASTEXITCODE
    if($status -match '(?i)signed.?out|not.logged|not.signed|login required'){$auth='signed-out'}
    elseif($code -eq 0 -and $status -match '(?i)signed.?in|authenticated'){$auth='signed-in'}
  } catch { $auth='unknown' }
}
# Never return the account name, email, identifiers or raw CLI output.
@{installed=$true;version=$version;authentication=$auth}|ConvertTo-Json -Compress
