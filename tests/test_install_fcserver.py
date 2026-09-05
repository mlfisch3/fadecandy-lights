"""Behaviour of deploy/install-fcserver.sh, driven through its command line.

The script exists because the documented fcserver install was wrong twice over:
it pointed at a repository that no longer exists, and it handed a 32-bit armhf
binary to a machine running the 64-bit image this project targets. So the two
things worth pinning down are that it plans the right work for the architecture
it is on, and that it never installs a binary it could not verify.

Everything here runs the real script. The network-touching tests are marked and
the arm64 path is planned rather than executed, because no Pi is involved.
"""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
SCRIPT = REPO / "deploy" / "install-fcserver.sh"
SETUP = REPO / "deploy" / "setup.sh"

# Recorded from the binary the pinned commit serves; docs/fcserver.md explains
# why it is pinned by commit rather than tracking the mirror's master branch.
EXPECTED_SHA256 = "a3efacd668f8aea042f4948e25753d3cba603c78451409ca04dbb2da4d7a6fb7"

SIXTY_FOUR_BIT = ("arm64", "aarch64")
THIRTY_TWO_BIT = ("armhf", "armv7l", "armv6l", "arm")
NOT_ARM = ("amd64", "i386", "x86_64", "riscv64", "ppc64el")


def run(*args: str, arch: str | None = None, **env: str) -> subprocess.CompletedProcess[str]:
    environ = dict(os.environ, **env)
    if arch is not None:
        environ["FCSERVER_ARCH"] = arch
    return subprocess.run(
        [str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=environ,
        stdin=subprocess.DEVNULL,
        timeout=120,
    )


def plan(arch: str, target: Path) -> subprocess.CompletedProcess[str]:
    return run("--dry-run", arch=arch, FCSERVER_BIN=str(target))


@pytest.fixture
def target(tmp_path: Path) -> Path:
    return tmp_path / "bin" / "fcserver"


def test_script_is_executable_and_valid_bash() -> None:
    assert SCRIPT.stat().st_mode & stat.S_IXUSR
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
    subprocess.run(["bash", "-n", str(SETUP)], check=True)


@pytest.mark.parametrize("arch", SIXTY_FOUR_BIT)
def test_64_bit_plans_the_armhf_runtime(arch: str, target: Path) -> None:
    """The whole reason this script exists: arm64 needs the 32-bit userland."""
    result = plan(arch, target)
    assert result.returncode == 0, result.stderr
    assert "dpkg --add-architecture armhf" in result.stdout
    assert "libc6:armhf" in result.stdout
    assert "libstdc++6:armhf" in result.stdout


@pytest.mark.parametrize("arch", THIRTY_TWO_BIT)
def test_32_bit_arm_plans_no_multiarch(arch: str, target: Path) -> None:
    result = plan(arch, target)
    assert result.returncode == 0, result.stderr
    assert "add-architecture" not in result.stdout
    assert "libc6:armhf" not in result.stdout
    assert "apt-get" not in result.stdout


@pytest.mark.parametrize("arch", SIXTY_FOUR_BIT + THIRTY_TWO_BIT)
def test_every_supported_arch_installs_the_same_binary(arch: str, target: Path) -> None:
    result = plan(arch, target)
    assert EXPECTED_SHA256 in result.stdout
    assert f"install it as {target}" in result.stdout


@pytest.mark.parametrize("arch", NOT_ARM)
def test_non_arm_stops_with_its_own_exit_code(arch: str, target: Path) -> None:
    """No prebuilt fcserver exists off ARM, and guessing would be worse."""
    result = plan(arch, target)
    assert result.returncode == 3
    assert "unsupported architecture" in result.stderr
    assert "docs/fcserver.md" in result.stderr


def test_dry_run_changes_nothing(target: Path) -> None:
    assert plan("arm64", target).returncode == 0
    assert not target.exists()
    assert not target.parent.exists()


def test_without_a_terminal_it_declines_rather_than_assuming_consent(target: Path) -> None:
    target.parent.mkdir()
    result = run(arch="armhf", FCSERVER_BIN=str(target))
    assert result.returncode == 2
    assert not target.exists()


def test_the_plan_is_printed_before_consent_is_asked_for(target: Path) -> None:
    """An operator declining should still have been told what was proposed."""
    target.parent.mkdir()
    result = run(arch="armhf", FCSERVER_BIN=str(target))
    assert "==> Plan" in result.stdout
    assert EXPECTED_SHA256 in result.stdout


def test_a_bad_digest_is_fatal_and_installs_nothing(target: Path) -> None:
    target.parent.mkdir()
    result = run(
        "--yes",
        arch="armhf",
        FCSERVER_BIN=str(target),
        FCSERVER_SHA256="0" * 64,
        FCSERVER_URL=Path(__file__).resolve().as_uri(),
    )
    assert result.returncode == 1
    assert "sha256 mismatch" in result.stderr
    assert not target.exists()


def test_an_unreachable_source_is_fatal_and_installs_nothing(target: Path) -> None:
    target.parent.mkdir()
    result = run(
        "--yes",
        arch="armhf",
        FCSERVER_BIN=str(target),
        FCSERVER_URL=(target.parent / "absent").as_uri(),
    )
    assert result.returncode == 1
    assert "download failed" in result.stderr
    assert not target.exists()


def test_unknown_options_are_rejected() -> None:
    result = run("--definitely-not-an-option")
    assert result.returncode == 1
    assert "unknown option" in result.stderr


def test_setup_defers_to_the_installer_rather_than_printing_a_recipe() -> None:
    """Defect 1 was a dead URL pasted into setup.sh; it must not come back."""
    setup = SETUP.read_text()
    assert "install-fcserver.sh" in setup
    assert "scanlime" not in setup


def test_nothing_shipped_still_points_at_the_dead_upstream() -> None:
    tracked = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z", "README.md", "docs", "deploy"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\0")
    offenders = []
    for name in filter(None, tracked):
        path = REPO / name
        if path.suffix not in {".md", ".sh", ".service", ".rules", ".json"}:
            continue
        for number, line in enumerate(path.read_text().splitlines(), 1):
            # docs/wiring.md and docs/fcserver.md name it precisely to record
            # that it is gone; what must not survive is an instruction to fetch
            # from it.
            if "github.com/scanlime/fadecandy" in line and "404" not in line:
                offenders.append(f"{name}:{number}: {line.strip()}")
    assert offenders == [], "\n".join(offenders)


