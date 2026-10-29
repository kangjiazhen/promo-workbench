/**
 * 浏览器文件夹上传的相对路径只在这里处理。
 * webkitRelativePath 形如 "AJ13103/客厅/01.jpg"。
 */

export interface ManifestItem {
  path: string; // 完整 webkitRelativePath
  size: number;
}

const SUPPORTED_EXTS = [".jpg", ".jpeg", ".png", ".webp"];
const JUNK_NAMES = [".ds_store", "thumbs.db", "desktop.ini"];

export interface FolderSummary {
  rootDirName: string;
  manifest: ManifestItem[];
  rooms: { name: string; count: number }[];
  loose: string[]; // 图片直接放在房源根目录
  unsupported: string[]; // 不支持的类型 / 嵌套目录
  totalBytes: number;
}

/** 选择文件夹后立即在本地分类，让用户在上传前就看到结构问题。 */
export function summarize(files: FileList): FolderSummary | null {
  const list = Array.from(files).filter((f) => f.webkitRelativePath);
  if (!list.length) return null;
  const rootDirName = list[0].webkitRelativePath!.split("/")[0] || "房源";
  const rooms = new Map<string, number>();
  const loose: string[] = [];
  const unsupported: string[] = [];
  let totalBytes = 0;
  const manifest = list.map((f) => {
    const path = f.webkitRelativePath!;
    totalBytes += f.size;
    const segs = path.split("/");
    const name = segs[segs.length - 1];
    const lower = name.toLowerCase();
    if (!(JUNK_NAMES.includes(lower) || name.startsWith("._"))) {
      const ext = lower.slice(lower.lastIndexOf("."));
      if (!SUPPORTED_EXTS.includes(ext)) {
        unsupported.push(path);
      } else if (segs.length < 3) {
        loose.push(name);
      } else if (segs.length > 3) {
        unsupported.push(path);
      } else {
        const room = segs[1];
        rooms.set(room, (rooms.get(room) ?? 0) + 1);
      }
    }
    return { path, size: f.size };
  });
  return {
    rootDirName,
    manifest,
    rooms: [...rooms.entries()].map(([name, count]) => ({ name, count })),
    loose,
    unsupported,
    totalBytes,
  };
}

export function buildManifest(files: FileList): {
  rootDirName: string;
  manifest: ManifestItem[];
} | null {
  const list = Array.from(files).filter((f) => f.webkitRelativePath);
  if (!list.length) return null;
  const rootDirName = list[0].webkitRelativePath!.split("/")[0] || "房源";
  const manifest = list.map((f) => ({
    path: f.webkitRelativePath!,
    size: f.size,
  }));
  return { rootDirName, manifest };
}

/** 提交给服务器的清单只包含合法的 房间/图片 文件。 */
export function roomManifest(summary: FolderSummary): ManifestItem[] {
  const roomSet = new Set(summary.rooms.map((r) => r.name));
  return summary.manifest.filter((m) => {
    const segs = m.path.split("/");
    return segs.length === 3 && roomSet.has(segs[1]);
  });
}

export interface UploadProgress {
  done: number;
  total: number;
  failed: { path: string; message: string }[];
  current?: string;
}

export async function uploadFolder(
  files: FileList,
  onProgress: (p: UploadProgress) => void,
  signal?: AbortSignal,
): Promise<{ listingId: string; rooms: number; photos: number }> {
  const built = buildManifest(files);
  if (!built) throw new Error("没有选择任何文件");

  // 1) 创建房源 + 声明清单（只声明合法的房间照片）
  const summary = summarize(files);
  if (!summary || (!summary.rooms.length && !summary.loose.length && !summary.unsupported.length)) {
    throw new Error(
      "这个文件夹里没有上传任何文件。注意：浏览器不会上传空文件夹，" +
      "请确认房间子文件夹里确实放了照片后再选择。");
  }
  const uploadable = summary ? roomManifest(summary) : built.manifest;
  if (!uploadable.length) {
    throw new Error(
      "没有找到符合“房间/图片”结构的照片。" +
      "请把照片放进对应的房间子文件夹（例如 客厅/01.jpg）。");
  }
  const createRes = await fetch("/api/listings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rootDirName: built.rootDirName, manifest: uploadable }),
    signal,
  });
  const created = await createRes.json();
  if (!createRes.ok) throw new Error(created.message || "创建房源失败");

  // 2) 逐个上传（串行，便于进度与取消；失败收集后统一重试）
  const uploadSet = new Set(uploadable.map((m) => m.path));
  const list = Array.from(files).filter(
    (f) => f.webkitRelativePath && uploadSet.has(f.webkitRelativePath!),
  );
  const failed: { path: string; message: string }[] = [];
  let done = 0;
  const sent = new Set<string>();

  for (const f of list) {
    if (signal?.aborted) throw new DOMException("上传已取消", "AbortError");
    const path = f.webkitRelativePath!;
    onProgress({ done, total: list.length, failed, current: path });
    try {
      const fd = new FormData();
      fd.append("file", f);
      const res = await fetch(
        `/api/listings/${created.listing_id}/files?rel_path=${encodeURIComponent(path)}`,
        { method: "POST", body: fd, signal },
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({ message: res.statusText }));
        failed.push({ path, message: err.message || `HTTP ${res.status}` });
      } else {
        sent.add(path);
      }
    } catch (e) {
      if ((e as DOMException).name === "AbortError") throw e;
      failed.push({ path, message: String(e) });
    }
    done += 1;
    onProgress({ done, total: list.length, failed, current: path });
  }

  // 3) 有失败 → 不核对，把失败清单抛给 UI 重试
  if (failed.length) {
    const err = new Error("部分文件上传失败") as Error & {
      listingId: string;
      failed: { path: string; message: string }[];
      retryFiles: File[];
    };
    err.listingId = created.listing_id;
    err.failed = failed;
    err.retryFiles = list.filter((f) => !sent.has(f.webkitRelativePath!));
    throw err;
  }

  // 4) 核对清单
  const doneRes = await fetch(
    `/api/listings/${created.listing_id}/upload-complete`,
    { method: "POST", signal },
  );
  const body = await doneRes.json();
  if (!doneRes.ok) throw new Error(body.message || "清单核对失败");
  return {
    listingId: created.listing_id,
    rooms: body.rooms,
    photos: body.photos,
  };
}

/** empty-dir 工具：所有子文件夹里都没有可上传文件时给出明确原因。 */
export function explainEmptyFolder(files: FileList): string {
  const list = Array.from(files);
  const withRel = list.filter((f) => f.webkitRelativePath);
  if (!withRel.length) {
    return "没有选中任何文件——这可能是因为所选文件夹里只有空的子文件夹。浏览器不会上传空文件夹，请先把照片放进房间子文件夹。";
  }
  return "";
}
