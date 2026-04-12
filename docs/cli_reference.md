# CLI Reference

## Primary Commands

```bash
atlas
atlas help <topic>
atlas menu
atlas init
```

## Registry

```bash
atlas registry scan
atlas registry list
atlas registry show <ref>
atlas registry resolve <ref>
atlas registry root-add <path>
atlas registry root-remove <path>
atlas registry alias-add <ref> <alias>
atlas registry alias-remove <ref> <alias>
```

## Notes and Memory

```bash
atlas today
atlas capture [--project <ref>] [--kind <kind>] [--tag <tag>] <text...>
atlas review inbox
atlas review daily
atlas promote entry <id> [--kind <kind>] [--title <title>] [--project <ref>]
atlas promote auto
atlas note new <title> [--kind <kind>] [--project <ref>] [--tag <tag>] [--body <text>]
atlas note find <query...>
atlas note open [query...] [--print]
atlas note sync
```

## Context

```bash
atlas context notes [--pwd-only] <path>
atlas context repo <project-ref-or-path> [group]
atlas context stack [--group <group>] [--remember] [-o <file>] <items...>
```

## Maintenance

```bash
atlas snapshot <name> -- <command...>
atlas related <path> [--limit N]
atlas index rebuild
atlas prune snapshots [--days N] [--apply]
```
