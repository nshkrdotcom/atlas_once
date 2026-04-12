# Agent Onboarding

Atlas Once is optimized for agent-readable CLI workflows.

## Preferred Entry Point

Use:

```bash
atlas
```

for the dashboard, then direct subcommands for execution.

## Common Agent Flows

Resolve a project:

```bash
atlas registry resolve jsp
```

Build repo context:

```bash
atlas context repo jsp current
```

Build multi-repo context:

```bash
atlas context stack 1 3 5
atlas context stack --group current jsp jido_domain
```

Find or open notes:

```bash
atlas note find routing daemon
atlas note open switchyard --print
```

Capture and promote:

```bash
atlas capture --project jsp --kind decision "Prefer workspace root for mixed bundles"
atlas review inbox
atlas promote auto
```

Refresh graph and indexes:

```bash
atlas index rebuild
```

## Assumptions

- note data lives under `~/jb`
- operational state lives under `~/.atlas_once`
- project roots are managed through `atlas registry`
- generated backlink and related sections are owned by Atlas
