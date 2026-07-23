"""Robust XML serialization for multi-file Atlas outputs.

Every Atlas command that concatenates multiple files (ranked context, ``ctx``,
``mctx``/stack, ``mcc`` markdown concat, bundles) emits a well-formed XML
``<pack>`` document instead of plaintext ``===== path =====`` / ``# FILE:``
markers. The shape mirrors repomix's ``<file path=...>`` envelope so downstream
agents can parse Atlas packs and repomix packs the same way.

Robustness rules:
- File content lives in ``<![CDATA[ ... ]]>``; any literal ``]]>`` inside the
  content is split-escaped (``]]]]><![CDATA[>``) so it can never terminate the
  section early. This makes the document safe for arbitrary source text.
- Attribute values are XML-escaped.
- Output is deterministic given deterministic input (callers order the files).

This module has no third-party dependencies and does no I/O.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

PACK_FORMAT_VERSION = "1"


@dataclass(frozen=True)
class PackFile:
    """One file in a pack.

    ``path`` is the repo-root-relative POSIX path (matches repomix). ``content``
    is the raw file text. The remaining fields are optional metadata emitted as
    attributes only when known.
    """

    path: str
    content: str
    project: str | None = None
    byte_size: int | None = None
    token_estimate: int | None = None
    rank: float | None = None


def escape_attr(value: str) -> str:
    """Escape a string for use inside a double-quoted XML attribute."""
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def escape_text(value: str) -> str:
    """Escape a string for XML character data (not inside CDATA)."""
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def wrap_cdata(content: str) -> str:
    """Wrap arbitrary text in CDATA, split-escaping any embedded ``]]>``."""
    safe = content.replace("]]>", "]]]]><![CDATA[>")
    return f"<![CDATA[{safe}]]>"


def _attr_pairs(pairs: Iterable[tuple[str, object]]) -> str:
    out: list[str] = []
    for key, value in pairs:
        if value is None or value == "":
            continue
        out.append(f'{key}="{escape_attr(str(value))}"')
    return " ".join(out)


def render_file_element(pf: PackFile, *, indent: str = "  ") -> str:
    """Render a single ``<file>`` element (trailing newline included)."""
    attrs = _attr_pairs(
        [
            ("path", pf.path),
            ("project", pf.project),
            ("bytes", pf.byte_size),
            ("tokens", pf.token_estimate),
            ("rank", f"{pf.rank:.6f}" if pf.rank is not None else None),
        ]
    )
    return f"{indent}<file {attrs}>{wrap_cdata(pf.content)}</file>\n"


def render_pack(
    files: Iterable[PackFile],
    *,
    generator: str = "atlas",
    kind: str = "pack",
    meta: Mapping[str, object] | None = None,
    summary: str | None = None,
    warnings: list[str] | None = None,
    root_tag: str = "pack",
) -> str:
    """Render a complete ``<pack>`` XML document for a list of files.

    ``meta`` becomes ordered attributes on the root element (skipping
    ``None``/empty). ``file_count`` is always appended. ``warnings`` (e.g. files
    that went missing between selection and render) are emitted as a
    ``<warnings>`` block so they never break the document the way a raw text
    warning line would.
    """
    file_list = list(files)
    meta_pairs: list[tuple[str, object]] = [("generator", generator), ("kind", kind)]
    if meta:
        meta_pairs.extend(meta.items())
    meta_pairs.append(("format_version", PACK_FORMAT_VERSION))
    meta_pairs.append(("file_count", len(file_list)))
    attrs = _attr_pairs(meta_pairs)

    parts = [f"<{root_tag} {attrs}>\n"]
    if summary:
        parts.append(f"  <summary>{escape_text(summary)}</summary>\n")
    if warnings:
        parts.append("  <warnings>\n")
        for warning in warnings:
            parts.append(f"    <warning>{escape_text(warning)}</warning>\n")
        parts.append("  </warnings>\n")
    parts.append("  <files>\n")
    for pf in file_list:
        parts.append(render_file_element(pf, indent="    "))
    parts.append("  </files>\n")
    parts.append(f"</{root_tag}>\n")
    return "".join(parts)


def render_packs(
    packs: Iterable[str],
    *,
    generator: str = "atlas",
    kind: str = "packs",
    meta: Mapping[str, object] | None = None,
) -> str:
    """Wrap several already-rendered ``<pack>`` documents in a ``<packs>`` root.

    Used for multi-target commands (``mctx``/stack) so the overall output stays
    a single well-formed XML document. Each child pack is indented one level.
    """
    pack_list = list(packs)
    meta_pairs: list[tuple[str, object]] = [("generator", generator), ("kind", kind)]
    if meta:
        meta_pairs.extend(meta.items())
    meta_pairs.append(("format_version", PACK_FORMAT_VERSION))
    meta_pairs.append(("pack_count", len(pack_list)))
    attrs = _attr_pairs(meta_pairs)

    parts = [f"<packs {attrs}>\n"]
    for pack in pack_list:
        for line in pack.splitlines():
            parts.append(f"  {line}\n" if line else "\n")
    parts.append("</packs>\n")
    return "".join(parts)
