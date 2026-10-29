"""Render one REAL film from a real listing with the Remotion renderer.

Not a unit test — an evidence probe. It builds the frozen storyboard for a
listing that actually exists in the module database, renders it through
`RemotionRenderer` exactly as a worker would, then prints ffprobe facts so
the result can be checked instead of assumed.

Usage:  python tests/render_real_film.py [listing_id] [aspect]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from property_promo.api import app as app_mod           # noqa: E402
from property_promo.rendering.remotion_renderer import RemotionRenderer  # noqa: E402
from property_promo.storyboard import planner          # noqa: E402


def main() -> int:
    listing_id = sys.argv[1] if len(sys.argv) > 1 else "2edb9e3dff0c"
    aspect = sys.argv[2] if len(sys.argv) > 2 else "16:9"

    store = app_mod.store
    listing = store.get_listing(listing_id)
    if listing is None:
        print(f"没有这个房源：{listing_id}")
        return 2
    service = app_mod.current_render_service()
    settings = service.effective_settings(listing_id)
    settings["aspect"] = aspect
    board = planner.build_storyboard(listing, settings)
    print(f"房源 {listing.name}：{len(board['shots'])} 个镜头，"
          f"{board['canvas']['width']}x{board['canvas']['height']}，"
          f"{board['fps']}fps，预计 "
          f"{sum(s['duration_s'] for s in board['shots']):.1f}s")

    media_dir = Path(app_mod.CFG["_data_dir"]) / "media" / listing_id
    out = Path(app_mod.CFG["_renders_dir"]) / listing_id / f"real_{aspect.replace(':', 'x')}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)

    renderer = RemotionRenderer()
    ok, why = renderer.available()
    print("渲染器可用：", ok, why or "-")
    if not ok:
        return 3

    mark = time.time()

    def on_progress(p: float) -> None:
        pct = int(p * 100)
        if pct % 10 == 0:
            print(f"  进度 {pct}%  {time.time() - mark:.0f}s", flush=True)

    result = renderer.render(board, out, media_dir=media_dir,
                             on_progress=on_progress, job_token="realfilm")
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    print(f"总耗时 {time.time() - mark:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
