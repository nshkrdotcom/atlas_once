from __future__ import annotations

from dataclasses import asdict

from .base import ProfileTemplate
from .default import PROFILE as DEFAULT_PROFILE
from .nshkrdotcom import PROFILE as NSHKR_PROFILE
from .ranked_contexts import has_ranked_context_template, ranked_contexts_template_for_profile

DEFAULT_INSTALL_PROFILE = "nshkrdotcom"


PROFILES = {
    DEFAULT_PROFILE.name: DEFAULT_PROFILE,
    NSHKR_PROFILE.name: NSHKR_PROFILE,
}


def list_profiles() -> list[ProfileTemplate]:
    return [PROFILES[name] for name in sorted(PROFILES)]


def get_profile(name: str) -> ProfileTemplate:
    try:
        return PROFILES[name]
    except KeyError as exc:
        raise SystemExit(f"Unknown profile: {name}") from exc


def profile_dict(profile: ProfileTemplate) -> dict[str, object]:
    payload = asdict(profile)
    payload["settings"] = asdict(profile.settings)
    payload["ranked_contexts_template"] = has_ranked_context_template(profile.name)
    return payload


def get_ranked_context_template(name: str) -> dict[str, object] | None:
    return ranked_contexts_template_for_profile(get_profile(name))
