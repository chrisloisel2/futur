from __future__ import annotations

import subprocess
from pathlib import Path


def git_head(repo_root: str | Path) -> str:
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def git_is_dirty(repo_root: str | Path) -> bool:
    out = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=True,
    )
    return bool(out.stdout.strip())


def verify_code_commit(claimed_commit: str, repo_root: str | Path) -> None:
    """Raise unless --code-commit is exactly the actual git HEAD and the tree is clean.

    A declared code_commit that doesn't match reality (stale, copy-pasted from a
    previous run, or hand-edited) makes every downstream seal a lie about what
    code actually produced the result. A dirty tree means HEAD alone doesn't
    describe what ran even if it matches.
    """
    actual = git_head(repo_root)
    if str(claimed_commit) != actual:
        raise ValueError(f"--code-commit {claimed_commit!r} does not match git HEAD {actual!r}")
    if git_is_dirty(repo_root):
        raise ValueError(
            f"working tree at {repo_root} has uncommitted changes -- "
            "code_commit alone does not describe what code actually ran"
        )
