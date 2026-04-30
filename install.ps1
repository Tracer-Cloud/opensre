param(
    [ValidateSet("release", "main")]
    [string]$Channel = $(if ($env:OPENSRE_INSTALL_CHANNEL) { $env:OPENSRE_INSTALL_CHANNEL } else { "release" }),
    [switch]$SkipMain
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-OpenSreDefaultInstallDir {
    $userHome = if ($HOME) { $HOME } else { [System.Environment]::GetFolderPath("UserProfile") }
    return Join-Path $userHome ".local\bin"
}

function Get-OpenSreRequestHeaders {
    return @{
        "Accept" = "application/vnd.github+json"
        "User-Agent" = "opensre-install-script"
    }
}

function Invoke-OpenSreWithRetry {
    param(
        [Parameter(Mandatory = $true)]
        [scriptblock]$Operation,
        [Parameter(Mandatory = $true)]
        [string]$Description,
        [int]$MaxAttempts = 3
    )

    $attempt = 1

    while ($true) {
        try {
            return & $Operation
        }
        catch {
            $statusCode = Get-OpenSreHttpStatusCodeFromError -ErrorRecord $_
            if ($null -ne $statusCode -and $statusCode -ge 400 -and $statusCode -lt 500) {
                throw "Failed to $Description. $($_.Exception.Message)"
            }

            if ($attempt -ge $MaxAttempts) {
                throw "Failed to $Description after $attempt attempts. $($_.Exception.Message)"
            }

            Write-Warning "Attempt $attempt to $Description failed: $($_.Exception.Message). Retrying..."
            Start-Sleep -Seconds $attempt
            $attempt += 1
        }
    }
}

function Get-OpenSreHttpStatusCodeFromError {
    param(
        [Parameter(Mandatory = $true)]
        [System.Management.Automation.ErrorRecord]$ErrorRecord
    )

    $exception = $ErrorRecord.Exception

    while ($null -ne $exception) {
        if ($exception.PSObject.Properties["Response"] -and $null -ne $exception.Response) {
            $response = $exception.Response
            if ($response.PSObject.Properties["StatusCode"] -and $null -ne $response.StatusCode) {
                try {
                    return [int]$response.StatusCode
                }
                catch {
                    return $null
                }
            }
        }

        if ($exception.PSObject.Properties["StatusCode"] -and $null -ne $exception.StatusCode) {
            try {
                return [int]$exception.StatusCode
            }
            catch {
                return $null
            }
        }

        $exception = $exception.InnerException
    }

    return $null
}

function Enable-OpenSreTls {
    try {
        $protocol = [System.Net.ServicePointManager]::SecurityProtocol
        $availableProtocols = [System.Enum]::GetNames([System.Net.SecurityProtocolType])

        if ($availableProtocols -contains "Tls12") {
            $protocol = $protocol -bor [System.Net.SecurityProtocolType]::Tls12
        }

        if ($availableProtocols -contains "Tls13") {
            $protocol = $protocol -bor [System.Net.SecurityProtocolType]::Tls13
        }

        [System.Net.ServicePointManager]::SecurityProtocol = $protocol
    }
    catch {
        # Best-effort compatibility tweak for older Windows PowerShell runtimes.
    }
}

function Invoke-OpenSreRestMethod {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Uri
    )

    $params = @{
        Uri = $Uri
        Headers = Get-OpenSreRequestHeaders
    }

    $command = Get-Command Invoke-RestMethod -ErrorAction Stop
    if ($command.Parameters.ContainsKey("UseBasicParsing")) {
        $params.UseBasicParsing = $true
    }

    return Invoke-OpenSreWithRetry -Description "fetch release metadata from GitHub" -Operation {
        Invoke-RestMethod @params
    }
}

