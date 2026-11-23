# Shared setup for the AiPi-CAM-D200 helper scripts. Dot-source it: . "$PSScriptRoot\env.ps1"
# Layout assumed: <SDK root>\scripts\env.ps1 and <SDK root>\AiPi-CAM-D200\
$ErrorActionPreference = 'Stop'

$Repo = Split-Path -Parent $PSScriptRoot
$App  = Join-Path $Repo 'AiPi-CAM-D200'

if (-not (Test-Path $App)) {
    throw "Cannot find $App. Put the 'scripts' folder inside the SDK root (the folder that contains AiPi-CAM-D200)."
}
if ($Repo -match '[^\x00-\x7F]|\s') {
    throw "The SDK path '$Repo' contains a space or non-English characters, which breaks the build. Rename the folders so the whole path uses only English letters, numbers, - and _ (for example D:\Projects\G12_Capstone\firmware)."
}

# Same PATH the README uses for the bundled toolchain, make, cmake and ninja.
$env:Path = @(
    "$Repo\toolchain_gcc_t-head_windows\bin",
    "$Repo\bouffalo_sdk\tools\make",
    "$Repo\bouffalo_sdk\tools\cmake\bin",
    "$Repo\bouffalo_sdk\tools\ninja",
    $env:Path
) -join ';'
