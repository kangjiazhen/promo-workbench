#!/usr/bin/env bash
# promo-workbench 服务器安装脚本（Ubuntu 24.04 / Debian 12 验证过）
#
#   sudo bash deploy/install.sh
#
# 干的事：装系统依赖（python3.12 / node20 / ffmpeg / caddy / pnpm）→ 建 venv →
# pip install -e . → 前端 pnpm build（产物给 Caddy 发）→ 建 promo 用户 →
# 装 systemd 单元 → 启动。重复执行是安全的（幂等）。
#
# 不做的事（要你自己按行决定）：
#   * 改 Caddyfile 里的域名与密码哈希（deploy/Caddyfile）
#   * 阿里云安全组放行 80/443（或你挑的端口）
#   * ICP 备案（国内节点用域名 + 80/443 必须备案）

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/promo-workbench}"
APP_USER="${APP_USER:-promo}"

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

[[ $EUID -eq 0 ]] || { echo "请用 sudo 跑：sudo bash deploy/install.sh"; exit 1; }

say "系统依赖"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip \
    ffmpeg curl ca-certificates git \
    debian-keyring debian-archive-keyring apt-transport-https

# Node 20（Remotion 需要）——只在没有 node 或版本太老时装
need_node=1
if command -v node >/dev/null 2>&1; then
    major="$(node -v | sed 's/^v\([0-9]*\).*/\1/')"
    [[ "$major" -ge 20 ]] && need_node=0
fi
if [[ $need_node -eq 1 ]]; then
    say "安装 Node 20"
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    apt-get install -y nodejs
fi
command -v pnpm >/dev/null 2>&1 || { say "安装 pnpm"; npm install -g pnpm; }

# Python 版本：代码要求 ≥3.11（PEP 604 类型写法 + 部分语法）
PY="$(command -v python3)"
"$PY" - <<'EOF'
import sys
if sys.version_info < (3, 11):
    sys.exit("需要 Python >= 3.11（Ubuntu 24.04 / Debian 12 自带就够；22.04 请加 deadsnakes PPA）")
EOF

say "服务用户"
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER"

say "代码与依赖"
[[ -d "$APP_DIR/.git" ]] || { echo "没有 $APP_DIR：先 git clone 到 $APP_DIR 再跑本脚本"; exit 1; }
cd "$APP_DIR"

[[ -d .venv ]] || "$PY" -m venv .venv
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -e . -q

say "配置"
[[ -f config.json ]] || cp config.example.json config.json
python3 - <<'EOF'
import json, pathlib
p = pathlib.Path("config.json")
cfg = json.loads(p.read_text(encoding="utf-8"))
# 服务器上渲染上限别开太大：每路 Remotion 起一个 chromium，按核数给
cfg.setdefault("renderer", "auto")
p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
EOF

say "前端构建"
cd "$APP_DIR/web"
[[ -d node_modules ]] || pnpm install --frozen-lockfile
pnpm build
cd "$APP_DIR"

say "渲染器依赖（首次渲染会自动 build 合成工程）"
cd "$APP_DIR/renderer"
[[ -d node_modules ]] || pnpm install --frozen-lockfile
cd "$APP_DIR"

say "目录权限"
mkdir -p "$APP_DIR/data" "$APP_DIR/data/renders"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

say "systemd 单元"
install -m 0644 "$APP_DIR/deploy/promo-workbench.service" /etc/systemd/system/promo-workbench.service
systemctl daemon-reload
systemctl enable --now promo-workbench
sleep 2
systemctl --no-pager --lines=8 status promo-workbench || true

say "自检"
curl -fsS http://127.0.0.1:8861/api/workbench >/dev/null && echo "API OK（127.0.0.1:8861）" || {
    echo "API 没起来：journalctl -u promo-workbench -n 50 --no-pager"; exit 1; }

cat <<'TIP'

下一步（手动三件事）：
  1) Caddy：把 deploy/Caddyfile 复制到 /etc/caddy/Caddyfile，改域名与密码哈希
       caddy hash-password --plaintext '你的密码'
       systemctl reload caddy
  2) 阿里云安全组：只放行 80/443（或你选的端口）；8861 不要放行。
  3) 备份：data/property_promo.db 与 data/renders/ —— 见 deploy/backup.sh

TIP
