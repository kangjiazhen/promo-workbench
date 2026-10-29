# promo-workbench — 把一整个文件夹变成一套宣传片

**中文** ｜ [English summary](#english-summary)

批量宣传片工作台：把「一个文件夹 = 一个作品」的照片丢进去，选片、套用统一文案、批量渲染成片、
逐条确认版本。**行业无关** —— 文件夹名就是画面上的分组名。

- 房产中介：`某小区8号楼1201/客厅/01.jpg`、`…/主卧/…` → 带房间标识的看房片
- 产品/电商：`产品A/正面/01.png`、`产品A/细节/…` → 带「正面 / 细节 / 侧面」角标的产品片
- 展厅/门店：`展位B/入口/…`、`展位B/主推/…`
- 二手车、民宿、设备出租、作品集……只要有「一张张图 + 想按组讲」的结构就行

一次准备几套到十几套，一次上传、逐套确认、排队渲染，渲染不阻塞后续操作。全部本地运行，
不需要任何云端账号。

---

## 1. 它替你做了什么

手工做法是：一套一套拖进剪辑软件、对齐时长、打字幕、导出、命名、发客户 —— 十几套就是一整天。
这个工作台把重复的部分自动化，把不能自动化的部分（选哪张图、这套要说什么）留给你：

| 手工步骤 | 这里怎么处理 |
|---|---|
| 按组挑图、去掉糊的/重复的 | 上传后自动选片（模糊 / 偏暗 / 重复给理由），**手动调整永不被覆盖** |
| 每套改片头、片尾联系方式 | 全局设置一次，单套可覆盖 |
| 一张张对齐时长与运镜 | 固定节奏公式：4.2s 片头 + 每张 2.4~3.2s + 5.6s 片尾 |
| 导出、命名、找文件 | 渲染完直接预览 / 下载 / 打开所在文件夹，版本自动留 20 个 |
| 十几套排队等渲染 | 并发渲染池（默认 2 路，可调），一路在跑时可以继续上传、改别的套 |

**画面上的名字**：每个分组（房间 / 产品部位 / 展区）的名字会印在**每一张**照片左下角，
名字就是上传时的文件夹名，可以在界面里改。同一组连着的几张标签原地不动、跨镜头不闪，
换组那一刀才淡入新名字。不想要就关掉（全局默认开，单套可单独关）。

**隐私**：数据模型里没有「地址」字段，片头也**不会**取素材文件夹名（文件夹名常常就是门牌号）。
分组名可以上画面，作品名不行 —— 这条是设计出来的，不是运气。

---

## 2. 文件夹怎么整理

```text
我的素材/
├── 某小区8号楼1201/            ← 一套（一个作品 = 一个文件夹）
│   ├── 客厅/                   ← 分组名，会印在画面上
│   │   ├── 01.jpg
│   │   └── 02.jpg
│   ├── 主卧/
│   └── 厨房/
└── 某小区9号楼505/
    ├── 客厅/
    └── 主卧/
```

规则只有两条：

1. **图片的上一层文件夹 = 分组名**，再上一层 = 一套。
2. 支持 `.jpg .jpeg .png .webp`；根目录散图、分组里再嵌套一层，都会在预检里标红并告诉你该挪哪个文件。

批量上传多套就选**它们的父文件夹**（上例选 `我的素材/`）；只做一套就选那一套自己的文件夹。
判定看「图片在第几层」，不看文件夹叫什么 —— 所以随便命名，不会因为叫「客厅」才被认成房间。

---

## 3. 跑起来

需要 Python **3.11+**、Node **20+**、pnpm（或 npm）。

```powershell
# 1) 依赖
python -m venv .venv
.venv\Scripts\activate
pip install -e .
pip install pytest playwright pillow          # 开发/测试用
python -m playwright install chromium          # 只有跑 e2e 才需要

cd web && pnpm install && cd ..
cd renderer && pnpm install && cd ..           # 首次渲染会自动下载 headless chromium

# 2) 配置（可跳过，默认即本地单机）
copy config.example.json config.json

# 3) 起服务
powershell -File scripts/dev.ps1               # 后端 8861 + 前端 5188
```

打开 <http://localhost:5188> 就是工作台。只想要后端：

```powershell
$env:PYTHONPATH="src"; python -m uvicorn property_promo.api.app:app --port 8861
```

`config.json` 里的开关：

| 键 | 默认 | 说明 |
|---|---|---|
| `data_dir` | `./data` | 素材与 SQLite 都放这 |
| `renders_dir` | `./data/renders` | 成片输出目录 |
| `renderer` | `auto` | `remotion`（真渲染）/ `stub`（占位视频，秒出，用于跑通流程）/ `auto` |
| `max_upload_mb` / `max_photos_per_listing` | 30 / 300 | 单张上限 / 单套张数上限 |

环境变量：`PROPERTY_PROMO_RENDERER`（同 `renderer`）、`PROMO_CHROME`（指定本机 Chromium，
不指定就让 Remotion 自己下载）。

---

## 4. 用法

1. **批量上传** → 拖文件夹进来 → 每套一张预检卡片（分组数 / 张数 / 大小 / 结构问题）→ 开始上传。
   上传中可以取消；关掉页面重选同一个文件夹会按（套 / 相对路径 / 字节数）**跳过已传的**。
2. **待确认** → 点左侧任一套：照片墙按分区分组，自动选片给出「模糊 / 偏暗 / 重复」的理由。
   点图或勾选框保留/排除；分组名就地改（改名同时改画面上的字和顺序）。
3. **画幅 + 生成** → 左下选画幅（16:9 / 9:16 / 4:3 / 3:4 / 1:1），勾选要出片的套，点「批量生成」。
   一次一个画幅；要多个画幅就分几次生成，各占一个任务。
4. **渲染队列** → 只在真的有活任务时出现（排队 / 渲染中 + 取消），跑完自己收起。
   失败的那套在左侧带着原因，点开它再点「生成本套」就是重试。
5. **成片** → 右侧「成片版本」：页面内放大播放（Esc 关闭）、进原生全屏、下载 MP4、
   打开所在文件夹。每套保留最近 20 个版本。

「全局设置」里配：联系人（姓名/电话/微信/机构/结语）、片头大字、默认画幅、音乐、运镜、
渲染并发、是否印分组名。单套可以在详情里覆盖，留空即跟随全局。

片头大字默认「房源实拍」，改一次全局生效（例如「新品实拍」「展厅导览」）。

---

## 5. 换行业怎么用

**不用改代码**：分组名就是画面上的字，随便起。下面这些只需要改配置或文案。

| 想要 | 改哪里 |
|---|---|
| 片头大字、片尾结语 | 全局设置（或改 `src/property_promo/settings/module_settings.py` 的默认值） |
| 界面上的「房源 / 房间」称呼 | `web/src/**` 里的中文字符串（`grep -rn "房源\|房间" web/src`，约 100 处） |
| 片长节奏（4.2 + 2.4~3.2 + 5.6） | `shared/constants.json`，改完跑 `python scripts/sync_shared_constants.py`；`tests/test_shared_constants.py` 守口径 |
| 配色 / 字体 / 排版 | `renderer/src/PropertyPromo.tsx` 顶部的 `INK / PAPER / BRASS / MUTED / SANS` 与 `RoomLabel` |
| 加一个画幅 | `shared/constants.json` 的 `aspects`（像素只许写在这里，测试禁止别处硬编码 1920/1440/1080） |

内部包名与库表沿用 `property_promo` / `listing` / `room` 这套词（历史原因），语义上就是
「作品 / 分组」。改包名要动 import，不建议为了好看而改。

---

## 6. 设计上想清楚的几件事

- **生成即冻结**：点「生成」那一刻把 Storyboard（镜头顺序、文案、画幅、运镜、联系方式、
  分组名）序列化存库，渲染只吃这份快照。渲染中继续改设置，改的是**下一次**。
- **人工确认门**：只有「待确认」的套能进渲染队列，没有隐式自动出片。
- **并发但可恢复**：SQLite（WAL + busy_timeout），每个渲染作业是独立 `node` 进程 +
  自己的浏览器 + 独立临时目录。服务重启时把「渲染中且租约过期」的作业标记失败，
  **不自动重排队**（重试要人点），避免开机自己跑一夜。
- **版本化**：每次生成新版本，每套留最近 20 个，旧版本可下载。
- **取消是真取消**：杀子进程 + 清临时目录，不留半成品。
- **合成工程缓存**：bundle 只在源码变更时重建；重建归属按 pid 判活、新鲜度按源码 stamp 比对 ——
  被强杀留下的死标记不会让「改了代码却出一样的片子」。

更多取舍见 [`docs/DESIGN.md`](docs/DESIGN.md)（原始设计文档，含被真机 E2E 推翻后修正的规则）。

---

## 7. 目录

```text
src/property_promo/
├── api/          # app.py（上传/文件/批次）+ workbench.py（工作台 API）
├── domain/       # models、constants（唯一口径）、errors（错误目录）、分组排序
├── ingestion/    # 分类、相对路径、媒体路径、探测、上传服务（含续传）
├── selection/    # 自动选片：模糊 / 偏暗 / 重复
├── storyboard/   # 规划器（冻结快照）、schema、时长与画幅数学
├── rendering/    # remotion_renderer、stub_renderer、verifier（ffprobe + 抽帧）、music_bed
├── jobs/         # store（SQLite WAL）、service、worker_pool（并发池 + 租约）
└── settings/     # 全局默认 + 单套覆盖
web/              # React + Vite 单页工作台
renderer/         # Remotion 合成（PropertyPromo.tsx）、render-job.mjs、still-job.mjs、bundle-cache.mjs
shared/constants.json   # 前后端共享口径（生成 web 端常量，测试校验一致）
tests/            # 151 passed, 1 skipped
```

---

## 8. 验证

```powershell
$env:PYTHONPATH="src"; python -m pytest tests -q     # 151 passed, 1 skipped（约 75s）
cd web; pnpm exec tsc -b                             # 前端类型检查 + 构建
cd renderer; pnpm exec tsc -p tsconfig.json --noEmit
python scripts/sync_shared_constants.py --check      # 前后端口径有没有漂移
```

> 还没跑过 `pnpm install`（`renderer/node_modules` 不存在）时，三个依赖渲染器的测试会
> 自动 skip，结果是 148 passed / 4 skipped —— 装好依赖就回到 151 / 1。

- 唯一 skip 是「真渲染一条成片」（`tests/test_stage3_render.py`，约 2~3 分钟）：
  设 `PROPERTY_PROMO_REAL_RENDER=1` 打开。
- `tests/test_bundle_cache.py`：死标记 / 活标记 / stamp 新鲜度（用真 node 子进程验）。
- `tests/test_render_mounts.py`：Windows junction 的删除语义（API 用错会让渲染崩在启动）。
- `tests/test_room_label_still.py`：真渲同一帧两遍（标签开/关）比像素，确认「字真的画上去了」。
- `tests/e2e_browser.py`：真浏览器 + 真渲染全链路（30 项检查），需要 8861 / 5188 都在跑，
  并指一个装满照片的文件夹：

  ```powershell
  $env:PROMO_SAMPLE_ALBUM="D:\photos\showroom"; python tests\e2e_browser.py
  ```
- `python tests/render_real_film.py [listing_id] [16:9|9:16|4:3|3:4|1:1]`：只验渲染器。

---

## 9. 已知限制

- 空文件夹不会被浏览器上传，所以「空分组」看不到。
- 桌面 Chrome / Edge 优先；`webkitdirectory` 在移动端浏览器不可用。
- 一次只做一个画幅。
- 续传按（套 / 相对路径 / 字节数）判定：同路径同字节数但内容不同的极端情况按「已上传」跳过。
- 片尾二维码只能是文字说明或你上传的现成图片（个人微信二维码无法仅凭微信号本地生成）。
- 渲染并发上限 8；实测每路 Remotion 占 2 线程以上，建议 2–3。
- Windows 上「打开所在文件夹」用 `os.startfile`，其他平台返回不支持。

---

## English summary

**promo-workbench** turns folders of photos into a batch of short promo videos, locally.

One folder = one film; each sub-folder names a section (`living-room/`, `front-view/`, `zone-b/`)
and that name is printed on every photo of that section. Upload a dozen folders, confirm the
auto-selection, pick an aspect ratio, and a concurrent render pool produces versioned MP4s —
no cloud account, no per-film editing.

Stack: Python 3.11 + FastAPI + SQLite (WAL) for the workbench and job pool; React + Vite for the
UI; Remotion for rendering. `python -m pytest tests -q` → 151 passed, 1 skipped.

MIT licensed.

---

## License

MIT — see [LICENSE](LICENSE).
