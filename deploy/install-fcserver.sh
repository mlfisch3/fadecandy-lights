#!/usr/bin/env bash
#
# Install fcserver, the stock Fadecandy Open Pixel Control server.
#
#     sudo ./deploy/install-fcserver.sh              # ask before changing anything
#     sudo ./deploy/install-fcserver.sh --yes        # no prompt
#          ./deploy/install-fcserver.sh --dry-run    # print the plan, touch nothing
#
# fcserver is not part of this repository and cannot be built from source any
# more; see docs/fcserver.md for why, and for what this script is doing and to
# whom it is talking. The short version: Micah Scott's original repository is
# gone from GitHub, so the binary comes from an unmaintained third-party
# mirror, pinned by commit and verified by digest.
#
# The only prebuilt Linux/ARM binary anyone ships is 32-bit armhf. On a 64-bit
# Raspberry Pi OS image it needs the armhf runtime alongside the arm64 one -
# not emulation; a Cortex-A53 runs the 32-bit instruction set natively.
#
# Exit codes: 0 installed or already present, 1 failed, 2 declined,
#             3 no binary exists for this architecture.

set -euo pipefail

# Pinned to a commit rather than a branch so the bytes cannot change under us,
# and checked against the digest of the binary this pin was written for.
FCSERVER_COMMIT=36f616158f195a327f8486474af2956dad52881d
FCSERVER_URL="${FCSERVER_URL:-https://github.com/PimentNoir/fadecandy/raw/${FCSERVER_COMMIT}/bin/fcserver-rpi}"
FCSERVER_SHA256="${FCSERVER_SHA256:-a3efacd668f8aea042f4948e25753d3cba603c78451409ca04dbb2da4d7a6fb7}"
FCSERVER_BIN="${FCSERVER_BIN:-/usr/local/bin/fcserver}"

ASSUME_YES=0
DRY_RUN=0
for arg in "$@"; do
    case "${arg}" in
        -y|--yes)     ASSUME_YES=1 ;;
        -n|--dry-run) DRY_RUN=1 ;;
        -h|--help)    sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)            printf 'error: unknown option %s\n' "${arg}" >&2; exit 1 ;;
    esac
done

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33mwarning: %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[31merror: %s\033[0m\n' "$*" >&2; exit 1; }
step() { printf '    %s\n' "$*"; }

# --- Architecture ---------------------------------------------------------
#
# FCSERVER_ARCH exists so the detection and the plan can be exercised for an
# architecture the test machine is not; it is not meant for operators.

detect_arch() {
    if [[ -n "${FCSERVER_ARCH:-}" ]]; then
        printf '%s\n' "${FCSERVER_ARCH}"
    elif command -v dpkg >/dev/null 2>&1; then
        dpkg --print-architecture
    else
        uname -m
    fi
}

ARCH="$(detect_arch)"

# NEEDS_MULTIARCH is the whole point of this script: the two ARM cases install
# the same binary, and differ only in whether the 32-bit runtime has to be
# brought in first.
case "${ARCH}" in
    arm64|aarch64)
        NEEDS_MULTIARCH=1
        ARCH_NOTE="64-bit userland; the armhf binary needs the 32-bit runtime beside it" ;;
    armhf|armv7l|armv6l|arm)
        NEEDS_MULTIARCH=0
        ARCH_NOTE="32-bit ARM userland; the armhf binary runs as-is" ;;
    *)
        NEEDS_MULTIARCH=
        ARCH_NOTE="no prebuilt fcserver exists for this architecture" ;;
esac

say "fcserver install"
step "architecture: ${ARCH} - ${ARCH_NOTE}"
step "target:       ${FCSERVER_BIN}"

if [[ -z "${NEEDS_MULTIARCH}" ]]; then
    warn "unsupported architecture: ${ARCH}"
    cat >&2 <<'EOT'

    The mirror ships prebuilt fcserver binaries for 32-bit ARM (Raspberry Pi),
    i386 Linux (fcserver-galileo), macOS and Windows only, and the source tree
    can no longer be built - docs/fcserver.md has the evidence.

    On x86 Linux you can try bin/fcserver-galileo from the mirror by hand. On a
    Raspberry Pi, use a Raspberry Pi OS image (arm64 or armhf) and rerun this.

EOT
    exit 3
fi

# --- Plan -----------------------------------------------------------------

