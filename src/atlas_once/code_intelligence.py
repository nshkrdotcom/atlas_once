from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import AtlasPaths
from .ranked_context import RankedRuntime, load_ranked_default_runtime
from .registry import resolve_project_ref
from .runtime import AtlasCliError, ExitCode
from .shadow_workspace import ensure_shadow_project_root


@dataclass(frozen=True)
class IntelligenceTarget:
    reference: str
    project_ref: str
    project_root: Path
    shadow_root: Path
    runtime: RankedRuntime


@dataclass(frozen=True)
class IntelligenceRun:
    command: list[str]
    cwd: Path
    returncode: int
    stdout: str
    stderr: str


def _is_path_like(reference: str) -> bool:
    return (
        reference in {".", ".."}
        or reference.startswith("/")
        or reference.startswith("~/")
        or "/" in reference
    )


def find_project_root(path: Path) -> Path:
    current = path.expanduser()
    if not current.is_absolute():
        current = Path.cwd() / current
    current = current.resolve()
    if current.is_file():
        current = current.parent

    for marker in ("mix.exs", ".git"):
        probe = current
        while True:
            if (probe / marker).exists():
                return probe
            if probe.parent == probe:
                break
            probe = probe.parent

    return current


def current_directory_is_mix_project() -> bool:
    return (find_project_root(Path.cwd()) / "mix.exs").is_file()


def resolve_intelligence_target(
    paths: AtlasPaths,
    reference: str | None = None,
    *,
    runtime: RankedRuntime | None = None,
) -> IntelligenceTarget:
    raw_reference = (reference or ".").strip() or "."
    resolved_runtime = runtime or load_ranked_default_runtime(paths)

    if _is_path_like(raw_reference) or Path(raw_reference).expanduser().exists():
        project_root = find_project_root(Path(raw_reference))
        project_ref = project_root.name
    else:
        record = resolve_project_ref(paths, raw_reference)
        project_root = Path(record.path).expanduser().resolve()
        project_ref = record.name

    if not (project_root / "mix.exs").is_file():
        raise AtlasCliError(
            ExitCode.VALIDATION,
            "not_mix_project",
            f"Atlas code intelligence requires an Elixir Mix project: {project_root}",
            {"project_root": str(project_root), "reference": raw_reference},
        )

    shadow_project_root = ensure_shadow_project_root(project_root, resolved_runtime.shadow_root)
    return IntelligenceTarget(
        reference=raw_reference,
        project_ref=project_ref,
        project_root=project_root,
        shadow_root=shadow_project_root,
        runtime=resolved_runtime,
    )


def target_dict(target: IntelligenceTarget) -> dict[str, str]:
    return {
        "reference": target.reference,
        "project_ref": target.project_ref,
        "repo_root": str(target.project_root),
        "shadow_root": str(target.shadow_root),
        "dexterity_root": str(target.runtime.dexterity_root),
        "dexter_bin": target.runtime.dexter_bin,
    }


