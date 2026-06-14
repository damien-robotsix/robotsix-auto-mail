"""Shared autoupdate CLI for robotsix deploy trees.

Pull the latest ``origin/<branch>`` and rebuild/restart the docker
compose stack, but only when origin has new commits. Designed to run
from cron and to be driven by a thin per-repo bash wrapper.

The module is stdlib-only so it can be vendored into multiple repos
without dragging in any heavy transitive dependencies. It mirrors the
behaviour of the original standalone ``auto-mail-autoupdate.sh``:

* flock-based mutual exclusion (no concurrent runs),
* pin the deploy tree to a branch (auto-switch only when the tree is
  clean of uncommitted tracked changes),
* bail on uncommitted tracked changes,
* ``git fetch`` + SHA comparison, skipping when already deployed,
* ``git merge --ff-only`` then ``docker compose build`` / ``up -d``,
* record the deployed SHA and log every step with timestamps.

Runtime files (log, deployed-SHA marker, lock) are written outside the
working tree so they never dirty it.
"""

from __future__ import annotations

import argparse
import fcntl
import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger("robotsix_autoupdate")


def _capture(
    cmd: list[str], *, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run ``cmd`` capturing output, without touching the log file."""
    return subprocess.run(  # noqa: S603
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _run_logged(
    cmd: list[str],
    *,
    log_path: Path,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> int:
    """Run ``cmd``, append its combined output to ``log_path``, return rc."""
    proc = subprocess.run(  # noqa: S603
        cmd,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    output = proc.stdout + proc.stderr
    if output:
        with log_path.open("a") as handle:
            handle.write(output)
            if not output.endswith("\n"):
                handle.write("\n")
    return proc.returncode


def _docker_gid() -> str:
    """Return the numeric gid of the ``docker`` group, or empty string."""
    res = _capture(["getent", "group", "docker"])
    fields = res.stdout.strip().split(":")
    if res.returncode != 0 or len(fields) < 3:
        return ""
    return fields[2]


def _deployed_marker(state_dir: Path, state_prefix: str) -> Path:
    """Path of the deployed-SHA marker derived from ``state_prefix``.

    The marker drops the ``-autoupdate`` suffix from the prefix so that a
    ``state-prefix`` of ``auto-mail-autoupdate`` yields
    ``.auto-mail-deployed-sha``.
    """
    base = state_prefix.removesuffix("-autoupdate")
    return state_dir / f".{base}-deployed-sha"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="robotsix-autoupdate",
        description="Rebuild a docker compose deploy tree when origin has new commits.",
    )
    parser.add_argument("--repo", required=True, help="path to the repo working tree")
    parser.add_argument(
        "--state-dir",
        required=True,
        help="directory for runtime files (log, deployed-SHA marker)",
    )
    parser.add_argument(
        "--state-prefix",
        required=True,
        help='prefix for state files (e.g. "auto-mail-autoupdate")',
    )
    parser.add_argument(
        "--service",
        required=True,
        help="docker compose service name for logging",
    )
    parser.add_argument(
        "--ensure-branch",
        default="main",
        help="branch to pin the deploy tree to (default: main)",
    )
    parser.add_argument(
        "--no-idle-check",
        action="store_true",
        help="skip idle/busy polling before rebuild",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    repo = Path(args.repo)
    state_dir = Path(args.state_dir)
    prefix: str = args.state_prefix
    service: str = args.service
    branch: str = args.ensure_branch

    log_path = state_dir / f"{prefix}.log"
    deployed_file = _deployed_marker(state_dir, prefix)
    lock_path = Path("/tmp") / f"{prefix}.lock"  # noqa: S108  # nosec B108  # intentional: cross-process flock in a well-known location

    handler = logging.FileHandler(log_path)
    handler.setFormatter(
        logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    )
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    # Skip if a previous run is still going.
    lock_handle = lock_path.open("w")
    try:
        fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        logger.info("another run in progress — skipping")
        return 0

    if not repo.is_dir():
        logger.error("ERROR: cannot cd to %s", repo)
        return 1

    # Pin the deploy tree to the target branch. This ff-merges origin/<branch>
    # into the CURRENTLY checked-out branch, so a tree parked on a diverged
    # feature branch would abort every merge. Switch back first, but refuse to
    # auto-switch when there are uncommitted tracked changes (don't clobber WIP).
    head = _capture(["git", "symbolic-ref", "--short", "-q", "HEAD"], cwd=repo)
    current_branch = head.stdout.strip() if head.returncode == 0 else "(detached)"
    if current_branch != branch:
        if _has_uncommitted_changes(repo):
            logger.info(
                "on '%s' with uncommitted changes — not auto-switching to %s; skip",
                current_branch,
                branch,
            )
            return 0
        logger.info(
            "deploy tree on '%s', not %s — switching to %s",
            current_branch,
            branch,
            branch,
        )
        if _run_logged(["git", "checkout", branch], cwd=repo, log_path=log_path) != 0:
            logger.error("ERROR: failed to checkout %s — skipping", branch)
            return 1

    # Never clobber manual WIP — bail on uncommitted TRACKED changes.
    if _has_uncommitted_changes(repo):
        logger.info("working tree has uncommitted changes — skipping pull/rebuild")
        return 0

    if (
        _run_logged(["git", "fetch", "origin", branch], cwd=repo, log_path=log_path)
        != 0
    ):
        logger.error("ERROR: git fetch failed (SSH auth / network?) — skipping")
        return 1

    remote = _capture(["git", "rev-parse", f"origin/{branch}"], cwd=repo).stdout.strip()
    deployed = deployed_file.read_text().strip() if deployed_file.exists() else ""
    if deployed == remote:
        logger.info("stack already on %s — nothing to do", remote[:7])
        return 0

    dep_short = deployed[:7] or "(first run)"
    logger.info("new commits on origin/%s (%s -> %s):", branch, dep_short, remote[:7])
    history = _capture(
        ["git", "--no-pager", "log", "--oneline", f"{deployed or 'HEAD'}..{remote}"],
        cwd=repo,
    )
    if history.stdout:
        with log_path.open("a") as handle:
            for line in history.stdout.splitlines():
                handle.write(f"    {line}\n")

    if (
        _run_logged(
            ["git", "merge", "--ff-only", f"origin/{branch}"],
            cwd=repo,
            log_path=log_path,
        )
        != 0
    ):
        logger.error(
            "ERROR: ff-only merge failed (local diverged from origin/%s) — skipping",
            branch,
        )
        return 1

    if not args.no_idle_check:
        # No idle-check provider is configured for this deploy tree; proceed.
        logger.info("no idle-check provider configured — proceeding")

    env = os.environ.copy()
    env["DOCKER_GID"] = _docker_gid()

    logger.info("building images for %s %s", service, remote[:7])
    if (
        _run_logged(
            ["docker", "compose", "build"], cwd=repo, log_path=log_path, env=env
        )
        != 0
    ):
        logger.error("ERROR: docker compose build failed")
        return 1

    if (
        _run_logged(
            ["docker", "compose", "up", "-d"], cwd=repo, log_path=log_path, env=env
        )
        == 0
    ):
        deployed_file.write_text(f"{remote}\n")
        logger.info("rebuild + restart OK — stack now on %s", remote[:7])
    else:
        logger.error("ERROR: docker compose up failed")
        return 1

    return 0


def _has_uncommitted_changes(repo: Path) -> bool:
    """True when the tree has uncommitted TRACKED changes."""
    res = _capture(["git", "status", "--porcelain", "--untracked-files=no"], cwd=repo)
    return bool(res.stdout.strip())


if __name__ == "__main__":
    raise SystemExit(main())
