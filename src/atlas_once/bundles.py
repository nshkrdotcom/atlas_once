from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import AtlasPaths, ensure_state
from .markdown_ctx import collect_markdown_bundle
from .mix_ctx import collect_mix_bundle
from .multi_ctx import load_presets, resolve_targets
from .ranked_context import render_prepared_ranked_bundle
from .runtime import approx_tokens


@dataclass(frozen=True)
class BundleManifest:
    kind: str
    bundle_path: str
    bytes: int
    approx_tokens: int
    file_count: int
    included_files: list[str]
    source_roots: list[str]
    cache_key: str


def _write_bundle(
    paths: AtlasPaths, kind: str, text: str, included_files: list[str], source_roots: list[str]
) -> BundleManifest:
    ensure_state(paths)
    digest = hashlib.sha256(
        (
            kind + "\0" + "\n".join(source_roots) + "\0" + "\n".join(included_files) + "\0" + text
        ).encode("utf-8")
    ).hexdigest()[:24]
    bundle_path = paths.bundle_cache_root / f"{digest}.ctx"
    bundle_path.write_text(text, encoding="utf-8")
    return BundleManifest(
        kind=kind,
        bundle_path=str(bundle_path),
        bytes=len(text.encode("utf-8")),
        approx_tokens=approx_tokens(text),
        file_count=len(included_files),
        included_files=included_files,
        source_roots=source_roots,
        cache_key=digest,
    )


def markdown_manifest(paths: AtlasPaths, target: Path, pwd_only: bool) -> BundleManifest:
    bundle = collect_markdown_bundle(target, pwd_only=pwd_only)
    return _write_bundle(
        paths,
        "notes",
        bundle.text,
        [str(path) for path in bundle.files],
        [str(bundle.root)],
    )


def mix_manifest(paths: AtlasPaths, target: Path, group: str | None) -> BundleManifest:
    bundle = collect_mix_bundle(target, requested_group=group)
    return _write_bundle(
        paths,
        "repo",
        bundle.text,
        [str(path) for path in bundle.files],
        [str(bundle.repo_root)],
    )


def stack_manifest(paths: AtlasPaths, items: list[str], group: str | None) -> BundleManifest:
    presets = load_presets()
    targets = resolve_targets(items, presets)
    chunks: list[str] = []
    included_files: list[str] = []
    source_roots: list[str] = []

    for target in targets:
        bundle = collect_mix_bundle(Path(target), requested_group=group)
        if len(targets) > 1:
            chunks.append(f"===== mcc {target} =====\n")
        chunks.append(bundle.text)
        source_roots.append(str(bundle.repo_root))
        included_files.extend(str(path) for path in bundle.files)

    return _write_bundle(paths, "stack", "".join(chunks), included_files, source_roots)


def ranked_manifest(paths: AtlasPaths, config_name: str) -> BundleManifest:
    bundle = render_prepared_ranked_bundle(paths, config_name)
    return _write_bundle(
        paths,
        "ranked",
        bundle.text,
        [str(path) for path in bundle.files],
        [str(path) for path in bundle.source_roots],
    )


def manifest_dict(manifest: BundleManifest) -> dict[str, object]:
    return asdict(manifest)
