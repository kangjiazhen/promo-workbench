"""Settings package: global defaults + per-listing overrides (§5.2/§5.3)."""
from .module_settings import (ASPECTS, DEFAULT_CONTACT, DEFAULT_SETTINGS,
                              MAX_MUSIC_MB, MUSIC_EXTS, MUSIC_MODES, normalize)

__all__ = ["ASPECTS", "DEFAULT_CONTACT", "DEFAULT_SETTINGS", "MAX_MUSIC_MB",
           "MUSIC_EXTS", "MUSIC_MODES", "normalize"]
