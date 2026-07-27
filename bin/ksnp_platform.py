"""Is the vendored kSNP4 payload runnable on *this* host?

kSNP4 is not a conda package. SourceForge publishes two mutually incompatible
packages — kSNP4.1 Linux (ELF) and kSNP4.1 Mac (Mach-O) — and deploy/install.sh
unpacks one of them into ``vendor/kSNP4-bin``, which the launcher prepends to
PATH.

Every readiness check here used to be ``shutil.which(tool) is not None``. That
answers "is a file with this name on PATH", which is *not* the same question. A
host that got the Linux package (the URL used to be hard-coded) but runs macOS
passes every ``which`` check and then dies on the first exec::

    OSError: [Errno 8] Exec format error: 'MakeKSNP4infile'

...0.4 s into the run, after the GUI has already reported the job as started.

So: check the executable format, from the magic bytes. Not by exec'ing the
binaries (they take positional arguments and would block on stdin or write into
the caller's cwd), and not via ``file(1)`` (absent from minimal containers).

Both published packages are **x86_64 only**, so the OS is not the whole story —
the CPU architecture matters too, and the two hosts differ:

* **macOS/arm64** runs the x86_64 Mac package under **Rosetta 2**. Fine, as long
  as Rosetta is actually installed — if it is not, the failure is the same
  "Exec format error" with a completely different remedy, so it is reported
  separately rather than lumped in with a wrong-OS payload.
* **Linux/aarch64** (ARM WSL on Windows-on-ARM, Graviton, ARM servers) has no
  such translation layer. The x86_64 Linux package cannot run there at all, and
  no kSNP4.1 package exists that can.

This module is the single implementation of that question, used by the GUI's
readiness endpoint, the pipeline preflight, and (via the CLI at the bottom)
deploy/install.sh — so an install, a doctor run and a job launch cannot disagree.
"""

from __future__ import annotations

import platform
import shutil
import struct
import subprocess
from typing import List, Optional, Tuple

# The programs the pipeline shells out to. A missing vendor/kSNP4-bin loses all
# three at once, which is why they are checked as a set.
REQUIRED_TOOLS: Tuple[str, ...] = ("kSNP4", "Kchooser4", "MakeKSNP4infile")

# `kSNP4` itself is a bash script, so it says nothing about the payload's
# architecture — probe a compiled member instead.
_COMPILED_PROBES: Tuple[str, ...] = ("MakeKSNP4infile", "Kchooser4", "jellyfish")