def _run(
    command: list[str],
    *,
    cwd: Path,
) -> IntelligenceRun:
    result = subprocess.run(
        command,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
        env=os.environ.copy(),
    )
    return IntelligenceRun(
        command=command,
        cwd=cwd,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def _raise_on_failure(run: IntelligenceRun, *, kind: str, fallback: str) -> None:
    if run.returncode == 0:
        return
    message = run.stderr.strip() or run.stdout.strip() or fallback
    raise AtlasCliError(
        ExitCode.EXTERNAL,
        kind,
        message,
        {
            "command": run.command,
            "cwd": str(run.cwd),
            "returncode": run.returncode,
            "stderr": run.stderr,
            "stdout": run.stdout,
        },
    )


def _map_shadow_string(value: str, target: IntelligenceTarget) -> str:
    return value.replace(str(target.shadow_root), str(target.project_root))


def map_shadow_paths(value: Any, target: IntelligenceTarget) -> Any:
    if isinstance(value, str):
        return _map_shadow_string(value, target)
    if isinstance(value, list):
        return [map_shadow_paths(item, target) for item in value]
    if isinstance(value, dict):
        return {key: map_shadow_paths(item, target) for key, item in value.items()}
    return value


def repo_relative_arg(value: str, target: IntelligenceTarget) -> str:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        return value
    try:
        return candidate.resolve().relative_to(target.project_root).as_posix()
    except ValueError:
        return value


def shadow_path_arg(value: str, target: IntelligenceTarget) -> str:
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        try:
            rel_path = candidate.resolve().relative_to(target.project_root)
        except ValueError:
            return str(candidate.resolve())
    else:
        rel_path = Path(value)
    return str((target.shadow_root / rel_path).resolve())


def _normalize_query_positionals(
    action: str,
    positional: list[str],
    target: IntelligenceTarget,
) -> list[str]:
    if action in {"blast", "blast_count", "cochanges"} and positional:
        return [repo_relative_arg(positional[0], target), *positional[1:]]
    return positional


def _normalize_query_options(
    option_args: list[str] | None,
    target: IntelligenceTarget,
) -> list[str]:
    if not option_args:
        return []
    path_options = {"--active-file", "--mentioned-file", "--edited-file", "--changed-file"}
    normalized: list[str] = []
    index = 0
    while index < len(option_args):
        item = option_args[index]
        normalized.append(item)
        if item in path_options and index + 1 < len(option_args):
            normalized.append(repo_relative_arg(option_args[index + 1], target))
            index += 2
            continue
        index += 1
    return normalized


def ensure_intelligence_index(
    paths: AtlasPaths,
    reference: str | None = None,
    *,
    runtime: RankedRuntime | None = None,
) -> tuple[IntelligenceTarget, IntelligenceRun]:
    target = resolve_intelligence_target(paths, reference, runtime=runtime)
    command = [
        "mix",
        "dexterity.index",
        "--repo-root",
        str(target.shadow_root),
        "--dexter-bin",
        target.runtime.dexter_bin,
    ]
    run = _run(command, cwd=target.runtime.dexterity_root)
    _raise_on_failure(
        run,
        kind="dexterity_index_failed",
        fallback=f"dexterity.index failed for {target.project_root}",
    )
    return target, run


def run_dexterity_query(
    paths: AtlasPaths,
    action: str,
    positional: list[str],
    *,
    reference: str | None = None,
    option_args: list[str] | None = None,
) -> dict[str, Any]:
    target, index_run = ensure_intelligence_index(paths, reference)
    normalized_positionals = _normalize_query_positionals(action, positional, target)
    normalized_options = _normalize_query_options(option_args, target)
    command = [
        "mix",
        "dexterity.query",
        action,
        *normalized_positionals,
        "--repo-root",
        str(target.shadow_root),
        "--dexter-bin",
        target.runtime.dexter_bin,
        "--json",
        *normalized_options,
    ]
    run = _run(command, cwd=target.runtime.dexterity_root)
    _raise_on_failure(
        run,
        kind="dexterity_query_failed",
        fallback=f"dexterity.query {action} failed for {target.project_root}",
    )
    try:
        payload = json.loads(run.stdout)
    except json.JSONDecodeError as exc:
        raise AtlasCliError(
            ExitCode.EXTERNAL,
            "invalid_dexterity_json",
            f"dexterity.query {action} did not return JSON",
            {"stdout": run.stdout, "stderr": run.stderr},
        ) from exc

    mapped_payload = map_shadow_paths(payload, target)
    result = mapped_payload.get("result") if isinstance(mapped_payload, dict) else mapped_payload
    return {
        "project": target_dict(target),
        "tool": {
            "kind": "dexterity",
            "command": command,
            "cwd": str(target.runtime.dexterity_root),
            "returncode": run.returncode,
        },
        "index": {
            "command": index_run.command,
            "returncode": index_run.returncode,
            "stdout": _map_shadow_string(index_run.stdout, target),
            "stderr": _map_shadow_string(index_run.stderr, target),
        },
        "result": result,
        "raw": mapped_payload,
    }


def run_dexterity_map(
    paths: AtlasPaths,
    *,
    reference: str | None = None,
    option_args: list[str] | None = None,
) -> dict[str, Any]:
    target, index_run = ensure_intelligence_index(paths, reference)
    normalized_options = _normalize_query_options(option_args, target)
    command = [
        "mix",
        "dexterity.map",
        "--repo-root",
        str(target.shadow_root),
        *normalized_options,
    ]
    run = _run(command, cwd=target.runtime.dexterity_root)
    _raise_on_failure(
        run,
        kind="dexterity_map_failed",
        fallback=f"dexterity.map failed for {target.project_root}",
    )
    return {
        "project": target_dict(target),
        "tool": {
            "kind": "dexterity",
            "command": command,
            "cwd": str(target.runtime.dexterity_root),
            "returncode": run.returncode,
        },
        "index": {
            "command": index_run.command,
            "returncode": index_run.returncode,
            "stdout": _map_shadow_string(index_run.stdout, target),
            "stderr": _map_shadow_string(index_run.stderr, target),
        },
        "result": _map_shadow_string(run.stdout, target),
        "stderr": _map_shadow_string(run.stderr, target),
    }


def run_dexter_cli(
    paths: AtlasPaths,
    action: str,
    positional: list[str],
    *,
    reference: str | None = None,
    option_args: list[str] | None = None,
    ensure_index: bool = True,
) -> dict[str, Any]:
    if ensure_index:
        target, index_run = ensure_intelligence_index(paths, reference)
    else:
        target = resolve_intelligence_target(paths, reference)
        index_run = IntelligenceRun([], target.runtime.dexterity_root, 0, "", "")

    dexter_action = "references" if action == "refs" else action
    mapped_positionals = list(positional)
    if dexter_action == "reindex" and mapped_positionals:
        mapped_positionals = [shadow_path_arg(mapped_positionals[0], target)]
    if dexter_action == "init":
        mapped_positionals = [str(target.shadow_root), *mapped_positionals]

    command = [
        target.runtime.dexter_bin,
        dexter_action,
        *mapped_positionals,
        *(option_args or []),
    ]
    run = _run(command, cwd=target.shadow_root)
    _raise_on_failure(
        run,
        kind="dexter_cli_failed",
        fallback=f"dexter {dexter_action} failed for {target.project_root}",
    )
    return {
        "project": target_dict(target),
        "tool": {
            "kind": "dexter",
            "command": command,
            "cwd": str(target.shadow_root),
            "returncode": run.returncode,
        },
        "index": {
            "command": index_run.command,
            "returncode": index_run.returncode,
            "stdout": _map_shadow_string(index_run.stdout, target),
            "stderr": _map_shadow_string(index_run.stderr, target),
        },
        "stdout": _map_shadow_string(run.stdout, target),
        "stderr": _map_shadow_string(run.stderr, target),
    }
