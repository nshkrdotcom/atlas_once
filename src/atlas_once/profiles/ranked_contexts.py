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
        "version": 1,
        "defaults": {
            "dexterity_root": f"{code_root}/dexterity",
            "dexter_bin": "dexter",
            "include_readme": True,
            "top_files": 10,
            "overscan_limit": 50,
        },
        "configs": {},
    }


def _nshkrdotcom_template(profile: ProfileTemplate) -> dict[str, object]:
    code_root = profile.settings.code_root or "~/p/g/n"
    return {
        "version": 1,
        "defaults": {
            "dexterity_root": f"{code_root}/dexterity",
            "dexter_bin": "dexter",
            "include_readme": True,
            "top_files": 10,
            "overscan_limit": 50,
        },
        "configs": {
            "ops-default": {
                "repos": [
                    {"path": f"{code_root}/jido"},
                    {"path": f"{code_root}/jido_action"},
                    {"path": f"{code_root}/jido_signal"},
                    {"path": f"{code_root}/jido_domain"},
                    {"path": f"{code_root}/jido_harness"},
                    {
                        "path": f"{code_root}/jido_integration",
                        "top_files": 6,
                        "projects": {
                            "apps/devops_incident_response": {"top_files": 4},
                            "apps/inference_ops": {"top_files": 4},
                            "apps/trading_ops": {"top_files": 4},
                        },
                    },
                ]
            },
            "platform-broad": {
                "top_files": 6,
                "repos": [
                    {"path": f"{code_root}/execution_plane"},
                    {"path": f"{code_root}/pristine"},
                    {"path": f"{code_root}/ground_plane"},
                    {"path": f"{code_root}/app_kit"},
                    {"path": f"{code_root}/outer_brain"},
                    {"path": f"{code_root}/jido_hive"},
                    {"path": f"{code_root}/citadel"},
                    {"path": f"{code_root}/stack_lab"},
                    {"path": f"{code_root}/AITrace"},
                ],
            },
        },
    }
