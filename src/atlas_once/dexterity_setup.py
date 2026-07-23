"""Reproducible Dexterity provisioning from GitHub.

Atlas consumes Dexterity (the Elixir ranked-context library) by running its mix
tasks. For a reproducible, no-manual-install setup, Atlas keeps a managed
Dexterity checkout cloned from GitHub under the state home
(``~/.atlas_once/code/dexterity``) and points ``defaults.runtime.dexterity_root``
at it, instead of assuming a hand-maintained local dev tree.

``ensure_dexterity`` is idempotent and degrades gracefully (never raises) when
``git`` or ``mix`` are unavailable, so ``atlas install`` stays robust on hosts
that only need part of the toolchain.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import AtlasPaths

DEXTERITY_REPO_URL = "https://github.com/nshkrdotcom/dexterity"
DEXTERITY_REF = "main"


@dataclass(frozen=True)
class DexteritySetupResult:
    root: Path
    status: str  # cloned | updated | present | skipped
    built: bool
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "root": str(self.root),
            "status": self.status,
            "built": self.built,
            "detail": self.detail,
        }


def managed_dexterity_root(paths: AtlasPaths) -> Path:
    """The Atlas-managed Dexterity checkout path (GitHub-sourced)."""
    return (paths.state_home / "code" / "dexterity").resolve()


def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
        check=False,
    )


def ensure_dexterity(
    paths: AtlasPaths,
    *,
    ref: str = DEXTERITY_REF,
    build: bool = True,
    url: str = DEXTERITY_REPO_URL,
) -> DexteritySetupResult:
    """Clone or update the managed Dexterity checkout from GitHub and build it.

    Idempotent. Returns a result describing what happened; never raises.
    """
    root = managed_dexterity_root(paths)

    if shutil.which("git") is None:
        return DexteritySetupResult(root, "skipped", False, "git not on PATH")

    if (root / ".git").is_dir():
        _run(["git", "fetch", "--quiet", "origin", ref], cwd=root)
        _run(["git", "checkout", "--quiet", ref], cwd=root)
        pull = _run(["git", "pull", "--quiet", "--ff-only", "origin", ref], cwd=root)
        status = "updated" if pull.returncode == 0 else "present"
        detail = "" if pull.returncode == 0 else pull.stderr.strip()[:300]
    else:
        root.parent.mkdir(parents=True, exist_ok=True)
        clone = _run(["git", "clone", "--quiet", "--branch", ref, url, str(root)])
        if clone.returncode != 0:
            return DexteritySetupResult(root, "skipped", False, clone.stderr.strip()[:300])
        status = "cloned"
        detail = ""

    built = False
    if build and shutil.which("mix") is not None:
        deps = _run(["mix", "deps.get"], cwd=root)
        compiled = _run(["mix", "compile"], cwd=root)
        built = deps.returncode == 0 and compiled.returncode == 0
        if not built and not detail:
            detail = (compiled.stderr or deps.stderr).strip()[:300]

    return DexteritySetupResult(root, status, built, detail)
