/**
 * 成片放大播放（§5.2「成片版本」）
 *
 * 版本行里的播放器只有 168px 宽，浏览器原生控件在这个宽度下会把全屏按钮
 * 裁掉——用户没法把片子放大看清楚。所以这里给一条独立通路：
 *
 *   · 点缩略播放器或「放大播放」→ 铺满窗口的大播放器（不离开页面）；
 *   · 「全屏」用原生 requestFullscreen()，投影/电视上直接从这一层进全屏；
 *   · Esc 或点遮罩关闭，方向键 / 空格仍是播放器自己的行为。
 */
import { useEffect, useRef } from "react";

interface Props {
  src: string;
  downloadHref: string;
  title: string;
  meta: string;
  onClose: () => void;
}

export default function VideoLightbox({
  src,
  downloadHref,
  title,
  meta,
  onClose,
}: Props) {
  const videoRef = useRef<HTMLVideoElement | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    // 打开就把焦点交给播放器：空格播放/暂停、方向键快进都直接可用。
    videoRef.current?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const goFullscreen = () => {
    const el = videoRef.current;
    if (!el) return;
    if (document.fullscreenElement) void document.exitFullscreen();
    else void el.requestFullscreen?.();
  };

  return (
    <>
      <div className="scrim" onClick={onClose} />
      <div className="lightbox" role="dialog" aria-label="成片播放">
        <header>
          <div className="lightbox-title">
            <strong>{title}</strong>
            <span className="kv">{meta}</span>
          </div>
          <span className="spacer" />
          <button className="btn" onClick={goFullscreen}>
            全屏
          </button>
          <a className="btn" href={downloadHref} download>
            下载 MP4
          </a>
          <button className="btn ghost" onClick={onClose}>
            关闭
          </button>
        </header>

        <div className="lightbox-stage">
          <video
            ref={videoRef}
            src={src}
            controls
            autoPlay
            playsInline
            preload="auto"
            tabIndex={0}
          />
        </div>

        <footer>
          <span className="kv">
            Esc 关闭 · 空格播放/暂停 · 「全屏」进原生全屏（投影或电视上用）
          </span>
        </footer>
      </div>
    </>
  );
}
