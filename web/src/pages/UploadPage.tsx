import { useRef, useState } from "react";
import {
  explainEmptyFolder,
  roomManifest,
  summarize,
  uploadFolder,
  type FolderSummary,
  type UploadProgress,
} from "../lib/uploadFolder";

type Phase =
  | { step: "idle" }
  | { step: "picked"; summary: FolderSummary }
  | { step: "uploading"; progress: UploadProgress }
  | { step: "failed"; message: string; retryFiles?: File[]; listingId?: string }
  | { step: "done"; listingId: string; rooms: number; photos: number };

const fmtMB = (n: number) => `${(n / 1024 / 1024).toFixed(1)} MB`;

export default function UploadPage() {
  const [phase, setPhase] = useState<Phase>({ step: "idle" });
  const [pickedFiles, setPickedFiles] = useState<FileList | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const onPick = (files: FileList | null) => {
    if (!files || !files.length) {
      setPhase({
        step: "failed",
        message:
          "没有选中任何文件——可能所选文件夹里只有空的子文件夹。" +
          "浏览器不会上传空文件夹，请先把照片放进房间子文件夹。",
      });
      return;
    }
    setPickedFiles(files);
    const summary = summarize(files);
    if (!summary) {
      setPhase({
        step: "failed",
        message: explainEmptyFolder(files) || "没有读取到任何文件。",
      });
      return;
    }
    setPhase({ step: "picked", summary });
  };

  const startUpload = async (files: FileList) => {
    const ac = new AbortController();
    abortRef.current = ac;
    setPhase({ step: "uploading", progress: { done: 0, total: files.length, failed: [] } });
    try {
      const r = await uploadFolder(files, (p) => {
        setPhase({ step: "uploading", progress: p });
      }, ac.signal);
      setPhase({ step: "done", listingId: r.listingId, rooms: r.rooms, photos: r.photos });
    } catch (e) {
      const err = e as Error & {
        listingId?: string;
        failed?: { path: string; message: string }[];
        retryFiles?: File[];
      };
      if (err.name === "AbortError") {
        setPhase({
          step: "failed",
          message: "上传已取消。可以重新选择同一文件夹续传，已传完的文件会自动跳过。",
          listingId: err.listingId,
        });
        return;
      }
      setPhase({
        step: "failed",
        message: err.message || "上传失败",
        retryFiles: err.retryFiles,
        listingId: err.listingId,
      });
    }
  };

  const retry = async () => {
    // 失败重试：用同一个 FileList 重传（服务端幂等，已传文件跳过）
    if (!pickedFiles) return;
    await startUpload(pickedFiles);
  };

  const pct =
    phase.step === "uploading"
      ? Math.round((phase.progress.done / Math.max(1, phase.progress.total)) * 100)
      : 0;

  const s = phase.step === "picked" ? phase.summary : null;
  const uploadableCount = s ? roomManifest(s).length : 0;
  const blocking =
    s && uploadableCount === 0
      ? s.loose.length
        ? `图片直接放在了房源根目录（如 ${s.loose.slice(0, 3).join("、")}）。请先按房间整理成 房源/房间/图片 的结构。`
        : "没有找到符合“房间/图片”结构的照片。请把照片放进对应的房间子文件夹（例如 客厅/01.jpg）。"
      : null;

  return (
    <main style={{ maxWidth: 720, margin: "48px auto", padding: "0 16px", fontFamily: "system-ui, 'Microsoft YaHei', sans-serif" }}>
      <h1 style={{ fontSize: 24 }}>房源宣传片</h1>
      <p style={{ color: "#666" }}>
        上传一个房源文件夹：一级子文件夹 = 房间（客厅/、主卧/…），房间里的图片 = 该房间照片。
      </p>

      <input
        ref={inputRef}
        type="file"
        webkitdirectory=""
        directory=""
        multiple
        style={{ display: "none" }}
        onChange={(e) => onPick(e.target.files)}
      />

      {phase.step === "idle" && (
        <button
          onClick={() => inputRef.current?.click()}
          style={{ padding: "14px 28px", fontSize: 16, cursor: "pointer" }}
        >
          上传房源文件夹
        </button>
      )}

      {s && (
        <section style={{ border: "1px solid #ddd", borderRadius: 8, padding: 16 }}>
          <h2 style={{ marginTop: 0 }}>{s.rootDirName}</h2>
          {blocking ? (
            <>
              <p style={{ color: "#a33" }}>{blocking}</p>
              <button onClick={() => { setPhase({ step: "idle" }); setPickedFiles(null); }} style={{ padding: "10px 18px" }}>
                重新选择
              </button>
            </>
          ) : (
            <>
              <p>
                {s.rooms.length} 个房间 · {uploadableCount} 张照片 · {fmtMB(s.totalBytes)}
              </p>
              <ul style={{ margin: "8px 0", paddingLeft: 20, color: "#444" }}>
                {s.rooms.map((r) => (
                  <li key={r.name}>{r.name}：{r.count} 张</li>
                ))}
              </ul>
              {s.loose.length > 0 && (
                <p style={{ color: "#b80", fontSize: 14 }}>
                  注意：{s.loose.length} 张图片直接放在根目录，不会包含在本次上传（如 {s.loose.slice(0, 3).join("、")}）。
                </p>
              )}
              {s.unsupported.length > 0 && (
                <p style={{ color: "#b80", fontSize: 14 }}>
                  注意：{s.unsupported.length} 个文件不受支持，将被忽略（如 {s.unsupported.slice(0, 3).join("、")}）。
                </p>
              )}
              <button onClick={() => startUpload(pickedFiles!)} style={{ padding: "10px 22px", fontSize: 15, cursor: "pointer" }}>
                开始上传
              </button>
              <button onClick={() => { setPhase({ step: "idle" }); setPickedFiles(null); }} style={{ marginLeft: 12, padding: "10px 18px" }}>
                重新选择
              </button>
            </>
          )}
        </section>
      )}

      {phase.step === "uploading" && (
        <section>
          <p>
            正在上传 {phase.progress.done}/{phase.progress.total}
            {phase.progress.current ? ` · ${phase.progress.current}` : ""}
          </p>
          <div style={{ background: "#eee", borderRadius: 4, height: 12 }}>
            <div style={{ width: `${pct}%`, background: "#4a7dff", height: "100%", borderRadius: 4, transition: "width .2s" }} />
          </div>
          {phase.progress.failed.length > 0 && (
            <ul style={{ color: "#c33" }}>
              {phase.progress.failed.slice(0, 5).map((f) => (
                <li key={f.path}>{f.path}：{f.message}</li>
              ))}
            </ul>
          )}
          <button onClick={() => abortRef.current?.abort()} style={{ marginTop: 12, padding: "8px 16px" }}>
            取消上传
          </button>
        </section>
      )}

      {phase.step === "failed" && (
        <section style={{ border: "1px solid #e3b7b7", background: "#fdf3f3", borderRadius: 8, padding: 16 }}>
          <h2 style={{ marginTop: 0, color: "#a33" }}>上传没有完成</h2>
          <p>{phase.message}</p>
          {pickedFiles && (
            <button onClick={retry} style={{ padding: "10px 22px", cursor: "pointer" }}>
              重试（已传完的会跳过）
            </button>
          )}
        </section>
      )}

      {phase.step === "done" && (
        <section style={{ border: "1px solid #b7e3b7", background: "#f3fdf3", borderRadius: 8, padding: 16 }}>
          <h2 style={{ marginTop: 0, color: "#2a7a2a" }}>上传完成</h2>
          <p>
            {phase.rooms} 个房间 · {phase.photos} 张照片已就绪。
          </p>
          <p style={{ color: "#666" }}>下一步：选片与排序（阶段 ② 将提供）。</p>
        </section>
      )}
    </main>
  );
}
