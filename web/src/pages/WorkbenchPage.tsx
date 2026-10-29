/**
 * 工作台主页面（§5）
 *
 * 三常驻区 + 一抽屉：
 *   顶栏：批量上传 / 全局设置 / 渲染队列状态
 *   左列：任务列表   右侧：详情面板
 *   底栏：已勾选 N 套 · 批量生成 · 渲染池
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiRequestError } from "../lib/api";
import { ASPECT_OPTIONS, DEFAULT_ASPECT } from "../lib/aspects";
import { useWorkbench } from "../hooks/useWorkbench";
import BatchUploadPanel from "../components/BatchUploadPanel";
import DetailPanel from "../components/DetailPanel";
import GlobalSettingsDrawer from "../components/GlobalSettingsDrawer";
import RenderQueuePanel from "../components/RenderQueuePanel";
import TaskList from "../components/TaskList";
import type { ListingSummary } from "../lib/types";

type Toast = { message: string; kind: "ok" | "err" } | null;

export default function WorkbenchPage() {
  const { data, loading, error, refresh, detail, loadDetail } = useWorkbench();
  const [filter, setFilter] = useState("");
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [activeId, setActiveId] = useState<string | null>(null);
  const [showUpload, setShowUpload] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [toast, setToast] = useState<Toast>(null);
  const [aspect, setAspect] = useState(DEFAULT_ASPECT);
  const [busy, setBusy] = useState(false);

  const showToast = useCallback((message: string, kind: "ok" | "err" = "ok") => {
    setToast({ message, kind });
    window.setTimeout(() => setToast(null), 4200);
  }, []);

  const listings = useMemo(() => data?.listings ?? [], [data]);

  // 队列面板只关心「还在跑」的任务（服务端也只发这些，这里再兜一层）。
  const liveJobs = useMemo(
    () =>
      (data?.queue.jobs ?? []).filter(
        (j) => j.state === "queued" || j.state === "rendering",
      ),
    [data],
  );

  useEffect(() => {
    if (data?.settings?.aspect) setAspect(data.settings.aspect);
  }, [data?.settings?.aspect]);

  // 选中的套如果从列表里消失（被删/重置），取消勾选
  useEffect(() => {
    if (!listings.length) return;
    const alive = new Set(listings.map((l: ListingSummary) => l.listing_id));
    setChecked((prev) => {
      const next = new Set([...prev].filter((id) => alive.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [listings]);

  const toggle = (listingId: string) => {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(listingId)) next.delete(listingId);
      else next.add(listingId);
      return next;
    });
  };

  const activate = async (listingId: string) => {
    setActiveId(listingId);
    await loadDetail(listingId);
  };

  const refreshAll = useCallback(async () => {
    await refresh();
    if (activeId) await loadDetail(activeId);
  }, [refresh, loadDetail, activeId]);

  const generate = async () => {
    if (!checked.size) {
      showToast("先在左侧勾选要生成的房源。", "err");
      return;
    }
    setBusy(true);
    try {
      const r = await api.renderBatch([...checked], aspect);
      if (r.refused.length) {
        showToast(
          `${r.jobs.length} 套已排队。${r.refused.length} 套没有排队：` +
            r.refused.map((x) => `${x.name}（${x.reason}）`).join("；"),
          r.jobs.length ? "ok" : "err",
        );
      } else {
        showToast(`${r.jobs.length} 套已排队（${aspect}）。`, "ok");
      }
      setChecked(new Set());
      await refreshAll();
    } catch (e) {
      showToast(
        e instanceof ApiRequestError ? e.message : "排队失败。",
        "err",
      );
    } finally {
      setBusy(false);
    }
  };

  const generateOne = async (listingId: string) => {
    setBusy(true);
    try {
      await api.renderOne(listingId, aspect);
      showToast(`已排队（${aspect}）。`, "ok");
      await refreshAll();
    } catch (e) {
      showToast(
        e instanceof ApiRequestError ? e.message : "排队失败。",
        "err",
      );
    } finally {
      setBusy(false);
    }
  };

  const active = detail?.listing_id === activeId ? detail : null;

  return (
    <div className="app">
      <header className="topbar">
        <h1>
          <span className="brand-dot" />
          房源宣传片工作台
        </h1>
        <button className="btn" onClick={() => setShowUpload((v) => !v)}>
          {showUpload ? "收起上传区" : "批量上传"}
        </button>
        <button className="btn" onClick={() => setShowSettings(true)}>
          全局设置
        </button>
        <div className="spacer" />
        <span className="kv">
          渲染池 {data?.queue.active ?? 0}/{data?.queue.concurrency ?? 2} 忙碌
          {loading ? " · 载入中" : ""}
        </span>
      </header>

      {error && (
        <div className="notice err" style={{ margin: 12 }}>
          {error}
        </div>
      )}

      {showUpload && (
        <div style={{ padding: "16px 22px", borderBottom: "1px solid var(--line)" }}>
          <BatchUploadPanel
            onUploaded={() => void refreshAll()}
            onToast={showToast}
          />
        </div>
      )}

      <div className="body">
        <TaskList
          listings={listings}
          counts={data?.counts ?? {}}
          filter={filter}
          onFilter={setFilter}
          selected={checked}
          onToggle={toggle}
          activeId={activeId}
          onActivate={(id) => void activate(id)}
        />
        <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
          <section className="detail" style={{ flex: "1 1 auto" }}>
            {active ? (
              <>
                <DetailPanel
                  detail={active}
                  onChanged={() => void refreshAll()}
                  onToast={showToast}
                />
                <div className="row" style={{ marginTop: 6 }}>
                  <button
                    className="btn primary"
                    disabled={busy || !active.can_render}
                    onClick={() => void generateOne(active.listing_id)}
                  >
                    生成本套
                  </button>
                  <span className="kv">
                    用当前画幅 {aspect} 生成一个新版本，历史版本会保留。
                  </span>
                </div>
              </>
            ) : (
              <p className="empty">
                在左侧选一套房源，查看它的照片、设置和成片。
              </p>
            )}
          </section>
          {/* 队列只在有活任务时出现：完成后就收起来，不留在页面上。 */}
          {liveJobs.length > 0 && (
            <div style={{ padding: "0 22px 18px" }}>
              <RenderQueuePanel
                jobs={liveJobs}
                active={data?.queue.active ?? 0}
                concurrency={data?.queue.concurrency ?? 2}
                onToast={showToast}
                onChanged={() => void refreshAll()}
              />
            </div>
          )}
        </div>
      </div>

      <footer className="footer">
        <span className="kv">已勾选 {checked.size} 套</span>
        <select
          className="select"
          style={{ width: 200 }}
          value={aspect}
          onChange={(e) => setAspect(e.target.value)}
          aria-label="画幅"
        >
          {ASPECT_OPTIONS.map((a) => (
            <option key={a.value} value={a.value}>
              {a.text}
            </option>
          ))}
        </select>
        <button
          className="btn primary"
          disabled={busy || checked.size === 0}
          onClick={() => void generate()}
        >
          批量生成
        </button>
        <span className="kv">
          一次生成一个画幅；要多个画幅就分几次生成，各占一个任务。
        </span>
      </footer>

      <GlobalSettingsDrawer
        open={showSettings}
        settings={data?.settings ?? null}
        onClose={() => setShowSettings(false)}
        onSaved={() => void refreshAll()}
        onToast={showToast}
      />

      {toast && <div className={`toast ${toast.kind}`}>{toast.message}</div>}
    </div>
  );
}
