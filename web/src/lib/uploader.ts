/**
 * 上传执行器（设计文档 §3.3）。
 *
 * 三条硬规则：
 *   * 全局上传并发预算 = 4（多套共享一个预算，不是每套 4）
 *   * 每个文件最多 3 次自动重试，退避 1s / 2s / 4s
 *   * 续传键 = (listing_id, 相对路径, 字节数)：服务端按此键跳过已到文件
 */
import type { ManifestItem } from "./batchUpload";

export interface UploadUnit {
  rootDirName: string;
  manifest: ManifestItem[];
  /** manifest 里每一项对应的原始 File（用于真正上传）。 */
  files: Map<string, File>;
  /** 本次上传会话的批次 id（§3.1）；空表示不归属任何批次。 */
  batchId?: string | null;
}
export interface FileFailure {
  path: string;
  message: string;
  attempts: number;
}

export interface UnitProgress {
  listingId: string;
  name: string;
  done: number;
  total: number;
  failed: FileFailure[];
  state: "uploading" | "done" | "failed";
}

export interface UploadOutcome {
  listingId: string;
  name: string;
  ok: boolean;
  failed: FileFailure[];
  message?: string;
}

const MAX_ATTEMPTS = 3;
const BACKOFF_MS = [1000, 2000, 4000];

const sleep = (ms: number) =>
  new Promise<void>((r) => window.setTimeout(r, ms));

/** 一个全局信号量：所有套共享 4 个上传位。 */
export class Semaphore {
  private free: number;

  constructor(slots: number) {
    this.free = Math.max(1, slots);
  }

  async acquire(): Promise<void> {
    while (this.free <= 0) {
      await sleep(40);
    }
    this.free -= 1;
  }

  release(): void {
    this.free += 1;
  }
}

async function putFile(
  listingId: string,
  relPath: string,
  file: File,
  signal?: AbortSignal,
): Promise<string> {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch(
    `/api/listings/${encodeURIComponent(listingId)}/files` +
      `?rel_path=${encodeURIComponent(relPath)}`,
    { method: "POST", body: fd, signal },
  );
  if (res.ok) return "";
  const body = await res.json().catch(() => ({}) as { message?: string });
  return body.message || `HTTP ${res.status}`;
}

/** 上传一套房源：先创建，再逐个上传，最后核对清单。 */
export async function uploadUnit(
  unit: UploadUnit,
  sem: Semaphore,
  onProgress: (p: UnitProgress) => void,
  signal?: AbortSignal,
): Promise<UploadOutcome> {
  const total = unit.manifest.length;
  const failed: FileFailure[] = [];
  let done = 0;

  const createRes = await fetch("/api/listings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      rootDirName: unit.rootDirName,
      manifest: unit.manifest,
      batch_id: unit.batchId || null,
    }),
    signal,
  });
  const created = await createRes.json().catch(() => ({}));
  if (!createRes.ok) {
    return {
      listingId: "",
      name: unit.rootDirName,
      ok: false,
      failed,
      message: created.message || "创建房源失败",
    };
  }
  const listingId: string = created.listing_id;

  const report = (state: UnitProgress["state"]) =>
    onProgress({ listingId, name: unit.rootDirName, done, total, failed, state });

  report("uploading");

  const tasks = unit.manifest.map(async (item) => {
    const file = unit.files.get(item.path);
    if (!file) {
      failed.push({ path: item.path, message: "浏览器没有拿到这个文件", attempts: 0 });
      done += 1;
      report("uploading");
      return;
    }
    await sem.acquire();
    try {
      let message = "";
      for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt += 1) {
        if (signal?.aborted) throw new DOMException("上传已取消", "AbortError");
        try {
          message = await putFile(listingId, item.path, file, signal);
          if (!message) break;
        } catch (e) {
          if ((e as DOMException).name === "AbortError") throw e;
          message = String(e);
        }
        if (attempt < MAX_ATTEMPTS) await sleep(BACKOFF_MS[attempt - 1]);
      }
      if (message) {
        failed.push({ path: item.path, message, attempts: MAX_ATTEMPTS });
      }
    } finally {
      sem.release();
      done += 1;
      report("uploading");
    }
  });

  await Promise.all(tasks);

  if (failed.length) {
    report("failed");
    return {
      listingId,
      name: unit.rootDirName,
      ok: false,
      failed,
      message: `${failed.length} 个文件上传失败，已自动重试 ${MAX_ATTEMPTS} 次。`,
    };
  }

  const doneRes = await fetch(
    `/api/listings/${encodeURIComponent(listingId)}/upload-complete`,
    { method: "POST", signal },
  );
  const body = await doneRes.json().catch(() => ({}));
  if (!doneRes.ok) {
    report("failed");
    return {
      listingId,
      name: unit.rootDirName,
      ok: false,
      failed,
      message: body.message || "清单核对失败",
    };
  }
  report("done");
  return { listingId, name: unit.rootDirName, ok: true, failed };
}
