"""deploy/setup.sh must survive whatever deploy/install-fcserver.sh returns.

setup.sh runs under `set -e` and calls the fcserver installer second-to-last,
so an unguarded call would take a complete, working install down at the final
step - which is close to what the operator hit in the first place. fcserver is
the one component that is not ours and cannot be built, so its install is the
step most likely to fail; the rest of the install must not depend on it.

These tests run the real setup.sh end to end against a scratch tree: its
install roots are pointed at a temporary directory, the commands that need a
real machine (apt-get, useradd, systemctl, udevadm) are stubbed on PATH, and
`unshare -r` supplies the root it insists on without granting any.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
SETUP = REPO / "deploy" / "setup.sh"

STUBBED = ("apt-get", "useradd", "usermod", "systemctl", "udevadm")


def fake_root() -> list[str]:
    """A command prefix that makes EUID 0 without granting real privilege."""
    if shutil.which("unshare") is None or subprocess.run(
        ["unshare", "-r", "true"], capture_output=True
    ).returncode:
        pytest.skip("unshare -r is unavailable, so setup.sh's root check cannot be met")
    return ["unshare", "-r"]


class Installation:
    """A scratch tree that setup.sh can be run against for real."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.root = tmp_path / "repo"
        self.prefix = tmp_path / "opt"
        self.config_dir = tmp_path / "etc" / "fclights"
        self.unit_dir = tmp_path / "etc" / "systemd"
        self.udev_dir = tmp_path / "etc" / "udev"
        self.fcserver_bin = tmp_path / "usr" / "bin" / "fcserver"
        self.installer_log = tmp_path / "installer.log"

        (self.root / "deploy").mkdir(parents=True)
        for name in ("setup.sh", "99-fadecandy.rules", "fcserver.service", "fclights.service"):
            shutil.copy(REPO / "deploy" / name, self.root / "deploy" / name)
        shutil.copytree(REPO / "config", self.root / "config")
        for name in ("src", "docs"):
            (self.root / name).mkdir()
            (self.root / name / "placeholder").write_text("")
        (self.root / "pyproject.toml").write_text("[project]\nname = 'fclights'\n")
        (self.root / "README.md").write_text("fclights\n")

        # setup.sh skips creating the venv if one is already there, so these
        # stand in for the pip and fclights it would otherwise have built.
        venv_bin = self.prefix / "venv" / "bin"
        venv_bin.mkdir(parents=True)
        for name in ("pip", "fclights"):
            self._executable(venv_bin / name, "exit 0")

        self.stub_bin = tmp_path / "stub-bin"
        self.stub_bin.mkdir()
        for name in STUBBED:
            self._executable(self.stub_bin / name, "exit 0")
        self._executable(self.stub_bin / "dpkg", "printf 'arm64\\n'\nexit 0")
        # install(1) does the real work here, minus the ownership it cannot
        # take in a user namespace where only one uid is mapped.
        self._executable(
            self.stub_bin / "install",
            """
            args=""
            while [ $# -gt 0 ]; do
                case "$1" in
                    -o|-g) shift 2 ;;
                    *) args="${args} $1"; shift ;;
                esac
            done
            exec /usr/bin/install ${args}
            """,
        )

    def _executable(self, path: Path, body: str) -> None:
        path.write_text("#!/bin/sh\n" + body.strip() + "\n")
        path.chmod(0o755)

    def with_installer_exiting(self, code: int) -> None:
        self._executable(
            self.root / "deploy" / "install-fcserver.sh",
            f'printf \'called with FCSERVER_BIN=%s\\n\' "${{FCSERVER_BIN}}" '
            f'>>"{self.installer_log}"\nexit {code}',
        )

    def run(self, *args: str, allow_non_pi: bool = True) -> subprocess.CompletedProcess[str]:
        environ = dict(
            os.environ,
            PATH=f"{self.stub_bin}{os.pathsep}{os.environ['PATH']}",
            FCLIGHTS_PREFIX=str(self.prefix),
            FCLIGHTS_CONFIG_DIR=str(self.config_dir),
            FCLIGHTS_STATE_DIR=str(self.tmp_path / "var"),
            FCLIGHTS_UNIT_DIR=str(self.unit_dir),
            FCLIGHTS_UDEV_DIR=str(self.udev_dir),
            FCSERVER_BIN=str(self.fcserver_bin),
        )
        # The tests run on Linux boxes that are not Raspberry Pis, so the host
        # guard fires unless explicitly allowed. The tests that exercise the
        # guard itself pass allow_non_pi=False.
        if allow_non_pi:
            environ["FCLIGHTS_ALLOW_NON_PI"] = "1"
        else:
            environ.pop("FCLIGHTS_ALLOW_NON_PI", None)
        return subprocess.run(
            [*fake_root(), str(self.root / "deploy" / "setup.sh"), *args],
            capture_output=True,
            text=True,
            env=environ,
            stdin=subprocess.DEVNULL,
            timeout=300,
        )

    def finished(self) -> bool:
        """Everything after the fcserver step actually happened."""
        return (
            (self.unit_dir / "fclights.service").is_file()
            and (self.unit_dir / "fcserver.service").is_file()
            and (self.config_dir / "fclights.json").is_file()
        )


