"""Guarded Windows Git Bash compatibility data: fallback command bodies,
runtime delegates, unsupported-command refusal reasons, and the helper
constructors that turn a body into a runtime-guarded shell definition.

Moved byte-for-byte from ``tools/environments/bash_fix.py`` (the
fallback-data section, former lines 26-762, plus the
``_FALLBACK_COMMAND_WRAPPERS`` table, former lines 801-805, relocated
because it maps fallback names to wrapper kinds and belongs with the
fallback tables).  ``tools/environments/bash_fix.py`` is the stable
compatibility FACADE over this package — the implementation lives here, in
``shell_compat.fallback_data`` — and re-exports this surface.
"""

from __future__ import annotations

# ======================================================================
# bash_fix.py (kimi-agent) - Windows Git Bash compatibility scanner
# ======================================================================

from agent.re_compat import re

_REV_PERL = (
    "perl '-Mopen=:std,:encoding(UTF-8)' -e '"
    "my $zero = shift @ARGV; my $failed = 0; "
    "sub reverse_fh { my ($fh, $zero) = @_; "
    "local $/ = $zero ? qq(\\0) : qq(\\n); "
    "while (my $record = <$fh>) { "
    "my $ended = $zero ? $record =~ s/\\0\\z// : $record =~ s/\\r?\\n\\z//; "
    "print scalar reverse($record); "
    "print($zero ? qq(\\0) : qq(\\n)) if $ended } } "
    "if (@ARGV) { for my $file (@ARGV) { "
    "if (open my $fh, q(<:encoding(UTF-8)), $file) { reverse_fh($fh, $zero); close $fh } "
    "else { warn qq(rev: $file: $!\\n); $failed = 1 } } } "
    "else { reverse_fh(*STDIN, $zero) } exit $failed'"
)

_NATIVE_DELEGATE = (
    "local __hermes_native=''; __hermes_native=$(type -P {name}) || :; "
    "if [[ -n $__hermes_native ]]; then \"$__hermes_native\" \"$@\"; return; fi; "
)

# Fallbacks whose ``command -v`` hit can be a non-functional placeholder: the
# Microsoft Store App Execution Alias stubs in ``WindowsApps`` print an
# install prompt instead of running the tool.  They get a stub-aware guard
# (define the fallback even when ``command -v`` succeeds) and a delegate
# that refuses stub paths.
_STUB_AWARE_FALLBACKS = frozenset({"pip3", "python3"})

_PGREP_PS_NAME = (
    "$m = Get-Process | Where-Object { $_.Name -match $env:__HERMES_PAT }; "
    "if ($m) { $m | ForEach-Object { if ($env:__HERMES_LIST -eq \"1\") { "
    "\"$($_.Id) $($_.Name)\" } else { $_.Id } }; exit 0 } else { exit 1 }"
)
_PGREP_PS_FULL = (
    "$m = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match $env:__HERMES_PAT }; "
    "if ($m) { $m | ForEach-Object { if ($env:__HERMES_LIST -eq \"1\") { "
    "\"$($_.ProcessId) $($_.Name)\" } else { $_.ProcessId } }; exit 0 } else { exit 1 }"
)
_PKILL_PS_NAME = (
    "$m = Get-Process | Where-Object { $_.Name -match $env:__HERMES_PAT }; "
    "if ($m) { $m | Stop-Process -Force; exit 0 } else { exit 1 }"
)
_PKILL_PS_FULL = (
    "$m = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match $env:__HERMES_PAT }; "
    "if ($m) { $m | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; "
    "exit 0 } else { exit 1 }"
)

_ZIP_PS = (
    "Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem; "
    "$level = [System.IO.Compression.CompressionLevel]$env:__HERMES_ZIP_LEVEL; "
    "$dest = $env:__HERMES_ZIP_DEST; "
    "if (Test-Path -LiteralPath $dest) { Remove-Item -LiteralPath $dest -Force }; "
    "$zip = [System.IO.Compression.ZipFile]::Open($dest, [System.IO.Compression.ZipArchiveMode]::Create); "
    "foreach ($p in ($env:__HERMES_ZIP_PATHS -split \"`n\")) { "
    "$item = Get-Item -LiteralPath $p; $base = $item.Name; "
    "if ($item.PSIsContainer) { $root = $item.FullName; "
    "Get-ChildItem -LiteralPath $root -Recurse -Force | ForEach-Object { "
    "$rel = $_.FullName.Substring($root.Length).TrimStart(\"\\\") -replace \"\\\\\", \"/\"; "
    "if ($_.PSIsContainer) { $zip.CreateEntry($base + \"/\" + $rel + \"/\") | Out-Null } "
    "else { [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $_.FullName, $base + \"/\" + $rel, $level) | Out-Null } } } "
    "else { [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $item.FullName, $base, $level) | Out-Null } }; "
    "$zip.Dispose(); if (Test-Path -LiteralPath $dest) { exit 0 } else { exit 1 }"
)

_TREE_PERL = (
    "perl -e '"
    "my ($maxdepth,$showall,$dirsonly,$noreport,$top)=@ARGV; "
    "print qq($top\\n); "
    "my ($ndirs,$nfiles)=(0,0); "
    "sub walk { my ($path,$prefix,$depth)=@_; "
    "return if $maxdepth && $depth>$maxdepth; "
    "opendir(my $dh,$path) or return; "
    "my @e = grep { ! /^[.][.]?$/ } readdir($dh); closedir($dh); "
    "@e = grep { $showall || ! /^[.]/ } @e; "
    "@e = grep { ! $dirsonly || -d qq($path/$_) } @e; "
    "@e = sort { lc($a) cmp lc($b) } @e; "
    "my $n=@e; my $i=0; "
    "for my $e (@e) { $i++; my $last = $i==$n; my $full = qq($path/$e); "
    "my $isdir = -d $full; "
    "if ($isdir) { $ndirs++ } else { $nfiles++ } "
    "print $prefix, ($last ? qq(`-- ) : qq(|-- )), $e, qq(\\n); "
    "walk($full, $prefix . ($last ? qq(    ) : qq(|   )), $depth+1) "
    "if $isdir && ! -l $full } } "
    "walk($top,q(),1); "
    "my $dw = $ndirs==1 ? q(directory) : q(directories); "
    "my $fw = $nfiles==1 ? q(file) : q(files); "
    "print qq(\\n$ndirs $dw, $nfiles $fw\\n) unless $noreport'"
)

