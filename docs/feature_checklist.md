# Feature Checklist

## Core Platform

- [x] Canonical `atlas` CLI for primary workflows
- [x] Profile-based install and config seeding
- [x] Config root under `~/.config/atlas_once`
- [x] State root under `~/.atlas_once`
- [x] Append-only event log at `~/.atlas_once/events.jsonl`
- [x] Mutation locks under `~/.atlas_once/locks`
- [x] Stable JSON envelope for automation

## Registry

- [x] Multi-root repo scanning
- [x] Ref and alias resolution
- [x] Owner-scope and fork classification
- [x] Language inventory and primary-language detection
- [x] Repo capability detection for context strategies
- [x] Registry state written under `registry/repos.json`, `registry/projects.json`, and `registry/meta.json`

## Context Bundles

- [x] Notes bundle generation
- [x] Repo bundle generation
- [x] Explicit and remembered stack bundles
- [x] Stack preset storage under `~/.atlas_once/presets/context_stack.json`
- [x] Bundle cache under `~/.atlas_once/cache/bundles`

## Ranked Contexts V3

- [x] Single supported ranked schema version: `3`
- [x] Selector-driven groups with root scoping
- [x] Explicit repo groups with reusable repo variants
- [x] Per-project Mix overrides with `top_files`, `top_percent`, and `exclude`
- [x] Classification-aware nested Mix project discovery
- [x] Default exclusion of legacy, test, fixture, example, support, temp, dependency, doc, bench, and vendor trees
- [x] Atlas-managed shadow workspaces under `~/.atlas_once/code/shadows`
- [x] No Dexterity state written into source repos
- [x] Per-repo prepared manifest cache under `~/.atlas_once/cache/ranked_contexts/repos`
- [x] Per-group prepared manifests with repo and project summaries
- [x] Deterministic fallback when Dexterity returns no ranked files

## Memory And Notes

- [x] Capture inbox
- [x] Review inbox and daily review
- [x] Promotion into durable notes
- [x] Backlink generation
- [x] Related-note generation
- [x] Project, tag, relationship, and link indexes

## Public Surface Cleanup

- [x] Legacy `ctx`, `mixctx`, `mctx`, and `mcc` command entrypoints removed from installs
- [x] Install output and shell snippet no longer advertise removed compatibility commands
- [x] Ranked docs updated to the v3 model and current group examples
