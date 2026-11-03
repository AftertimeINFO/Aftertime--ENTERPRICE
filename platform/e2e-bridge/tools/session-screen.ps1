<#
  Screen of an interactive Windows session, saved as JPEG every IntervalMs.

  Runs INSIDE the machine under test, in the session where the tests work (for example from a
  scheduled task with an interactive principal). It only reads the screen: no input, no windows.
  Writes OutDir\latest.jpg atomically and stops when OutDir\stop exists or after MaxMinutes.

    powershell -NoProfile -File session-screen.ps1 -OutDir C:\Stage\screen [-IntervalMs 2000] [-Quality 70] [-Width 1366]
#>
param(
    [Parameter(Mandatory)][string]$OutDir,
    [int]$IntervalMs = 2000,
    [int]$Quality = 70,
    [int]$Width = 1366,
    [int]$MaxMinutes = 180
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing, System.Windows.Forms
New-Item -ItemType Directory -Force $OutDir | Out-Null
$stop = Join-Path $OutDir 'stop'
$latest = Join-Path $OutDir 'latest.jpg'
$temp = Join-Path $OutDir 'latest.tmp'
$codec = [Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object { $_.MimeType -eq 'image/jpeg' }
$params = New-Object Drawing.Imaging.EncoderParameters 1
$params.Param[0] = New-Object Drawing.Imaging.EncoderParameter ([Drawing.Imaging.Encoder]::Quality), ([long]$Quality)
$deadline = (Get-Date).AddMinutes($MaxMinutes)

while (-not (Test-Path $stop) -and (Get-Date) -lt $deadline) {
    try {
        $bounds = [Windows.Forms.SystemInformation]::VirtualScreen
        $screen = New-Object Drawing.Bitmap $bounds.Width, $bounds.Height
        $graphics = [Drawing.Graphics]::FromImage($screen)
        $graphics.CopyFromScreen($bounds.Left, $bounds.Top, 0, 0, $screen.Size)
        $graphics.Dispose()
        $w = [Math]::Min($Width, $bounds.Width)
        $h = [int]($w * $bounds.Height / $bounds.Width)
        $frame = if ($w -lt $bounds.Width) { New-Object Drawing.Bitmap $screen, $w, $h } else { $screen }
        $frame.Save($temp, $codec, $params)
        if ($frame -ne $screen) { $frame.Dispose() }
        $screen.Dispose()
        Move-Item -Force $temp $latest
    } catch {
        # A locked or switching desktop has no screen to copy; try again on the next tick.
    }
    Start-Sleep -Milliseconds $IntervalMs
}
Remove-Item -Force -ErrorAction SilentlyContinue $stop
