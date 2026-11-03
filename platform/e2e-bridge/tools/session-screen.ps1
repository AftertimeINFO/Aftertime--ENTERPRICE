<#
  Screen of an interactive Windows session: a continuous recording and a live frame.

  Runs INSIDE the machine under test, in the session where the tests work (for example from a
  scheduled task with an interactive principal). It only reads the screen: no input, no windows.

  - OutDir\seq\<ms>.jpg   — every IntervalMs a frame; <ms> is the time since the recording started
                            (a monotonic stopwatch, not the machine clock, which tests may shift);
  - OutDir\latest.jpg     — the newest frame, replaced atomically, for live viewing.
  Stops when OutDir\stop exists or after MaxMinutes.

    powershell -NoProfile -File session-screen.ps1 -OutDir C:\Stage\screen [-IntervalMs 1000] [-Quality 60] [-Width 1366]
#>
param(
    [Parameter(Mandatory)][string]$OutDir,
    [int]$IntervalMs = 1000,
    [int]$Quality = 60,
    [int]$Width = 1366,
    [int]$MaxMinutes = 180
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing, System.Windows.Forms
$sequence = Join-Path $OutDir 'seq'
New-Item -ItemType Directory -Force $sequence | Out-Null
$stop = Join-Path $OutDir 'stop'
$latest = Join-Path $OutDir 'latest.jpg'
$temp = Join-Path $OutDir 'latest.tmp'
$codec = [Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object { $_.MimeType -eq 'image/jpeg' }
$params = New-Object Drawing.Imaging.EncoderParameters 1
$params.Param[0] = New-Object Drawing.Imaging.EncoderParameter ([Drawing.Imaging.Encoder]::Quality), ([long]$Quality)
$clock = [Diagnostics.Stopwatch]::StartNew()
$deadline = [TimeSpan]::FromMinutes($MaxMinutes)

while (-not (Test-Path $stop) -and $clock.Elapsed -lt $deadline) {
    $tick = $clock.ElapsedMilliseconds
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
        Copy-Item -Force $temp (Join-Path $sequence ('{0:D9}.jpg' -f $tick))
        Move-Item -Force $temp $latest
    } catch {
        # A locked or switching desktop has no screen to copy; try again on the next tick.
    }
    $wait = $IntervalMs - ($clock.ElapsedMilliseconds - $tick)
    if ($wait -gt 0) { Start-Sleep -Milliseconds $wait }
}
Remove-Item -Force -ErrorAction SilentlyContinue $stop
