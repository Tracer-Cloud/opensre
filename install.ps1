param(
    [ValidateSet("release", "main")]
    [string]$Channel = $(if ($env:OPENSRE_INSTALL_CHANNEL) { $env:OPENSRE_INSTALL_CHANNEL } else { "main" }),
    [switch]$SkipMain
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$script:OpenSreProgressStep = 0
$script:OpenSreChannelExplicit = $PSBoundParameters.ContainsKey("Channel") -or [bool]$env:OPENSRE_INSTALL_CHANNEL

function Test-OpenSreVerboseInstall {
    $value = [string]$env:OPENSRE_INSTALL_VERBOSE
    return ($value -eq "1" -or $value -eq "true" -or $value -eq "TRUE" -or $value -eq "yes" -or $value -eq "YES")
}

function Test-OpenSreInteractiveHost {
    try {
        if ([System.Console]::IsOutputRedirected) {
            return $false
        }
    }
    catch {
        if ($null -eq $Host -or $null -eq $Host.UI) {
            return $false
        }
    }

    try {
        if ($null -eq $Host -or $null -eq $Host.UI -or $null -eq $Host.UI.RawUI) {
            return $false
        }

        $null = $Host.UI.RawUI.WindowSize
    }
    catch {
        return $false
    }

    return $true
}

function Get-OpenSreConsoleWidth {
    [int]$width = 0

    try {
        if ($null -ne $Host -and $null -ne $Host.UI -and $null -ne $Host.UI.RawUI) {
            $hostWidth = [int]$Host.UI.RawUI.WindowSize.Width
            if ($hostWidth -gt 0) {
                $width = $hostWidth
            }
        }
    }
    catch {
        $width = 0
    }

    if ($width -le 0) {
        try {
            $consoleWidth = [int][System.Console]::WindowWidth
            if ($consoleWidth -gt 0) {
                $width = $consoleWidth
            }
        }
        catch {
            $width = 0
        }
    }

    if ($width -lt 20) {
        $width = 80
    }

    return $width
}

function Limit-OpenSreText {
    param(
        [AllowEmptyString()]
        [string]$Text,
        [int]$MaxWidth
    )

    $value = [string]$Text
    $value = $value.Replace("`r", " ").Replace("`n", " ")

    if ($MaxWidth -le 0) {
        return ""
    }

    if ($value.Length -le $MaxWidth) {
        return $value
    }

    if ($MaxWidth -le 3) {
        return $value.Substring(0, $MaxWidth)
    }

    return ($value.Substring(0, $MaxWidth - 3) + "...")
}

function Get-OpenSreFriendlyProgressLabel {
    param(
        [AllowEmptyString()]
        [string]$Label
    )

    if ($Label -like "*Fetching latest main build metadata*" -or
        $Label -like "*Fetching latest release version*" -or
        $Label -like "*Fetching release metadata*") {
        return "fetching metadata"
    }

    # More specific than ``*Preparing opensre*`` below (install warm-up step).
    if ($Label -like "*first launch*") {
        return "preparing first launch"
    }

    if ($Label -like "*Preparing opensre*") {
        return "resolving build"
    }

    if ($Label -like "*Downloading release archive*" -or
        $Label -like "*.zip" -or
        $Label -like "*.tar.gz") {
        return "downloading archive"
    }

    if ($Label -like "*Downloading and verifying checksum*" -or
        $Label -like "*Verifying release archive*" -or
        $Label -like "*.sha256") {
        return "verifying checksum"
    }

    if ($Label -like "*Extracting and verifying binary*") {
        return "verifying binary"
    }

    if ($Label -like "*Installing*binary*" -or
        $Label -like "*Installing*opensre*") {
        return "installing binary"
    }

    return ([System.Text.RegularExpressions.Regex]::Replace([string]$Label, '^\[[0-9]+/[0-9]+\]\s*', ""))
}

function Get-OpenSreProgressFrame {
    param(
        [int]$Step
    )

    $frames = @("-", "\", "|", "/")
    return $frames[$Step % $frames.Count]
}

function New-OpenSreProgressBar {
    param(
        [int]$Step,
        [int]$Width
    )

    if ($Width -lt 1) {
        return ""
    }

    [int]$trail = 8
    [int]$head = $Step % ($Width + $trail)
    $builder = New-Object System.Text.StringBuilder

    for ($i = 0; $i -lt $Width; $i += 1) {
        $age = $head - $i
        if ($age -ge 0 -and $age -lt $trail) {
            if ($age -eq 0 -or $age -eq 1) {
                [void]$builder.Append("#")
            }
            elseif ($age -eq 2 -or $age -eq 3) {
                [void]$builder.Append("=")
            }
            elseif ($age -eq 4 -or $age -eq 5) {
                [void]$builder.Append("+")
            }
            else {
                [void]$builder.Append("-")
            }
        }
        else {
            [void]$builder.Append(".")
        }
    }

    return $builder.ToString()
}

function Write-OpenSreLine {
    param(
        [AllowEmptyString()]
        [string]$Message,
        [string]$Color = ""
    )

    if ((Test-OpenSreInteractiveHost) -and $Color) {
        Write-Host $Message -ForegroundColor $Color
        return
    }

    Write-Host $Message
}

function Write-OpenSreDetail {
    param(
        [AllowEmptyString()]
        [string]$Message
    )

    if (-not $Message) {
        return
    }

    Write-OpenSreLine -Message "  $Message" -Color "DarkGray"
}

function Write-OpenSreHeader {
    param(
        [string]$Channel = "",
        [string]$RequestedVersion = "",
        [string]$InstallDir = "",
        [string]$Repo = ""
    )

    Write-OpenSreLine -Message "OpenSRE installer" -Color "Cyan"
    Write-OpenSreLine -Message "Installing the OpenSRE CLI for Windows." -Color "DarkGray"

    if (Test-OpenSreVerboseInstall) {
        Write-OpenSreDetail -Message "Verbose logging enabled by OPENSRE_INSTALL_VERBOSE=1."
        if ($Repo) {
            Write-OpenSreDetail -Message "Repository: $Repo"
        }
        if ($Channel) {
            Write-OpenSreDetail -Message "Channel: $Channel"
        }
        if ($RequestedVersion) {
            Write-OpenSreDetail -Message "Requested version: $RequestedVersion"
        }
        if ($InstallDir) {
            Write-OpenSreDetail -Message "Install directory: $InstallDir"
        }
    }
}

function Write-OpenSreProgressLine {
    param(
        [string]$Label,
        [Int64]$DownloadedBytes,
        [Int64]$TotalBytes = -1
    )

    if (-not (Test-OpenSreInteractiveHost) -or (Test-OpenSreVerboseInstall)) {
        return
    }

    $width = Get-OpenSreConsoleWidth
    [int]$clearWidth = $width - 1
    if ($clearWidth -lt 1) {
        $clearWidth = 1
    }

    $title = "Installing OpenSRE"
    if ($width -lt 56) {
        $title = "OpenSRE"
    }

    $percentText = ""
    if ($TotalBytes -gt 0) {
        $percent = [Math]::Min(100, [Math]::Floor(($DownloadedBytes * 100) / $TotalBytes))
        $percentText = " $percent%"
    }

    [int]$reserve = 2 + 1 + 1 + 1 + $title.Length + 1 + $percentText.Length
    [int]$available = $clearWidth - $reserve
    [int]$barWidth = 8
    if ($available -lt 12) {
        $barWidth = 4
    }
    else {
        $barWidth = [Math]::Floor($available / 2)
        if ($barWidth -gt 28) {
            $barWidth = 28
        }
        if ($barWidth -lt 8) {
            $barWidth = 8
        }
    }

    [int]$labelWidth = $clearWidth - $reserve - $barWidth
    if ($labelWidth -lt 8 -and $barWidth -gt 4) {
        $barWidth = $clearWidth - $reserve - 8
        if ($barWidth -lt 4) {
            $barWidth = 4
        }
        $labelWidth = $clearWidth - $reserve - $barWidth
    }
    if ($labelWidth -lt 0) {
        $labelWidth = 0
    }

    $script:OpenSreProgressStep += 1
    $frame = Get-OpenSreProgressFrame -Step $script:OpenSreProgressStep
    $bar = New-OpenSreProgressBar -Step $script:OpenSreProgressStep -Width $barWidth
    $status = Limit-OpenSreText -Text (Get-OpenSreFriendlyProgressLabel -Label $Label) -MaxWidth $labelWidth
    $content = "  $frame $bar $title $status$percentText"
    if ($content.Length -gt $clearWidth) {
        $content = $content.Substring(0, $clearWidth)
    }

    # Parenthesize the entire -f expression. Without that, PowerShell treats the
    # comma as a Console.Write argument separator, so -f only receives one value
    # and "{1}" raises: "Error formatting a string: Index ... argument list."
    [System.Console]::Write(("`r{0}`r{1}" -f (" " * $clearWidth), $content))
}

function Clear-OpenSreProgressLine {
    if (-not (Test-OpenSreInteractiveHost) -or (Test-OpenSreVerboseInstall)) {
        return
    }

    $width = Get-OpenSreConsoleWidth
    [int]$clearWidth = $width - 1
    if ($clearWidth -lt 1) {
        $clearWidth = 1
    }

    [System.Console]::Write("`r{0}`r" -f (" " * $clearWidth))
}

function Invoke-OpenSreStep {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [scriptblock]$Operation,
        [string]$Detail = ""
    )

    Write-OpenSreLine -Message $Name -Color "Cyan"
    Write-OpenSreDetail -Message $Detail

    if ($Operation) {
        try {
            $result = & $Operation
            Write-OpenSreLine -Message "  OK $Name" -Color "Green"
            return $result
        }
        catch {
            Write-OpenSreLine -Message "  FAILED $Name" -Color "Red"
            throw
        }
    }
}

function Invoke-OpenSreStreamDownload {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Uri,
        [Parameter(Mandatory = $true)]
        [string]$OutFile,
        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    $request = [System.Net.HttpWebRequest]::Create($Uri)
    $headers = Get-OpenSreRequestHeaders
    foreach ($key in $headers.Keys) {
        if ($key -eq "User-Agent") {
            $request.UserAgent = [string]$headers[$key]
        }
        elseif ($key -eq "Accept") {
            $request.Accept = [string]$headers[$key]
        }
        else {
            $request.Headers[$key] = [string]$headers[$key]
        }
    }

    $response = $request.GetResponse()
    try {
        $totalBytes = [Int64]$response.ContentLength
        $inputStream = $response.GetResponseStream()
        $outputStream = [System.IO.File]::Open($OutFile, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write)
        try {
            $buffer = New-Object byte[] 65536
            [Int64]$downloadedBytes = 0

            while ($true) {
                $read = $inputStream.Read($buffer, 0, $buffer.Length)
                if ($read -le 0) {
                    break
                }

                $outputStream.Write($buffer, 0, $read)
                $downloadedBytes += $read
                Write-OpenSreProgressLine -Label $Label -DownloadedBytes $downloadedBytes -TotalBytes $totalBytes
            }
        }
        finally {
            if ($outputStream) {
                $outputStream.Dispose()
            }
            if ($inputStream) {
                $inputStream.Dispose()
            }
            Clear-OpenSreProgressLine
        }
    }
    finally {
        if ($response) {
            $response.Dispose()
        }
    }
}

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

    if (Test-OpenSreVerboseInstall) {
        Write-OpenSreDetail -Message "GET $Uri"
    }

    return Invoke-OpenSreWithRetry -Description "fetch release metadata from GitHub" -Operation {
        Invoke-RestMethod @params
    }
}

