#!/usr/bin/env bash
# 备份：SQLite（WAL 下必须用 .backup，直接 cp 可能拿到撕裂的库）+ 成片目录。
#
#   sudo bash deploy/backup.sh                  # 备份到 /var/backups/promo-workbench
#   KEEP=14 sudo bash deploy/backup.sh          # 保留 14 天
#
# 定时：sudo crontab -e
#   30 3 * * * bash /opt/promo-workbench/deploy/backup.sh >> /var/log/promo-backup.log 2>&1

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/promo-workbench}"
DEST="${DEST:-/var/backups/promo-workbench}"
KEEP="${KEEP:-7}"
STAMP="$(date +%Y%m%d-%H%M%S)"

mkdir -p "$DEST"

# 1) 数据库：.backup 会在 WAL 下生成一致的快照
if [[ -f "$APP_DIR/data/property_promo.db" ]]; then
    if command -v sqlite3 >/dev/null 2>&1; then
        sqlite3 "$APP_DIR/data/property_promo.db" ".backup '$DEST/db-$STAMP.sqlite'"
    else
        # 没有 sqlite3 时退回 python 的 backup API（等价语义）
        "$APP_DIR/.venv/bin/python" - "$APP_DIR/data/property_promo.db" "$DEST/db-$STAMP.sqlite" <<'EOF'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
with sqlite3.connect(src) as s, sqlite3.connect(dst) as d:
    s.backup(d)
EOF
    fi
    gzip -f "$DEST/db-$STAMP.sqlite"
fi

# 2) 成片：只打包，不搬走
if [[ -d "$APP_DIR/data/renders" ]]; then
    tar -czf "$DEST/renders-$STAMP.tar.gz" -C "$APP_DIR/data" renders
fi

# 3) 配置与素材清单（不含原始照片：那部分请用自己的对象存储策略）
cp -f "$APP_DIR/config.json" "$DEST/config-$STAMP.json" 2>/dev/null || true

# 4) 老化
find "$DEST" -maxdepth 1 -name 'db-*.sqlite.gz' -mtime +"$KEEP" -delete
find "$DEST" -maxdepth 1 -name 'renders-*.tar.gz' -mtime +"$KEEP" -delete
find "$DEST" -maxdepth 1 -name 'config-*.json' -mtime +"$KEEP" -delete

echo "backup ok: $DEST (keep ${KEEP} days)"
ls -lh "$DEST" | tail -n +2