function Invoke-OpenSreWebRequest {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Uri,
        [Parameter(Mandatory = $true)]
        [string]$OutFile
    )

    $params = @{
        Uri = $Uri
        Headers = Get-OpenSreRequestHeaders
        OutFile = $OutFile
    }

    $command = Get-Command Invoke-WebRequest -ErrorAction Stop
    if ($command.Parameters.ContainsKey("UseBasicParsing")) {
        $params.UseBasicParsing = $true
    }

    Invoke-OpenSreWithRetry -Description "download '$Uri'" -Operation {
        Invoke-WebRequest @params | Out-Null
    } | Out-Null
}

function Get-OpenSreRuntimeArchitecture {
    try {
        $runtimeInformation = [System.Runtime.InteropServices.RuntimeInformation]
        return [string]$runtimeInformation::OSArchitecture
    }
    catch {
        return ""
    }
}

function Resolve-OpenSreWindowsArchitecture {
    param(
        [string]$RuntimeArchitecture = (Get-OpenSreRuntimeArchitecture),
        [string]$ProcessorArchitectureW6432 = $env:PROCESSOR_ARCHITEW6432,
        [string]$ProcessorArchitecture = $env:PROCESSOR_ARCHITECTURE,
        [bool]$Is64BitOperatingSystem = [System.Environment]::Is64BitOperatingSystem
    )

    $candidates = @(
        $RuntimeArchitecture,
        $ProcessorArchitectureW6432,
        $ProcessorArchitecture
    ) | Where-Object { $_ -and $_.Trim() }

    foreach ($candidate in $candidates) {
        $normalized = $candidate.Trim().ToUpperInvariant()

        switch ($normalized) {
            { $_ -in @("X64", "AMD64", "X86_64") } { return "x64" }
            { $_ -in @("ARM64", "AARCH64") } { return "arm64" }
            { $_ -in @("X86", "I386", "I686") } {
                throw "Unsupported Windows architecture: $candidate. OpenSRE releases are available only for x64 and arm64."
            }
        }
    }

    if ($Is64BitOperatingSystem) {
        return "x64"
    }

    throw "Unsupported Windows architecture. Could not detect a supported architecture from RuntimeInformation, PROCESSOR_ARCHITEW6432, or PROCESSOR_ARCHITECTURE."
}

function Get-OpenSreArchiveName {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Version,
        [Parameter(Mandatory = $true)]
        [ValidateSet("release", "main")]
        [string]$Channel,
        [Parameter(Mandatory = $true)]
        [string]$TargetArch
    )

    $archiveVersion = if ($Channel -eq "main") { "main" } else { $Version }
    return "opensre_${archiveVersion}_windows-$TargetArch.zip"
}

function Get-OpenSreReleaseMetadata {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Repo,
        [ValidateSet("release", "main")]
        [string]$Channel = "release",
        [string]$RequestedVersion = $env:OPENSRE_VERSION
    )

    $normalizedVersion = ""
    if ($RequestedVersion) {
        $normalizedVersion = $RequestedVersion.Trim().TrimStart("v")
    }

    if ($Channel -eq "main" -and $normalizedVersion) {
        throw "OPENSRE_VERSION cannot be combined with the main install channel."
    }

    if ($Channel -eq "main") {
        Write-Host "Fetching latest main build metadata..."
    }
    elseif (-not $normalizedVersion) {
        Write-Host "Fetching latest release version..."
    }

    $releaseUri = if ($Channel -eq "main") {
        "https://api.github.com/repos/$Repo/releases/tags/main"
    }
    elseif ($normalizedVersion) {
        "https://api.github.com/repos/$Repo/releases/tags/v$normalizedVersion"
    }
    else {
        "https://api.github.com/repos/$Repo/releases/latest"
    }

    try {
        $release = Invoke-OpenSreRestMethod -Uri $releaseUri
    }
    catch {
        if ($Channel -eq "main") {
            throw "Failed to fetch main build metadata from GitHub for '$Repo'. $($_.Exception.Message)"
        }

        if ($normalizedVersion) {
            throw "Failed to fetch release metadata for version '$normalizedVersion' from GitHub repo '$Repo'. $($_.Exception.Message)"
        }

        throw "Failed to fetch latest release metadata from GitHub for '$Repo'. $($_.Exception.Message)"
    }

    $version = if ($Channel -eq "main") { "main" } else { [string]$release.tag_name }
    if ($Channel -ne "main" -and $version) {
        $version = $version.Trim().TrimStart("v")
    }

    if (-not $version) {
        if ($Channel -eq "main") {
            throw "Failed to determine the main build tag."
        }

        throw "Failed to determine the latest release version."
    }

    return [pscustomobject]@{
        Release = $release
        Version = $version
    }
}