function Invoke-OpenSreDownloadFileWithProgress {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Uri,
        [Parameter(Mandatory = $true)]
        [string]$OutFile,
        [string]$Label = ""
    )

    if (-not $Label) {
        $Label = [System.IO.Path]::GetFileName($OutFile)
    }

    if (-not $Label) {
        $Label = "file"
    }

    $params = @{
        Uri = $Uri
        Headers = Get-OpenSreRequestHeaders
        OutFile = $OutFile
    }

    $command = Get-Command Invoke-WebRequest -ErrorAction Stop
    if ($command.Parameters.ContainsKey("UseBasicParsing")) {
        $params.UseBasicParsing = $true
    }

    if (Test-OpenSreVerboseInstall) {
        Write-OpenSreDetail -Message "Download URL: $Uri"
        Write-OpenSreDetail -Message "Destination: $OutFile"
    }
    else {
        Write-OpenSreDetail -Message $Label
    }

    Invoke-OpenSreWithRetry -Description "download '$Uri'" -Operation {
        if ((Test-OpenSreInteractiveHost) -and -not (Test-OpenSreVerboseInstall)) {
            Invoke-OpenSreStreamDownload -Uri $Uri -OutFile $OutFile -Label $Label
        }
        else {
            $previousProgressPreference = $ProgressPreference
            try {
                $ProgressPreference = "SilentlyContinue"
                Invoke-WebRequest @params | Out-Null
            }
            finally {
                $ProgressPreference = $previousProgressPreference
            }
        }
    } | Out-Null
}

