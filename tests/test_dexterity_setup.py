"""Tests for reproducible GitHub-sourced Dexterity provisioning."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from atlas_once import dexterity_setup
from atlas_once.config import get_paths


def _fake_which(present: set[str]):
    def which(name: str) -> str | None:
        return f"/usr/bin/{name}" if name in present else None

    return which


def test_skips_when_git_missing(atlas_env: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(dexterity_setup.shutil, "which", _fake_which(set()))
    result = dexterity_setup.ensure_dexterity(get_paths())
    assert result.status == "skipped"
    assert result.built is False


def test_clones_and_builds_when_absent(atlas_env: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(dexterity_setup.shutil, "which", _fake_which({"git", "mix"}))
    calls: list[list[str]] = []

    def fake_run(cmd, cwd=None, capture_output=True, text=True, check=False):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(dexterity_setup.subprocess, "run", fake_run)

    paths = get_paths()
    result = dexterity_setup.ensure_dexterity(paths)

    assert result.status == "cloned"
    assert result.built is True
    assert result.root == dexterity_setup.managed_dexterity_root(paths)
    # cloned from the GitHub URL, then built via mix
    assert any(c[:2] == ["git", "clone"] and dexterity_setup.DEXTERITY_REPO_URL in c for c in calls)
    assert ["mix", "deps.get"] in calls
    assert ["mix", "compile"] in calls


def test_updates_existing_checkout(atlas_env: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(dexterity_setup.shutil, "which", _fake_which({"git", "mix"}))
    paths = get_paths()
    root = dexterity_setup.managed_dexterity_root(paths)
    (root / ".git").mkdir(parents=True)  # pretend a checkout already exists

    calls: list[list[str]] = []

    def fake_run(cmd, cwd=None, capture_output=True, text=True, check=False):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(dexterity_setup.subprocess, "run", fake_run)
    result = dexterity_setup.ensure_dexterity(paths)

    assert result.status == "updated"
    assert any(c[:2] == ["git", "pull"] for c in calls)
    assert not any(c[:2] == ["git", "clone"] for c in calls)


def test_clone_failure_is_non_fatal(atlas_env: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(dexterity_setup.shutil, "which", _fake_which({"git"}))

    def fake_run(cmd, cwd=None, capture_output=True, text=True, check=False):
        return subprocess.CompletedProcess(cmd, 1, "", "fatal: could not read")

    monkeypatch.setattr(dexterity_setup.subprocess, "run", fake_run)
    result = dexterity_setup.ensure_dexterity(get_paths())
    assert result.status == "skipped"
    assert result.built is False