_ELF = b"\x7fELF"
_MACHO_THIN = (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe")   # 64/32-bit LE
_MACHO_FAT = b"\xca\xfe\xba\xbe"                            # universal

# ELF e_machine (offset 0x12, uint16 LE) and Mach-O cputype (offset 4, uint32 LE).
_ELF_MACHINES = {0x03: "i386", 0x3E: "x86_64", 0xB7: "arm64", 0x28: "arm"}
_MACHO_CPUS = {0x00000007: "i386", 0x01000007: "x86_64", 0x0100000C: "arm64"}


def _head(path: str, n: int = 24) -> bytes:
    try:
        with open(path, "rb") as fh:
            return fh.read(n)
    except OSError:
        return b""


def inspect(path: str) -> Optional[Tuple[str, str]]:
    """(os, arch) a binary targets, e.g. ("linux", "x86_64"). None if unreadable,
    not a recognised executable, or a script (portable by nature)."""
    head = _head(path)
    if len(head) < 20 or head[:2] == b"#!":
        return None
    magic = head[:4]
    if magic == _ELF:
        machine = struct.unpack("<H", head[18:20])[0]
        return "linux", _ELF_MACHINES.get(machine, f"unknown(0x{machine:x})")
    if magic in _MACHO_THIN:
        cpu = struct.unpack("<I", head[4:8])[0]
        return "macos", _MACHO_CPUS.get(cpu, f"unknown(0x{cpu:x})")
    if magic == _MACHO_FAT:
        # A universal binary carries several slices; assume the loader will find
        # a usable one rather than parsing the fat header. Being permissive here
        # is right — this is a guard against the obvious mistake, not a loader.
        return "macos", "universal"
    return None


def describe(path: str) -> str:
    """Human-readable name of what a binary was built for."""
    info = inspect(path)
    if info is None:
        return "an unrecognised format"
    os_name, arch = info
    return f"{'Linux (ELF)' if os_name == 'linux' else 'macOS (Mach-O)'} {arch}"


def _rosetta_available() -> bool:
    """Can this Apple Silicon host run x86_64 binaries?"""
    try:
        return subprocess.run(["/usr/bin/arch", "-x86_64", "/usr/bin/true"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _host() -> Tuple[str, str]:
    sysname = platform.system()
    os_name = {"Linux": "linux", "Darwin": "macos"}.get(sysname, sysname.lower())
    machine = platform.machine().lower()
    arch = {"x86_64": "x86_64", "amd64": "x86_64",
            "arm64": "arm64", "aarch64": "arm64"}.get(machine, machine)
    return os_name, arch


def runnable(path: str) -> Tuple[bool, str]:
    """Can this host exec `path`? Returns (ok, reason-when-not).

    `ok` is True when there is nothing to object to — including when the file
    isn't a recognised binary at all, since absence and unreadability are the
    caller's existence checks to report, not this one's.
    """
    info = inspect(path)
    if info is None:
        return True, ""
    bin_os, bin_arch = info
    host_os, host_arch = _host()

    if host_os not in ("linux", "macos"):
        return True, ""      # no rules for this platform; don't invent a verdict

    if bin_os != host_os:
        # Name the host from the same derivation the decision used, not from a
        # second call to platform.system() — they agree today, and a message that
        # could contradict its own verdict is not worth the risk.
        host_pretty = {"linux": "Linux", "macos": "macOS"}[host_os]
        return False, (
            f"The installed kSNP4 binaries were built for {describe(path)}, but "
            f"this computer is {host_pretty} ({host_arch}). Every analysis would "
            f"fail immediately with \"Exec format error\". This happens when the "
            f"wrong SourceForge package was downloaded. "
            f"Fix with:  bin/bdtools install ksnp_gui    "
            f"Check with: bin/bdtools doctor ksnp_gui"
        )

    if bin_arch in ("universal", host_arch):
        return True, ""

    # Right OS, wrong CPU. The two hosts diverge here, and so does the remedy.
    if host_os == "macos" and host_arch == "arm64" and bin_arch == "x86_64":
        if _rosetta_available():
            return True, ""
        return False, (
            "kSNP4 ships as Intel (x86_64) binaries, which this Apple Silicon Mac "
            "runs through Rosetta 2 — and Rosetta 2 is not installed. Install it "
            "with:  softwareupdate --install-rosetta --agree-to-license    "
            "then re-run: bin/bdtools doctor ksnp_gui"
        )

    if host_os == "linux" and host_arch == "arm64":
        return False, (
            f"kSNP4 ships only as x86_64 binaries and this is an ARM (aarch64) "
            f"Linux host, which has no x86 translation layer. There is no kSNP4.1 "
            f"package that can run here. Run kSNP analyses on an x86_64 Linux "
            f"machine, a Mac, or an OOD deployment."
        )

    return False, (
        f"The installed kSNP4 binaries are {bin_arch} but this host is "
        f"{host_arch}. Every analysis would fail with \"Exec format error\"."
    )


def _probe_path() -> Optional[str]:
    for name in _COMPILED_PROBES:
        hit = shutil.which(name)
        if hit:
            return hit
    return None


def version_from_path(exe: Optional[str]) -> Optional[str]:
    """kSNP's version, derived from its install path (e.g. ".../kSNP4.1pkg/kSNP4"
    -> "kSNP4.1").

    kSNP4 and Kchooser4 have no --version flag; run with no arguments they print a
    usage error. The path is the only place the version appears. Lives here rather
    than in ksnp_pipeline.py so the GUI's Settings panel and the run manifest cannot
    report different versions for the same binary.
    """
    if not exe:
        return None
    import os
    import re
    m = re.search(r"kSNP(\d+(?:\.\d+)?)", os.path.realpath(exe))
    return f"kSNP{m.group(1)}" if m else "kSNP4"


def describe_payload() -> str:
    """What the kSNP4 payload on PATH was built for, e.g. "macOS (Mach-O) x86_64".

    Probes a COMPILED member: `kSNP4` itself is a bash script, so describing it
    would always say "an unrecognised format".
    """
    probe = _probe_path()
    return describe(probe) if probe else "not found"


def payload_platform_error() -> Optional[str]:
    """None when the kSNP4 binaries on PATH can run here; else why not.

    The message is meant to be shown to a non-technical user verbatim.
    """
    probe = _probe_path()
    if probe is None:
        # Nothing compiled resolved — that is a *missing* install, which the
        # caller's own existence check reports with a better message than this.
        return None
    ok, reason = runnable(probe)
    return None if ok else reason


def dir_error(directory: str) -> Optional[str]:
    """Same question, for an unpacked package dir rather than PATH."""
    import os
    for name in _COMPILED_PROBES:
        cand = os.path.join(directory, name)
        if os.path.isfile(cand):
            ok, reason = runnable(cand)
            return None if ok else reason
    return None      # nothing compiled to judge


def missing_tools() -> List[str]:
    """Required kSNP4 programs that do not resolve on PATH at all."""
    return [t for t in REQUIRED_TOOLS if shutil.which(t) is None]


def package_label() -> Optional[str]:
    """Which SourceForge package this host needs, or None if none can run here."""
    host_os, host_arch = _host()
    if host_os == "linux":
        # No package exists for ARM Linux — say so rather than naming one that
        # would only reproduce the exec failure.
        return "kSNP4.1 Linux package" if host_arch == "x86_64" else None
    if host_os == "macos":
        return "kSNP4.1 Mac package"
    return None


# ---------------------------------------------------------------------------
# CLI — so deploy/install.sh asks this module instead of reimplementing it in
# bash. It used to carry its own od(1)-based copy of the magic-byte logic; two
# implementations of a subtle check is how they drift apart.
#
#   ksnp_platform.py check-dir <dir>   exit 0 = runnable here, 1 = not (reason on stderr)
#   ksnp_platform.py describe <dir>    print what the payload was built for
#   ksnp_platform.py package           print the package this host needs (empty = none)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import os
    import sys

    args = sys.argv[1:]
    cmd = args[0] if args else ""

    if cmd == "check-dir" and len(args) == 2:
        err = dir_error(args[1])
        if err:
            print(err, file=sys.stderr)
            sys.exit(1)
        sys.exit(0)

    if cmd == "describe" and len(args) == 2:
        for _n in _COMPILED_PROBES:
            _c = os.path.join(args[1], _n)
            if os.path.isfile(_c):
                print(describe(_c))
                sys.exit(0)
        print("unknown")
        sys.exit(0)

    if cmd == "package":
        print(package_label() or "")
        sys.exit(0)

    print(__doc__.strip().splitlines()[0], file=sys.stderr)
    print("usage: ksnp_platform.py {check-dir DIR|describe DIR|package}", file=sys.stderr)
    sys.exit(2)
