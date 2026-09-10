#!/usr/bin/env python3
"""Extract or CRC-test a zip archive, preserving each member's stored file mode.

Portable fallback for deploy/install.sh on hosts that have neither `unzip` nor
`bsdtar`. A minimal Ubuntu/WSL rootfs ships neither, and this installer is
no-sudo by design, so it cannot apt-install one. `python` is the one
interpreter install.sh is guaranteed to have -- it resolves ${PYTHON} from the
conda env it just built, one step earlier.

Why the modes matter: kSNP4 is stored 0755 and Kchooser4/MakeKSNP4infile are
stored 0744 inside the kSNP4.1 package, and install.sh widens them afterwards
with `chmod -R a+rX`. Capital X adds +x only to files that ALREADY carry an
execute bit, so an extractor that drops modes -- zipfile.extractall() does,
it writes 0644 -- turns that chmod into a no-op and yields an install whose
binaries cannot run while passing every `command -v` check. So the stored mode
is applied explicitly here.

Usage:
  ksnp_unzip.py <archive.zip> <destdir>   extract, overwriting (like unzip -o)
  ksnp_unzip.py --test <archive.zip>      CRC-check every member; exit 1 if bad
"""

import os
import stat
import sys
import zipfile

# For archives written by tools that record no POSIX mode (most Windows zip
# writers): the same defaults unzip itself falls back to.
DEFAULT_FILE_MODE = 0o644
DEFAULT_DIR_MODE = 0o755
CHUNK = 1024 * 1024


def _stored_mode(info):
    """(full_mode, permission_bits) as recorded, or (0, 0) if the zip has none."""
    # create_system 3 == Unix. Anything else did not store a POSIX mode, and
    # external_attr then holds DOS attribute bits that must not be read as one.
    if info.create_system != 3:
        return 0, 0
    full = info.external_attr >> 16
    return full, full & 0o7777


def _safe_target(dest, name):
    """Absolute path for a member inside dest, refusing zip-slip escapes."""
    root = os.path.realpath(dest)
    target = os.path.realpath(os.path.join(root, name))
    if target != root and not target.startswith(root + os.sep):
        sys.exit("refusing unsafe member path in archive: %r" % (name,))
    return target


def extract(archive, dest):
    os.makedirs(dest, exist_ok=True)
    deferred_dirs = []
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            target = _safe_target(dest, info.filename)
            full, perm = _stored_mode(info)

            if info.is_dir():
                os.makedirs(target, exist_ok=True)
                # Directory modes are applied at the very end: the kSNP package
                # stores its package dir as drwxr-Sr--, and setting that before
                # writing the files inside it would block those writes.
                deferred_dirs.append((target, perm or DEFAULT_DIR_MODE))
                continue

            parent = os.path.dirname(target)
            if parent:
                os.makedirs(parent, exist_ok=True)

            if full and stat.S_ISLNK(full):
                # The Mac package carries symlinks inside its app bundles; a
                # zip stores the link target as the member's contents.
                link = zf.read(info).decode("utf-8", "surrogateescape")
                if os.path.lexists(target):
                    os.unlink(target)
                os.symlink(link, target)
                continue

            # Streamed in chunks: individual members here run to 100s of MB.
            with zf.open(info) as src, open(target, "wb") as out:
                while True:
                    buf = src.read(CHUNK)
                    if not buf:
                        break
                    out.write(buf)
            os.chmod(target, perm or DEFAULT_FILE_MODE)

    # Deepest path first, so a parent turned read-only never blocks its children.
    for target, perm in sorted(deferred_dirs, key=lambda t: len(t[0]), reverse=True):
        os.chmod(target, perm)


def test(archive):
    with zipfile.ZipFile(archive) as zf:
        bad = zf.testzip()
    if bad is not None:
        sys.exit("CRC mismatch in archive member: %s" % (bad,))


def main(argv):
    try:
        if len(argv) == 3 and argv[1] == "--test":
            test(argv[2])
        elif len(argv) == 3:
            extract(argv[1], argv[2])
        else:
            sys.exit("usage: ksnp_unzip.py <archive.zip> <destdir>\n"
                     "       ksnp_unzip.py --test <archive.zip>")
    except zipfile.BadZipFile as exc:
        sys.exit("not a readable zip archive: %s" % (exc,))
    except OSError as exc:
        sys.exit("error unpacking archive: %s" % (exc,))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
