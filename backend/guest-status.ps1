$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
# Query session state directly. An unrelated RDP login screen is not proof that
# the user's existing interactive session is locked. Never collect window titles.
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public class OmachinesSession {
 [DllImport("wtsapi32.dll")] static extern bool WTSQuerySessionInformation(IntPtr h,int id,int cls,out IntPtr p,out int n);
 [DllImport("wtsapi32.dll")] static extern void WTSFreeMemory(IntPtr p);
 public static int State(int id) {
  IntPtr p; int n;
  if (!WTSQuerySessionInformation(IntPtr.Zero,id,25,out p,out n)) return -1;
  try {
   // WTSINFOEX: DWORD level, padding, WTSINFOEX_LEVEL1 (sessionId, state, flags).
   if(n < 20 || Marshal.ReadInt32(p,0)!=1) return -1;
   if(Marshal.ReadInt32(p,12)!=0) return 2; // not WTSActive
   return Marshal.ReadInt32(p,16); // Windows 8+: 0 locked, 1 unlocked
  } finally { WTSFreeMemory(p); }
 }
}
'@
$driver = @(Get-Process -Name cua-driver -ErrorAction SilentlyContinue | Where-Object {$_.SessionId -gt 0} | Select-Object -ExpandProperty SessionId -Unique)
$explorer = @(Get-Process -Name explorer -ErrorAction SilentlyContinue | Select-Object -ExpandProperty SessionId -Unique)
$desktop = 'unknown'
$sessions = if($driver.Count -gt 0){$driver}else{$explorer}
$flags = @($sessions | ForEach-Object {[OmachinesSession]::State($_)})
if($flags -contains 1){$desktop='available'}elseif($flags -contains 0){$desktop='locked'}elseif($flags -contains 2){$desktop='disconnected'}elseif($sessions.Count -eq 0){$desktop='signed-out'}
$os=Get-CimInstance Win32_OperatingSystem
$cpu=Get-CimInstance Win32_ComputerSystem
$disk=Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
$release=Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
$pending=[bool](@('HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired','HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending') | Where-Object {Test-Path $_})
# The last Omachines update run: its status and finish time only.
$maintenance=$null
try { $maintenance=Get-Content -Raw 'C:\OEM\Omachines\maintenance.json' -ErrorAction Stop | ConvertFrom-Json } catch {}
[ordered]@{
 desktop=$desktop
 cpu=[int]$cpu.NumberOfLogicalProcessors
 memoryBytes=[long]$cpu.TotalPhysicalMemory
 diskBytes=[long]$disk.Size
 diskFreeBytes=[long]$disk.FreeSpace
 boot=$os.LastBootUpTime.ToUniversalTime().ToString('o')
 osVersion="$($release.DisplayVersion) build $($release.CurrentBuild).$($release.UBR)"
 rebootPending=$pending
 updateStatus=$(if($maintenance){$maintenance.status}else{''})
 updatedAt=$(if($maintenance){$maintenance.finishedAt}else{''})
 asideInstalled=(Test-Path "$env:LOCALAPPDATA\Aside\Application\aside.exe")
 asidePath="$env:LOCALAPPDATA\Aside\Application\aside.exe"
 asideRunning=(@(Get-Process -Name aside -ErrorAction SilentlyContinue | Where-Object {$_.SessionId -gt 0}).Count -gt 0)
} | ConvertTo-Json -Compress
