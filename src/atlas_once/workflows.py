from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .config import AtlasPaths, ensure_state
from .fleet import RepoModel, bootstrap_fleet_config, load_repos, repo_model_dict, select_repos
from .git_health import health_by_path
from .runtime import AtlasCliError, ExitCode


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def _run_id() -> str:
    return time.strftime("run_%Y%m%d_%H%M%S", time.localtime()) + f"_{os.getpid()}"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _append_event(run_root: Path, event: str, payload: dict[str, Any]) -> None:
    with (run_root / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps({"timestamp": _now_iso(), "event": event, **payload}, sort_keys=True) + "\n"
        )


def default_prompt_runner_config() -> dict[str, Any]:
    return {
        "sdk": {
            "path": "~/p/g/n/prompt_runner_sdk",
            "binary": "prompt_runner",
            "entrypoint": "mix",
            "use_local_path": True,
            "command_timeout_seconds": 1800,
        },
        "defaults": {
            "provider": "simulated",
            "model": "simulated-demo",
            "serial_default": True,
            "concurrency": 1,
            "dry_run_default": False,
            "no_commit": False,
            "preflight_default": True,
        },
        "presets": {
            "foo-prompt": {
                "id": "foo-prompt",
                "prompt_ref": "foo-prompt",
                "packet": ".",
                "provider": "simulated",
                "targets": ["@all"],
                "execution": {"mode": "serial", "concurrency": 1, "timeout_seconds": 1200},
            }
        },
    }


