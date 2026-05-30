"""Genericity invariant guard (Atlas docset I1).

The core codebase under src/atlas_once/ must not hard-code the maintainer's
host layout. Host-specific values (the literal string ``nshkrdotcom`` as a
self-owner, the path prefix ``~/p/g/n``, the docs hub
``~/p/g/j/jido_brainstorm/nshkrdotcom``) may appear only in:

* ``src/atlas_once/profiles/nshkrdotcom/`` (the packaged user profile),
* ``src/atlas_once/profiles/ranked_contexts.py`` (which exists explicitly to
  host the ``nshkrdotcom`` ranked-context template; the ``nshkrdotcom`` token
  there is gated to ``_nshkrdotcom_template``),
* docstrings/comments that explain genericity itself (we tolerate the
  spelling once per file as a documentation reference).

Tests live under tests/ and are allowed to mention host values explicitly,
because they construct fixtures and override env vars.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "atlas_once"

# Files that are allowed to reference host-specific values because they
# *are* the maintainer's named profile / ranked-context template.
PROFILE_ALLOWLIST = {
    SRC / "profiles" / "nshkrdotcom" / "__init__.py",
    SRC / "profiles" / "ranked_contexts.py",
}


def _iter_source_files() -> list[Path]:
    return [
        p
        for p in SRC.rglob("*.py")
        if "__pycache__" not in p.parts
    ]


@pytest.mark.parametrize("needle", ["~/p/g/n", "jido_brainstorm"])
def test_no_host_path_literals_outside_named_profiles(needle: str) -> None:
    offenders: list[str] = []
    for path in _iter_source_files():
        if path in PROFILE_ALLOWLIST:
            continue
        text = path.read_text(encoding="utf-8")
        if needle in text:
            offenders.append(f"{path.relative_to(SRC.parent.parent)}: contains {needle!r}")
    assert not offenders, (
        "Host-specific path literals leaked into generic modules.\n"
        "Move host-specific values into a named profile (e.g. "
        "src/atlas_once/profiles/nshkrdotcom/) or read them from an env var.\n"
        + "\n".join(offenders)
    )


def test_nshkrdotcom_self_owner_only_in_named_profile() -> None:
    """``nshkrdotcom`` may appear as a profile NAME in profile registration
    glue, but not as a hard-coded self_owner / default outside the named
    profile package and its ranked-context template helper."""

    # Pattern: "nshkrdotcom" appearing in a *value* position that smells
    # like a self-owner literal (e.g. inside a list/tuple/set with quotes).
    bad_pattern = re.compile(r"""self_owners?\s*[:=]\s*\[\s*["']nshkrdotcom["']""")

    offenders: list[str] = []
    for path in _iter_source_files():
        if path in PROFILE_ALLOWLIST:
            continue
        text = path.read_text(encoding="utf-8")
        if bad_pattern.search(text):
            offenders.append(str(path.relative_to(SRC.parent.parent)))

    assert not offenders, (
        "Hard-coded ``self_owners = ['nshkrdotcom']`` outside the named "
        "profile package:\n" + "\n".join(offenders)
    )


def test_default_profile_is_generic() -> None:
    """The packaged ``default`` profile must contain no host-specific
    literals (the executable form of I1+I2: ``default`` is generic,
    ``nshkrdotcom`` provides the local convenience defaults)."""
    text = (SRC / "profiles" / "default" / "__init__.py").read_text(encoding="utf-8")
    for needle in ("~/p/g/n", "jido_brainstorm", "nshkrdotcom"):
        assert needle not in text, (
            f"default profile must not mention {needle!r}; it lives in profiles/nshkrdotcom/"
        )
