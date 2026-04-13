from __future__ import annotations

from ...config import AtlasSettings
from ..base import ProfileTemplate

PROFILE = ProfileTemplate(
    name="nshkrdotcom",
    description="Sample profile that matches nshkrdotcom's current Atlas layout and workflows.",
    settings=AtlasSettings(
        data_home="~/p/g/j/jido_brainstorm/nshkrdotcom",
        code_root="~/p/g/n",
        project_roots=["~/p/g/j", "~/p/g/n", "~/p/g/North-Shore-AI"],
        self_owners=["nshkrdotcom"],
        auto_sync_relationships=True,
        review_window_days=7,
    ),
    sample=True,
    install_default=True,
)
