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
          atlas install
          atlas init
          atlas config show
          atlas config ranked install --profile nshkrdotcom --force
          atlas status
          atlas next
          atlas registry scan
          atlas registry list
          atlas resolve <ref>
          atlas today
          atlas capture --project <ref> --kind decision "Move daemon ownership into core"
          atlas review inbox
          atlas promote auto
          atlas context repo <ref> current
          atlas context stack 1 3 5
          atlas context ranked prepare gn-ten
          atlas context ranked status gn-ten
          atlas context ranked gn-ten
          atlas context ranked prepare owned-elixir-all
          atlas context ranked owned-elixir-all
          atlas index
          atlas agent task "add streaming support"
          atlas agent find Agent
          atlas agent related lib/my_app/worker.ex
          atlas agent impact lib/my_app/worker.ex
          atlas symbols Agent --limit 10
          atlas def MyApp.Worker
          atlas refs MyApp.Worker handle_event
          atlas ranked-files --active lib/my_app/worker.ex --limit 10
          atlas impact lib/my_app/worker.ex --token-budget 12000
          atlas repo-map --active lib/my_app/worker.ex
          atlas note new "Routing notes" --project <ref> --tag routing
          atlas related <note-path>
          atlas --json status
          atlas menu

        Help Topics:
          atlas help install
          atlas help config
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
        "install": dedent(
            """\
            atlas install

            Recommended first-run setup after `uv tool install`.

              atlas install
              atlas install --profile default
              atlas install --shell-setup
              atlas install --print-shell
            """
        ),
        "config": dedent(
            """\
            atlas config

            Inspect and customize settings, profiles, and shell helpers.

              atlas config show
              atlas config profile list
              atlas config profile use default
              atlas config set data_home ~/atlas_once
              atlas config set code_root ~/code
              atlas config roots add ~/code
              atlas config shell show
              atlas config shell install
              atlas config ranked path
              atlas config ranked show
              atlas config ranked install --force
            """
        ),
        "registry": dedent(
            """\
            atlas registry

            Manage project roots, scan repos, and resolve aliases.

              atlas registry scan
              atlas registry scan --changed-only
              atlas registry list
              atlas resolve <ref>
              atlas registry show <ref>
              atlas registry root-add <path>
              atlas registry root-remove <path>
              atlas registry alias-add <ref> <alias>
              atlas registry alias-remove <ref> <alias>
            """
        ),
        "note": dedent(
            """\
            atlas note

            Create, find, open, and sync notes.

              atlas today
              atlas note new "System design" --project <ref> --tag architecture
              atlas note new "Atlas system design" --body-stdin
              atlas note open atlas
              atlas note find routing daemon
              atlas note sync
              atlas note sync <note-path>
            """
        ),
        "review": dedent(
            """\
            atlas review and promote

            Review inbox state and promote captured items into durable memory.

              atlas capture --project <ref> --kind decision \\
                "Prefer workspace root for mixed bundles"
              atlas review inbox
              atlas review daily
              atlas promote entry <entry-id> --kind decision --title "Workspace root preference"
              atlas promote auto
            """
        ),
        "context": dedent(
            """\
            atlas context

            Build LLM-ready context bundles from notes and repos.

              atlas context notes <notes-dir>
              atlas context notes <notes-dir> --pwd-only
              atlas context repo <ref> current
              atlas --json context repo <ref> current
              atlas context stack 1 3 5
              atlas context stack --group current <ref-a> <ref-b>
              atlas context ranked prepare <config-name>
              atlas --json context ranked status <config-name>
              atlas context ranked <config-name>
              atlas --json context ranked <config-name>
            """
        ),
        "agent": dedent(
            """\
            atlas agent quickstart

            Prefer the short agent surface inside a Mix repo:

              atlas agent status
              atlas agent task "add streaming support"
              atlas agent find Agent
              atlas agent def MyApp.Worker
              atlas agent refs MyApp.Worker
              atlas agent related lib/my_app/worker.ex
              atlas agent impact lib/my_app/worker.ex
              atlas --json agent task "add streaming support"

            The lower-level flows remain available when an agent needs a specific
            primitive or a cross-repo bundle:

              atlas --json status
              atlas --json next
              atlas --json resolve <project-ref>
              atlas context repo <project-ref> [group]
              atlas --json context repo <project-ref> [group]
              atlas --json context stack <preset-id|project-ref|path>...
              atlas --json context ranked prepare <config-name>
              atlas --json context ranked status <config-name>
              atlas --json context ranked <config-name>
              atlas --json index
              atlas --json symbols <query> --limit 10
              atlas --json def <Module>
              atlas --json def <Module> <function> [arity]
              atlas --json refs <Module> [function] [arity]
              atlas --json ranked-files --active <file> --limit 10
              atlas --json impact <file> --token-budget 12000
              atlas --json dexter lookup <Module>
              atlas --json note find <query>
              atlas --json note open <query> --print
              atlas --json review inbox
              atlas --json related <note-path>

            JSON payloads use schema_version, ok, command, exit_code, data, and errors.
            Atlas writes an append-only event log to the configured state root.
            Dexterity and raw Dexter commands run through Atlas shadow workspaces,
            so source repos stay clean.
            """
        ),
        "human": dedent(
            """\
            atlas human quickstart

              atlas install
              atlas init
              atlas config show
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
