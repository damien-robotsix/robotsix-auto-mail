"""Unit tests for the stdlib-only ``autoupdate`` deploy CLI.

The module shells out to ``git``/``docker``/``getent``, appends to a log
file and a deployed-SHA marker, and uses ``fcntl`` for mutual exclusion.
All of that I/O is mocked here: ``subprocess.run`` and ``fcntl.flock`` are
patched at the module level, the hard-coded ``/tmp`` lock directory is
redirected into ``tmp_path``, and the file handler that ``main`` attaches
to the module logger is removed after every test so handlers never leak.

No real git/docker/getent process is spawned and nothing is written
outside ``tmp_path``.
"""

from __future__ import annotations

import subprocess
from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest

from robotsix_auto_mail.dev import autoupdate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cp(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
    args: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Build a ``CompletedProcess`` with string streams for mocking."""
    return subprocess.CompletedProcess(
        args=args or [],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


class FakeRun:
    """Map a command-prefix to a ``CompletedProcess``; record every call."""

    def __init__(
        self,
        rules: list[tuple[list[str], subprocess.CompletedProcess[str]]],
        default: subprocess.CompletedProcess[str] | None = None,
    ) -> None:
        self.rules = rules
        self.default = default
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(
        self, cmd: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((list(cmd), dict(kwargs)))
        for prefix, result in self.rules:
            if cmd[: len(prefix)] == prefix:
                return result
        if self.default is not None:
            return self.default
        raise AssertionError(f"unexpected command: {cmd}")

    def env_for(self, prefix: list[str]) -> dict[str, str] | None:
        """Return the ``env`` kwarg of the first call matching ``prefix``."""
        for cmd, kwargs in self.calls:
            if cmd[: len(prefix)] == prefix:
                return kwargs.get("env")
        raise AssertionError(f"no recorded call for {prefix}")

    def ran(self, prefix: list[str]) -> bool:
        """True when any recorded call started with ``prefix``."""
        return any(cmd[: len(prefix)] == prefix for cmd, _ in self.calls)


# ---------------------------------------------------------------------------
# _capture
# ---------------------------------------------------------------------------


def test_capture_passes_through_and_forwards_flags(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sentinel = _cp(stdout="hi")
    run = mock.MagicMock(return_value=sentinel)
    monkeypatch.setattr("robotsix_auto_mail.dev.autoupdate.subprocess.run", run)

    result = autoupdate._capture(["git", "status"], cwd=tmp_path)

    assert result is sentinel
    _, kwargs = run.call_args
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert kwargs["check"] is False
    assert kwargs["cwd"] == tmp_path


# ---------------------------------------------------------------------------
# _run_logged
# ---------------------------------------------------------------------------


def test_run_logged_appends_and_adds_trailing_newline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log_path = tmp_path / "run.log"
    monkeypatch.setattr(
        "robotsix_auto_mail.dev.autoupdate.subprocess.run",
        lambda *a, **k: _cp(0, stdout="out", stderr="err"),
    )

    rc = autoupdate._run_logged(["git", "x"], log_path=log_path)

    assert rc == 0
    assert log_path.read_text() == "outerr\n"


def test_run_logged_preserves_existing_trailing_newline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log_path = tmp_path / "run.log"
    monkeypatch.setattr(
        "robotsix_auto_mail.dev.autoupdate.subprocess.run",
        lambda *a, **k: _cp(3, stdout="line\n", stderr=""),
    )

    rc = autoupdate._run_logged(["git", "x"], log_path=log_path)

    assert rc == 3
    assert log_path.read_text() == "line\n"


def test_run_logged_writes_nothing_on_empty_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log_path = tmp_path / "run.log"
    monkeypatch.setattr(
        "robotsix_auto_mail.dev.autoupdate.subprocess.run",
        lambda *a, **k: _cp(0, stdout="", stderr=""),
    )

    rc = autoupdate._run_logged(["git", "x"], log_path=log_path)

    assert rc == 0
    assert not log_path.exists()


def test_run_logged_forwards_env_and_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log_path = tmp_path / "run.log"
    run = mock.MagicMock(return_value=_cp(0))
    monkeypatch.setattr("robotsix_auto_mail.dev.autoupdate.subprocess.run", run)
    env = {"DOCKER_GID": "999"}

    autoupdate._run_logged(["docker", "x"], log_path=log_path, cwd=tmp_path, env=env)

    _, kwargs = run.call_args
    assert kwargs["cwd"] == tmp_path
    assert kwargs["env"] == env
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert kwargs["check"] is False


# ---------------------------------------------------------------------------
# _docker_gid
# ---------------------------------------------------------------------------


def test_docker_gid_returns_third_field(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        autoupdate, "_capture", lambda *a, **k: _cp(0, stdout="docker:x:999\n")
    )
    assert autoupdate._docker_gid() == "999"


@pytest.mark.parametrize(
    "result",
    [
        _cp(2, stdout="docker:x:999\n"),  # non-zero return code
        _cp(0, stdout="docker:x\n"),  # fewer than three fields
        _cp(0, stdout="\n"),  # empty / malformed
    ],
)
def test_docker_gid_returns_empty_on_failure(
    monkeypatch: pytest.MonkeyPatch, result: subprocess.CompletedProcess[str]
) -> None:
    monkeypatch.setattr(autoupdate, "_capture", lambda *a, **k: result)
    assert autoupdate._docker_gid() == ""


# ---------------------------------------------------------------------------
# _deployed_marker
# ---------------------------------------------------------------------------


def test_deployed_marker_strips_autoupdate_suffix(tmp_path: Path) -> None:
    marker = autoupdate._deployed_marker(tmp_path, "auto-mail-autoupdate")
    assert marker == tmp_path / ".auto-mail-deployed-sha"


def test_deployed_marker_without_suffix_used_verbatim(tmp_path: Path) -> None:
    marker = autoupdate._deployed_marker(tmp_path, "foo")
    assert marker == tmp_path / ".foo-deployed-sha"


# ---------------------------------------------------------------------------
# _parse_args
# ---------------------------------------------------------------------------


def _required_argv() -> list[str]:
    return [
        "--repo",
        "/repo",
        "--state-dir",
        "/state",
        "--state-prefix",
        "auto-mail-autoupdate",
        "--service",
        "mail",
    ]


def test_parse_args_defaults() -> None:
    ns = autoupdate._parse_args(_required_argv())
    assert ns.ensure_branch == "main"
    assert ns.no_idle_check is False


def test_parse_args_no_idle_check_flag() -> None:
    ns = autoupdate._parse_args([*_required_argv(), "--no-idle-check"])
    assert ns.no_idle_check is True


@pytest.mark.parametrize(
    "drop", ["--repo", "--state-dir", "--state-prefix", "--service"]
)
def test_parse_args_required(drop: str) -> None:
    argv = _required_argv()
    idx = argv.index(drop)
    del argv[idx : idx + 2]
    with pytest.raises(SystemExit):
        autoupdate._parse_args(argv)


# ---------------------------------------------------------------------------
# _has_uncommitted_changes
# ---------------------------------------------------------------------------


def test_has_uncommitted_changes_true_and_args(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, Any] = {}

    def fake_capture(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        captured["cwd"] = kwargs.get("cwd")
        return _cp(0, stdout=" M file.py\n")

    monkeypatch.setattr(autoupdate, "_capture", fake_capture)

    assert autoupdate._has_uncommitted_changes(tmp_path) is True
    assert captured["cmd"] == [
        "git",
        "status",
        "--porcelain",
        "--untracked-files=no",
    ]
    assert captured["cwd"] == tmp_path


def test_has_uncommitted_changes_false_when_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(autoupdate, "_capture", lambda *a, **k: _cp(0, stdout=""))
    assert autoupdate._has_uncommitted_changes(tmp_path) is False


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[Any, None, None]:
    """Wire up directories, redirect the ``/tmp`` lock, clean up handlers."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    lock_dir = tmp_path / "lock"
    lock_dir.mkdir()

    def fake_path(*parts: Any) -> Path:
        built = Path(*parts)
        if str(built) == "/tmp":  # noqa: S108  # match the module's lock dir
            return lock_dir
        return built

    monkeypatch.setattr("robotsix_auto_mail.dev.autoupdate.Path", fake_path)
    # flock succeeds by default; contention tests override this.
    monkeypatch.setattr(
        "robotsix_auto_mail.dev.autoupdate.fcntl.flock", lambda *a, **k: None
    )

    yield SimpleNamespace(state_dir=state_dir, repo=repo, prefix="auto-mail-autoupdate")

    for handler in list(autoupdate.logger.handlers):
        autoupdate.logger.removeHandler(handler)
        handler.close()


def _argv(env: Any, *, branch: str = "main", repo: Path | None = None) -> list[str]:
    return [
        "--repo",
        str(repo if repo is not None else env.repo),
        "--state-dir",
        str(env.state_dir),
        "--state-prefix",
        env.prefix,
        "--service",
        "mail",
        "--ensure-branch",
        branch,
    ]


def _happy_rules(
    remote: str = "abc123def456",
) -> list[tuple[list[str], subprocess.CompletedProcess[str]]]:
    return [
        (["git", "symbolic-ref"], _cp(0, stdout="main\n")),
        (["git", "status"], _cp(0, stdout="")),
        (["git", "fetch"], _cp(0)),
        (["git", "rev-parse"], _cp(0, stdout=f"{remote}\n")),
        (["git", "--no-pager", "log"], _cp(0, stdout="abc123 a commit\n")),
        (["git", "merge"], _cp(0)),
        (["getent", "group", "docker"], _cp(0, stdout="docker:x:999\n")),
        (["docker", "compose", "build"], _cp(0)),
        (["docker", "compose", "up"], _cp(0)),
    ]


def _install(monkeypatch: pytest.MonkeyPatch, fake: FakeRun) -> None:
    monkeypatch.setattr("robotsix_auto_mail.dev.autoupdate.subprocess.run", fake)


def test_main_lock_contention_returns_zero(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def raise_oserror(*a: Any, **k: Any) -> None:
        raise OSError("locked")

    monkeypatch.setattr("robotsix_auto_mail.dev.autoupdate.fcntl.flock", raise_oserror)
    fake = FakeRun([], default=_cp(0))
    _install(monkeypatch, fake)

    assert autoupdate.main(_argv(env)) == 0
    assert fake.calls == []


def test_main_missing_repo_dir_returns_one(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeRun([], default=_cp(0))
    _install(monkeypatch, fake)
    missing = env.repo.parent / "nope"

    assert autoupdate.main(_argv(env, repo=missing)) == 1


def test_main_branch_switch_with_uncommitted_changes_returns_zero(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeRun(
        [
            (["git", "symbolic-ref"], _cp(0, stdout="feature\n")),
            (["git", "status"], _cp(0, stdout=" M wip.py\n")),
        ]
    )
    _install(monkeypatch, fake)

    assert autoupdate.main(_argv(env, branch="main")) == 0
    assert not fake.ran(["git", "checkout"])


def test_main_checkout_failure_returns_one(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeRun(
        [
            (["git", "symbolic-ref"], _cp(0, stdout="feature\n")),
            (["git", "status"], _cp(0, stdout="")),
            (["git", "checkout"], _cp(1, stderr="boom")),
        ]
    )
    _install(monkeypatch, fake)

    assert autoupdate.main(_argv(env, branch="main")) == 1
    assert fake.ran(["git", "checkout"])


def test_main_uncommitted_on_target_branch_returns_zero(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeRun(
        [
            (["git", "symbolic-ref"], _cp(0, stdout="main\n")),
            (["git", "status"], _cp(0, stdout=" M wip.py\n")),
        ]
    )
    _install(monkeypatch, fake)

    assert autoupdate.main(_argv(env, branch="main")) == 0
    assert not fake.ran(["git", "fetch"])


def test_main_fetch_failure_returns_one(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rules = _happy_rules()
    rules = [r for r in rules if r[0] != ["git", "fetch"]]
    rules.append((["git", "fetch"], _cp(1, stderr="network")))
    fake = FakeRun(rules)
    _install(monkeypatch, fake)

    assert autoupdate.main(_argv(env)) == 1


def test_main_already_deployed_returns_zero(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote = "abc123def456"
    marker = autoupdate._deployed_marker(env.state_dir, env.prefix)
    marker.write_text(f"{remote}\n")
    fake = FakeRun(_happy_rules(remote))
    _install(monkeypatch, fake)

    assert autoupdate.main(_argv(env)) == 0
    assert not fake.ran(["git", "merge"])


def test_main_ff_merge_failure_returns_one(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rules = [r for r in _happy_rules() if r[0] != ["git", "merge"]]
    rules.append((["git", "merge"], _cp(1, stderr="diverged")))
    fake = FakeRun(rules)
    _install(monkeypatch, fake)

    assert autoupdate.main(_argv(env)) == 1


def test_main_docker_build_failure_returns_one(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rules = [r for r in _happy_rules() if r[0] != ["docker", "compose", "build"]]
    rules.append((["docker", "compose", "build"], _cp(1, stderr="build fail")))
    fake = FakeRun(rules)
    _install(monkeypatch, fake)

    assert autoupdate.main(_argv(env)) == 1


def test_main_docker_up_failure_returns_one(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rules = [r for r in _happy_rules() if r[0] != ["docker", "compose", "up"]]
    rules.append((["docker", "compose", "up"], _cp(1, stderr="up fail")))
    fake = FakeRun(rules)
    _install(monkeypatch, fake)

    assert autoupdate.main(_argv(env)) == 1


def test_main_full_happy_path_writes_marker_and_sets_gid(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote = "abc123def456"
    fake = FakeRun(_happy_rules(remote))
    _install(monkeypatch, fake)

    assert autoupdate.main(_argv(env)) == 0

    marker = autoupdate._deployed_marker(env.state_dir, env.prefix)
    assert marker.read_text() == f"{remote}\n"
    build_env = fake.env_for(["docker", "compose", "build"])
    up_env = fake.env_for(["docker", "compose", "up"])
    assert build_env is not None
    assert build_env["DOCKER_GID"] == "999"
    assert up_env is not None
    assert up_env["DOCKER_GID"] == "999"
