"""真浏览器端到端：拖一个房源文件夹进去 → 确认照片 → 生成 → 播放成片。

这条脚本证明的是**用户真正会做的事**，不是接口能通：
  1. 打开 http://localhost:5188 ，页面上能看到工作台；
  2. 用 `<input webkitdirectory>` 真的选一个文件夹（Playwright 传目录路径），
     页面自己识别出「1 套房源 / 6 个房间 / N 张照片」；
  3. 点「上传 N 套」，等任务列表出现这套房源且状态是「待确认」；
  4. 点开它，照片墙按房间分组显示，房间名就是文件夹名；
  5. 点「生成本套」，等渲染任务走完，页面上出现可播放的 <video>；
  6. 视频文件能被服务端取回、ffprobe 认得、抽帧不是黑的、有声音。

用法（需要 8861 与 5188 都在跑）：
    python tests/e2e_browser.py [--real-render]
不加 --real-render 时如果后端是 Remotion 也照样等（约 3 分钟）。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import urllib.request

BASE = "http://localhost:5188"
API = "http://127.0.0.1:8861"
_REPO_MODULE = Path(__file__).resolve().parents[1]
if str(_REPO_MODULE / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_MODULE / "src"))
REAL_PHOTOS = Path(os.environ.get("PROMO_SAMPLE_ALBUM", ""))
ROOMS = {"客厅": 6, "主卧": 3, "厨房": 2, "卫生间": 2, "阳台": 2, "书房": 2}

OK: list[str] = []
BAD: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        OK.append(name)
        print(f"  PASS  {name}")
    else:
        BAD.append(f"{name}{(' :: ' + detail) if detail else ''}")
        print(f"  FAIL  {name}" + (f" :: {detail}" if detail else ""))


def build_fixture(root: Path) -> Path:
    """Create 房源E2E/<room>/<n>.jpg from PROMO_SAMPLE_ALBUM.

    Any folder of real photos works — the point of the test is that real
    pictures (not flat gradients) go through the real pipeline. The room names
    below are just the fixture's folder names; they can be anything.
    """
    if not REAL_PHOTOS.is_dir():
        raise SystemExit(
            "需要一个真实相册目录：设 PROMO_SAMPLE_ALBUM 指向任意装满照片的文件夹"
            "（例如 set PROMO_SAMPLE_ALBUM=D:\\photos\\showroom）")
    photos = sorted(p for p in REAL_PHOTOS.iterdir()
                    if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
    assert photos, f"没有可用的真实照片：{REAL_PHOTOS}"
    listing = root / "房源E2E"
    i = 0
    for room, count in ROOMS.items():
        d = listing / room
        d.mkdir(parents=True, exist_ok=True)
        for k in range(count):
            src = photos[i % len(photos)]
            shutil.copyfile(src, d / f"{k + 1:02}.jpg")
            i += 1
    return listing


def api_json(path: str) -> dict:
    with urllib.request.urlopen(API + path, timeout=30) as r:
        return json.loads(r.read())


def main() -> int:
    from playwright.sync_api import sync_playwright

    tmp = Path(tempfile.mkdtemp(prefix="promo_e2e_"))
    folder = build_fixture(tmp)
    total = sum(ROOMS.values())
    print(f"fixture: {folder}  ({len(ROOMS)} 房间 / {total} 张)")

    before = {x["listing_id"] for x in api_json("/api/workbench")["listings"]}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 950})
        console: list[str] = []
        page.on("console", lambda m: console.append(f"{m.type}: {m.text}"))
        page.on("pageerror", lambda e: console.append(f"pageerror: {e}"))

        page.goto(BASE, wait_until="domcontentloaded")
        page.wait_for_selector("text=房源宣传片工作台", timeout=15000)
        check("页面打开，标题是「房源宣传片工作台」", True)

        page.click("text=批量上传")
        page.wait_for_selector("text=把房源文件夹拖到这里", timeout=5000)

        # 真正的 webkitdirectory 选择：Playwright 传目录路径
        page.set_input_files("input[type=file]", str(folder))
        page.wait_for_selector("text=一套房源", timeout=10000)
        body = page.inner_text("body")
        check("识别为「一套房源」", "一套房源" in body)
        check(f"识别出 {len(ROOMS)} 个房间", f"{len(ROOMS)} 个房间" in body
              or "6 个房间" in body, body[:400])
        check("没有报结构错误", "结构冲突" not in body and "没有照片" not in body)
        page.screenshot(path=str(tmp / "01_picked.png"), full_page=True)

        # 上传按钮的文案是「上传 N 套」——必须精确匹配，
        # 否则 has-text('上传') 会点到顶栏的「批量上传」把面板收起来。
        upload_btn = page.get_by_role(
            "button", name=re.compile(r"^上传 \d+ 套$"))
        check("出现「上传 N 套」按钮", upload_btn.count() == 1,
              f"{upload_btn.count()} 个")
        upload_btn.click()
        page.wait_for_selector("text=上传完成", timeout=180000)
        check("上传完成提示出现", True)

        # 新房源出现在任务列表
        listing_id = None
        deadline = time.time() + 60
        while time.time() < deadline and listing_id is None:
            for li in api_json("/api/workbench")["listings"]:
                if li["listing_id"] not in before and li["name"].startswith("房源E2E"):
                    listing_id = li["listing_id"]
                    break
            time.sleep(0.5)
        check("服务端多了一套房源", listing_id is not None)
        if listing_id is None:
            return 1

        page.wait_for_selector(f"text=房源E2E", timeout=20000)
        page.click("text=房源E2E")
        page.wait_for_selector("text=成片版本", timeout=15000)
        detail_text = page.inner_text("body")
        # 房间名是可编辑的 <input class="room-name">，值不在 innerText 里，
        # 必须读 input 的 value，否则会误判「房间没显示」。
        room_names = page.eval_on_selector_all(
            "input.room-name", "els => els.map(e => e.value)")
        for room in ROOMS:
            check(f"照片墙显示房间「{room}」", room in room_names,
                  f"实际房间：{room_names}")
        # 看房顺序由后端 ROOM_ORDER_DEFAULT 决定：客厅→厨房→主卧→书房→卫生间→阳台。
        # 这里从后端模块直接算期望值，避免测试自己写死一套顺序。
        from property_promo.domain.room_order import sort_room_dirs
        expected_order = sort_room_dirs(list(ROOMS))
        check("房间按看房顺序排列", room_names == expected_order,
              f"期望 {expected_order}，实际 {room_names}")
        check("出现「生成本套」按钮", "生成本套" in detail_text)
        # §5.4：成片区的「打开所在文件夹」由后端 os.startfile 实现，
        # 前端只拿 ID。这里只确认入口在，不在测试里真的弹资源管理器。
        check("出现「打开所在文件夹」入口", "打开所在文件夹" in detail_text)
        page.screenshot(path=str(tmp / "02_detail.png"), full_page=True)

        page.click("button:has-text('生成本套')")
        page.wait_for_selector("text=已排队", timeout=20000)
        check("按钮点击后进入渲染队列", True)

        # 等渲染结束（真实 Remotion 约 3 分钟）
        final = None
        deadline = time.time() + 900
        while time.time() < deadline:
            st = api_json(f"/api/listings/{listing_id}")
            versions = st.get("versions") or []
            if versions:
                final = versions[0]
                break
            if st["state"] == "failed":
                check("渲染没有失败", False, st.get("error") or "")
                break
            time.sleep(3)
        check("生成了成片版本", final is not None,
              "" if final else "900s 内没有版本")
        if final is None:
            page.screenshot(path=str(tmp / "03_timeout.png"), full_page=True)
            browser.close()
            return 1

        # 页面上真的出现播放器。渲染要等 3 分钟，期间只要有人动了
        # web/src，Vite 就会整页重载并把详情面板收回初始态 —— 那不是产品
        # 缺陷，所以这里先尝试，失败就重新点开这套房源再看一次。
        def wait_video(timeout: int) -> bool:
            try:
                page.wait_for_selector("video", timeout=timeout)
                return True
            except Exception:
                return False

        if not wait_video(20000):
            print("  详情面板被整页刷新重置了，重新点开房源再看")
            page.click("text=房源E2E")
            page.wait_for_selector("text=成片版本", timeout=30000)
            wait_video(30000)
        src = ((page.get_attribute("video", "src") or "")
               if page.query_selector("video") else "")
        check("页面出现 <video> 播放器", bool(src), src or "页面上没有 <video>")
        page.screenshot(path=str(tmp / "04_result.png"), full_page=True)
        if not src:
            browser.close()
            return 1

        # 放大播放：缩略播放器只有 168px，片子得能放大 / 进全屏看
        page.click("text=放大播放")
        box = None
        try:
            page.wait_for_selector("[aria-label='成片播放']", timeout=15000)
            stage = page.query_selector("[aria-label='成片播放'] video")
            box = stage.bounding_box() if stage else None
            has_full = page.query_selector("[aria-label='成片播放'] >> text=全屏") is not None
        except Exception:
            has_full = False
        check("点「放大播放」能弹出大播放器",
              bool(box) and box["width"] > 600,
              "" if not box else f"{box['width']:.0f}×{box['height']:.0f}")
        check("放大层提供「全屏」按钮", has_full)
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
        check("Esc 能关掉放大层",
              page.query_selector("[aria-label='成片播放']") is None)

        # 服务端能取回这个文件，且 ffprobe 认得
        vid = API + src if src.startswith("/") else src
        with urllib.request.urlopen(vid, timeout=120) as r:
            blob = r.read()
        check("成片能从页面地址下载", len(blob) > 1_000_000, f"{len(blob)} B")
        local = tmp / "downloaded.mp4"
        local.write_bytes(blob)
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", "-i", str(local)],
            capture_output=True, text=True)
        check("ffprobe 能解析成片", probe.returncode == 0, probe.stderr[-200:])
        if probe.returncode == 0:
            info = json.loads(probe.stdout)
            kinds = {s["codec_type"] for s in info["streams"]}
            check("成片有画面也有声音", kinds == {"video", "audio"}, str(kinds))
            check("时长符合公式（>20s）",
                  float(info["format"]["duration"]) > 20,
                  info["format"]["duration"])
        # 抽帧不是黑片
        frames = tmp / "frames"
        frames.mkdir()
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(local),
                        "-vf", "fps=1/10", str(frames / "f%02d.png")],
                       capture_output=True)
        pngs = sorted(frames.glob("*.png"))
        check("抽到了多帧画面", len(pngs) >= 3, f"{len(pngs)} 帧")
        if pngs:
            from PIL import Image
            stats = []
            for png in pngs:
                with Image.open(png) as im:
                    small = im.convert("L").resize((64, 36))
                    # Pillow 14 前 getdata 仍可用但会告警，优先用新接口。
                    raw = (getattr(small, "get_flattened_data", None)
                           or small.getdata)()
                    px = list(raw)
                stats.append(round(sum(px) / len(px), 1))
            check("画面不是全黑", max(stats) > 20, str(stats))
            check("画面有变化（不是一张静止卡）",
                  max(stats) - min(stats) > 5, str(stats))

        check("浏览器没有 JS 报错", not any("pageerror" in c for c in console),
              "; ".join(c for c in console if "pageerror" in c)[:300])
        browser.close()

    print(f"\n{len(OK)} passed, {len(BAD)} failed")
    for b in BAD:
        print("  -", b)
    print(f"screenshots: {tmp}")
    return 0 if not BAD else 1


if __name__ == "__main__":
    sys.exit(main())
