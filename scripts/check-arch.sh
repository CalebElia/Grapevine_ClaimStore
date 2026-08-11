#!/usr/bin/env bash
# Toolchain architecture guard.
#
# This machine is an M4 Pro migrated from a ~10-year-old Intel Mac. An x86_64
# toolchain is one bad PATH entry away, and the failure is SILENT: an x86_64
# python3 gets no MPS backend and an x86_64 ffmpeg gets no VideoToolbox, so S1/S2
# fall back to CPU and still produce correct output -- just hours later instead of
# minutes. Nothing raises, nothing warns. This script is the thing that notices.
#
#   ./scripts/check-arch.sh      exit 0 if the toolchain is native, 1 if it regressed
#
# tests/test_toolchain_arch.py runs this on every `pytest tests/`, so it fires
# without anyone remembering to run it.
#
# Output contract: one TAB-separated line per check -- STATUS<TAB>name<TAB>detail.
# The test parses those; lines without exactly two tabs are ignored, so summary
# prose below is safe to add. Keep the field format stable.
#
# Deliberately NOT `set -e`: every check must run so one failure does not mask
# the rest.
set -uo pipefail

# postgres lives in the grapevine-db conda env and is intentionally NOT on PATH,
# so it is probed by absolute path. Mirrors scripts/db.sh.
PGBIN="${CONDA_PREFIX_ROOT:-/opt/miniconda3}/envs/grapevine-db/bin"

# The guard's OWN tooling is addressed absolutely, because PATH is the thing this
# script polices and must not be able to switch it off. sysctl in particular lives
# in /usr/sbin, which a trimmed PATH (launchd, cron, CI) routinely omits -- when
# that lookup failed by name this script exited 0 having checked nothing, which is
# a worse outcome than no guard at all. Binaries under test are still resolved
# through PATH via `command -v`, deliberately: that IS the thing being checked.
SYSCTL=/usr/sbin/sysctl
FILE=/usr/bin/file
UNAME=/usr/bin/uname

failed=0

report() { # status name detail
  printf '%s\t%s\t%s\n' "$1" "$2" "$3"
  [ "$1" = FAIL ] && failed=1
  return 0
}

# A universal binary carrying an arm64 slice passes: on Apple Silicon dyld always
# picks the native slice. Only a pure-x86_64 binary is a regression.
check_binary_arm64() { # name path
  local name=$1 path=${2:-} desc
  if [ -z "$path" ]; then
    report FAIL "$name" "not found on PATH"
    return 0
  fi
  if [ ! -e "$path" ]; then
    report FAIL "$name" "does not exist: $path"
    return 0
  fi
  desc=$("$FILE" -b "$path" 2>/dev/null)
  case "$desc" in
    *arm64*) report PASS "$name" "$path -- $desc" ;;
    *) report FAIL "$name" "NOT arm64: $path -- $desc" ;;
  esac
  return 0
}

# Skip only on a host that is positively identified as not-Apple-Silicon. If the
# probes themselves are missing we cannot make that determination, so fail loudly
# rather than assume the guard is inapplicable.
if [ ! -x "$SYSCTL" ] || [ ! -x "$UNAME" ]; then
  echo "FAIL	host_probe	cannot run $SYSCTL / $UNAME to identify the host" >&2
  echo "Refusing to report success without being able to check." >&2
  exit 1
fi

if [ "$("$UNAME" -s)" != Darwin ] || [ "$("$SYSCTL" -n hw.optional.arm64 2>/dev/null)" != 1 ]; then
  echo "Host is not Apple Silicon macOS; this guard does not apply."
  exit 0
fi

# --- is this very shell translated? -----------------------------------------
translated=$("$SYSCTL" -n sysctl.proc_translated 2>/dev/null || echo 0)
if [ "${translated:-0}" = 0 ]; then
  report PASS shell_not_translated "running native, not under Rosetta"
else
  report FAIL shell_not_translated "this shell is running under Rosetta translation"
fi

# --- python3 ------------------------------------------------------------------
# platform.machine() is authoritative here: it reports what the interpreter is
# actually executing as, which is the thing that decides whether MPS exists.
py=$(command -v python3 || true)
if [ -z "$py" ]; then
  report FAIL python3_arm64 "python3 not found on PATH"
else
  mach=$("$py" -c 'import platform;print(platform.machine())' 2>/dev/null || echo unknown)
  if [ "$mach" = arm64 ]; then
    report PASS python3_arm64 "$py reports $mach"
  else
    report FAIL python3_arm64 "NOT arm64: $py reports $mach"
  fi
fi

# --- ffmpeg / ffprobe ---------------------------------------------------------
ff=$(command -v ffmpeg || true)
check_binary_arm64 ffmpeg_arm64 "$ff"
check_binary_arm64 ffprobe_arm64 "$(command -v ffprobe || true)"

# NOTE: this check does NOT discriminate architecture -- verified on this machine
# that the x86_64 Homebrew ffmpeg both lists these encoders and really does
# hardware-encode under Rosetta, because VideoToolbox is a system framework and
# Rosetta translates the calls fine. What Rosetta actually costs ffmpeg is the
# CPU side: x264/x265/libaom ship hand-written arm64 NEON assembly that a
# translated build never reaches, plus every filter, resample and audio-extract
# path runs translated. Keep this check anyway -- it catches a stripped build
# with VideoToolbox compiled out, which is a separate real regression.
if [ -n "$ff" ] && "$ff" -hide_banner -encoders 2>/dev/null | grep -q videotoolbox; then
  report PASS ffmpeg_videotoolbox "VideoToolbox encoders available"
else
  report FAIL ffmpeg_videotoolbox "no VideoToolbox encoders -- hardware encode/decode unavailable"
fi

# --- postgres -----------------------------------------------------------------
check_binary_arm64 postgres_arm64 "$PGBIN/postgres"

# --- torch MPS ----------------------------------------------------------------
if [ -z "$py" ]; then
  report FAIL torch_mps "no python3 on PATH to test"
else
  mps=$("$py" -c 'import torch;print("OK" if torch.backends.mps.is_available() else "NO_MPS")' 2>/dev/null || echo IMPORT_FAIL)
  case "$mps" in
    OK) report PASS torch_mps "torch.backends.mps.is_available() is True" ;;
    NO_MPS) report FAIL torch_mps "torch imported but MPS is unavailable -- no GPU acceleration" ;;
    *) report FAIL torch_mps "could not import torch with $py" ;;
  esac
fi

# --- summary ------------------------------------------------------------------
if [ "$failed" -ne 0 ]; then
  echo ""
  echo "TOOLCHAIN REGRESSION DETECTED -- see the FAIL lines above."
  echo "This class of failure is silent: output stays correct, but MPS and/or"
  echo "VideoToolbox are gone and transcription takes hours instead of minutes."
  echo "Check PATH order first: /opt/homebrew and /usr/local must not shadow the"
  echo "arm64 miniconda python3 or ~/.local/bin/ffmpeg."
  exit 1
fi

echo ""
echo "All toolchain checks passed -- native arm64, MPS and VideoToolbox available."
