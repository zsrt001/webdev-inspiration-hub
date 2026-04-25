Add-Type -AssemblyName System.Drawing

$root = Resolve-Path (Join-Path $PSScriptRoot '..')
$sourcePath = Join-Path $root 'frontend/src/static/style-previews/golden_modern_remake.jpg'
$outputPaths = @(
  (Join-Path $root 'frontend/src/static/legacy_promo_banner.jpg'),
  (Join-Path $root 'frontend/static/legacy_promo_banner.jpg')
)

$image = [System.Drawing.Image]::FromFile($sourcePath)
$canvas = New-Object System.Drawing.Bitmap 1400, 900
$graphics = [System.Drawing.Graphics]::FromImage($canvas)

$graphics.SmoothingMode = 'HighQuality'
$graphics.InterpolationMode = 'HighQualityBicubic'
$graphics.PixelOffsetMode = 'HighQuality'
$graphics.Clear([System.Drawing.Color]::FromArgb(250, 242, 246))

$shadowBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(22, 131, 24, 67))
$graphics.FillRectangle($shadowBrush, 435, 70, 540, 760)

$ratio = [Math]::Min(500 / $image.Width, 720 / $image.Height)
$width = [int]($image.Width * $ratio)
$height = [int]($image.Height * $ratio)
$x = 455
$y = [int]((900 - $height) / 2)

$graphics.DrawImage($image, $x, $y, $width, $height)
$borderPen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(40, 131, 24, 67), 2)
$graphics.DrawRectangle($borderPen, $x - 1, $y - 1, $width + 1, $height + 1)

foreach ($outputPath in $outputPaths) {
  $canvas.Save($outputPath, [System.Drawing.Imaging.ImageFormat]::Jpeg)
  Write-Output "Saved $outputPath"
}

$borderPen.Dispose()
$shadowBrush.Dispose()
$graphics.Dispose()
$image.Dispose()
$canvas.Dispose()
