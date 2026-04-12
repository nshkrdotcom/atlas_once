# Install And Profiles

## Recommended Install

```bash
uv tool install git+https://github.com/nshkrdotcom/atlas_once
atlas install
```

After that, use:

```bash
atlas
```

No alias is required.

## Profile Selection

Atlas Once ships packaged profiles:

- `default`
- `nshkrdotcom`

Inspect them:

```bash
atlas config profile list
atlas config profile show default
atlas config profile show nshkrdotcom
```

Use a specific profile at install time:

```bash
atlas install --profile default
atlas install --profile nshkrdotcom
```

Switch later:

```bash
atlas config profile use default
```

The install command defaults to the `nshkrdotcom` sample profile. That is intentional for the shipped experience, but it is still just a profile and can be changed immediately.

## Shell Setup

Installed commands such as `atlas`, `ctx`, `mctx`, `mcc`, and `docday` work directly on `PATH`.

The only command that needs shell integration is `d`, because it must change the current shell directory.

Show the snippet:

```bash
atlas config shell show
```

Install it:

```bash
atlas config shell install
```

That writes a managed shell snippet and adds a source line to `~/.bashrc` by default.

## Customize The System

Show effective settings:

```bash
atlas config show
```

Adjust paths:

```bash
atlas config set data_home ~/atlas_once
atlas config set code_root ~/code
atlas config roots add ~/code
atlas config roots remove ~/code
```

## Dev Checkout Mode

If you are working inside the repository:

```bash
git clone https://github.com/nshkrdotcom/atlas_once
cd atlas_once
uv sync --dev
uv run atlas
```

That is a development workflow, not the primary end-user install story.