function Invoke-OpenSreWebRequest {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Uri,
        [Parameter(Mandatory = $true)]
        [string]$OutFile
    )

    Invoke-OpenSreDownloadFileWithProgress -Uri $Uri -OutFile $OutFile
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

    $mainReleaseTag = if ($env:OPENSRE_MAIN_RELEASE_TAG) { $env:OPENSRE_MAIN_RELEASE_TAG } else { "main-build" }

    $releaseUri = if ($Channel -eq "main") {
        "https://api.github.com/repos/$Repo/releases/tags/$mainReleaseTag"
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

function Invoke-OpenSreFirstLaunchWarmup {
    param(
        [Parameter(Mandatory = $true)]
        [string]$BinaryPath
    )

    # Windows Defender / Smart App Control often scan a freshly installed
    # onedir tree on first execution. ``--version`` is a fast path and loads
    # little of the bundle; ``_package-smoke`` imports the tool registry,
    # verifiers and skills so that cost lands under the installer instead of
    # the user's first real ``opensre``. (POSIX ``install.sh`` only warms on
    # Darwin for codesign-cache reasons.) Best-effort: binary already passed
    # ``--version``, so a smoke failure warns rather than aborts the install.
    Write-OpenSreLine -Message "Preparing OpenSRE for first launch" -Color "Cyan"
    try {
        $null = & $BinaryPath _package-smoke 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-OpenSreLine -Message "  OK Preparing OpenSRE for first launch" -Color "Green"
            return
        }
    }
    catch {
        # Fall through to the warning below.
    }
    Write-Warning "First-launch warm-up did not complete; the first opensre may start slowly."
}

