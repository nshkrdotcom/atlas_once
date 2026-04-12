from __future__ import annotations

from dataclasses import dataclass

from ..config import AtlasSettings


@dataclass(frozen=True)
class ProfileTemplate:
    name: str
    description: str
    settings: AtlasSettings
    sample: bool = False
    install_default: bool = False