@pytest.fixture
def installation(tmp_path: Path) -> Installation:
    return Installation(tmp_path)


@pytest.mark.parametrize(
    ("code", "expected_warning"),
    [
        (0, ""),
        (1, "installing fcserver failed"),
        (2, "fcserver was not installed"),
        (3, "no fcserver binary exists for this architecture"),
    ],
)
def test_setup_finishes_whatever_the_installer_returns(
    code: int, expected_warning: str, installation: Installation
) -> None:
    installation.with_installer_exiting(code)

    result = installation.run("--yes")

    assert result.returncode == 0, result.stdout + result.stderr
    assert installation.finished(), result.stdout + result.stderr
    assert "==> Done" in result.stdout
    assert installation.installer_log.read_text().startswith("called with FCSERVER_BIN=")
    if expected_warning:
        assert expected_warning in result.stderr
    else:
        assert "fcserver" not in result.stderr


def test_setup_tells_the_installer_where_the_binary_goes(installation: Installation) -> None:
    installation.with_installer_exiting(0)

    installation.run("--yes")

    log = installation.installer_log.read_text()
    assert f"called with FCSERVER_BIN={installation.fcserver_bin}" in log


def test_no_fcserver_skips_the_installer_entirely(installation: Installation) -> None:
    installation.with_installer_exiting(1)

    result = installation.run("--no-fcserver")

    assert result.returncode == 0, result.stdout + result.stderr
    assert not installation.installer_log.exists()
    assert "Skipping fcserver" in result.stdout
    assert installation.finished()


def test_setup_installs_the_packaged_configuration(installation: Installation) -> None:
    """The config an operator ends up with is the one the service reads."""
    installation.with_installer_exiting(0)

    installation.run("--yes")

    config = json.loads((installation.config_dir / "fclights.json").read_text())
    assert isinstance(config, dict)
    assert (installation.udev_dir / "99-fadecandy.rules").is_file()


def test_setup_refuses_on_non_pi_by_default(installation: Installation) -> None:
    """Running on a WSL box or a laptop is what caused the runaway crash loop."""
    installation.with_installer_exiting(0)

    result = installation.run("--yes", allow_non_pi=False)

    assert result.returncode != 0
    assert "Raspberry Pi" in result.stderr
    # The refusal must name the override, or the operator cannot recover from it.
    assert "--allow-non-pi" in result.stderr
    assert "FCLIGHTS_ALLOW_NON_PI" in result.stderr
    # Nothing was installed; the guard fired before any state was touched.
    assert not installation.finished()
    assert not installation.installer_log.exists()


def test_setup_allow_non_pi_flag_overrides_the_guard(installation: Installation) -> None:
    installation.with_installer_exiting(0)

    result = installation.run("--yes", "--allow-non-pi", allow_non_pi=False)

    assert result.returncode == 0, result.stdout + result.stderr
    assert installation.finished()
    assert "--allow-non-pi is set" in result.stderr


@pytest.mark.parametrize("unit", ["fcserver.service", "fclights.service"])
def test_systemd_units_have_start_rate_limit(unit: str) -> None:
    """A doomed unit must stop; an unrate-limited Restart=always is a crash loop."""
    body = (REPO / "deploy" / unit).read_text()
    assert "StartLimitIntervalSec=" in body
    assert "StartLimitBurst=" in body