function Get-OpenSreReleaseAsset {
    param(
        [Parameter(Mandatory = $true)]
        $Release,
        [Parameter(Mandatory = $true)]
        [string]$AssetName
    )

    foreach ($asset in @($Release.assets)) {
        if ([string]$asset.name -eq $AssetName) {
            return $asset
        }
    }

    return $null
}

function Resolve-OpenSreArchiveDownload {
    param(
        [Parameter(Mandatory = $true)]
        $Release,
        [Parameter(Mandatory = $true)]
        [string]$Version,
        [Parameter(Mandatory = $true)]
        [ValidateSet("release", "main")]
        [string]$Channel,
        [Parameter(Mandatory = $true)]
        [string]$TargetArch
    )

    $resolvedArch = $TargetArch
    $archiveName = Get-OpenSreArchiveName -Version $Version -Channel $Channel -TargetArch $resolvedArch
    $archiveAsset = Get-OpenSreReleaseAsset -Release $Release -AssetName $archiveName

    if (-not $archiveAsset -and $TargetArch -eq "arm64") {
        $fallbackArchiveName = Get-OpenSreArchiveName -Version $Version -Channel $Channel -TargetArch "x64"
        $fallbackAsset = Get-OpenSreReleaseAsset -Release $Release -AssetName $fallbackArchiveName

        if ($fallbackAsset) {
            $resolvedArch = "x64"
            $archiveName = $fallbackArchiveName
            $archiveAsset = $fallbackAsset
            if ($Channel -eq "main") {
                Write-Warning "Windows ARM64 artifact is not published for the main build; falling back to the x64 build."
            }
            else {
                Write-Warning "Windows ARM64 artifact is not published for v$Version; falling back to the x64 build."
            }
        }
    }

    if (-not $archiveAsset) {
        $availableAssets = @($Release.assets | ForEach-Object { [string]$_.name } | Where-Object { $_ }) -join ", "
        if ($availableAssets) {
            if ($Channel -eq "main") {
                throw "Main build release does not include asset '$archiveName'. Available assets: $availableAssets"
            }

            throw "Release v$Version does not include asset '$archiveName'. Available assets: $availableAssets"
        }

        if ($Channel -eq "main") {
            throw "Main build release does not include asset '$archiveName'."
        }

        throw "Release v$Version does not include asset '$archiveName'."
    }

    $checksumAsset = Get-OpenSreReleaseAsset -Release $Release -AssetName "$archiveName.sha256"

    return [pscustomobject]@{
        ArchiveName = $archiveName
        ArchiveUrl = [string]$archiveAsset.browser_download_url
        ChecksumName = if ($checksumAsset) { [string]$checksumAsset.name } else { "" }
        ChecksumUrl = if ($checksumAsset) { [string]$checksumAsset.browser_download_url } else { "" }
        ResolvedArch = $resolvedArch
    }
}

function Get-OpenSreExpectedSha256 {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ChecksumPath,
        [Parameter(Mandatory = $true)]
        [string]$ArchiveName
    )

    foreach ($line in Get-Content -LiteralPath $ChecksumPath) {
        if (-not $line.Trim()) {
            continue
        }

        $match = [System.Text.RegularExpressions.Regex]::Match(
            $line,
            '^(?<hash>[A-Fa-f0-9]{64})\s+\*?(?<name>.+)$'
        )

        if (-not $match.Success) {
            continue
        }

        $name = [System.IO.Path]::GetFileName($match.Groups["name"].Value.Trim())
        if ($name -eq $ArchiveName) {
            return $match.Groups["hash"].Value.ToLowerInvariant()
        }
    }

    throw "Checksum file '$ChecksumPath' does not contain a SHA256 entry for '$ArchiveName'."
}