_POWERSHELL_PASTE = (
    "powershell.exe -NoProfile -NonInteractive -Command "
    "'[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
    "[Console]::Out.Write((Get-Clipboard -Raw))'"
)

# Windows cmd-style command fallbacks.  These names are not part of the Git
# Bash POSIX userland and are commonly emitted by agents accustomed to cmd.exe
# or cross-platform documentation.  Each fallback is only installed when Git
# Bash cannot already resolve the command via ``command -v``.

_TASKLIST_PS = (
    "Get-Process | Select-Object Name, Id, CPU, WorkingSet | Format-Table -AutoSize"
)

_TASKKILL_PS = (
    # ``[System.Management.Automation.ActionPreference]`` (not the string ``'Stop'``)
    # because the whole script lives inside a single-quoted bash ``-Command`` word
    # that cannot carry quotes.  ``Stop`` turns cmdlet failures (unknown PID,
    # access denied) into catchable exceptions so a failed kill exits non-zero
    # like real ``taskkill.exe`` instead of unconditionally reporting success.
    "$ErrorActionPreference = [System.Management.Automation.ActionPreference]::Stop; "
    "$force = $env:__HERMES_FORCE -eq '1'; "
    "$ok = $true; "
    # Process *both* operands when combined (``/IM x /PID 1``): a failing PID
    # kill must not be masked by ``exit 0``, and ``/IM`` must not be silently
    # dropped when ``/PID`` is also present.
    "if ($env:__HERMES_PID) { "
    "try { Stop-Process -Id $env:__HERMES_PID -Force:$force } catch { $ok = $false } "
    "} "
    # taskkill's /IM operand is the image name *with* its extension (``notepad.exe``),
    # but Get-Process ``.Name`` excludes it (``notepad``); normalise so both spellings
    # and mixed case match.  ``[IO.Path]`` avoids quotes, which the surrounding
    # single-quoted bash ``-Command`` string cannot carry.
    "$im = [IO.Path]::GetFileNameWithoutExtension($env:__HERMES_IM); "
    "$procs = Get-Process | Where-Object { $_.Name -eq $im }; "
    "if ($procs) { try { $procs | Stop-Process -Force:$force } catch { $ok = $false } } "
    "elseif ($env:__HERMES_IM) { $ok = $false }; "
    "if ($ok) { exit 0 } else { exit 1 }"
)

_SYSTEMINFO_PS = "Get-ComputerInfo | Format-List"

_KILLALL_PS = (
    "$procs = Get-Process | Where-Object { $_.Name -eq $env:__HERMES_NAME }; "
    "if ($procs) { $procs | Stop-Process -Force; exit 0 } else { exit 1 }"
)

_PIDOF_PS = (
    "$ids = (Get-Process | Where-Object { $_.Name -eq $env:__HERMES_NAME }).Id; "
    "if ($ids) { $ids -join \" \"; exit 0 } else { exit 1 }"
)

_COLUMN_PERL = (
    "perl -e '"
    "my $sep = shift @ARGV; $sep = qr/\\s+/ if $sep eq \"DEFAULT\"; "
    "my @rows; my @max; "
    "while (<>) { chomp; my @c = split $sep; push @rows, \\@c; "
    "for my $i (0..$#c) { $max[$i] = length($c[$i]) if !defined $max[$i] || length($c[$i]) > $max[$i]; } } "
    "for my $r (@rows) { print join(\"  \", map { sprintf(\"%-*s\", $max[$_]//0, $r->[$_]) } 0..$#$r), \"\\n\"; }'"
)

