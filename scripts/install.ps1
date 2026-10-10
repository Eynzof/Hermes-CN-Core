# Hermes Agent bootstrap: git checkout + venv + hermes command on PATH.
# Heavy dependencies (tool binaries, browsers, node) are pm's job after
# this: `hermes pm install`. Stage protocol kept for Hermes-Setup:
#   -Manifest             print the stage list as JSON
#   -Stage NAME [-Json]   run one stage
#   -NonInteractive       skip stages that need input
#   -SkipSetup            deprecated alias for -NonInteractive
#   -IncludeDesktop       add the desktop build stage
#   -ProtocolVersion      print the stage protocol version
#   -SkipBrowser          do not install the browser tools (agent-browser +
#                         Chromium); remembered by later
#                         installs and `hermes update`, undone by
#                         `hermes pm install agent-browser`
#   -SkipComputerUse      do not install the computer-use driver (cua-driver);
#                         remembered the same way, undone by
#                         `hermes pm install cua-driver`
#   -Verbose              stream every child command's output (the default
#                         with redirected output and in CI)
[CmdletBinding(PositionalBinding=$false)]
param(
    [string]$Branch = "main",
    [string]$Commit = "",
    [string]$HermesHome = $(if ($env:HERMES_HOME) { $env:HERMES_HOME } else { "$env:LOCALAPPDATA\hermes" }),
    [string]$InstallDir = $(if ($env:HERMES_HOME) { "$env:HERMES_HOME\hermes-agent" } else { "$env:LOCALAPPDATA\hermes\hermes-agent" }),
    [switch]$Manifest,
    [string]$Stage,
    [switch]$ProtocolVersion,
    [switch]$NonInteractive,
    # Pre-rework spelling of -NonInteractive, still accepted so install
    # wrappers written against the old switch keep binding (#125350).
    [switch]$SkipSetup,
    [switch]$Json,
    [switch]$IncludeDesktop,
    # Same opt-out as install.sh --skip-browser: PM records it, so later
    # installs and `hermes update` keep the browser tools off until
    # `hermes pm install agent-browser` opts back in.
    [switch]$SkipBrowser,
    [switch]$SkipComputerUse,
    # Print the paths this install would use, as JSON on stdout, and exit
    # without touching anything. The first question on any "installer says a
    # path doesn't exist" report is which paths it actually resolved --
    # especially on profiles Windows exposes through an 8.3 alias.
    #   powershell -File install.ps1 -ShowResolvedPaths
    [switch]$ShowResolvedPaths
)

$ErrorActionPreference = "Stop"

# -SkipSetup is the pre-rework spelling of -NonInteractive.
if ($SkipSetup) { $NonInteractive = $true }

# --- Dot-source guard (part 1: detect) ---------------------------------------
# Tests (and any embedding host) dot-source this file (`. install.ps1`) to get
# at its FUNCTIONS. Only the definitions must enter the caller's session --
# the install itself must never run, not even its side-effectful-looking
# prologue (the 8.3 normalization below rewrites process env vars). Dot-sourced
# files see InvocationName '.'; a real invocation sees the script
# path/expression. The flag is checked before the entry dispatch at the bottom
# (part 2), so dot-sourcing still loads every function definition.
$script:IsDotSourced = $MyInvocation.InvocationName -eq '.'
# `iex (irm .../install.ps1)` runs this text inside the caller's session,
# where `exit` closes their PowerShell window (or ends their script). Only a
# script file (-File, `& .\install.ps1`) owns its process and may exit with a
# code. A scriptblock literal records the file its text was parsed from;
# iex'd text has none. ($MyInvocation.MyCommand.Path is the CALLER's script
# under iex, so it cannot tell the two apart.)
$script:RunAsFile = [bool]{}.File
# $PSBoundParameters inside a FUNCTION refers to the function's own binding,
# so the script's binding is captured here, once, at script scope.
$script:BoundParams = $PSBoundParameters
# Under iex, script scope is the caller's session and outlives a run; start
# each run without the previous run's answer (see Set-LauncherUserPath).
$script:BinDirOnCallerPath = $null
$RepoUrl = if ($env:HERMES_REPO_URL) { $env:HERMES_REPO_URL } else { "https://github.com/NousResearch/hermes-agent.git" }

# --- BEGIN GENERATED: bootstrap pins (scripts/gen-bootstrap-pins.py) ---
# Derived from pm/lock.json. DO NOT EDIT BY HAND:
# run scripts/gen-bootstrap-pins.py after a pin bump.
$script:UvPinVersion = "0.12.3"
$script:UvPinFiles = @{
    "win32-x64" = @{
        Url    = "https://github.com/astral-sh/uv/releases/download/0.12.3/uv-x86_64-pc-windows-msvc.zip"
        MirrorUrl = "https://hermes-assets.nousresearch.com/upstream/sha256/b23350c79e8ad0192b8124af13a0f17e8d4e4549524785e1aef389ae5a06990e"
        Sha256 = "b23350c79e8ad0192b8124af13a0f17e8d4e4549524785e1aef389ae5a06990e"
    }
    "win32-arm64" = @{
        Url    = "https://github.com/astral-sh/uv/releases/download/0.12.3/uv-aarch64-pc-windows-msvc.zip"
        MirrorUrl = "https://hermes-assets.nousresearch.com/upstream/sha256/4343217d668727b8a8eb5cad92389a1d2eeead93c89940d1b955ba1bb15462eb"
        Sha256 = "4343217d668727b8a8eb5cad92389a1d2eeead93c89940d1b955ba1bb15462eb"
    }
}

$script:GitPinVersion = "2.53.0+3"
$script:GitPinFiles = @{
    "win32-x64" = @{
        Url    = "https://github.com/git-for-windows/git/releases/download/v2.53.0.windows.3/PortableGit-2.53.0.3-64-bit.7z.exe"
        MirrorUrl = "https://hermes-assets.nousresearch.com/upstream/sha256/b365da794b1d2225eb24d5f5e09ef7792cfd5fa26c3a3586210280c80dff3a2a"
        Sha256 = "b365da794b1d2225eb24d5f5e09ef7792cfd5fa26c3a3586210280c80dff3a2a"
    }
    "win32-arm64" = @{
        Url    = "https://github.com/git-for-windows/git/releases/download/v2.53.0.windows.3/PortableGit-2.53.0.3-arm64.7z.exe"
        MirrorUrl = "https://hermes-assets.nousresearch.com/upstream/sha256/0db54010054c01f35501cf69e1e32d3710138ecb934d188bd77093afed24300e"
        Sha256 = "0db54010054c01f35501cf69e1e32d3710138ecb934d188bd77093afed24300e"
    }
}
# --- END GENERATED: bootstrap pins ---

# ============================================================================
# 8.3 short-path normalization
# ============================================================================
# Windows generates an 8.3 short alias for a user-profile folder whose name
# contains a space ("First Last" -> FIRST~1.LAS), a dot, or an accented
# character. It can then expose %TEMP%, %TMP%, %LOCALAPPDATA%, %APPDATA% and
# %USERPROFILE% -- plus everything derived from them, including the default
# HERMES_HOME and InstallDir -- in that short form:
#   C:\Users\FIRST~1.LAS\AppData\Local\Temp
# PowerShell's FileSystem provider mishandles the aliased component once it
# reaches a provider cmdlet (Tee-Object -FilePath, Out-File, New-Item,
# Test-Path), throwing "An object at the specified path ... does not exist".
# Expanding every profile-rooted path back to long form once, up front, lets
# every downstream cmdlet and child process see something the provider can
# resolve. Three resolvers, tried in order, because no single one covers every
# host:
#   1. kernel32!GetLongPathNameW -- expands any 8.3 component regardless of
#      locale.
#   2. Scripting.FileSystemObject -- fallback where P/Invoke is blocked.
#   3. Profile-root substitution -- when the volume has 8.3 generation
#      disabled or the alias is stale, neither resolver can expand the name
#      because it no longer maps to anything on disk. The aliased component
#      is always the profile folder itself (everything below it was created
#      long), so swap in a profile root we can prove is long and reattach
#      the tail.
# All three degrade to returning the input untouched, so a host where none
# of them apply -- including non-Windows -- behaves exactly as before.

$script:LongProfileRoot = $null

function Write-PathDiag {
    # Diagnostics for this block go to stderr, never stdout: the stage
    # protocol hands drivers a single line of JSON on stdout and a stray note
    # would break anything parsing it. Suppressed entirely under
    # -ShowResolvedPaths, which is a machine-readable query: Windows
    # PowerShell 5.1 wraps any native-command stderr in a NativeCommandError
    # and folds it back into the caller's own stream, so a child writing here
    # at all is enough to corrupt a 5.1 caller's capture. The JSON already
    # carries everything these lines say.
    param([string]$Message)
    if ($ShowResolvedPaths) { return }
    [Console]::Error.WriteLine("[hermes] $Message")
}

