from __future__ import annotations

import json
import subprocess
from pathlib import Path

from atlas_once.atlas import main


def _git_init(path: Path) -> None:
    path.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)


def _git_commit_all(path: Path, message: str) -> None:
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Atlas Test",
            "-c",
            "user.email=atlas@example.test",
            "commit",
            "-m",
            message,
        ],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )


def _write_prompt_runner_config(atlas_env: Path, fake_binary: Path) -> None:
    config_dir = atlas_env / "config" / "atlas_once"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "prompt_runner.json").write_text(
        json.dumps(
            {
                "sdk": {
                    "use_local_path": False,
                    "binary": str(fake_binary),
                    "command_timeout_seconds": 5,
                },
                "defaults": {"provider": "simulated", "model": "simulated-demo"},
                "presets": {},
            }
        ),
        encoding="utf-8",
    )


def _fake_prompt_runner(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return path


def test_git_status_refresh_reports_dirty_repo(atlas_env: Path, capsys) -> None:
    repo = atlas_env / "code" / "atlas_once"
    _git_init(repo)
    (repo / "README.md").write_text("changed\n", encoding="utf-8")

    assert main(["registry", "scan"]) == 0
    capsys.readouterr()

    assert main(["--json", "git", "status", "atlas_once", "--refresh"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True
    assert payload["command"] == "git.status"
    assert payload["data"]["repo_count"] == 1
    assert payload["data"]["repos"][0]["untracked_count"] == 1


def test_git_status_reports_working_and_index_dirty(atlas_env: Path, capsys) -> None:
    repo = atlas_env / "code" / "atlas_once"
    _git_init(repo)
    source = repo / "README.md"
    source.write_text("initial\n", encoding="utf-8")
    _git_commit_all(repo, "initial")

    source.write_text("modified\n", encoding="utf-8")
    assert main(["registry", "scan"]) == 0
    capsys.readouterr()

    assert main(["--json", "git", "status", "atlas_once", "--refresh"]) == 0
    modified_payload = json.loads(capsys.readouterr().out)
    modified_repo = modified_payload["data"]["repos"][0]
    assert modified_repo["working_dirty"] is True
    assert modified_repo["index_dirty"] is False

    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
    assert main(["--json", "git", "status", "atlas_once", "--refresh"]) == 0
    staged_payload = json.loads(capsys.readouterr().out)
    staged_repo = staged_payload["data"]["repos"][0]
    assert staged_repo["working_dirty"] is False
    assert staged_repo["index_dirty"] is True


def test_alternate_manifest_selectors_and_exclusions(atlas_env: Path, capsys) -> None:
    first = atlas_env / "repos" / "first"
    second = atlas_env / "repos" / "second"
    _git_init(first)
    _git_init(second)
    manifest = atlas_env / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "repos": [
                    {"ref": "first", "path": str(first), "groups": ["demo"]},
                    {"ref": "second", "path": str(second), "groups": ["demo"]},
                    {"ref": "second-copy", "path": str(second), "groups": ["demo"]},
                ]
            }
        ),
        encoding="utf-8",
    )

    assert (
        main(
            [
                "--json",
                "git",
                "status",
                "@group:demo",
                "!second",
                "--manifest",
                str(manifest),
                "--refresh",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True
    assert [repo["repo_ref"] for repo in payload["data"]["repos"]] == ["first"]

    assert (
        main(
            [
                "--json",
                "git",
                "status",
                "@group:demo",
                "--manifest",
                str(manifest),
                "--refresh",
            ]
        )
        == 0
    )
    deduped_payload = json.loads(capsys.readouterr().out)
    assert [repo["repo_ref"] for repo in deduped_payload["data"]["repos"]] == [
        "first",
        "second",
    ]
    assert deduped_payload["data"]["repos"][0]["working_dirty"] is False


def test_git_status_reports_unresolved_selector_and_bad_repo_record(
    atlas_env: Path,
    capsys,
) -> None:
    manifest = atlas_env / "manifest.json"
    missing = atlas_env / "repos" / "missing"
    manifest.write_text(
        json.dumps({"repos": [{"ref": "missing", "path": str(missing)}]}),
        encoding="utf-8",
    )

    assert (
        main(
            [
                "--json",
                "git",
                "status",
                "missing",
                "--manifest",
                str(manifest),
                "--refresh",
            ]
        )
        == 0
    )
    bad_payload = json.loads(capsys.readouterr().out)
    assert bad_payload["data"]["repos"][0]["exists"] is False
    assert bad_payload["data"]["repos"][0]["errors"][0]["kind"] == "missing_path"

    assert (
        main(
            [
                "--json",
                "git",
                "status",
                "not-there",
                "--manifest",
                str(manifest),
            ]
        )
        == 0
    )
    unresolved_payload = json.loads(capsys.readouterr().out)
    assert unresolved_payload["data"]["selector_errors"][0]["kind"] == "unresolved_selector"


def test_prompt_run_sdk_dry_run_writes_run_record(atlas_env: Path, capsys) -> None:
    repo = atlas_env / "code" / "atlas_once"
    _git_init(repo)
    packet = atlas_env / "packet"
    packet.mkdir()

    assert main(["registry", "scan"]) == 0
    capsys.readouterr()

    assert (
        main(
            [
                "--json",
                "prompt-run-sdk",
                "foo-prompt",
                "simulated",
                str(packet),
                "--targets",
                "atlas_once",
                "--dry-run",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    run_id = payload["data"]["run_id"]
    run_path = atlas_env / "config" / "atlas_once" / "workflows" / "runs" / run_id / "run.json"

    assert payload["ok"] is True
    assert payload["data"]["status"] == "planned"
    assert payload["data"]["target_repos"] == ["atlas-once"]
    assert run_path.is_file()


def test_prompt_run_sdk_real_run_fails_fast_when_preflight_fails(
    atlas_env: Path,
    capsys,
) -> None:
    repo = atlas_env / "code" / "atlas_once"
    _git_init(repo)
    packet = atlas_env / "packet"
    packet.mkdir()
    log = atlas_env / "fake-sdk.log"
    fake = _fake_prompt_runner(
        atlas_env / "prompt_runner",
        f"""
printf '%s\\n' "$*" >> {log}
if [ "$1" = "packet" ] && [ "$2" = "preflight" ]; then
  cat <<'JSON'
{{"runtime_ready?":false,"readiness_errors":[{{"kind":"path_not_found","path":"/missing","scope":"repo","name":"app"}}]}}
JSON
  echo 'missing packet repo' >&2
  exit 1
fi
echo 'provider should not be invoked' >&2
exit 9
""",
    )
    _write_prompt_runner_config(atlas_env, fake)

    assert main(["registry", "scan"]) == 0
    capsys.readouterr()

    assert (
        main(
            [
                "--json",
                "prompt-run-sdk",
                "01",
                "simulated",
                str(packet),
                "--targets",
                "atlas_once",
            ]
        )
        != 0
    )
    payload = json.loads(capsys.readouterr().out)
    details = payload["errors"][0]["details"]
    run_path = Path(details["run_path"])
    run = json.loads(run_path.read_text(encoding="utf-8"))

    assert payload["errors"][0]["kind"] == "prompt_runner_preflight_failed"
    assert run["status"] == "preflight_failed"
    assert run["preflight"]["exit_code"] == 1
    assert run["preflight"]["report"]["runtime_ready?"] is False
    assert log.read_text(encoding="utf-8").splitlines() == [
        f"packet preflight {packet.resolve()}"
    ]


def test_prompt_run_sdk_preflight_only_records_success_without_provider_run(
    atlas_env: Path,
    capsys,
) -> None:
    repo = atlas_env / "code" / "atlas_once"
    _git_init(repo)
    packet = atlas_env / "packet"
    packet.mkdir()
    log = atlas_env / "fake-sdk.log"
    fake = _fake_prompt_runner(
        atlas_env / "prompt_runner",
        f"""
printf '%s\\n' "$*" >> {log}
if [ "$1" = "packet" ] && [ "$2" = "preflight" ]; then
  echo '{{"runtime_ready?":true,"readiness_errors":[]}}'
  exit 0
fi
echo 'provider should not be invoked' >&2
exit 9
""",
    )
    _write_prompt_runner_config(atlas_env, fake)

    assert main(["registry", "scan"]) == 0
    capsys.readouterr()

    assert (
        main(
            [
                "--json",
                "prompt-run-sdk",
                "01",
                "simulated",
                str(packet),
                "--targets",
                "atlas_once",
                "--preflight-only",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["data"]["status"] == "preflight_passed"
    assert payload["data"]["preflight"]["report"]["runtime_ready?"] is True
    assert log.read_text(encoding="utf-8").splitlines() == [
        f"packet preflight {packet.resolve()}"
    ]


def test_prompt_run_sdk_skip_preflight_invokes_provider_run(
    atlas_env: Path,
    capsys,
) -> None:
    repo = atlas_env / "code" / "atlas_once"
    _git_init(repo)
    packet = atlas_env / "packet"
    packet.mkdir()
    log = atlas_env / "fake-sdk.log"
    fake = _fake_prompt_runner(
        atlas_env / "prompt_runner",
        f"""
printf '%s\\n' "$*" >> {log}
if [ "$1" = "packet" ] && [ "$2" = "preflight" ]; then
  echo 'preflight should not be invoked' >&2
  exit 9
fi
exit 0
""",
    )
    _write_prompt_runner_config(atlas_env, fake)

    assert main(["registry", "scan"]) == 0
    capsys.readouterr()

    assert (
        main(
            [
                "--json",
                "prompt-run-sdk",
                "01",
                "simulated",
                str(packet),
                "--targets",
                "atlas_once",
                "--skip-preflight",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["data"]["status"] == "done"
    assert payload["data"]["preflight"] is None
    assert log.read_text(encoding="utf-8").splitlines() == [
        f"run {packet.resolve()} 01 --provider simulated"
    ]