_FALLBACK_BODIES = {
    "gtimeout": "timeout \"$@\"",
    "rev": (
        "local __hermes_zero=0; while (( $# )); do case $1 in "
        "-0|--zero) __hermes_zero=1; shift;; "
        "--) shift; break;; "
        "-*) printf '%s\\n' \"rev: unsupported option: $1\" >&2; return 1;; "
        "*) break;; esac; done; "
        + _REV_PERL
        + " -- \"$__hermes_zero\" \"$@\""
    ),
    "xdg-open": "start \"$@\"",
    "open": "start \"$@\"",
    "pbcopy": "clip.exe \"$@\"",
    "pbpaste": _POWERSHELL_PASTE + " \"$@\"",
    "wget": (
        "local __hermes_url='' __hermes_out='' __hermes_stdout=0; "
        "local -a __hermes_args=(); "
        "while (( $# )); do case $1 in "
        "-O|--output-document) [[ $# -ge 2 ]] || { printf '%s\\n' \"wget: option requires an argument -- '${1//[![:alnum:]]/}'\" >&2; return 1; }; __hermes_out=$2; shift 2;; "
        "-O?*) __hermes_out=${1#-O}; shift;; "
        "--output-document=*) __hermes_out=${1#*=}; shift;; "
        "-q|--quiet) __hermes_args+=(-s); shift;; "
        "-c|--continue) __hermes_args+=(-C -); shift;; "
        "--no-check-certificate) __hermes_args+=(-k); shift;; "
        "-T|--timeout) [[ $# -ge 2 ]] || { printf '%s\\n' \"wget: option requires an argument -- '${1//[![:alnum:]]/}'\" >&2; return 1; }; __hermes_args+=(--max-time \"$2\"); shift 2;; "
        "--timeout=*) __hermes_args+=(--max-time \"${1#*=}\"); shift;; "
        "-*) printf '%s\\n' \"wget: unsupported option for curl fallback: $1\" >&2; return 1;; "
        "*) __hermes_url=$1; shift;; esac; done; "
        "if [[ -z $__hermes_url ]]; then "
        "printf '%s\\n' 'wget: missing URL' >&2; return 1; fi; "
        "if [[ $__hermes_out == '-' ]]; then __hermes_stdout=1; fi; "
        "if [[ -z $__hermes_out && $__hermes_stdout -eq 0 ]]; then "
        "__hermes_out=${__hermes_url##*/}; "
        "[[ -n $__hermes_out ]] || __hermes_out=index.html; fi; "
        "if (( __hermes_stdout )); then "
        "curl -fSL \"${__hermes_args[@]}\" -- \"$__hermes_url\"; "
        "else curl -fSL \"${__hermes_args[@]}\" -o \"$__hermes_out\" -- \"$__hermes_url\"; fi"
    ),
    "xclip": (
        "local __hermes_out=0; while (( $# )); do case $1 in "
        "-o|-out) __hermes_out=1; shift;; "
        "-i|-in) shift;; "
        "-selection|-d|-display) [[ $# -ge 2 ]] || { printf '%s\\n' \"xclip: option requires an argument -- '${1//[![:alnum:]]/}'\" >&2; return 1; }; shift 2;; "
        "-selection*|-display*) shift;; "
        "-*) printf '%s\\n' \"xclip: unsupported option for clipboard fallback: $1\" >&2; return 1;; "
        "*) shift;; esac; done; "
        "if (( __hermes_out )); then " + _POWERSHELL_PASTE + "; else clip.exe; fi"
    ),
    "xsel": (
        "local __hermes_out=0; while (( $# )); do case $1 in "
        "--output) __hermes_out=1; shift;; "
        "--input|--clipboard|--primary|--secondary) shift;; "
        "--*) printf '%s\\n' \"xsel: unsupported option for clipboard fallback: $1\" >&2; return 1;; "
        "-*) case $1 in *o*) __hermes_out=1;; esac; shift;; "
        "*) shift;; esac; done; "
        "if (( __hermes_out )); then " + _POWERSHELL_PASTE + "; else clip.exe; fi"
    ),
    "wl-copy": (
        "while (( $# )); do case $1 in "
        "-*) printf '%s\\n' \"wl-copy: unsupported option for clipboard fallback: $1\" >&2; return 1;; "
        "*) shift;; esac; done; clip.exe"
    ),
    "wl-paste": (
        "while (( $# )); do case $1 in "
        "-n|--no-newline) shift;; "
        "-*) printf '%s\\n' \"wl-paste: unsupported option for clipboard fallback: $1\" >&2; return 1;; "
        "*) shift;; esac; done; " + _POWERSHELL_PASTE
    ),
    "zip": (
        "local __hermes_archive='' __hermes_level=Optimal __hermes_p='' "
        "__hermes_combo='' __hermes_i=0; "
        "local -a __hermes_paths=() __hermes_wpaths=() __hermes_split=(); "
        "while (( $# )); do "
        "if [[ $1 == -[!-]* && ${#1} -gt 2 ]]; then "
        "__hermes_combo=${1#-}; __hermes_split=(); shift; "
        "for (( __hermes_i=0; __hermes_i<${#__hermes_combo}; __hermes_i++ )); do "
        "__hermes_split+=(-${__hermes_combo:__hermes_i:1}); done; "
        "set -- \"${__hermes_split[@]}\" \"$@\"; continue; fi; "
        "case $1 in "
        "-r|-R|--recurse-paths|-q|--quiet) shift;; "
        "-0) __hermes_level=NoCompression; shift;; "
        "-1) __hermes_level=Fastest; shift;; "
        "-[2-9]) shift;; "
        "-*) printf '%s\\n' \"zip: unsupported option for Compress-Archive fallback: $1\" >&2; return 1;; "
        "*) if [[ -z $__hermes_archive ]]; then __hermes_archive=$1; "
        "else __hermes_paths+=(\"$1\"); fi; shift;; esac; done; "
        "if [[ -z $__hermes_archive || ${#__hermes_paths[@]} -eq 0 ]]; then "
        "printf '%s\\n' 'zip: missing archive name or input paths' >&2; return 1; fi; "
        "for __hermes_p in \"${__hermes_paths[@]}\"; do "
        "__hermes_wpaths+=(\"$(cygpath -w -- \"$__hermes_p\")\"); done; "
        "__hermes_archive=$(cygpath -w -- \"$__hermes_archive\"); "
        "__HERMES_ZIP_LEVEL=$__hermes_level __HERMES_ZIP_DEST=$__hermes_archive "
        "__HERMES_ZIP_PATHS=$(printf '%s\\n' \"${__hermes_wpaths[@]}\") "
        "powershell.exe -NoProfile -NonInteractive -Command '" + _ZIP_PS + "'"
    ),
    "nc": (
        "local __hermes_z=0 __hermes_v=0 __hermes_w='' __hermes_host='' __hermes_port=''; "
        "while (( $# )); do case $1 in "
        "-z) __hermes_z=1; shift;; "
        "-v) __hermes_v=1; shift;; "
        "-zv|-vz) __hermes_z=1; __hermes_v=1; shift;; "
        "-w) [[ $# -ge 2 ]] || { printf '%s\\n' \"nc: option requires an argument -- 'w'\" >&2; return 1; }; __hermes_w=$2; shift 2;; "
        "-w?*) __hermes_w=${1#-w}; shift;; "
        "-*) printf '%s\\n' \"nc: unsupported option for /dev/tcp fallback: $1\" >&2; return 1;; "
        "*) if [[ -z $__hermes_host ]]; then __hermes_host=$1; "
        "elif [[ -z $__hermes_port ]]; then __hermes_port=$1; "
        "else printf '%s\\n' 'nc: too many arguments' >&2; return 1; fi; "
        "shift;; esac; done; "
        "if (( ! __hermes_z )); then "
        "printf '%s\\n' 'nc: only -z (zero-I/O scan) mode is supported by this fallback' >&2; "
        "return 1; fi; "
        "if [[ -z $__hermes_host || -z $__hermes_port ]]; then "
        "printf '%s\\n' 'nc: missing host or port' >&2; return 1; fi; "
        "if [[ -n $__hermes_w ]]; then "
        "timeout \"$__hermes_w\" bash -c 'exec 3<>/dev/tcp/$1/$2' _ "
        "\"$__hermes_host\" \"$__hermes_port\" 2>/dev/null; "
        "else (exec 3<>/dev/tcp/\"$__hermes_host\"/\"$__hermes_port\") 2>/dev/null; fi; "
        "local __hermes_rc=$?; "
        "(( __hermes_rc != 0 )) && __hermes_rc=1; "
        "if (( __hermes_rc == 0 )); then "
        "(( __hermes_v )) && printf '%s\\n' \"Connection to $__hermes_host $__hermes_port port [tcp/*] succeeded!\" >&2; "
        "else "
        "(( __hermes_v )) && printf '%s\\n' \"nc: connect to $__hermes_host port $__hermes_port (tcp) failed\" >&2; fi; "
        "return $__hermes_rc"
    ),
    "pgrep": (
        "local __hermes_list=0 __hermes_full=0 __hermes_pat=''; "
        "while (( $# )); do case $1 in "
        "-l) __hermes_list=1; shift;; "
        "-f) __hermes_full=1; shift;; "
        "-lf|-fl) __hermes_list=1; __hermes_full=1; shift;; "
        "--) shift; break;; "
        "-*) printf '%s\\n' \"pgrep: unsupported option for Get-Process fallback: $1\" >&2; return 1;; "
        "*) __hermes_pat=$1; shift;; esac; done; "
        "if [[ -z $__hermes_pat && $# -gt 0 ]]; then __hermes_pat=$1; fi; "
        "if [[ -z $__hermes_pat ]]; then "
        "printf '%s\\n' 'pgrep: missing pattern' >&2; return 1; fi; "
        "if (( __hermes_full )); then "
        "__HERMES_PAT=$__hermes_pat __HERMES_LIST=$__hermes_list "
        "powershell.exe -NoProfile -NonInteractive -Command '" + _PGREP_PS_FULL + "'; "
        "else "
        "__HERMES_PAT=$__hermes_pat __HERMES_LIST=$__hermes_list "
        "powershell.exe -NoProfile -NonInteractive -Command '" + _PGREP_PS_NAME + "'; fi"
    ),
    "pkill": (
        "local __hermes_full=0 __hermes_pat=''; "
        "while (( $# )); do case $1 in "
        "-f) __hermes_full=1; shift;; "
        "--) shift; break;; "
        "-*) printf '%s\\n' \"pkill: unsupported option for Stop-Process fallback: $1\" >&2; return 1;; "
        "*) __hermes_pat=$1; shift;; esac; done; "
        "if [[ -z $__hermes_pat && $# -gt 0 ]]; then __hermes_pat=$1; fi; "
        "if [[ -z $__hermes_pat ]]; then "
        "printf '%s\\n' 'pkill: missing pattern' >&2; return 1; fi; "
        "if (( __hermes_full )); then "
        "__HERMES_PAT=$__hermes_pat "
        "powershell.exe -NoProfile -NonInteractive -Command '" + _PKILL_PS_FULL + "'; "
        "else "
        "__HERMES_PAT=$__hermes_pat "
        "powershell.exe -NoProfile -NonInteractive -Command '" + _PKILL_PS_NAME + "'; fi"
    ),
    "traceroute": (
        "local -a __hermes_args=(); "
        "while (( $# )); do case $1 in "
        "-n) __hermes_args+=(-d); shift;; "
        "-m) [[ $# -ge 2 ]] || { printf '%s\\n' \"traceroute: option requires an argument -- 'm'\" >&2; return 1; }; __hermes_args+=(-h \"$2\"); shift 2;; "
        "-m?*) __hermes_args+=(-h \"${1#-m}\"); shift;; "
        "--max-hop=*) __hermes_args+=(-h \"${1#*=}\"); shift;; "
        "-w) [[ $# -ge 2 ]] || { printf '%s\\n' \"traceroute: option requires an argument -- 'w'\" >&2; return 1; }; __hermes_args+=(-w \"$(( $2 * 1000 ))\"); shift 2;; "
        "-w?*) __hermes_args+=(-w \"$(( ${1#-w} * 1000 ))\"); shift;; "
        "-*) printf '%s\\n' \"traceroute: unsupported option for tracert fallback: $1\" >&2; return 1;; "
        "*) __hermes_args+=(\"$1\"); shift;; esac; done; "
        "tracert \"${__hermes_args[@]}\""
    ),
    "tree": (
        "local __hermes_depth=0 __hermes_all=0 __hermes_dirsonly=0 __hermes_noreport=0; "
        "local -a __hermes_dirs=(); "
        "while (( $# )); do case $1 in "
        "-L) [[ $# -ge 2 ]] || { printf '%s\\n' \"tree: option requires an argument -- 'L'\" >&2; return 1; }; __hermes_depth=$2; shift 2;; "
        "-L?*) __hermes_depth=${1#-L}; shift;; "
        "-a) __hermes_all=1; shift;; "
        "-d) __hermes_dirsonly=1; shift;; "
        "--noreport) __hermes_noreport=1; shift;; "
        "--) shift; __hermes_dirs+=(\"$@\"); break;; "
        "-*) printf '%s\\n' \"tree: unsupported option for perl fallback: $1\" >&2; return 1;; "
        "*) __hermes_dirs+=(\"$1\"); shift;; esac; done; "
        "[[ ${#__hermes_dirs[@]} -gt 0 ]] || __hermes_dirs=(.); "
        "local __hermes_d; "
        "for __hermes_d in \"${__hermes_dirs[@]}\"; do "
        + _TREE_PERL
        + " -- \"$__hermes_depth\" \"$__hermes_all\" \"$__hermes_dirsonly\" \"$__hermes_noreport\" \"$__hermes_d\"; done"
    ),
    "say": (
        "while (( $# )); do case $1 in "
        "-*) printf '%s\\n' \"say: unsupported option for SAPI fallback: $1\" >&2; return 1;; "
        "*) shift;; esac; done; "
        "__HERMES_SAY_TEXT=$* "
        "powershell.exe -NoProfile -NonInteractive -Command "
        "'Add-Type -AssemblyName System.Speech; "
        "(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak($env:__HERMES_SAY_TEXT)'"
    ),
    "python3": 'python "$@"',
    "pip3": 'pip "$@"',
    # Windows cmd-style commands -> POSIX/Git Bash equivalents.
    "copy": (
        "if [[ $# -lt 2 ]]; then "
        "printf '%s\\n' 'copy: missing source or destination' >&2; return 1; fi; "
        "cp -R -- \"$@\""
    ),
    "move": (
        "if [[ $# -lt 2 ]]; then "
        "printf '%s\\n' 'move: missing source or destination' >&2; return 1; fi; "
        "mv -- \"$@\""
    ),
    "del": "rm -- \"$@\"",
    "erase": "rm -- \"$@\"",
    "ren": (
        "if [[ $# -ne 2 ]]; then "
        "printf '%s\\n' 'ren: exactly two arguments required' >&2; return 1; fi; "
        "mv -- \"$1\" \"$2\""
    ),
    "rename": (
        "if [[ $# -ne 2 ]]; then "
        "printf '%s\\n' 'rename: exactly two arguments required' >&2; return 1; fi; "
        "mv -- \"$1\" \"$2\""
    ),
    "rd": "rmdir -- \"$@\"",
    "md": "mkdir -p -- \"$@\"",
    "chdir": "cd -- \"$@\"",
    "cls": "clear",
    "xcopy": "cp -r -- \"$@\"",
    "mklink": (
        "local __hermes_hard=0 __hermes_link='' __hermes_target=''; "
        "while (( $# )); do case $1 in "
        "/D|/d|/J|/j) shift;; "
        "/H|/h) __hermes_hard=1; shift;; "
        "*) if [[ -z $__hermes_link ]]; then __hermes_link=$1; "
        "elif [[ -z $__hermes_target ]]; then __hermes_target=$1; "
        "else printf '%s\\n' 'mklink: too many arguments' >&2; return 1; fi; "
        "shift;; esac; done; "
        "if [[ -z $__hermes_link || -z $__hermes_target ]]; then "
        "printf '%s\\n' 'mklink: missing link name or target' >&2; return 1; fi; "
        "if (( __hermes_hard )); then ln -f -- \"$__hermes_target\" \"$__hermes_link\"; "
        "else ln -s -- \"$__hermes_target\" \"$__hermes_link\"; fi"
    ),
    "findstr": "grep \"$@\"",
    "fc": "diff \"$@\"",
    "where": "which \"$@\"",
    "tasklist": (
        "powershell.exe -NoProfile -NonInteractive -Command '" + _TASKLIST_PS + "'"
    ),
    "taskkill": (
        "local __hermes_force=0 __hermes_pid='' __hermes_im=''; "
        "while (( $# )); do case $1 in "
        "/F|/f) __hermes_force=1; shift;; "
        "/IM|/im) [[ $# -ge 2 ]] || { printf '%s\\n' \"taskkill: option requires an argument -- '${1//[![:alnum:]]/}'\" >&2; return 1; }; __hermes_im=$2; shift 2;; "
        "/PID|/pid) [[ $# -ge 2 ]] || { printf '%s\\n' \"taskkill: option requires an argument -- '${1//[![:alnum:]]/}'\" >&2; return 1; }; __hermes_pid=$2; shift 2;; "
        "/*) printf '%s\\n' \"taskkill: unsupported option: $1\" >&2; return 1;; "
        "*) printf '%s\\n' \"taskkill: unsupported argument: $1\" >&2; return 1;; esac; done; "
        "if [[ -n $__hermes_pid ]]; then "
        "__HERMES_FORCE=$__hermes_force __HERMES_PID=$__hermes_pid "
        "powershell.exe -NoProfile -NonInteractive -Command '" + _TASKKILL_PS + "'; "
        "elif [[ -n $__hermes_im ]]; then "
        "__HERMES_FORCE=$__hermes_force __HERMES_IM=$__hermes_im "
        "powershell.exe -NoProfile -NonInteractive -Command '" + _TASKKILL_PS + "'; "
        "else printf '%s\\n' 'taskkill: missing /PID or /IM' >&2; return 1; fi"
    ),
    "systeminfo": (
        "powershell.exe -NoProfile -NonInteractive -Command '" + _SYSTEMINFO_PS + "'"
    ),
    # POSIX utilities often absent from a bare Git Bash userland.
    "watch": (
        "local __hermes_interval=2; "
        "while (( $# )); do case $1 in "
        "-n|--interval) [[ $# -ge 2 ]] || { printf '%s\\n' \"watch: option requires an argument -- '${1//[![:alnum:]]/}'\" >&2; return 1; }; __hermes_interval=$2; shift 2;; "
        "--interval=*) __hermes_interval=${1#*=}; shift;; "
        "-n?*) __hermes_interval=${1#-n}; shift;; "
        "-t|-d|--no-title|--color) shift;; "
        "--) shift; break;; "
        "-*) printf '%s\\n' \"watch: unsupported option: $1\" >&2; return 1;; "
        "*) break;; esac; done; "
        "if [[ $# -eq 0 ]]; then printf '%s\\n' 'watch: missing command' >&2; return 1; fi; "
        "while true; do clear; eval \"$*\"; sleep \"$__hermes_interval\"; done"
    ),
    "killall": (
        "if [[ $# -eq 0 ]]; then printf '%s\\n' 'killall: missing process name' >&2; return 1; fi; "
        "__HERMES_NAME=$1 powershell.exe -NoProfile -NonInteractive -Command '" + _KILLALL_PS + "'"
    ),
    "pidof": (
        "if [[ $# -eq 0 ]]; then printf '%s\\n' 'pidof: missing process name' >&2; return 1; fi; "
        "__HERMES_NAME=$1 powershell.exe -NoProfile -NonInteractive -Command '" + _PIDOF_PS + "'"
    ),
    "column": (
        "local __hermes_sep='DEFAULT'; "
        "while (( $# )); do case $1 in "
        "-t) shift;; "
        "-s) [[ $# -ge 2 ]] || { printf '%s\\n' \"column: option requires an argument -- 's'\" >&2; return 1; }; __hermes_sep=$2; shift 2;; "
        "-s?*) __hermes_sep=${1#-s}; shift;; "
        "-*) printf '%s\n' \"column: unsupported option for perl fallback: $1\" >&2; return 1;; "
        "*) break;; esac; done; "
        + _COLUMN_PERL
        + " \"$__hermes_sep\" \"$@\""
    ),
    # POSIX utilities from the common command cheat-sheet that are absent
    # from a bare Git Bash userland and map onto native Windows tools.
    "free": (
        "local __hermes_unit=K; "
        "while (( $# )); do case $1 in "
        "-b|--bytes) __hermes_unit=B; shift;; "
        "-k|--kibi|--kilo) __hermes_unit=K; shift;; "
        "-m|--mebi|--mega) __hermes_unit=M; shift;; "
        "-g|--gibi|--giga) __hermes_unit=G; shift;; "
        "-h|--human) __hermes_unit=H; shift;; "
        "--help) printf '%s\n' 'free: report memory usage (Windows: Win32_OperatingSystem; no swap row)'; return 0;; "
        "-*) printf '%s\n' \"free: unsupported option: $1\" >&2; return 1;; "
        "*) shift;; esac; done; "
        "local -a __hermes_m=(); "
        "mapfile -t __hermes_m < <(powershell.exe -NoProfile -NonInteractive -Command "
        "'$o=Get-CimInstance Win32_OperatingSystem; "
        "Write-Output $o.TotalVisibleMemorySize; Write-Output $o.FreePhysicalMemory' "
        "2>/dev/null | tr -d '\\r'); "
        "if [[ ${#__hermes_m[@]} -lt 2 ]]; then "
        "printf '%s\n' 'free: failed to query memory information' >&2; return 1; fi; "
        "local __hermes_total=${__hermes_m[0]} __hermes_free=${__hermes_m[1]}; "
        "local __hermes_used=$(( __hermes_total - __hermes_free )); "
        "printf '%s\n' '               total        used        free'; "
        "case $__hermes_unit in "
        "B) printf 'Mem: %12d %11d %11d\n' $(( __hermes_total * 1024 )) $(( __hermes_used * 1024 )) $(( __hermes_free * 1024 ));; "
        "M) printf 'Mem: %12d %11d %11d\n' $(( __hermes_total / 1024 )) $(( __hermes_used / 1024 )) $(( __hermes_free / 1024 ));; "
        "G) printf 'Mem: %12d %11d %11d\n' $(( __hermes_total / 1024 / 1024 )) $(( __hermes_used / 1024 / 1024 )) $(( __hermes_free / 1024 / 1024 ));; "
        "H) awk -v t=\"$__hermes_total\" -v f=\"$__hermes_free\" '"
        "function h(x,  i){i=1; while(x>=10240&&i<4){x=x/1024;i++} "
        "return sprintf(\"%.1f%s\", x, substr(\"KMGT\", i, 1))} "
        "BEGIN{printf \"Mem: %11s %10s %10s\\n\", h(t), h(t-f), h(f)}';; "
        "*) printf 'Mem: %12d %11d %11d\n' \"$__hermes_total\" \"$__hermes_used\" \"$__hermes_free\";; esac"
    ),
    "uptime": (
        "local __hermes_since=0; "
        "while (( $# )); do case $1 in "
        "-s|--since) __hermes_since=1; shift;; "
        "--help) printf '%s\n' 'uptime: tell how long the system has been running (Windows approximation; no user count)'; return 0;; "
        "-*) printf '%s\n' \"uptime: unsupported option: $1\" >&2; return 1;; "
        "*) printf '%s\n' \"uptime: unsupported argument: $1\" >&2; return 1;; esac; done; "
        "local -a __hermes_u=(); "
        "mapfile -t __hermes_u < <(powershell.exe -NoProfile -NonInteractive -Command "
        "'$b=(Get-CimInstance Win32_OperatingSystem).LastBootUpTime; "
        "Write-Output $b.ToString(\"yyyy-MM-dd HH:mm:ss\"); "
        "$up=New-TimeSpan -Start $b -End (Get-Date); "
        "Write-Output $up.Days; Write-Output $up.Hours; Write-Output $up.Minutes' "
        "2>/dev/null | tr -d '\\r'); "
        "if [[ ${#__hermes_u[@]} -lt 4 ]]; then "
        "printf '%s\n' 'uptime: failed to query boot time' >&2; return 1; fi; "
        "if (( __hermes_since )); then printf '%s\n' \"${__hermes_u[0]}\"; return 0; fi; "
        "local __hermes_days=${__hermes_u[1]} __hermes_hours=${__hermes_u[2]} __hermes_mins=${__hermes_u[3]}; "
        "printf -v __hermes_mins '%02d' \"$__hermes_mins\"; "
        "local __hermes_up; "
        "if (( __hermes_days > 0 )); then "
        "printf -v __hermes_up '%d day(s), %d:%s' \"$__hermes_days\" \"$__hermes_hours\" \"$__hermes_mins\"; "
        "else printf -v __hermes_up '%d:%s' \"$__hermes_hours\" \"$__hermes_mins\"; fi; "
        "printf '%s up %s, load average: n/a (not reported on Windows)\n' \"$(date '+%H:%M:%S')\" \"$__hermes_up\""
    ),
    "top": (
        "local __hermes_delay=3 __hermes_iters=0 __hermes_batch=0 __hermes_count=0; "
        "while (( $# )); do case $1 in "
        "-b) __hermes_batch=1; shift;; "
        "-d) [[ $# -ge 2 ]] || { printf '%s\\n' \"top: option requires an argument -- 'd'\" >&2; return 1; }; __hermes_delay=$2; shift 2;; "
        "-d?*) __hermes_delay=${1#-d}; shift;; "
        "-n) [[ $# -ge 2 ]] || { printf '%s\\n' \"top: option requires an argument -- 'n'\" >&2; return 1; }; __hermes_iters=$2; shift 2;; "
        "-n?*) __hermes_iters=${1#-n}; shift;; "
        "-h|--help) printf '%s\n' 'top: display processes (Windows approximation via Get-Process; Ctrl+C quits)'; return 0;; "
        "-*) printf '%s\n' \"${FUNCNAME[0]}: unsupported option: $1\" >&2; return 1;; "
        "*) shift;; esac; done; "
        "__hermes_snapshot() { powershell.exe -NoProfile -NonInteractive -Command "
        "'Get-Process | Sort-Object -Property CPU -Descending | "
        "Select-Object -First 25 Id,ProcessName,CPU,WorkingSet | Format-Table -AutoSize'; }; "
        "if (( __hermes_batch )); then "
        "local __hermes_n=$__hermes_iters; (( __hermes_n > 0 )) || __hermes_n=1; "
        "while (( __hermes_count < __hermes_n )); do "
        "__hermes_snapshot; __hermes_count=$(( __hermes_count + 1 )); done; return 0; fi; "
        "while (( __hermes_iters == 0 || __hermes_count < __hermes_iters )); do "
        "clear; __hermes_snapshot; __hermes_count=$(( __hermes_count + 1 )); "
        "(( __hermes_iters > 0 && __hermes_count >= __hermes_iters )) && break; "
        "sleep \"$__hermes_delay\"; done"
    ),
    "ss": (
        "local __hermes_stats=0; "
        "local -a __hermes_split=(); "
        "local __hermes_combo='' __hermes_i=0; "
        "while (( $# )); do "
        "if [[ $1 == -[!-]* && ${#1} -gt 2 ]]; then "
        "__hermes_combo=${1#-}; __hermes_split=(); shift; "
        "for (( __hermes_i=0; __hermes_i<${#__hermes_combo}; __hermes_i++ )); do "
        "__hermes_split+=(-${__hermes_combo:__hermes_i:1}); done; "
        "set -- \"${__hermes_split[@]}\" \"$@\"; continue; fi; "
        "case $1 in "
        "-s|--summary) __hermes_stats=1; shift;; "
        "-t|-u|-l|-n|-a|-p|-e|-m|-r|-i|-x|-4|-6|-T|--tcp|--udp|--listening|--numeric|--all|--process|--extended|--memory|--resolve|--inet|--inet4|--inet6) shift;; "
        "--*) shift;; "
        "-*) printf '%s\n' \"ss: unsupported option: $1\" >&2; return 1;; "
        "*) shift;; esac; done; "
        "if (( __hermes_stats )); then netstat -s; else netstat -ano; fi"
    ),
    "ip": (
        "local __hermes_sub=''; "
        "while (( $# )); do case $1 in "
        "-4|-6|-br|--brief|-details|-s|-human|-iec|-o|-oneline|-c|--color|-color) shift;; "
        "-h|--help) __hermes_sub=help; shift;; "
        "-*) printf '%s\n' \"ip: unsupported option: $1\" >&2; return 1;; "
        "*) if [[ -z $__hermes_sub ]]; then __hermes_sub=$1; fi; shift;; esac; done; "
        "case $__hermes_sub in "
        "help) printf '%s\n' 'Usage: ip [addr|link|route|neigh] (Windows equivalents: ipconfig / Get-NetAdapter / route print / arp -a)'; return 0;; "
        "'') printf '%s\n' 'Usage: ip [addr|link|route|neigh] (Windows equivalents: ipconfig / Get-NetAdapter / route print / arp -a)' >&2; return 1;; "
        "addr|address|a) ipconfig;; "
        "link|l) powershell.exe -NoProfile -NonInteractive -Command "
        "'Get-NetAdapter | ForEach-Object { \"$($_.Name) $($_.Status) $($_.LinkSpeed) $($_.MacAddress)\" }';; "
        "route|r) route print;; "
        "neigh|n) arp -a;; "
        "*) printf '%s\n' \"ip: unsupported object: $__hermes_sub (supported: addr link route neigh)\" >&2; return 1;; esac"
    ),
    "man": (
        "local __hermes_rc=0 __hermes_seen=0; "
        "while (( $# )); do case $1 in "
        "--help) printf '%s\n' 'man: show command help (fallback prints <command> --help)'; return 0;; "
        "--) shift; break;; "
        "-*) printf '%s\n' \"man: unsupported option for --help fallback: $1\" >&2; return 1;; "
        "*) break;; esac; done; "
        "while (( $# )); do "
        "if [[ $1 =~ ^[0-9]+$ ]]; then shift; continue; fi; "
        "__hermes_seen=1; \"$1\" --help || __hermes_rc=1; shift; done; "
        "if (( ! __hermes_seen )); then printf '%s\n' 'man: missing command name' >&2; return 1; fi; "
        "return $__hermes_rc"
    ),
    "systemctl": (
        "local __hermes_cmd=''; "
        "local -a __hermes_names=(); "
        "while (( $# )); do case $1 in "
        "-q|--quiet|--no-pager|--plain|--full|-l|--no-legend|--no-ask-password|--user|--system|--global) shift;; "
        "--type=*) shift;; "
        "--) shift; break;; "
        "-*) printf '%s\n' \"systemctl: unsupported option: $1\" >&2; return 1;; "
        "*) if [[ -z $__hermes_cmd ]]; then __hermes_cmd=$1; else __hermes_names+=(\"$1\"); fi; shift;; esac; done; "
        "if [[ -z $__hermes_cmd ]]; then "
        "printf '%s\n' 'systemctl: missing subcommand (Windows service equivalents; supported: status start stop restart reload enable disable is-active is-enabled list-units list-unit-files)' >&2; return 1; fi; "
        "case $__hermes_cmd in "
        "status) (( ${#__hermes_names[@]} )) || { printf '%s\n' 'systemctl: missing unit name' >&2; return 1; }; "
        "local __hermes_csv=$(IFS=,; echo \"${__hermes_names[*]}\"); "
        "powershell.exe -NoProfile -NonInteractive -Command "
        "\"Get-Service -Name '$__hermes_csv' | Format-List Name,DisplayName,Status,StartType\";; "
        "start|stop|restart|reload) (( ${#__hermes_names[@]} )) || { printf '%s\n' 'systemctl: missing unit name' >&2; return 1; }; "
        "local __hermes_ps='' __hermes_n; "
        "for __hermes_n in \"${__hermes_names[@]}\"; do "
        "if [[ $__hermes_cmd == stop || $__hermes_cmd == restart || $__hermes_cmd == reload ]]; then "
        "__hermes_ps+=\"Stop-Service -Name '$__hermes_n' -ErrorAction Stop; \"; fi; "
        "if [[ $__hermes_cmd == start || $__hermes_cmd == restart || $__hermes_cmd == reload ]]; then "
        "__hermes_ps+=\"Start-Service -Name '$__hermes_n' -ErrorAction Stop; \"; fi; done; "
        "powershell.exe -NoProfile -NonInteractive -Command \"$__hermes_ps\";; "
        "enable|disable) (( ${#__hermes_names[@]} )) || { printf '%s\n' 'systemctl: missing unit name' >&2; return 1; }; "
        "local __hermes_start=demand; [[ $__hermes_cmd == enable ]] && __hermes_start=auto; "
        "local __hermes_rc=0 __hermes_n; "
        "for __hermes_n in \"${__hermes_names[@]}\"; do "
        "sc.exe config \"$__hermes_n\" start= \"$__hermes_start\" >/dev/null || __hermes_rc=1; done; "
        "return $__hermes_rc;; "
        "is-active) (( ${#__hermes_names[@]} )) || { printf '%s\n' 'systemctl: missing unit name' >&2; return 1; }; "
        "local __hermes_rc=0 __hermes_n; "
        "for __hermes_n in \"${__hermes_names[@]}\"; do "
        "if sc.exe query \"$__hermes_n\" 2>/dev/null | grep -q 'RUNNING'; then "
        "printf '%s\n' active; else printf '%s\n' inactive; __hermes_rc=3; fi; done; "
        "return $__hermes_rc;; "
        "is-enabled) (( ${#__hermes_names[@]} )) || { printf '%s\n' 'systemctl: missing unit name' >&2; return 1; }; "
        "local __hermes_rc=0 __hermes_n; "
        "for __hermes_n in \"${__hermes_names[@]}\"; do "
        "if sc.exe qc \"$__hermes_n\" 2>/dev/null | grep -q 'DISABLED'; then "
        "printf '%s\n' disabled; __hermes_rc=1; else printf '%s\n' enabled; fi; done; "
        "return $__hermes_rc;; "
        "list-units|list-unit-files) "
        "powershell.exe -NoProfile -NonInteractive -Command "
        "'Get-Service | ForEach-Object { \"$($_.Status) $($_.Name) $($_.DisplayName)\" }';; "
        "*) printf '%s\n' \"systemctl: unsupported subcommand: $__hermes_cmd (supported: status start stop restart reload enable disable is-active is-enabled list-units list-unit-files)\" >&2; return 1;; esac"
    ),
    # Windows 11 ships sudo.exe in system32; older Windows releases have no
    # sudo at all, so the guard below only defines this on those hosts.  The
    # body elevates through a UAC prompt: output is NOT captured (it shows in
    # the elevated window), which is an honest, documented deviation.
    "sudo": (
        "if (( $# == 0 )); then printf '%s\n' 'sudo: missing command' >&2; return 1; fi; "
        "local __hermes_bash; __hermes_bash=$(type -P bash) || { printf '%s\n' 'sudo: bash not found' >&2; return 1; }; "
        "local __hermes_wbash; __hermes_wbash=$(cygpath -w -- \"$__hermes_bash\") || return 1; "
        "printf '%s\n' 'sudo: elevating via UAC in a separate window; output is not captured here' >&2; "
        "__HERMES_SUDO_CMD=\"$*\" powershell.exe -NoProfile -NonInteractive -Command "
        "\"Start-Process -Verb RunAs -Wait -FilePath '$__hermes_wbash' -ArgumentList '-c', \\$env:__HERMES_SUDO_CMD\"; "
        "local __hermes_rc=$?; return $__hermes_rc"
    ),
}


