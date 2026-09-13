param(
    [int]$ParentProcessId,
    [string]$CleanupPayload,
    [string]$CleanupScriptPath
)

$ErrorActionPreference = 'Stop'

function Exit-OpenSreCleanup {
    param([int]$ExitCode)

    Remove-Item -LiteralPath $CleanupScriptPath -Force -ErrorAction SilentlyContinue
    exit $ExitCode
}

trap {
    Remove-Item -LiteralPath $CleanupScriptPath -Force -ErrorAction SilentlyContinue
    exit 1
}

if ($PSVersionTable.PSEdition -cne 'Desktop' -or
    [int]$PSVersionTable.PSVersion.Major -ne 5 -or
    [int]$PSVersionTable.PSVersion.Minor -lt 1) {
    Exit-OpenSreCleanup -ExitCode 1
}

$payloadJson = [System.Text.Encoding]::UTF8.GetString(
    [System.Convert]::FromBase64String($CleanupPayload)
)
$payload = ConvertFrom-Json -InputObject $payloadJson
if (-not $payload.operation_id -or
    $null -eq $payload.parent -or
    [int]$payload.parent.pid -ne $ParentProcessId -or
    -not [string]$payload.parent.path -or
    [int64]$payload.parent.started_filetime_utc -le 0) {
    Exit-OpenSreCleanup -ExitCode 1
}

