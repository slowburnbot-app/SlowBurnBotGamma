#!/usr/bin/env python3
"""Fail when a PyInstaller onefile binary needs a newer glibc than the runtime image has.

Usage:  python scripts/check_glibc.py <binary> --max <X.Y>
        (needs PyInstaller: the release job installs it)

Why: PyInstaller binds the bundled libraries to the glibc of the build host. v1.220 was
built on a runner with glibc 2.42, the runtime image has 2.41, and the container stopped at
start with "GLIBC_2.42 not found" while the release run was green (2026-11-20).

How: the onefile bundle is compressed, so a text scan of the binary finds only the
bootloader (GLIBC_2.14). Each bundled library is read from the archive instead, and the
highest "GLIBC_X.Y" symbol version in it is taken.

Exit 0: every library needs <= --max. Exit 1: a library needs more (each one is listed).
Exit 2: the binary or the archive cannot be read, or the archive holds no library (a green
result with nothing checked would be worse than a failure).
"""
import argparse
import re
import sys

GLIBC_RE = re.compile(rb"GLIBC_(\d+)\.(\d+)(?:\.(\d+))?")


def highest(data):
    """Highest GLIBC_X.Y[.Z] version in `data` as a tuple, or None."""
    found = [tuple(int(g) for g in m.groups() if g is not None) for m in GLIBC_RE.finditer(data)]
    return max(found) if found else None


def fmt(version):
    return ".".join(str(n) for n in version)


def parse_version(text):
    try:
        return tuple(int(p) for p in text.split("."))
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a version: {text!r}")


def libraries(binary):
    """Yield (name, bytes) of every bundled shared library, then the bootloader itself."""
    from PyInstaller.archive.readers import CArchiveReader

    reader = CArchiveReader(binary)
    names = [n for n in reader.toc if re.search(r"\.so(\.|$)", n)]
    if not names:
        raise ValueError("no shared library in the archive")
    for name in names:
        yield name, reader.extract(name)
    with open(binary, "rb") as fh:
        yield "(bootloader)", fh.read(2_000_000)   # the ELF part in front of the archive


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("binary")
    ap.add_argument("--max", required=True, type=parse_version, help="glibc of the runtime image, e.g. 2.41")
    args = ap.parse_args(argv)

    try:
        results = [(name, highest(data)) for name, data in libraries(args.binary)]
    except Exception as exc:   # unreadable file, wrong PyInstaller version, empty archive
        print(f"check_glibc: cannot read {args.binary}: {exc}", file=sys.stderr)
        return 2

    versions = [(n, v) for n, v in results if v]
    top = max(versions, key=lambda nv: nv[1]) if versions else None
    bad = sorted((n, v) for n, v in versions if v > args.max)
    print(f"check_glibc: {len(results)} libraries read; "
          f"highest need: {fmt(top[1]) if top else 'none'}{f' ({top[0]})' if top else ''}; "
          f"runtime glibc: {fmt(args.max)}")
    if bad:
        for name, v in bad:
            print(f"  TOO NEW: {name} needs GLIBC_{fmt(v)}", file=sys.stderr)
        print(f"check_glibc: FAIL - {len(bad)} librar{'y' if len(bad) == 1 else 'ies'} need more than glibc {fmt(args.max)}",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
