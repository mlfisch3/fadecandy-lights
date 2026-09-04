#!/usr/bin/env bash
#
# Install fclights on a fresh Raspberry Pi OS image.
#
# Run it from a clone of this repository, on the Pi:
#
#     sudo ./deploy/setup.sh
#
# It is idempotent: running it again upgrades in place and leaves your config,
# your layout and your saved scenes alone.
#
# What it does not do is build fcserver. Upstream ships an armhf binary that
# runs on both 32- and 64-bit Raspberry Pi OS; if it is not already present the
# script tells you where to get it and stops, rather than guessing.

set -euo pipefail

PREFIX=/opt/fclights
CONFIG_DIR=/etc/fclights
STATE_DIR=/var/lib/fclights
SERVICE_USER=fclights
FCSERVER_BIN=/usr/local/bin/fcserver
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33mwarning: %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[31merror: %s\033[0m\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run this with sudo"

say "Checking the host"
if [[ -r /proc/device-tree/model ]]; then
    MODEL="$(tr -d '\0' < /proc/device-tree/model)"
    echo "    board:  ${MODEL}"
    case "${MODEL}" in
        *"Raspberry Pi 3"*) : ;;
        *"Raspberry Pi"*)   warn "built and tested for a Pi 3 B+; ${MODEL} should work but is untested" ;;
    esac
else
    warn "this does not look like a Raspberry Pi; continuing anyway"
fi
echo "    arch:   $(dpkg --print-architecture)"
echo "    python: $(python3 --version)"

python3 - <<'PY' || die "fclights needs Python 3.10 or newer"
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY

say "Installing OS packages"
apt-get update -qq
# python3-venv for the virtualenv, libusb for fcserver, avahi for mDNS discovery.
apt-get install -y --no-install-recommends \
    python3-venv python3-pip \
    libusb-1.0-0 \
    avahi-daemon avahi-utils

say "Creating the ${SERVICE_USER} service account"
if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
    useradd --system --home-dir "${STATE_DIR}" --shell /usr/sbin/nologin "${SERVICE_USER}"
    echo "    created ${SERVICE_USER}"
else
    echo "    ${SERVICE_USER} already exists"
fi
# plugdev is what the udev rule grants Fadecandy access to.
usermod -aG plugdev "${SERVICE_USER}"

install -d -o "${SERVICE_USER}" -g "${SERVICE_USER}" -m 0755 "${STATE_DIR}"
install -d -m 0755 "${CONFIG_DIR}"

say "Installing fclights into ${PREFIX}"
install -d -m 0755 "${PREFIX}"
rm -rf "${PREFIX}/src" "${PREFIX}/docs"
cp -r "${REPO_ROOT}/src" "${PREFIX}/src"
cp -r "${REPO_ROOT}/docs" "${PREFIX}/docs"
cp "${REPO_ROOT}/pyproject.toml" "${REPO_ROOT}/README.md" "${PREFIX}/"

if [[ ! -d "${PREFIX}/venv" ]]; then
    python3 -m venv "${PREFIX}/venv"
fi
# --only-binary keeps a missing wheel loud rather than starting a numpy build
# that would take the better part of an hour on a Pi 3 and may well run the
# board out of memory.
"${PREFIX}/venv/bin/pip" install --quiet --upgrade pip wheel
"${PREFIX}/venv/bin/pip" install --quiet --only-binary=:all: --upgrade "${PREFIX}" \
    || die "a dependency has no prebuilt wheel for $(dpkg --print-architecture); see docs/bring-up.md"

say "Installing configuration"
for pair in "fclights.example.json:fclights.json" "layout.example.json:layout.json" "fcserver.json:fcserver.json"; do
    src="${REPO_ROOT}/config/${pair%%:*}"
    dst="${CONFIG_DIR}/${pair##*:}"
    if [[ -e "${dst}" ]]; then
        echo "    keeping your ${dst}"
        cp "${src}" "${dst}.new"
        echo "    (packaged version left at ${dst}.new)"
    else
        install -m 0644 "${src}" "${dst}"
        echo "    installed ${dst}"
    fi
done

say "Installing the udev rule for the Fadecandy"
install -m 0644 "${REPO_ROOT}/deploy/99-fadecandy.rules" /etc/udev/rules.d/99-fadecandy.rules
udevadm control --reload-rules
udevadm trigger --subsystem-match=usb || true

say "Checking for fcserver"
if [[ -x "${FCSERVER_BIN}" ]]; then
    echo "    found ${FCSERVER_BIN}"
else
    warn "${FCSERVER_BIN} is missing."
    cat <<'EOT'

    fcserver is the stock upstream Fadecandy server and is not part of this
    repository. Fetch the release binary and install it:

        wget https://github.com/scanlime/fadecandy/archive/refs/heads/master.tar.gz
        tar xf master.tar.gz
        sudo install -m 0755 fadecandy-master/bin/fcserver-rpi /usr/local/bin/fcserver

    Then rerun this script. Everything else has been installed already.

EOT
fi

say "Installing systemd units"
install -m 0644 "${REPO_ROOT}/deploy/fcserver.service" /etc/systemd/system/fcserver.service
install -m 0644 "${REPO_ROOT}/deploy/fclights.service" /etc/systemd/system/fclights.service
systemctl daemon-reload

say "Publishing the mDNS service name"
"${PREFIX}/venv/bin/fclights" announce --config "${CONFIG_DIR}/fclights.json"
systemctl reload avahi-daemon 2>/dev/null || systemctl restart avahi-daemon

say "Validating the installed configuration"
"${PREFIX}/venv/bin/fclights" check --config "${CONFIG_DIR}/fclights.json"

say "Enabling services"
systemctl enable fcserver.service fclights.service
if [[ -x "${FCSERVER_BIN}" ]]; then
    systemctl restart fcserver.service
fi
systemctl restart fclights.service

PORT="$(python3 -c "import json;print(json.load(open('${CONFIG_DIR}/fclights.json')).get('server',{}).get('port',7891))")"

say "Done"
cat <<EOT
    API          http://$(hostname).local:${PORT}/api/health
    mDNS         _fclights._tcp on $(hostname).local
    config       ${CONFIG_DIR}/fclights.json
    layout       ${CONFIG_DIR}/layout.json
    fcserver     ${CONFIG_DIR}/fcserver.json  (gamma and whitepoint live here)
    state        ${STATE_DIR}/state.json
    logs         journalctl -u fclights -u fcserver -f

    The power ceiling defaults to 24 A, the 30 A supply derated to 80%. Read
    the sizing section of README.md and set it to the real usable current of
    the supply feeding YOUR strip before you expect full brightness.

    Now work through docs/bring-up.md with the strip connected.

EOT
