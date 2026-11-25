<#
.SYNOPSIS
  Build the lens_z firmware and flash it to the Arduino Mega 2560, then check
  that it answers.

.DESCRIPTION
  1. compiles firmware\lens_z for arduino:avr:mega
  2. finds the Mega by its USB ID, never by COM number
  3. uploads with avrdude (via arduino-cli)
  4. resets the board, waits for READY, sends `ping` and `status`

  Why the USB ID and not a COM number: this PC also has the tip-tilt piezo
  controller and the K-Cube FTDI adapters on serial ports, and avrdude will
  talk its bootloader protocol at whatever port it is given. The script
  refuses unless exactly one Mega 2560 is plugged in.

  Keep the motor supply OFF while flashing. During the bootloader the Mega's
  pins float, and the A4988 carrier enables its outputs when ENABLE floats.

.EXAMPLE
  .\tools\flash.ps1                 # build, flash, check
.EXAMPLE
  .\tools\flash.ps1 -CompileOnly    # build only; touches no hardware
.EXAMPLE
  .\tools\flash.ps1 -CheckOnly      # no build, no flash: reset, ping, status
#>
param(
  [switch]$CompileOnly,
  [switch]$CheckOnly,
  [string]$Cli = $(if ($env:ARDUINO_CLI) { $env:ARDUINO_CLI } else { Join-Path $env:USERPROFILE 'tools\arduino-cli\arduino-cli.exe' })
)
$ErrorActionPreference = 'Stop'

$repo    = Split-Path -Parent $PSScriptRoot
$sketch  = Join-Path $repo 'firmware\lens_z'
$fqbn    = 'arduino:avr:mega'
# Mega 2560: R3 (2341:0042), early R1/R2 (2341:0010), arduino.org boards (2A03:0042)
$megaIds = @('2341:0042', '2341:0010', '2A03:0042')

# ---------------------------------------------------------------- compile
if (-not $CheckOnly) {
  if (-not (Test-Path $Cli)) {
    throw "arduino-cli not found at $Cli. Set ARDUINO_CLI or pass -Cli."
  }
  Write-Host "== compile  $fqbn  $sketch"
  & $Cli compile --fqbn $fqbn --warnings default $sketch
  if ($LASTEXITCODE -ne 0) { throw "compile failed (exit $LASTEXITCODE)" }
  if ($CompileOnly) { Write-Host "== compiled; -CompileOnly, so nothing flashed"; return }
}

# ---------------------------------------------------------------- find the Mega
$megas = @()
foreach ($d in Get-CimInstance Win32_PnPEntity) {
  if ($d.Name -notmatch '\((COM\d+)\)') { continue }
  $port = $Matches[1]
  if ($d.PNPDeviceID -notmatch 'VID_([0-9A-F]{4})&PID_([0-9A-F]{4})') { continue }
  $id = '{0}:{1}' -f $Matches[1], $Matches[2]
  if ($megaIds -contains $id) { $megas += [pscustomobject]@{ Port = $port; Id = $id } }
}
if ($megas.Count -ne 1) {
  throw ("expected exactly one Mega 2560 (USB {0}), found {1}. Nothing was flashed." -f ($megaIds -join ', '), $megas.Count)
}
$port = $megas[0].Port

# ---------------------------------------------------------------- upload
if (-not $CheckOnly) {
  Write-Host ("== upload   {0}  (USB {1})" -f $port, $megas[0].Id)
  & $Cli upload --fqbn $fqbn --port $port $sketch
  if ($LASTEXITCODE -ne 0) {
    throw "upload failed (exit $LASTEXITCODE). Is a serial monitor or another program holding $port open?"
  }
}

# ---------------------------------------------------------------- does it answer?
# Lines are split here from ReadExisting(). SerialPort.ReadLine() with a
# timeout drops characters in .NET Framework when a line arrives in pieces,
# and across a reset it always does.
Write-Host "== check    $port at 115200"
$sp = New-Object System.IO.Ports.SerialPort $port, 115200, 'None', 8, 'One'
$sp.DtrEnable = $true                  # opening with DTR resets the board
$sp.Open()
try {
  # Output from before the port opened can still be queued in the USB chip. It
  # arrives in the first few ms, usually cut off mid-line. Drop it: the reset's
  # own READY comes about a second later, after the bootloader.
  Start-Sleep -Milliseconds 150
  $sp.DiscardInBuffer()

  $script:rx = ''
  function Get-Line([double]$seconds) {
    $end = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $end) {
      $script:rx += $sp.ReadExisting()
      $i = $script:rx.IndexOf("`n")
      if ($i -ge 0) {
        $line = $script:rx.Substring(0, $i).Trim()
        $script:rx = $script:rx.Substring($i + 1)
        return $line
      }
      Start-Sleep -Milliseconds 10
    }
    return $null
  }

  $ready = $null
  $end = (Get-Date).AddSeconds(6)
  while (-not $ready -and (Get-Date) -lt $end) {
    $l = Get-Line 1
    if ($l -like 'READY*') { $ready = $l }
  }
  if (-not $ready) { throw "the board never printed READY after reset" }
  Write-Host "   $ready"

  # Every command ends in exactly one line starting OK or ERR. Anything before
  # it (a progress line, or a READY if the board reset) is shown and skipped.
  foreach ($cmd in 'ping', 'status') {
    $sp.Write("$cmd`n")
    $reply = $null
    $end = (Get-Date).AddSeconds(3)
    while (-not $reply -and (Get-Date) -lt $end) {
      $l = Get-Line 0.5
      if ($null -eq $l) { continue }
      if ($l -like 'OK*' -or $l -like 'ERR*') { $reply = $l } else { Write-Host "   (skipped) $l" }
    }
    if (-not $reply) { throw "no OK/ERR reply to '$cmd'" }
    Write-Host ("   {0,-7}-> {1}" -f $cmd, $reply)
    if ($cmd -eq 'ping' -and $reply -notlike 'OK lens_z*') { throw "unexpected reply to ping: $reply" }
    if ($cmd -eq 'status' -and $reply -match ' sw ([01])([01])([01])([01]) ') {
      Write-Host ("   switches: Z1 MIN {0}  Z1 MAX {1}  Z2 MIN {2}  Z2 MAX {3}   (1 = tripped, or unplugged)" -f $Matches[1], $Matches[2], $Matches[3], $Matches[4])
    }
    if ($cmd -eq 'status' -and $reply -match ' axes 1$') {
      Write-Host "   one-motor build (Z2_FITTED 0): Z2 and its switches are ignored"
    }
  }
} finally {
  $sp.Close()
}
Write-Host "== done: lens_z is on the Mega and answering"
