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
Rosetta 2 translates macOS x86_64, not Linux ELF, so there is no host
configuration that makes the wrong payload work — it has to be detected.

So: check the executable format, from the magic bytes. Not by exec'ing the
binaries (they take positional arguments and would block on stdin or write into
the caller's cwd), and not via ``file(1)`` (absent from minimal containers).
"""

from __future__ import annotations

import platform
import shutil
from typing import List, Optional, Tuple

# The programs the pipeline shells out to. A missing vendor/kSNP4-bin loses all
# three at once, which is why they are checked as a set.
REQUIRED_TOOLS: Tuple[str, ...] = ("kSNP4", "Kchooser4", "MakeKSNP4infile")

# `kSNP4` itself is a bash script, so it says nothing about the payload's
# architecture — probe a compiled member instead.
_COMPILED_PROBES: Tuple[str, ...] = ("MakeKSNP4infile", "Kchooser4", "jellyfish")

_ELF = b"\x7fELF"
# Mach-O 64-bit LE, 32-bit LE, and universal ("fat") binaries.
_MACHO = (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe")


def _magic(path: str) -> bytes:
    try:
        with open(path, "rb") as fh:
            return fh.read(4)
    except OSError:
        return b""


def describe_format(magic: bytes) -> str:
    """Human-readable name of the OS a payload with these magic bytes targets."""
    if magic == _ELF:
        return "Linux (ELF)"
    if magic in _MACHO:
        return "macOS (Mach-O)"
    return "an unrecognised format"


def payload_platform_error() -> Optional[str]:
    """``None`` when the kSNP4 binaries on PATH can run here; else why not.

    Returns a message meant to be shown to a non-technical user verbatim.
    """
    probe = next((p for p in _COMPILED_PROBES if shutil.which(p)), None)
    if probe is None:
        # Nothing compiled resolved — that is a *missing* install, which the
        # caller's own existence check reports with a better message than this.
        return None

    magic = _magic(shutil.which(probe) or "")
    if not magic:
        return None

    system = platform.system()
    if system == "Linux":
        expected_ok = magic == _ELF
        want = "Linux (ELF)"
    elif system == "Darwin":
        expected_ok = magic in _MACHO
        want = "macOS (Mach-O)"
    else:
        # Unknown OS: no kSNP4.1 package is published for it. Don't invent a
        # verdict — let the existence checks speak.
        return None

    if expected_ok:
        return None

    return (
        f"The installed kSNP4 binaries were built for {describe_format(magic)}, "
        f"but this computer is {system} and needs {want}. Every analysis would "
        f"fail immediately with \"Exec format error\". This happens when the "
        f"wrong SourceForge package was downloaded. "
        f"Fix with:  bin/bdtools install ksnp_gui    "
        f"Check with: bin/bdtools doctor ksnp_gui"
    )


def missing_tools() -> List[str]:
    """Required kSNP4 programs that do not resolve on PATH at all."""
    return [t for t in REQUIRED_TOOLS if shutil.which(t) is None]


def package_label() -> Optional[str]:
    """Which SourceForge package this host needs, or None if none is published."""
    return {"Linux": "kSNP4.1 Linux package", "Darwin": "kSNP4.1 Mac package"}.get(
        platform.system()
    )
