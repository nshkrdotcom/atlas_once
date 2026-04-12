from __future__ import annotations

from textwrap import dedent

from .config import AtlasPaths, AtlasSettings
from .registry import ProjectRecord


def render_dashboard(
    paths: AtlasPaths, settings: AtlasSettings, registry: list[ProjectRecord]
) -> str:
    roots = "\n".join(f"  - {root}" for root in settings.project_roots) or "  - none"
    return dedent(
        f"""\
        atlas: filesystem-first memory and context system

        Storage:
          data:  {paths.data_home}
          state: {paths.state_home}

        Registry:
          projects: {len(registry)}
          roots:
        {roots}

        Common Commands:
          atlas init
          atlas status
          atlas next
          atlas registry scan
          atlas registry list
          atlas resolve jsp
          atlas today
          atlas capture --project jsp --kind decision "Move daemon ownership into switchyard core"
          atlas review inbox
          atlas promote auto
          atlas context repo jsp current
          atlas context stack 1 3 5
          atlas note new "Switchyard routing notes" --project jsp --tag routing
          atlas related ~/jb/docs/20260411/switchyard/routing-notes.md
          atlas --json status
          atlas menu

        Help Topics:
          atlas help registry
          atlas help note
          atlas help review
          atlas help context
          atlas help agent
          atlas help human
        """
    ).rstrip()


def render_topic_help(topic: str) -> str:
    help_map = {
        "registry": dedent(
            """\
            atlas registry

            Manage project roots, scan repos, and resolve aliases.

              atlas registry scan
              atlas registry scan --changed-only
              atlas registry list
              atlas resolve jsp
              atlas registry show jsp
              atlas registry root-add ~/p/g/North-Shore-AI
              atlas registry root-remove ~/p/g/n
              atlas registry alias-add jido_symphony_prime jsp
              atlas registry alias-remove jido_symphony_prime jsp
            """
        ),
        "note": dedent(
            """\
            atlas note

            Create, find, open, and sync notes.

              atlas today
              atlas note new "Atlas system design" --project atlas_once --tag architecture
              atlas note new "Atlas system design" --body-stdin
              atlas note open atlas
              atlas note find routing daemon
              atlas note sync
              atlas note sync ~/jb/docs/20260411/atlas_once/system-design.md
            """
        ),
        "review": dedent(
            """\
            atlas review and promote

            Review inbox state and promote captured items into durable memory.

              atlas capture --project jsp --kind decision "Prefer workspace root for mixed bundles"
              atlas review inbox
              atlas review daily
              atlas promote entry 20260411-153045 --kind decision \\
                --title "Workspace root preference"
              atlas promote auto
            """
        ),
        "context": dedent(
            """\
            atlas context

            Build LLM-ready context bundles from notes and repos.

              atlas context notes ~/jb/docs/20260411/switchyard
              atlas context notes ~/jb/docs/20260411/switchyard --pwd-only
              atlas context repo jsp current
              atlas --json context repo jsp current
              atlas context stack 1 3 5
              atlas context stack --group current jsp jido_domain
            """
        ),
        "agent": dedent(
            """\
            atlas agent quickstart

            Prefer these flows:

              atlas --json status
              atlas --json next
              atlas --json resolve <project-ref>
              atlas context repo <project-ref> [group]
              atlas --json context repo <project-ref> [group]
              atlas --json context stack <preset-id|project-ref|path>...
              atlas --json note find <query>
              atlas --json note open <query> --print
              atlas --json review inbox
              atlas --json related <note-path>

            JSON payloads use schema_version, ok, command, exit_code, data, and errors.
            Atlas writes an append-only event log to ~/.atlas_once/events.jsonl.
            """
        ),
        "human": dedent(
            """\
            atlas human quickstart

              atlas init
              atlas registry scan
              atlas status
              atlas today
              atlas capture "A loose thought to review later"
              atlas review daily
              atlas note new "Intentional note title"
              atlas menu
            """
        ),
    }
    if topic not in help_map:
        raise SystemExit(f"Unknown help topic: {topic}")
    return help_map[topic].rstrip()
