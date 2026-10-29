/**
 * 照片放大预览（§5.2「放大预览」）
 *
 * 点开一张照片先看清楚，再决定留不留——在 148px 的缩略图上判断虚不虚
 * 是不现实的。左右箭头在同一房间内翻页，Enter/Esc 关闭。
 */
import { useEffect } from "react";
import { api } from "../lib/api";
import type { Photo } from "../lib/types";

interface Props {
  photos: Photo[];
  index: number;
  roomName: string;
  busy: boolean;
  onIndex: (next: number) => void;
  onToggle: (photo: Photo) => void;
  onClose: () => void;
}

const FLAG_TEXT: Record<string, string> = {
  模糊: "模糊",
  过暗: "偏暗",
  过曝: "偏亮",
  重复: "与其它照片重复",
  无法读取: "无法读取",
  同房间多拍: "同房间有更多角度",
};

export default function PhotoLightbox({
  photos,
  index,
  roomName,
  busy,
  onIndex,
  onToggle,
  onClose,
}: Props) {
  const photo = photos[index];

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowLeft" && index > 0) onIndex(index - 1);
      if (e.key === "ArrowRight" && index < photos.length - 1)
        onIndex(index + 1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [index, photos.length, onIndex, onClose]);

  if (!photo) return null;

  return (
    <>
      <div className="scrim" onClick={onClose} />
      <div className="lightbox" role="dialog" aria-label="照片预览">
        <header>
          <div className="lightbox-title">
            <strong>{roomName}</strong>
            <span className="kv">
              {index + 1} / {photos.length} · {photo.file_name}
            </span>
          </div>
          <span className="spacer" />
          <button
            className={`btn${photo.selected ? " ghost" : ""}`}
            disabled={busy}
            onClick={() => onToggle(photo)}
          >
            {photo.selected ? "排除这张" : "保留这张"}
          </button>
          <button className="btn ghost" onClick={onClose}>
            关闭
          </button>
        </header>

        <div className="lightbox-stage">
          <button
            className="btn ghost lightbox-nav"
            disabled={index === 0}
            onClick={() => onIndex(index - 1)}
            aria-label="上一张"
          >
            ‹
          </button>
          <img
            src={api.thumbnailUrl(photo.photo_id, 1600)}
            alt={photo.file_name}
          />
          <button
            className="btn ghost lightbox-nav"
            disabled={index === photos.length - 1}
            onClick={() => onIndex(index + 1)}
            aria-label="下一张"
          >
            ›
          </button>
        </div>

        <footer>
          <span className={`badge ${photo.selected ? "complete" : ""}`}>
            {photo.selected ? "已保留" : "已排除"}
          </span>
          {photo.manual && <span className="badge">手动调整</span>}
          {photo.flags.map((f) => (
            <span className="flag" key={f}>
              {FLAG_TEXT[f] ?? f}
            </span>
          ))}
          <span className="spacer" />
          <span className="kv mono">
            {photo.width}×{photo.height} ·{" "}
            {(photo.size_bytes / 1024).toFixed(0)} KB
          </span>
        </footer>
      </div>
    </>
  );
}