plan=()
if [[ -x "${FCSERVER_BIN}" ]]; then
    step "already installed, will re-verify only"
else
    plan+=("fetch ${FCSERVER_URL}")
    plan+=("verify sha256 ${FCSERVER_SHA256}")
    plan+=("install it as ${FCSERVER_BIN} (mode 0755)")
fi
if [[ ${NEEDS_MULTIARCH} -eq 1 ]]; then
    if [[ "$(dpkg --print-foreign-architectures 2>/dev/null || true)" == *armhf* ]]; then
        step "armhf is already a foreign architecture"
    else
        plan=("dpkg --add-architecture armhf" "apt-get update" "${plan[@]}")
    fi
    plan+=("apt-get install -y libc6:armhf libstdc++6:armhf")
fi

if [[ ${#plan[@]} -gt 0 ]]; then
    say "Plan"
    for p in "${plan[@]}"; do step "- ${p}"; done
fi

if [[ ${DRY_RUN} -eq 1 ]]; then
    say "Dry run; nothing was changed"
    exit 0
fi

if [[ ${#plan[@]} -gt 0 && ${ASSUME_YES} -eq 0 ]]; then
    if [[ ! -t 0 ]]; then
        warn "not a terminal and --yes was not given; declining to change anything"
        exit 2
    fi
    read -r -p "    Proceed? [Y/n] " reply
    case "${reply}" in
        ''|[Yy]*) : ;;
        *) say "Declined"; exit 2 ;;
    esac
fi

# Only the parts of the plan that need root demand it, so the fetch-and-verify
# path can be exercised against a writable target without sudo.
if [[ ${NEEDS_MULTIARCH} -eq 1 && $EUID -ne 0 ]]; then
    die "enabling the armhf runtime needs root; rerun with sudo"
fi
if [[ ! -x "${FCSERVER_BIN}" && ! -w "$(dirname "${FCSERVER_BIN}")" ]]; then
    die "cannot write ${FCSERVER_BIN}; rerun with sudo"
fi

# --- Do it ----------------------------------------------------------------

if [[ ${NEEDS_MULTIARCH} -eq 1 ]]; then
    say "Enabling the armhf runtime"
    if [[ "$(dpkg --print-foreign-architectures)" != *armhf* ]]; then
        dpkg --add-architecture armhf
        apt-get update -qq
    fi
    apt-get install -y --no-install-recommends libc6:armhf libstdc++6:armhf \
        || die "could not install the armhf runtime; see docs/fcserver.md"
fi

if [[ ! -x "${FCSERVER_BIN}" ]]; then
    say "Fetching fcserver"
    tmp="$(mktemp -d)"
    trap 'rm -rf "${tmp}"' EXIT
    step "from ${FCSERVER_URL}"
    curl -fsSL --retry 3 -o "${tmp}/fcserver" "${FCSERVER_URL}" \
        || die "download failed; see docs/fcserver.md for the manual steps"
    got="$(sha256sum "${tmp}/fcserver" | cut -d' ' -f1)"
    if [[ "${got}" != "${FCSERVER_SHA256}" ]]; then
        die "sha256 mismatch: got ${got}, expected ${FCSERVER_SHA256}. Refusing to install."
    fi
    step "sha256 ok"
    install -m 0755 "${tmp}/fcserver" "${FCSERVER_BIN}"
    step "installed ${FCSERVER_BIN}"
fi

# --- Prove it actually runs -----------------------------------------------
#
# This is the failure the whole script exists for: on arm64 without the armhf
# runtime the kernel loads the binary and the dynamic linker is missing, so the
# shell reports "cannot execute: required file not found" and nothing else.
# fcserver with an option it does not know prints its usage banner and exits
# non-zero without opening a socket, which is exactly the smoke test we want.

say "Checking that it runs"
out="$("${FCSERVER_BIN}" --help 2>&1 || true)"
case "${out}" in
    *"Fadecandy Open Pixel Control server"*|*"Error initializing USB library"*)
        step "$(printf '%s\n' "${out}" | grep -m1 '^fcserver-' || echo 'fcserver responded')"
        ;;
    *)
        printf '%s\n' "${out}" >&2
        die "${FCSERVER_BIN} did not run. If that says 'required file not found', the armhf runtime is missing; see docs/fcserver.md"
        ;;
esac

say "fcserver is installed"
