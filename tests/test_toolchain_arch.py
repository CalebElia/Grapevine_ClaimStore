"""Guard against a silent Rosetta regression in the local toolchain.

This machine is an M4 Pro that was migrated from a ~10-year-old Intel Mac, so an
x86_64 toolchain is one bad PATH entry away at all times. The failure mode has no
error message: an x86_64 `python3` gets no MPS backend and an x86_64 `ffmpeg` gets
no VideoToolbox, so S1/S2 fall back to CPU and transcription goes from minutes to
hours while still producing correct output. Nothing raises. You just wait longer.

These assertions are therefore deliberately *not* skippable on Apple Silicon --
skipping is the exact behaviour that would let the regression through. The whole
module skips only on a host that is not Apple Silicon at the hardware level, which
is checked via sysctl rather than platform.machine() because a translated x86_64
interpreter reports "x86_64" for the latter -- and that case must FAIL, not skip.

`scripts/check-arch.sh` holds the actual checks so the same guard runs from a shell
prompt. This module is the wiring that makes the suite run it on every invocation.
"""

from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "check-arch.sh"

# Every check scripts/check-arch.sh is expected to report on. Listed here so that a
# check silently disappearing from the script fails the suite instead of quietly
# reducing coverage.
EXPECTED_CHECKS = (
    "shell_not_translated",
    "python3_arm64",
    "ffmpeg_arm64",
    "ffmpeg_videotoolbox",
    "ffprobe_arm64",
    "postgres_arm64",
    "torch_mps",
)


def _is_apple_silicon() -> bool:
    """True on Apple Silicon hardware regardless of process translation.

    sysctl is addressed absolutely: it lives in /usr/sbin, which a trimmed PATH
    routinely omits, and resolving it by name would let a PATH problem turn this
    guard into a silent skip -- the precise outcome the module exists to prevent.
    """
    if platform.system() != "Darwin":
        return False
    try:
        out = subprocess.run(
            ["/usr/sbin/sysctl", "-n", "hw.optional.arm64"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return out.stdout.strip() == "1"


pytestmark = pytest.mark.skipif(
    not _is_apple_silicon(),
    reason="Apple Silicon toolchain guard; host hardware is not arm64",
)


@pytest.fixture(scope="module")
def report():
    """Run the guard once and parse its STATUS<TAB>name<TAB>detail lines."""
    proc = subprocess.run(
        [str(SCRIPT)], capture_output=True, text=True, timeout=180, cwd=REPO
    )
    rows = {}
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            rows[parts[1]] = (parts[0], parts[2])
    return proc, rows


def test_check_arch_script_is_present_and_executable():
    assert SCRIPT.is_file(), f"missing {SCRIPT}"
    assert os.access(SCRIPT, os.X_OK), f"not executable: {SCRIPT}"


@pytest.mark.parametrize("check", EXPECTED_CHECKS)
def test_toolchain_check_passes(report, check):
    proc, rows = report
    assert check in rows, (
        f"check-arch.sh reported no result for {check!r}. "
        f"Reported: {sorted(rows)}\n--- stdout ---\n{proc.stdout}"
        f"\n--- stderr ---\n{proc.stderr}"
    )
    status, detail = rows[check]
    assert status == "PASS", f"{check}: {detail}"


def test_check_arch_exits_zero(report):
    proc, _ = report
    assert proc.returncode == 0, (
        f"check-arch.sh exited {proc.returncode}\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    )
