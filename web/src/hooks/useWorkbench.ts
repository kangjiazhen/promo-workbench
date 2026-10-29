/**
 * 工作台的数据轮询（设计文档 §5.4：DB + 1s 轮询）。
 *
 * 所有状态都来自服务端 SQLite，所以刷新页面、重开页面、换台机器打开
 * 都是同一份状态；前端不缓存任何「真相」。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiRequestError } from "../lib/api";
import type { ListingDetail, ListingSummary, WorkbenchPayload } from "../lib/types";

export interface WorkbenchState {
  data: WorkbenchPayload | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  /** 详情面板的即时内容，优先于轮询结果。 */
  detail: ListingDetail | null;
  loadDetail: (listingId: string) => Promise<void>;
  setDetail: (detail: ListingDetail | null) => void;
}

/** 判断轮询拿到的概要是否说明「详情值得重取」。 */
function detailStale(prev: ListingSummary, next: ListingSummary): boolean {
  return (
    prev.state !== next.state ||
    prev.selected !== next.selected ||
    prev.photos !== next.photos ||
    prev.room_count !== next.room_count ||
    Math.abs(prev.progress - next.progress) > 1e-6 ||
    (prev.error ?? "") !== (next.error ?? "")
  );
}

export function useWorkbench(pollMs = 1000): WorkbenchState {
  const [data, setData] = useState<WorkbenchPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [detail, setDetail] = useState<ListingDetail | null>(null);
  const detailIdRef = useRef<string | null>(null);
  /** 上一次轮询里「当前选中套」的概要，用来判断详情要不要重取。 */
  const lastSummaryRef = useRef<ListingSummary | null>(null);
  const alive = useRef(true);
  const loadDetailRef = useRef<((id: string) => Promise<void>) | null>(null);

  const loadDetail = useCallback(async (listingId: string) => {
    detailIdRef.current = listingId;
    try {
      const d = await api.listing(listingId);
      if (!alive.current) return;
      setDetail(d);
      lastSummaryRef.current = d;
    } catch (e) {
      if (!alive.current) return;
      setError(
        e instanceof ApiRequestError ? e.message : "读取房源详情失败。",
      );
    }
  }, []);
  loadDetailRef.current = loadDetail;

  const refresh = useCallback(async () => {
    try {
      const payload = await api.workbench();
      if (!alive.current) return;
      setData(payload);
      setError(null);
      // 详情面板跟着当前选中的套刷新。只在概要真的变了（状态/进度/选片数）
      // 时才重取详情：详情带整面照片墙，每秒无条件重拉既浪费带宽，也会把
      // 用户正在编辑的房间名覆盖回去（§5.2）。
      const id = detailIdRef.current;
      if (id) {
        const fresh = payload.listings.find(
          (l: ListingSummary) => l.listing_id === id,
        );
        if (fresh) {
          const last = lastSummaryRef.current;
          if (!last || last.listing_id !== fresh.listing_id) {
            lastSummaryRef.current = fresh;
          } else if (detailStale(last, fresh)) {
            lastSummaryRef.current = fresh;
            void loadDetailRef.current?.(id);
          }
        }
      }
    } catch (e) {
      if (!alive.current) return;
      setError(
        e instanceof ApiRequestError ? e.message : "无法连接服务端，请确认后端在运行。",
      );
    } finally {
      if (alive.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    void refresh();
    const timer = window.setInterval(() => {
      void refresh();
    }, pollMs);
    return () => {
      alive.current = false;
      window.clearInterval(timer);
    };
  }, [pollMs, refresh]);

  return { data, loading, error, refresh, detail, loadDetail, setDetail };
}