function Normalize-OpenSrePath {
    param(
        [string]$PathValue
    )

    if (-not $PathValue) {
        return ""
    }

    $trimmedPath = $PathValue.Trim().TrimEnd("\", "/")
    if (-not $trimmedPath) {
        return ""
    }

    try {
        return [System.IO.Path]::GetFullPath($trimmedPath).TrimEnd("\", "/")
    }
    catch {
        return $trimmedPath
    }
}

function Test-OpenSreDirectoryOnPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Directory,
        [string]$PathValue = $env:PATH
    )

    if (-not $PathValue) {
        return $false
    }

    $normalizedDirectory = Normalize-OpenSrePath -PathValue $Directory

    foreach ($entry in $PathValue -split ";") {
        if (-not $entry) {
            continue
        }

        if ([string]::Equals(
                $normalizedDirectory,
                (Normalize-OpenSrePath -PathValue $entry),
                [System.StringComparison]::OrdinalIgnoreCase
            )) {
            return $true
        }
    }

    return $false
}

function Get-OpenSreBinaryPathFromArchive {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ExtractionRoot,
        [Parameter(Mandatory = $true)]
        [string]$BinaryName
    )

    $directBinaryPath = Join-Path $ExtractionRoot $BinaryName
    if (Test-Path -LiteralPath $directBinaryPath -PathType Leaf) {
        return $directBinaryPath
    }

    $binaryCandidates = @(Get-ChildItem -Path $ExtractionRoot -Recurse -File -Filter $BinaryName)

    if ($binaryCandidates.Count -eq 1) {
        return $binaryCandidates[0].FullName
    }

    if ($binaryCandidates.Count -gt 1) {
        $locations = $binaryCandidates | ForEach-Object { $_.FullName }
        throw "Found multiple '$BinaryName' files after extraction: $($locations -join ', ')"
    }

    throw "Archive did not contain '$BinaryName'."
}

function Get-OpenSreBinaryVersionInfo {
    param(
        [Parameter(Mandatory = $true)]
        [string]$BinaryPath
    )

    try {
        $versionOutput = & $BinaryPath --version 2>&1
    }
    catch {
        throw "Failed to execute '$BinaryPath --version'. $($_.Exception.Message)"
    }

    $versionText = ($versionOutput | Out-String).Trim()
    $detectedVersion = ""
    $match = [System.Text.RegularExpressions.Regex]::Match($versionText, '\d{4}\.\d{1,2}\.\d{1,2}')
    if ($match.Success) {
        $detectedVersion = $match.Value
    }

    return [pscustomobject]@{
        Text = $versionText
        Version = $detectedVersion
    }
}