function Ensure-OpenSreGithubCli {
    # Soft dependency for github_cli chat tools. Never fails the OpenSRE install.
    if (Get-Command gh -ErrorAction SilentlyContinue) {
        return
    }

    $skip = [string]$env:OPENSRE_SKIP_GH_INSTALL
    if ($skip -eq "1" -or $skip -eq "true" -or $skip -eq "TRUE" -or $skip -eq "yes" -or $skip -eq "YES" -or $skip -eq "on" -or $skip -eq "ON") {
        Write-Warning "GitHub CLI (gh) is not on PATH; skipped install because OPENSRE_SKIP_GH_INSTALL is set."
        Write-Warning "Install manually: winget install --id GitHub.cli  (or https://cli.github.com/)"
        return
    }

    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-OpenSreLine -Message "Installing GitHub CLI (gh) for OpenSRE GitHub tools" -Color "Cyan"
        try {
            winget install --id GitHub.cli --exact --accept-package-agreements --accept-source-agreements
            if ($LASTEXITCODE -eq 0) {
                Write-OpenSreLine -Message "  OK Installed GitHub CLI (gh) via winget" -Color "Green"
                return
            }
        }
        catch {
            # Soft dependency — fall through to the manual hint.
        }
    }

    Write-Warning "Install manually: winget install --id GitHub.cli  (or https://cli.github.com/) for OpenSRE GitHub chat tools."
}

function Test-OpenSreAutoLaunchEnabled {
    $value = [string]$env:OPENSRE_AUTO_LAUNCH
    return -not ($value -eq "0" -or $value -eq "false" -or $value -eq "FALSE" -or $value -eq "no" -or $value -eq "NO" -or $value -eq "off" -or $value -eq "OFF")
}

