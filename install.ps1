$ErrorActionPreference = "Stop"

$Repo = if ($env:OPENSRE_INSTALL_REPO) { $env:OPENSRE_INSTALL_REPO } else { "Tracer-Cloud/opensre" }
$InstallDir = if ($env:OPENSRE_INSTALL_DIR) { $env:OPENSRE_INSTALL_DIR } else { Join-Path $HOME ".local\bin" }
$BinaryName = "opensre.exe"

$arch = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture
switch ($arch) {
    "X64" { $TargetArch = "x64" }
    "Arm64" { $TargetArch = "arm64" }
    default { throw "Unsupported Windows architecture: $arch" }
}

$version = $env:OPENSRE_VERSION
if (-not $version) {
    $release = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/latest"
    $version = $release.tag_name.TrimStart("v")
}

if (-not $version) {
    throw "Failed to determine the latest release version."
}

$archive = "opensre_${version}_windows-$TargetArch.zip"
$downloadUrl = "https://github.com/$Repo/releases/download/v$version/$archive"
$tmpDir = Join-Path ([System.IO.Path]::GetTempPath()) ("opensre-install-" + [System.Guid]::NewGuid().ToString("N"))

New-Item -ItemType Directory -Path $tmpDir | Out-Null
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

try {
    $archivePath = Join-Path $tmpDir $archive
    Write-Host "Downloading $downloadUrl"
    Invoke-WebRequest -Uri $downloadUrl -OutFile $archivePath
    Expand-Archive -Path $archivePath -DestinationPath $tmpDir -Force

    $binaryPath = Join-Path $tmpDir $BinaryName
    Copy-Item -Path $binaryPath -Destination (Join-Path $InstallDir $BinaryName) -Force
}
finally {
    Remove-Item -Path $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "Installed opensre $version to $(Join-Path $InstallDir $BinaryName)"
if (-not (($env:PATH -split ';') -contains $InstallDir)) {
    Write-Warning "Add $InstallDir to your PATH to run opensre from any terminal."
}
