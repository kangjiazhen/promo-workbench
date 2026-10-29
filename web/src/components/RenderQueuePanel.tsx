/**
 * 渲染队列（§5.4）
 *
 * 队列状态完全来自服务端 DB，前端每 1s 轮询一次。
 *
 * 这里只画「还在跑」的任务：服务端只把排队中/渲染中发过来，完成的成片在
 * 「成片版本」里，失败的房源在详情面板里带原因（重试就走「生成本套」）。
 * 由 WorkbenchPage 决定要不要挂载这个组件——空闲时整块收起来，不占版面。
 */
import { api, ApiRequestError } from "../lib/api";
import type { JobState } from "../lib/types";

interface Props {
  jobs: JobState[];
  active: number;
  concurrency: number;
  onToast: (m: string, k?: "ok" | "err") => void;
  onChanged: () => void;
}

const STAGE_TEXT: Record<string, string> = {
  preparing: "素材准备",
  rendering: "渲染",
  checking: "质检",
};

const STATE_TEXT: Record<string, string> = {
  queued: "排队中",
  rendering: "渲染中",
};

export default function RenderQueuePanel({
  jobs,
  active,
  concurrency,
  onToast,
  onChanged,
}: Props) {
  const live = jobs.filter((j) => j.state === "queued" || j.state === "rendering");

  const cancel = async (job: JobState) => {
    try {
      await api.cancelJob(job.job_id);
      onToast("已取消该渲染任务。", "ok");
      onChanged();
    } catch (e) {
      onToast(
        e instanceof ApiRequestError ? e.message : "取消失败。",
        "err",
      );
    }
  };

  return (
    <div className="section" style={{ marginBottom: 0 }}>
      <h3>
        渲染队列
        <span className="spacer" />
        <span className="kv">
          {active}/{concurrency} 忙碌
        </span>
      </h3>
      {live.length === 0 && <p className="empty">队列是空的。</p>}
      {live.map((job) => (
        <div className="version" key={job.job_id}>
          <div className="grow">
            <div>
              {job.listing_name || job.listing_id} · {job.aspect}
              <span className={`badge ${job.state}`} style={{ marginLeft: 8 }}>
                {STATE_TEXT[job.state] ?? job.state}
              </span>
              {job.stage && (
                <span className="kv" style={{ marginLeft: 8 }}>
                  {STAGE_TEXT[job.stage] ?? job.stage}
                </span>
              )}
            </div>
            {job.state === "rendering" && (
              <div className="bar" style={{ marginTop: 6 }}>
                <i style={{ width: `${Math.round(job.progress * 100)}%` }} />
              </div>
            )}
          </div>
          <button className="btn" onClick={() => void cancel(job)}>
            取消
          </button>
        </div>
      ))}
    </div>
  );
}
