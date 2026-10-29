/**
 * 批量上传面板（§3）
 *
 * 拖拽是主入口（能一次拖多个文件夹），选择器是补充（一次一个）。
 * 预检在本地完成，所以用户点上传之前就知道哪套有问题。
 */
import { useEffect, useRef, useState } from "react";
import {
  classifyFolder,
  filesFromPicker,
  MAX_SCAN_FILES,
  readDropTree,
  scanLimitReached,
  unitBlocked,
  withUniqueRootName,
  type AddedFolder,
  type ListingUnit,
  type RawFile,
} from "../lib/batchUpload";
import { Semaphore, uploadUnit, type UploadUnit } from "../lib/uploader";
import { api } from "../lib/api";

interface Props {
  onUploaded: () => void;
  onToast: (m: string, k?: "ok" | "err") => void;
}

interface FolderEntry {
  id: string;
  folder: AddedFolder;
  raw: RawFile[];
}

const fmtMB = (n: number) => `${(n / 1024 / 1024).toFixed(1)} MB`;

export default function BatchUploadPanel({ onUploaded, onToast }: Props) {
  const [folders, setFolders] = useState<FolderEntry[]>([]);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<
    Record<string, { done: number; total: number; failed: number }>
  >({});
  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const counter = useRef(0);

  // §3.4：上传中关闭页签先问一句，避免用户以为传完了。
  useEffect(() => {
    if (!busy) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [busy]);

  const addFolder = (displayName: string, raw: RawFile[], dirs: string[] = []) => {
    if (!raw.length) {
      onToast("这个文件夹里没有文件。", "err");
      return;
    }
    const folder = classifyFolder(displayName, raw, dirs);
    counter.current += 1;
    setFolders((prev) => {
      // §12.2：两个拖进来的房源同名 → 后来者加后缀，各自独立成一套。
      const used = new Set(
        prev.flatMap((e) => e.folder.units.map((u) => u.rootDirName)),
      );
      const units = folder.units.map((u) => withUniqueRootName(u, used));
      return [...prev, { id: `f${counter.current}`, folder: { ...folder, units }, raw }];
    });
  };

  const onDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    setOver(false);
    const tree = await readDropTree(e.dataTransfer);
    const files = tree.files;
    if (!files.length) {
      onToast(
        tree.dirs.length
          ? `拖进来的 ${tree.dirs.length} 个文件夹里都没有照片。` +
            "浏览器不会上传空文件夹，请把照片放进房间文件夹。"
          : "没能读到拖进来的文件夹。请改用「选择文件夹」按钮。",
        "err",
      );
      return;
    }
    // §3.4：拖了过大的目录（整个盘/下载目录）时，宁可停下来说清楚，
    // 也不要让浏览器读完几万个文件再卡死。
    if (scanLimitReached(files)) {
      onToast(
        `这个目录里的文件超过 ${MAX_SCAN_FILES.toLocaleString()} 个，` +
          "已停止读取。请拖入更具体的房源或父文件夹。",
        "err",
      );
      return;
    }
    // 按顶层目录名分组（拖拽多个文件夹时）
    const groups = new Map<string, RawFile[]>();
    for (const f of files) {
      const top = f.path.split("/")[0];
      const rest = f.path.split("/").slice(1).join("/");
      groups.set(top, [...(groups.get(top) ?? []), { ...f, path: rest }]);
    }
    // 目录也要按顶层分组，才能提示空房间（§3.2）。
    const dirGroups = new Map<string, string[]>();
    for (const d of tree.dirs) {
      const segs = d.split("/");
      const top = segs[0];
      const rest = segs.slice(1).join("/");
      if (!dirGroups.has(top)) dirGroups.set(top, []);
      if (rest) dirGroups.get(top)!.push(rest);
    }
    for (const [name, group] of groups) {
      addFolder(name, group, dirGroups.get(name) ?? []);
    }
  };

  const onPick = (e: React.ChangeEvent<HTMLInputElement>) => {
    const list = e.target.files;
    if (!list || !list.length) return;
    // §3.4：选择器也可能选中整盘，先按文件数拦一道。
    if (list.length >= MAX_SCAN_FILES) {
      onToast(
        `这个目录里的文件超过 ${MAX_SCAN_FILES.toLocaleString()} 个，` +
          "请选择更具体的房源或父文件夹。",
        "err",
      );
      e.target.value = "";
      return;
    }
    const raw = filesFromPicker(list);
    if (!raw.length) return;
    // webkitRelativePath = "目录名/房间/文件" → 剥掉目录名
    const top = (list[0].webkitRelativePath || "").split("/")[0] || "房源";
    const stripped = raw.map((f) => ({
      ...f,
      path: f.path.split("/").slice(1).join("/"),
    }));
    addFolder(top, stripped);
    e.target.value = "";
  };

  const allUnits = folders.flatMap((f) =>
    f.folder.units.map((u) => ({ entry: f, unit: u })),
  );
  const blocked = allUnits.filter(({ unit }) => unitBlocked(unit) !== null);

  const start = async () => {
    const units = allUnits.filter(({ unit }) => !unitBlocked(unit));
    if (!units.length) {
      onToast("没有可以上传的房源：请先按「房源/房间/照片」整理文件夹。", "err");
      return;
    }
    setBusy(true);
    const ac = new AbortController();
    abortRef.current = ac;
    const sem = new Semaphore(4);
    let ok = 0;
    let bad = 0;
    try {
      // 一次「开始上传」= 一个批次（§3.1）。批次只是组织手段；即使开批次
      // 失败也不该挡住上传，所以失败时退化为「无批次」继续传。
      const batchId = await api
        .openBatch(units.map(({ unit }) => unit.rootDirName).join("、"))
        .then((r) => r.batch_id)
        .catch(() => null);
      await Promise.all(
        units.map(async ({ unit }) => {
          // unit.files 已经按 manifest 的三段路径建好索引，
          // 不再用模糊匹配去找原始 File（那会把同名文件配错房间）。
          const upload: UploadUnit = {
            rootDirName: unit.rootDirName,
            manifest: unit.manifest,
            files: unit.files,
            batchId,
          };
          const outcome = await uploadUnit(
            upload,
            sem,
            (p) =>
              setProgress((prev) => ({
                ...prev,
                [p.listingId || unit.rootDirName]: {
                  done: p.done,
                  total: p.total,
                  failed: p.failed.length,
                },
              })),
            ac.signal,
          );
          if (outcome.ok) ok += 1;
          else bad += 1;
        }),
      );
      // 「完成上传」→ 批次关闭，之后的「继续添加」开新批次（§3.1）。
      if (batchId) void api.closeBatch(batchId).catch(() => undefined);
    } catch (e) {
      if ((e as DOMException).name === "AbortError") {
        onToast("上传已取消。已传完的文件不会重传。", "err");
      } else {
        onToast(String(e), "err");
      }
    } finally {
      setBusy(false);
      abortRef.current = null;
      setFolders([]);
      setProgress({});
      if (ok) {
        onToast(
          `上传完成：${ok} 套已就绪${bad ? `，${bad} 套失败` : ""}。`,
          "ok",
        );
      }
      onUploaded();
    }
  };

  return (
    <div>
      <div
        className={`dropzone${over ? " over" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => void onDrop(e)}
      >
        <p style={{ margin: "0 0 6px" }}>
          <strong>把房源文件夹拖到这里</strong>
        </p>
        <p style={{ margin: "0 0 12px", fontSize: 13 }}>
          可以一次拖多个文件夹。父文件夹（里面每个子文件夹是一套房源）会自动拆开。
        </p>
        <button className="btn" onClick={() => inputRef.current?.click()}>
          或选择文件夹
        </button>
        <input
          ref={inputRef}
          type="file"
          webkitdirectory=""
          directory=""
          multiple
          style={{ display: "none" }}
          onChange={onPick}
        />
      </div>

      {folders.length > 0 && (
        <div style={{ marginTop: 14 }}>
          {folders.map((entry) => (
            <FolderCard key={entry.id} entry={entry} />
          ))}
        </div>
      )}

      {busy && (
        <div style={{ marginTop: 14 }}>
          {Object.entries(progress).map(([key, p]) => (
            <div key={key} style={{ marginBottom: 8 }}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <span>{key}</span>
                <span className="kv">
                  {p.done}/{p.total}
                  {p.failed ? ` · ${p.failed} 失败` : ""}
                </span>
              </div>
              <div className="bar">
                <i style={{ width: `${(p.done / Math.max(1, p.total)) * 100}%` }} />
              </div>
            </div>
          ))}
          <button
            className="btn danger"
            onClick={() => abortRef.current?.abort()}
          >
            取消上传
          </button>
        </div>
      )}

      {folders.length > 0 && !busy && (
        <div className="row" style={{ marginTop: 14 }}>
          <button className="btn primary" onClick={() => void start()}>
            上传 {allUnits.length - blocked.length} 套
          </button>
          <button
            className="btn ghost"
            onClick={() => setFolders([])}
          >
            清空
          </button>
          {blocked.length > 0 && (
            <span className="kv">
              {blocked.length} 套结构有问题，不会被上传（见下方红色说明）
            </span>
          )}
        </div>
      )}
    </div>
  );
}

function FolderCard({ entry }: { entry: FolderEntry }) {
  const { folder } = entry;
  return (
    <div className="unit-card">
      <h4>
        {folder.displayName}
        <span className="kv" style={{ marginLeft: 8, fontWeight: 400 }}>
          {folder.mode === "parent"
            ? "父文件夹 → 拆成多套"
            : folder.mode === "listing"
              ? "一套房源"
              : folder.mode === "ambiguous"
                ? "结构冲突"
                : "没有照片"}
        </span>
      </h4>
      {folder.message && (
        <div className="notice err" style={{ marginBottom: 8 }}>
          {folder.message}
        </div>
      )}
      {folder.stray.length > 0 && (
        <div className="notice warn">
          根目录有 {folder.stray.length} 张散图（如 {folder.stray.slice(0, 3).join("、")}
          ），不属于任何一套，已忽略。
        </div>
      )}
      {folder.units.map((unit) => (
        <UnitCard key={unit.rootDirName} unit={unit} />
      ))}
    </div>
  );
}

function UnitCard({ unit }: { unit: ListingUnit }) {
  const problem = unitBlocked(unit);
  return (
    <div
      className={`unit-card${problem ? " blocked" : ""}`}
      style={{ marginTop: 8, background: "var(--bg-input)" }}
    >
      <h4 style={{ display: "flex", alignItems: "center", gap: 8 }}>
        {unit.rootDirName}
        <span className="kv" style={{ fontWeight: 400 }}>
          {unit.rooms.length} 个房间 · {unit.manifest.length} 张 ·{" "}
          {fmtMB(unit.totalBytes)}
        </span>
      </h4>
      {problem && <div className="notice err">{problem}</div>}
      {unit.emptyRooms && unit.emptyRooms.length > 0 && (
        <div className="notice warn">
          这些房间文件夹里没有照片：{unit.emptyRooms.slice(0, 5).join("、")}
          {unit.emptyRooms.length > 5 ? ` 等 ${unit.emptyRooms.length} 个` : ""}
          。空文件夹不会被上传，照片放进去后会出现在成片里。
        </div>
      )}
      {unit.unsupported.length > 0 && (
        <div className="notice warn">
          {unit.unsupported.length} 个文件不受支持，将忽略（如{" "}
          {unit.unsupported.slice(0, 3).join("、")}）。
        </div>
      )}
      <div className="rooms">
        {unit.rooms.map((r) => (
          <span key={r.name} style={{ marginRight: 10 }}>
            {r.name} {r.count}
          </span>
        ))}
      </div>
    </div>
  );
}
