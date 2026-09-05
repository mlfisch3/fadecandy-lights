"""Behaviour of deploy/install-fcserver.sh, driven through its command line.

The script exists because the documented fcserver install was wrong twice over:
it pointed at a repository that no longer exists, and it handed a 32-bit armhf
binary to a machine running the 64-bit image this project targets. So the two
things worth pinning down are that it plans the right work for the architecture
it is on, and that it never installs a binary it could not verify.

Everything here runs the real script against a fake host: a `dpkg` and an
`apt-get` on PATH whose answers the test fixes, so nothing depends on how the
machine running the tests happens to be configured. `unshare -r` supplies the
root the multiarch path insists on.
"""

from __future__ import annotations

import hashlib
import os
import shutil
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

# fcserver prints this from its usage banner, which is the script's smoke test.
BANNER = "fcserver-1.04-25-gf911031\nFadecandy Open Pixel Control server\n"


def a_working_fcserver(text: str = "the pinned build") -> str:
    return f'#!/bin/sh\nprintf %s "# {text}" >/dev/null\ncat <<EOT\n{BANNER}EOT\nexit 1\n'


def sha256(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


def fake_root() -> list[str]:
    """A command prefix that makes EUID 0 without granting real privilege."""
    if shutil.which("unshare") is None or subprocess.run(
        ["unshare", "-r", "true"], capture_output=True
    ).returncode:
        pytest.skip("unshare -r is unavailable, so the root-only paths cannot run")
    return ["unshare", "-r"]


class FakeHost:
    """A host whose dpkg/apt-get answers this test controls."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.target = tmp_path / "bin" / "fcserver"
        self.target.parent.mkdir(parents=True, exist_ok=True)
        self.foreign_architectures = ""
        self.installed_packages: tuple[str, ...] = ()
        self.apt_log = tmp_path / "apt.log"
        self.stub_bin = tmp_path / "stub-bin"
        self.stub_bin.mkdir()
        self._write(
            "dpkg",
            """
            case "$1" in
                --print-foreign-architectures)
                    [ -n "${STUB_FOREIGN}" ] && printf '%s\\n' "${STUB_FOREIGN}"
                    exit 0 ;;
                --print-architecture) printf 'arm64\\n'; exit 0 ;;
                --add-architecture) printf 'dpkg %s %s\\n' "$1" "$2" >>"${STUB_APT_LOG}"; exit 0 ;;
            esac
            exit 0
            """,
        )
        self._write(
            "dpkg-query",
            """
            for arg in "$@"; do
                case " ${STUB_INSTALLED} " in
                    *" ${arg} "*) printf 'installed'; exit 0 ;;
                esac
            done
            exit 1
            """,
        )
        self._write("apt-get", 'printf \'apt-get %s\\n\' "$*" >>"${STUB_APT_LOG}"\nexit 0\n')

    def _write(self, name: str, body: str) -> None:
        path = self.stub_bin / name
        path.write_text("#!/bin/sh\n" + body.strip() + "\n")
        path.chmod(0o755)

    def install(self, content: str) -> str:
        """Put a working fcserver at the target and return its digest."""
        self.target.write_text(content)
        self.target.chmod(0o755)
        return sha256(content)

    def run(
        self, *args: str, arch: str | None = None, as_root: bool = False, **env: str
    ) -> subprocess.CompletedProcess[str]:
        environ = dict(
            os.environ,
            PATH=f"{self.stub_bin}{os.pathsep}{os.environ['PATH']}",
            STUB_FOREIGN=self.foreign_architectures,
            STUB_INSTALLED=" ".join(self.installed_packages),
            STUB_APT_LOG=str(self.apt_log),
            FCSERVER_BIN=str(self.target),
            **env,
        )
        if arch is not None:
            environ["FCSERVER_ARCH"] = arch
        prefix = fake_root() if as_root else []
        return subprocess.run(
            [*prefix, str(SCRIPT), *args],
            capture_output=True,
            text=True,
            env=environ,
            stdin=subprocess.DEVNULL,
            timeout=120,
        )

    def plan(self, arch: str, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        return self.run("--dry-run", *args, arch=arch, **env)

    def apt_commands(self) -> list[str]:
        return self.apt_log.read_text().splitlines() if self.apt_log.exists() else []


@pytest.fixture
def host(tmp_path: Path) -> FakeHost:
    return FakeHost(tmp_path)


def test_script_is_executable_and_valid_bash() -> None:
    assert SCRIPT.stat().st_mode & stat.S_IXUSR
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
    subprocess.run(["bash", "-n", str(SETUP)], check=True)


@pytest.mark.parametrize("arch", SIXTY_FOUR_BIT)
def test_64_bit_plans_the_armhf_runtime(arch: str, host: FakeHost) -> None:
    """The whole reason this script exists: arm64 needs the 32-bit userland."""
    result = host.plan(arch)
    assert result.returncode == 0, result.stderr
    assert "dpkg --add-architecture armhf" in result.stdout
    assert "libc6:armhf" in result.stdout
    assert "libstdc++6:armhf" in result.stdout


@pytest.mark.parametrize("arch", THIRTY_TWO_BIT)
def test_32_bit_arm_plans_no_multiarch(arch: str, host: FakeHost) -> None:
    result = host.plan(arch)
    assert result.returncode == 0, result.stderr
    assert "add-architecture" not in result.stdout
    assert "libc6:armhf" not in result.stdout
    assert "apt-get" not in result.stdout


@pytest.mark.parametrize("arch", SIXTY_FOUR_BIT + THIRTY_TWO_BIT)
def test_every_supported_arch_installs_the_same_binary(arch: str, host: FakeHost) -> None:
    result = host.plan(arch)
    assert EXPECTED_SHA256 in result.stdout
    assert f"install it as {host.target}" in result.stdout


@pytest.mark.parametrize("arch", NOT_ARM)
def test_non_arm_stops_with_its_own_exit_code(arch: str, host: FakeHost) -> None:
    """No prebuilt fcserver exists off ARM, and guessing would be worse."""
    result = host.plan(arch)
    assert result.returncode == 3
    assert "unsupported architecture" in result.stderr
    assert "docs/fcserver.md" in result.stderr


def test_dry_run_changes_nothing(host: FakeHost) -> None:
    assert host.plan("arm64").returncode == 0
    assert not host.target.exists()
    assert host.apt_commands() == []


def test_a_fully_provisioned_64_bit_host_has_nothing_to_do(host: FakeHost) -> None:
    """Rerunning on a finished Pi must be a clean no-op: no plan, no root."""
    digest = host.install(a_working_fcserver())
    host.foreign_architectures = "armhf"
    host.installed_packages = ("libc6:armhf", "libstdc++6:armhf")

    result = host.run(arch="arm64", FCSERVER_SHA256=digest)

    assert result.returncode == 0, result.stderr
    assert "==> Plan" not in result.stdout
    assert "needs root" not in result.stderr
    assert "armhf is already a foreign architecture" in result.stdout
    assert "the armhf runtime is already installed" in result.stdout
    assert "fcserver is installed" in result.stdout
    assert host.apt_commands() == []


def test_the_armhf_runtime_is_installed_when_the_architecture_is_already_registered(
    host: FakeHost,
) -> None:
    """A run that added armhf and then died before apt-get update must recover.

    Tying the update to `dpkg --add-architecture` meant a host that had the
    foreign architecture but no armhf package lists could never install the
    runtime, and rerunning - the documented recovery - could not fix it.
    """
    digest = host.install(a_working_fcserver())
    host.foreign_architectures = "armhf"

    result = host.run("--yes", arch="arm64", as_root=True, FCSERVER_SHA256=digest)

    assert result.returncode == 0, result.stderr
    assert host.apt_commands() == [
        "apt-get update -qq",
        "apt-get install -y --no-install-recommends libc6:armhf libstdc++6:armhf",
    ]


def test_a_64_bit_host_without_the_architecture_registers_it_first(host: FakeHost) -> None:
    digest = host.install(a_working_fcserver())

    result = host.run("--yes", arch="arm64", as_root=True, FCSERVER_SHA256=digest)

    assert result.returncode == 0, result.stderr
    assert host.apt_commands() == [
        "dpkg --add-architecture armhf",
        "apt-get update -qq",
        "apt-get install -y --no-install-recommends libc6:armhf libstdc++6:armhf",
    ]


def test_an_installed_binary_is_checked_against_the_pin(host: FakeHost) -> None:
    digest = host.install(a_working_fcserver())

    result = host.run(arch="armhf", FCSERVER_SHA256=digest)

    assert result.returncode == 0, result.stderr
    assert "sha256 matches the pinned build" in result.stdout
    assert digest in result.stdout


def test_an_unpinned_binary_is_reported_loudly_and_left_alone(host: FakeHost) -> None:
    """Detection and repair are separate: warn, name the fix, touch nothing."""
    installed = a_working_fcserver("hand-built by the operator")
    installed_sha = host.install(installed)
    pinned_sha = sha256("some other fcserver")

    result = host.run(arch="armhf", FCSERVER_SHA256=pinned_sha)

    assert result.returncode == 0, result.stderr
    assert "warning:" in result.stderr
    assert "provenance has not been verified" in result.stderr
    assert installed_sha in result.stderr
    assert pinned_sha in result.stderr
    assert "--force" in result.stderr
    assert host.target.read_text() == installed
    assert host.target.stat().st_mode & 0o777 == 0o755


@pytest.mark.parametrize("how", ["flag", "environment"])
def test_force_refetches_verifies_and_replaces_the_binary(how: str, host: FakeHost) -> None:
    host.install(a_working_fcserver("the operator's own build"))
    replacement = host.tmp_path / "fcserver-pinned"
    replacement.write_text(a_working_fcserver("the pinned build"))

    args = ["--force", "--yes"] if how == "flag" else ["--yes"]
    env = {} if how == "flag" else {"FCSERVER_REINSTALL": "1"}
    result = host.run(
        *args,
        arch="armhf",
        FCSERVER_URL=replacement.as_uri(),
        FCSERVER_SHA256=sha256(replacement.read_bytes()),
        **env,
    )

    assert result.returncode == 0, result.stderr
    assert host.target.read_text() == replacement.read_text()
    assert host.target.stat().st_mode & 0o777 == 0o755


def test_force_still_refuses_a_binary_that_fails_the_digest(host: FakeHost) -> None:
    """--force replaces the binary; it does not relax the supply-chain check."""
    original = a_working_fcserver("the operator's own build")
    host.install(original)
    replacement = host.tmp_path / "fcserver-tampered"
    replacement.write_text(a_working_fcserver("something else entirely"))

    result = host.run(
        "--force",
        "--yes",
        arch="armhf",
        FCSERVER_URL=replacement.as_uri(),
        FCSERVER_SHA256="0" * 64,
    )

    assert result.returncode == 1
    assert "sha256 mismatch" in result.stderr
    assert host.target.read_text() == original


def test_without_a_terminal_it_declines_rather_than_assuming_consent(host: FakeHost) -> None:
    result = host.run(arch="armhf")
    assert result.returncode == 2
    assert not host.target.exists()


def test_the_plan_is_printed_before_consent_is_asked_for(host: FakeHost) -> None:
    """An operator declining should still have been told what was proposed."""
    result = host.run(arch="armhf")
    assert "==> Plan" in result.stdout
    assert EXPECTED_SHA256 in result.stdout


def test_a_bad_digest_is_fatal_and_installs_nothing(host: FakeHost) -> None:
    result = host.run(
        "--yes",
        arch="armhf",
        FCSERVER_SHA256="0" * 64,
        FCSERVER_URL=Path(__file__).resolve().as_uri(),
    )
    assert result.returncode == 1
    assert "sha256 mismatch" in result.stderr
    assert not host.target.exists()


def test_an_unreachable_source_is_fatal_and_installs_nothing(host: FakeHost) -> None:
    result = host.run(
        "--yes",
        arch="armhf",
        FCSERVER_URL=(host.tmp_path / "absent").as_uri(),
    )
    assert result.returncode == 1
    assert "download failed" in result.stderr
    assert not host.target.exists()


def test_unknown_options_are_rejected(host: FakeHost) -> None:
    result = host.run("--definitely-not-an-option")
    assert result.returncode == 1
    assert "unknown option" in result.stderr


def test_nothing_shipped_still_points_at_the_dead_upstream() -> None:
    """A documentation lint over the shipped prose, not evidence of behaviour.

    The defect this branch fixes was an install recipe pointing at a repository
    that had 404'd, so the contract being checked is the text an operator is
    told to follow: nothing in README.md, docs/ or deploy/ may send them back
    to github.com/scanlime/fadecandy except to record that it is gone.
    """
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
            if "github.com/scanlime/fadecandy" in line and "404" not in line:
                offenders.append(f"{name}:{number}: {line.strip()}")
    assert offenders == [], "\n".join(offenders)


def pinned_url(host: FakeHost) -> str:
    """The URL the script itself says it would fetch from."""
    plan = host.plan("armhf", FCSERVER_URL="")
    line = next(p for p in plan.stdout.splitlines() if "- fetch " in p)
    return line.split("- fetch ", 1)[1].strip()


@pytest.mark.network
def test_the_pinned_url_serves_the_binary_we_expect(host: FakeHost) -> None:
    """The dead-upstream defect was a URL nobody rechecked. Recheck this one."""
    url = pinned_url(host)
    assert url.startswith("https://github.com/PimentNoir/fadecandy/raw/")
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            payload = response.read()
    except (urllib.error.URLError, TimeoutError) as exc:  # pragma: no cover - offline
        pytest.skip(f"no network: {exc}")
    assert hashlib.sha256(payload).hexdigest() == EXPECTED_SHA256
    # 32-bit ARM ELF: \x7fELF, class 1 (32-bit), and e_machine 0x28 = EM_ARM.
    assert payload[:5] == b"\x7fELF\x01"
    assert payload[18:20] == b"\x28\x00"