function ConvertTo-OpenSreExtendedPath {
    param([string]$Path)

    $fullPath = [System.IO.Path]::GetFullPath($Path)
    if ($fullPath.StartsWith('\\?\')) {
        return $fullPath
    }
    if ($fullPath.StartsWith('\\')) {
        return '\\?\UNC\' + $fullPath.Substring(2)
    }
    return '\\?\' + $fullPath
}

function ConvertTo-OpenSreComparablePath {
    param([string]$Path)

    $fullPath = [System.IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
    if ($fullPath.StartsWith('\\?\UNC\')) {
        return '\\' + $fullPath.Substring(8)
    }
    if ($fullPath.StartsWith('\\?\')) {
        return $fullPath.Substring(4)
    }
    return $fullPath
}

function Initialize-OpenSreCleanupNativePathApi {
    if (([System.Management.Automation.PSTypeName]'OpenSre.CleanupNativePathApi').Type) {
        return
    }

    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;

namespace OpenSre
{
    public sealed class CleanupPathIdentity
    {
        public string FinalPath { get; private set; }
        public uint VolumeSerialNumber { get; private set; }
        public ulong FileIndex { get; private set; }
        public long CreationFileTimeUtc { get; private set; }

        public CleanupPathIdentity(
            string finalPath,
            uint volumeSerialNumber,
            ulong fileIndex,
            long creationFileTimeUtc
        )
        {
            FinalPath = finalPath;
            VolumeSerialNumber = volumeSerialNumber;
            FileIndex = fileIndex;
            CreationFileTimeUtc = creationFileTimeUtc;
        }
    }

    internal sealed class CleanupDeletionNodeV1 : IDisposable
    {
        internal CleanupDeletionNodeV1(
            SafeFileHandle handle,
            string path,
            bool isDirectory
        )
        {
            Handle = handle;
            Path = path;
            IsDirectory = isDirectory;
            Children = new System.Collections.Generic.List<CleanupDeletionNodeV1>();
        }

        internal SafeFileHandle Handle { get; private set; }
        internal string Path { get; private set; }
        internal bool IsDirectory { get; private set; }
        internal System.Collections.Generic.List<CleanupDeletionNodeV1> Children
        {
            get;
            private set;
        }

        internal void CloseHandle()
        {
            if (Handle != null)
            {
                Handle.Dispose();
                Handle = null;
            }
        }

        public void Dispose()
        {
            foreach (CleanupDeletionNodeV1 child in Children)
            {
                child.Dispose();
            }
            Children.Clear();
            CloseHandle();
        }
    }

    public sealed class CleanupDeletionLeaseV1 : IDisposable
    {
        private readonly CleanupDeletionNodeV1 root;
        private bool treePrepared;
        private bool filesDeleted;
        private bool directoriesDeleted;
        private bool rootDeleted;

        internal CleanupDeletionLeaseV1(CleanupDeletionNodeV1 root)
        {
            this.root = root;
        }

        public void PrepareTree()
        {
            if (!treePrepared)
            {
                CleanupNativePathApi.PrepareOpenedChildren(root);
                treePrepared = true;
            }
        }

        public void DeleteFiles()
        {
            if (!treePrepared)
            {
                throw new InvalidOperationException("The cleanup tree was not prepared.");
            }
            if (!filesDeleted)
            {
                CleanupNativePathApi.DeleteOpenedFiles(root, true);
                filesDeleted = true;
            }
        }

        public void DeleteDirectories()
        {
            if (!filesDeleted)
            {
                throw new InvalidOperationException("The cleanup files were not retired.");
            }
            if (!directoriesDeleted)
            {
                CleanupNativePathApi.DeleteOpenedDirectories(root, true);
                directoriesDeleted = true;
            }
        }

        public void DeleteRoot()
        {
            if (!directoriesDeleted)
            {
                throw new InvalidOperationException("The cleanup directories were not retired.");
            }
            if (!rootDeleted)
            {
                CleanupNativePathApi.DeleteOpenedRoot(root);
                rootDeleted = true;
            }
        }

        public void Dispose()
        {
            root.Dispose();
        }
    }

    public static class CleanupNativePathApi
    {
        private const uint FileShareRead = 0x00000001;
        private const uint FileShareWrite = 0x00000002;
        private const uint FileShareDelete = 0x00000004;
        private const uint GenericRead = 0x80000000;
        private const uint GenericWrite = 0x40000000;
        private const uint DeleteAccess = 0x00010000;
        private const uint OpenExisting = 3;
        private const uint FileFlagBackupSemantics = 0x02000000;
        private const uint FileFlagOpenReparsePoint = 0x00200000;
        private const uint FileAttributeReadOnly = 0x00000001;
        private const uint FileAttributeDirectory = 0x00000010;
        private const uint FileAttributeReparsePoint = 0x00000400;
        private const uint InvalidFileAttributes = 0xFFFFFFFF;
        private const int ErrorFileNotFound = 2;
        private const int ErrorPathNotFound = 3;
        private const int FileDispositionInfoClass = 4;

        [StructLayout(LayoutKind.Sequential)]
        private struct FileDispositionInfo
        {
            [MarshalAs(UnmanagedType.U1)]
            public bool DeleteFile;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct NativeFileTime
        {
            public uint LowDateTime;
            public uint HighDateTime;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct ByHandleFileInformation
        {
            public uint FileAttributes;
            public NativeFileTime CreationTime;
            public NativeFileTime LastAccessTime;
            public NativeFileTime LastWriteTime;
            public uint VolumeSerialNumber;
            public uint FileSizeHigh;
            public uint FileSizeLow;
            public uint NumberOfLinks;
            public uint FileIndexHigh;
            public uint FileIndexLow;
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern SafeFileHandle CreateFile(
            string fileName,
            uint desiredAccess,
            uint shareMode,
            IntPtr securityAttributes,
            uint creationDisposition,
            uint flagsAndAttributes,
            IntPtr templateFile
        );

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern uint GetFinalPathNameByHandle(
            SafeFileHandle file,
            StringBuilder path,
            uint pathLength,
            uint flags
        );

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool GetFileInformationByHandle(
            SafeFileHandle file,
            out ByHandleFileInformation information
        );

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool SetFileInformationByHandle(
            SafeFileHandle file,
            int fileInformationClass,
            ref FileDispositionInfo fileInformation,
            uint bufferSize
        );

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern uint GetFileAttributes(string fileName);

        public static bool ExistsNoFollow(string path)
        {
            uint attributes = GetFileAttributes(path);
            if (attributes != InvalidFileAttributes)
            {
                return true;
            }
            int error = Marshal.GetLastWin32Error();
            if (error == ErrorFileNotFound || error == ErrorPathNotFound)
            {
                return false;
            }
            throw new Win32Exception(error);
        }

        public static CleanupPathIdentity GetIdentity(string path)
        {
            using (SafeFileHandle handle = CreateFile(
                path,
                0,
                FileShareRead | FileShareWrite | FileShareDelete,
                IntPtr.Zero,
                OpenExisting,
                FileFlagBackupSemantics | FileFlagOpenReparsePoint,
                IntPtr.Zero
            ))
            {
                if (handle.IsInvalid)
                {
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                }
                return GetIdentityFromHandle(handle);
            }
        }

        public static SafeFileHandle OpenExclusiveLock(string path)
        {
            SafeFileHandle handle = CreateFile(
                path,
                GenericRead | GenericWrite | DeleteAccess,
                0,
                IntPtr.Zero,
                OpenExisting,
                FileFlagOpenReparsePoint,
                IntPtr.Zero
            );
            if (handle.IsInvalid)
            {
                int error = Marshal.GetLastWin32Error();
                handle.Dispose();
                throw new Win32Exception(error);
            }
            return handle;
        }

        public static SafeFileHandle OpenPathIdentityHandle(string path)
        {
            SafeFileHandle handle = CreateFile(
                path,
                0,
                FileShareRead | FileShareWrite | FileShareDelete,
                IntPtr.Zero,
                OpenExisting,
                FileFlagBackupSemantics | FileFlagOpenReparsePoint,
                IntPtr.Zero
            );
            if (handle.IsInvalid)
            {
                int error = Marshal.GetLastWin32Error();
                handle.Dispose();
                throw new Win32Exception(error);
            }
            return handle;
        }

        public static CleanupDeletionLeaseV1 OpenDeletionLease(
            string path,
            string expectedPath,
            uint expectedVolumeSerialNumber,
            ulong expectedFileIndex,
            long expectedCreationFileTimeUtc,
            bool expectedIsDirectory
        )
        {
            SafeFileHandle handle = OpenDeletionHandle(path);
            try
            {
                ByHandleFileInformation information = GetInformation(handle);
                bool isDirectory =
                    (information.FileAttributes & FileAttributeDirectory) != 0;
                ulong fileIndex = ((ulong)information.FileIndexHigh << 32)
                    | information.FileIndexLow;
                ulong creationFileTime = ((ulong)information.CreationTime.HighDateTime << 32)
                    | information.CreationTime.LowDateTime;
                if ((information.FileAttributes &
                        (FileAttributeReadOnly | FileAttributeReparsePoint)) != 0 ||
                    information.VolumeSerialNumber != expectedVolumeSerialNumber ||
                    fileIndex != expectedFileIndex ||
                    checked((long)creationFileTime) != expectedCreationFileTimeUtc ||
                    isDirectory != expectedIsDirectory ||
                    !String.Equals(
                        NormalizePath(GetFinalPathFromHandle(handle)),
                        NormalizePath(expectedPath),
                        StringComparison.OrdinalIgnoreCase
                    ))
                {
                    throw new InvalidOperationException(
                        "The cleanup target identity changed before deletion."
                    );
                }
                CleanupDeletionNodeV1 root =
                    new CleanupDeletionNodeV1(handle, path, isDirectory);
                return new CleanupDeletionLeaseV1(root);
            }
            catch
            {
                handle.Dispose();
                throw;
            }
        }

        public static void MarkDeleteOnClose(SafeFileHandle handle)
        {
            if (handle == null || handle.IsInvalid || handle.IsClosed)
            {
                throw new ArgumentException("A live file handle is required.", "handle");
            }
            FileDispositionInfo disposition = new FileDispositionInfo();
            disposition.DeleteFile = true;
            if (!SetFileInformationByHandle(
                    handle,
                    FileDispositionInfoClass,
                    ref disposition,
                    (uint)Marshal.SizeOf(typeof(FileDispositionInfo))
                ))
            {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
        }

        public static CleanupPathIdentity GetIdentityFromHandle(SafeFileHandle handle)
        {
            if (handle == null || handle.IsInvalid || handle.IsClosed)
            {
                throw new ArgumentException("A live file handle is required.", "handle");
            }

            uint capacity = 512;
            string finalPath;
            while (true)
            {
                StringBuilder value = new StringBuilder((int)capacity);
                uint length = GetFinalPathNameByHandle(handle, value, capacity, 0);
                if (length == 0)
                {
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                }
                if (length < capacity)
                {
                    finalPath = value.ToString();
                    break;
                }
                capacity = length + 1;
            }

            ByHandleFileInformation information;
            if (!GetFileInformationByHandle(handle, out information))
            {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            if ((information.FileAttributes & FileAttributeReparsePoint) != 0)
            {
                throw new InvalidOperationException("A reparse-point handle is not safe.");
            }
            ulong fileIndex = ((ulong)information.FileIndexHigh << 32)
                | information.FileIndexLow;
            ulong creationFileTime = ((ulong)information.CreationTime.HighDateTime << 32)
                | information.CreationTime.LowDateTime;
            return new CleanupPathIdentity(
                finalPath,
                information.VolumeSerialNumber,
                fileIndex,
                checked((long)creationFileTime)
            );
        }

        internal static void PrepareOpenedChildren(CleanupDeletionNodeV1 parent)
        {
            if (!parent.IsDirectory)
            {
                return;
            }
            foreach (string childPath in System.IO.Directory.GetFileSystemEntries(parent.Path))
            {
                SafeFileHandle childHandle = OpenDeletionHandle(childPath);
                CleanupDeletionNodeV1 child = null;
                try
                {
                    ByHandleFileInformation information = GetInformation(childHandle);
                    if ((information.FileAttributes &
                            (FileAttributeReadOnly | FileAttributeReparsePoint)) != 0)
                    {
                        throw new InvalidOperationException(
                            "A read-only or reparse-point cleanup target is retained."
                        );
                    }
                    bool childIsDirectory =
                        (information.FileAttributes & FileAttributeDirectory) != 0;
                    child = new CleanupDeletionNodeV1(
                        childHandle,
                        childPath,
                        childIsDirectory
                    );
                    parent.Children.Add(child);
                    childHandle = null;
                    PrepareOpenedChildren(child);
                }
                finally
                {
                    if (childHandle != null)
                    {
                        childHandle.Dispose();
                    }
                }
            }
        }

        internal static void DeleteOpenedFiles(CleanupDeletionNodeV1 node, bool isRoot)
        {
            foreach (CleanupDeletionNodeV1 child in node.Children)
            {
                DeleteOpenedFiles(child, false);
            }
            if (node.IsDirectory)
            {
                return;
            }
            MarkOpenedNodeForDeletion(node);
            if (!isRoot)
            {
                node.CloseHandle();
            }
        }

        internal static void DeleteOpenedDirectories(
            CleanupDeletionNodeV1 node,
            bool isRoot
        )
        {
            foreach (CleanupDeletionNodeV1 child in node.Children)
            {
                DeleteOpenedDirectories(child, false);
            }
            if (!node.IsDirectory || isRoot)
            {
                return;
            }
            MarkOpenedNodeForDeletion(node);
            node.CloseHandle();
        }

        internal static void DeleteOpenedRoot(CleanupDeletionNodeV1 root)
        {
            if (root.IsDirectory)
            {
                MarkOpenedNodeForDeletion(root);
            }
            root.CloseHandle();
        }

        private static void MarkOpenedNodeForDeletion(CleanupDeletionNodeV1 node)
        {
            ByHandleFileInformation information = GetInformation(node.Handle);
            if ((information.FileAttributes & FileAttributeReparsePoint) != 0)
            {
                throw new InvalidOperationException(
                    "A cleanup tree reparse point is not safe."
                );
            }
            if ((information.FileAttributes & FileAttributeReadOnly) != 0)
            {
                throw new InvalidOperationException("A read-only cleanup target is retained.");
            }
            MarkDeleteOnClose(node.Handle);
        }

        private static SafeFileHandle OpenDeletionHandle(string path)
        {
            SafeFileHandle handle = CreateFile(
                path,
                DeleteAccess,
                FileShareRead,
                IntPtr.Zero,
                OpenExisting,
                FileFlagBackupSemantics | FileFlagOpenReparsePoint,
                IntPtr.Zero
            );
            if (handle.IsInvalid)
            {
                int error = Marshal.GetLastWin32Error();
                handle.Dispose();
                throw new Win32Exception(error);
            }
            return handle;
        }

        private static ByHandleFileInformation GetInformation(SafeFileHandle handle)
        {
            ByHandleFileInformation information;
            if (!GetFileInformationByHandle(handle, out information))
            {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            return information;
        }

        private static string GetFinalPathFromHandle(SafeFileHandle handle)
        {
            uint capacity = 512;
            while (true)
            {
                StringBuilder value = new StringBuilder((int)capacity);
                uint length = GetFinalPathNameByHandle(handle, value, capacity, 0);
                if (length == 0)
                {
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                }
                if (length < capacity)
                {
                    return value.ToString();
                }
                capacity = length + 1;
            }
        }

        private static string NormalizePath(string path)
        {
            string value = path.TrimEnd('\\', '/');
            if (value.StartsWith("\\\\?\\UNC\\", StringComparison.OrdinalIgnoreCase))
            {
                return "\\\\" + value.Substring(8);
            }
            if (value.StartsWith("\\\\?\\", StringComparison.OrdinalIgnoreCase))
            {
                return value.Substring(4);
            }
            return value;
        }
    }
}
'@
}

function Get-OpenSreExistingPathIdentity {
    param([string]$Path)

    Initialize-OpenSreCleanupNativePathApi
    $identity = [OpenSre.CleanupNativePathApi]::GetIdentity(
        (ConvertTo-OpenSreExtendedPath -Path $Path)
    )
    return ConvertFrom-OpenSreNativePathIdentity -Identity $identity
}

function Get-OpenSreHandleIdentity {
    param([Microsoft.Win32.SafeHandles.SafeFileHandle]$Handle)

    Initialize-OpenSreCleanupNativePathApi
    $identity = [OpenSre.CleanupNativePathApi]::GetIdentityFromHandle($Handle)
    return ConvertFrom-OpenSreNativePathIdentity -Identity $identity
}

function Open-OpenSrePathIdentityHandle {
    param([string]$Path)

    Initialize-OpenSreCleanupNativePathApi
    return [OpenSre.CleanupNativePathApi]::OpenPathIdentityHandle(
        (ConvertTo-OpenSreExtendedPath -Path $Path)
    )
}

function Open-OpenSreDeletionLease {
    param(
        [string]$Path,
        [psobject]$ExpectedIdentity
    )

    Initialize-OpenSreCleanupNativePathApi
    return [OpenSre.CleanupNativePathApi]::OpenDeletionLease(
        (ConvertTo-OpenSreExtendedPath -Path $Path),
        (ConvertTo-OpenSreComparablePath -Path $Path),
        [uint32]$ExpectedIdentity.volume_serial_number,
        [uint64]$ExpectedIdentity.file_index,
        [int64]$ExpectedIdentity.creation_filetime_utc,
        ([string]$ExpectedIdentity.kind -ceq 'directory')
    )
}

function ConvertFrom-OpenSreNativePathIdentity {
    param([object]$Identity)

    $canonicalPath = ([string]$Identity.FinalPath).TrimEnd('\', '/')
    if ($canonicalPath.StartsWith('\\?\UNC\')) {
        $canonicalPath = '\\' + $canonicalPath.Substring(8)
    }
    elseif ($canonicalPath.StartsWith('\\?\')) {
        $canonicalPath = $canonicalPath.Substring(4)
    }
    return [pscustomobject]@{
        Path = $canonicalPath
        VolumeSerialNumber = [uint32]$Identity.VolumeSerialNumber
        FileIndex = [uint64]$Identity.FileIndex
        CreationFileTimeUtc = [int64]$Identity.CreationFileTimeUtc
    }
}

function Get-OpenSreCanonicalPath {
    param([string]$Path)

    $fullPath = [System.IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
    if (-not $fullPath) {
        throw 'OpenSRE cleanup path is empty.'
    }
    $existingPath = $fullPath
    $missingSegments = New-Object 'System.Collections.Generic.List[string]'
    while (-not (Test-OpenSreCleanupTarget -Path $existingPath)) {
        $leaf = [System.IO.Path]::GetFileName($existingPath)
        $parent = [System.IO.Path]::GetDirectoryName($existingPath)
        if (-not $leaf -or -not $parent -or $parent -eq $existingPath) {
            throw "Could not resolve an existing cleanup-path ancestor: $Path"
        }
        $missingSegments.Insert(0, $leaf)
        $existingPath = $parent
    }
    $canonicalPath = [string](
        Get-OpenSreExistingPathIdentity -Path $existingPath
    ).Path
    foreach ($segment in $missingSegments) {
        $canonicalPath = [System.IO.Path]::Combine($canonicalPath, $segment)
    }
    return $canonicalPath.TrimEnd('\', '/')
}

function Test-OpenSreSameExistingFile {
    param(
        [string]$Left,
        [string]$Right
    )

    $leftIdentity = Get-OpenSreExistingPathIdentity -Path $Left
    $rightIdentity = Get-OpenSreExistingPathIdentity -Path $Right
    return (
        ([string]$leftIdentity.Path).Equals(
            [string]$rightIdentity.Path,
            [System.StringComparison]::OrdinalIgnoreCase
        ) -and
        [uint32]$leftIdentity.VolumeSerialNumber -eq [uint32]$rightIdentity.VolumeSerialNumber -and
        [uint64]$leftIdentity.FileIndex -eq [uint64]$rightIdentity.FileIndex
    )
}

function Test-OpenSreCleanupTarget {
    param([string]$Path)

    Initialize-OpenSreCleanupNativePathApi
    return [OpenSre.CleanupNativePathApi]::ExistsNoFollow(
        (ConvertTo-OpenSreExtendedPath -Path $Path)
    )
}

function Test-OpenSreReparsePoint {
    param([string]$Path)

    $attributes = [System.IO.File]::GetAttributes(
        (ConvertTo-OpenSreExtendedPath -Path $Path)
    )
    return [bool](
        $attributes -band [System.IO.FileAttributes]::ReparsePoint
    )
}

function Assert-OpenSreSafeAncestorChain {
    param([string]$Path)

    $fullPath = [System.IO.Path]::GetFullPath($Path)
    if (-not [System.IO.Path]::IsPathRooted($fullPath)) {
        throw "OpenSRE cleanup path is not absolute: $Path"
    }
    $ancestors = New-Object 'System.Collections.Generic.Stack[string]'
    $current = $fullPath
    while ($current) {
        $ancestors.Push($current)
        $parent = [System.IO.Directory]::GetParent($current)
        if ($null -eq $parent -or
            $parent.FullName.Equals($current, [System.StringComparison]::OrdinalIgnoreCase)) {
            break
        }
        $current = $parent.FullName
    }
    while ($ancestors.Count -gt 0) {
        $ancestor = $ancestors.Pop()
        if ((Test-OpenSreCleanupTarget -Path $ancestor) -and
            (Test-OpenSreReparsePoint -Path $ancestor)) {
            throw "OpenSRE cleanup refuses a reparse-point ancestor: $ancestor"
        }
    }
}

function Assert-OpenSreSafeTree {
    param([string]$Path)

    if (-not (Test-OpenSreCleanupTarget -Path $Path)) {
        return
    }
    Assert-OpenSreSafeAncestorChain -Path $Path
    $pending = New-Object 'System.Collections.Generic.Stack[string]'
    $pending.Push((ConvertTo-OpenSreExtendedPath -Path $Path))
    while ($pending.Count -gt 0) {
        $current = $pending.Pop()
        $attributes = [System.IO.File]::GetAttributes($current)
        if ($attributes -band [System.IO.FileAttributes]::ReparsePoint) {
            throw "OpenSRE cleanup refuses a reparse point: $current"
        }
        if ($attributes -band [System.IO.FileAttributes]::Directory) {
            foreach ($entry in [System.IO.Directory]::EnumerateFileSystemEntries($current)) {
                $pending.Push($entry)
            }
        }
    }
}

function Assert-OpenSreExpectedTargetIdentity {
    param(
        [psobject]$Expected,
        [psobject]$Actual,
        [string]$ExpectedPath,
        [switch]$AllowMovedPath
    )

    if ($null -eq $Expected -or
        -not [string]$Expected.path -or
        $null -eq $Expected.PSObject.Properties['volume_serial_number'] -or
        $null -eq $Expected.PSObject.Properties['file_index'] -or
        $null -eq $Expected.PSObject.Properties['creation_filetime_utc']) {
        throw 'OpenSRE cleanup target identity is invalid.'
    }
    $metadataPath = ConvertTo-OpenSreComparablePath -Path ([string]$Expected.path)
    $expectedComparablePath = ConvertTo-OpenSreComparablePath -Path $ExpectedPath
    if ((-not $AllowMovedPath -and
            -not $metadataPath.Equals(
                $expectedComparablePath,
                [System.StringComparison]::OrdinalIgnoreCase
            )) -or
        -not ([string]$Actual.Path).Equals(
            $expectedComparablePath,
            [System.StringComparison]::OrdinalIgnoreCase
        ) -or
        [uint32]$Actual.VolumeSerialNumber -ne [uint32]$Expected.volume_serial_number -or
        [uint64]$Actual.FileIndex -ne [uint64]$Expected.file_index -or
        [int64]$Actual.CreationFileTimeUtc -ne [int64]$Expected.creation_filetime_utc -or
        [uint64]$Expected.file_index -eq 0 -or
        [int64]$Expected.creation_filetime_utc -le 0) {
        throw "OpenSRE cleanup target was replaced after scheduling: $ExpectedPath"
    }
}

function Test-OpenSreExpectedTarget {
    param([psobject]$Target)

    $path = [string]$Target.path
    $kind = [string]$Target.kind
    if (-not $path -or $kind -notin @('missing', 'file', 'directory')) {
        throw 'OpenSRE cleanup target metadata is invalid.'
    }
    $exists = Test-OpenSreCleanupTarget -Path $path
    if ($kind -ceq 'missing') {
        if ($exists) {
            throw "OpenSRE cleanup target was replaced after scheduling: $path"
        }
        return $false
    }
    if (-not $exists) {
        return $false
    }
    Assert-OpenSreSafeTree -Path $path
    $extendedPath = ConvertTo-OpenSreExtendedPath -Path $path
    $attributes = [System.IO.File]::GetAttributes($extendedPath)
    $isDirectory = [bool](
        $attributes -band [System.IO.FileAttributes]::Directory
    )
    if (($kind -ceq 'directory') -ne $isDirectory) {
        throw "OpenSRE cleanup target type changed after scheduling: $path"
    }
    $actualIdentity = Get-OpenSreExistingPathIdentity -Path $path
    Assert-OpenSreExpectedTargetIdentity `
        -Expected $Target `
        -Actual $actualIdentity `
        -ExpectedPath $path
    if ($kind -ceq 'file') {
        $expectedHash = [string]$Target.sha256
        if ($expectedHash -notmatch '\A[0-9a-f]{64}\z') {
            throw 'OpenSRE cleanup target identity is invalid.'
        }
        $actualHash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actualHash -cne $expectedHash) {
            throw "OpenSRE cleanup target changed after scheduling: $path"
        }
    }
    return $true
}

function Assert-OpenSreExpectedLock {
    param(
        [psobject]$Expected,
        [psobject]$Actual
    )

    if ($null -eq $Expected -or
        -not [string]$Expected.path -or
        $null -eq $Expected.PSObject.Properties['volume_serial_number'] -or
        $null -eq $Expected.PSObject.Properties['file_index'] -or
        $null -eq $Expected.PSObject.Properties['creation_filetime_utc']) {
        throw 'OpenSRE cleanup lock identity is invalid.'
    }
    $expectedPath = ConvertTo-OpenSreComparablePath -Path ([string]$Expected.path)
    if (-not ([string]$Actual.Path).Equals(
            $expectedPath,
            [System.StringComparison]::OrdinalIgnoreCase
        ) -or
        [uint32]$Actual.VolumeSerialNumber -ne [uint32]$Expected.volume_serial_number -or
        [uint64]$Actual.FileIndex -ne [uint64]$Expected.file_index -or
        [int64]$Actual.CreationFileTimeUtc -ne [int64]$Expected.creation_filetime_utc -or
        [uint64]$Expected.file_index -eq 0 -or
        [int64]$Expected.creation_filetime_utc -le 0) {
        throw 'OpenSRE cleanup lock was replaced after scheduling.'
    }
}

function Close-OpenSreOwnedProcess {
    param([object]$Process)

    try {
        if ($Process -is [System.IDisposable]) {
            $Process.Dispose()
        }
    }
    catch {
        # Releasing a local handle must not override the safety classification.
    }
}

function Get-OpenSreProcessExitState {
    param([object]$Process)

    try {
        $hasExited = $Process.HasExited
    }
    catch {
        return 'unknown'
    }
    if ($hasExited -isnot [bool]) {
        return 'unknown'
    }
    if ($hasExited -eq $true) {
        return 'exited'
    }
    return 'running'
}

function Get-OpenSreParentIdentityState {
    $parent = $null
    try {
        try {
            $parent = Get-Process -Id $ParentProcessId -ErrorAction Stop
        }
        catch {
            $parentLookupError = [string]$_.FullyQualifiedErrorId
            if ($parentLookupError -like 'NoProcessFoundForGivenId*') {
                return 'exited'
            }
            return 'unknown'
        }
        if ($null -eq $parent) {
            return 'unknown'
        }
        $parentExitState = Get-OpenSreProcessExitState -Process $parent
        if ($parentExitState -ceq 'exited') {
            return 'exited'
        }
        if ($parentExitState -cne 'running') {
            return 'unknown'
        }
        try {
            $parentPath = [string]$parent.Path
            $parentStarted = [int64]$parent.StartTime.ToUniversalTime().ToFileTimeUtc()
            $sameExecutable = Test-OpenSreSameExistingFile `
                -Left $parentPath `
                -Right ([string]$payload.parent.path)
        }
        catch {
            # The parent can exit after enumeration but before its metadata is read.
            if ((Get-OpenSreProcessExitState -Process $parent) -ceq 'exited') {
                return 'exited'
            }
            return 'unknown'
        }
        if (-not $parentPath) {
            if ((Get-OpenSreProcessExitState -Process $parent) -ceq 'exited') {
                return 'exited'
            }
            return 'unknown'
        }
        if (-not $sameExecutable -or
            $parentStarted -ne [int64]$payload.parent.started_filetime_utc) {
            # The scheduled parent exited and Windows reused its PID.
            return 'exited'
        }
        return Get-OpenSreProcessExitState -Process $parent
    }
    finally {
        Close-OpenSreOwnedProcess -Process $parent
    }
}

function Test-OpenSrePathContains {
    param(
        [string]$Root,
        [string]$Candidate
    )

    $rootPath = Get-OpenSreCanonicalPath -Path $Root
    $candidatePath = [string](
        Get-OpenSreExistingPathIdentity -Path $Candidate
    ).Path
    if ($candidatePath.Equals($rootPath, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }
    return $candidatePath.StartsWith(
        $rootPath + [System.IO.Path]::DirectorySeparatorChar,
        [System.StringComparison]::OrdinalIgnoreCase
    )
}

function Get-OpenSreTargetUseState {
    param(
        [string]$Path,
        [switch]$TreatAsDirectory
    )

    $targetIsDirectory = $TreatAsDirectory -or [System.IO.Directory]::Exists(
        (ConvertTo-OpenSreExtendedPath -Path $Path)
    )
    $processes = New-Object 'System.Collections.Generic.List[object]'
    try {
        try {
            Get-Process -ErrorAction Stop | ForEach-Object { $processes.Add($_) }
        }
        catch {
            return 'unknown'
        }
        foreach ($process in $processes) {
            try {
                $processName = [string]$process.ProcessName
            }
            catch {
                if ((Get-OpenSreProcessExitState -Process $process) -ceq 'exited') {
                    continue
                }
                return 'unknown'
            }
            if ([string]::IsNullOrWhiteSpace($processName)) {
                if ((Get-OpenSreProcessExitState -Process $process) -ceq 'exited') {
                    continue
                }
                return 'unknown'
            }
            if ($processName -ine 'opensre') {
                continue
            }
            $exitState = Get-OpenSreProcessExitState -Process $process
            if ($exitState -ceq 'exited') {
                continue
            }
            if ($exitState -cne 'running') {
                return 'unknown'
            }
            try {
                $processPath = [string]$process.Path
                if (-not $processPath) {
                    throw 'The OpenSRE process path is unavailable.'
                }
                if ($targetIsDirectory) {
                    $sameTarget = Test-OpenSrePathContains `
                        -Root $Path `
                        -Candidate $processPath
                }
                else {
                    if (Test-OpenSreCleanupTarget -Path $Path) {
                        $sameTarget = Test-OpenSreSameExistingFile `
                            -Left $Path `
                            -Right $processPath
                    }
                    else {
                        $targetPath = Get-OpenSreCanonicalPath -Path $Path
                        $runningPath = [string](
                            Get-OpenSreExistingPathIdentity -Path $processPath
                        ).Path
                        $sameTarget = $runningPath.Equals(
                            $targetPath,
                            [System.StringComparison]::OrdinalIgnoreCase
                        )
                    }
                }
            }
            catch {
                if ((Get-OpenSreProcessExitState -Process $process) -ceq 'exited') {
                    continue
                }
                return 'unknown'
            }
            $exitState = Get-OpenSreProcessExitState -Process $process
            if ($exitState -ceq 'exited') {
                continue
            }
            if ($exitState -cne 'running') {
                return 'unknown'
            }
            if ($sameTarget) {
                return 'busy'
            }
        }
        return 'safe'
    }
    finally {
        foreach ($process in $processes) {
            Close-OpenSreOwnedProcess -Process $process
        }
    }
}

function Test-OpenSreTargetInUse {
    param(
        [string]$Path,
        [switch]$TreatAsDirectory
    )

    # Incomplete enumeration is neither proof of use nor permission to delete.
    # Rescans share the lock deadline; successive targets cannot reset the budget.
    do {
        try {
            $state = Get-OpenSreTargetUseState -Path $Path -TreatAsDirectory:$TreatAsDirectory
        }
        catch {
            $state = 'unknown'
        }
        if ($state -ceq 'safe') { return $false }
        if ($state -ceq 'busy') { return $true }
        if ([System.DateTime]::UtcNow -ge $lockDeadline) { return $true }
        Start-Sleep -Milliseconds 250
    } while ([System.DateTime]::UtcNow -lt $lockDeadline)
    return $true
}

function Restore-OpenSreGuardedRetirement {
    param(
        [Microsoft.Win32.SafeHandles.SafeFileHandle]$IdentityHandle,
        [psobject]$ExpectedIdentity,
        [string]$Path,
        [string]$RetiredPath
    )

    $retiredIdentity = Get-OpenSreHandleIdentity -Handle $IdentityHandle
    Assert-OpenSreExpectedTargetIdentity `
        -Expected $ExpectedIdentity `
        -Actual $retiredIdentity `
        -ExpectedPath $RetiredPath `
        -AllowMovedPath
    if (Test-OpenSreCleanupTarget -Path $Path) {
        throw "OpenSRE cleanup target could not be restored because its path is occupied: $Path"
    }
    Assert-OpenSreSafeAncestorChain -Path $RetiredPath
    Assert-OpenSreSafeAncestorChain -Path $Path
    Move-Item -LiteralPath $RetiredPath -Destination $Path -ErrorAction Stop
    $restoredIdentity = Get-OpenSreHandleIdentity -Handle $IdentityHandle
    Assert-OpenSreExpectedTargetIdentity `
        -Expected $ExpectedIdentity `
        -Actual $restoredIdentity `
        -ExpectedPath $Path
}

function Restore-OpenSreUnexpectedRetirement {
    param(
        [string]$Path,
        [string]$RetiredPath
    )

    if ((Test-OpenSreCleanupTarget -Path $Path) -or
        -not (Test-OpenSreCleanupTarget -Path $RetiredPath)) {
        throw "OpenSRE cleanup could not restore a replaced target: $Path"
    }
    $pathParent = Split-Path -Parent $Path
    $retiredParent = Split-Path -Parent $RetiredPath
    Assert-OpenSreSafeAncestorChain -Path $pathParent
    Assert-OpenSreSafeAncestorChain -Path $retiredParent

    if (Test-OpenSreReparsePoint -Path $RetiredPath) {
        Move-Item -LiteralPath $RetiredPath -Destination $Path -ErrorAction Stop
        if (-not (Test-OpenSreCleanupTarget -Path $Path) -or
            -not (Test-OpenSreReparsePoint -Path $Path) -or
            (Test-OpenSreCleanupTarget -Path $RetiredPath)) {
            throw "OpenSRE cleanup could not verify a restored reparse-point target: $Path"
        }
        return
    }

    $replacementHandle = $null
    try {
        $replacementHandle = Open-OpenSrePathIdentityHandle -Path $RetiredPath
        $retiredIdentity = Get-OpenSreHandleIdentity -Handle $replacementHandle
        Move-Item -LiteralPath $RetiredPath -Destination $Path -ErrorAction Stop
        $restoredIdentity = Get-OpenSreHandleIdentity -Handle $replacementHandle
        $expectedPath = ConvertTo-OpenSreComparablePath -Path $Path
        if (-not ([string]$restoredIdentity.Path).Equals(
                $expectedPath,
                [System.StringComparison]::OrdinalIgnoreCase
            ) -or
            [uint32]$restoredIdentity.VolumeSerialNumber -ne
                [uint32]$retiredIdentity.VolumeSerialNumber -or
            [uint64]$restoredIdentity.FileIndex -ne [uint64]$retiredIdentity.FileIndex -or
            [int64]$restoredIdentity.CreationFileTimeUtc -ne
                [int64]$retiredIdentity.CreationFileTimeUtc) {
            throw "OpenSRE cleanup could not verify the restored target: $Path"
        }
    }
    finally {
        if ($null -ne $replacementHandle) {
            $replacementHandle.Dispose()
        }
    }
}

function New-OpenSreRetiredTarget {
    param(
        [string]$Path,
        [psobject]$ExpectedIdentity,
        [Microsoft.Win32.SafeHandles.SafeFileHandle]$IdentityHandle,
        [object[]]$Guards = @()
    )

    return [pscustomobject]@{
        Path = $Path
        ExpectedIdentity = $ExpectedIdentity
        IdentityHandle = $IdentityHandle
        Guards = @($Guards)
    }
}

function Close-OpenSreRetiredTargetGuards {
    param([psobject]$RetiredTarget)

    foreach ($guard in @($RetiredTarget.Guards)) {
        try {
            if ($null -ne $guard -and $guard -is [System.IDisposable]) {
                $guard.Dispose()
            }
        }
        catch {
            # Process exit remains the final handle cleanup fallback.
        }
    }
    $RetiredTarget.Guards = @()
}

function Close-OpenSreRetiredTarget {
    param([psobject]$RetiredTarget)

    Close-OpenSreRetiredTargetGuards -RetiredTarget $RetiredTarget
    try {
        if ($null -ne $RetiredTarget.IdentityHandle) {
            $RetiredTarget.IdentityHandle.Dispose()
        }
    }
    catch {
        # Process exit remains the final handle cleanup fallback.
    }
}

function Restore-OpenSreRetiredTargets {
    param([object[]]$RetiredTargets)

    for ($rollbackIndex = $RetiredTargets.Count - 1; $rollbackIndex -ge 0; $rollbackIndex--) {
        $retiredTarget = $RetiredTargets[$rollbackIndex]
        try {
            Close-OpenSreRetiredTargetGuards -RetiredTarget $retiredTarget
            Assert-OpenSreSafeTree -Path ([string]$retiredTarget.Path)
            Restore-OpenSreGuardedRetirement `
                -IdentityHandle $retiredTarget.IdentityHandle `
                -ExpectedIdentity $retiredTarget.ExpectedIdentity `
                -Path ([string]$retiredTarget.ExpectedIdentity.path) `
                -RetiredPath ([string]$retiredTarget.Path)
        }
        catch {
            # Preserve an unsafe or occupied retirement rather than overwrite it.
        }
        finally {
            Close-OpenSreRetiredTarget -RetiredTarget $retiredTarget
        }
    }
}

function Test-OpenSreInvalidDeletionException {
    param([System.Exception]$Exception)

    $current = $Exception
    while ($null -ne $current) {
        if ($current -is [System.InvalidOperationException]) {
            return $true
        }
        $current = $current.InnerException
    }
    return $false
}

function Open-OpenSreRetiredTargetDeletion {
    param([psobject]$RetiredTarget)

    $target = [string]$RetiredTarget.Path
    for ($removeAttempt = 0; $removeAttempt -lt 150; $removeAttempt++) {
        try {
            $handleIdentity = Get-OpenSreHandleIdentity `
                -Handle $RetiredTarget.IdentityHandle
            Assert-OpenSreExpectedTargetIdentity `
                -Expected $RetiredTarget.ExpectedIdentity `
                -Actual $handleIdentity `
                -ExpectedPath $target `
                -AllowMovedPath
            if (-not (Test-OpenSreCleanupTarget -Path $target)) {
                return $null
            }
            $removalPathIdentity = Get-OpenSreExistingPathIdentity -Path $target
            Assert-OpenSreExpectedTargetIdentity `
                -Expected $RetiredTarget.ExpectedIdentity `
                -Actual $removalPathIdentity `
                -ExpectedPath $target `
                -AllowMovedPath
        }
        catch {
            # Losing the retired object's identity revokes deletion authority.
            return $null
        }

        $deletionLease = $null
        try {
            $deletionLease = Open-OpenSreDeletionLease `
                -Path $target `
                -ExpectedIdentity $RetiredTarget.ExpectedIdentity
            $deletionLease.PrepareTree()
            $preparedLease = $deletionLease
            $deletionLease = $null
            return $preparedLease
        }
        catch {
            if (Test-OpenSreInvalidDeletionException -Exception $_.Exception) {
                # Identity and reparse failures cannot become safe on retry.
                return $null
            }
        }
        finally {
            if ($null -ne $deletionLease) {
                $deletionLease.Dispose()
            }
        }
        if ([System.DateTime]::UtcNow -ge $lockDeadline) {
            return $null
        }
        Start-Sleep -Milliseconds 200
    }
    return $null
}

function Move-OpenSreTargetIfUnused {
    param(
        [string]$Path,
        [psobject]$ExpectedIdentity
    )

    if (-not (Test-OpenSreCleanupTarget -Path $Path)) {
        return ''
    }
    Assert-OpenSreSafeAncestorChain -Path $Path

    $identityHandle = $null
    $guard = $null
    $targetWasDirectory = [string]$ExpectedIdentity.kind -ceq 'directory'
    try {
        $identityHandle = Open-OpenSrePathIdentityHandle -Path $Path
        $openedIdentity = Get-OpenSreHandleIdentity -Handle $identityHandle
        Assert-OpenSreExpectedTargetIdentity `
            -Expected $ExpectedIdentity `
            -Actual $openedIdentity `
            -ExpectedPath $Path
        if (Test-OpenSreTargetInUse -Path $Path) {
            throw "OpenSRE cleanup target is still in use: $Path"
        }
        $guardPath = if ($targetWasDirectory) {
            Join-Path $Path 'opensre.exe'
        }
        else {
            $Path
        }
        if (-not $targetWasDirectory -and
            [System.IO.File]::Exists((ConvertTo-OpenSreExtendedPath -Path $guardPath))) {
            $guard = [System.IO.File]::Open(
                $guardPath,
                [System.IO.FileMode]::Open,
                [System.IO.FileAccess]::Read,
                [System.IO.FileShare]::Delete
            )
        }
        if (Test-OpenSreTargetInUse -Path $Path) {
            throw "OpenSRE cleanup target became busy: $Path"
        }
        $retiredPath = "$Path.uninstall-$([System.Guid]::NewGuid().ToString('N'))"
        Assert-OpenSreSafeAncestorChain -Path $Path
        Assert-OpenSreSafeAncestorChain -Path $retiredPath
        Move-Item -LiteralPath $Path -Destination $retiredPath -ErrorAction Stop
        $retiredIdentityMatches = $true
        try {
            $retiredIdentity = Get-OpenSreHandleIdentity -Handle $identityHandle
            Assert-OpenSreExpectedTargetIdentity `
                -Expected $ExpectedIdentity `
                -Actual $retiredIdentity `
                -ExpectedPath $retiredPath `
                -AllowMovedPath
        }
        catch {
            $retiredIdentityMatches = $false
        }
        if (-not $retiredIdentityMatches) {
            Restore-OpenSreUnexpectedRetirement -Path $Path -RetiredPath $retiredPath
            throw "OpenSRE cleanup target was replaced during retirement: $Path"
        }
        # Child handles prevent a directory rename; guard its new name before
        # the retirement scan and retain the complete tree if a launch won the race.
        if ($targetWasDirectory) {
            $retiredExecutable = Join-Path $retiredPath 'opensre.exe'
            try {
                if ([System.IO.File]::Exists((ConvertTo-OpenSreExtendedPath -Path $retiredExecutable))) {
                    $guard = [System.IO.File]::Open(
                        (ConvertTo-OpenSreExtendedPath -Path $retiredExecutable),
                        [System.IO.FileMode]::Open,
                        [System.IO.FileAccess]::Read,
                        [System.IO.FileShare]::Delete
                    )
                }
            }
            catch {
                Restore-OpenSreGuardedRetirement `
                    -IdentityHandle $identityHandle `
                    -ExpectedIdentity $ExpectedIdentity `
                    -Path $Path `
                    -RetiredPath $retiredPath
                throw
            }
        }
        if ((Test-OpenSreTargetInUse -Path $Path -TreatAsDirectory:$targetWasDirectory) -or
            (Test-OpenSreTargetInUse -Path $retiredPath -TreatAsDirectory:$targetWasDirectory)) {
            if ($targetWasDirectory -and $null -ne $guard) {
                $guard.Dispose()
                $guard = $null
            }
            Restore-OpenSreGuardedRetirement `
                -IdentityHandle $identityHandle `
                -ExpectedIdentity $ExpectedIdentity `
                -Path $Path `
                -RetiredPath $retiredPath
            throw "OpenSRE cleanup target became busy during retirement: $Path"
        }
        $retiredTarget = New-OpenSreRetiredTarget `
            -Path $retiredPath `
            -ExpectedIdentity $ExpectedIdentity `
            -IdentityHandle $identityHandle `
            -Guards @($guard)
        $identityHandle = $null
        $guard = $null
        return $retiredTarget
    }
    catch {
        throw
    }
    finally {
        if ($null -ne $guard) {
            $guard.Dispose()
        }
        if ($null -ne $identityHandle) {
            $identityHandle.Dispose()
        }
    }
}

function Test-OpenSreManagedLauncher {
    param([string]$Path)

    try {
        $lines = @(Get-Content -LiteralPath $Path)
        return (
            $lines.Count -ge 2 -and
            $lines[0].Trim() -ieq '@echo off' -and
            $lines[1].Trim() -ceq ':: OpenSRE Windows launcher v1'
        )
    }
    catch {
        return $false
    }
}

for ($waitAttempt = 0; $waitAttempt -lt 2400; $waitAttempt++) {
    $parentState = Get-OpenSreParentIdentityState
    if ($parentState -ceq 'exited') {
        break
    }
    if ($parentState -cne 'running') {
        Exit-OpenSreCleanup -ExitCode 1
    }
    if ($waitAttempt -eq 2399) {
        Exit-OpenSreCleanup -ExitCode 1
    }
    Start-Sleep -Milliseconds 250
}

$managed = $payload.managed
$retiredTargets = @()
$deleteLockPath = ''
$deleteData = $null -eq $managed
$lockHandle = $null
$cleanupLockPath = ''
$lockDeadline = [System.DateTime]::UtcNow.AddSeconds(30)
$cleanupLock = $payload.lock
if ($null -ne $cleanupLock) {
    $cleanupLockPath = [string]$cleanupLock.path
}
if ($null -ne $managed) {
    if ([string]$managed.layout_marker_text -cne 'OpenSRE Windows bundle layout v1') {
        Exit-OpenSreCleanup -ExitCode 1
    }
    $managedLockPath = ConvertTo-OpenSreComparablePath -Path ([string]$managed.lock_path)
    $payloadLockPath = ConvertTo-OpenSreComparablePath -Path $cleanupLockPath
    if (-not $cleanupLockPath -or
        -not $managedLockPath.Equals(
            $payloadLockPath,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
        Exit-OpenSreCleanup -ExitCode 1
    }
}
if ($cleanupLockPath) {
    try {
        Assert-OpenSreSafeAncestorChain -Path $cleanupLockPath
        if (-not (Test-OpenSreCleanupTarget -Path $cleanupLockPath) -or
            (Test-OpenSreReparsePoint -Path $cleanupLockPath)) {
            Exit-OpenSreCleanup -ExitCode 1
        }
    }
    catch {
        Exit-OpenSreCleanup -ExitCode 1
    }
    while ($null -eq $lockHandle -and [System.DateTime]::UtcNow -lt $lockDeadline) {
        try {
            Assert-OpenSreSafeAncestorChain -Path $cleanupLockPath
            if (-not (Test-OpenSreCleanupTarget -Path $cleanupLockPath) -or
                (Test-OpenSreReparsePoint -Path $cleanupLockPath)) {
                Exit-OpenSreCleanup -ExitCode 1
            }
        }
        catch {
            Exit-OpenSreCleanup -ExitCode 1
        }
        try {
            $lockHandle = [OpenSre.CleanupNativePathApi]::OpenExclusiveLock(
                (ConvertTo-OpenSreExtendedPath -Path $cleanupLockPath)
            )
        }
        catch {
            Start-Sleep -Milliseconds 250
        }
    }
    if ($null -eq $lockHandle) {
        Exit-OpenSreCleanup -ExitCode 1
    }
    try {
        $acquiredLockIdentity = Get-OpenSreHandleIdentity -Handle $lockHandle
        Assert-OpenSreExpectedLock -Expected $cleanupLock -Actual $acquiredLockIdentity
        Assert-OpenSreSafeAncestorChain -Path $cleanupLockPath
        if (-not (Test-OpenSreCleanupTarget -Path $cleanupLockPath) -or
            (Test-OpenSreReparsePoint -Path $cleanupLockPath)) {
            $lockHandle.Dispose()
            $lockHandle = $null
            Exit-OpenSreCleanup -ExitCode 1
        }
    }
    catch {
        if ($null -ne $lockHandle) {
            $lockHandle.Dispose()
            $lockHandle = $null
        }
        Exit-OpenSreCleanup -ExitCode 1
    }
}

if ($null -ne $managed) {
    $launcherRetirement = $null
    $appRootIdentityHandle = $null
    $versionGuards = @()
    try {
        $appRoot = [string]$managed.app_root
        $expectedInstallId = [string]$managed.expected_install_id
        $activeVersion = [string]$managed.active_version
        $activeExecutable = Join-Path $activeVersion 'opensre.exe'
        $versionsRoot = Join-Path $appRoot 'versions'
        $expectedActiveVersion = Join-Path $versionsRoot $expectedInstallId
        $expectedLockPath = Join-Path (Split-Path -Parent $appRoot) '.opensre-app.install.lock'
        if (-not $appRoot -or
            [System.IO.Path]::GetFileName($appRoot) -ine '.opensre-app' -or
            $expectedInstallId -notmatch '\A[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?\z' -or
            -not ([System.IO.Path]::GetFullPath($activeVersion)).Equals(
                [System.IO.Path]::GetFullPath($expectedActiveVersion),
                [System.StringComparison]::OrdinalIgnoreCase
            ) -or
            -not ([System.IO.Path]::GetFullPath([string]$managed.lock_path)).Equals(
                [System.IO.Path]::GetFullPath($expectedLockPath),
                [System.StringComparison]::OrdinalIgnoreCase
            )) {
            Exit-OpenSreCleanup -ExitCode 1
        }
        if (-not (Test-OpenSreCleanupTarget -Path $appRoot)) {
            Exit-OpenSreCleanup -ExitCode 1
        }
        $appRootIdentityHandle = Open-OpenSrePathIdentityHandle -Path $appRoot
        $openedAppRootIdentity = Get-OpenSreHandleIdentity -Handle $appRootIdentityHandle
        Assert-OpenSreExpectedTargetIdentity `
            -Expected $managed.app_target `
            -Actual $openedAppRootIdentity `
            -ExpectedPath $appRoot
        Assert-OpenSreSafeAncestorChain -Path $appRoot
        Assert-OpenSreSafeTree -Path $appRoot
        $appCreated = [int64](
            Get-Item -LiteralPath $appRoot -Force -ErrorAction Stop
        ).CreationTimeUtc.ToFileTimeUtc()
        if ($appCreated -ne [int64]$managed.app_created_filetime_utc) {
            Exit-OpenSreCleanup -ExitCode 1
        }
        if (-not (Test-Path -LiteralPath $activeExecutable -PathType Leaf)) {
            Exit-OpenSreCleanup -ExitCode 1
        }
        $activeHash = (
            Get-FileHash -LiteralPath $activeExecutable -Algorithm SHA256
        ).Hash.ToLowerInvariant()
        if ($activeHash -cne [string]$managed.active_executable_sha256) {
            Exit-OpenSreCleanup -ExitCode 1
        }
        $pointerPath = Join-Path $appRoot 'current.txt'
        $currentInstallId = ''
        if (Test-Path -LiteralPath $pointerPath -PathType Leaf) {
            if (Test-OpenSreReparsePoint -Path $pointerPath) {
                Exit-OpenSreCleanup -ExitCode 1
            }
            $pointerText = [string](Get-Content -LiteralPath $pointerPath -Raw)
            $pointerMatch = [System.Text.RegularExpressions.Regex]::Match(
                $pointerText,
                '\A(?<id>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)(?:\r?\n)?\z',
                [System.Text.RegularExpressions.RegexOptions]::CultureInvariant
            )
            if (-not $pointerMatch.Success) {
                Exit-OpenSreCleanup -ExitCode 1
            }
            $currentInstallId = $pointerMatch.Groups['id'].Value
        }
        if (-not $currentInstallId) {
            Exit-OpenSreCleanup -ExitCode 1
        }

        if ($currentInstallId -ne $expectedInstallId) {
            $currentVersionPath = Join-Path (Join-Path $appRoot 'versions') $currentInstallId
            $currentExecutable = Join-Path $currentVersionPath 'opensre.exe'
            if (-not $currentInstallId -or
                -not (Test-Path -LiteralPath $currentExecutable -PathType Leaf)) {
                Exit-OpenSreCleanup -ExitCode 1
            }
            $retiredVersion = Move-OpenSreTargetIfUnused `
                -Path ([string]$managed.active_version) `
                -ExpectedIdentity $managed.active_version_target
            if ($retiredVersion) {
                $retiredTargets += $retiredVersion
            }
        }
        else {
            $markerPath = Join-Path $appRoot 'layout-v1.marker'
            $markerText = ''
            if (Test-Path -LiteralPath $markerPath -PathType Leaf) {
                if (Test-OpenSreReparsePoint -Path $markerPath) {
                    Exit-OpenSreCleanup -ExitCode 1
                }
                $markerText = ([string](Get-Content -LiteralPath $markerPath -Raw)).Trim()
            }
            if ($markerText -cne [string]$managed.layout_marker_text) {
                Exit-OpenSreCleanup -ExitCode 1
            }

            $launcher = [string]$managed.launcher
            $launcherTarget = $managed.launcher_target
            if (-not $launcher -and $null -ne $launcherTarget) {
                Exit-OpenSreCleanup -ExitCode 1
            }
            if ($launcher) {
                if ($null -eq $launcherTarget -or
                    [string]$launcherTarget.kind -notin @('missing', 'file')) {
                    Exit-OpenSreCleanup -ExitCode 1
                }
                $launcherMetadataPath = ConvertTo-OpenSreComparablePath `
                    -Path ([string]$launcherTarget.path)
                $launcherExpectedPath = ConvertTo-OpenSreComparablePath -Path $launcher
                if (-not $launcherMetadataPath.Equals(
                        $launcherExpectedPath,
                        [System.StringComparison]::OrdinalIgnoreCase
                    )) {
                    Exit-OpenSreCleanup -ExitCode 1
                }
            }
            if ($launcher -and (Test-OpenSreExpectedTarget -Target $launcherTarget)) {
                if (-not (Test-OpenSreManagedLauncher -Path $launcher)) {
                    Exit-OpenSreCleanup -ExitCode 1
                }
                $launcherRetirement = Move-OpenSreTargetIfUnused `
                    -Path $launcher `
                    -ExpectedIdentity $launcherTarget
            }

            if (Test-OpenSreTargetInUse -Path $appRoot) {
                throw 'OpenSRE bundle is still in use.'
            }
            $versionExecutablePaths = @()
            if (Test-Path -LiteralPath $versionsRoot -PathType Container) {
                foreach ($versionDirectory in @(Get-ChildItem -LiteralPath $versionsRoot -Directory -Force)) {
                    $versionExecutable = Join-Path $versionDirectory.FullName 'opensre.exe'
                    if (Test-Path -LiteralPath $versionExecutable -PathType Leaf) {
                        $versionExecutablePaths += $versionExecutable
                    }
                }
            }
            if (Test-OpenSreTargetInUse -Path $appRoot) {
                throw 'OpenSRE bundle became busy during uninstall.'
            }
            $movedAppRoot = "$appRoot.uninstall-$([System.Guid]::NewGuid().ToString('N'))"
            Assert-OpenSreSafeAncestorChain -Path $appRoot
            Assert-OpenSreSafeAncestorChain -Path $movedAppRoot
            Move-Item -LiteralPath $appRoot -Destination $movedAppRoot -ErrorAction Stop
            $movedAppRootMatches = $true
            try {
                $movedAppRootIdentity = Get-OpenSreHandleIdentity `
                    -Handle $appRootIdentityHandle
                Assert-OpenSreExpectedTargetIdentity `
                    -Expected $managed.app_target `
                    -Actual $movedAppRootIdentity `
                    -ExpectedPath $movedAppRoot `
                    -AllowMovedPath
            }
            catch {
                $movedAppRootMatches = $false
            }
            if (-not $movedAppRootMatches) {
                Restore-OpenSreUnexpectedRetirement `
                    -Path $appRoot `
                    -RetiredPath $movedAppRoot
                throw 'OpenSRE bundle was replaced during retirement.'
            }
            try {
                foreach ($versionExecutable in $versionExecutablePaths) {
                    $retiredExecutable = Join-Path $movedAppRoot (
                        $versionExecutable.Substring($appRoot.Length).TrimStart('\', '/')
                    )
                    $versionGuards += [System.IO.File]::Open(
                        (ConvertTo-OpenSreExtendedPath -Path $retiredExecutable),
                        [System.IO.FileMode]::Open,
                        [System.IO.FileAccess]::Read,
                        [System.IO.FileShare]::Delete
                    )
                }
            }
            catch {
                foreach ($guard in $versionGuards) { $guard.Dispose() }
                $versionGuards = @()
                Restore-OpenSreGuardedRetirement `
                    -IdentityHandle $appRootIdentityHandle `
                    -ExpectedIdentity $managed.app_target `
                    -Path $appRoot `
                    -RetiredPath $movedAppRoot
                throw
            }
            if ((Test-OpenSreTargetInUse -Path $appRoot -TreatAsDirectory) -or
                (Test-OpenSreTargetInUse -Path $movedAppRoot -TreatAsDirectory)) {
                foreach ($guard in $versionGuards) { $guard.Dispose() }
                $versionGuards = @()
                try {
                    Restore-OpenSreGuardedRetirement `
                        -IdentityHandle $appRootIdentityHandle `
                        -ExpectedIdentity $managed.app_target `
                        -Path $appRoot `
                        -RetiredPath $movedAppRoot
                }
                catch {
                    if ($null -ne $launcherRetirement) {
                        Close-OpenSreRetiredTarget -RetiredTarget $launcherRetirement
                        $launcherRetirement = $null
                    }
                    throw 'OpenSRE bundle retirement could not be rolled back safely.'
                }
                throw 'OpenSRE bundle became busy during retirement.'
            }
            $appRootRetirement = New-OpenSreRetiredTarget `
                -Path $movedAppRoot `
                -ExpectedIdentity $managed.app_target `
                -IdentityHandle $appRootIdentityHandle `
                -Guards $versionGuards
            $appRootIdentityHandle = $null
            $versionGuards = @()
            if ($null -ne $launcherRetirement) {
                $retiredTargets += $launcherRetirement
                $launcherRetirement = $null
            }
            $retiredTargets += $appRootRetirement
            $deleteData = $true
            $deleteLockPath = [string]$managed.lock_path
        }

        foreach ($targetValue in @($payload.targets)) {
            if (-not (Test-OpenSreExpectedTarget -Target $targetValue)) {
                continue
            }
            $retiredTarget = Move-OpenSreTargetIfUnused `
                -Path ([string]$targetValue.path) `
                -ExpectedIdentity $targetValue
            if ($retiredTarget) {
                $retiredTargets += $retiredTarget
            }
        }
    }
    catch {
        if ($null -ne $launcherRetirement -and
            (Test-Path -LiteralPath ([string]$managed.app_root) -PathType Container) -and
            (Test-Path -LiteralPath ([string]$launcherRetirement.Path) -PathType Leaf) -and
            -not (Test-Path -LiteralPath ([string]$managed.launcher))) {
            Restore-OpenSreGuardedRetirement `
                -IdentityHandle $launcherRetirement.IdentityHandle `
                -ExpectedIdentity $launcherRetirement.ExpectedIdentity `
                -Path ([string]$managed.launcher) `
                -RetiredPath ([string]$launcherRetirement.Path)
        }
        if ($null -ne $launcherRetirement) {
            Close-OpenSreRetiredTarget -RetiredTarget $launcherRetirement
            $launcherRetirement = $null
        }
        Restore-OpenSreRetiredTargets -RetiredTargets $retiredTargets
        $retiredTargets = @()
        Exit-OpenSreCleanup -ExitCode 1
    }
    finally {
        foreach ($guard in $versionGuards) {
            $guard.Dispose()
        }
        if ($null -ne $appRootIdentityHandle) {
            $appRootIdentityHandle.Dispose()
        }
    }
}
else {
    try {
        foreach ($targetValue in @($payload.targets)) {
            if (-not (Test-OpenSreExpectedTarget -Target $targetValue)) {
                continue
            }
            $retiredTarget = Move-OpenSreTargetIfUnused `
                -Path ([string]$targetValue.path) `
                -ExpectedIdentity $targetValue
            if ($retiredTarget) {
                $retiredTargets += $retiredTarget
            }
        }
    }
    catch {
        Restore-OpenSreRetiredTargets -RetiredTargets $retiredTargets
        $retiredTargets = @()
        if ($null -ne $lockHandle) {
            $lockHandle.Dispose()
            $lockHandle = $null
        }
        Exit-OpenSreCleanup -ExitCode 1
    }
}

# Decide whether data belongs to this uninstall before crossing the deletion
# commit boundary. Every installation and eligible data retirement is then
# prepared as one transaction, so an ordinary preparation failure can restore
# the complete runnable layout and all user data.
$preparationFailed = $false
if ($deleteData) {
    try {
        foreach ($guardPathValue in @($payload.data_guard_paths)) {
            if (Test-OpenSreCleanupTarget -Path ([string]$guardPathValue)) {
                $deleteData = $false
                break
            }
        }
    }
    catch {
        $preparationFailed = $true
    }
}
$dataFailed = $false
$dataRetiredTargets = @()
$preparedRetiredTargets = @()
$preparedDataTargets = @()

if ($deleteData) {
    try {
        foreach ($dataTargetValue in @($payload.data_targets)) {
            if ($null -eq $dataTargetValue -or
                [string]$dataTargetValue.kind -notin @('missing', 'directory')) {
                throw 'OpenSRE cleanup data-target metadata is invalid.'
            }
            if (-not (Test-OpenSreExpectedTarget -Target $dataTargetValue)) {
                continue
            }
            $dataRetiredTarget = Move-OpenSreTargetIfUnused `
                -Path ([string]$dataTargetValue.path) `
                -ExpectedIdentity $dataTargetValue
            if ($null -ne $dataRetiredTarget) {
                $dataRetiredTargets += $dataRetiredTarget
            }
        }
    }
    catch {
        $preparationFailed = $true
    }
}

if (-not $preparationFailed) {
    foreach ($retiredTarget in $retiredTargets) {
        $deletionLease = Open-OpenSreRetiredTargetDeletion -RetiredTarget $retiredTarget
        if ($null -eq $deletionLease) {
            $preparationFailed = $true
            break
        }
        $preparedRetiredTargets += [pscustomobject]@{
            RetiredTarget = $retiredTarget
            Lease = $deletionLease
        }
    }
}
if (-not $preparationFailed) {
    foreach ($dataRetiredTarget in $dataRetiredTargets) {
        $dataDeletionLease = Open-OpenSreRetiredTargetDeletion `
            -RetiredTarget $dataRetiredTarget
        if ($null -eq $dataDeletionLease) {
            $preparationFailed = $true
            break
        }
        $preparedDataTargets += [pscustomobject]@{
            RetiredTarget = $dataRetiredTarget
            Lease = $dataDeletionLease
        }
    }
}

if ($preparationFailed) {
    foreach ($preparedDataTarget in $preparedDataTargets) {
        $preparedDataTarget.Lease.Dispose()
    }
    foreach ($preparedTarget in $preparedRetiredTargets) {
        $preparedTarget.Lease.Dispose()
    }
    Restore-OpenSreRetiredTargets -RetiredTargets $dataRetiredTargets
    Restore-OpenSreRetiredTargets -RetiredTargets $retiredTargets
    if ($null -ne $lockHandle) {
        $lockHandle.Dispose()
        $lockHandle = $null
    }
    Exit-OpenSreCleanup -ExitCode 1
}

$commitFailed = $false
try {
    foreach ($preparedTarget in $preparedRetiredTargets) {
        $preparedTarget.Lease.DeleteFiles()
    }
    # Every entrypoint is now delete-pending, so closing its execution guard
    # cannot reopen a launch window before directories are retired.
    foreach ($preparedTarget in $preparedRetiredTargets) {
        Close-OpenSreRetiredTargetGuards -RetiredTarget $preparedTarget.RetiredTarget
    }
    foreach ($preparedTarget in $preparedRetiredTargets) {
        $preparedTarget.Lease.DeleteDirectories()
        $preparedTarget.Lease.DeleteRoot()
    }
}
catch {
    # Disposition failures occur after the irreversible commit boundary. Keep
    # user data and the install lock so the residual quarantine can be diagnosed.
    $commitFailed = $true
}
finally {
    foreach ($preparedTarget in $preparedRetiredTargets) {
        $preparedTarget.Lease.Dispose()
    }
    foreach ($retiredTarget in $retiredTargets) {
        Close-OpenSreRetiredTarget -RetiredTarget $retiredTarget
    }
}

if ($commitFailed) {
    foreach ($preparedDataTarget in $preparedDataTargets) {
        $preparedDataTarget.Lease.Dispose()
    }
    Restore-OpenSreRetiredTargets -RetiredTargets $dataRetiredTargets
    if ($null -ne $lockHandle) {
        $lockHandle.Dispose()
        $lockHandle = $null
    }
    Exit-OpenSreCleanup -ExitCode 1
}

if ($deleteData) {
    try {
        foreach ($preparedDataTarget in $preparedDataTargets) {
            $preparedDataTarget.Lease.DeleteFiles()
        }
        foreach ($preparedDataTarget in $preparedDataTargets) {
            Close-OpenSreRetiredTargetGuards `
                -RetiredTarget $preparedDataTarget.RetiredTarget
        }
        foreach ($preparedDataTarget in $preparedDataTargets) {
            $preparedDataTarget.Lease.DeleteDirectories()
            $preparedDataTarget.Lease.DeleteRoot()
        }
    }
    catch {
        $dataFailed = $true
    }
    finally {
        foreach ($preparedDataTarget in $preparedDataTargets) {
            $preparedDataTarget.Lease.Dispose()
        }
        foreach ($dataRetiredTarget in $dataRetiredTargets) {
            Close-OpenSreRetiredTarget -RetiredTarget $dataRetiredTarget
        }
    }
    if (-not $dataFailed -and -not $deleteLockPath -and $cleanupLockPath) {
        $deleteLockPath = $cleanupLockPath
    }
}
if (-not $dataFailed -and
    $deleteLockPath -and
    $null -ne $lockHandle -and
    ($null -eq $managed -or -not (Test-Path -LiteralPath ([string]$managed.app_root)))) {
    try {
        Assert-OpenSreSafeAncestorChain -Path $deleteLockPath
        [OpenSre.CleanupNativePathApi]::MarkDeleteOnClose($lockHandle)
    }
    catch {
        # Preserve the verified lock rather than deleting a path that may have changed.
    }
}
if ($null -ne $lockHandle) {
    $lockHandle.Dispose()
    $lockHandle = $null
}
if ($dataFailed) {
    Exit-OpenSreCleanup -ExitCode 1
}
Exit-OpenSreCleanup -ExitCode 0