# GNU ``g``-prefixed command names (the Homebrew coreutils spelling used on
# macOS) map to the very same GNU tools that Git Bash already ships, so the
# mapping is faithful by construction.  ``gtimeout`` is spelled out above.
for _gnu_command in (
    "awk", "cat", "comm", "cp", "cut", "date", "df", "du", "egrep",
    "fgrep", "find", "grep", "head", "join", "ln", "ls", "make", "mkdir",
    "mv", "paste", "readlink", "realpath", "rm", "rmdir", "sed", "seq",
    "shuf", "sort", "split", "stat", "tail", "tar", "tr", "uniq", "wc",
    "xargs",
):
    _FALLBACK_BODIES.setdefault("g" + _gnu_command, f'{_gnu_command} "$@"')


def _fallback_definition(name: str) -> str:
    body = _FALLBACK_BODIES[name]
    if name in _STUB_AWARE_FALLBACKS:
        # The Microsoft Store App Execution Alias satisfies ``command -v``
        # but is not a working interpreter: define the fallback anyway, and
        # never delegate to the stub path.
        guard = (
            f"if ! command -v {name} >/dev/null 2>&1 "
            f"|| [[ $(type -P {name}) == *WindowsApps* ]]; then "
        )
        delegate = (
            f"local __hermes_native=''; __hermes_native=$(type -P {name}) || :; "
            f"if [[ -n $__hermes_native && $__hermes_native != *WindowsApps* ]]; then "
            f"\"$__hermes_native\" \"$@\"; return; fi; "
        )
    else:
        guard = f"if ! command -v {name} >/dev/null 2>&1; then "
        delegate = _NATIVE_DELEGATE.format(name=name)
    return f"{guard}{name}() {{ {delegate}{body}; }}; fi"


