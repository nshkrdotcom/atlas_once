# Atlas Once

Atlas Once is a filesystem-first personal memory system and Unix-native context engineering toolkit.

It is built around plain files, small Python CLIs, and composable shell workflows for:

- daily notes
- inbox capture
- session summaries
- durable project and decision memory
- Markdown context bundling
- Elixir repo context bundling
- multi-repo presets
- snapshotting command output for LLM or review workflows

## Stack

- Python 3.12+
- `uv`
- `ruff`
- `pytest`
- plain Markdown and JSON

## Commands

- `ctx`
- `mixctx` / `mctx`
- `mcc`
- `docday`
- `today`
- `memadd`
- `memfind`
- `memopen`
- `memsnap`
- `session-close`
- `atlas-index`
- `atlas-related`
- `atlas-prune`

## Development

```bash
uv sync
uv run pytest
uv run ruff check .
uv run mypy src
```
