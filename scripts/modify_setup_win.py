"""Patch Adobe's Windows Setup.exe so installer validation always succeeds."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Signature & patch bytes
# ---------------------------------------------------------------------------

# 28-byte unique prologue of the validation function in Setup.exe
WIN32_ORIGINAL_PATTERN = bytes([
    0x53, 0x8B, 0xDC, 0x83, 0xEC, 0x08, 0x83, 0xE4,
    0xF8, 0x83, 0xC4, 0x04, 0x55, 0x8B, 0x6B, 0x04,
    0x89, 0x6C, 0x24, 0x04, 0x8B, 0xEC, 0x6A, 0xFF,
    0x68, 0x4D, 0x35, 0x48,
])

# Replacement: first 4 bytes = push 1; pop eax; ret (forces return TRUE),
# remaining bytes are dead code (execution never reaches them).
WIN32_PATCHED_PATTERN = bytes([
    0x6A, 0x01, 0x58, 0xC3,  # push 1; pop eax; ret
]) + WIN32_ORIGINAL_PATTERN[4:]


def is_already_patched(data: bytes) -> bool:
    """Return True when the Setup.exe signature has already been replaced."""
    return data.find(WIN32_ORIGINAL_PATTERN) == -1


def apply_setup_patch(data: bytes) -> bytes:
    """Replace the validation routine prologue with a forced success return."""
    idx = data.find(WIN32_ORIGINAL_PATTERN)
    if idx == -1:
        raise RuntimeError(
            "Original pattern not found — binary may already be patched "
            "or is a different version."
        )
    patched_binary = bytearray(data)
    patched_binary[idx : idx + len(WIN32_PATCHED_PATTERN)] = WIN32_PATCHED_PATTERN
    return bytes(patched_binary)


def patch_setup_file(setup_path: Path) -> None:
    """Create a backup when needed and patch Setup.exe in place."""
    backup_path = setup_path.with_suffix(".exe.original")

    data = setup_path.read_bytes()

    if is_already_patched(data):
        print("[*] Setup.exe appears to be already patched.")
        return

    if backup_path.exists():
        print("[*] Backup already exists, restoring from backup first …")
        data = backup_path.read_bytes()
        setup_path.write_bytes(data)
    else:
        print("[*] Creating backup:", backup_path)
        shutil.copy2(setup_path, backup_path)

    patched = apply_setup_patch(data)
    setup_path.write_bytes(patched)

    verify = setup_path.read_bytes()
    if is_already_patched(verify):
        print("[+] Patch applied successfully.")
    else:
        print("[-] ERROR: verification failed after patching.")
        sys.exit(1)


def main() -> None:
    if len(sys.argv) != 2:
        print(f"Usage: {Path(sys.argv[0]).name} <path-to-Setup.exe>")
        sys.exit(2)

    setup_path = Path(sys.argv[1])

    if not setup_path.exists():
        print(f"[-] Setup.exe not found: {setup_path}")
        sys.exit(1)

    print(f"[*] Target: {setup_path}")
    patch_setup_file(setup_path)


if __name__ == "__main__":
    main()