def _single_quote(command: str) -> str:
    """Quote *command* as one literal Bash word."""
    return "'" + command.replace("'", "'\"'\"'") + "'"


def _wrapper_runner(name: str) -> str:
    """Return an executable command for wrappers that cannot invoke functions."""
    script = _fallback_definition(name) + f"; {name} \"$@\""
    return "/usr/bin/bash -c " + _single_quote(script) + " --"


# ``netcat`` is a common synonym for ``nc`` on systems where the binary is
# spelled with the longer name; both are absent from Git Bash, so share the
# same ``/dev/tcp`` zero-I/O fallback.
_FALLBACK_BODIES.setdefault("netcat", _FALLBACK_BODIES["nc"])

# ``htop`` is the fancy TUI spelling of the same live-process view; the
# Windows approximation is identical to ``top``'s.
_FALLBACK_BODIES.setdefault("htop", _FALLBACK_BODIES["top"])

_FALLBACKS = {name: _fallback_definition(name) for name in _FALLBACK_BODIES}

# Commands with no faithful Windows Git Bash equivalent at all.  The scanner
# records these in ``BashFix.unsupported`` (command text is left untouched)
# so the app layer can refuse to run the command and return the reason in its
# error message instead of letting Bash fail with a bare "command not found".
_UNSUPPORTED_BODIES = {
    "journalctl": (
        "systemd's journal does not exist on Windows and Git Bash has no "
        "journal daemon, so there is no faithful equivalent. Read the "
        "application's own log file(s), or query the Windows Event Log "
        "instead: powershell.exe Get-WinEvent -LogName Application -MaxEvents 50"
    ),
    "goto": (
        "cmd.exe 'goto'/':label' jumps have no bash equivalent: bash has no "
        "label control flow and would run the jumped-over code anyway "
        "(command not found, then continue linearly). Restructure with bash "
        "if/else/loops/functions, or run the batch through cmd.exe "
        "directly: cmd //c script.bat"
    ),
    "call :label": (
        "cmd.exe 'call :label'/'call script.bat' subroutine or child-script "
        "invocation has no bash equivalent: bash cannot jump to a batch "
        "label or run a .bat inline. Define the routine as a bash function "
        "and invoke it, or run the batch through cmd.exe directly: "
        "cmd //c script.bat"
    ),
    "exit /b": (
        "cmd.exe 'exit /b [N]' returns from a batch script with exit code N; "
        "there is no batch here, so there is no faithful equivalent, and a "
        "plain bash 'exit N' would close the persistent shell session "
        "instead of just the block. Guard the remaining commands with "
        "if/else, or return from a function, or run the batch through "
        "cmd.exe directly: cmd //c script.bat"
    ),
}

# Fallback names that behave like command wrappers too: the word itself
# needs its fallback definition recorded AND its first non-option operand
# is a command the wrapper executes (consumed by the bash scanner; see
# shell_compat.bash_core).  The wrapper-semantics note for
# each name lives next to _COMMAND_WRAPPERS in bash_core.
_FALLBACK_COMMAND_WRAPPERS = {
    "gtimeout": "timeout",
    "watch": "watch",
    "sudo": "sudo",
}