def bootstrap_prompt_runner_config(paths: AtlasPaths) -> bool:
    ensure_state(paths)
    bootstrap_fleet_config(paths)
    if paths.prompt_runner_config_path.exists():
        return False
    paths.prompt_runner_config_path.write_text(
        json.dumps(default_prompt_runner_config(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return True


def load_prompt_runner_config(paths: AtlasPaths) -> dict[str, Any]:
    ensure_state(paths)
    if not paths.prompt_runner_config_path.is_file():
        bootstrap_prompt_runner_config(paths)
    payload = json.loads(paths.prompt_runner_config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return default_prompt_runner_config()
    merged = default_prompt_runner_config()
    _deep_update(merged, payload)
    return merged


def _deep_update(base: dict[str, Any], update: dict[str, Any]) -> None:
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_update(base[key], value)
        else:
            base[key] = value


def list_presets(paths: AtlasPaths) -> dict[str, Any]:
    config = load_prompt_runner_config(paths)
    presets = config.get("presets", {})
    return {"presets": sorted(presets.values(), key=lambda item: str(item.get("id", "")))}


def show_preset(paths: AtlasPaths, preset_id: str) -> dict[str, Any]:
    preset = _preset(paths, preset_id)
    return {"preset": preset}


def upsert_preset(paths: AtlasPaths, preset_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    config = load_prompt_runner_config(paths)
    payload["id"] = payload.get("id") or preset_id
    config.setdefault("presets", {})[preset_id] = payload
    paths.prompt_runner_config_path.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {"preset": payload}


def _preset(paths: AtlasPaths, preset_id: str) -> dict[str, Any]:
    presets = load_prompt_runner_config(paths).get("presets", {})
    preset = presets.get(preset_id) if isinstance(presets, dict) else None
    if not isinstance(preset, dict):
        raise AtlasCliError(
            ExitCode.NOT_FOUND,
            "preset_not_found",
            f"Workflow preset not found: {preset_id}",
            {"preset": preset_id},
        )
    return dict(preset)


def plan_or_run_direct(
    paths: AtlasPaths,
    *,
    prompt_ref: str,
    provider: str,
    packet_root: str,
    selectors: list[str] | None,
    manifest: str | None = None,
    manifest_format: str = "json",
    model: str | None = None,
    serial: bool = True,
    concurrency: int = 1,
    timeout_seconds: int | None = None,
    dry_run: bool = False,
    no_commit: bool = False,
    preflight: bool | None = None,
    preflight_only: bool = False,
) -> dict[str, Any]:
    config = load_prompt_runner_config(paths)
    repos = load_repos(paths, manifest=manifest, manifest_format=manifest_format)
    selection = select_repos(repos, selectors, health_by_path=health_by_path(paths))
    if selection.errors:
        raise AtlasCliError(
            ExitCode.NOT_FOUND,
            "invalid_selector",
            "One or more target selectors did not resolve.",
            {"errors": selection.errors},
        )
    packet = Path(packet_root).expanduser().resolve()
    if not packet.exists():
        raise AtlasCliError(
            ExitCode.NOT_FOUND,
            "packet_root_not_found",
            f"Packet root not found: {packet}",
            {"packet_root": str(packet)},
        )
    run_id = _run_id()
    run_root = paths.workflow_runs_root / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    timeout_seconds = timeout_seconds or int(
        config.get("sdk", {}).get("command_timeout_seconds") or 1800
    )
    if preflight is None:
        preflight = bool(config.get("defaults", {}).get("preflight_default", True))
    mode = "serial" if serial else "parallel"
    run = _initial_run(
        run_id,
        prompt_ref,
        provider,
        model,
        packet,
        selection.repos,
        mode=mode,
        concurrency=concurrency,
        timeout_seconds=timeout_seconds,
        dry_run=dry_run,
        no_commit=no_commit,
        preflight=preflight,
        preflight_only=preflight_only,
    )
    _atomic_write_json(run_root / "run.json", run)
    _append_event(run_root, "run.planned", {"target_count": len(selection.repos)})
    if dry_run:
        run["status"] = "planned"
        _atomic_write_json(run_root / "run.json", run)
        return run
    if preflight_only:
        return _execute_preflight_only(config, run_root, run)
    if not serial:
        raise AtlasCliError(
            ExitCode.VALIDATION,
            "parallel_not_supported",
            "Parallel prompt-run-sdk execution is not implemented yet; use --serial.",
        )
    return _execute_serial(paths, config, run_root, run)


def run_preset(
    paths: AtlasPaths,
    preset_id: str,
    *,
    selectors: list[str] | None = None,
    provider: str | None = None,
    model: str | None = None,
    dry_run: bool = False,
    preflight_only: bool = False,
    skip_preflight: bool = False,
) -> dict[str, Any]:
    preset = _preset(paths, preset_id)
    execution = preset.get("execution", {}) if isinstance(preset.get("execution"), dict) else {}
    return plan_or_run_direct(
        paths,
        prompt_ref=str(preset.get("prompt_ref") or preset_id),
        provider=provider or str(preset.get("provider") or "simulated"),
        packet_root=str(preset.get("packet") or "."),
        selectors=selectors or [str(item) for item in preset.get("targets", ["@all"])],
        model=model or preset.get("model"),
        serial=str(execution.get("mode", "serial")) == "serial",
        concurrency=int(execution.get("concurrency") or 1),
        timeout_seconds=int(execution.get("timeout_seconds") or 1200),
        dry_run=dry_run,
        preflight=not skip_preflight,
        preflight_only=preflight_only,
        no_commit=bool(preset.get("no_commit", False)),
    )


def workflow_status(paths: AtlasPaths, run_id: str) -> dict[str, Any]:
    run_path = paths.workflow_runs_root / run_id / "run.json"
    if not run_path.is_file():
        raise AtlasCliError(
            ExitCode.NOT_FOUND,
            "run_not_found",
            f"Workflow run not found: {run_id}",
            {"run_id": run_id},
        )
    payload = json.loads(run_path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def workflow_list(paths: AtlasPaths) -> dict[str, Any]:
    runs: list[dict[str, Any]] = []
    if paths.workflow_runs_root.is_dir():
        for run_path in sorted(paths.workflow_runs_root.glob("*/run.json"), reverse=True):
            try:
                run = json.loads(run_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            runs.append(
                {
                    "run_id": run.get("run_id"),
                    "status": run.get("status"),
                    "prompt_ref": run.get("prompt_ref"),
                    "provider": run.get("provider"),
                    "started_at": run.get("started_at"),
                    "completed_at": run.get("completed_at"),
                    "summary": run.get("summary"),
                }
            )
    return {"runs": runs}


def _initial_run(
    run_id: str,
    prompt_ref: str,
    provider: str,
    model: str | None,
    packet: Path,
    targets: list[RepoModel],
    *,
    mode: str,
    concurrency: int,
    timeout_seconds: int,
    dry_run: bool,
    no_commit: bool,
    preflight: bool,
    preflight_only: bool,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "status": "planned",
        "provider": provider,
        "model": model,
        "prompt_ref": prompt_ref,
        "packet_root": str(packet),
        "target_repos": [repo.ref for repo in targets],
        "targets": [
            {
                **repo_model_dict(repo),
                "repo_ref": repo.ref,
                "status": "pending",
                "stdout_path": None,
                "stderr_path": None,
                "exit_code": None,
                "duration_ms": 0,
            }
            for repo in targets
        ],
        "execution": {
            "mode": mode,
            "concurrency": concurrency,
            "timeout_seconds": timeout_seconds,
            "dry_run": dry_run,
            "no_commit": no_commit,
            "preflight": preflight,
            "preflight_only": preflight_only,
        },
        "preflight": None,
        "started_at": _now_iso(),
        "completed_at": None,
        "summary": {"succeeded": 0, "failed": 0, "skipped": 0, "pending": len(targets)},
    }


def _execute_serial(
    paths: AtlasPaths,
    config: dict[str, Any],
    run_root: Path,
    run: dict[str, Any],
) -> dict[str, Any]:
    del paths
    run["status"] = "running"
    _atomic_write_json(run_root / "run.json", run)
    command_base, cwd = _sdk_command(config)
    if run["execution"].get("preflight", True):
        preflight = _run_preflight(command_base, cwd, run_root, run)
        if preflight["status"] != "passed":
            _fail_preflight(run_root, run, preflight)
    for target in run["targets"]:
        _append_event(run_root, "run.target.started", {"repo_ref": target["repo_ref"]})
        target_root = run_root / "targets" / _safe_name(str(target["repo_ref"]))
        target_root.mkdir(parents=True, exist_ok=True)
        stdout_path = target_root / "stdout.log"
        stderr_path = target_root / "stderr.log"
        command = [
            *command_base,
            "run",
            run["packet_root"],
            run["prompt_ref"],
            "--provider",
            run["provider"],
        ]
        if run.get("model"):
            command.extend(["--model", str(run["model"])])
        if run["execution"].get("no_commit"):
            command.append("--no-commit")
        started = time.monotonic()
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=int(run["execution"]["timeout_seconds"]),
            check=False,
        )
        stdout_path.write_text(completed.stdout, encoding="utf-8")
        stderr_path.write_text(completed.stderr, encoding="utf-8")
        target["stdout_path"] = str(stdout_path)
        target["stderr_path"] = str(stderr_path)
        target["exit_code"] = completed.returncode
        target["duration_ms"] = int((time.monotonic() - started) * 1000)
        target["status"] = "succeeded" if completed.returncode == 0 else "failed"
        _append_event(
            run_root,
            f"run.target.{target['status']}",
            {"repo_ref": target["repo_ref"], "exit_code": completed.returncode},
        )
        _summarize(run)
        _atomic_write_json(run_root / "run.json", run)
    run["completed_at"] = _now_iso()
    _summarize(run)
    run["status"] = "done" if run["summary"]["failed"] == 0 else "partial_failure"
    _append_event(run_root, "run.finished", {"status": run["status"], "summary": run["summary"]})
    _atomic_write_json(run_root / "run.json", run)
    return run


def _execute_preflight_only(
    config: dict[str, Any],
    run_root: Path,
    run: dict[str, Any],
) -> dict[str, Any]:
    command_base, cwd = _sdk_command(config)
    preflight = _run_preflight(command_base, cwd, run_root, run)
    run["completed_at"] = _now_iso()
    run["status"] = "preflight_passed" if preflight["status"] == "passed" else "preflight_failed"
    if preflight["status"] == "failed":
        for target in run["targets"]:
            target["status"] = "skipped"
    _summarize(run)
    _append_event(run_root, "run.preflight_only.finished", {"status": run["status"]})
    _atomic_write_json(run_root / "run.json", run)
    return run


def _run_preflight(
    command_base: list[str],
    cwd: Path,
    run_root: Path,
    run: dict[str, Any],
) -> dict[str, Any]:
    preflight_root = run_root / "preflight"
    preflight_root.mkdir(parents=True, exist_ok=True)
    stdout_path = preflight_root / "stdout.log"
    stderr_path = preflight_root / "stderr.log"
    command = [*command_base, "packet", "preflight", run["packet_root"]]
    started = time.monotonic()
    _append_event(run_root, "run.preflight.started", {"command": command})
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=int(run["execution"]["timeout_seconds"]),
        check=False,
    )
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    report = _parse_preflight_report(completed.stdout)
    preflight = {
        "status": "passed" if completed.returncode == 0 else "failed",
        "exit_code": completed.returncode,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "report": report,
    }
    run["preflight"] = preflight
    _append_event(
        run_root,
        f"run.preflight.{preflight['status']}",
        {"exit_code": completed.returncode},
    )
    _atomic_write_json(run_root / "run.json", run)
    return preflight


def _parse_preflight_report(stdout: str) -> dict[str, Any] | None:
    stripped = stdout.strip()
    if not stripped:
        return None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _fail_preflight(run_root: Path, run: dict[str, Any], preflight: dict[str, Any]) -> None:
    for target in run["targets"]:
        target["status"] = "skipped"
    run["status"] = "preflight_failed"
    run["completed_at"] = _now_iso()
    _summarize(run)
    _atomic_write_json(run_root / "run.json", run)
    raise AtlasCliError(
        ExitCode.EXTERNAL,
        "prompt_runner_preflight_failed",
        "Prompt runner SDK preflight failed; provider run was not started.",
        {
            "run_id": run["run_id"],
            "run_path": str(run_root / "run.json"),
            "packet_root": run["packet_root"],
            "preflight": preflight,
        },
    )


def _sdk_command(config: dict[str, Any]) -> tuple[list[str], Path]:
    sdk = config.get("sdk", {}) if isinstance(config.get("sdk"), dict) else {}
    local_path = Path(str(sdk.get("path") or "")).expanduser()
    use_local = bool(sdk.get("use_local_path", True))
    entrypoint = str(sdk.get("entrypoint") or "mix")
    if use_local and local_path.is_dir():
        if entrypoint == "mix":
            return ["mix", "prompt_runner"], local_path
        return [entrypoint], local_path
    binary = str(sdk.get("binary") or "prompt_runner")
    resolved = shutil.which(binary)
    if resolved:
        return [resolved], Path.cwd()
    raise AtlasCliError(
        ExitCode.EXTERNAL,
        "prompt_runner_unavailable",
        "Prompt runner SDK entrypoint is unavailable.",
        {"local_path": str(local_path), "binary": binary},
    )


def _summarize(run: dict[str, Any]) -> None:
    statuses = [target["status"] for target in run["targets"]]
    run["summary"] = {
        "succeeded": statuses.count("succeeded"),
        "failed": statuses.count("failed"),
        "skipped": statuses.count("skipped"),
        "pending": statuses.count("pending"),
    }


def _safe_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in value)