@pytest.mark.network
def test_the_pinned_url_serves_the_binary_we_expect() -> None:
    """The dead-upstream defect was a URL nobody rechecked. Recheck this one."""
    url = (
        f"https://github.com/PimentNoir/fadecandy/raw/{commit()}/bin/fcserver-rpi"
    )
    assert "raw/${FCSERVER_COMMIT}/bin/fcserver-rpi" in SCRIPT.read_text()
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            payload = response.read()
    except (urllib.error.URLError, TimeoutError) as exc:  # pragma: no cover - offline
        pytest.skip(f"no network: {exc}")
    assert hashlib.sha256(payload).hexdigest() == EXPECTED_SHA256
    # 32-bit ARM ELF: \x7fELF, class 1 (32-bit), and e_machine 0x28 = EM_ARM.
    assert payload[:5] == b"\x7fELF\x01"
    assert payload[18:20] == b"\x28\x00"


def commit() -> str:
    """The commit deploy/install-fcserver.sh pins the binary to."""
    return next(
        line.split("=", 1)[1].strip()
        for line in SCRIPT.read_text().splitlines()
        if line.startswith("FCSERVER_COMMIT=")
    )


def test_setup_survives_every_exit_code_the_installer_can_return() -> None:
    """A failed fcserver step must warn, not abort an otherwise complete install.

    `deploy/setup.sh` runs under `set -e`, so an unguarded call would take the
    whole install down at the last step - which is close to what the operator
    hit in the first place.
    """
    setup = SETUP.read_text()
    block = setup.split("install-fcserver.sh", 1)[1].split('say "Installing systemd units"')[0]
    assert "|| rc=$?" in block
    for code in ("2)", "3)", "*)"):
        assert code in block, f"setup.sh does not handle installer exit {code}"
