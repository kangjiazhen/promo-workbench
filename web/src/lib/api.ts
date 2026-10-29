/**
 * One place that talks to the server.
 *
 * Every call goes through `request()`, so the统一错误信封
 * ({code, message, listing_id?, rel_path?, hint?}) is unwrapped the same
 * way everywhere and the UI always shows the server's message — the one
 * that says what to do next — instead of a raw status code.
 */
import type {
  ApiError,
  GlobalSettings,
  JobState,
  ListingDetail,
  ListingSummary,
  VersionInfo,
  WorkbenchPayload,
} from "./types";

export class ApiRequestError extends Error {
  code: string;
  listingId?: string;
  relPath?: string;
  hint?: string;

  constructor(body: ApiError, status: number) {
    super(body.message || `请求失败（HTTP ${status}）`);
    this.name = "ApiRequestError";
    this.code = body.code || `http_${status}`;
    this.listingId = body.listing_id;
    this.relPath = body.rel_path;
    this.hint = body.hint;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  const text = await res.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = { message: text };
    }
  }
  if (!res.ok) {
    const err = body as ApiError;
    throw new ApiRequestError(
      err && typeof err === "object"
        ? { ...err, message: err.message || `请求失败（HTTP ${res.status}）` }
        : { code: `http_${res.status}`, message: `请求失败（HTTP ${res.status}）` },
      res.status,
    );
  }
  return body as T;
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  /** 开一个批次（§3.1）：一次「开始上传」= 一个批次。 */
  openBatch: (label = "") =>
    request<{ batch_id: string; state: string }>(
      "/api/batches",
      json("POST", { label }),
    ),
  /** 「完成上传」→ 关闭批次，之后的「继续添加」开新批次。 */
  closeBatch: (batchId: string) =>
    request<{ batch_id: string; state: string }>(
      `/api/batch/${encodeURIComponent(batchId)}/close`,
      { method: "POST" },
    ),
  workbench: (state?: string) =>
    request<WorkbenchPayload>(
      `/api/workbench${state ? `?state=${encodeURIComponent(state)}` : ""}`,
    ),
  listing: (listingId: string) =>
    request<ListingDetail>(`/api/listings/${encodeURIComponent(listingId)}`),
  rename: (listingId: string, name: string) =>
    request<{ listing_id: string; name: string }>(
      `/api/listings/${encodeURIComponent(listingId)}/name`,
      json("PUT", { name }),
    ),
  autoSelect: (listingId: string) =>
    request<{ changed: number; selected: number }>(
      `/api/listings/${encodeURIComponent(listingId)}/auto-select`,
      { method: "POST" },
    ),
  setSelection: (
    listingId: string,
    selections: Record<string, boolean>,
    orders?: Record<string, number>,
  ) =>
    request<ListingSummary>(
      `/api/listings/${encodeURIComponent(listingId)}/selection`,
      json("PUT", { selections, orders }),
    ),
  setRooms: (
    listingId: string,
    rooms: {
      room_dir: string;
      display_name?: string;
      order_index?: number;
      drag_order?: boolean;
    }[],
  ) =>
    request<ListingDetail>(
      `/api/listings/${encodeURIComponent(listingId)}/rooms`,
      json("PUT", { rooms }),
    ),
  globalSettings: () => request<GlobalSettings>("/api/settings/global"),
  setGlobalSettings: (patch: Partial<GlobalSettings>) =>
    request<GlobalSettings>("/api/settings/global", json("PUT", patch)),
  setListingSettings: (
    listingId: string,
    overrides: Record<string, unknown>,
  ) =>
    request<{ listing_id: string; overrides: Record<string, unknown> }>(
      `/api/listings/${encodeURIComponent(listingId)}/settings`,
      json("PUT", overrides),
    ),
  uploadMusic: (listingId: string, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<{ file_name: string; size_bytes: number }>(
      `/api/listings/${encodeURIComponent(listingId)}/music`,
      { method: "POST", body: fd },
    );
  },
  deleteMusic: (listingId: string) =>
    request<{ listing_id: string }>(
      `/api/listings/${encodeURIComponent(listingId)}/music`,
      { method: "DELETE" },
    ),
  renderBatch: (listingIds: string[], aspect: string) =>
    request<{ jobs: JobState[]; refused: { listing_id: string; name: string; reason: string }[] }>(
      "/api/renders/batch",
      json("POST", { listing_ids: listingIds, aspect }),
    ),
  renderOne: (listingId: string, aspect: string) =>
    request<{ jobs: JobState[] }>(
      `/api/listings/${encodeURIComponent(listingId)}/render`,
      json("POST", { aspect }),
    ),
  jobs: () => request<JobState[]>("/api/render-jobs"),
  cancelJob: (jobId: string) =>
    request<JobState>(`/api/render-jobs/${encodeURIComponent(jobId)}/cancel`, {
      method: "POST",
    }),
  retryJob: (jobId: string) =>
    request<JobState>(`/api/render-jobs/${encodeURIComponent(jobId)}/retry`, {
      method: "POST",
    }),
  versions: (listingId: string) =>
    request<VersionInfo[]>(
      `/api/listings/${encodeURIComponent(listingId)}/video-versions`,
    ),
  videoUrl: (versionId: string) =>
    `/api/video-versions/${encodeURIComponent(versionId)}/file`,
  /** 打开这套房源输出目录（本地应用，后端 os.startfile；§5.4）。 */
  openFolder: (listingId: string) =>
    request<{ opened: boolean }>(
      `/api/listings/${encodeURIComponent(listingId)}/open-folder`,
      { method: "POST" },
    ),
  thumbnailUrl: (photoId: string, size = 320) =>
    `/api/photos/${encodeURIComponent(photoId)}/thumbnail?size=${size}`,
};

export type Api = typeof api;
