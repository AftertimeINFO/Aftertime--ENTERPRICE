<#
  Frames of a Hyper-V virtual machine screen, taken from the host (read-only for the VM).

  Uses Msvm_VirtualSystemManagementService.GetVirtualSystemThumbnailImage: nothing is installed
  in the VM and no session is opened, so tests running in its console are not disturbed.
  Prints one JSON line per frame: {"t": "<ISO time>", "w": 1024, "h": 768, "jpeg": "<base64>"}.

    pwsh -NoProfile -File hyperv-screen.ps1 -VmName "My VM" [-IntervalMs 3000] [-Width 1024] [-Quality 70] [-Once]
#>
param(
    [Parameter(Mandatory)][string]$VmName,
    [int]$IntervalMs = 3000,
    [int]$Width = 1024,
    [int]$Quality = 70,
    [switch]$Once
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$ns = 'root\virtualization\v2'
$service = Get-CimInstance -Namespace $ns -ClassName Msvm_VirtualSystemManagementService
$jpegCodec = [Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object { $_.MimeType -eq 'image/jpeg' }
$encoderParams = New-Object Drawing.Imaging.EncoderParameters 1
$encoderParams.Param[0] = New-Object Drawing.Imaging.EncoderParameter ([Drawing.Imaging.Encoder]::Quality), ([long]$Quality)

function Get-Frame {
    $vm = Get-CimInstance -Namespace $ns -ClassName Msvm_ComputerSystem -Filter "ElementName='$VmName'"
    if (-not $vm) { throw "VM '$VmName' not found" }
    $settings = Get-CimAssociatedInstance -InputObject $vm -ResultClassName Msvm_VirtualSystemSettingData |
        Where-Object { $_.VirtualSystemType -eq 'Microsoft:Hyper-V:System:Realized' } | Select-Object -First 1
    $head = Get-CimInstance -Namespace $ns -ClassName Msvm_VideoHead | Where-Object { $_.SystemName -eq $vm.Name } | Select-Object -First 1
    $w = [Math]::Min($Width, [int]$head.CurrentHorizontalResolution)
    $h = [int]($w * $head.CurrentVerticalResolution / $head.CurrentHorizontalResolution)
    $result = Invoke-CimMethod -InputObject $service -MethodName GetVirtualSystemThumbnailImage -Arguments @{
        TargetSystem = $settings; WidthPixels = [uint16]$w; HeightPixels = [uint16]$h }
    if ($result.ReturnValue -ne 0 -or -not $result.ImageData) { throw "thumbnail failed: $($result.ReturnValue)" }
    # The thumbnail is raw RGB565, top-down, w*h*2 bytes.
    $bitmap = New-Object Drawing.Bitmap $w, $h, ([Drawing.Imaging.PixelFormat]::Format16bppRgb565)
    $data = $bitmap.LockBits((New-Object Drawing.Rectangle 0, 0, $w, $h), [Drawing.Imaging.ImageLockMode]::WriteOnly, $bitmap.PixelFormat)
    for ($row = 0; $row -lt $h; $row++) {
        [Runtime.InteropServices.Marshal]::Copy($result.ImageData, $row * $w * 2, [IntPtr]($data.Scan0.ToInt64() + $row * $data.Stride), $w * 2)
    }
    $bitmap.UnlockBits($data)
    $stream = New-Object IO.MemoryStream
    $bitmap.Save($stream, $jpegCodec, $encoderParams)
    $bitmap.Dispose()
    [pscustomobject]@{ t = (Get-Date).ToUniversalTime().ToString('o'); w = $w; h = $h; jpeg = [Convert]::ToBase64String($stream.ToArray()) }
}

while ($true) {
    try {
        Get-Frame | ConvertTo-Json -Compress
    } catch {
        [pscustomobject]@{ t = (Get-Date).ToUniversalTime().ToString('o'); error = $_.Exception.Message } | ConvertTo-Json -Compress
    }
    [Console]::Out.Flush()
    if ($Once) { break }
    Start-Sleep -Milliseconds $IntervalMs
}
