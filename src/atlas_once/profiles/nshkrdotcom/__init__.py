from __future__ import annotations

from ...config import AtlasSettings
from ..base import ProfileTemplate

PROFILE = ProfileTemplate(
    name="nshkrdotcom",
    description="Sample profile that matches nshkrdotcom's current Atlas layout and workflows.",
    settings=AtlasSettings(
        data_home="~/jb",
        code_root="~/p/g/n",
        project_roots=["~/p/g/n", "~/p/g/North-Shore-AI"],
        auto_sync_relationships=True,
        review_window_days=7,
    ),
    sample=True,
    install_default=True,
)
