from __future__ import annotations

from .base import ProfileTemplate


def has_ranked_context_template(profile_name: str) -> bool:
    return profile_name in {"default", "nshkrdotcom"}


def ranked_contexts_template_for_profile(profile: ProfileTemplate) -> dict[str, object] | None:
    if profile.name == "default":
        return _default_template(profile)
    if profile.name == "nshkrdotcom":
        return _nshkrdotcom_template(profile)
    return None


def _default_template(profile: ProfileTemplate) -> dict[str, object]:
    code_root = profile.settings.code_root or "~/code"
    return {
        "version": 3,
        "defaults": {
            "registry": {"self_owners": profile.settings.self_owners},
            "runtime": {
                "dexterity_root": f"{code_root}/dexterity",
                "dexter_bin": "dexter",
                "shadow_root": "~/.atlas_once/code/shadows",
            },
            "strategies": {
                "elixir_ranked_v1": {
                    "include_readme": True,
                    "top_files": 10,
                    "overscan_limit": 50,
                },
                "python_default_v1": {"include_readme": True, "top_files": 10},
                "rust_default_v1": {"include_readme": True, "top_files": 10},
                "node_default_v1": {"include_readme": True, "top_files": 10},
                "generic_default_v1": {"include_readme": True, "top_files": 10},
            },
        },
        "repos": {},
        "groups": {},
    }


def _nshkrdotcom_template(profile: ProfileTemplate) -> dict[str, object]:
    code_root = profile.settings.code_root or "~/p/g/n"
    return {
        "version": 3,
        "defaults": {
            "registry": {"self_owners": profile.settings.self_owners or ["nshkrdotcom"]},
            "runtime": {
                "dexterity_root": f"{code_root}/dexterity",
                "dexter_bin": "dexter",
                "shadow_root": "~/.atlas_once/code/shadows",
            },
            "strategies": {
                "elixir_ranked_v1": {
                    "include_readme": True,
                    "top_files": 10,
                    "overscan_limit": 50,
                },
                "python_default_v1": {"include_readme": True, "top_files": 10},
                "rust_default_v1": {"include_readme": True, "top_files": 10},
                "node_default_v1": {"include_readme": True, "top_files": 10},
                "generic_default_v1": {"include_readme": True, "top_files": 10},
            },
        },
        "repos": {
            "jido_integration": {
                "ref": "jido_integration",
                "variants": {
                    "ops-lite": {
                        "top_files": 6,
                        "projects": {
                            "apps/devops_incident_response": {"top_files": 4},
                            "apps/inference_ops": {"top_files": 4},
                            "apps/trading_ops": {"top_files": 4},
                        },
                    }
                },
            }
        },
        "groups": {
            "owned-elixir-all": {
                "selectors": [
                    {
                        "owner_scope": "self",
                        "primary_language": "elixir",
                        "relation": "primary",
                        "roots": [code_root],
                        "variant": "default",
                    }
                ]
            }
        },
    }
