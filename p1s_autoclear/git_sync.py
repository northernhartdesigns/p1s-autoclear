"""
Find the project Git repo and run fetch/pull for the Settings tab.
Requires Git installed and on PATH; uses the same credentials as your CLI.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def find_repo_root(start: Path | None = None) -> Path | None:
    """Walk upward from ``start`` (default: this file's directory) for a `.git` directory."""
    p = (start or Path(__file__).resolve()).resolve()
    if p.is_file():
        p = p.parent
    for _ in range(12):
        git = p / ".git"
        if git.is_dir() or git.is_file():
            return p
        if p == p.parent:
            break
        p = p.parent
    return None


def run_git(cwd: Path, args: list[str], timeout: int = 180) -> tuple[int, str, str]:
    r = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()


def current_branch(repo: Path) -> str | None:
    code, out, _ = run_git(repo, ["branch", "--show-current"], timeout=30)
    return out if code == 0 and out else None


def update_from_github(repo: Path) -> tuple[bool, str]:
    """
    ``git fetch origin`` then ``git pull --ff-only`` (uses upstream for current branch).
    Returns (success, combined message for display).
    """
    parts: list[str] = []
    code, out, err = run_git(repo, ["fetch", "origin"])
    if out:
        parts.append(out)
    if err:
        parts.append(err)
    if code != 0:
        return False, "\n".join(parts) if parts else f"git fetch failed (exit code {code})."

    code2, out2, err2 = run_git(repo, ["pull", "--ff-only"])
    if out2:
        parts.append(out2)
    if err2:
        parts.append(err2)
    if code2 != 0:
        return False, "\n".join(parts) if parts else (
            "git pull --ff-only failed. You may have local commits that aren't fast-forwardable, "
            "or conflicts — resolve in Git, then try again."
        )
    msg = "\n".join(parts).strip()
    return True, msg or "Already up to date."
