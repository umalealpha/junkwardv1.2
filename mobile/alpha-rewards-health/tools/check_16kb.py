#!/usr/bin/env python3
"""Check that an .aab/.apk is compatible with Android's 16 KB memory page size.

Google Play has enforced this since 1 November 2025 for apps targeting Android 15+.
A bundle passes when every 64-bit native library declares PT_LOAD segments aligned
to at least 16384 bytes. 32-bit ABIs (armeabi-v7a, x86) are reported but never
counted as failures: 16 KB pages only exist on 64-bit devices.

Reading p_align straight out of the ELF program headers is the check that matters.
`useLegacyPackaging = false` only controls how the .so sits inside the zip — it
cannot re-align a prebuilt library, so the packaging flag alone proves nothing.

Usage:
    python tools/check_16kb.py path/to/app-release.aab [...]

Exit status 0 when every 64-bit library is aligned, 1 otherwise.
"""

from __future__ import annotations

import struct
import sys
import zipfile

PAGE = 16 * 1024
PT_LOAD = 1
ABI_64 = ("arm64-v8a", "x86_64")
ABI_32 = ("armeabi-v7a", "x86")


def load_alignments(data: bytes) -> list[int] | None:
    """Return the p_align of every PT_LOAD segment, or None if not an ELF."""
    if data[:4] != b"\x7fELF":
        return None
    is_64 = data[4] == 2
    if is_64:
        (phoff,) = struct.unpack_from("<Q", data, 0x20)
        (phentsize,) = struct.unpack_from("<H", data, 0x36)
        (phnum,) = struct.unpack_from("<H", data, 0x38)
        align_off, align_fmt = 0x30, "<Q"
    else:
        (phoff,) = struct.unpack_from("<I", data, 0x1C)
        (phentsize,) = struct.unpack_from("<H", data, 0x2A)
        (phnum,) = struct.unpack_from("<H", data, 0x2C)
        align_off, align_fmt = 0x1C, "<I"

    aligns = []
    for i in range(phnum):
        entry = phoff + i * phentsize
        (p_type,) = struct.unpack_from("<I", data, entry)
        if p_type == PT_LOAD:
            (p_align,) = struct.unpack_from(align_fmt, data, entry + align_off)
            aligns.append(p_align)
    return aligns


def abi_of(name: str) -> str:
    for abi in ABI_64 + ABI_32:
        if f"/{abi}/" in name:
            return abi
    return "?"


def check(path: str) -> bool:
    with zipfile.ZipFile(path) as archive:
        libs = sorted(n for n in archive.namelist() if n.endswith(".so"))
        if not libs:
            print(f"{path}: no native libraries — nothing to align")
            return True

        failures = []
        checked = 0
        for name in libs:
            aligns = load_alignments(archive.read(name))
            if aligns is None:
                print(f"  SKIP  not an ELF file: {name}")
                continue
            worst = min(aligns) if aligns else 0
            abi = abi_of(name)
            if abi not in ABI_64:
                print(f"  n/a   32-bit, not page-checked: {name}")
                continue
            checked += 1
            ok = worst >= PAGE
            if not ok:
                failures.append(name)
            print(f"  {'PASS' if ok else 'FAIL'}  p_align={worst:<6} {name}")

        print(f"{path}: {checked - len(failures)}/{checked} 64-bit libraries 16 KB-aligned")
        if failures:
            print("  Google Play will reject this bundle. The misaligned libraries are")
            print("  prebuilt by dependencies — upgrade the dependency that ships them.")
        return not failures


def main(argv: list[str]) -> int:
    paths = argv[1:]
    if not paths:
        print(__doc__)
        return 2
    return 0 if all(check(p) for p in paths) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