function Install-OpenSre {
    $repo = if ($env:OPENSRE_INSTALL_REPO) { $env:OPENSRE_INSTALL_REPO } else { "Tracer-Cloud/opensre" }
    $installDir = if ($env:OPENSRE_INSTALL_DIR) { $env:OPENSRE_INSTALL_DIR } else { Get-OpenSreDefaultInstallDir }
    $binaryName = "opensre.exe"
    $requestedVersion = if ($env:OPENSRE_VERSION) { $env:OPENSRE_VERSION.Trim().TrimStart("v") } else { "" }
    $resolvedChannel = if ($Channel) { $Channel.Trim().ToLowerInvariant() } else { "release" }

    Enable-OpenSreTls

    $targetArch = Resolve-OpenSreWindowsArchitecture
    $releaseMetadata = Get-OpenSreReleaseMetadata -Repo $repo -Channel $resolvedChannel -RequestedVersion $requestedVersion
    $version = [string]$releaseMetadata.Version
    $downloadPlan = Resolve-OpenSreArchiveDownload -Release $releaseMetadata.Release -Version $version -Channel $resolvedChannel -TargetArch $targetArch
    $archive = [string]$downloadPlan.ArchiveName
    $downloadUrl = [string]$downloadPlan.ArchiveUrl
    $checksumUrl = [string]$downloadPlan.ChecksumUrl
    $resolvedArch = [string]$downloadPlan.ResolvedArch
    $tmpDir = Join-Path ([System.IO.Path]::GetTempPath()) ("opensre-install-" + [System.Guid]::NewGuid().ToString("N"))

    New-Item -ItemType Directory -Path $tmpDir | Out-Null
    New-Item -ItemType Directory -Force -Path $installDir | Out-Null

    try {
        $archivePath = Join-Path $tmpDir $archive
        $checksumPath = "$archivePath.sha256"

        if ($resolvedChannel -eq "main") {
            Write-Host "Installing opensre main build (windows/$targetArch)..."
        }
        else {
            Write-Host "Installing opensre v$version (windows/$targetArch)..."
        }
        if ($resolvedArch -ne $targetArch) {
            Write-Host "Using release asset built for windows/$resolvedArch."
        }
        Write-Host "Downloading $downloadUrl"
        Invoke-OpenSreWebRequest -Uri $downloadUrl -OutFile $archivePath

        if ($checksumUrl) {
            Write-Host "Verifying archive checksum"
            Invoke-OpenSreWebRequest -Uri $checksumUrl -OutFile $checksumPath

            $expectedHash = Get-OpenSreExpectedSha256 -ChecksumPath $checksumPath -ArchiveName $archive
            $actualHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()

            if ($actualHash -ne $expectedHash) {
                throw "Checksum verification failed for '$archive'. Expected '$expectedHash' but got '$actualHash'."
            }
        }
        else {
            if ($resolvedChannel -eq "main") {
                Write-Warning "Main build release is missing checksum asset '$archive.sha256'."
            }
            else {
                Write-Warning "Release v$version is missing checksum asset '$archive.sha256'."
            }
        }

        Expand-Archive -LiteralPath $archivePath -DestinationPath $tmpDir -Force

        $binaryPath = Get-OpenSreBinaryPathFromArchive -ExtractionRoot $tmpDir -BinaryName $binaryName
        $binaryVersionInfo = Get-OpenSreBinaryVersionInfo -BinaryPath $binaryPath
        $binaryVersionText = [string]$binaryVersionInfo.Text
        $binaryVersion = [string]$binaryVersionInfo.Version

        if ($resolvedChannel -ne "main" -and $binaryVersionText -notmatch [Regex]::Escape($version)) {
            if ($requestedVersion) {
                throw "Downloaded binary version mismatch. Expected '$version' but got '$binaryVersionText'."
            }

            if (-not $binaryVersion) {
                throw "Downloaded binary version mismatch. Expected '$version' but got '$binaryVersionText'."
            }

            Write-Warning "Latest release metadata reports v$version, but the downloaded binary reports v$binaryVersion. Installing the verified binary anyway."
            $version = $binaryVersion
        }

        Copy-Item -LiteralPath $binaryPath -Destination (Join-Path $installDir $binaryName) -Force
    }
    finally {
        Remove-Item -LiteralPath $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
    }

    $installedBinaryPath = Join-Path $installDir $binaryName
    if ($resolvedChannel -eq "main") {
        if ($binaryVersion) {
            Write-Host "Installed opensre main build ($binaryVersion) to $installedBinaryPath"
        }
        else {
            Write-Host "Installed opensre main build to $installedBinaryPath"
        }
    }
    else {
        Write-Host "Installed opensre $version to $installedBinaryPath"
    }

    if (-not (Test-OpenSreDirectoryOnPath -Directory $installDir)) {
        Write-Warning "Add $installDir to your PATH to run opensre from any terminal."
    }
}

if (-not $SkipMain) {
    Install-OpenSre
}
