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


def _strategy_defaults() -> dict[str, object]:
    return {
        "elixir_ranked_v1": {
            "include_readme": True,
            "top_files": 10,
            "overscan_limit": 50,
            "max_bytes": 60_000,
            "max_tokens": 15_000,
        },
        "python_default_v1": {
            "include_readme": True,
            "top_files": 10,
            "max_bytes": 40_000,
            "max_tokens": 10_000,
        },
        "rust_default_v1": {
            "include_readme": True,
            "top_files": 10,
            "max_bytes": 40_000,
            "max_tokens": 10_000,
        },
        "node_default_v1": {
            "include_readme": True,
            "top_files": 10,
            "max_bytes": 40_000,
            "max_tokens": 10_000,
        },
        "generic_default_v1": {
            "include_readme": True,
            "top_files": 10,
            "max_bytes": 40_000,
            "max_tokens": 10_000,
        },
    }


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
            "strategies": _strategy_defaults(),
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
            "strategies": _strategy_defaults(),
        },
        "repos": {
            "app_kit": {
                "ref": "app_kit",
                "variants": {
                    "gn-ten": {
                        "top_files": 8,
                        "max_bytes": 35_000,
                        "max_tokens": 9_000,
                        "projects": {
                            ".": {"exclude": True},
                            "bridges/domain_bridge": {"exclude": True},
                            "bridges/integration_bridge": {"exclude": True},
                            "bridges/mezzanine_bridge": {"exclude": True},
                            "bridges/outer_brain_bridge": {"exclude": True},
                            "bridges/projection_bridge": {"exclude": True},
                            "core/app_config": {"exclude": True},
                            "core/chat_surface": {"exclude": True},
                            "core/conversation_bridge": {"exclude": True},
                            "core/domain_surface": {"exclude": True},
                            "core/installation_surface": {"exclude": True},
                            "core/operator_surface": {"exclude": True},
                            "core/review_surface": {"exclude": True},
                            "core/run_governance": {"exclude": True},
                            "core/runtime_gateway": {"exclude": True},
                            "core/scope_objects": {"exclude": True},
                            "core/work_control": {"exclude": True},
                            "core/work_surface": {"exclude": True},
                            "examples/reference_host": {"exclude": True},
                        },
                    }
                },
            },
            "citadel": {
                "ref": "citadel",
                "variants": {
                    "gn-ten": {
                        "top_files": 4,
                        "max_bytes": 100_000,
                        "max_tokens": 25_000,
                        "projects": {
                            ".": {"exclude": True},
                            "apps/coding_assist": {"exclude": True},
                            "apps/host_surface_harness": {"exclude": True},
                            "apps/operator_assist": {"exclude": True},
                            "bridges/jido_integration_bridge": {
                                "top_files": 5,
                                "priority_tier": 2,
                            },
                            "bridges/query_bridge": {
                                "top_files": 5,
                                "priority_tier": 3,
                            },
                            "bridges/trace_bridge": {
                                "top_files": 5,
                                "priority_tier": 3,
                            },
                            "core/authority_contract": {
                                "top_files": 6,
                                "priority_tier": 2,
                            },
                            "core/citadel_core": {
                                "top_files": 6,
                                "priority_tier": 1,
                            },
                            "core/citadel_runtime": {
                                "top_files": 6,
                                "priority_tier": 1,
                            },
                            "core/conformance": {"exclude": True},
                            "core/contract_core": {
                                "top_files": 6,
                                "priority_tier": 1,
                            },
                            "core/execution_governance_contract": {
                                "top_files": 5,
                                "priority_tier": 3,
                            },
                            "core/jido_integration_v2_contracts": {
                                "top_files": 5,
                                "priority_tier": 2,
                            },
                            "surfaces/citadel_domain_surface": {"exclude": True},
                        },
                    }
                },
            },
            "ground_plane": {
                "ref": "ground_plane",
                "variants": {
                    "gn-ten": {
                        "top_files": 6,
                        "max_bytes": 16_000,
                        "max_tokens": 4_000,
                        "projects": {
                            ".": {"exclude": True},
                            "core/ground_plane_postgres": {"exclude": True},
                            "core/ground_plane_projection": {"exclude": True},
                            "examples/projection_smoke": {"exclude": True},
                        },
                    }
                },
            },
            "jido_integration": {
                "ref": "jido_integration",
                "variants": {
                    "ops-lite": {
                        "top_files": 6,
                        "max_bytes": 90_000,
                        "max_tokens": 22_500,
                        "projects": {
                            "apps/devops_incident_response": {"top_files": 4},
                            "apps/inference_ops": {"top_files": 4},
                            "apps/trading_ops": {"top_files": 4},
                        },
                    },
                    "gn-ten": {
                        "top_files": 4,
                        "max_bytes": 120_000,
                        "max_tokens": 30_000,
                        "projects": {
                            ".": {"exclude": True},
                            "apps/devops_incident_response": {"exclude": True},
                            "apps/inference_ops": {"exclude": True},
                            "apps/trading_ops": {"exclude": True},
                            "connectors/codex_cli": {"exclude": True},
                            "connectors/github": {
                                "top_files": 6,
                                "priority_tier": 1,
                            },
                            "connectors/linear": {
                                "top_files": 5,
                                "priority_tier": 2,
                            },
                            "connectors/market_data": {"exclude": True},
                            "connectors/notion": {
                                "top_files": 6,
                                "priority_tier": 1,
                            },
                            "core/asm_runtime_bridge": {"exclude": True},
                            "core/auth": {
                                "top_files": 5,
                                "priority_tier": 2,
                            },
                            "core/brain_ingress": {
                                "top_files": 5,
                                "priority_tier": 2,
                            },
                            "core/conformance": {"exclude": True},
                            "core/consumer_surfaces": {
                                "top_files": 6,
                                "priority_tier": 2,
                            },
                            "core/contracts": {
                                "top_files": 6,
                                "priority_tier": 1,
                            },
                            "core/control_plane": {
                                "top_files": 6,
                                "priority_tier": 1,
                            },
                            "core/direct_runtime": {
                                "top_files": 6,
                                "priority_tier": 1,
                            },
                            "core/dispatch_runtime": {
                                "top_files": 5,
                                "priority_tier": 2,
                            },
                            "core/ingress": {
                                "top_files": 5,
                                "priority_tier": 2,
                            },
                            "core/platform": {
                                "top_files": 6,
                                "priority_tier": 1,
                            },
                            "core/policy": {
                                "top_files": 5,
                                "priority_tier": 2,
                            },
                            "core/runtime_control": {"exclude": True},
                            "core/runtime_router": {"exclude": True},
                            "core/session_runtime": {"exclude": True},
                            "core/store_local": {
                                "top_files": 5,
                                "priority_tier": 2,
                            },
                            "core/store_postgres": {
                                "top_files": 5,
                                "priority_tier": 2,
                            },
                            "core/webhook_router": {
                                "top_files": 6,
                                "priority_tier": 1,
                            },
                        },
                    },
                },
            },
            "mezzanine": {
                "ref": "mezzanine",
                "variants": {
                    "gn-ten": {
                        "top_files": 4,
                        "max_bytes": 90_000,
                        "max_tokens": 22_500,
                        "projects": {
                            ".": {"exclude": True},
                            "bridges/citadel_bridge": {"exclude": True},
                            "bridges/integration_bridge": {"exclude": True},
                            "core/decision_engine": {
                                "top_files": 5,
                                "priority_tier": 2,
                            },
                            "core/execution_engine": {
                                "top_files": 6,
                                "priority_tier": 1,
                            },
                            "core/mezzanine_core": {
                                "top_files": 6,
                                "priority_tier": 1,
                            },
                            "core/object_engine": {
                                "top_files": 5,
                                "priority_tier": 2,
                            },
                            "core/ops_assurance": {"exclude": True},
                            "core/ops_audit": {"exclude": True},
                            "core/ops_control": {"exclude": True},
                            "core/projection_engine": {
                                "top_files": 5,
                                "priority_tier": 2,
                            },
                            "core/runtime_scheduler": {
                                "top_files": 5,
                                "priority_tier": 2,
                            },
                        },
                    }
                },
            },
            "outer_brain": {
                "ref": "outer_brain",
                "variants": {
                    "gn-ten": {
                        "top_files": 8,
                        "max_bytes": 24_000,
                        "max_tokens": 6_000,
                        "projects": {
                            ".": {"exclude": True},
                            "apps/host_surface": {"exclude": True},
                            "bridges/citadel_bridge": {"exclude": True},
                            "bridges/domain_bridge": {"exclude": True},
                            "bridges/ground_plane_projection_bridge": {"exclude": True},
                            "bridges/publication_bridge": {"exclude": True},
                            "bridges/review_bridge": {"exclude": True},
                            "core/outer_brain_core": {"exclude": True},
                            "core/outer_brain_journal": {"exclude": True},
                            "core/outer_brain_persistence": {"exclude": True},
                            "core/outer_brain_prompting": {"exclude": True},
                            "core/outer_brain_quality": {"exclude": True},
                            "core/outer_brain_restart_authority": {"exclude": True},
                            "core/outer_brain_runtime": {"exclude": True},
                            "examples/console_chat": {"exclude": True},
                            "examples/direct_citadel_action": {"exclude": True},
                        },
                    }
                },
            },
            "stack_lab": {
                "ref": "stack_lab",
                "variants": {
                    "gn-ten": {
                        "top_files": 6,
                        "max_bytes": 12_000,
                        "max_tokens": 3_000,
                        "project_discovery": {
                            "include_path_prefixes": ["support/lab_core"],
                        },
                        "projects": {
                            ".": {"exclude": True},
                            "support/citadel_spine_harness": {"exclude": True},
                        },
                    }
                },
            },
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
            },
            "gn-ten": {
                "items": [
                    {"ref": "app_kit", "variant": "gn-ten"},
                    {"ref": "extravaganza", "variant": "default"},
                    {"ref": "mezzanine", "variant": "gn-ten"},
                    {"ref": "outer_brain", "variant": "gn-ten"},
                    {"ref": "citadel", "variant": "gn-ten"},
                    {"ref": "jido_integration", "variant": "gn-ten"},
                    {"ref": "execution_plane", "variant": "default"},
                    {"ref": "ground_plane", "variant": "gn-ten"},
                    {"ref": "stack_lab", "variant": "gn-ten"},
                    {"ref": "AITrace", "variant": "default"},
                ]
            },
        },
    }
