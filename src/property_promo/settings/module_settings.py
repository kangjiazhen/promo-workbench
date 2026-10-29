"""Global default settings (§5.3).

Stored in the `global_settings` table so they can be changed at runtime
(the static config file stays read-only at startup). New listings inherit
a snapshot of the defaults at upload time; the values used by a render are
frozen into the Storyboard when the user clicks 生成 (§5.3).
"""
from __future__ import annotations

from ..domain import constants, errors

#: The five supported aspect ratios (§5.2, user-pinned: 4:3 and 16:9 must exist).
#: Aliased from `shared/constants.json`: the frontend builds its <select>
#: options from the same file, so the two ends can never disagree on pixels.
ASPECTS: dict[str, dict] = dict(constants.ASPECTS)

DEFAULT_CONTACT: dict = {
    "name": "",
    "phone": "",
    "wechat": "",
    "agency": "",
    "closing": "",
    "qr_note": "",
}

#: Opener big text. Deliberately NOT the folder name: the upload folder is
#: usually named after something identifying (e.g. a street address), and
#: §4 forbids showing that on screen. The old verified film printed exactly
#: this generic line instead.
DEFAULT_TITLE = "房源实拍"

#: Long titles break the opener layout; the card is sized for ~10 chars.
MAX_TITLE_CHARS = 24

#: End-card line used when the user filled in no contact at all. Without it
#: the outro is a heading with nothing under it — verified on a real render.
DEFAULT_OUTRO_LINE = "欢迎预约看房"

#: Print the room name on the first frame of each room's run of photos.
#: The name comes from the upload folder (or the room's editable display
#: name), which is why it is a *setting* rather than an always-on overlay:
#: a listing exported from a floor plan may carry names the agent would
#: rather not print. On by default — 「文件夹名就是房间名」 is the whole
#: reason the uploader asks for room folders.
DEFAULT_ROOM_LABELS = True

DEFAULT_SETTINGS: dict = {
    "contact": dict(DEFAULT_CONTACT),
    "title": DEFAULT_TITLE,
    "aspect": constants.DEFAULT_ASPECT,
    "room_labels": DEFAULT_ROOM_LABELS,
    "music": {"mode": "synth", "track": "warm"},   # none | synth | custom
    "motion": {"enabled": True, "pattern": "auto", "zoom": 0.26,
               "drift": 0.55, "max_upscale": 2.6, "ease": "lin"},
    "render_concurrency": constants.RENDER_CONCURRENCY_DEFAULT,
    "upload_concurrency": constants.UPLOAD_CONCURRENCY_DEFAULT,
    "max_versions_per_listing": constants.MAX_VERSIONS_PER_LISTING,
}

#: Music file limits (§12.2).
MAX_MUSIC_MB = constants.MUSIC_MAX_MB
MUSIC_EXTS = set(constants.MUSIC_EXTS)
MUSIC_MODES = set(constants.MUSIC_MODES)


def validate(settings: dict) -> None:
    """Validate a *complete* settings dict (no default-filling).

    `normalize` fills defaults; this only checks. It is used after
    overrides are applied to a base that already carries the global
    values, so a reset-to-global must not be replaced by the built-in
    default.
    """
    if "aspect" in settings and settings["aspect"] not in ASPECTS:
        raise errors.bad_setting("aspect", "画幅只能是 " + "、".join(ASPECTS))
    if "title" in settings:
        title = settings["title"]
        if not isinstance(title, str):
            raise errors.bad_setting("title", "片头标题必须是文字")
        if len(title.strip()) > MAX_TITLE_CHARS:
            raise errors.bad_setting(
                "title", f"片头标题最多 {MAX_TITLE_CHARS} 个字")
    music = settings.get("music")
    if isinstance(music, dict):
        mode = music.get("mode", "synth")
        if mode not in MUSIC_MODES:
            raise errors.bad_setting("music", "音乐模式只能是 none / synth / custom")
    if "room_labels" in settings and not isinstance(settings["room_labels"], bool):
        raise errors.bad_setting("room_labels", "房间名开关只能是开或关")
    for key in ("render_concurrency", "upload_concurrency"):
        if key in settings:
            try:
                n = int(settings[key])
            except (TypeError, ValueError):
                raise errors.bad_setting(key, "并发数必须是数字") from None
            if not 1 <= n <= 8:
                raise errors.bad_setting(key, "并发数必须在 1~8 之间")


def apply_overrides(base: dict, overrides: dict | None) -> dict:
    """Apply a *sparse* override map over a full settings dict.

    The listing's stored settings are overrides only — a key that is not
    present inherits the global default (§5.3: 全局默认在上传时给新套做
    初始值，真正渲染取值在点击生成那一刻冻结). A key whose value is
    `None` is *removed* from the override set, which is how 「恢复全局
    默认」 works.
    """
    out = {k: (dict(v) if isinstance(v, dict) else v)
           for k, v in base.items()}
    for key, value in (overrides or {}).items():
        if value is None:
            out.pop(key, None)
            continue
        if key in ("contact", "music", "motion") and isinstance(value, dict):
            merged = dict(out.get(key) or {})
            allowed = DEFAULT_SETTINGS.get(key, {})
            for ck, cv in value.items():
                if cv is None:
                    merged.pop(ck, None)
                elif ck in allowed:
                    merged[ck] = cv
            out[key] = merged
        else:
            out[key] = value
    validate(out)
    return out


def normalize(settings: dict | None) -> dict:
    """Merge a partial settings patch over the defaults."""
    out = {k: (dict(v) if isinstance(v, dict) else v)
           for k, v in DEFAULT_SETTINGS.items()}
    out["contact"] = dict(DEFAULT_CONTACT)
    for k, v in (settings or {}).items():
        if k == "contact" and isinstance(v, dict):
            out["contact"].update({ck: cv for ck, cv in v.items()
                                   if ck in DEFAULT_CONTACT})
        elif k == "music" and isinstance(v, dict):
            music = dict(out["music"])
            music.update(v)
            mode = music.get("mode", "synth")
            if mode not in MUSIC_MODES:
                raise errors.bad_setting("music", "音乐模式只能是 none / synth / custom")
            out["music"] = music
        elif k == "motion" and isinstance(v, dict):
            motion = dict(out["motion"])
            motion.update(v)
            out["motion"] = motion
        elif k == "aspect":
            if v not in ASPECTS:
                raise errors.bad_setting("aspect", "画幅只能是 " + "、".join(ASPECTS))
            out["aspect"] = v
        elif k == "title":
            if not isinstance(v, str):
                raise errors.bad_setting("title", "片头标题必须是文字")
            if len(v.strip()) > MAX_TITLE_CHARS:
                raise errors.bad_setting(
                    "title", f"片头标题最多 {MAX_TITLE_CHARS} 个字")
            # 空标题回落到默认文案，避免片头出现空白大标题。
            out["title"] = v.strip() or DEFAULT_TITLE
        elif k == "room_labels":
            out["room_labels"] = bool(v)
        elif k in ("render_concurrency", "upload_concurrency"):
            try:
                n = int(v)
            except (TypeError, ValueError):
                raise errors.bad_setting(k, "并发数必须是数字") from None
            if not 1 <= n <= 8:
                raise errors.bad_setting(k, "并发数必须在 1~8 之间")
            out[k] = n
        elif k == "max_versions_per_listing":
            out[k] = max(1, min(50, int(v)))
        else:
            out[k] = v
    return out
