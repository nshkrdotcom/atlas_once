from __future__ import annotations

from ...config import GENERIC_DATA_HOME, AtlasSettings
from ..base import ProfileTemplate

PROFILE = ProfileTemplate(
    name="default",
    description="Generic install-first profile with neutral storage defaults.",
    settings=AtlasSettings(
        data_home=GENERIC_DATA_HOME,
        code_root=None,
        project_roots=[],
        auto_sync_relationships=True,
        review_window_days=7,
    ),
    sample=False,
    install_default=False,
)
