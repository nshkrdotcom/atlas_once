from __future__ import annotations

from pathlib import Path

from .config import AtlasPaths, ensure_state


def render_bash_snippet(profile_name: str | None = None) -> str:
    profile_line = f"# profile: {profile_name}\n" if profile_name else ""
    return (
        "# atlas_once managed shell snippet\n"
        f"{profile_line}"
        "# Commands such as atlas and docday should already be on PATH.\n"
        "# This snippet adds the d helper and can be edited locally if you want extra aliases.\n\n"
        "d() {\n"
        "  local target\n"
        '  target="$(docday "$@")" || return\n'
        '  cd "$target" || return\n'
        "}\n"
    )


def install_bash_snippet(paths: AtlasPaths, target: Path, profile_name: str | None = None) -> Path:
    ensure_state(paths)
    snippet_path = paths.bash_shell_path
    snippet_path.parent.mkdir(parents=True, exist_ok=True)
    snippet_path.write_text(render_bash_snippet(profile_name=profile_name), encoding="utf-8")

    target = target.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    source_line = f'[ -f "{snippet_path}" ] && . "{snippet_path}"'
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    if source_line not in existing:
        text = existing.rstrip()
        if text:
            text += "\n"
        text += source_line + "\n"
        target.write_text(text, encoding="utf-8")
    return snippet_path