function Get-LongProfileRoot {
    # The user's profile directory in long form, or '' when every source we
    # can reach is itself aliased. Cached: this runs per env var.
    if ($null -ne $script:LongProfileRoot) { return $script:LongProfileRoot }
    $script:LongProfileRoot = ''

    # %USERPROFILE% first: it is what the rest of the install derives from.
    # Then the HOMEDRIVE/HOMEPATH pair, then the profile's parent (C:\Users
    # never carries an alias) plus %USERNAME%, which stays the long account
    # name even when every path is short.
    $envProfile = [Environment]::GetEnvironmentVariable('USERPROFILE')
    $shellProfile = [Environment]::GetFolderPath('UserProfile')
    $candidates = @($envProfile, $shellProfile, "$env:HOMEDRIVE$env:HOMEPATH")
    foreach ($anchor in @($envProfile, $shellProfile)) {
        if ($anchor -and $env:USERNAME) {
            $parent = Split-Path -Parent $anchor.TrimEnd('\', '/')
            if ($parent) { $candidates += (Join-Path $parent $env:USERNAME) }
        }
    }

    foreach ($candidate in $candidates) {
        if ([string]::IsNullOrWhiteSpace($candidate)) { continue }
        # Trailing separators make Split-Path -Parent return the directory
        # itself, which would silently break the ancestry check downstream.
        $candidate = $candidate.TrimEnd('\', '/')
        if (-not $candidate) { continue }
        if ($candidate -match '~\d') { continue }
        try {
            if (Test-Path -LiteralPath $candidate -PathType Container) {
                $script:LongProfileRoot = $candidate
                break
            }
        } catch {
            # Unreadable candidate (denied, malformed): try the next one.
        }
    }

    return $script:LongProfileRoot
}

function Expand-ShortProfileRoot {
    # Rebuild $Path onto a known-long profile root when its aliased component
    # is the profile folder. Returns $Path unchanged when it isn't, so a
    # custom TEMP on another volume (D:\SHORT~1\Temp) is never rewritten.
    param([string]$Path)

    $longRoot = Get-LongProfileRoot
    if (-not $longRoot) { return $Path }
    $longRootParent = Split-Path -Parent $longRoot
    if (-not $longRootParent) { return $Path }

    $node = $Path
    $tail = ''
    while ($node -and ($node -match '~\d')) {
        $leaf = Split-Path -Leaf $node
        $parent = Split-Path -Parent $node
        if (-not $parent) { return $Path }
        if ($leaf -match '~\d') {
            # Candidate profile folder. Only substitute when it sits in the
            # same directory as the real profile (both C:\Users).
            if ($parent -ne $longRootParent) { return $Path }
            if ($tail) { return (Join-Path $longRoot $tail) }
            return $longRoot
        }
        $tail = if ($tail) { Join-Path $leaf $tail } else { $leaf }
        $node = $parent
    }
    return $Path
}

function ConvertTo-LongPath {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return $Path }
    # Only 8.3 short names carry a tilde+digit ("~1"); skip every resolver
    # for ordinary long paths, which is the overwhelmingly common case.
    if ($Path -notmatch '~\d') {
        $script:LastResolver = 'skipped-long-path'
        return $Path
    }

    # 1. kernel32. Compiled on first use only, so a normal profile never pays
    #    the Add-Type cost (this file is re-entered once per install stage).
    try {
        if (-not ([System.Management.Automation.PSTypeName]'HermesInstall.LongPath').Type) {
            Add-Type -Namespace 'HermesInstall' -Name 'LongPath' -MemberDefinition @'
[DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
public static extern int GetLongPathNameW(string lpszShortPath, System.Text.StringBuilder lpszLongPath, int cchBuffer);
'@
        }
        $buffer = New-Object System.Text.StringBuilder 4096
        $length = [HermesInstall.LongPath]::GetLongPathNameW($Path, $buffer, $buffer.Capacity)
        if ($length -gt $buffer.Capacity) {
            $buffer = New-Object System.Text.StringBuilder $length
            $length = [HermesInstall.LongPath]::GetLongPathNameW($Path, $buffer, $buffer.Capacity)
        }
        if ($length -gt 0) {
            $expanded = $buffer.ToString()
            if ($expanded -and $expanded -notmatch '~\d') {
                $script:LastResolver = 'kernel32'
                return $expanded
            }
        }
    } catch {
        # Not Windows, or P/Invoke denied by policy: try the next resolver.
    }

    # 2. COM. Validate the result the same way the kernel32 branch does: this
    #    resolver can report success and still hand back a path that carries
    #    the alias (observed on a windows-latest runner). An unexpanded
    #    result counts as failure and falls through.
    try {
        $fso = New-Object -ComObject Scripting.FileSystemObject
        $resolved = $null
        if ($fso.FolderExists($Path))   { $resolved = $fso.GetFolder($Path).Path }
        elseif ($fso.FileExists($Path)) { $resolved = $fso.GetFile($Path).Path }
        if ($resolved -and $resolved -notmatch '~\d') {
            $script:LastResolver = 'com'
            return $resolved
        }
    } catch {
        # COM unavailable / locked-down host: try the next resolver.
    }

    # 3. The alias resolves to nothing. Rebuild from a long profile root.
    $rebuilt = Expand-ShortProfileRoot $Path
    $script:LastResolver = if ($rebuilt -ne $Path) { 'profile-root' } else { 'none' }
    return $rebuilt
}

function Set-LongProfileEnvVars {
    # Normalize every profile-rooted variable the install reads, not just
    # %TEMP%: the desktop stage derives InstallDir from %LOCALAPPDATA%, and a
    # short root there fails the post-build probe after a successful build.
    # Returns $true when anything was rewritten.
    $rewrote = $false
    $script:NormalizedPathRewrites = @{}
    foreach ($name in @('TEMP', 'TMP', 'LOCALAPPDATA', 'APPDATA', 'USERPROFILE')) {
        $current = [Environment]::GetEnvironmentVariable($name)
        if (-not $current) { continue }
        $expanded = ConvertTo-LongPath $current
        if ($expanded -and $expanded -ne $current) {
            Set-Item -Path "Env:$name" -Value $expanded
            $rewrote = $true
            $script:NormalizedPathRewrites[$name] = $expanded
        }
    }
    return $rewrote
}

# ConvertTo-LongPath only assigns $script:LastResolver when a ~\d short path
# actually needs expansion, so an ordinary long profile leaves it unset --
# and the report below reads it unconditionally. 'none' is the resolver's own
# value for "nothing ran".
$script:LastResolver = 'none'
$script:NormalizedPathRewrites = @{}

# (Dot-source guard, prologue side: a dot-source must not rewrite the
# caller's process env, so the normalization prologue runs only on real
# entry. Called from the entry dispatch below, before -ProtocolVersion and
# every other switch, so the resolved paths are always the install's own.)
function Initialize-ResolvedPaths {
    $script:NormalizedProfilePaths = Set-LongProfileEnvVars

    # Re-derive the install paths now that the env vars behind their defaults
    # are long. An explicitly passed -HermesHome / -InstallDir is normalized
    # in place rather than replaced, so a caller's choice is never
    # overwritten by a default. The script's own $PSBoundParameters was
    # captured at script scope ($script:BoundParams) because a function body
    # sees its own binding, not the script's.
    $resolvedHome = if ($script:BoundParams.ContainsKey('HermesHome')) {
        ConvertTo-LongPath $HermesHome
# Captured here, where the values are final, and emitted from the entry-point
# dispatch at the bottom (alongside -ProtocolVersion / -Manifest) so
# -ShowResolvedPaths exits before any stage runs.
#
# The report goes to STDOUT as JSON: on Windows a child's stderr does not
# reliably reach a parent process -- three separate capture mechanisms each came
# back empty on a windows-latest runner while stdout arrived intact -- and the
# first question on any "installer says a path doesn't exist" report is which
# paths it actually resolved.
$script:ResolvedPathReport = @{
    long_profile_root = (Get-LongProfileRoot)
    normalized        = $script:NormalizedPathRewrites
    resolver          = $script:LastResolver
    temp              = $env:TEMP
    hermes_home       = $HermesHome
    install_dir       = $InstallDir
}

# ============================================================================
# Configuration
# ============================================================================

$RepoUrlSsh = "git@github.com:NousResearch/hermes-agent.git"
$RepoUrlHttps = "https://github.com/NousResearch/hermes-agent.git"
$PythonVersion = "3.14"
# Minor versions the installer accepts when the requested $PythonVersion isn't
# available, in preference order. Only checkout-private uv-managed interpreters
# are eligible. Single source of truth shared by Test-Python's fallback and
# Resolve-AvailablePythonVersion.
$PythonFallbackVersions = @("3.12", "3.13", "3.10")
$PythonFindTimeoutMs = 30000
$NodeVersion = "22"
# The npm range the root package.json pins in `engines.npm`.  A constant rather
# than a manifest read like the POSIX side does: Test-Node runs BEFORE the repo
# is cloned, so there is usually no package.json on disk yet (and none at all
# when install.ps1 is piped straight from the web). Keep this fallback in sync
# with package.json; Get-NpmRange prefers the manifest once a checkout exists.
$NpmRange = "<11.10.0 || >=11.17.0"

# Stage-protocol version.  Bumped only for genuinely breaking changes to the
# manifest schema, stage-name set semantics, or stdout JSON shape.  Adding a
# new stage does NOT bump this -- drivers iterate the manifest dynamically.
$InstallStageProtocolVersion = 1

# Hermes-managed external tools directory.  We keep known-good copies here so
# broken system shims/symlinks on PATH cannot brick the installer or runtime.
$script:ManagedToolsDir = Join-Path $HermesHome "tools"

# Hermes-managed ripgrep binary.
$script:ManagedRg = Join-Path $script:ManagedToolsDir "rg.exe"

# Hermes-managed rtk (reasoning toolkit) binary.  Used to collapse repeated
# command output lines, reducing token consumption.
$script:ManagedRtk = Join-Path $script:ManagedToolsDir "rtk.exe"

# Hermes-managed Coreutils directory.  Used on Windows to provide POSIX CLI tools
# (cat, cp, mv, ls, sort, wc, whoami, id, ...) that are commonly referenced in
# shell scripts and skills.  Managed by install_coreutils.py.
$script:ManagedCoreutilsDir = Join-Path $script:ManagedToolsDir "coreutils"
$script:ManagedCat          = Join-Path $script:ManagedCoreutilsDir "bin\cat.exe"

# ============================================================================
# Helper functions

# Return the real OS processor architecture as a lowercase string suitable for
# Node.js / electron download URL slugs: "arm64", "x64", or "x86".
#
# Why not just trust [Environment]::Is64BitOperatingSystem or
# [RuntimeInformation]::OSArchitecture?  On Windows on ARM, when this script
# is invoked from Windows PowerShell 5.1 (the default `powershell.exe`) or
# any x64 PowerShell host, the process runs under Prism x64 emulation and
# BOTH of those APIs report `X64` -- they describe the emulated view, not
# the real OS.  We've seen this concretely on Snapdragon X1 hardware: an
# ARM64-based Surface Laptop returns OSArchitecture=X64 from an emulated
# PowerShell session.
#
# Win32_Processor.Architecture is invariant to emulation.  Values:
#   0=x86, 5=ARM, 9=AMD64/x64, 12=ARM64.  We fall back to
#   PROCESSOR_ARCHITEW6432 (set on WoW64 with the real OS arch) and then
#   PROCESSOR_ARCHITECTURE so we still produce a sensible answer if CIM
#   isn't available (locked-down WMI, container, etc.).
function Get-WindowsArch {
    try {
        $proc = Get-CimInstance -ClassName Win32_Processor -ErrorAction Stop |
            Select-Object -First 1
        switch ([int]$proc.Architecture) {
            12 { return "arm64" }
            9  { return "x64" }
            0  { return "x86" }
            5  { return "arm" }
        }
    } catch {
        # CIM unavailable -- fall through to env-var path
    }

    $envArch = if ($env:PROCESSOR_ARCHITEW6432) {
        $env:PROCESSOR_ARCHITEW6432
    } else {
        ConvertTo-LongPath $(
            if ($env:HERMES_HOME) { $env:HERMES_HOME } else { "$env:LOCALAPPDATA\hermes" }
        )
    }
    $resolvedDir = if ($script:BoundParams.ContainsKey('InstallDir')) {
        ConvertTo-LongPath $InstallDir
    } else {
        Join-Path $resolvedHome 'hermes-agent'
    }
    # A HermesHome equal to or inside InstallDir puts the pm tool store
    # (<home>\tools) inside the checkout: the repository stage's
    # occupied-directory preflight then refuses every retry after the first
    # run populated it, and `git stash --include-untracked` would sweep the
    # toolchain into the stash. Refuse before anything downloads (#124526)
    # unless HERMES_RUNTIME_DIR parks the store outside the checkout.
    # The store lands in HERMES_RUNTIME_DIR when set (Get-PmStoreRoot), else under HermesHome.
    $cmpStore = if ($env:HERMES_RUNTIME_DIR) { "$env:HERMES_RUNTIME_DIR" } else { "$resolvedHome" }
    $cmpStore = $cmpStore.TrimEnd('\', '/')
    $cmpDir = "$resolvedDir".TrimEnd('\', '/')
    $dirPrefix = $cmpDir + [IO.Path]::DirectorySeparatorChar
    $storeInside = $cmpStore -eq $cmpDir -or
        $cmpStore.StartsWith($dirPrefix, [StringComparison]::OrdinalIgnoreCase)
    if ($storeInside) {
        Fail "HermesHome ($resolvedHome) cannot be the install directory or live inside it ($resolvedDir): the tool store would land inside the checkout. Use a separate -HermesHome, or point HERMES_RUNTIME_DIR outside -InstallDir."
    }
    # The param() variables live in the CALLER's scope, which is the script
    # scope only under -File. Under the documented
    # `& ([scriptblock]::Create((irm ...)))` install they live in the
    # scriptblock's scope and `$script:` names the caller's session instead,
    # so `$script:HermesHome` read '' and every stage's bare $HermesHome kept
    # the un-normalized value. Scope 1 is where param() bound in every mode
    # (-File, scriptblock, dot-source).
    Set-Variable -Scope 1 -Name HermesHome -Value $resolvedHome
    Set-Variable -Scope 1 -Name InstallDir -Value $resolvedDir
    $env:HERMES_HOME = $resolvedHome

    # Captured here, where the values are final. The report goes to STDOUT as
    # JSON under -ShowResolvedPaths: on Windows a child's stderr does not
    # reliably reach a parent process, and the first question on any
    # "installer says a path doesn't exist" report is which paths it
    # actually resolved.
    $script:ResolvedPathReport = @{
        long_profile_root = (Get-LongProfileRoot)
        normalized        = $script:NormalizedPathRewrites
        resolver          = $script:LastResolver
        temp              = $env:TEMP
        hermes_home       = $resolvedHome
        install_dir       = $resolvedDir
    }
}

# Resolve the pm store root (same resolution as pm's store_root()):
# $env:HERMES_RUNTIME_DIR wins, else <HermesHome>\tools.
function Get-PmStoreRoot {
    if ($env:HERMES_RUNTIME_DIR) { return $env:HERMES_RUNTIME_DIR }
    return (Join-Path $HermesHome "tools")
}

# The MACHINE's architecture (registry PROCESSOR_ARCHITECTURE), not the
# interpreter's — an x64 powershell on Windows-on-ARM must stage arm64.
function Get-WindowsArch {
    $machineArch = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment' -ErrorAction SilentlyContinue).PROCESSOR_ARCHITECTURE
    if ($machineArch -eq 'ARM64') { return 'arm64' }
    return 'x64'
}

# Mirror bytes must match the same pin; corruption is never a cache miss.
function Invoke-VerifiedDownload {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][string]$Sha256,
        [Parameter(Mandatory = $true)][string]$OutFile,
        [string]$MirrorUrl = ""
    )
    $urls = @($Url)
    if ($MirrorUrl -and $MirrorUrl -ne $Url) { $urls += $MirrorUrl }
    $httpFailure = ""
    foreach ($candidate in $urls) {
        try {
            Invoke-DownloadWithProgress -Uri $candidate -OutFile $OutFile
        } catch {
            $errorType = $_.Exception.GetType().FullName
            if ($_.Exception -is [System.Net.WebException]) {
                # Windows PowerShell 5.1: DNS/connect/HTTP failures.
                if ($_.Exception.Status -in @('TrustFailure', 'SecureChannelFailure')) { throw }
            } elseif ($errorType -eq 'System.Net.Http.HttpRequestException') {
                # pwsh 7: DNS/connect failures. A TLS trust failure arrives
                # with an AuthenticationException inside and is never a routing
                # problem. (Matched by name: 5.1 may not load System.Net.Http.)
                $inner = $_.Exception.InnerException
                if ($inner -and $inner.GetType().FullName -eq 'System.Security.Authentication.AuthenticationException') { throw }
            } elseif ($errorType -ne 'Microsoft.PowerShell.Commands.HttpResponseException') {
                throw
            }
            $httpFailure = $_.Exception.Message
            continue
        }
        $digest = (Get-FileHash -Path $OutFile -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($digest -eq $Sha256.ToLowerInvariant()) { return }
        Remove-Item -Path $OutFile -Force -ErrorAction SilentlyContinue
        # Wrong bytes = tampering or a corrupt mirror, not a routing problem.
        Fail "download digest mismatch for $candidate (expected $Sha256, got $digest)" download_digest_mismatch
    }
    $tried = $urls -join " or "
    if ($httpFailure) {
        Fail "failed to download from $tried : $httpFailure" download_failed
    }
    Fail "failed to download from $tried" download_failed
}

# Best-effort: how big is $Uri, per the server? Returns 0 when the server
# doesn't say (missing/blocked Content-Length on a redirect chain), never
# throws -- a failed probe here must fall back to an indeterminate bar, not
# abort a download that Invoke-WebRequest itself would still complete.
function Get-RemoteContentLength([string]$Uri) {
    try {
        $resp = Invoke-WebRequest -Uri $Uri -Method Head -UseBasicParsing -ErrorAction Stop
        $len = $resp.Headers['Content-Length']
        if ($len) { return [long]([string]$len -split ',' | Select-Object -First 1) }
    } catch {
        # HEAD unsupported / blocked: fall back silently.
    }
    return 0
}

# Runs the same Invoke-WebRequest call the direct version made, on a
# separate runspace, so the main thread can drive Write-Progress off
# $OutFile's size on disk while it downloads. This preserves the exact
# exception TYPE Invoke-VerifiedDownload's catch block dispatches on for
# both PS 5.1 and pwsh 7 -- EndInvoke's terminating error is unwrapped via
# .InnerException before it is rethrown, so the caller sees the same
# WebException / HttpRequestException / HttpResponseException it would
# have gotten from a direct call.
function Invoke-DownloadWithProgress {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$OutFile
    )
    if (Test-Path $OutFile) { Remove-Item -Path $OutFile -Force -ErrorAction SilentlyContinue }

    $totalBytes = Get-RemoteContentLength $Uri
    $activity = "Downloading $(Split-Path -Leaf $Uri)"

    $ps = [powershell]::Create()
    $ps.AddScript({
        param($Uri, $OutFile)
        # Invoke-WebRequest's own progress bar fights ours (and is a known
        # throughput killer); we're rendering progress from outside, so
        # turn it off inside the runspace.
        $ProgressPreference = 'SilentlyContinue'
        Invoke-WebRequest -Uri $Uri -OutFile $OutFile -UseBasicParsing
    }).AddArgument($Uri).AddArgument($OutFile) | Out-Null

    $handle = $ps.BeginInvoke()
    try {
        while (-not $handle.IsCompleted) {
            Start-Sleep -Milliseconds 200
            $haveBytes = if (Test-Path $OutFile) { (Get-Item $OutFile).Length } else { 0 }
            if ($totalBytes -gt 0) {
                $pct = [math]::Min(100, [math]::Round(($haveBytes / $totalBytes) * 100))
                $haveMb = [math]::Round($haveBytes / 1MB, 1)
                $totalMb = [math]::Round($totalBytes / 1MB, 1)
                Write-Progress -Activity $activity -Status "$haveMb MB / $totalMb MB" -PercentComplete $pct
            } else {
                # Unknown size: PercentComplete -1 draws an indeterminate/marquee
                # bar in hosts that support it, and is simply ignored elsewhere.
                $haveMb = [math]::Round($haveBytes / 1MB, 1)
                Write-Progress -Activity $activity -Status "$haveMb MB (size unknown)" -PercentComplete -1
            }
        }
        $ps.EndInvoke($handle) | Out-Null
        # Invoke-WebRequest's HTTP/DNS failures are non-terminating inside the
        # runspace: EndInvoke returns normally and the error sits in the stream.
        $streamError = if ($ps.Streams.Error.Count) { $ps.Streams.Error[0].Exception } else { $null }
    } catch {
        $inner = $_.Exception.InnerException
        if ($inner) { throw $inner } else { throw }
    } finally {
        Write-Progress -Activity $activity -Completed
        $ps.Dispose()
    }
    # Rethrown as-is (outside the unwrapping catch) so the caller classifies it
    # and tries the next candidate.
    if ($streamError) { throw $streamError }
}

# Provision uv for this host from the pinned pm/lock.json artifact. Stages
# the EXACT artifact pm itself uses into the same store slot
# (<store>\uv-<version>-<target>\), sha256-verified, so pm adopts the same
# bytes — no astral-latest, no irm|iex. Returns the uv.exe path.
function Get-Uv {
    # Always the pinned artifact, never a uv already on PATH: Hermes runs only
    # its own packaged toolchain.
    $target = "win32-$(Get-WindowsArch)"
    $pin = $script:UvPinFiles[$target]
    if (-not $pin) {
        Fail "no pinned uv artifact for $target; Hermes does not support this host" unsupported_platform
    }
    $entry = Join-Path (Get-PmStoreRoot) "uv-$($script:UvPinVersion)-$target"
    $uvExe = Join-Path $entry "uv.exe"
    if (Test-Path $uvExe) {
        if (Test-UvAtLeastPin $uvExe) { return $uvExe }
        Log "cached pinned uv does not run; downloading our own copy"
        Remove-Item -Path $uvExe -Force
    }
    Log "downloading uv $($script:UvPinVersion) ($target)"
    $tmpDir = Join-Path ([IO.Path]::GetTempPath()) "hermes-uv-bootstrap-$PID"
    try {
        New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null
        $zipPath = Join-Path $tmpDir "uv.zip"
        Invoke-VerifiedDownload -Url $pin.Url -MirrorUrl $pin.MirrorUrl -Sha256 $pin.Sha256 -OutFile $zipPath
        $extractDir = Join-Path $tmpDir "unpacked"
        Expand-Archive -Path $zipPath -DestinationPath $extractDir -Force
        # The zip carries uv.exe (+ uvx.exe) at the root or under one
        # versioned wrapper dir — take whichever layout arrived.
        $found = Get-ChildItem -Path $extractDir -Filter "uv.exe" -Recurse | Select-Object -First 1
        if (-not $found) { Fail "uv.exe not found in the downloaded archive" uv_unusable }
        New-Item -ItemType Directory -Force -Path $entry | Out-Null
        Move-Item -Path $found.FullName -Destination $uvExe -Force
        $uvx = Get-ChildItem -Path $extractDir -Filter "uvx.exe" -Recurse | Select-Object -First 1
        if ($uvx) { Move-Item -Path $uvx.FullName -Destination (Join-Path $entry "uvx.exe") -Force }
    } finally {
        Remove-Item -Path $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (-not (Test-UvAtLeastPin $uvExe)) { Fail "pinned uv staged but does not run on this host" uv_unusable }
    return $uvExe
}

# Provision git for this host from the pinned pm/lock.json artifact, into
# the same store slot (<store>\git-<version>-<target>\) pm uses. Returns the
# git.exe path, or $null when no pinned artifact exists for this target.
function Get-PinnedGit {
    $target = "win32-$(Get-WindowsArch)"
    $pin = $script:GitPinFiles[$target]
    if (-not $pin) { return $null }
    $entry = Join-Path (Get-PmStoreRoot) "git-$($script:GitPinVersion)-$target"
    $gitExe = Join-Path $entry "cmd\git.exe"
    if (Test-Path $gitExe) { return $gitExe }
    Log "installing git $($script:GitPinVersion) ($target)"
    $tmpDir = Join-Path ([IO.Path]::GetTempPath()) "hermes-git-bootstrap-$PID"
    try {
        New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null
        $sfxPath = Join-Path $tmpDir "portable-git.7z.exe"
        Invoke-VerifiedDownload -Url $pin.Url -MirrorUrl $pin.MirrorUrl -Sha256 $pin.Sha256 -OutFile $sfxPath
        $extractDir = Join-Path $tmpDir "unpacked"
        New-Item -ItemType Directory -Force -Path $extractDir | Out-Null
        # PortableGit's self-extracting 7z carries its own extractor: stock
        # Windows 10 tar.exe has no bzip2 ("unable to run program bzip2 -d").
        Unblock-File -Path $sfxPath -ErrorAction SilentlyContinue
        # A GUI-subsystem exe: `&` would not wait for it. Under -y it reports
        # nothing, so the exit code is all there is. Bound the wait like pm's
        # timeout; kill the whole tree, since the stub's post-install children
        # would otherwise keep $tmpDir held past the cleanup below.
        $sfx = [System.Diagnostics.Process]::Start($sfxPath, "-o`"$extractDir`" -y")
        if (-not $sfx.WaitForExit(600000)) {
            Invoke-Native { taskkill.exe /T /F /PID $sfx.Id 2>&1 | Out-Null }
            $sfx.WaitForExit()
            Fail "pinned git self-extractor timed out after 600s" git_extract_failed
        }
        if ($sfx.ExitCode) {
            Fail "pinned git self-extractor exited $($sfx.ExitCode) (it reports nothing under -y; usual causes: disk full, path-length limit, antivirus lock)" git_extract_failed
        }
        if (-not (Test-Path (Join-Path $extractDir "cmd\git.exe"))) { Fail "git.exe not found in the downloaded archive" git_extract_failed }
        if (Test-Path $entry) { Remove-Item -Recurse -Force $entry }
        # Prerequisites run first, so on a fresh host the store root does not
        # exist yet; Move-Item never creates the destination's parent.
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $entry) | Out-Null
        Move-Item $extractDir $entry
    } finally {
        Remove-Item -Path $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    return $gitExe
}

# Each -Stage is a new PowerShell process. Restore the pinned pm store Git
# PATH in every stage that invokes git; never inherit an unpinned system Git.
function Ensure-Git {
    $g = Get-PinnedGit
    if (-not $g) { return $false }
    # The same dirs pm's git package env() composes.
    $gitEntry = Split-Path (Split-Path $g -Parent) -Parent
    $env:Path = "$gitEntry\cmd;$gitEntry\usr\bin;$env:Path"
    return $true
}

# The pre-pm installer's line style. ASCII glyphs: Windows PowerShell 5.1
# reads a BOM-less script as the ANSI code page, so arrows would mojibake.
function Log([string]$msg) { Write-Host "-> $msg" -ForegroundColor Cyan }
function Write-Ok([string]$msg) { Write-Host "[OK] $msg" -ForegroundColor Green }
function Write-Warn([string]$msg) { Write-Host "[!] $msg" -ForegroundColor Yellow }
function Write-Err([string]$msg) { Write-Host "[X] $msg" -ForegroundColor Red }

function Write-Banner {
    Write-Host ""
    Write-Host "+---------------------------------------------------------+" -ForegroundColor Magenta
    Write-Host "|             * Hermes Agent Installer                    |" -ForegroundColor Magenta
    Write-Host "+---------------------------------------------------------+" -ForegroundColor Magenta
    Write-Host "|  An open source AI agent by Nous Research.              |" -ForegroundColor Magenta
    Write-Host "+---------------------------------------------------------+" -ForegroundColor Magenta
    Write-Host ""
}

# Windows PowerShell 5.1 turns a native command's stderr into an ErrorRecord
# whenever that stream is redirected inside PowerShell (`2>$null`, `2>&1`),
# and under $ErrorActionPreference = "Stop" the record terminates the script
# -- even when the tool exits 0, or the caller meant to tolerate its failure.
# Native calls run through here; the exit code stays in $LASTEXITCODE for the
# caller to judge. (The relaxed preference lives in this function's scope and
# reaches only the block invoked from it.)
function Invoke-Native([scriptblock]$Command, [switch]$Utf8Output) {
    $ErrorActionPreference = 'Continue'
    if (-not $Utf8Output) { & $Command; return }

    # Windows PowerShell 5.1 decodes captured native stdout using the console
    # code page. uv prints UTF-8 even when that code page is CP936/CP437.
    # Scope the decoder to UTF-8 producers; other tools and an iex caller
    # must retain their original encoding. $OutputEncoding controls stdin,
    # so changing it would not repair paths returned by `uv python find`.
    $previousNativeOutputEncoding = [Console]::OutputEncoding
    try {
        [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
        & $Command
    } finally {
        [Console]::OutputEncoding = $previousNativeOutputEncoding
    }
}

# Interactive runs collapse child-process output (git, uv, pm, the builds)
# into one status line. CI, -Verbose and redirected output -- the
# Hermes-Setup -Json driver, E2E transcripts -- keep the full stream those
# readers parse.
function Test-QuietOutput {
    if ($env:CI -or $env:GITHUB_ACTIONS -or $env:HERMES_INSTALL_VERBOSE) { return $false }
    if ($VerbosePreference -ne 'SilentlyContinue') { return $false }
    try { return -not [Console]::IsOutputRedirected } catch { return $false }
}

function Write-StatusLine([string]$Text, [int]$Width) {
    $line = "  $Text"
    if ($line.Length -ge $Width) { $line = $line.Substring(0, $Width - 1) }
    Write-Host ("`r" + $line.PadRight($Width - 1)) -NoNewline -ForegroundColor DarkGray
}

# Run a native command block like Invoke-Native: $LASTEXITCODE stays the
# caller's to judge. Quiet mode shows $StatusLabel with the block's newest
# output line rewritten in place, appends everything to the install log and,
# on failure, prints the tail and the log path (-MayFail: the caller handles
# the failure, so no report). Otherwise the label is logged and the output
# streams to the host -- never to the pipeline, so a function returning a
# value can call this. The block resolves its variables through this
# function's scope, so locals here avoid the names call sites use.
function Invoke-Logged {
    param([string]$StatusLabel, [scriptblock]$NativeBlock, [switch]$MayFail)
    $logWriter = $null
    if (Test-QuietOutput) {
        $logPath = Join-Path (Join-Path $HermesHome 'logs') 'install.log'
        try {
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $logPath) | Out-Null
            $logWriter = New-Object System.IO.StreamWriter($logPath, $true, (New-Object System.Text.UTF8Encoding($false)))
        } catch {
            # An unwritable log must not stop the install: stream instead.
            $logWriter = $null
        }
    }
    if (-not $logWriter) {
        Log $StatusLabel
        Invoke-Native $NativeBlock | Out-Host
        return
    }
    $columns = 80
    try { $columns = [Math]::Max(20, $Host.UI.RawUI.WindowSize.Width) } catch { $columns = 80 }
    $recentLines = New-Object 'System.Collections.Generic.Queue[string]'
    try {
        $logWriter.WriteLine("==> $StatusLabel ($((Get-Date).ToUniversalTime().ToString('s'))Z)")
        Write-StatusLine $StatusLabel $columns
        Invoke-Native { & $NativeBlock 2>&1 } | ForEach-Object {
            $outputLine = "$_".TrimEnd("`r")
            $logWriter.WriteLine($outputLine)
            $recentLines.Enqueue($outputLine)
            if ($recentLines.Count -gt 20) { [void]$recentLines.Dequeue() }
            # git and uv redraw progress with bare CRs; show the newest.
            $newest = ($outputLine -split "`r")[-1].Trim()
            if ($newest) { Write-StatusLine "${StatusLabel}: $newest" $columns }
        }
        $exitCode = $LASTEXITCODE
    } finally {
        $logWriter.Dispose()
        Write-Host ("`r" + (' ' * ($columns - 1)) + "`r") -NoNewline
    }
    if ($exitCode -and -not $MayFail) {
        Write-Err "$StatusLabel failed (exit $exitCode). Last output:"
        foreach ($recent in $recentLines) { Write-Host "    $recent" }
        Write-Host "    full log: $logPath"
    }
    $global:LASTEXITCODE = $exitCode
}

# Does the uv at $Path run, and is it at least the pinned version? The
# bootstrap passes flags an older uv lacks (`python install --no-bin` arrived
# in 0.7), and a broken shim can exist without running.
function Test-UvAtLeastPin([string]$Path) {
    $global:LASTEXITCODE = 0
    $out = Invoke-Native { & $Path --version 2>$null }
    if ($LASTEXITCODE -or -not $out) { return $false }
    $have = ("$out".Trim() -split '\s+')[1] -replace '[^0-9.].*$', ''
    try { return ([version]$have -ge [version]$script:UvPinVersion) } catch { return $false }
}
function Fail([string]$msg, [string]$Class = "other") {
    # Throw, never exit: the entry points below own reporting and the exit
    # code, and the stage dispatcher's catch emits the -Json failure frame.
    # $Class: closed failure class for the local install receipt.
    $script:FailureClass = $Class
    throw $msg
}

# One local receipt per full-ladder run, under the profile's shared-metrics dir.
# Only closed tokens and two timestamps (never the error text, paths or URLs);
# the installer never sends anything. Hermes reports it as hermes.install.run on
# a later start only while shared metrics collection is on, and deletes it
# unreported when collection is off. Best effort: never fails the install.
function Write-InstallReceipt([string]$Outcome, [string]$FailedStage, [string]$Class) {
    if ($script:InstallReceiptWritten) { return }
    $script:InstallReceiptWritten = $true
    $tmp = $null  # never a caller's $tmp (dynamic scope) in the catch below
    try {
        $dir = Join-Path $HermesHome "telemetry\shared_metrics\pending_installs"
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        $id = [guid]::NewGuid().ToString("N")
        $json = '{{"id":"{0}","installer":"install_ps1","outcome":"{1}","failed_stage":"{2}","failure_class":"{3}","started_at":{4},"finished_at":{5}}}' -f `
            $id, $Outcome, ($FailedStage -replace '-', '_'), $Class, $script:InstallStarted, [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
        $tmp = Join-Path $dir ".$id.tmp"
        [IO.File]::WriteAllText($tmp, $json, (New-Object System.Text.UTF8Encoding $false))
        Move-Item -LiteralPath $tmp -Destination (Join-Path $dir "$id.json") -Force
    } catch {
        # A full disk leaves a partial temp file the reader never looks at (it skips dotfiles).
        if ($tmp) { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
    }
}

# The receipt class for a ladder stage that threw. A native child Ctrl-C ended
# (130, or Windows STATUS_CONTROL_C_EXIT 0xC000013A) is a user abort even when
# the host did not stop the pipeline first; it beats the stage's own class.
# (A Ctrl-C the host saw stops the pipeline: catch is skipped and finally
# records `interrupted`.)
function Get-StageFailureClass {
    if ($global:LASTEXITCODE -in @(130, -1073741510, 3221225786)) { return "interrupted" }
    if ($script:FailureClass) { return $script:FailureClass }
    return "other"
}

function Emit-Frame([bool]$ok, [string]$name, [bool]$skipped, [string]$reason = "") {
    $frame = [ordered]@{ ok = $ok; stage = $name; skipped = $skipped }
    if ($reason) { $frame.reason = $reason }
    $frame | ConvertTo-Json -Compress | Write-Output
}

$ProductTitle = if ($IncludeDesktop) { "Install command and app + desktop" } else { "Install command and app" }
$Stages = @(
    @{ name = "prerequisites"; title = "System prerequisites"; category = "runtime"; needs_user_input = $false },
    @{ name = "repository"; title = "Download Hermes Agent"; category = "runtime"; needs_user_input = $false },
    @{ name = "venv"; title = "Create Python environment"; category = "runtime"; needs_user_input = $false },
    @{ name = "python-deps"; title = "Install Python dependencies"; category = "runtime"; needs_user_input = $false },
    @{ name = "config"; title = "Prepare config and skills"; category = "configuration"; needs_user_input = $false },
    # The shared completion tail -- the same call `hermes update` makes -- so
    # the manifest and the run cannot disagree. -IncludeDesktop selects the
    # desktop product inside this stage instead of adding a second build stage.
    @{ name = "products"; title = $ProductTitle; category = "runtime"; needs_user_input = $false },
    @{ name = "setup"; title = "Configure API keys and settings"; category = "configuration"; needs_user_input = $true },
    @{ name = "gateway"; title = "Configure gateway service"; category = "configuration"; needs_user_input = $true }
)
$Stages += @{ name = "complete"; title = "Finish install"; category = "runtime"; needs_user_input = $false }
function Stage-Prerequisites {
    if (-not (Ensure-Git)) {
        Fail "no pinned Git artifact for this Windows architecture" unsupported_platform
    }
    Write-Ok "prerequisites ok (git)"
}

# A treeless checkout must never write a commit-graph: over a graph with changed-path
# data that lazy-fetches the trees of every unseen commit, in a loop (#127711).
# gc.auto stays on: `hermes update` folds lazy-fetch packs with `gc --auto`.
function Disable-TreelessGraphWrites([string]$Dir) {
    foreach ($key in 'maintenance.commit-graph.enabled', 'gc.writeCommitGraph', 'fetch.writeCommitGraph') {
        Invoke-Native { git -C $Dir config $key false } | Out-Null
        if ($LASTEXITCODE) { Write-Warn "could not set $key in $Dir" }
    }
}

function Stage-Repository {
    $pinned = $false
    # Refuse an occupied non-checkout before provisioning Git. This check
    # needs no tool download and must not overwrite a user's existing files.
    if (-not (Test-Path (Join-Path $InstallDir ".git")) -and (Test-Path -LiteralPath $InstallDir)) {
        $item = Get-Item -LiteralPath $InstallDir -Force
        $empty = $item.PSIsContainer -and -not $item.LinkType -and -not (Get-ChildItem -LiteralPath $InstallDir -Force | Select-Object -First 1)
        if (-not $empty) {
            Fail "$InstallDir exists and is not a Hermes git checkout. Move it aside, or install elsewhere with -InstallDir <path>." dir_not_checkout
        }
    }
    if (-not (Ensure-Git)) { Fail "no pinned Git artifact for this Windows architecture" unsupported_platform }
    # An interrupted clone from an older installer can leave a .git with no
    # initial commit, where stash/checkout abort ("You do not have the initial
    # commit yet", #40998). Move it aside -- never delete it, it may hold
    # something the user wants -- and clone fresh below.
    if (Test-Path (Join-Path $InstallDir ".git")) {
        Invoke-Native { git -C $InstallDir rev-parse --verify HEAD 2>$null } | Out-Null
        if ($LASTEXITCODE) {
            $broken = "$InstallDir.broken-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
            Write-Warn "$InstallDir has no commits (interrupted clone); moving it aside to $broken"
            Move-Item -LiteralPath $InstallDir -Destination $broken
        }
    }
    if (Test-Path (Join-Path $InstallDir ".git")) {
        Log "Updating $InstallDir ($Branch)"
        # An explicit HERMES_REPO_URL names the source for reruns too, not
        # just the first clone.
        if ($env:HERMES_REPO_URL) {
            Invoke-Native { git -C $InstallDir remote set-url origin $RepoUrl }
            if ($LASTEXITCODE) { Fail "cannot point origin at $RepoUrl" git_fetch_failed }
        }
        # Explicit refspec: a tag-pinned --single-branch checkout from an older installer maps only
        # the tag, so a by-name fetch never writes the origin/$Branch used below (#125112).
        # git 2.53+ aborts fetches into a partial clone whose packs lack a .promisor marker
        # (#124272), and an install stuck there never fetches the updater that heals it.
        # Marking is idempotent and never rewrites objects.
        $promisor = Invoke-Native { git -C $InstallDir config --bool --get remote.origin.promisor }
        $packDir = Join-Path $InstallDir '.git\objects\pack'
        if ("$promisor".Trim() -eq 'true' -and (Test-Path -LiteralPath $packDir)) {
            Get-ChildItem -LiteralPath $packDir -Filter 'pack-*.pack' | ForEach-Object {
                $marker = [IO.Path]::ChangeExtension($_.FullName, '.promisor')
                if (-not (Test-Path -LiteralPath $marker)) {
                    try { New-Item -ItemType File -Path $marker | Out-Null }
                    catch { Write-Warn "could not mark $marker as a partial-clone pack" }
                }
            }
            Disable-TreelessGraphWrites $InstallDir
            # A treeless (tree:0) checkout from a late-September installer downloads whole
            # directory snapshots again on every history walk (#129514). Fetch its trees once; the
            # new filter is recorded only after that succeeds, so `hermes update` retries otherwise.
            $partialFilter = Invoke-Native { git -C $InstallDir config --get remote.origin.partialclonefilter }
            if ("$partialFilter".Trim() -eq 'tree:0') {
                Invoke-Logged -MayFail "Fetching directory history once (treeless checkout)" { git -C $InstallDir -c gc.auto=0 -c maintenance.auto=false fetch --refetch --filter=blob:none origin "+refs/heads/${Branch}:refs/remotes/origin/${Branch}" }
                if ($LASTEXITCODE) {
                    Write-Warn "could not fetch the directory history; the next hermes update retries it"
                } else {
                    Invoke-Native { git -C $InstallDir config remote.origin.partialclonefilter blob:none }
                    if ($LASTEXITCODE) { Write-Warn "could not record the blobless filter in $InstallDir" }
                }
            }
        }
        Invoke-Logged "Fetching origin/$Branch" { git -C $InstallDir fetch origin "+refs/heads/${Branch}:refs/remotes/origin/${Branch}" }
        if ($LASTEXITCODE) { Fail "git fetch failed" git_fetch_failed }
        $stamp = (Get-Date -Format 'yyyyMMdd-HHmmss')
        # Park local work BEFORE switching branches: checkout refuses a dirty
        # tree that conflicts, and the reset below would discard it. Work that
        # cannot be parked stops the install -- never overwrite it.
        if (Invoke-Native { git -C $InstallDir status --porcelain }) {
            # An interrupted update can leave unmerged index entries, where
            # stash aborts ("could not write index"). Dropping only the
            # index-level conflict state keeps the working-tree changes for
            # the stash below (#4735).
            if (Invoke-Native { git -C $InstallDir ls-files --unmerged }) {
                Write-Warn "clearing unmerged index entries from a previous conflict"
                Invoke-Native { git -C $InstallDir reset -q }
                if ($LASTEXITCODE) { Fail "cannot clear the unmerged index in $InstallDir" local_changes_blocked }
            }
            Invoke-Logged "Stashing local changes" { git -C $InstallDir stash push --include-untracked -m "hermes-install-autostash-$stamp" }
            if ($LASTEXITCODE) { Fail "could not stash local changes in $InstallDir; commit or move them aside, then rerun" local_changes_blocked }
            Write-Warn "local changes stashed as hermes-install-autostash-$stamp"
        }
        # checkout's branch guess only sees remote refs the refspec maps, so a narrow checkout
        # (detached at its tag, no local branch) gets the branch created at the fetched tip.
        Invoke-Native { git -C $InstallDir show-ref --verify --quiet "refs/heads/$Branch" }
        if ($LASTEXITCODE) {
            Invoke-Logged "Checking out $Branch" { git -C $InstallDir checkout -b $Branch "origin/$Branch" }
        } else {
            Invoke-Logged "Checking out $Branch" { git -C $InstallDir checkout $Branch }
        }
        if ($LASTEXITCODE) { Fail "git checkout failed" git_checkout_failed }
        # --no-stat: across a large gap (v2026.7.1 -> today is ~27k lines) the
        # diffstat arrives as one burst. Hermes-Setup.exe forwards every line
        # to its window as a separate event; the burst overflows the Windows
        # posted-message queue (10k), events drop, and the installer's Launch
        # button can then hang on "Launching" forever.
        Invoke-Logged -MayFail "Fast-forwarding to origin/$Branch" { git -C $InstallDir merge --ff-only --no-stat "origin/$Branch" }
        if ($LASTEXITCODE) {
            # A release cut off the main line, a force-pushed remote, or the
            # user's own commits cannot fast-forward. Every stage below reads
            # files only the new tree has (pm/), so an install left on the old
            # tree cannot finish -- match the remote the way `hermes update`
            # does, after parking the old tip. Mirrors scripts/install.sh.
            # Keep commits absent from origin in the updater's rescue namespace.
            $droppedText = (Invoke-Native { git -C $InstallDir rev-list --count "origin/$Branch..HEAD" 2>$null })
            if ($LASTEXITCODE) { Fail "cannot count commits before reset" git_reset_failed }
            [long]$dropped = 0
            if (-not [long]::TryParse("$droppedText".Trim(), [ref]$dropped)) { Fail "cannot count commits before reset" git_reset_failed }
            if ($dropped -gt 0) {
                Invoke-Native { git -C $InstallDir merge-base HEAD "origin/$Branch" 2>$null } | Out-Null
                $rescueKind = if ($LASTEXITCODE -eq 0) { 'diverged' } else { 'orphan' }
                $prior = (Invoke-Native { git -C $InstallDir rev-parse --short=12 HEAD 2>$null })
                if ($LASTEXITCODE -or -not $prior) { Fail "cannot identify commits before reset" git_reset_failed }
                $rescue = "refs/hermes-update-backups/$rescueKind-$Branch-$stamp-$prior"
                Invoke-Native { git -C $InstallDir update-ref $rescue HEAD 2>$null }
                if ($LASTEXITCODE) { Fail "cannot back up $dropped local commit(s); refusing to reset" git_reset_failed }
                Write-Warn "$dropped commit(s) not on origin/$Branch backed up to $rescue"
                Log "List them with: git -C `"$InstallDir`" log origin/$Branch..$rescue"
            }
            Invoke-Logged "Resetting to origin/$Branch" { git -C $InstallDir reset --hard "origin/$Branch" }
            if ($LASTEXITCODE) { Fail "git reset failed" git_reset_failed }
            Write-Warn "not fast-forwardable; reset to origin/$Branch"
        }
    } else {
        # Moving a clone onto an existing directory would nest it. The
        # preflight above already refused nonempty or linked destinations.
        if (Test-Path -LiteralPath $InstallDir) {
            Remove-Item -LiteralPath $InstallDir -Force
        }
        $parent = Split-Path $InstallDir
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
        # Clone into a sibling staging dir and publish only a complete,
        # materialized checkout: a clone that dies half-way must not leave a
        # .git behind that the next rerun would try to update.
        $staged = Join-Path $parent ".hermes-clone-$PID-$(Get-Random)"
        $tree = Join-Path $staged "tree"
        New-Item -ItemType Directory -Force -Path $staged | Out-Null
        # Phase lines ("Receiving objects: 42%") feed the status line; git
        # prints none to a pipe unless asked.
        $progress = @()
        if (Test-QuietOutput) { $progress = @('--progress') }
        # A pinned install materializes only the pin (below): checking out the
        # branch tip first fetches and rewrites every file the two differ in.
        $checkout = @()
        if ($Commit) { $checkout = @('--no-checkout') }
        try {
            $cloned = $false
            $deferred = $false
            foreach ($attempt in 1..3) {
                # Blobless: every commit, tree and release tag (runtime identity is
                # the nearest reachable release; -Commit pins and branch switches
                # still resolve), file contents fetched on demand. Not treeless:
                # a long-lived treeless checkout re-downloads whole trees on every
                # checkout and history walk (#129712).
                $cloneLabel = "Cloning $RepoUrl ($Branch) into $InstallDir"
                if ($attempt -gt 1) { $cloneLabel += " (attempt $attempt of 3)" }
                Invoke-Logged $cloneLabel { git clone @progress --filter=blob:none @checkout --branch $Branch $RepoUrl $tree }
                if (-not $LASTEXITCODE) { $cloned = $true; break }
                Remove-Item -LiteralPath $tree -Recurse -Force -ErrorAction SilentlyContinue
                if ($attempt -lt 3) { Start-Sleep -Seconds ($attempt * 5) }
            }
            if (-not $cloned) {
                # The checkout step is where throttled downloads die: clone the
                # graph alone, then retry materializing the tree separately.
                Write-Warn "direct clone failed; trying deferred checkout"
                Invoke-Logged "Cloning history" { git clone @progress --filter=blob:none --no-checkout --branch $Branch $RepoUrl $tree }
                if (-not $LASTEXITCODE) { $cloned = $true; $deferred = $true }
            }
            if (-not $cloned) { Fail "git clone failed; no checkout published" git_clone_failed }
            if ($Commit) {
                # Pin before publishing: a pinned or deferred clone has no files yet,
                # and a failure here leaves only the staging dir, which finally removes.
                Invoke-Native { git -C $tree merge-base --is-ancestor $Commit "origin/$Branch" 2>$null }
                if ($LASTEXITCODE) { Fail "commit $Commit is not on branch $Branch" commit_not_on_branch }
            }
            if ($Commit -or $deferred) {
                # The checkout step is where throttled downloads die: retry it alone.
                # A pin detaches HEAD and leaves the branch at its tip, as the published pin did.
                $what = if ($Commit) { $Commit } else { "files" }
                $materialized = $false
                foreach ($attempt in 1..2) {
                    Invoke-Logged "Checking out $what (attempt $attempt of 2)" {
                        if ($Commit) { git -C $tree checkout --force --detach $Commit } else { git -C $tree reset --hard HEAD }
                    }
                    if (-not $LASTEXITCODE) { $materialized = $true; break }
                    if ($attempt -lt 2) { Start-Sleep -Seconds 5 }
                }
                if (-not $materialized) {
                    $what = if ($Commit) { $Commit } else { "the cloned tree" }
                    Fail "could not check out $what; no checkout published" git_checkout_failed
                }
                $pinned = [bool]$Commit
            }
            Move-Item -LiteralPath $tree -Destination $InstallDir
            Disable-TreelessGraphWrites $InstallDir
            Write-Ok "Hermes Agent cloned"
        } finally {
            Remove-Item -LiteralPath $staged -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    if ($Commit -and -not $pinned) {
        # A rerun over an existing checkout pins here (a fresh clone pinned
        # before publishing). A pin must come from the branch being installed:
        # the complete marker records both, and a commit off that branch would
        # make the next plain rerun "update" onto a different line.
        Invoke-Native { git -C $InstallDir merge-base --is-ancestor $Commit "origin/$Branch" 2>$null }
        if ($LASTEXITCODE) { Fail "commit $Commit is not on branch $Branch" commit_not_on_branch }
        Invoke-Logged "Pinning $Commit" { git -C $InstallDir checkout $Commit }
        if ($LASTEXITCODE) { Fail "could not pin commit $Commit" git_checkout_failed }
    }
}

function Stage-Venv {
    # Keep the installer stage protocol; PM alone creates dependency environments.
    Get-BootstrapPython | Out-Null
    Write-Ok "bootstrap Python ready; PM prepares the dependency environment"
}

# Delegate the whole python+venv+tools install to pm: stage the pinned uv,
# let uv locate Python and exit before PM starts. PM provisions the interpreter,
# the venv (default extras = [all], matching `hermes update`), and the
# tool store — all hash-verified against pm/lock.json + uv.lock. install.ps1
# no longer runs `uv sync` directly; pm is the single install authority
# (the run_locked_uv_sync contract moved into pm/environment.py).
# This tool-only bootstrap runs before PM's own dependencies exist. pm.cli
# prepares and enters its independently locked runtime before installing apps.
function Get-BootstrapPython {
    # The full ladder runs every stage in one process and four of them need
    # this interpreter; resolve uv and Python once per process.
    if ($script:BootstrapPython) { return $script:BootstrapPython }
    $uv = Get-Uv
    $lock = Get-Content (Join-Path $InstallDir "pm\lock.json") -Raw | ConvertFrom-Json
    $pyPin = $lock.packages.python
    $pyVersion = if ($pyPin) { ($pyPin.version -split '\+')[0] -replace '^(\d+\.\d+).*', '$1' } else { '3.14' }
    # A bare version lets uv pick emulated x86_64 on Windows-on-ARM.
    $pyArch = if ((Get-WindowsArch) -eq 'arm64') { 'aarch64' } else { 'x86_64' }
    $pyRequest = "cpython-$pyVersion-windows-$pyArch-none"
    $bootPy = (Invoke-Native -Utf8Output { & $uv python find --managed-python --no-project $pyRequest 2>$null }) -join "`n"
    if ($LASTEXITCODE -or -not $bootPy) {
        Invoke-Logged "Downloading Python $pyVersion" { & $uv python install --no-bin --no-registry $pyRequest }
        if ($LASTEXITCODE) { Fail "bootstrap Python installation failed" python_install_failed }
        $bootPy = (Invoke-Native -Utf8Output { & $uv python find --managed-python --no-project $pyRequest }) -join "`n"
    }
    if ($LASTEXITCODE -or -not $bootPy) { Fail "bootstrap Python lookup failed" python_install_failed }
    $script:BootstrapPython = $bootPy.Trim()
    return $script:BootstrapPython
}

function Invoke-BootstrapPm {
    $bootPy = Get-BootstrapPython
    Push-Location $InstallDir
    try {
        # Finish bootstrap uv before PM replaces or cleans its store entry.
        # Bare $SkipBrowser, like $InstallDir: under iex/scriptblock entry the
        # param() binding is not in $script: scope (see Initialize-ResolvedPaths).
        $pmArgs = @('install')
        if ($SkipBrowser) { $pmArgs += @('--without', 'agent-browser') }
        if ($SkipComputerUse) { $pmArgs += @('--without', 'cua-driver') }
        Invoke-Logged "Installing dependencies (hash-verified via uv.lock)" { & $bootPy -m pm.cli @pmArgs }
        if ($LASTEXITCODE) { Fail "dependency install failed" deps_install_failed }
    } finally {
        Pop-Location
    }
    Write-Ok "dependencies installed"
}

function Stage-PythonDeps {
    Invoke-BootstrapPm
}

function Invoke-SourceCompletion([bool]$Desktop) {
    # The whole tail in one place, by calling the completion an update calls:
    # publish the commands, build the products (tui/web, plus the desktop app
    # when asked), then run the post-build maintenance that syncs bundled
    # skills and migrates config. Node, browsers and the frontend build tools
    # arrive through pm as the build asks for them; the bootstrap interpreter
    # itself only re-enters the tree on PM's selected Python.
    $bootPy = Get-BootstrapPython
    $completionArgs = @('-I', '-B', '-X', 'utf8', 'hermes_cli/source_completion.py', '--source', $InstallDir)
    if ($Desktop) { $completionArgs += '--desktop' }
    Push-Location $InstallDir
    try {
        Invoke-Logged "Building the hermes command and apps" { & $bootPy @completionArgs }
        $code = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($code) { Fail "app products or command publication failed (exit $code)" products_build_failed }
    Write-Ok "app products and hermes command ready"
}

function Publish-UserCommand {
    # PATH exposure stays installer-owned on Windows: expose_cli() answers
    # "windows-installer-owned" rather than creating the user-facing command,
    # so the install-scoped launchers the completion publishes are not the ones
    # the user's PATH points at.
    $binDir = Join-Path $HermesHome "bin"
    $bootPy = Get-BootstrapPython
    Push-Location $InstallDir
    try {
        Invoke-Logged "Publishing the hermes command" { & $bootPy -I -X utf8 hermes_cli/_launchers.py $binDir }
        $code = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($code) { Fail "launcher staging failed" products_build_failed }
    Set-LauncherUserPath $binDir
    Write-Ok "hermes command installed at $binDir"
}

function Test-DesktopProductPresent {
    # Does this checkout already carry a built desktop app? A plain repair or
    # upgrade rerun on a desktop install must REBUILD it rather than leave a
    # bundle built by the previous code: the app is part of that install and its
    # artifacts live inside the tree, so an update makes them stale, not gone.
    # electron-builder suffixes the output dir with the arch on every non-x64
    # target (win-arm64-unpacked, linux-arm64-unpacked, mac-arm64), so the x64
    # names alone miss a desktop build on ARM64 (#94703).
    $release = Join-Path $InstallDir "apps/desktop/release"
    foreach ($candidate in @("win-unpacked", "win-ia32-unpacked", "win-arm64-unpacked",
                             "linux-unpacked", "linux-arm64-unpacked", "mac", "mac-arm64")) {
        if (Test-Path (Join-Path $release $candidate)) { return $true }
    }
    return $false
}

function Stage-Products {
    $desktop = [bool]$IncludeDesktop -or [bool](Test-DesktopProductPresent)
    Invoke-SourceCompletion $desktop
    Publish-UserCommand
    if ($desktop) { Confirm-DesktopArtifact }
}

function Set-LauncherUserPath([string]$binDir) {
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($userPath -notlike "*$binDir*") {
        [Environment]::SetEnvironmentVariable("Path", "$binDir;$userPath", "User")
        Write-Ok "added $binDir to your user PATH (new shells pick it up)"
    }
    # The registry write only reaches shells started later. $env:Path is
    # process-wide, so prepending it here makes `hermes` resolve in the
    # caller's own window whenever this code runs in the caller's process
    # (`irm | iex`, `& .\install.ps1`); a -File child just discards it.
    # Recorded before the first prepend only (the -IncludeDesktop ladder
    # publishes twice): it is what the caller's shell inherited.
    $sessionEntries = @($env:Path -split ';' | ForEach-Object { $_.TrimEnd('\') })
    $onPath = $sessionEntries -contains $binDir.TrimEnd('\')
    if ($null -eq $script:BinDirOnCallerPath) { $script:BinDirOnCallerPath = $onPath }
    if (-not $onPath) { $env:Path = "$binDir;$env:Path" }
}

function Write-PathReloadHint {
    # A script file may be a separate powershell.exe (-File), whose $env:Path
    # dies with it; the parent keeps the PATH it started with until reloaded.
    # iex'd text always runs in the caller's process, where the prepend in
    # Set-LauncherUserPath already made `hermes` resolvable.
    if (-not $script:RunAsFile -or $script:BinDirOnCallerPath -ne $false) { return }
    Log 'Restart your terminal to use hermes, or run: $env:Path = [Environment]::GetEnvironmentVariable(''Path'',''User'') + '';'' + [Environment]::GetEnvironmentVariable(''Path'',''Machine'')'
}

function Stage-Config {
    foreach ($d in @("cron","sessions","logs","pairing","hooks","image_cache","audio_cache","memories","skills")) {
        New-Item -ItemType Directory -Force -Path (Join-Path $HermesHome $d) | Out-Null
    }
    $envFile = Join-Path $HermesHome ".env"
    if (-not (Test-Path $envFile)) {
        $example = Join-Path $InstallDir ".env.example"
        if (Test-Path $example) { Copy-Item $example $envFile } else { New-Item -ItemType File -Path $envFile | Out-Null }
    }
    $cfg = Join-Path $HermesHome "config.yaml"
    $cfgExample = Join-Path $InstallDir "cli-config.yaml.example"
    if (-not (Test-Path $cfg) -and (Test-Path $cfgExample)) { Copy-Item $cfgExample $cfg }
    Write-Ok "config prepared in $HermesHome"
}

function Invoke-InstalledHermes([string[]]$CommandArgs) {
    # Load the helper from its text, not its path. Under `irm | iex` this
    # installer runs as a string that execution policy never checks, but
    # dot-sourcing a .ps1 from disk is a file load. The default Restricted
    # policy (Windows Sandbox, fresh machines) refuses that load.
    $runtimeHelper = Join-Path $InstallDir 'scripts/desktop-update/runtime.ps1'
    . ([ScriptBlock]::Create([IO.File]::ReadAllText($runtimeHelper)))
    # Not `$command`: Invoke-Native's `$Command` parameter shadows it
    # (names are case-insensitive) and the block would invoke itself.
    $runtimeCommand = @(Get-HermesRuntimeCommand -InstallRoot $InstallDir)
    $runtimeArgs = @($runtimeCommand | Select-Object -Skip 1) + $CommandArgs
    Invoke-Native { & $runtimeCommand[0] @runtimeArgs }
    if ($LASTEXITCODE) {
        Fail "hermes $($CommandArgs -join ' ') failed (exit $LASTEXITCODE)" $(if ($CommandArgs[0] -eq 'setup') { 'setup_failed' } else { 'gateway_failed' })
    }
}

function Stage-Setup {
    if ($NonInteractive) { return }
    Invoke-InstalledHermes @('setup')
}

function Stage-Gateway {
    if ($NonInteractive) { return }
    # Setup installs the service when it handles the gateway; ask only if it did not.
    Invoke-InstalledHermes @('gateway', 'install', '--if-missing')
}

function Stage-Desktop {
    # External-caller contract: -Stage desktop stays dispatchable on its own
    # (see Invoke-StageByName). The work is the same completion call with the
    # desktop product selected. Voice and wake extras are not synced here: pm
    # lazy-installs them at first use (policy: Teknium, July 2026, #70509).
    Invoke-SourceCompletion $true
    Publish-UserCommand
    Confirm-DesktopArtifact
}

function Confirm-DesktopArtifact {
    # Probe the packaged artifact the completion just built -- the same
    # candidates hermes_cli/main_desktop._desktop_packaged_executable resolves.
    Push-Location $InstallDir
    try {
        $desktopDir = Join-Path $InstallDir "apps\desktop"
        $candidates = @(
            (Join-Path $desktopDir "release\win-unpacked\Hermes.exe"),
            (Join-Path $desktopDir "release\win-ia32-unpacked\Hermes.exe"),
            (Join-Path $desktopDir "release\win-arm64-unpacked\Hermes.exe")
        )
        $desktopExe = $null
        foreach ($cand in $candidates) {
            if (Test-Path $cand) { $desktopExe = $cand; break }
        }
        if (-not $desktopExe) {
            Fail "desktop build produced no Hermes.exe under $desktopDir\release\*-unpacked" products_build_failed
        }
        Write-Ok "Desktop ready: $desktopExe"

        # Grant ALL APPLICATION PACKAGES (S-1-15-2-2) RX on the unpacked
        # app directory: Chromium's GPU/renderer sandboxes CHECK-fail with
        # 0x80000003 without this ACE beside orphan AppContainer SIDs under
        # %LOCALAPPDATA% (electron/electron#51761, hermes-agent#38216).
        # Best-effort -- never fail an otherwise-good install over ACL.
        try {
            $appDir = Split-Path -Parent $desktopExe
            Invoke-Native { & icacls $appDir /grant "*S-1-15-2-2:(OI)(CI)(RX)" /T /C /Q } | Out-Null
            # PowerShell 5.1 can lose a nested native command's stdout when
            # this installer itself is redirected by the desktop bootstrapper.
            # Capture uv directly through ProcessStartInfo instead of relying
            # on the native-command pipeline for the interpreter path.
            $process = New-Object System.Diagnostics.Process
            $startInfo = New-Object System.Diagnostics.ProcessStartInfo
            $startInfo.FileName = $UvCmd
            $startInfo.Arguments = "python find $ver --managed-python --no-config"
            $startInfo.UseShellExecute = $false
            $startInfo.CreateNoWindow = $true
            $startInfo.RedirectStandardOutput = $true
            $startInfo.RedirectStandardError = $true
            $process.StartInfo = $startInfo
            if (-not $process.Start()) { continue }
            $stdoutTask = $process.StandardOutput.ReadToEndAsync()
            $stderrTask = $process.StandardError.ReadToEndAsync()
            if (-not $process.WaitForExit($PythonFindTimeoutMs)) {
                try { $process.Kill() } catch { }
                $process.WaitForExit()
                throw "uv python find $ver timed out after $PythonFindTimeoutMs ms"
            }
            $stdout = $stdoutTask.Result
            $stderrTask.Result | Out-Null
            if ($process.ExitCode -ne 0) { continue }
            [string]$foundPath = ($stdout.Trim() -split "`r?`n") | Select-Object -Last 1
            if ($foundPath) {
                $absolute = [System.IO.Path]::GetFullPath($foundPath)
                if ($absolute.StartsWith($managedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
                    return [PSCustomObject]@{
                        Path = $absolute
                        Version = $ver
                    }
                }
            }
        } catch {
            throw "Failed to resolve Hermes-managed Python $ver`: $_"
        } finally {
            if ($process) { $process.Dispose() }
        }
    }
    return $null
}

function Test-Python {
    Initialize-ManagedPythonEnvironment | Out-Null
    Write-Info "Checking Python $PythonVersion..."

    # Only a checkout-private uv-managed interpreter satisfies this stage.
    try {
        $resolvedPython = Resolve-AvailablePythonVersion
        if ($resolvedPython) {
            $ver = & $resolvedPython.Path --version 2>$null
            Write-Success "Python found: $ver"
            return $true
        }
    } catch { }
    
    # Python not found -- use uv to install it (no admin needed!)
    Write-Info "Python $PythonVersion not found, installing via uv..."
    # Capture EAP outside the try block so the catch's restore call always
    # has a meaningful value (see Install-Uv for the full rationale).
    $prevEAP = $ErrorActionPreference
    try {
        # Temporarily relax ErrorActionPreference: uv writes download progress
        # ("Downloading cpython-3.11.15-windows-x86_64-none (24.5MiB)") to
        # stderr.  With $ErrorActionPreference = "Stop" (set at the top of this
        # script) PowerShell wraps stderr lines from native commands as
        # ErrorRecord objects when captured via 2>&1, then throws a terminating
        # exception on the first one -- even though uv exits 0 and Python was
        # installed successfully.  Verify success via `uv python find`
        # afterwards, which is the reliable signal regardless of exit-code
        # semantics or stderr noise.  This fix was previously landed as
        # commit ec1714e71 and then lost in a release squash; reapplied here.
        $ErrorActionPreference = "Continue"
        $uvOutput = & $UvCmd python install $PythonVersion --no-bin --no-registry --no-config 2>&1
        $uvExitCode = $LASTEXITCODE
        $ErrorActionPreference = $prevEAP

        # Check if Python is now available (more reliable than exit code
        # since uv may return non-zero due to "already installed" etc.)
        $resolvedPython = Resolve-AvailablePythonVersion
        if ($resolvedPython) {
            $ver = & $resolvedPython.Path --version 2>$null
            Write-Success "Python installed: $ver"
            return $true
        }

        # uv ran but Python still not findable -- show what happened
        if ($uvExitCode -ne 0) {
            Write-Warn "uv python install output:"
            Write-Host $uvOutput -ForegroundColor DarkGray
        }
    } catch {
        # Restore EAP in case the try block threw before the assignment
        if ($prevEAP) { $ErrorActionPreference = $prevEAP }
        Write-Warn "uv python install error: $_"
    }

    # Preserve the established minor-version fallback contract, but provision
    # every fallback into the same private store instead of borrowing a system
    # interpreter. This path is reached only when the preferred install failed.
    foreach ($fallbackVer in $PythonFallbackVersions) {
        try {
            Write-Info "Trying managed Python fallback $fallbackVer..."
            $previousFallbackEAP = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            & $UvCmd python install $fallbackVer --no-bin --no-registry --no-config 2>&1 | Out-Null
            $ErrorActionPreference = $previousFallbackEAP
            $resolvedPython = Resolve-AvailablePythonVersion
            if ($resolvedPython) {
                $ver = & $resolvedPython.Path --version 2>$null
                Write-Success "Python fallback installed: $ver"
                return $true
            }
        } catch {
            if ($previousFallbackEAP) { $ErrorActionPreference = $previousFallbackEAP }
        }
    }

    Write-Err "Failed to install Python $PythonVersion"
    Write-Info "Check network access to uv's managed Python downloads, then retry."
    return $false
}

$script:GitInstallFailureReason = $null
$script:GitBashPath = $null
$script:GitBashProbeOutput = $null

function Test-GitBashCompatibility {
    <#
    .SYNOPSIS
    Verify that Git Bash can launch external MSYS programs, not just evaluate
    shell builtins. Mandatory ASLR can allow bash.exe itself to start while
    every child linked to msys-2.0.dll fails during fork/spawn.
    #>
    param([Parameter(Mandatory = $true)][string]$BashPath)

    $script:GitBashProbeOutput = $null
    if (-not (Test-Path -LiteralPath $BashPath)) {
        $script:GitBashProbeOutput = "bash.exe was not found at $BashPath"
        return $false
    }

    $process = New-Object System.Diagnostics.Process
    try {
        $startInfo = New-Object System.Diagnostics.ProcessStartInfo
        $startInfo.FileName = $BashPath
        $startInfo.Arguments = '--noprofile --norc -c "/usr/bin/true; /usr/bin/cat --version >/dev/null"'
        $startInfo.UseShellExecute = $false
        $startInfo.CreateNoWindow = $true
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true
        $process.StartInfo = $startInfo

        if (-not $process.Start()) {
            $script:GitBashProbeOutput = "bash.exe did not start"
            return $false
        }
        if (-not $process.WaitForExit(15000)) {
            try { $process.Kill() } catch { }
            $script:GitBashProbeOutput = "Git Bash compatibility probe timed out"
            return $false
        }

        $stdout = $process.StandardOutput.ReadToEnd()
        $stderr = $process.StandardError.ReadToEnd()
        $script:GitBashProbeOutput = ("$stdout`n$stderr").Trim()
        return ($process.ExitCode -eq 0)
    } catch {
        $script:GitBashProbeOutput = $_.Exception.Message
        return $false
    } finally {
        $process.Dispose()
    }
}

function Test-MandatoryAslrEnabled {
    <# Return true only when Windows reports system-wide ForceRelocateImages=ON. #>
    try {
        $cmd = Get-Command Get-ProcessMitigation -ErrorAction SilentlyContinue
        if (-not $cmd) { return $false }
        $mitigations = & $cmd -System
        $value = $mitigations.Aslr.ForceRelocateImages
        return ($null -ne $value -and $value.ToString().ToUpperInvariant() -eq "ON")
    } catch {
        return $false
    }
}

function Get-GitRootFromBashPath {
    param([Parameter(Mandatory = $true)][string]$BashPath)

    $binDir = Split-Path -Path $BashPath -Parent
    if ((Split-Path -Path $binDir -Leaf) -ine "bin") {
        return (Split-Path -Path $binDir -Parent)
    }

    $parent = Split-Path -Path $binDir -Parent
    if ((Split-Path -Path $parent -Leaf) -ieq "usr") {
        return (Split-Path -Path $parent -Parent)
    }
    return $parent
}

function New-GitBashAslrFailureReason {
    param([Parameter(Mandatory = $true)][string]$BashPath)

    $gitRoot = Get-GitRootFromBashPath -BashPath $BashPath
    $escapedRoot = $gitRoot -replace "'", "''"
    return @(
        "Git Bash at $BashPath cannot launch required MSYS child processes because Windows Mandatory ASLR (ForceRelocateImages) is enabled system-wide. Reinstalling Git will not change this policy."
        "Open PowerShell as Administrator and run:"
        "`$gitRoot = '$escapedRoot'"
        'Get-Item "$gitRoot\bin\bash.exe", "$gitRoot\usr\bin\*.exe" -ErrorAction SilentlyContinue | ForEach-Object { Set-ProcessMitigation -Name $_.FullName -Disable ForceRelocateImages }'
        "Then rerun Hermes setup. If the override is blocked or later re-applied, ask your Windows administrator to allow this per-program exception."
    ) -join [Environment]::NewLine
}

function Install-Git {
    <#
    .SYNOPSIS
    Ensure Git is available on PATH (for cloning, version control, etc.).

    Hermes no longer requires Git Bash for shell commands -- Windows PowerShell
    5.1 (powershell.exe) ships with every Windows 10/11 system and is used
    as the terminal shell.  Git is still useful for repository operations but
    is not a hard dependency for the terminal tool.

    Priority order:
      1. Existing ``git`` on PATH -- use it as-is (the common fast path).
      2. Download **PortableGit** from the official git-for-windows GitHub
         release (self-extracting 7z.exe) and unpack it to
         ``%LOCALAPPDATA%\hermes\git`` -- never touches system Git, never
         requires admin.
    #>
    $script:GitInstallFailureReason = $null
    Write-Info "Checking Git..."

    if (Get-Command git -ErrorAction SilentlyContinue) {
        $version = git --version
        Write-Success "Git found ($version)"
        return $true
    }

    # Download PortableGit into $HermesHome\git.
    Write-Info "Git not found -- downloading PortableGit to $HermesHome\git\ ..."
    Write-Info "(no admin rights required; isolated from any system Git install)"

    try {
        $arch = Get-WindowsArch
        if ($arch -eq 'arm64') {
            $assetTag = 'arm64'
            $downloadIsZip = $false
        } elseif ($arch -eq 'x64') {
            $assetTag = '64-bit'
            $downloadIsZip = $false
        } else {
            $assetTag = '32-bit-mingit'
            $downloadIsZip = $true
        }

        $gitTag    = "v2.54.0.windows.1"
        $gitVer    = "2.54.0"
        $gitVerTag = "$gitVer.windows.1"

        if ($arch -eq "32-bit-mingit") {
            Write-Warn "32-bit Windows detected -- PortableGit is 64-bit only.  Installing MinGit 32-bit as a last resort."
            $assetName    = "MinGit-$gitVer-32-bit.zip"
            $downloadIsZip = $true
        } elseif ($arch -eq "arm64") {
            $assetName    = "PortableGit-$gitVer-arm64.7z.exe"
            $downloadIsZip = $false
        } else {
            $assetName    = "PortableGit-$gitVer-64-bit.7z.exe"
            $downloadIsZip = $false
        }

        $downloadUrl = "https://github.com/git-for-windows/git/releases/download/$gitTag/$assetName"
        $downloadExt = if ($downloadIsZip) { "zip" } else { "7z.exe" }
        $tmpFile = "$env:TEMP\$assetName"
        $gitDir = "$HermesHome\git"

        Write-Info "Downloading $assetName (Git for Windows $gitVerTag)..."
        Invoke-WebRequest -Uri $downloadUrl -OutFile $tmpFile -UseBasicParsing

        if (Test-Path $gitDir) {
            Write-Info "Removing previous Git install at $gitDir ..."
            Remove-Item -Recurse -Force $gitDir
        }
        New-Item -ItemType Directory -Path $gitDir -Force | Out-Null

        if ($downloadIsZip) {
            Expand-Archive -Path $tmpFile -DestinationPath $gitDir -Force
        } else {
            Write-Info "Extracting PortableGit to $gitDir ..."
            $extractProc = Start-Process -FilePath $tmpFile `
                -ArgumentList "-o`"$gitDir`"", "-y" `
                -NoNewWindow -Wait -PassThru
            if ($extractProc.ExitCode -ne 0) {
                throw "PortableGit extraction failed (exit code $($extractProc.ExitCode))"
            }
        }
        Remove-Item -Force $tmpFile -ErrorAction SilentlyContinue

        $gitExe = "$gitDir\cmd\git.exe"
        if (-not (Test-Path $gitExe)) {
            throw "Git extraction did not produce git.exe at $gitExe"
        }

        # Add to session PATH so the rest of this install run can use git.
        $env:Path = "$gitDir\cmd;$env:Path"

        # Persist to User PATH.
        $newPathEntries = @(
            "$gitDir\cmd",
            "$gitDir\bin",
            "$gitDir\usr\bin"
        )
        $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
        $userPathItems = if ($userPath) { $userPath -split ";" } else { @() }
        $changed = $false
        foreach ($entry in $newPathEntries) {
            if ($userPathItems -notcontains $entry) {
                $userPathItems += $entry
                $changed = $true
            }
        }
        if ($changed) {
            [Environment]::SetEnvironmentVariable("Path", ($userPathItems -join ";"), "User")
        }

        $version = & $gitExe --version
        Write-Success "Git $version installed to $gitDir (portable, user-scoped)"
        return $true
    } catch {
        if ($script:GitInstallFailureReason) {
            Write-Err $script:GitInstallFailureReason
            return $false
        }
        Write-Err "Could not install portable Git: $_"
        Write-Info ""
        Write-Info "Fallback: install Git manually from https://git-scm.com/download/win"
        Write-Info "then re-run this installer."
        return $false
    }
}

# The desktop build runs Vite ^8, which refuses to start on Node outside
# `^20.19 || >=22.12` -- older Node lacks node:util.styleText, so `vite build`
# crashes with a SyntaxError that surfaces only as the opaque "Build desktop
# app ... exit code 1" install failure. The dependency tree's real Node floor
# is >=22.22.0, set by react-router 8.3.0 (`engines.node`). Keep this in sync
# with the root package.json: looser lets an install reach a `npm ci` that
# dies with EBADENGINE, stricter replaces a working user toolchain for
# nothing. Returns $true when a `node --version` string clears that floor.
# The dependency tree supports Node 22.22+, 24.11+, and 26+. nanoid 6 excludes
# Node 23 and 25 while its >=26 arm accepts later releases, and @babel/* 8.x
# requires ^22.18.0 || >=24.11.0 -- so accepting 23/25 or an early Node 24
# only defers the failure to `npm ci` under engine-strict. Keep this in sync
# with the root package.json.
function Test-NodeVersionOk {
    param([string]$Version)
    if ($Version -match '-') { return $false }
    try {
        $v = [version]($Version -replace '^v', '')
    } catch {
        return $false
    }
    if ($v.Major -eq 22) { return ($v.Minor -ge 22) }
    if ($v.Major -eq 24) { return ($v.Minor -ge 11) }
    return ($v.Major -ge 26)
}

# Accept a system Node only when its companion npm also satisfies the same
# range used to provision the Hermes-managed tree. Keeping this probe separate
# lets the initial PATH check and the post-winget check share one authority.
function Test-SystemNodeReady {
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) { return $false }

    $version = node --version
    if (Test-NodeVersionOk $version) {
        Ensure-NodeExeOnPath | Out-Null
    } else {
        Write-Warn "Node.js $version is unsupported (Hermes requires Node 22.22+, 24.11+, or 26+)"
        return $false
    }

    $npmRange = Get-NpmRange
    $npmCmd = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $npmCmd) {
        $npmCmd = Get-Command npm -ErrorAction SilentlyContinue
    }

    $npmVersion = $null
    if ($npmCmd) {
        try {
            $npmVersion = (& $npmCmd --version 2>$null | Select-Object -First 1)
        } catch { }
    }

    if ($npmVersion -and (Test-NpmVersionOk $npmVersion $npmRange)) {
        Write-Success "Node.js $version with npm $npmVersion found"
        return $true
    }

    if ($npmVersion) {
        Write-Warn "Node.js $version uses npm $npmVersion, which does not satisfy Hermes requirement $npmRange"
    } else {
        Write-Warn "Node.js $version was found, but npm is missing or could not report its version"
    }
    return $false
}

function Test-Node {
    Write-Info "Checking Node.js (for browser tools)..."

    if (Test-SystemNodeReady) {
        $script:HasNode = $true
        return $true
    }

    Write-Info "Using a Hermes-managed Node.js installation instead..."

    # Prefer a Hermes-managed Node from a previous run over a too-old system one.
    $managedNode = "$HermesHome\node\node.exe"
    if ((Test-Path $managedNode) -and (Test-NodeVersionOk (& $managedNode --version))) {
        $version = & $managedNode --version
        $env:Path = "$HermesHome\node;$env:Path"
        Set-ManagedNodeFirstOnUserPath "$HermesHome\node"
        Write-Success "Node.js $version found (Hermes-managed)"
        # A tree from an older install still has that Node major's bundled
        # npm, which is below the current engines.npm floor. No-ops when the
        # npm is already in range, so reruns cost one --version probe.
        Update-ManagedNpm "$HermesHome\node" | Out-Null
        $script:HasNode = $true
        return $true
    }

    Write-Info "Installing Hermes-managed Node.js $NodeVersion LTS..."

    # Try the portable-zip path FIRST -- no UAC, no admin, no winget MSI.
    # winget install OpenJS.NodeJS.LTS triggers a system-wide MSI install
    # which prompts UAC (the dialog often appears minimized in the taskbar
    # and the install silently waits for consent, looking like a hang).
    # The portable zip path drops node.exe + npm into $HermesHome\node\
    # which is user-scoped and identical to how Install-Git handles
    # PortableGit.  Same UX guarantee: works on locked-down enterprise
    # machines with no admin rights.
    Write-Info "Downloading portable Node.js $NodeVersion to $HermesHome\node\ ..."
    Write-Info "(no admin rights required; isolated from any system Node install)"
    try {
        $arch = Get-WindowsArch
        $indexUrl = "https://nodejs.org/dist/latest-v${NodeVersion}.x/"
        $indexPage = Invoke-WebRequest -Uri $indexUrl -UseBasicParsing
        $zipName = ($indexPage.Content | Select-String -Pattern "node-v${NodeVersion}\.\d+\.\d+-win-${arch}\.zip" -AllMatches).Matches[0].Value

        if ($zipName) {
            $downloadUrl = "${indexUrl}${zipName}"
            $tmpZip = "$env:TEMP\$zipName"
            $tmpDir = "$env:TEMP\hermes-node-extract"

            Invoke-WebRequest -Uri $downloadUrl -OutFile $tmpZip -UseBasicParsing
            if (Test-Path $tmpDir) { Remove-Item -Recurse -Force $tmpDir }
            Expand-Archive -Path $tmpZip -DestinationPath $tmpDir -Force

            $extractedDir = Get-ChildItem $tmpDir -Directory | Select-Object -First 1
            if ($extractedDir) {
                # Rename-swap instead of delete-then-move: the live tree is
                # never removed before its replacement is fully extracted.
                # Windows permits renaming a tree with running executables,
                # but if a process holds it without FILE_SHARE_DELETE the
                # rename fails with WinError 5 -- that refusal means the tree
                # is in use, so defer instead of forcing the write (#80926).
                # Best-effort sweep of staging/backup litter from interrupted
                # runs; locked files simply stay for the next attempt.  Only
                # dirs older than 10 minutes are removed so a concurrent
                # heal's in-flight swap is never disturbed.
                Get-ChildItem "$HermesHome" -Directory -Filter "node.old-*" -ErrorAction SilentlyContinue |
                    Where-Object { $_.LastWriteTime -lt (Get-Date).AddMinutes(-10) } |
                    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
                Get-ChildItem "$HermesHome" -Directory -Filter "node.new-*" -ErrorAction SilentlyContinue |
                    Where-Object { $_.LastWriteTime -lt (Get-Date).AddMinutes(-10) } |
                    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
                $stamp = [Guid]::NewGuid().ToString("N")
                $staged = "$HermesHome\node.new-$stamp"
                $backup = "$HermesHome\node.old-$stamp"
                # Stage to a sibling directory so the final swap is a
                # same-volume rename (atomic), not a cross-volume Move-Item
                # (copy+delete, non-atomic -- a partial copy would leave a
                # broken tree).  Move from $env:TEMP here, rename below.
                try {
                    Move-Item $extractedDir.FullName $staged -ErrorAction Stop
                } catch {
                    Write-Warn "Failed to stage the new Node.js tree; aborting the Node upgrade."
                    Remove-Item -Recurse -Force $tmpDir -ErrorAction SilentlyContinue
                    Remove-Item -Force $tmpZip -ErrorAction SilentlyContinue
                    return $false
                }
                if (Test-Path "$HermesHome\node") {
                    try {
                        Rename-Item "$HermesHome\node" $backup -ErrorAction Stop
                    } catch {
                        Write-Warn "Hermes-managed Node.js is in use by a running app; deferring its upgrade. Close the app and re-run the update."
                        Remove-Item -Recurse -Force $staged -ErrorAction SilentlyContinue
                        Remove-Item -Recurse -Force $tmpDir -ErrorAction SilentlyContinue
                        Remove-Item -Force $tmpZip -ErrorAction SilentlyContinue
                        return $false
                    }
                    # A rename preserves LastWriteTime, so a backup renamed
                    # from a long-lived tree would instantly look older than
                    # the litter-sweep cutoff to a concurrent heal.  Touch it
                    # (best-effort) so the in-flight backup is never swept.
                    try {
                        (Get-Item $backup).LastWriteTime = Get-Date
                    } catch { }
                    try {
                        Rename-Item $staged "$HermesHome\node" -ErrorAction Stop
                    } catch {
                        # Restore the live tree before bailing.  The swap is a
                        # same-volume rename, so a failure leaves no partial
                        # target to clear.
                        Rename-Item $backup "$HermesHome\node" -ErrorAction SilentlyContinue
                        Remove-Item -Recurse -Force $staged -ErrorAction SilentlyContinue
                        Remove-Item -Recurse -Force $tmpDir -ErrorAction SilentlyContinue
                        Remove-Item -Force $tmpZip -ErrorAction SilentlyContinue
                        return $false
                    }
                    Remove-Item -Recurse -Force $backup -ErrorAction SilentlyContinue
                } else {
                    try {
                        Rename-Item $staged "$HermesHome\node" -ErrorAction Stop
                    } catch {
                        Remove-Item -Recurse -Force $staged -ErrorAction SilentlyContinue
                        Remove-Item -Recurse -Force $tmpDir -ErrorAction SilentlyContinue
                        Remove-Item -Force $tmpZip -ErrorAction SilentlyContinue
                        return $false
                    }
                }

                # Session PATH so the rest of this run sees node/npm.
                $env:Path = "$HermesHome\node;$env:Path"

                # Persist to User PATH so fresh shells (and future stages
                # in cross-process driver mode) see it.  Matches the
                # pattern Install-Git uses for PortableGit.  See
                # Set-ManagedNodeFirstOnUserPath for why this is a
                # move-to-front and not an add-if-missing.
                Set-ManagedNodeFirstOnUserPath "$HermesHome\node"

                $version = & "$HermesHome\node\node.exe" --version
                Write-Success "Node.js $version installed to $HermesHome\node\ (portable, user-scoped)"
                # The zip's bundled npm is below the repo's engines.npm floor.
                Update-ManagedNpm "$HermesHome\node" | Out-Null
                $script:HasNode = $true

                Remove-Item -Force $tmpZip -ErrorAction SilentlyContinue
                Remove-Item -Recurse -Force $tmpDir -ErrorAction SilentlyContinue
                return $true
            }
        }
    } catch {
        Write-Warn "Portable Node.js download failed: $_"
    }

    # Fallback: try winget (used to be primary, demoted because the MSI
    # install triggers a UAC prompt that frequently appears minimized in
    # the taskbar -- looks like a hang to users on stock Windows).
    # Kept for environments where the portable download fails (proxy,
    # locked firewall, etc.) but the user is willing to consent to UAC.
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Info "Falling back to winget (may prompt UAC -- check your taskbar for a flashing icon)..."
        # Capture EAP outside the try block so the catch's restore call always
        # has a meaningful value (see Install-Uv for the full rationale).
        $prevEAP = $ErrorActionPreference
        try {
            # Relax EAP=Stop so stderr lines from winget don't get wrapped
            # as ErrorRecords and short-circuit the 2>&1 pipe before we can
            # check the post-condition.  See the long comment in Install-Uv
            # for the same pattern.
            $ErrorActionPreference = "Continue"
            # On ARM64, force winget to fetch the ARM64 installer.  Without
            # the explicit override, winget on WoW64 sometimes still resolves
            # to x64 manifests, leaving us with an emulated Node toolchain
            # even after a "successful" install.  The OpenJS manifest does
            # publish an arm64 installer, so this is safe.
            $wingetArgs = @(
                'install','OpenJS.NodeJS','--silent',
                '--accept-package-agreements','--accept-source-agreements'
            )
            if ((Get-WindowsArch) -eq 'arm64') {
                $wingetArgs += @('--architecture','arm64')
            }
            winget @wingetArgs 2>&1 | Out-Null
            $ErrorActionPreference = $prevEAP
            # Refresh PATH
            $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
            if (Test-SystemNodeReady) {
                $script:HasNode = $true
                return $true
            }
        } catch {
            if ($prevEAP) { $ErrorActionPreference = $prevEAP }
        }
    }


    Write-Info "Install manually: https://nodejs.org/en/download/"
    $script:HasNode = $false
    return $true
}

function Update-ProcessPathForPackages {
    # Make freshly-installed shims (rg.exe, ffmpeg.exe) visible to Get-Command in
    # THIS process without spawning a new shell, by folding the persisted
    # User+Machine hives plus winget's alias-shim directory into $env:Path.
    # Called after every package-manager attempt (winget/choco/scoop): previously
    # PATH was only refreshed inside the winget branch, so a successful
    # choco/scoop fallback -- or any install on a box without winget -- could be
    # misreported as "not installed".
    #
    # MERGE rather than overwrite: start from the existing process PATH so any
    # process-only entries added earlier in this installer run survive, then
    # APPEND hive/winget-Links entries not already present (case-insensitive,
    # order-preserving dedupe). A wholesale replace would silently drop those
    # process-only entries.
    $candidates = @()
    $candidates += $env:Path
    $candidates += [Environment]::GetEnvironmentVariable("Path", "User")
    $candidates += [Environment]::GetEnvironmentVariable("Path", "Machine")
    $wingetLinks = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links"
    if (Test-Path $wingetLinks) {
        $candidates += $wingetLinks
    }
    $seen = New-Object System.Collections.Generic.HashSet[string] ([StringComparer]::OrdinalIgnoreCase)
    $ordered = New-Object System.Collections.Generic.List[string]
    foreach ($chunk in $candidates) {
        if ([string]::IsNullOrEmpty($chunk)) { continue }
        foreach ($entry in $chunk.Split(';')) {
            $trimmed = $entry.Trim()
            if ($trimmed -and $seen.Add($trimmed)) {
                $ordered.Add($trimmed)
            }
        }
    }
    $env:Path = [string]::Join(';', $ordered)
}

function Test-Ripgrep {
    <#
    .SYNOPSIS
    Verify that an rg executable is actually runnable.

    Returns $true only if the binary exists and `rg --version` produces output.
    This catches broken symlinks, missing winget alias targets, and other
    cases where Get-Command finds a path but the file is not usable.
    #>
    param([string]$Path = "")
    if ([string]::IsNullOrWhiteSpace($Path)) {
        $cmd = Get-Command rg -ErrorAction SilentlyContinue
        if (-not $cmd) { return $false }
        $Path = $cmd.Path
    }
    if (-not (Test-Path $Path)) { return $false }
    try {
        $ver = & $Path --version 2>$null | Select-Object -First 1
        return [bool]$ver
    } catch {
        return $false
    }
}

function Test-Ffmpeg {
    <#
    .SYNOPSIS
    Verify that ffmpeg is available and runnable.
    #>
    $cmd = Get-Command ffmpeg -ErrorAction SilentlyContinue
    if (-not $cmd) { return $false }
    if (-not (Test-Path $cmd.Path)) { return $false }
    try {
        $ver = & $cmd.Path -version 2>$null | Select-Object -First 1
        return [bool]$ver
    } catch {
        return $false
    }
}

function Install-ManagedRipgrep {
    <#
    .SYNOPSIS
    Download a known-good ripgrep binary into $HERMES_HOME\bin\rg.exe.

    Mirrors the existing managed-uv pattern.  The binary is placed at the
    front of the process PATH and persisted to User PATH so it overrides any
    broken system shim in this shell and in future shells.
    #>
    if ((Test-Path $script:ManagedRg) -and (Test-Ripgrep $script:ManagedRg)) {
        Write-Success "Managed ripgrep already usable"
        return
    }

    New-Item -ItemType Directory -Path $script:ManagedToolsDir -Force | Out-Null

    $arch = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") {
        "aarch64-pc-windows-msvc"
    } else {
        "x86_64-pc-windows-msvc"
    }

    $rgVersion = "15.2.0"
    $zipName   = "ripgrep-$rgVersion-$arch.zip"
    $assetUrl  = "https://github.com/BurntSushi/ripgrep/releases/download/$rgVersion/$zipName"
    $zipPath   = Join-Path $env:TEMP $zipName

    try {
        Invoke-WebRequest -Uri $assetUrl -OutFile $zipPath -UseBasicParsing
        $extractDir = Join-Path $env:TEMP "ripgrep-$rgVersion"
        if (Test-Path $extractDir) {
            Remove-Item $extractDir -Recurse -Force -ErrorAction SilentlyContinue
        }
        Expand-Archive -Path $zipPath -DestinationPath $extractDir -Force
        $rgSource = Join-Path $extractDir "ripgrep-$rgVersion-$arch\rg.exe"
        if (-not (Test-Path $rgSource)) {
            throw "Expected rg.exe at $rgSource but it was not found after extraction"
        }
        Copy-Item -Path $rgSource -Destination $script:ManagedRg -Force
    } finally {
        Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
        Remove-Item $extractDir -Recurse -Force -ErrorAction SilentlyContinue
    }

    if (-not (Test-Ripgrep $script:ManagedRg)) {
        throw "Managed ripgrep installed at $script:ManagedRg but cannot run"
    }
    Write-Success "Managed ripgrep installed"

    # Current process: put managed rg first so it overrides any broken shim.
    Update-ProcessPathForPackages
    $env:Path = "$($script:ManagedToolsDir);$env:Path"

    # Persist to User PATH so fresh shells also prefer the managed binary.
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $items = if ($userPath) { $userPath -split ";" } else { @() }
    if ($items -notcontains $script:ManagedToolsDir) {
        [Environment]::SetEnvironmentVariable(
            "Path",
            "$($script:ManagedToolsDir);$userPath",
            "User"
        )
    }
}

function Test-RtkBinary {
    <#
    .SYNOPSIS
    Verify that rtk.exe is available and runnable.
    #>
    param([string]$Path = $script:ManagedRtk)
    if (-not (Test-Path $Path)) { return $false }
    try {
        $verOutput = & $Path --version 2>&1
        $exitCode = $LASTEXITCODE
        $ver = $verOutput | Select-Object -First 1
        return ($exitCode -eq 0) -and ($ver -match 'rtk')
    } catch {
        return $false
    }
}

function Install-ManagedRtk {
    <#
    .SYNOPSIS
    Download a known-good rtk binary into $HERMES_HOME\bin\rtk.exe.

    Mirrors the Install-ManagedRipgrep pattern.  The binary is placed at the
    front of the process PATH and persisted to User PATH so it overrides any
    broken system shim in this shell and in future shells.
    #>
    if ((Test-Path $script:ManagedRtk) -and (Test-RtkBinary $script:ManagedRtk)) {
        Write-Success "Managed rtk already usable"
        return
    }

    New-Item -ItemType Directory -Path $script:ManagedToolsDir -Force | Out-Null

    # Architecture detection (same as ripgrep)
    if ([Environment]::Is64BitOperatingSystem) {
        $arch = "x86_64-pc-windows-msvc"
    } else {
        $arch = "aarch64-pc-windows-msvc"
    }

    $rtkVersion = "0.44.0"
    $zipName   = "rtk-$arch.zip"
    $assetUrl  = "https://github.com/rtk-ai/rtk/releases/download/v$rtkVersion/$zipName"
    $zipPath   = Join-Path $env:TEMP $zipName
    $extractDir = Join-Path $env:TEMP "rtk-$rtkVersion"

		    try {
		        Invoke-WebRequest -Uri $assetUrl -OutFile $zipPath -UseBasicParsing
	        if (Test-Path $extractDir) {
	            Remove-Item $extractDir -Recurse -Force -ErrorAction SilentlyContinue
	        }
	        Expand-Archive -Path $zipPath -DestinationPath $extractDir -Force
	        $rtkSource = Join-Path $extractDir "rtk.exe"
	        if (-not (Test-Path $rtkSource)) {
	            Write-Warn "Could not find rtk binary in extracted tarball"
	            return
	        }
	        Copy-Item -Path $rtkSource -Destination $script:ManagedRtk -Force
	    } catch {
	        Write-Warn "Failed to download or extract rtk: $_"
	        return
	    } finally {
	        Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
	        Remove-Item $extractDir -Recurse -Force -ErrorAction SilentlyContinue
	    }
	
	    if (-not (Test-RtkBinary $script:ManagedRtk)) {
	        Write-Warn "Could not verify installed rtk binary"
	        return
	    }
	    Write-Success "Managed rtk installed"
	
	    # Current process: put managed rtk first so it overrides any broken shim.
	    Update-ProcessPathForPackages
	    $env:Path = "$($script:ManagedToolsDir);$env:Path"
	
	    # Persist to User PATH so fresh shells also prefer the managed binary.
	    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
	    $items = if ($userPath) { $userPath -split ";" } else { @() }
	    if ($items -notcontains $script:ManagedToolsDir) {
	        [Environment]::SetEnvironmentVariable(
	            "Path",
	            "$($script:ManagedToolsDir);$userPath",
	            "User"
	        )
	    }
	}

function Test-Coreutils {
    <#
    .SYNOPSIS
    Verify that Microsoft Coreutils (cat.exe) is available and runnable.
    
    Checks the managed install directory first, then falls back to PATH.
    Returns $true only if the binary exists and runs.
    #>
    param([string]$Path = "")
    if ([string]::IsNullOrWhiteSpace($Path)) {
        # Check managed install first
        if (Test-Path $script:ManagedCat) {
            $Path = $script:ManagedCat
        } else {
            $cmd = Get-Command cat.exe -ErrorAction SilentlyContinue
            if (-not $cmd) { return $false }
            $Path = $cmd.Path
        }
    }
    if (-not (Test-Path $Path)) { return $false }
    try {
        $ver = & $Path --version 2>$null | Select-Object -First 1
        if ($ver -match "coreutils" -or $ver) { return $true }
        return $false
    } catch {
        return $false
    }
}

function Install-Coreutils {
    <#
    .SYNOPSIS
    Install Microsoft Coreutils on Windows to provide POSIX CLI tools.
    
    Strategy (in priority order):
      1. WinGet (official Microsoft channel)
      2. Fallback: invoke scripts/install_coreutils.py (GitHub direct download)
    
    Mirrors the Install-ManagedRipgrep pattern but delegates to the Python
    script for the actual installation since Coreutils is a suite of tools.
    #>
    if (Test-Coreutils) {
        Write-Success "Coreutils already available"
        return
    }
    
    if ($env:OS -ne "Windows_NT") {
        Write-Info "Coreutils installation is Windows-only; skipping."
        return
    }
    
    # Try winget first (most common, official Microsoft channel)
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Info "Installing Coreutils via WinGet..."
        try {
            $output = winget install --exact --id Microsoft.Coreutils --source winget --silent `
                --accept-package-agreements --accept-source-agreements 2>&1
            $code = $LASTEXITCODE
            if ($code -eq -1978335189) {
                # Already-installed/no-upgrade; retry with --force
                $output = winget install --exact --id Microsoft.Coreutils --source winget --silent --force `
                    --accept-package-agreements --accept-source-agreements 2>&1
            }
        } catch {
            Write-Warn "WinGet install attempt failed: $_"
        }
        Update-ProcessPathForPackages
        if (Test-Coreutils) {
            Write-Success "Coreutils installed via WinGet"
            $script:HasCoreutils = $true
            return
        }
    }
    
    # Fallback: use the Python script for managed download
    $pythonExe = if (Test-Path "$InstallDir\venv\Scripts\python.exe") {
        "$InstallDir\venv\Scripts\python.exe"
    } else {
        Get-Command python -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source
    }
    $coreutilsScript = Join-Path $InstallDir "scripts\install_coreutils.py"
    if (-not (Test-Path $coreutilsScript)) {
        # Try relative to the script's own location
        $coreutilsScript = Join-Path $PSScriptRoot "install_coreutils.py"
    }
    if ($pythonExe -and (Test-Path $coreutilsScript)) {
        Write-Info "Installing Coreutils via managed Python script..."
        try {
            $env:HERMES_HOME = $HermesHome
            # Use relaxed EAP so stderr from the Python script (e.g. progress
            # messages when not on Windows) doesn't get wrapped as
            # NativeCommandError and trigger a terminating exception under
            # $ErrorActionPreference = "Stop".  Check $LASTEXITCODE for real
            # failures.  Same pattern as Test-Python and Install-Uv.
            \$prevEAP = \$ErrorActionPreference
            \$ErrorActionPreference = "Continue"
            & \$pythonExe \$coreutilsScript --dir \$script:ManagedCoreutilsDir --ensure
            \$pythonExit = \$LASTEXITCODE
            \$ErrorActionPreference = \$prevEAP
            # Refresh process PATH
            $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
            # Add managed coreutils dir to PATH if not already there
            $coreutilsBin = Join-Path $script:ManagedCoreutilsDir "bin"
            if ((Test-Path $coreutilsBin) -and ($env:Path -notlike "*$coreutilsBin*")) {
                $env:Path = "$coreutilsBin;$env:Path"
            }
            if (Test-Coreutils) {
                Write-Success "Coreutils installed via managed Python script"
                $script:HasCoreutils = $true
                return
            }
        } catch {
            Write-Warn "Managed Coreutils install failed: $_"
        }
    }
    
    # Still missing: show manual hint
    if (-not (Test-Coreutils)) {
        Write-Warn "Coreutils not installed (POSIX CLI tools will be unavailable on Windows)"
        Write-Info "  Install manually: winget install Microsoft.Coreutils"
        Write-Info "  Or download from: https://github.com/microsoft/coreutils/releases"
    }
}

function Install-SystemPackages {
    $script:HasRipgrep = Test-Ripgrep
    $script:HasFfmpeg  = Test-Ffmpeg
    $script:HasCoreutils = Test-Coreutils
    $needRipgrep = -not $script:HasRipgrep
    $needFfmpeg  = -not $script:HasFfmpeg

    Write-Info "Checking ripgrep (fast file search)..."
    if ($script:HasRipgrep) {
        $version = rg --version | Select-Object -First 1
        Write-Success "$version found"
    } else {
        Write-Warn "ripgrep missing or broken; installing managed copy..."
        $badRg = Get-Command rg -ErrorAction SilentlyContinue
        if ($badRg) {
            try { Remove-Item $badRg.Path -Force -ErrorAction SilentlyContinue } catch {}
        }
        Install-ManagedRipgrep
        $script:HasRipgrep = $true
        $needRipgrep = $false
    }

    Write-Info "Checking ffmpeg (TTS voice messages)..."
    if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
        Write-Success "ffmpeg found"
        $script:HasFfmpeg = $true
    } else {
        $needFfmpeg = $true
    }

    # Coreutils (Windows-only POSIX CLI tools)
    if ($env:OS -eq "Windows_NT") {
        Write-Info "Checking Microsoft Coreutils (POSIX CLI tools)..."
        if (-not $script:HasCoreutils) {
            Install-Coreutils
        }
    }

    Write-Info "Checking rtk (CLI proxy, for token-kill)..."
    $script:HasRtk = Test-RtkBinary
    if ($script:HasRtk) {
        Write-Success "rtk found"
    } else {
        Write-Warn "rtk missing; installing managed copy..."
        Install-ManagedRtk
        $script:HasRtk = Test-RtkBinary
        if (-not $script:HasRtk) {
            Write-Warn "rtk not installed (token-kill will be unavailable)"
        }
    }

    if (-not $needRipgrep -and -not $needFfmpeg) { return }

    # Build description and package lists for each package manager
    $descParts = @()
    $wingetPkgs = @()
    $chocoPkgs = @()
    $scoopPkgs = @()

    if ($needRipgrep) {
        $descParts += "ripgrep for faster file search"
        $wingetPkgs += "BurntSushi.ripgrep.MSVC"
        $chocoPkgs += "ripgrep"
        $scoopPkgs += "ripgrep"
    }
    if ($needFfmpeg) {
        $descParts += "ffmpeg for TTS voice messages"
        $wingetPkgs += "Gyan.FFmpeg"
        $chocoPkgs += "ffmpeg"
        $scoopPkgs += "ffmpeg"
    }

    $description = $descParts -join " and "
    $hasWinget = Get-Command winget -ErrorAction SilentlyContinue
    $hasChoco = Get-Command choco -ErrorAction SilentlyContinue
    $hasScoop = Get-Command scoop -ErrorAction SilentlyContinue

    # Try winget first (most common on modern Windows)
    if ($hasWinget) {
        Write-Info "Installing $description via winget..."
        # Per-package log paths -- key the lookup by package id so we can
        # decide AFTER the post-install Get-Command check whether to keep
        # the log (still missing -> keep as breadcrumb) or delete it (now
        # present -> happy path, no clutter).
        $pkgLogs = @{}
        foreach ($pkg in $wingetPkgs) {
            $log = "$env:TEMP\hermes-winget-$($pkg -replace '[^A-Za-z0-9]','_')-$(Get-Random).log"
            $pkgLogs[$pkg] = $log
            # --source winget pins us to the github-backed source.  Without this,
            # a broken msstore source (cert validation failures like 0x8a15005e
            # are common on Windows-on-ARM and some corporate networks) makes
            # winget bail with "please specify --source" *before* attempting any
            # install -- and it exits 0, so the surrounding try/catch never fires.
            # We don't ship anything from msstore, so pinning is safe.
            try {
                $output = winget install --exact --id $pkg --source winget --silent `
                    --accept-package-agreements --accept-source-agreements 2>&1
                $code = $LASTEXITCODE
                $output | Out-File -FilePath $log -Encoding utf8
                "winget exit: $code" | Out-File -FilePath $log -Encoding utf8 -Append
                # 0x8A15002B (-1978335189) = APPINSTALLER_CLI_ERROR_UPDATE_NOT_APPLICABLE.
                # winget treats `install` on a package it already has registered as
                # an *upgrade*, finds no newer version, and bails with this code --
                # even when the binary is gone from disk/PATH (stale registration,
                # files removed outside winget, or a missing alias shim). We KNOW the
                # command was missing (that's why we're here), so a plain install
                # dead-ends forever. Force a reinstall to repair the registration so
                # the shim reappears.
                if ($code -eq -1978335189) {
                    "-> already-installed/no-upgrade; retrying with --force" | Out-File -FilePath $log -Encoding utf8 -Append
                    $output = winget install --exact --id $pkg --source winget --silent --force `
                        --accept-package-agreements --accept-source-agreements 2>&1
                    $output | Out-File -FilePath $log -Encoding utf8 -Append
                    "winget exit (force): $LASTEXITCODE" | Out-File -FilePath $log -Encoding utf8 -Append
                }
            } catch {
                $_ | Out-File -FilePath $log -Encoding utf8 -Append
                "winget exit: <exception>" | Out-File -FilePath $log -Encoding utf8 -Append
            }
        }
        # Refresh PATH so packages winget exposed via "command line aliases" in
        # %LOCALAPPDATA%\Microsoft\WinGet\Links (added to PATH only in
        # newly-spawned shells, not this process) are visible to Get-Command below.
        Update-ProcessPathForPackages
        if ($needRipgrep -and (Get-Command rg -ErrorAction SilentlyContinue)) {
            Write-Success "ripgrep installed"
            $script:HasRipgrep = $true
            $needRipgrep = $false
            Remove-Item -Path $pkgLogs["BurntSushi.ripgrep.MSVC"] -ErrorAction SilentlyContinue
        } elseif ($pkgLogs.ContainsKey("BurntSushi.ripgrep.MSVC")) {
            Write-Warn "winget could not install ripgrep; details: $($pkgLogs['BurntSushi.ripgrep.MSVC'])"
        }
        if ($needFfmpeg -and (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
            Write-Success "ffmpeg installed"
            $script:HasFfmpeg = $true
            $needFfmpeg = $false
            Remove-Item -Path $pkgLogs["Gyan.FFmpeg"] -ErrorAction SilentlyContinue
        } elseif ($pkgLogs.ContainsKey("Gyan.FFmpeg")) {
            Write-Warn "winget could not install ffmpeg; details: $($pkgLogs['Gyan.FFmpeg'])"
        }
        if (-not $needRipgrep -and -not $needFfmpeg) { return }
    }

    # Fallback: choco
    if ($hasChoco -and ($needRipgrep -or $needFfmpeg)) {
        Write-Info "Trying Chocolatey..."
        foreach ($pkg in $chocoPkgs) {
            try { choco install $pkg -y 2>&1 | Out-Null } catch { }
        }
        Update-ProcessPathForPackages
        if ($needRipgrep -and (Get-Command rg -ErrorAction SilentlyContinue)) {
            Write-Success "ripgrep installed via chocolatey"
            $script:HasRipgrep = $true
            $needRipgrep = $false
        }
        if ($needFfmpeg -and (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
            Write-Success "ffmpeg installed via chocolatey"
            $script:HasFfmpeg = $true
            $needFfmpeg = $false
        }
    }

    # Fallback: scoop
    if ($hasScoop -and ($needRipgrep -or $needFfmpeg)) {
        Write-Info "Trying Scoop..."
        foreach ($pkg in $scoopPkgs) {
            try { scoop install $pkg 2>&1 | Out-Null } catch { }
        }
        Update-ProcessPathForPackages
        if ($needRipgrep -and (Get-Command rg -ErrorAction SilentlyContinue)) {
            Write-Success "ripgrep installed via scoop"
            $script:HasRipgrep = $true
            $needRipgrep = $false
        }
        if ($needFfmpeg -and (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
            Write-Success "ffmpeg installed via scoop"
            $script:HasFfmpeg = $true
            $needFfmpeg = $false
        }
    }

    # Show manual instructions for anything still missing
    if ($needRipgrep) {
        Write-Warn "ripgrep not installed (file search will use findstr fallback)"
        Write-Info "  winget install BurntSushi.ripgrep.MSVC"
    }
    if ($needFfmpeg) {
        Write-Warn "ffmpeg not installed (TTS voice messages will be limited)"
        Write-Info "  winget install Gyan.FFmpeg"
    }
}

# ============================================================================
# Installation
# ============================================================================

function Install-Repository {
    Write-Info "Installing to $InstallDir..."

    $didUpdate = $false

    if (Test-Path $InstallDir) {
        # Test-Path "$InstallDir\.git" returns True when .git is a file OR a
        # directory OR a symlink OR a submodule-style gitfile -- and also when
        # it's a broken stub left over from a failed previous install (e.g.
        # a partial Remove-Item that couldn't delete a locked index.lock).
        # Validate the repo properly by asking git itself.  Three checks
        # belt-and-braces: rev-parse (work tree), git status, and a resolvable
        # HEAD (an initial commit).  If any fails the repo is broken and we
        # fall through to a fresh clone.
        $repoValid = $false
        if (Test-Path "$InstallDir\.git") {
            Push-Location $InstallDir
            try {
                # Reset $LASTEXITCODE before the probe so we don't pick up
                # a stale 0 from an earlier git call in this session.
                $global:LASTEXITCODE = 0
                $revParseOut = & git -c windows.appendAtomically=false rev-parse --is-inside-work-tree 2>&1
                $revParseOk = ($LASTEXITCODE -eq 0) -and ($revParseOut -match "true")

                $global:LASTEXITCODE = 0
                $null = & git -c windows.appendAtomically=false status --short 2>&1
                $statusOk = ($LASTEXITCODE -eq 0)

                # An interrupted previous clone leaves a repo with NO initial
                # commit. rev-parse/status still succeed there, but the update
                # path's `git stash` (and later `git checkout`) abort with
                # "You do not have the initial commit yet" and fail the install
                # (#40998). Require a resolvable HEAD so such partial checkouts
                # are treated as broken and re-cloned fresh below.
                $global:LASTEXITCODE = 0
                $null = & git -c windows.appendAtomically=false rev-parse --verify HEAD 2>&1
                $hasCommit = ($LASTEXITCODE -eq 0)

                if ($revParseOk -and $statusOk -and $hasCommit) {
                    $repoValid = $true
                }
            } catch {}
            Pop-Location
        }

        if ($repoValid) {
            Write-Info "Existing installation found, updating..."
            Push-Location $InstallDir
            # Wrap the entire fetch+checkout block in EAP=Continue so git's
            # routine stderr output (e.g. 'From <url>' info lines emitted by
            # `git fetch`) doesn't terminate the script under the global
            # EAP=Stop.  We rely on $LASTEXITCODE for actual failures.
            $prevEAP = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            $autostashRef = ""
            try {
                # This is a MANAGED checkout, not a repo the user edits. Git for
                # Windows defaults to core.autocrlf=true, which renormalizes the
                # repo's LF-only text files to CRLF in the working tree -- so
                # tracked files (.envrc, AGENTS.md, agent/*.py, workflows, ...)
                # show as locally modified even though nobody touched them. A
                # bare `git checkout` then aborts with "Your local changes would
                # be overwritten by checkout", which is exactly the failure GUI
                # users hit on update. Pin autocrlf=false so the dirt is never
                # created in the first place.
                git -c windows.appendAtomically=false config core.autocrlf false 2>$null
                Discard-LockfileChurn $InstallDir
                # Preserve any real local changes before the checkout instead of
                # discarding them with `reset --hard HEAD`. The old hard reset
                # silently destroyed agent-edited source on managed clones (the
                # #38542 data-loss class). Stash + restore mirrors install.sh:
                # nothing is lost, and a failed restore leaves the work in a
                # git stash for manual recovery. Untracked files are included so
                # agent-created dirs (e.g. tinker-atropos/) survive too.
                $statusOut = git -c windows.appendAtomically=false status --porcelain 2>$null
                if (-not [string]::IsNullOrWhiteSpace(($statusOut -join "`n"))) {
                    # A previously interrupted update can leave the index with
                    # unmerged entries. In that state `git stash` aborts with
                    # "could not write index" and the following `git checkout`
                    # aborts with "you need to resolve your current index first"
                    # -- the GUI "git checkout main failed (exit 1)" install
                    # failure. Clear the conflict markers with `git reset` first:
                    # working-tree changes are kept (and stashed just below); only
                    # the index conflict state is dropped. Mirrors the `hermes
                    # update` path (#4735).
                    $unmergedOut = git -c windows.appendAtomically=false ls-files --unmerged 2>$null
                    if (-not [string]::IsNullOrWhiteSpace(($unmergedOut -join "`n"))) {
                        Write-Info "Clearing unmerged index entries from a previous conflict..."
                        git -c windows.appendAtomically=false reset -q 2>$null
                    }
                    $stashName = "hermes-install-autostash-" + (Get-Date -Format "yyyyMMdd-HHmmss")
                    Write-Info "Local changes detected, stashing before update..."
                    git -c windows.appendAtomically=false stash push --include-untracked -m "$stashName"
                    if ($LASTEXITCODE -eq 0) { $autostashRef = "stash@{0}" }
                }
                git -c windows.appendAtomically=false fetch origin $Branch
                if ($LASTEXITCODE -ne 0) { throw "git fetch failed (exit $LASTEXITCODE)" }
                # Precedence: Commit > Tag > Branch.  Commit and Tag check
                # out as detached HEAD intentionally -- they're meant to be
                # reproducible pins, not branches the user pulls into.
                if ($Commit) {
                    # Make sure we have the commit locally (a tag-less commit
                    # SHA isn't always reachable from any one branch fetch).
                    git -c windows.appendAtomically=false fetch origin $Commit
                    # A commit pin must never move an existing install
                    # BACKWARDS. hermes-setup.exe bakes its build-time commit
                    # into the binary (BUILD_PIN_COMMIT) and passes it as
                    # -Commit on every install-mode run -- including the retry
                    # the desktop's "Update didn't finish" screen kicks off. An
                    # installer built months ago would otherwise rewind a
                    # current checkout to its build commit, leaving ancient
                    # code against a current venv (npm workspaces and Python
                    # deps that no longer match: the #74xxx report). Skip the
                    # pin when the target is already an ancestor of HEAD; a
                    # fresh clone has no such ancestry and pins normally.
                    $skipRollback = $false
                    if (-not $ForceCommit) {
                        git -c windows.appendAtomically=false merge-base --is-ancestor $Commit HEAD 2>$null
                        $isAncestor = ($LASTEXITCODE -eq 0)
                        $pinnedSha = (& git -c windows.appendAtomically=false rev-parse "$Commit^{commit}" 2>$null)
                        $headSha = (& git -c windows.appendAtomically=false rev-parse HEAD 2>$null)
                        $skipRollback = $isAncestor -and ($pinnedSha -ne $headSha)
                    }
                    if ($skipRollback) {
                        Write-Warn "Ignoring -Commit $Commit`: the checkout is already newer."
                        Write-Warn "Pinning to it would roll this install back. Pass -ForceCommit to override."
                    } else {
                        git -c windows.appendAtomically=false checkout --detach $Commit
                        if ($LASTEXITCODE -ne 0) { throw "git checkout $Commit failed (exit $LASTEXITCODE)" }
                    }
                } elseif ($Tag) {
                    git -c windows.appendAtomically=false fetch origin "refs/tags/${Tag}:refs/tags/${Tag}"
                    git -c windows.appendAtomically=false checkout --detach "refs/tags/$Tag"
                    if ($LASTEXITCODE -ne 0) { throw "git checkout tag $Tag failed (exit $LASTEXITCODE)" }
                } else {
                    git -c windows.appendAtomically=false checkout $Branch
                    if ($LASTEXITCODE -ne 0) { throw "git checkout $Branch failed (exit $LASTEXITCODE)" }
                    # Managed installs should follow origin/$Branch exactly. If
                    # the checkout has diverged (or has local-only commits),
                    # ff-only pull cannot succeed -- mirror ``hermes update`` and
                    # reset to the fetched remote so bootstrap/install can recover.
                    git -c windows.appendAtomically=false pull --ff-only origin $Branch
                    if ($LASTEXITCODE -ne 0) {
                        Write-Warn "Fast-forward not possible; resetting managed install to origin/$Branch..."
                        git -c windows.appendAtomically=false reset --hard "origin/$Branch"
                        if ($LASTEXITCODE -ne 0) { throw "git reset --hard origin/$Branch failed (exit $LASTEXITCODE)" }
                    }
                }

                if ($autostashRef) {
                    # Default to restoring so work is never silently dropped.
                    # Only prompt when we're certain a human can answer: an
                    # interactive session AND a real, non-redirected console on
                    # both stdin and stdout. The desktop "Update" button and
                    # bootstrap run the installer without a usable console -- in
                    # those cases Read-Host would hang or return empty, so we
                    # skip the prompt and just restore (the safe default).
                    $restoreNow = $true
                    $hasConsole = $false
                    try {
                        $hasConsole = (
                            [Environment]::UserInteractive `
                            -and (-not [Console]::IsInputRedirected) `
                            -and (-not [Console]::IsOutputRedirected) `
                            -and ($Host.Name -eq "ConsoleHost")
                        )
                    } catch { $hasConsole = $false }
                    if ($hasConsole) {
                        Write-Warn "Local changes were stashed before updating."
                        Write-Warn "Restoring them may reapply local customizations onto the updated codebase."
                        $restoreAnswer = Read-Host "Restore local changes now? [Y/n]"
                        if ($restoreAnswer -match '^(n|no)$') { $restoreNow = $false }
                    }

                    if ($restoreNow) {
                        Write-Info "Restoring local changes..."
                        $restoreOutput = @(git -c windows.appendAtomically=false stash apply $autostashRef 2>&1)
                        $restoreExit = $LASTEXITCODE
                        $conflictedFiles = @(
                            git -c windows.appendAtomically=false diff --name-only --diff-filter=U 2>$null
                        ) | Where-Object { $_ -and $_.ToString().Trim() }
                        if (($restoreExit -eq 0) -and ($conflictedFiles.Count -eq 0)) {
                            git -c windows.appendAtomically=false stash drop $autostashRef 2>$null
                            Write-Warn "Local changes were restored on top of the updated codebase."
                            Write-Warn "Review git diff / git status if Hermes behaves unexpectedly."
                        } else {
                            Write-Err "Update pulled new code, but restoring local changes hit conflicts."
                            foreach ($line in $restoreOutput) {
                                if ($line -and $line.ToString().Trim()) {
                                    Write-Host $line
                                }
                            }
                            if ($conflictedFiles.Count -gt 0) {
                                Write-Host ""
                                Write-Host "Conflicted files:"
                                foreach ($file in $conflictedFiles) {
                                    Write-Host "  - $file"
                                }
                            }
                            Write-Host ""
                            Write-Info "Your stashed changes are preserved -- nothing is lost."
                            Write-Info "  Stash ref: $autostashRef"
                            git -c windows.appendAtomically=false reset --hard HEAD 2>$null | Out-Null
                            Write-Info "Working tree reset to clean state."
                            Write-Info "Restore your changes later with: git stash apply $autostashRef"
                        }
                    } else {
                        Write-Info "Skipped restoring local changes."
                        Write-Info "Your changes are still preserved in git stash."
                        Write-Info "Restore manually with: git stash apply $autostashRef"
                    }
                    $autostashRef = ""
                }
            } finally {
                if ($autostashRef) {
                    # We stashed but never reached the restore block (a fetch/
                    # checkout/pull failure threw). Leave the stash in place and
                    # tell the user how to recover it -- never silently drop it.
                    Write-Warn "Update did not complete. Your local changes are preserved in git stash."
                    Write-Info "Restore manually with: git stash apply $autostashRef"
                }
                $ErrorActionPreference = $prevEAP
                Pop-Location
            }
            $didUpdate = $true
        } else {
            # Directory exists but isn't a usable git repo -- e.g. an
            # interrupted clone with no initial commit (#40998), or a leftover
            # ``.git`` stub from a partial uninstall that used to lock the
            # installer into the "update" branch forever. Move it aside rather
            # than deleting it -- never destroy a directory the user might still
            # want -- and fall through to a fresh clone.
            $backupDir = "$InstallDir.broken-" + (Get-Date -Format "yyyyMMdd-HHmmss")
            Write-Warn "Existing directory at $InstallDir is not a valid git repo."
            Write-Warn "Moving it aside to $backupDir before re-cloning."
            try {
                Move-Item -LiteralPath $InstallDir -Destination $backupDir -ErrorAction Stop
            } catch {
                Write-Err "Could not move $InstallDir aside : $_"
                Write-Info "Close any programs that might be using files in $InstallDir (editors,"
                Write-Info "terminals, running hermes processes) and try again."
                throw
            }
        }
    }

    if (-not $didUpdate) {
        $cloneSuccess = $false

        # Fix Windows git "copy-fd: write returned: Invalid argument" error.
        # Git for Windows can fail on atomic file operations (hook templates,
        # config lock files) due to antivirus, OneDrive, or NTFS filter drivers.
        # The -c flag injects config before any file I/O occurs.
        Write-Info "Configuring git for Windows compatibility..."
        $env:GIT_CONFIG_COUNT = "1"
        $env:GIT_CONFIG_KEY_0 = "windows.appendAtomically"
        $env:GIT_CONFIG_VALUE_0 = "false"
        git config --global windows.appendAtomically false 2>$null

        # Try SSH first, then HTTPS, with -c flag for atomic write fix
        Write-Info "Trying SSH clone..."
        $env:GIT_SSH_COMMAND = "ssh -o BatchMode=yes -o ConnectTimeout=5"
        try {
            Invoke-NativeWithRelaxedErrorAction { git -c windows.appendAtomically=false clone --depth 1 --branch $Branch $RepoUrlSsh $InstallDir }
            if ($LASTEXITCODE -eq 0) { $cloneSuccess = $true }
        } catch { }
        $env:GIT_SSH_COMMAND = $null

        if (-not $cloneSuccess) {
            if (Test-Path $InstallDir) { Remove-Item -Recurse -Force $InstallDir -ErrorAction SilentlyContinue }
            Write-Info "SSH failed, trying HTTPS..."
            try {
                Invoke-NativeWithRelaxedErrorAction { git -c windows.appendAtomically=false clone --depth 1 --branch $Branch $RepoUrlHttps $InstallDir }
                if ($LASTEXITCODE -eq 0) { $cloneSuccess = $true }
            } catch { }
        }

        # Fallback: download ZIP archive (bypasses git file I/O issues entirely)
        if (-not $cloneSuccess) {
            if (Test-Path $InstallDir) { Remove-Item -Recurse -Force $InstallDir -ErrorAction SilentlyContinue }
            Write-Warn "Git clone failed -- downloading ZIP archive instead..."
            try {
                # Pick the ZIP URL for the most-specific ref the caller asked
                # for.  GitHub supports archive URLs for commits, tags, and
                # branches; we honour Commit > Tag > Branch.
                if ($Commit) {
                    $zipUrl = "https://github.com/NousResearch/hermes-agent/archive/$Commit.zip"
                    $zipLabel = $Commit
                } elseif ($Tag) {
                    $zipUrl = "https://github.com/NousResearch/hermes-agent/archive/refs/tags/$Tag.zip"
                    $zipLabel = $Tag
                } else {
                    $zipUrl = "https://github.com/NousResearch/hermes-agent/archive/refs/heads/$Branch.zip"
                    $zipLabel = $Branch
                }
                $zipPath = "$env:TEMP\hermes-agent-$zipLabel.zip"
                $extractPath = "$env:TEMP\hermes-agent-extract"

                Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath -UseBasicParsing
                if (Test-Path $extractPath) { Remove-Item -Recurse -Force $extractPath }
                Expand-Archive -Path $zipPath -DestinationPath $extractPath -Force

                # GitHub ZIPs extract to repo-branch/ subdirectory
                $extractedDir = Get-ChildItem $extractPath -Directory | Select-Object -First 1
                if ($extractedDir) {
                    New-Item -ItemType Directory -Force -Path (Split-Path $InstallDir) -ErrorAction SilentlyContinue | Out-Null
                    Move-Item $extractedDir.FullName $InstallDir -Force
                    Write-Success "Downloaded and extracted"

                    # Initialize git repo so updates work later. A bare
                    # `git init` leaves NO HEAD -- desktop's write-build-stamp
                    # then hard-fails with "could not determine git commit"
                    # (#50823 / #61657). Fetch the requested ref and force-check
                    # it out (-f) so untracked ZIP files cannot block checkout.
                    Push-Location $InstallDir
                    git -c windows.appendAtomically=false init 2>$null
                    git -c windows.appendAtomically=false config windows.appendAtomically false 2>$null
                    # Pin autocrlf=false BEFORE the checkout below. Git for Windows
                    # defaults to core.autocrlf=true, which would renormalize the
                    # repo's LF text files to CRLF in the working tree during
                    # `checkout -f FETCH_HEAD` -- leaving this freshly-created
                    # managed checkout dirty vs HEAD and aborting the next
                    # `hermes update` (see the notes at the shared clone-path
                    # config below and install.ps1:1461-1469). The later pin on
                    # the shared path is idempotent and still covers git clones.
                    git -c windows.appendAtomically=false config core.autocrlf false 2>$null
                    git remote add origin $RepoUrlHttps 2>$null
                    $fetchRef = if ($Commit) { $Commit } elseif ($Tag) { "refs/tags/$Tag" } else { $Branch }
                    Write-Info "Fetching $fetchRef so the ZIP checkout has a resolvable HEAD..."
                    $prevZipEAP = $ErrorActionPreference
                    $ErrorActionPreference = "Continue"
                    try {
                        git -c windows.appendAtomically=false fetch --depth 1 origin $fetchRef 2>&1 | Out-Null
                        if ($LASTEXITCODE -eq 0) {
                            if ($Commit -or $Tag) {
                                git -c windows.appendAtomically=false checkout -f --detach FETCH_HEAD 2>&1 | Out-Null
                            } else {
                                git -c windows.appendAtomically=false checkout -f -B $Branch FETCH_HEAD 2>&1 | Out-Null
                            }
                            if ($LASTEXITCODE -eq 0) {
                                Write-Success "ZIP checkout pinned to $fetchRef"
                            } else {
                                # Checkout blocked, but FETCH_HEAD still has a SHA we can stamp with.
                                $fetchSha = & git -c windows.appendAtomically=false rev-parse FETCH_HEAD 2>$null
                                if ($LASTEXITCODE -eq 0 -and $fetchSha) {
                                    if (-not $env:GITHUB_SHA) { $env:GITHUB_SHA = ("$fetchSha").Trim() }
                                    Write-Warn "ZIP checkout failed; seeded GITHUB_SHA from FETCH_HEAD for desktop stamp"
                                } else {
                                    Write-Warn "ZIP extract succeeded but git checkout failed -- desktop build may need `$env:GITHUB_SHA"
                                }
                            }
                        } else {
                            Write-Warn "ZIP extract succeeded but git fetch of $fetchRef failed -- desktop build may need `$env:GITHUB_SHA"
                        }
                    } finally {
                        $ErrorActionPreference = $prevZipEAP
                    }
                    Pop-Location
                    Write-Success "Git repo initialized for future updates"

                    $cloneSuccess = $true
                }

                # Cleanup temp files
                Remove-Item -Force $zipPath -ErrorAction SilentlyContinue
                Remove-Item -Recurse -Force $extractPath -ErrorAction SilentlyContinue
            } catch {
                Write-Err "ZIP download also failed: $_"
            }
        }

        if (-not $cloneSuccess) {
            throw "Failed to download repository (tried git clone SSH, HTTPS, and ZIP)"
        }
    }

    # Set per-repo config (harmless if it fails)
    Push-Location $InstallDir
    git -c windows.appendAtomically=false config windows.appendAtomically false 2>$null
    # Pin autocrlf=false on the managed clone so git never renormalizes the
    # repo's LF text files to CRLF in the working tree. Without this, the very
    # next `hermes update` checkout aborts on a "dirty" tree the user never
    # touched (see the update path above).
    git -c windows.appendAtomically=false config core.autocrlf false 2>$null

    # Post-clone pin: when a clone (or ZIP-fallback init) just landed us on
    # $Branch's tip, honour the higher-precedence $Commit / $Tag by checking
    # the exact ref out as a detached HEAD.  Skipped for the in-place update
    # path (above) since that already routed via the same precedence.
    if (-not $didUpdate) {
        # Same EAP=Continue wrap as the update path -- git fetch's 'From <url>'
        # info line goes to stderr and would terminate the script under the
        # global EAP=Stop otherwise.  We check $LASTEXITCODE for real errors.
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            if ($Commit) {
                Write-Info "Pinning to commit $Commit..."
                git -c windows.appendAtomically=false fetch origin $Commit
                git -c windows.appendAtomically=false checkout --detach $Commit
                if ($LASTEXITCODE -ne 0) {
                    throw "git checkout $Commit failed (exit $LASTEXITCODE)"
                }
            } elseif ($Tag) {
                Write-Info "Pinning to tag $Tag..."
                git -c windows.appendAtomically=false fetch origin "refs/tags/${Tag}:refs/tags/${Tag}"
                git -c windows.appendAtomically=false checkout --detach "refs/tags/$Tag"
                if ($LASTEXITCODE -ne 0) {
                    throw "git checkout tag $Tag failed (exit $LASTEXITCODE)"
                }
            }
        } finally {
            $ErrorActionPreference = $prevEAP
        }
    }

    Write-Success "Repository ready"
}

function Install-Venv {
    if ($NoVenv) {
        Write-Info "Skipping virtual environment (-NoVenv)"
        return
    }

    # Re-resolve the interpreter before creating the venv.  Under Hermes-Setup.exe
    # each stage runs in its own powershell.exe, so the fallback the `python`
    # stage picked (e.g. 3.12 when 3.11 is absent) did NOT propagate into this
    # fresh process -- $PythonVersion is back at its "3.11" default.  Trusting it
    # here made `uv venv venv --python 3.11` fail with exit 2 on machines without
    # 3.11 even though the `python` stage reported success (issue #50769).
    $resolvedPython = Resolve-AvailablePythonVersion
    if (-not $resolvedPython) {
        throw "Hermes-managed Python is unavailable. Run install.ps1 -Stage python first."
    }

    Write-Info "Creating virtual environment with Python $($resolvedPython.Version)..."
    
    Push-Location $InstallDir

    # Tasks we disabled below and must re-enable no matter how this stage
    # exits. Populated only with tasks that were ENABLED before we touched
    # them, so a task the user deliberately disabled is never re-armed.
    $gatewayTasksDisabled = @()
    $venvHadExistingVenv = $false
    $venvBackupName = $null
    $venvParked = $false
    try {
    if (Test-Path -LiteralPath "venv") {
        $venvHadExistingVenv = $true
        Write-Info "Virtual environment already exists, recreating..."
        # On Windows, native Python extensions (e.g. _bcrypt.pyd, tornado's
        # speedups.pyd) are loaded as DLLs by any running hermes process.
        # Windows denies deletion of loaded DLLs, so every process running out
        # of this venv must be stopped before retiring it. This keeps cleanup
        # from accumulating locked stale trees and avoids carrying a live
        # gateway into the replacement venv.
        if ($env:OS -eq "Windows_NT") {
            $myPid = $PID
            Write-Info "Stopping any running hermes processes before recreating venv..."
            # Disarm the respawner FIRST: the gateway autostart Scheduled Task
            # relaunches a killed gateway within seconds, and losing that race
            # re-locks the venv's .pyd files between our kill sweep and
            # venv parking/cleanup (the July 2026 _brotlicffi.pyd incident). schtasks
            # /End stops a running task instance; /Change /DISABLE stops it
            # from re-firing mid-install. (The Startup-folder .vbs fallback is
            # NOT touched: it only fires at logon, so it cannot respawn a
            # gateway mid-install.) Re-enabled in the finally below -- including
            # on failure -- but only for tasks that were enabled to begin with.
            # Best-effort: a missing task just errors quietly.
            try {
                schtasks /Query /FO CSV 2>$null | ConvertFrom-Csv | Where-Object { $_.TaskName -like '*Hermes_Gateway*' } | ForEach-Object {
                    $tn = $_.TaskName
                    if ($_.Status -eq 'Disabled') {
                        Write-Info "  gateway autostart task $tn is already disabled; leaving it that way"
                        return
                    }
                    schtasks /End /TN $tn 2>$null | Out-Null
                    schtasks /Change /TN $tn /DISABLE 2>$null | Out-Null
                    $gatewayTasksDisabled += $tn
                    Write-Info "  disabled gateway autostart task $tn for the duration of the install"
                }
            } catch {
                Write-Warn "Could not enumerate gateway scheduled tasks: $($_.Exception.Message)"
            }
            # The launcher CLI (hermes.exe) plus its child tree.
            & taskkill /F /T /IM hermes.exe /FI "PID ne $myPid" 2>$null | Out-Null
            # taskkill /IM hermes.exe is NOT enough: the gateway/agent that a
            # scheduled task or watchdog autostarts runs as
            # `pythonw.exe -m hermes_cli.main gateway run` straight out of
            # venv\Scripts\, so its image name is python/pythonw, not hermes.exe.
            # That process holds the venv's .pyd files open and re-triggers the
            # access-denied failure. Select only roots whose executable lives
            # under this venv, then stop each root's whole process tree. Some
            # Hermes children re-exec through .hermes-runtime, so killing only
            # the selected venv process can leave its child holding the install
            # open. The path-prefix check still keeps unrelated Python processes
            # outside this venv untouched.
            #
            # The gateway autostart task registers with /RL LIMITED as the current
            # user (see hermes_cli/gateway_windows.py), so the installer always
            # runs at equal-or-higher integrity and can read its executable path.
            # Get-CimInstance is used over Get-Process because it returns a null
            # ExecutablePath for a process it cannot inspect (a different session)
            # instead of throwing, so an unreadable process is skipped rather than
            # aborting the whole sweep.
            #
            # The sweep is a bounded LOOP, not single-shot: supervised processes
            # (the Desktop app's backend, a watchdog-managed gateway) respawn in
            # the window between one kill pass and venv parking. Each pass re-
            # enumerates; three consecutive clean passes (or the attempt cap)
            # ends the loop.
            $venvPrefix = [System.IO.Path]::GetFullPath((Join-Path $InstallDir "venv")).TrimEnd('\') + '\'
            $cleanPasses = 0
            for ($sweep = 0; $sweep -lt 10 -and $cleanPasses -lt 3; $sweep++) {
                $found = 0
                try {
                    Get-CimInstance Win32_Process -ErrorAction Stop |
                        Where-Object { $_.ProcessId -ne $myPid -and $_.ExecutablePath -and $_.ExecutablePath.StartsWith($venvPrefix, [System.StringComparison]::OrdinalIgnoreCase) } |
                        ForEach-Object {
                            $found++
                            $treePid = [string]$_.ProcessId
                            Write-Info "  stopping process tree at PID $treePid ($($_.Name)) running from venv"
                            & taskkill /F /T /PID $treePid 2>$null | Out-Null
                        }
                } catch {
                    Write-Warn "Could not enumerate venv processes: $($_.Exception.Message)"
                    break
                }
                if ($found -eq 0) { $cleanPasses++ } else { $cleanPasses = 0 }
                Start-Sleep -Milliseconds 400
            }
        }
        # Move the old venv aside before creating its replacement. A directory
        # rename is atomic on the same volume and does not require deleting
        # files mapped as DLLs. NEVER fall back to deleting the live venv
        # (#83149): Remove-Item -Recurse can delete most of site-packages and
        # then fail on one locked .pyd, leaving a gutted venv with no usable
        # interpreter and no rollback source. Abort with the previous install
        # intact so the user can close holders and retry.
        $venvBackupName = "venv.stale.{0}-{1}" -f (Get-Date -Format "yyyyMMddHHmmss"), ([Guid]::NewGuid().ToString("N"))
        try {
            Rename-Item -LiteralPath "venv" -NewName $venvBackupName -ErrorAction Stop
            $venvParked = $true
        } catch {
            $renameErr = $_.Exception.Message
            throw (
                "Could not move the existing venv aside ($renameErr). " +
                "A process still has the install directory open (often a non-Hermes " +
                "python.exe that resolved into this venv via PATH). Close those " +
                "processes and retry - the previous install was left intact."
            )
        }
    }
    
    # Pass the already-validated private interpreter path and prohibit uv from
    # resolving or downloading a different Python during venv creation. Use
    # ProcessStartInfo because the desktop bootstrapper redirects this script;
    # Windows PowerShell 5.1 can otherwise lose nested native output/exit state.
    $venvProcess = New-Object System.Diagnostics.Process
    try {
        $venvStartInfo = New-Object System.Diagnostics.ProcessStartInfo
        $venvStartInfo.FileName = $UvCmd
        $venvStartInfo.Arguments = "venv venv --python `"$($resolvedPython.Path)`" --managed-python --no-python-downloads --no-config"
        $venvStartInfo.WorkingDirectory = $InstallDir
        $venvStartInfo.UseShellExecute = $false
        $venvStartInfo.CreateNoWindow = $true
        $venvStartInfo.RedirectStandardOutput = $true
        $venvStartInfo.RedirectStandardError = $true
        $venvProcess.StartInfo = $venvStartInfo
        if (-not $venvProcess.Start()) {
            throw "Failed to start uv while creating the virtual environment"
        }
        $venvStdoutTask = $venvProcess.StandardOutput.ReadToEndAsync()
        $venvStderrTask = $venvProcess.StandardError.ReadToEndAsync()
        $venvProcess.WaitForExit()
        $venvStdout = $venvStdoutTask.Result
        $venvStderr = $venvStderrTask.Result
        $venvExitCode = $venvProcess.ExitCode
        if ($venvStdout) { Write-Host $venvStdout.TrimEnd() }
        if ($venvStderr) { Write-Host $venvStderr.TrimEnd() }
    } finally {
        $venvProcess.Dispose()
    }
    # Fail fast so the stage cannot report ok=true when uv failed.
    if ($venvExitCode -ne 0) {
        throw "Failed to create virtual environment (uv venv exited with $venvExitCode)"
    }

    # uv can return success without leaving the interpreter expected by the
    # installer (for example after an interrupted filesystem operation). Treat
    # that as a failed transaction so the previous venv can be restored.
    $venvPythonExe = Join-Path $InstallDir "venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $venvPythonExe -PathType Leaf)) {
        throw "uv reported success but venv interpreter is missing at $venvPythonExe"
    }

    # The replacement has a working interpreter, but the transaction is only
    # committed after Install-Dependencies' baseline-import gate passes -- the
    # bootstrap runs the stages as separate processes, and every dependency
    # tier (or the import validation) can still fail after this stage
    # succeeds. Record the parked backup so the dependency stage can restore
    # it on failure and commit its cleanup only after validation (#83149).
    if ($venvParked) {
        Set-Content -LiteralPath (Join-Path $InstallDir "venv.pending-backup") -Value $venvBackupName -Encoding ascii
        Write-Info "Previous venv parked at $venvBackupName until the dependency install is verified"
    }

    # Clean up parked venvs from previous installs whose handles have since
    # been released. Best-effort -- a still-held tree just stays for next time.
    # The backup parked THIS run is excluded: it is the rollback source until
    # Install-Dependencies commits the transaction.
    Get-ChildItem -Directory -Filter "venv.stale.*" -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -ne $venvBackupName } | ForEach-Object {
            Remove-Item -Recurse -Force $_.FullName -ErrorAction SilentlyContinue
        }

    # Neutralize any inherited UV_PYTHON (e.g. $env:UV_PYTHON = "3.14" left in
    # the user's shell). uv honours UV_PYTHON over an existing venv for the
    # later `uv sync` / `uv pip install` tiers, so without this it would
    # silently delete this 3.11 venv and recreate it at the inherited version
    # -- building Rust transitives that have no wheel for that version from
    # source via maturin, which fails. Pinning UV_PYTHON to the interpreter we
    # just created forces every subsequent uv command onto it.
    $env:UV_PYTHON = $venvPythonExe
    } catch {
        $originalError = $_
        $rollbackError = $null

        if ($venvParked -and $venvBackupName -and (Test-Path -LiteralPath $venvBackupName)) {
            try {
                if (Test-Path -LiteralPath "venv") {
                    $failedVenvName = "venv.failed.{0}-{1}" -f (Get-Date -Format "yyyyMMddHHmmss"), ([Guid]::NewGuid().ToString("N"))
                    Rename-Item -LiteralPath "venv" -NewName $failedVenvName -ErrorAction Stop
                    Write-Warn "Failed replacement parked at $failedVenvName"
                }
                Rename-Item -LiteralPath $venvBackupName -NewName "venv" -ErrorAction Stop
                Write-Warn "Restored previous virtual environment after failed recreate"
            } catch {
                $rollbackError = $_.Exception.Message
            }

            if ($rollbackError) {
                throw "Virtual environment recreate failed: $($originalError.Exception.Message). Rollback failed: $rollbackError. Previous venv remains at $venvBackupName."
            }
        } elseif (-not $venvHadExistingVenv -and (Test-Path -LiteralPath "venv")) {
            # Preserve a partial first install too. This branch must not touch a
            # pre-existing venv whose move-aside failed above.
            try {
                $failedVenvName = "venv.failed.{0}-{1}" -f (Get-Date -Format "yyyyMMddHHmmss"), ([Guid]::NewGuid().ToString("N"))
                Rename-Item -LiteralPath "venv" -NewName $failedVenvName -ErrorAction Stop
                Write-Warn "Partial virtual environment parked at $failedVenvName"
            } catch {
                $rollbackError = $_.Exception.Message
            }
            if ($rollbackError) {
                throw "Virtual environment creation failed: $($originalError.Exception.Message). Could not park partial venv: $rollbackError"
            }
        }

        throw $originalError
    } finally {
        Pop-Location
        # Re-arm the gateway autostart tasks disabled during the venv teardown
        # -- in a finally so a failed teardown/creation can never strand the
        # user's gateway autostart in the disabled state. Same function scope,
        # so the list survives even under the stage-per-process bootstrap.
        # Deliberately NOT started here -- dependencies aren't installed yet;
        # the task fires normally on next logon and `hermes update` / the
        # gateway resume path handles the immediate restart.
        if ($gatewayTasksDisabled -and $gatewayTasksDisabled.Count -gt 0) {
            foreach ($tn in $gatewayTasksDisabled) {
                schtasks /Change /TN $tn /ENABLE 2>$null | Out-Null
            }
            Write-Info "Re-enabled gateway autostart task(s): $($gatewayTasksDisabled -join ', ')"
        }
    }

    Write-Success "Virtual environment ready (Python $($resolvedPython.Version))"
}

function Get-PendingVenvBackup {
    # Rollback source recorded by Install-Venv (#83149). Returns the parked
    # directory name, or $null when there is nothing to roll back to. A marker
    # pointing at a directory that no longer exists is stale -- drop it.
    $markerPath = Join-Path $InstallDir "venv.pending-backup"
    if (-not (Test-Path -LiteralPath $markerPath -PathType Leaf)) { return $null }
    $name = (Get-Content -LiteralPath $markerPath -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($name) { $name = $name.Trim() }
    if (-not $name -or -not (Test-Path -LiteralPath (Join-Path $InstallDir $name))) {
        Remove-Item -LiteralPath $markerPath -Force -ErrorAction SilentlyContinue
        return $null
    }
    return $name
}

function Complete-VenvTransaction {
    # Commit: dependency install + baseline imports passed, so the previous
    # venv is no longer needed as a rollback source. Best-effort delete; a
    # tree still held open just stays parked for the next install's sweep.
    $backupName = Get-PendingVenvBackup
    if (-not $backupName) { return }
    $backupPath = Join-Path $InstallDir $backupName
    Remove-Item -LiteralPath $backupPath -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $backupPath) {
        Write-Warn "Old venv parked at $backupName (a process still holds files in it); it will be cleaned up on the next install"
    }
    Remove-Item -LiteralPath (Join-Path $InstallDir "venv.pending-backup") -Force -ErrorAction SilentlyContinue
}

function Restore-VenvBackup {
    # Rollback: the dependency stage failed after Install-Venv replaced the
    # venv. Park the unusable replacement and restore the previous working
    # venv so Hermes (and the venv-blocker probe) stay usable (#83149).
    $backupName = Get-PendingVenvBackup
    if (-not $backupName) { return }
    try {
        if (Test-Path -LiteralPath (Join-Path $InstallDir "venv")) {
            $failedVenvName = "venv.failed.{0}-{1}" -f (Get-Date -Format "yyyyMMddHHmmss"), ([Guid]::NewGuid().ToString("N"))
            Rename-Item -LiteralPath (Join-Path $InstallDir "venv") -NewName $failedVenvName -ErrorAction Stop
            Write-Warn "Failed replacement parked at $failedVenvName"
        }
        Rename-Item -LiteralPath (Join-Path $InstallDir $backupName) -NewName "venv" -ErrorAction Stop
        Remove-Item -LiteralPath (Join-Path $InstallDir "venv.pending-backup") -Force -ErrorAction SilentlyContinue
        Write-Warn "Restored previous virtual environment after failed dependency install"
    } catch {
        Write-Warn "Could not restore previous venv (still parked at $backupName): $($_.Exception.Message)"
    }
}

function Install-Dependencies {
    Write-Info "Installing dependencies..."
    
    Push-Location $InstallDir
    
    if (-not $NoVenv) {
        # Tell uv to install into our venv (no activation needed)
        $env:VIRTUAL_ENV = "$InstallDir\venv"
    }

    # Re-pin UV_PYTHON to the venv interpreter. Install-Venv already does this,
    # but the bootstrap runs install stages (venv, python-deps) as separate
    # processes, so the env var set in Install-Venv does NOT survive into a
    # separate python-deps invocation. Re-deriving it here covers that path.
    # Without it, an inherited $env:UV_PYTHON = "3.14" makes the uv sync/pip
    # tiers below recreate the venv at 3.14 and fail the maturin source build
    # (no cp314 wheels yet).
    if (-not $NoVenv) {
        $venvPythonExe = Join-Path $InstallDir "venv\Scripts\python.exe"
        if (Test-Path $venvPythonExe) {
            $env:UV_PYTHON = $venvPythonExe
        }
    }

    # Hash-verified install (Tier 0) -- when uv.lock is present, prefer
    # `uv sync --locked`. The lockfile records SHA256 hashes for every
    # transitive dependency, so a compromised transitive (different hash
    # than what we shipped) is REJECTED by the resolver. This is the
    # *only* path that protects against the "direct dep is fine, but the
    # dep's dep got worm-poisoned overnight" failure mode. The
    # `uv pip install` tiers below re-resolve transitives fresh from PyPI
    # without any hash verification -- they exist to keep installs working
    # when the lockfile is stale, missing, or out-of-sync with the
    # current extras spec, NOT because they're equivalent in posture.
    #
    # Everything through the baseline-import gate runs inside the venv
    # transaction opened by Install-Venv (#83149): on any failure the parked
    # previous venv is restored before the error propagates, and the parked
    # tree is deleted only after the imports prove the replacement usable.
    try {
    if (Test-Path "uv.lock") {
        Write-Info "Trying tier: hash-verified (uv.lock) ..."
        # Critical flag choice: `--extra all`, NOT `--all-extras`.
        #   --all-extras = every [project.optional-dependencies] key,
        #                  bypassing the curated [all] extra. On Windows
        #                  that means [matrix] -> python-olm (no wheel,
        #                  needs `make` to build from sdist) and the
        #                  install fails.
        #   --extra all  = just the [all] extra's contents (curated).
        #
        # UV_PROJECT_ENVIRONMENT pins the sync target to our venv\.
        # Without it, modern uv (>=0.5) ignores VIRTUAL_ENV for `sync`
        # and creates a sibling .venv\ inside the repo -- leaving venv\
        # empty and producing the broken state where `hermes.exe` exists
        # in the wrong directory and imports fail with ModuleNotFoundError.
        # (Mirrors the same flag in scripts/install.sh::install_deps.)
        $env:UV_PROJECT_ENVIRONMENT = "$InstallDir\venv"
        Invoke-NativeWithRelaxedErrorAction { & $UvCmd sync --extra all --locked }
        if ($LASTEXITCODE -eq 0) {
            Write-Success "Main package installed (hash-verified via uv.lock)"
            $script:InstalledTier = "hash-verified (uv.lock)"
            # Skip the rest of the tiered cascade -- we already have a
            # complete, hash-verified install.
            $skipPipFallback = $true
        } else {
            Write-Warn "uv.lock sync failed (lockfile may be stale), falling back to PyPI resolve..."
            $skipPipFallback = $false
        }
    } else {
        Write-Info "uv.lock not found -- falling back to PyPI resolve (no hash verification)"
        $skipPipFallback = $false
    }

    # Install main package.  Tiered fallback so a single flaky transitive
    # doesn't silently drop everything.  Each tier's stdout/stderr is
    # preserved -- no Out-Null swallowing -- so the user can see what failed.
    #
    # Tier 1: [all] -- the curated extra in pyproject.toml.
    # Tier 2: [all] minus the currently-broken extras list ($brokenExtras).
    #         Edit $brokenExtras below when something on PyPI breaks; this
    #         lets users keep the rest of [all] when one transitive is
    #         unavailable. The list of [all]'s contents is parsed from
    #         pyproject.toml at runtime -- there is NO hand-mirrored copy
    #         to drift out of sync.
    # Tier 3: bare `.` -- last-resort so at least the core CLI launches.

    # Currently-broken extras. Edit this list when an upstream package
    # gets quarantined / yanked / breaks resolution. Empty means everything
    # in [all] should be installable; populate with the names of extras
    # whose deps are temporarily unavailable.
    $brokenExtras = @()

    # Parse [project.optional-dependencies].all from pyproject.toml.
    # tomllib is stdlib on Python 3.14+ which the bootstrap guarantees.
    $pythonExeForParse = if (-not $NoVenv) { "$InstallDir\venv\Scripts\python.exe" } else { (& $UvCmd python find $PythonVersion) }
    $allExtras = @()
    if (Test-Path $pythonExeForParse) {
        $parsed = & $pythonExeForParse -c @"
import re, sys, tomllib
try:
    with open('pyproject.toml', 'rb') as fh:
        data = tomllib.load(fh)
    specs = data['project']['optional-dependencies']['all']
    out = []
    for s in specs:
        m = re.search(r'hermes-agent\[([\w-]+)\]', s)
        if m: out.append(m.group(1))
    print(','.join(out))
except Exception:
    sys.exit(1)
"@ 2>$null
        if ($LASTEXITCODE -eq 0 -and $parsed) {
            $allExtras = $parsed.Trim().Split(',')
        }
    }
    if (-not $allExtras -or $allExtras.Count -eq 0) {
        Write-Warn "Could not parse [all] from pyproject.toml; Tier 2 will be a no-op."
        $safeAll = "all"
    } else {
        $safeAll = ($allExtras | Where-Object { $brokenExtras -notcontains $_ }) -join ","
    }
    $brokenLabel = if ($brokenExtras) { ($brokenExtras -join ", ") } else { "none" }

    $installTiers = @(
        @{ Name = "all"; Spec = ".[all]" },
        @{ Name = "all minus known-broken ($brokenLabel)"; Spec = ".[$safeAll]" },
        @{ Name = "core only (no extras)"; Spec = "." }
    )
    $installed = $skipPipFallback
    if (-not $skipPipFallback) {
        foreach ($tier in $installTiers) {
        Write-Info "Trying tier: $($tier.Name) ..."
        Invoke-NativeWithRelaxedErrorAction { & $UvCmd pip install -e $tier.Spec }
        if ($LASTEXITCODE -eq 0) {
            Write-Success "Main package installed ($($tier.Name))"
            $script:InstalledTier = $tier.Name
            $installed = $true
            break
        }
        Write-Warn "Tier '$($tier.Name)' failed (exit $LASTEXITCODE). Trying next tier..."
        }
    }
    if (-not $installed) {
        throw "Failed to install hermes-agent package even with no extras. Inspect the uv pip install output above."
    }

    # Baseline-import gate. Even if a tier reported success above, the
    # actual deps may have landed somewhere other than $InstallDir\venv\
    # (e.g. uv 0.5+ syncing into a sibling .venv\ when UV_PROJECT_ENVIRONMENT
    # isn't set, leaving venv\ empty and hermes.exe broken with
    # `ModuleNotFoundError: No module named 'dotenv'` on first run).
    # We probe via the venv's own python so a misdirected sync is caught
    # here, not 30 seconds later when the user runs `hermes`.
    if (-not $NoVenv) {
        $venvPython = "$InstallDir\venv\Scripts\python.exe"
        if (-not (Test-Path $venvPython)) {
            throw "Install reported success but $venvPython does not exist. The dependency sync likely landed in a sibling .venv\ directory. Re-run the installer; if it persists, close Hermes processes and preserve existing venv directories before retrying. Do not delete venv in place."
        }
        # Relax EAP=Stop while running the import probe.  Python writes
        # deprecation warnings and import-system info to stderr; under
        # EAP=Stop the 2>&1 merge wraps those as ErrorRecord objects and
        # throws even when the imports succeed.  $LASTEXITCODE is the
        # reliable signal (it's 0 iff the python invocation exited 0,
        # regardless of what was written to stderr).
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        & $venvPython -c "import dotenv, openai, rich, prompt_toolkit" 2>&1 | Out-Null
        $importExitCode = $LASTEXITCODE
        $ErrorActionPreference = $prevEAP
        if ($importExitCode -ne 0) {
            $sibling = "$InstallDir\.venv"
            $hint = if (Test-Path $sibling) {
                "Detected sibling .venv\ at $sibling -- uv synced there instead of venv\. Close Hermes processes, preserve the existing venv, and rerun the installer so the transactional recovery path can move directories safely."
            } else {
                "Recover with: cd '$InstallDir'; `$env:UV_PROJECT_ENVIRONMENT='$InstallDir\venv'; uv sync --extra all --locked"
            }
            throw "Baseline imports failed in $InstallDir\venv (dotenv/openai/rich/prompt_toolkit). The install completed but dependencies are not in the venv. $hint"
        }
        Write-Success "Baseline imports verified in venv"
    }

    # Commit the venv transaction: the dependency install completed and the
    # baseline imports passed, so the previous venv is no longer needed as a
    # rollback source (#83149).
    Complete-VenvTransaction
    } catch {
        # Dependency install or import validation failed: restore the previous
        # working venv (parked by Install-Venv) before surfacing the error, so
        # a failed update leaves Hermes and its blocker probe usable.
        Restore-VenvBackup
        Pop-Location
        throw
    }

    if (-not $NoVenv) {
        # uv on Windows can register hermes.exe in dist-info/RECORD but fail to
        # materialise the .exe (file lock during self-update, distlib edge case).
        # Catch it here so a fresh install/update does not finish with a broken
        # `hermes` command while hermes-agent.exe / hermes-acp.exe exist
        $scriptsDir = Join-Path $InstallDir "venv\Scripts"
        $pythonExe = Join-Path $scriptsDir "python.exe"
        if ((Test-Path $scriptsDir) -and (Test-Path $pythonExe)) {
            $scriptNames = & $pythonExe -c @"
import tomllib
with open('pyproject.toml', 'rb') as fh:
    scripts = tomllib.load(fh).get('project', {}).get('scripts', {}) or {}
print(','.join(scripts))
"@ 2>$null
            if ($LASTEXITCODE -eq 0 -and $scriptNames) {
                $expected = @($scriptNames.Trim().Split(',') | Where-Object { $_ })
                $missing = @()
                foreach ($name in $expected) {
                    $exe = Join-Path $scriptsDir "$name.exe"
                    if (-not (Test-Path $exe)) { $missing += "$name.exe" }
                }
                if ($missing.Count -gt 0) {
                    Write-Warn "Console entry point(s) missing: $($missing -join ', ')"
                    Write-Info "Reinstalling entry points..."
                    $env:UV_PROJECT_ENVIRONMENT = "$InstallDir\venv"
                    Invoke-NativeWithRelaxedErrorAction { & $UvCmd pip install --reinstall -e . }
                    $stillMissing = @()
                    foreach ($name in $expected) {
                        $exe = Join-Path $scriptsDir "$name.exe"
                        if (-not (Test-Path $exe)) { $stillMissing += "$name.exe" }
                    }
                    if ($stillMissing.Count -gt 0) {
                        Write-Warn "Entry points still missing after repair: $($stillMissing -join ', ')"
                        Write-Info "Workaround: `"$pythonExe`" -m hermes_cli.main <command>"
                    } else {
                        Write-Success "Console entry points restored"
                    }
                }
            }
        }
    }

    # Verify the dashboard deps specifically -- they're the most common thing
    # users hit and lazy-import errors from `hermes dashboard` are confusing.
    # If tier 1 failed (the common case), [web] was still picked up by tiers
    # 2-3; only tier 4 leaves you without it.
    $pythonExe = if (-not $NoVenv) { "$InstallDir\venv\Scripts\python.exe" } else { (& $UvCmd python find $PythonVersion) }
    if (Test-Path $pythonExe) {
        $webOk = $false
        $webServerSyntaxOk = $false
        # Relax EAP=Stop while running the import probe; see the matching
        # comment on the baseline-imports check above.  Python writes
        # deprecation warnings to stderr and we don't want those wrapped
        # as ErrorRecords that silently force the "not importable" path
        # even when fastapi/uvicorn are actually installed.
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            & $pythonExe -c "import fastapi, uvicorn" 2>&1 | Out-Null
            if ($LASTEXITCODE -eq 0) { $webOk = $true }
        } catch { }
        try {
            & $pythonExe -m py_compile "$InstallDir\hermes_cli\web_server.py" 2>&1 | Out-Null
            if ($LASTEXITCODE -eq 0) { $webServerSyntaxOk = $true }
        } catch { }
        $ErrorActionPreference = $prevEAP
        if (-not $webOk) {
            Write-Warn "fastapi/uvicorn not importable -- `hermes dashboard` will not work."
            Write-Info "Attempting targeted install of [web] extra as last resort..."
            & $UvCmd pip install -e ".[web]"
            if ($LASTEXITCODE -eq 0) {
                Log "Granted AppContainer read access on $appDir"
            } else {
                Write-Warn "icacls AppContainer grant returned exit $LASTEXITCODE for $appDir"
            }
        } catch {
            Write-Warn "Could not grant AppContainer ACL: $($_.Exception.Message)"
        }
    } finally {
        Pop-Location
    }
    New-DesktopShortcuts -TargetExe $desktopExe
}

function Stage-Complete {
    $commit = $Commit
    if (-not $commit) {
        if (-not (Ensure-Git)) { Fail "no pinned Git artifact for this Windows architecture" unsupported_platform }
        $commit = Invoke-Native { git -C $InstallDir rev-parse HEAD 2>$null }
    }
    if ($commit) {
        $marker = [ordered]@{
            schemaVersion = 1
            pinnedCommit = "$commit"
            pinnedBranch = $Branch
            completedAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
        }
        $marker | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $InstallDir ".hermes-bootstrap-complete") -Encoding UTF8
        Write-Ok "Hermes Agent install complete (pinned $commit). Run: hermes"
    }
}

function New-DesktopShortcuts {
    param([Parameter(Mandatory = $true)][string]$TargetExe)

    # Best-effort: a shortcut failure must never fail an otherwise-good install.
    try {
        $shell = New-Object -ComObject WScript.Shell
        $workDir = Split-Path -Parent $TargetExe

        # Prefer the standalone icon.ico (shipped beside the exe via
        # electron-builder extraResources -> resources/icon.ico) over the exe's
        # embedded resource. An explicit .ico path is more stable across update
        # cycles: pointing at "$TargetExe,0" makes Windows cache the icon it
        # extracted from the exe at shortcut-creation time, and that cached
        # bitmap can persist (showing the OLD/Electron icon) even after the exe
        # is re-stamped on update. A dedicated .ico sidesteps that extraction.
        $iconIco = Join-Path $workDir 'resources\icon.ico'
        if (Test-Path $iconIco) {
            $iconLocation = "$iconIco,0"
        } else {
            $iconLocation = "$TargetExe,0"
        }

        $targets = @(
            (Join-Path ([Environment]::GetFolderPath('Programs')) 'Hermes.lnk'),
            (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Hermes.lnk')
        )

        foreach ($lnkPath in $targets) {
            try {
                $parent = Split-Path -Parent $lnkPath
                if (-not (Test-Path $parent)) {
                    New-Item -ItemType Directory -Force -Path $parent | Out-Null
        }
                $sc = $shell.CreateShortcut($lnkPath)
                $sc.TargetPath = $TargetExe
                $sc.WorkingDirectory = $workDir
                $sc.IconLocation = $iconLocation
                $sc.Description = 'Hermes Agent'
                $sc.Save()
                Write-Ok "Shortcut created: $lnkPath"
            } catch {
                Write-Warn "Could not create shortcut $lnkPath : $($_.Exception.Message)"
            }
        }

        # Bust the Windows shell icon cache so the desktop/Start-Menu shortcut
        # repaints with the (possibly newly-stamped) icon instead of a stale
        # cached bitmap. Critical on the --update path: the exe was re-stamped
        # with the Hermes icon, but without this the shortcut can keep drawing
        # the old Electron icon until the user manually refreshes / reboots.
        # Best-effort and silent -- never fail the install over a cosmetic cache.
        try {
            Invoke-Native { & ie4uinit.exe -show 2>$null }
        } catch {
            # ie4uinit may be absent/renamed on some SKUs -- ignore.
        }
    } catch {
        Write-Warn "Skipping shortcut creation: $($_.Exception.Message)"
    }
}

function Invoke-StageByName([string]$name) {
    switch ($name) {
        "prerequisites" { Stage-Prerequisites }
        "repository" { Stage-Repository }
        "venv" { Stage-Venv }
        "python-deps" { Stage-PythonDeps }
        "products" { Stage-Products }
        "config" { Stage-Config }
        "setup" { Stage-Setup }
        "gateway" { Stage-Gateway }
        "desktop" { Stage-Desktop }
        "complete" { Stage-Complete }
        default { Write-Error "unknown stage: $name"; exit 2 }
    }
}

# --- Dot-source guard (part 2: stop before entry) ----------------------------
# Every function definition above has loaded; now stop before any real work.
if ($script:IsDotSourced) {
    Write-Verbose "[hermes] install.ps1 was dot-sourced; definitions only, no execution"
function Invoke-SetupWizard {
    if ($SkipSetup) {
        Write-Info "Skipping setup wizard (-SkipSetup)"
        return
    }

    if ($NonInteractive) {
        # The setup wizard prompts for API keys, model choice, persona, etc.
        # Non-interactive callers (GUI installer) own that UX themselves; let
        # them drive it after install.ps1 returns.
        Write-Info "Skipping setup wizard (non-interactive). Configure via the GUI or 'hermes setup'."
        return
    }

    Write-Host ""
    Write-Info "Starting setup wizard..."
    Write-Host ""

    Push-Location $InstallDir

    # Run hermes setup using the venv Python directly (no activation needed)
    if (-not $NoVenv) {
        & ".\venv\Scripts\python.exe" -m hermes_cli.main setup
    } else {
        python -m hermes_cli.main setup
    }

    Pop-Location
}

function Start-GatewayIfConfigured {
    $envPath = "$HermesHome\.env"
    if (-not (Test-Path $envPath)) { return }

    $hasMessaging = $false
    $content = Get-Content $envPath -ErrorAction SilentlyContinue
    foreach ($var in @("TELEGRAM_BOT_TOKEN", "DISCORD_BOT_TOKEN", "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "WHATSAPP_ENABLED")) {
        $match = $content | Where-Object { $_ -match "^${var}=.+" -and $_ -notmatch "your-token-here" }
        if ($match) { $hasMessaging = $true; break }
    }

    if (-not $hasMessaging) { return }

    $hermesCmd = "$InstallDir\venv\Scripts\hermes.exe"
    if (-not (Test-Path $hermesCmd)) {
        $hermesCmd = "hermes"
    }

    # If WhatsApp is enabled but not yet paired, run foreground for QR scan
    $whatsappEnabled = $content | Where-Object { $_ -match "^WHATSAPP_ENABLED=true" }
    $whatsappSession = "$HermesHome\whatsapp\session\creds.json"
    if ($whatsappEnabled -and -not (Test-Path $whatsappSession)) {
        Write-Host ""
        Write-Info "WhatsApp is enabled but not yet paired."
        Write-Info "Running 'hermes whatsapp' to pair via QR code..."
        Write-Host ""
        # Non-interactive callers (GUI installer, CI) skip the QR-pair prompt;
        # WhatsApp pairing requires a human looking at a phone camera, so the
        # downstream UI is responsible for surfacing this when it makes sense.
        if (-not $NonInteractive) {
            $response = Read-Host "Pair WhatsApp now? [Y/n]"
            if ($response -eq "" -or $response -match "^[Yy]") {
                try {
                    & $hermesCmd whatsapp
                } catch {
                    # Expected after pairing completes
                }
            }
        } else {
            Write-Info "Skipping WhatsApp pairing prompt (non-interactive)."
        }
    }

    Write-Host ""
    Write-Info "Messaging platform token detected!"
    Write-Info "The gateway handles messaging platforms and cron job execution."
    Write-Host ""

    # In non-interactive mode the gateway lifecycle is the caller's problem
    # (the GUI manages its own gateway process, CI doesn't want background
    # services on the build agent, etc.).  Treat it like the user declined.
    if ($NonInteractive) {
        Write-Info "Skipping gateway autostart prompt (non-interactive)."
        Write-Info "Start the gateway later with: hermes gateway"
        return
    }

    $response = Read-Host "Would you like to start the gateway now? [Y/n]"

    if ($response -eq "" -or $response -match "^[Yy]") {
        Write-Info "Starting gateway in background..."
        try {
            $logFile = "$HermesHome\logs\gateway.log"
            Start-Process -FilePath $hermesCmd -ArgumentList "gateway" `
                -RedirectStandardOutput $logFile `
                -RedirectStandardError "$HermesHome\logs\gateway-error.log" `
                -WindowStyle Hidden
            Write-Success "Gateway started! Your bot is now online."
            Write-Info "Logs: $logFile"
            Write-Info "To stop: close the gateway process from Task Manager"
        } catch {
            Write-Warn "Failed to start gateway. Run manually: hermes gateway"
        }
    } else {
        Write-Info "Skipped. Start the gateway later with: hermes gateway"
    }
}

function Write-Completion {
    Write-Host ""
    Write-Host "+---------------------------------------------------------+" -ForegroundColor Green
    Write-Host "|              [OK] Installation Complete!                |" -ForegroundColor Green
    Write-Host "+---------------------------------------------------------+" -ForegroundColor Green
    Write-Host ""
    
    # Show file locations
    Write-Host "* Your files:" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "   Config:    " -NoNewline -ForegroundColor Yellow
    Write-Host "$HermesHome\config.yaml"
    Write-Host "   API Keys:  " -NoNewline -ForegroundColor Yellow
    Write-Host "$HermesHome\.env"
    Write-Host "   Data:      " -NoNewline -ForegroundColor Yellow
    Write-Host "$HermesHome\cron\, sessions\, logs\"
    Write-Host "   Code:      " -NoNewline -ForegroundColor Yellow
    Write-Host "$HermesHome\hermes-agent\"
    Write-Host ""
    
    Write-Host "---------------------------------------------------------" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "* Commands:" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "   hermes              " -NoNewline -ForegroundColor Green
    Write-Host "Start chatting"
    Write-Host "   hermes setup        " -NoNewline -ForegroundColor Green
    Write-Host "Configure API keys & settings"
    Write-Host "   hermes config       " -NoNewline -ForegroundColor Green
    Write-Host "View/edit configuration"
    Write-Host "   hermes config edit  " -NoNewline -ForegroundColor Green
    Write-Host "Open config in editor"
    Write-Host "   hermes gateway      " -NoNewline -ForegroundColor Green
    Write-Host "Start messaging gateway (Telegram, Discord, etc.)"
    Write-Host "   hermes update       " -NoNewline -ForegroundColor Green
    Write-Host "Update to latest version"
    Write-Host ""
    
    Write-Host "---------------------------------------------------------" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "[*] Restart your terminal for PATH changes to take effect" -ForegroundColor Yellow
    Write-Host ""
    
    if (-not $HasNode) {
        Write-Host "Note: Node.js could not be installed automatically." -ForegroundColor Yellow
        Write-Host "Browser tools need Node.js. Install manually:" -ForegroundColor Yellow
        Write-Host "  https://nodejs.org/en/download/" -ForegroundColor Yellow
        Write-Host ""
    }
    
    if (-not $HasRipgrep) {
        Write-Host "Note: ripgrep (rg) was not installed. For faster file search:" -ForegroundColor Yellow
        Write-Host "  winget install BurntSushi.ripgrep.MSVC" -ForegroundColor Yellow
        Write-Host ""
    }
    
    if (-not $HasCoreutils -and ($env:OS -eq "Windows_NT")) {
        Write-Host "Note: Microsoft Coreutils was not installed. For POSIX CLI tools (cat, cp, mv, ls):" -ForegroundColor Yellow
        Write-Host "  winget install Microsoft.Coreutils" -ForegroundColor Yellow
        Write-Host ""
    }
}

# ============================================================================
# Stage protocol
# ============================================================================
#
# install.ps1 supports a small, stable "stage protocol" that lets programmatic
# callers (the desktop GUI's onboarding wizard, CI, future install.sh, etc.)
# drive the install one step at a time and surface progress/errors with their
# own UI.  CLI users running the canonical `irm | iex` one-liner never
# encounter this -- default invocation behaves exactly as before.
#
# Entry points:
#
#   install.ps1                       Interactive install (today's behavior).
#   install.ps1 -ProtocolVersion      Emit the protocol version integer.
#   install.ps1 -Manifest             Emit the stage manifest as JSON.
#   install.ps1 -Stage <name>         Run one stage and emit its result.
#   install.ps1 -NonInteractive       Disable all Read-Host prompts (also
#                                     skips the setup wizard and the gateway
#                                     autostart prompt).  Can be combined
#                                     with default invocation to do a full
#                                     non-interactive install.
#   install.ps1 -Json                 Emit machine-readable JSON instead of
#                                     the human-readable success banner at
#                                     the end of a full install.
#
# Manifest schema (the JSON returned by -Manifest):
#
#   {
#     "protocol_version": 1,
#     "stages": [
#       {
#         "name": "uv",
#         "title": "Installing uv package manager",
#         "category": "prereqs",
#         "needs_user_input": false
#       },
#       ...
#     ]
#   }
#
# Stage result (the JSON written by -Stage <name>):
#
#   {
#     "stage": "uv",
#     "ok": true,
#     "skipped": false,
#     "reason": null,
#     "duration_ms": 1234
#   }
#
# Exit codes:
#
#   0 -- success (stage ran, or stage was deliberately skipped).
#   1 -- generic failure; the stage threw.
#   2 -- unknown stage name passed to -Stage.
#
# Adding a stage:
#
#   1. Append an entry to $InstallStages below.
#   2. Make sure the worker function it points at is idempotent and respects
#      $NonInteractive when it has prompts.  Add it before "configure"
#      (the wizard) or "gateway" (autostart) if it should run unconditionally;
#      after those if it's optional post-install glue.
#   3. Do NOT bump $InstallStageProtocolVersion -- adding stages is additive.
#      Drivers iterate the manifest dynamically.
#
# ============================================================================

# Stage definitions -- the single source of truth.  Each entry maps a stable
# stage name (the API contract drivers depend on) to the worker function that
# implements it.  ``Title`` is what UIs show; ``Category`` lets UIs group
# stages; ``NeedsUserInput`` tells UIs "this stage prompts -- either skip it
# or arrange to provide answers another way."
$InstallStages = @(
    @{ Name = "uv";               Title = "Installing uv package manager";        Category = "prereqs";      NeedsUserInput = $false; Worker = "Stage-Uv" }
    @{ Name = "git";              Title = "Installing Git";                       Category = "prereqs";      NeedsUserInput = $false; Worker = "Stage-Git" }
    @{ Name = "node";             Title = "Detecting Node.js";                    Category = "prereqs";      NeedsUserInput = $false; Worker = "Stage-Node" }
    @{ Name = "system-packages";  Title = "Installing ripgrep, ffmpeg, and Coreutils";        Category = "prereqs";      NeedsUserInput = $false; Worker = "Stage-SystemPackages" }
    @{ Name = "repository";       Title = "Cloning Hermes repository";            Category = "install";      NeedsUserInput = $false; Worker = "Stage-Repository" }
    # Managed Python lives under $InstallDir\.hermes-runtime, so the checkout
    # must exist before this stage creates that directory. Otherwise the later
    # repository stage treats the runtime-only directory as a broken checkout,
    # parks it, and leaves Stage-Venv with no managed interpreter.
    @{ Name = "python";           Title = "Verifying Python $PythonVersion";      Category = "prereqs";      NeedsUserInput = $false; Worker = "Stage-Python" }
    @{ Name = "venv";             Title = "Creating Python virtual environment";  Category = "install";      NeedsUserInput = $false; Worker = "Stage-Venv" }
    @{ Name = "dependencies";     Title = "Installing Python dependencies";       Category = "install";      NeedsUserInput = $false; Worker = "Stage-Dependencies" }
    @{ Name = "node-deps";        Title = "Installing Node.js dependencies";      Category = "install";      NeedsUserInput = $false; Worker = "Stage-NodeDeps" }
)
if ($IncludeDesktop) {
    # Insert AFTER node-deps so workspace npm is already installed when
    # the desktop build runs. Inserted only when explicitly requested
    # (Hermes-Setup.exe), never via the irm|iex CLI one-liner.
    $InstallStages += @{ Name = "desktop"; Title = "Building desktop app"; Category = "install"; NeedsUserInput = $false; Worker = "Stage-Desktop" }
}
$InstallStages += @(
    @{ Name = "path";             Title = "Adding Hermes to PATH";                Category = "finalize";     NeedsUserInput = $false; Worker = "Stage-Path" }
    @{ Name = "config-templates"; Title = "Writing configuration templates";      Category = "finalize";     NeedsUserInput = $false; Worker = "Stage-ConfigTemplates" }
    @{ Name = "platform-sdks";    Title = "Installing messaging platform SDKs";   Category = "finalize";     NeedsUserInput = $false; Worker = "Stage-PlatformSdks" }
    @{ Name = "bootstrap-marker"; Title = "Marking install complete";              Category = "finalize";     NeedsUserInput = $false; Worker = "Stage-BootstrapMarker" }
    # Interactive stages.  In non-interactive mode these become no-ops; the
    # caller (GUI / CI) handles the equivalent UX themselves.
    @{ Name = "configure";        Title = "Configuring API keys and models";      Category = "post-install"; NeedsUserInput = $true;  Worker = "Stage-Configure" }
    @{ Name = "gateway";          Title = "Starting messaging gateway";           Category = "post-install"; NeedsUserInput = $true;  Worker = "Stage-Gateway" }
)

# Stage workers -- thin wrappers that delegate to the existing Install-* /
# Test-* / Invoke-* functions while preserving their error semantics.  Kept
# as a separate layer so the existing functions remain callable directly
# (helpful for one-off recovery: ``. install.ps1; Install-Venv``).
#
# Stages that depend on uv (anything after Stage-Uv) call Resolve-UvCmd
# first so they work in cross-process driver mode where $script:UvCmd
# set by Stage-Uv in a sibling powershell process is not visible here.
# Resolve-UvCmd is a fast no-op when $script:UvCmd is already populated
# (the default-invocation case where Main runs everything in one
# process), and throws cleanly if uv truly isn't installed yet.
function Stage-Uv               { if (-not (Install-Uv))     { throw "uv installation failed" } }
function Stage-Python           { Resolve-UvCmd; if (-not (Test-Python))    { throw "Python $PythonVersion not available" } }
function Stage-Git              {
    if (-not (Install-Git)) {
        if ($script:GitInstallFailureReason) { throw $script:GitInstallFailureReason }
        throw "Git not available and auto-install failed -- install from https://git-scm.com/download/win then re-run"
    }
}
# Node is optional (browser tools degrade gracefully without it).  Surface
# failure to the JSON contract as skipped=true / reason rather than ok=true,
# so a GUI driver consuming the manifest can distinguish "node ready" from
# "node missing".  Install flow continues either way -- matches the
# existing Write-Completion behavior that prints a "Note: Node.js could
# not be installed" hint instead of aborting.
function Stage-Node             {
    if (-not (Test-Node)) {
        $script:_StageSkippedReason = "Node.js not available; browser tools will be unavailable until node is installed manually from https://nodejs.org/en/download/"
    }
}
function Stage-SystemPackages   { Install-SystemPackages }
function Stage-Repository       { Install-Repository }
function Stage-Venv             { Resolve-UvCmd; Install-Venv }
function Stage-Dependencies     { Resolve-UvCmd; Install-Dependencies }
function Stage-NodeDeps         { Install-NodeDeps }
function Stage-Desktop          { Install-DesktopVoiceDeps; Install-Desktop }
function Stage-Path             { Set-PathVariable }
function Stage-ConfigTemplates  { Copy-ConfigTemplates }
function Stage-PlatformSdks     { Resolve-UvCmd; Install-PlatformSdks }
function Stage-BootstrapMarker  { Write-BootstrapMarker }
function Stage-Configure        { Invoke-SetupWizard }
function Stage-Gateway          { Start-GatewayIfConfigured }

function Get-InstallStage {
    param([string]$Name)
    foreach ($s in $InstallStages) {
        if ($s.Name -eq $Name) { return $s }
    }
    return $null
}

function Step-OutOfInstallDir {
    # Windows refuses to delete a directory any shell is currently cd'd
    # inside -- and silently leaves orphan files behind, which then wedge
    # "is this a valid git repo" probes on re-install.  Harmless when the
    # caller ran the installer from somewhere else.
    try {
        $currentResolved = (Get-Location).ProviderPath
        $installResolved = $null
        if (Test-Path $InstallDir) {
            $installResolved = (Resolve-Path $InstallDir -ErrorAction SilentlyContinue).ProviderPath
        }
        if ($installResolved -and $currentResolved.ToLower().StartsWith($installResolved.ToLower())) {
            Write-Info "Stepping out of $InstallDir so Windows can replace files there if needed..."
            Set-Location $env:USERPROFILE
        }
    } catch {}
}

function Invoke-Stage {
    param(
        [Parameter(Mandatory=$true)] [hashtable]$StageDef
    )

    # Refresh PATH from registry so this stage sees binaries installed by
    # prior stages, even when each stage runs in its own powershell process.
    # No-op in cost-relevant cases (default invocation path syncs once per
    # foreach pass; cross-process drivers get the necessary freshening).
    Sync-EnvPath

    # Per-stage soft-skip channel.  A worker can populate
    # $script:_StageSkippedReason to surface "ran, but the thing it was
    # supposed to set up is not available" as skipped=true in the JSON
    # frame, without throwing.  Used by Stage-Node so the install flow
    # doesn't abort when an optional capability is missing while still
    # being honest in the protocol contract.  Reset before each stage so
    # a prior stage's reason can never leak into a later stage's frame.
    $script:_StageSkippedReason = $null

    $start = [DateTime]::UtcNow
    $result = @{
        stage        = $StageDef.Name
        ok           = $false
        skipped      = $false
        reason       = $null
        duration_ms  = 0
    }

    try {
        & $StageDef.Worker
        $result.ok = $true
        if ($script:_StageSkippedReason) {
            $result.skipped = $true
            $result.reason  = $script:_StageSkippedReason
        }
    } catch {
        $result.ok = $false
        $result.reason = "$_"
        throw
    } finally {
        $result.duration_ms = [int]([DateTime]::UtcNow - $start).TotalMilliseconds
        if ($Json -or $Stage) {
            # In stage-driver mode every stage emits a JSON line so the
            # caller can stream progress.  In default interactive mode we
            # stay silent here (the worker already wrote human output).
            $result | ConvertTo-Json -Compress | Write-Output
            # Tell the entry-point catch that we've already emitted a
            # frame for this failure (when $result.ok = $false), so it
            # doesn't double-emit a second JSON object and break the
            # one-line-per-stage contract the driver protocol promises.
            if (-not $result.ok) {
                $script:_StageEmittedErrorFrame = $true
            }
        }
    }
}

# ============================================================================
# Main
# ============================================================================

function Invoke-AllStages {
    # Defensive check: powershell.exe ships with every Windows 10/11 system.
    if (-not (Get-Command powershell.exe -ErrorAction SilentlyContinue)) {
        throw "Windows PowerShell (powershell.exe) is required but was not found. PowerShell ships with every Windows 10/11 system -- check your system PATH."
    }
    Step-OutOfInstallDir
    foreach ($s in $InstallStages) {
        Invoke-Stage -StageDef $s
    }
}

function Invoke-EnsureMode {
    param([string]$Deps)
    $depList = $Deps -split ","
    foreach ($dep in $depList) {
        $dep = $dep.Trim()
        switch ($dep) {
            "node" {
                [void](Test-Node)
                if (-not $script:HasNode) {
                    Write-Err "Node.js could not be installed"
                    exit 1
                }
            }
            "browser" {
                [void](Test-Node)
                if ($script:HasNode) {
                    Install-AgentBrowser
                } else {
                    Write-Err "Node.js is required for browser tools but could not be installed"
                    exit 1
                }
            }
            "ripgrep" {
                Install-ManagedRipgrep
            }
            "ffmpeg" {
                Write-Info "ffmpeg: install manually on Windows (scoop install ffmpeg)"
            }
            "coreutils" {
                Install-Coreutils
            }
            "rtk" {
                Install-ManagedRtk
                if (-not (Test-RtkBinary)) {
                    Write-Warn "rtk could not be installed (token-kill will be unavailable)"
                }
            }
            default {
                Write-Err "Unknown dependency: $dep"
                exit 1
            }
        }
    }
}

function Invoke-PostInstallMode {
    Write-Info "Running post-install setup..."
    Invoke-EnsureMode -Deps "node,browser"
    Write-Info "Post-install complete"
}

function Main {
    Write-Banner
    Invoke-AllStages
    if (-not $Json) {
        Write-Completion
    } else {
        @{ ok = $true; protocol_version = $InstallStageProtocolVersion } | ConvertTo-Json -Compress | Write-Output
    }
}

# ----------------------------------------------------------------------------
# Entry-point dispatch
# ----------------------------------------------------------------------------
#
# All branches funnel through one try/catch so errors don't kill an `irm |
# iex` PowerShell session, and so failures in stage-driver mode produce a
# structured JSON error frame instead of a bare exception.

# Dot-sourcing loads the installer's real functions for isolated behavioral
# tests without running an install. Normal script and `irm | iex` entry points
# are unchanged.
if ($MyInvocation.InvocationName -eq ".") {
    return
}

# The normalization prologue runs exactly once per real entry, before any
# switch is honored, so every contract below sees long-form paths.
Initialize-ResolvedPaths

# Keep uv from discovering uv.toml / pyproject.toml config from whatever
# directory or user profile the installer runs under (mirrors install.sh).
$env:UV_NO_CONFIG = "1"
# Children that collapse their own output (windows-build-deps.ps1 under pm,
# when its stdout is still the console) stream too once -Verbose asked for it.
if ($VerbosePreference -ne 'SilentlyContinue') { $env:HERMES_INSTALL_VERBOSE = "1" }

if ($ProtocolVersion) { Write-Output 1; exit 0 }

if ($ShowResolvedPaths) {
    # Side-effect-free contract: by this point every mutation the prologue
    # performs (process-env 8.3 normalization) has already happened, and no
    # stage, download, or write has run. This process's env is private to it,
    # so the parent's environment is untouched. Stdout carries the resolved
    # path report; diagnostics were suppressed by Write-PathDiag.
    $script:ResolvedPathReport | ConvertTo-Json -Depth 5 -Compress | Write-Output
    exit 0
}

if ($Manifest) {
    @{ protocol_version = 1; stages = $Stages } | ConvertTo-Json -Depth 4 -Compress | Write-Output
    exit 0
}

if ($Stage) {
    # The $Stages table is the single authoritative list: it drives the
    # -Manifest output AND the no-flag ladder, so -IncludeDesktop affects
    # the real run exactly as the manifest advertises. "desktop" stays
    # directly dispatchable via -Stage even though it is never listed
    # (long-standing external-caller contract).
    $known = @($Stages | ForEach-Object { $_.name })
    if ($known -notcontains $Stage -and $Stage -ne "desktop") {
        if ($Json) { Emit-Frame $false $Stage $false "unknown stage: $Stage" }
        else { [Console]::Error.WriteLine("unknown stage: $Stage") }
        exit 2
    }
    $stageDef = $Stages | Where-Object { $_.name -eq $Stage } | Select-Object -First 1
    $needsInput = $stageDef -and $stageDef.needs_user_input
    if ($NonInteractive -and $needsInput) {
        if ($Json) { Emit-Frame $true $Stage $true "needs user input" }
        exit 0
    }
    try {
        Invoke-StageByName $Stage
        if ($Json) { Emit-Frame $true $Stage $false }
        exit 0
    } catch {
        Write-Err "$_"
        if ($Json) { Emit-Frame $false $Stage $false "$_" }
        exit 1
    }
}

# No -Stage: run the whole ladder — the same authoritative list the
# manifest prints, so -IncludeDesktop inserts desktop here too.
$script:InstallStarted = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
$script:InstallStage = "prerequisites"
$script:InstallReceiptWritten = $false
try {
    Write-Banner
    foreach ($s in $Stages) {
        $script:InstallStage = $s.name
        $script:FailureClass = ""
        Invoke-StageByName $s.name
    }
    Write-InstallReceipt "success" "none" "none"
    Write-PathReloadHint
} catch {
    Write-InstallReceipt "failed" $script:InstallStage (Get-StageFailureClass)
    Write-Err "$_"
    if ($script:RunAsFile) { exit 1 }
    # Under iex: report failure without closing the user's window.
    $global:LASTEXITCODE = 1
} finally {
    # Ctrl-C stops the run without reaching catch; finally still runs (no-op after a receipt).
    Write-InstallReceipt "failed" $script:InstallStage "interrupted"
}