function Start-OpenSreOnboardingAfterInstall {
    param(
        [string]$BinaryPath,
        [string]$DisplayName
    )

    if (-not (Test-OpenSreAutoLaunchEnabled) -or -not (Test-OpenSreInteractiveHost)) {
        return
    }

    # If stdin is redirected (e.g. the installer is piped), the full-screen
    # onboarding prompt cannot take control of the terminal and exits with a
    # terminal I/O error mid-render (issue #3273). Skip the auto-launch; the
    # "Next steps" output already tells the user to run onboarding themselves.
    try {
        if ([System.Console]::IsInputRedirected) {
            return
        }
    }
    catch {
        return
    }

    if (-not (Test-Path -LiteralPath $BinaryPath -PathType Leaf)) {
        Write-Warning "Could not auto-launch onboarding; $BinaryPath was not found."
        return
    }

    Write-Host "Launching $DisplayName setup..."
    & $BinaryPath setup
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Setup exited before completion. Run '$DisplayName setup' to retry."
    }
}

function Install-OpenSre {
    $repo = if ($env:OPENSRE_INSTALL_REPO) { $env:OPENSRE_INSTALL_REPO } else { "Tracer-Cloud/opensre" }
    $installDir = if ($env:OPENSRE_INSTALL_DIR) { $env:OPENSRE_INSTALL_DIR } else { Get-OpenSreDefaultInstallDir }
    $binaryName = "opensre.exe"
    $requestedVersion = if ($env:OPENSRE_VERSION) { $env:OPENSRE_VERSION.Trim().TrimStart("v") } else { "" }
    $resolvedChannel = if ($Channel) { $Channel.Trim().ToLowerInvariant() } else { "release" }
    $channelExplicit = [bool]$script:OpenSreChannelExplicit

    if ($requestedVersion -and $resolvedChannel -eq "main" -and -not $channelExplicit) {
        $resolvedChannel = "release"
    }

    Write-OpenSreHeader -Channel $resolvedChannel -RequestedVersion $requestedVersion -InstallDir $installDir -Repo $repo
    Enable-OpenSreTls

    $targetArch = Resolve-OpenSreWindowsArchitecture
    $metadataStepName = ""
    if ($resolvedChannel -eq "main") {
        $metadataStepName = "[1/6] Fetching latest main build metadata"
    }
    elseif ($requestedVersion) {
        $metadataStepName = "[1/6] Fetching release metadata for v$requestedVersion"
    }
    else {
        $metadataStepName = "[1/6] Fetching latest release version"
    }
    $releaseMetadata = Invoke-OpenSreStep -Name $metadataStepName -Operation {
        Get-OpenSreReleaseMetadata -Repo $repo -Channel $resolvedChannel -RequestedVersion $requestedVersion
    }
    $version = [string]$releaseMetadata.Version

    $assetStepName = if ($resolvedChannel -eq "main") {
        "[2/6] Preparing opensre main build (windows/$targetArch)"
    }
    else {
        "[2/6] Preparing opensre v$version (windows/$targetArch)"
    }
    $downloadPlan = Invoke-OpenSreStep -Name $assetStepName -Operation {
        Resolve-OpenSreArchiveDownload -Release $releaseMetadata.Release -Version $version -Channel $resolvedChannel -TargetArch $targetArch
    }
    $archive = [string]$downloadPlan.ArchiveName
    $downloadUrl = [string]$downloadPlan.ArchiveUrl
    $checksumUrl = [string]$downloadPlan.ChecksumUrl
    $resolvedArch = [string]$downloadPlan.ResolvedArch
    $tmpDir = Join-Path ([System.IO.Path]::GetTempPath()) ("opensre-install-" + [System.Guid]::NewGuid().ToString("N"))

    New-Item -ItemType Directory -Path $tmpDir | Out-Null

    try {
        $archivePath = Join-Path $tmpDir $archive
        $checksumPath = "$archivePath.sha256"

        if ($resolvedArch -ne $targetArch) {
            Write-OpenSreDetail -Message "Using release asset built for windows/$resolvedArch."
        }

        Invoke-OpenSreStep -Name "[3/6] Downloading release archive" -Operation {
            Invoke-OpenSreDownloadFileWithProgress -Uri $downloadUrl -OutFile $archivePath -Label $archive
        }

        if ($checksumUrl) {
            $checksumName = [string]$downloadPlan.ChecksumName
            Invoke-OpenSreStep -Name "[4/6] Downloading and verifying checksum" -Operation {
                Invoke-OpenSreDownloadFileWithProgress -Uri $checksumUrl -OutFile $checksumPath -Label $checksumName

                $expectedHash = Get-OpenSreExpectedSha256 -ChecksumPath $checksumPath -ArchiveName $archive
                $actualHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()

                if ($actualHash -ne $expectedHash) {
                    throw "Checksum verification failed for '$archive'. Expected '$expectedHash' but got '$actualHash'."
                }
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

        $verifiedBinary = Invoke-OpenSreStep -Name "[5/6] Extracting and verifying binary" -Operation {
            Expand-Archive -LiteralPath $archivePath -DestinationPath $tmpDir -Force

            $binaryPath = Get-OpenSreBinaryPathFromArchive -ExtractionRoot $tmpDir -BinaryName $binaryName
            $binaryVersionInfo = Get-OpenSreBinaryVersionInfo -BinaryPath $binaryPath
            $binaryVersionText = [string]$binaryVersionInfo.Text
            $binaryVersion = [string]$binaryVersionInfo.Version
            $installVersion = $version

            if ($resolvedChannel -ne "main" -and $binaryVersionText -notmatch [Regex]::Escape($version)) {
                if ($requestedVersion) {
                    throw "Downloaded binary version mismatch. Expected '$version' but got '$binaryVersionText'."
                }

                if (-not $binaryVersion) {
                    throw "Downloaded binary version mismatch. Expected '$version' but got '$binaryVersionText'."
                }

                Write-Warning "Latest release metadata reports v$version, but the downloaded binary reports v$binaryVersion. Installing the verified binary anyway."
                $installVersion = $binaryVersion
            }

            return [pscustomobject]@{
                Path = $binaryPath
                VersionText = $binaryVersionText
                Version = $binaryVersion
                InstallVersion = $installVersion
            }
        }

        $binaryPath = [string]$verifiedBinary.Path
        $binaryVersionText = [string]$verifiedBinary.VersionText
        $binaryVersion = [string]$verifiedBinary.Version
        $version = [string]$verifiedBinary.InstallVersion

        Invoke-OpenSreFirstLaunchWarmup -BinaryPath $binaryPath

        Invoke-OpenSreStep -Name "[6/6] Installing binary" -Detail (Join-Path $installDir $binaryName) -Operation {
            New-Item -ItemType Directory -Force -Path $installDir | Out-Null
            Copy-Item -LiteralPath $binaryPath -Destination (Join-Path $installDir $binaryName) -Force
        }
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

    Ensure-OpenSreGithubCli

    $exe = $binaryName.TrimEnd(".exe")
    $sep = "────────────────────────────────────────────"

    Write-Host ""
    Write-Host $sep
    if ($resolvedChannel -eq "main") {
        if ($binaryVersion) {
            Write-Host "  opensre main build ($binaryVersion) installed successfully"
        }
        else {
            Write-Host "  opensre main build installed successfully"
        }
    }
    else {
        Write-Host "  opensre v$version installed successfully"
    }
    Write-Host $sep
    Write-Host ""
    Write-Host "Next steps:"
    Write-Host "  1. Run  $exe setup"
    Write-Host "     Sign in or create an OpenSRE account, then open the interactive shell."
    Write-Host ""
    Write-Host "  2. Run  $exe  (no subcommand)"
    Write-Host "     This starts the account gate first, then opens the interactive shell."
    Write-Host ""
    Write-Host "  3. Optional — one-shot RCA from a file:"
    Write-Host "     $exe investigate -i path/to/alert.json"
    Write-Host ""
    Write-Host "Docs: https://www.opensre.com/docs"
    Write-Host ""

    Start-OpenSreOnboardingAfterInstall -BinaryPath $installedBinaryPath -DisplayName $exe
}

if (-not $SkipMain) {
    Install-OpenSre
}
