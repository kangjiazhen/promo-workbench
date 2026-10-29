/**
 * 任务列表（左列，§5.1）
 *
 * 排序：新上传在前；状态筛选；每行显示勾选框、名称、状态徽章、照片计数。
 */
import type { ListingSummary } from "../lib/types";

const FILTERS: { key: string; label: string }[] = [
  { key: "", label: "全部" },
  { key: "awaiting_confirm", label: "待确认" },
  { key: "uploading", label: "上传中" },
  { key: "queued,rendering", label: "渲染中" },
  { key: "complete", label: "完成" },
  { key: "failed", label: "失败" },
];

interface Props {
  listings: ListingSummary[];
  counts: Record<string, number>;
  filter: string;
  onFilter: (key: string) => void;
  selected: Set<string>;
  onToggle: (listingId: string) => void;
  activeId: string | null;
  onActivate: (listingId: string) => void;
}

export default function TaskList({
  listings,
  counts,
  filter,
  onFilter,
  selected,
  onToggle,
  activeId,
  onActivate,
}: Props) {
  return (
    <aside className="tasks">
      <header>
        <div className="title">
          <span>房源任务</span>
          <span>{listings.length} 套</span>
        </div>
        <div className="filters">
          {FILTERS.map((f) => {
            const n = f.key
              ? f.key
                  .split(",")
                  .reduce((sum, k) => sum + (counts[k] ?? 0), 0)
              : Object.values(counts).reduce((s, v) => s + v, 0);
            return (
              <button
                key={f.key || "all"}
                className={`chip${filter === f.key ? " on" : ""}`}
                onClick={() => onFilter(f.key)}
              >
                {f.label} {n > 0 ? n : ""}
              </button>
            );
          })}
        </div>
      </header>
      <div className="task-list">
        {listings.length === 0 && (
          <p className="empty">还没有房源。用右上角「批量上传」添加一个房源文件夹。</p>
        )}
        {listings.map((li) => (
          <div
            key={li.listing_id}
            className={`task${activeId === li.listing_id ? " active" : ""}`}
            onClick={() => onActivate(li.listing_id)}
          >
            <input
              type="checkbox"
              checked={selected.has(li.listing_id)}
              onClick={(e) => e.stopPropagation()}
              onChange={() => onToggle(li.listing_id)}
              aria-label={`选择 ${li.name}`}
              style={{ marginTop: 3 }}
            />
            <div style={{ minWidth: 0, flex: 1 }}>
              <div className="name" title={li.name}>
                {li.name}
              </div>
              <div className="meta">
                <span className={`badge ${li.state}`}>{li.state_label}</span>
                <span>
                  {li.room_count} 房间 · {li.selected}/{li.photos} 张
                </span>
                {li.state === "uploading" && (
                  <span>{Math.round(li.progress * 100)}%</span>
                )}
              </div>
              {li.error && (
                <div className="meta" style={{ color: "var(--err)" }}>
                  {li.error.slice(0, 60)}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </aside>
  );
}
