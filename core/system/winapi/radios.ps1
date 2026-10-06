# core/system/winapi/radios.ps1 — Windows.Devices.Radios from PowerShell 5.1.
#
# Invoked ONLY by core/system/winapi/radios.py with a constant argv:
#   powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File radios.ps1
#              -Kind <WiFi|Bluetooth|All> -State <Query|On|Off>
# Both parameters are closed sets, validated here AND in Python. Nothing in
# this file is built from a string it did not declare.
#
# This is the same API the Settings quick-toggles use. It needs no elevation:
# Radio.RequestAccessAsync() returns Allowed for a desktop process.
#
# Output, one record per line, parsed by radios.py:
#   ACCESS|<Allowed|DeniedBySystem|DeniedByUser|Unspecified>
#   SET|<Kind>|<status>              only when -State was On/Off
#   RADIO|<Kind>|<State>|<Name>      the readback, for every radio

param(
  [ValidateSet('WiFi', 'Bluetooth', 'All')][string]$Kind = 'All',
  [ValidateSet('Query', 'On', 'Off')][string]$State = 'Query'
)

$ErrorActionPreference = 'Stop'

[Windows.Devices.Radios.Radio, Windows.System.Devices, ContentType = WindowsRuntime] | Out-Null
Add-Type -AssemblyName System.Runtime.WindowsRuntime

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() |
  Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
                 $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]

function Await($WinRtTask, $ResultType) {
  $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
  $netTask = $asTask.Invoke($null, @($WinRtTask))
  $netTask.Wait(-1) | Out-Null
  $netTask.Result
}

$access = Await ([Windows.Devices.Radios.Radio]::RequestAccessAsync()) ([Windows.Devices.Radios.RadioAccessStatus])
"ACCESS|$access"
if ("$access" -ne 'Allowed') { exit 2 }

$listType = [System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]]
$radios = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) $listType

$changed = $false
if ($State -ne 'Query') {
  foreach ($r in $radios) {
    if ("$($r.Kind)" -eq $Kind) {
      if ($State -eq 'On') { $target = [Windows.Devices.Radios.RadioState]::On }
      else { $target = [Windows.Devices.Radios.RadioState]::Off }
      $res = Await ($r.SetStateAsync($target)) ([Windows.Devices.Radios.RadioAccessStatus])
      "SET|$($r.Kind)|$res"
      $changed = $true
    }
  }
  if ($changed) { Start-Sleep -Milliseconds 700 }
  $radios = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) $listType
}

foreach ($r in $radios) { "RADIO|{0}|{1}|{2}" -f $r.Kind, $r.State, $r.Name }
exit 0
