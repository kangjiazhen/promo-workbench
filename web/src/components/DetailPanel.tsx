/**
 * 详情面板（右侧，§5.2）
 *
 * 上传完成后进入「待确认」：照片墙按房间分组，自动选片带标记，人工调整
 * 永远不会被自动推荐覆盖。房间名只在「每个房间的第一张」上印一次（可在
 * 这里或全局设置里关掉），名字取上传时的文件夹名，也可就地改。
 *
 * 这一屏要回答的唯一问题是「这套能不能生成、要留哪几张」，所以：
 *   · 点缩略图 = 放大看清楚（148px 判断不了虚不虚），不在缩略图上猜；
 *   · 拖照片 = 决定它们在片子里出现的先后，松手即存；
 *   · 拖房间 = 决定看房动线，松手即存；
 *   · 每个「没被推荐」的照片都写着原因，用户能一眼改回来。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiRequestError } from "../lib/api";
import { ASPECT_OPTIONS } from "../lib/aspects";
import type { ListingDetail, Photo, Room } from "../lib/types";
import PhotoLightbox from "./PhotoLightbox";
import VideoLightbox from "./VideoLightbox";

interface Props {
  detail: ListingDetail | null;
  onChanged: () => void;
  onToast: (message: string, kind?: "ok" | "err") => void;
}

const FLAG_TEXT: Record<string, string> = {
  模糊: "模糊",
  过暗: "偏暗",
  过曝: "偏亮",
  重复: "与其它照片重复",
  无法读取: "无法读取",
  同房间多拍: "同房间有更多角度",
};

/** 拖拽中的载荷：房间用 room_dir，照片用 photo_id。 */
type DragPayload = { kind: "room" | "photo"; roomDir: string; photoId?: string };

export default function DetailPanel({ detail, onChanged, onToast }: Props) {
  const [busy, setBusy] = useState(false);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [lightbox, setLightbox] = useState<{ roomDir: string; index: number } | null>(
    null,
  );
  /** 正在放大播放的成片版本 id（null = 没开弹层）。 */
  const [playing, setPlaying] = useState<string | null>(null);
  const drag = useRef<DragPayload | null>(null);

  const listingId = detail?.listing_id ?? "";

  const fail = useCallback(
    (e: unknown, fallback: string) => {
      onToast(e instanceof ApiRequestError ? e.message : fallback, "err");
    },
    [onToast],
  );

  const togglePhoto = async (photo: Photo) => {
    setBusy(true);
    try {
      await api.setSelection(listingId, { [photo.photo_id]: !photo.selected });
      onChanged();
    } catch (e) {
      fail(e, "保存选片失败。");
    } finally {
      setBusy(false);
    }
  };

  /** 打开这套房源的输出目录（§5.4）：路径不经过前端，由后端 os.startfile。 */
  const openFolder = async () => {
    try {
      await api.openFolder(listingId);
      onToast("已在文件管理器中打开输出目录。", "ok");
    } catch (e) {
      fail(e, "打开文件夹失败。");
    }
  };

  const renameRoom = async (room: Room, name: string) => {
    const trimmed = name.trim();
    if (!trimmed || trimmed === room.display_name) return;
    setBusy(true);
    try {
      await api.setRooms(listingId, [
        { room_dir: room.room_dir, display_name: trimmed, order_index: room.order },
      ]);
      onChanged();
    } catch (e) {
      fail(e, "重命名房间失败。");
    } finally {
      setBusy(false);
    }
  };

  /** 房间拖动后重排：把当前显示顺序整个写回去，避免只改一部分导致顺序歧义。 */
  const reorderRooms = async (from: string, to: string) => {
    if (!detail || from === to) return;
    const dirs = detail.rooms.map((r) => r.room_dir);
    const fromIdx = dirs.indexOf(from);
    const toIdx = dirs.indexOf(to);
    if (fromIdx < 0 || toIdx < 0) return;
    dirs.splice(toIdx, 0, ...dirs.splice(fromIdx, 1));
    setBusy(true);
    try {
      await api.setRooms(
        listingId,
        dirs.map((room_dir, i) => ({
          room_dir,
          order_index: i,
          drag_order: true,
        })),
      );
      onToast("房间顺序已更新，成片会按这个顺序走。", "ok");
      onChanged();
    } catch (e) {
      fail(e, "调整房间顺序失败。");
    } finally {
      setBusy(false);
    }
  };

  /** 房间内照片拖动后重排：写回该房间全部照片的顺序。 */
  const reorderPhotos = async (room: Room, fromId: string, toId: string) => {
    if (fromId === toId) return;
    const ids = room.photos.map((p) => p.photo_id);
    const fromIdx = ids.indexOf(fromId);
    const toIdx = ids.indexOf(toId);
    if (fromIdx < 0 || toIdx < 0) return;
    ids.splice(toIdx, 0, ...ids.splice(fromIdx, 1));
    const orders: Record<string, number> = {};
    ids.forEach((id, i) => (orders[id] = i));
    setBusy(true);
    try {
      await api.setSelection(listingId, {}, orders);
      onChanged();
    } catch (e) {
      fail(e, "调整照片顺序失败。");
    } finally {
      setBusy(false);
    }
  };

  const runAutoSelect = async () => {
    setBusy(true);
    try {
      const r = await api.autoSelect(listingId);
      onToast(
        `自动选片完成：现在选中 ${r.selected} 张（改动 ${r.changed} 张，你手动调整过的照片不会被覆盖）。`,
        "ok",
      );
      onChanged();
    } catch (e) {
      fail(e, "自动选片失败。");
    } finally {
      setBusy(false);
    }
  };

  const toggleCollapse = (roomDir: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(roomDir)) next.delete(roomDir);
      else next.add(roomDir);
      return next;
    });
  };

  if (!detail) {
    return (
      <section className="detail">
        <p className="empty">在左侧选一套房源，查看它的照片、设置和成片。</p>
      </section>
    );
  }

  const lightboxRoom =
    lightbox && detail.rooms.find((r) => r.room_dir === lightbox.roomDir);

  return (
    <section className="detail">
      <div className="detail-head">
        <div style={{ flex: 1, minWidth: 0 }}>
          <h2>{detail.name}</h2>
          <div className="sub">
            {detail.room_count} 个房间 · 已选 {detail.selected} 张 · 预计约{" "}
            {Math.round(detail.duration_estimate_s)} 秒 · 画幅{" "}
            {detail.settings.aspect}
          </div>
        </div>
        <span className={`badge ${detail.state}`}>{detail.state_label}</span>
      </div>

      {detail.media_warning && (
        <div className="notice err">{detail.media_warning}</div>
      )}
      {detail.state === "rendering" && (
        <div className="notice info">
          这套正在渲染中。现在的修改会在下次生成时生效（本次渲染用的是点「生成」那一刻冻结的设置）。
        </div>
      )}
      {detail.state === "awaiting_confirm" && (
        <div className="notice info">
          请确认照片：点开看大图、拖动能调顺序，然后勾选左侧的房源生成视频。
        </div>
      )}
      {detail.error && <div className="notice err">{detail.error}</div>}

      <div className="section">
        <h3>
          照片墙
          <span className="spacer" />
          <button className="btn" disabled={busy} onClick={runAutoSelect}>
            重新自动选片
          </button>
        </h3>
        {detail.rooms.length === 0 && (
          <p className="empty">这套房源还没有照片。</p>
        )}
        {detail.rooms.map((room) => {
          const isCollapsed = collapsed.has(room.room_dir);
          const picked = room.photos.filter((p) => p.selected).length;
          return (
            <div
              className={`room${isCollapsed ? " collapsed" : ""}`}
              key={room.room_dir}
              onDragOver={(e) => {
                if (drag.current?.kind === "room") e.preventDefault();
              }}
              onDrop={() => {
                const d = drag.current;
                if (d?.kind === "room") void reorderRooms(d.roomDir, room.room_dir);
                drag.current = null;
              }}
            >
              <div className="room-head">
                <span
                  className="drag-handle"
                  draggable
                  title="拖动调整房间顺序"
                  onDragStart={() => {
                    drag.current = { kind: "room", roomDir: room.room_dir };
                  }}
                  onDragEnd={() => {
                    drag.current = null;
                  }}
                >
                  ⠿
                </span>
                <button
                  className="btn ghost caret"
                  onClick={() => toggleCollapse(room.room_dir)}
                  aria-expanded={!isCollapsed}
                  title={isCollapsed ? "展开这个房间" : "收起这个房间"}
                >
                  {isCollapsed ? "▸" : "▾"}
                </button>
                <input
                  className="room-name"
                  defaultValue={room.display_name}
                  key={`${room.room_dir}:${room.display_name}`}
                  onBlur={(e) => void renameRoom(room, e.target.value)}
                  aria-label="房间名称"
                />
                <span className="kv">
                  {picked}/{room.photos.length} 张入选
                </span>
              </div>

              {!isCollapsed && (
                <div className="photos">
                  {room.photos.map((p, photoIndex) => (
                    <figure
                      className={`photo${p.selected ? "" : " off"}`}
                      key={p.photo_id}
                      style={{ margin: 0 }}
                      draggable
                      onDragStart={() => {
                        drag.current = {
                          kind: "photo",
                          roomDir: room.room_dir,
                          photoId: p.photo_id,
                        };
                      }}
                      onDragEnd={() => {
                        drag.current = null;
                      }}
                      onDragOver={(e) => {
                        if (drag.current?.kind === "photo") e.preventDefault();
                      }}
                      onDrop={() => {
                        const d = drag.current;
                        if (d?.kind === "photo" && d.roomDir === room.room_dir)
                          void reorderPhotos(room, d.photoId ?? "", p.photo_id);
                        drag.current = null;
                      }}
                    >
                      <img
                        className="thumb"
                        src={api.thumbnailUrl(p.photo_id)}
                        alt={p.file_name}
                        loading="lazy"
                        onClick={() =>
                          setLightbox({ roomDir: room.room_dir, index: photoIndex })
                        }
                        style={{ cursor: "zoom-in" }}
                      />
                      {p.flags.length > 0 && (
                        <div className="flags">
                          {p.flags.map((f) => (
                            <span
                              className={`flag${f === "重复" ? " dup" : ""}`}
                              key={f}
                            >
                              {FLAG_TEXT[f] ?? f}
                            </span>
                          ))}
                        </div>
                      )}
                      {p.manual && <div className="manual-mark">已手动</div>}
                      <figcaption className="cap">
                        <input
                          type="checkbox"
                          checked={p.selected}
                          onChange={() => void togglePhoto(p)}
                          aria-label={`保留 ${p.file_name}`}
                        />
                        <span className="fname" title={p.rel_path}>
                          {p.file_name}
                        </span>
                      </figcaption>
                    </figure>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div className="section">
        <h3>单套设置覆盖</h3>
        <div className="kv" style={{ marginBottom: 10 }}>
          留空即使用全局默认。这里改的值只影响这一套。
        </div>
        <ListingOverrides detail={detail} onChanged={onChanged} onToast={onToast} />
      </div>

      <div className="section">
        <h3>
          成片版本
          <span className="spacer" />
          <button className="btn" onClick={() => void openFolder()}>
            打开所在文件夹
          </button>
        </h3>
        {detail.versions.length === 0 && (
          <p className="empty">还没有生成过视频。</p>
        )}
        {detail.versions.map((v) => (
          <div className="version" key={v.version_id}>
            <video
              src={api.videoUrl(v.version_id)}
              controls
              preload="metadata"
              className="version-thumb"
              onClick={() => setPlaying(v.version_id)}
              title="点击放大播放"
            />
            <div className="grow">
              <div>
                {v.aspect} · {v.duration_s.toFixed(1)} 秒 ·{" "}
                {(v.size_bytes / 1024 / 1024).toFixed(1)} MB
              </div>
              <div className="kv mono">{v.version_id.slice(0, 12)}</div>
            </div>
            <button
              className="btn"
              onClick={() => setPlaying(v.version_id)}
              title="在页面里放大播放（可再进全屏）"
            >
              放大播放
            </button>
            <a
              className="btn"
              href={`${api.videoUrl(v.version_id)}?download=true`}
              download
            >
              下载 MP4
            </a>
          </div>
        ))}
      </div>

      {playing && (
        <VideoLightbox
          src={api.videoUrl(playing)}
          downloadHref={`${api.videoUrl(playing)}?download=true`}
          title={`${detail.name} · 成片`}
          meta={(() => {
            const v = detail.versions.find((x) => x.version_id === playing);
            return v
              ? `${v.aspect} · ${v.duration_s.toFixed(1)} 秒 · ${(
                  v.size_bytes /
                  1024 /
                  1024
                ).toFixed(1)} MB`
              : "";
          })()}
          onClose={() => setPlaying(null)}
        />
      )}

      {lightbox && lightboxRoom && (
        <PhotoLightbox
          photos={lightboxRoom.photos}
          index={lightbox.index}
          roomName={lightboxRoom.display_name}
          busy={busy}
          onIndex={(i) => setLightbox({ ...lightbox, index: i })}
          onToggle={(p) => void togglePhoto(p)}
          onClose={() => setLightbox(null)}
        />
      )}
    </section>
  );
}

/** 单套覆盖：联系人 / 画幅 / 音乐（§5.2）。 */
function ListingOverrides({
  detail,
  onChanged,
  onToast,
}: {
  detail: ListingDetail;
  onChanged: () => void;
  onToast: (m: string, k?: "ok" | "err") => void;
}) {
  const overrides = detail.overrides ?? {};
  const [contact, setContact] = useState({
    name: "",
    phone: "",
    wechat: "",
    agency: "",
    closing: "",
  });
  const [aspect, setAspect] = useState<string>("");
  const [musicMode, setMusicMode] = useState<string>("");
  const [title, setTitle] = useState<string>("");
  /** "" = 跟随全局；"on" / "off" = 这一套自己的选择。 */
  const [roomLabels, setRoomLabels] = useState<string>("");

  useEffect(() => {
    const c = (overrides.contact ?? {}) as Record<string, string>;
    setContact({
      name: c.name ?? "",
      phone: c.phone ?? "",
      wechat: c.wechat ?? "",
      agency: c.agency ?? "",
      closing: c.closing ?? "",
    });
    setAspect((overrides.aspect as string) ?? "");
    setTitle((overrides.title as string) ?? "");
    setRoomLabels(
      typeof overrides.room_labels === "boolean"
        ? overrides.room_labels
          ? "on"
          : "off"
        : "",
    );
    const m = (overrides.music ?? {}) as Record<string, string>;
    setMusicMode((m.mode as string) ?? "");
  }, [detail.listing_id, overrides]);

  const patch = async (body: Record<string, unknown>, label: string) => {
    try {
      await api.setListingSettings(detail.listing_id, body);
      onChanged();
    } catch (e) {
      onToast(
        e instanceof ApiRequestError ? e.message : `保存${label}失败。`,
        "err",
      );
    }
  };

  const contactChanged =
    (contact.name !== (overrides.contact as Record<string, string>)?.name) ||
    (contact.phone !== (overrides.contact as Record<string, string>)?.phone) ||
    (contact.wechat !== (overrides.contact as Record<string, string>)?.wechat) ||
    (contact.agency !== (overrides.contact as Record<string, string>)?.agency) ||
    (contact.closing !== (overrides.contact as Record<string, string>)?.closing);

  return (
    <div>
      <div className="grid2">
        <div className="field">
          <label>联系人姓名（留空用全局默认）</label>
          <input
            className="input"
            value={contact.name}
            onChange={(e) => setContact({ ...contact, name: e.target.value })}
          />
        </div>
        <div className="field">
          <label>电话</label>
          <input
            className="input"
            value={contact.phone}
            onChange={(e) => setContact({ ...contact, phone: e.target.value })}
          />
        </div>
        <div className="field">
          <label>微信</label>
          <input
            className="input"
            value={contact.wechat}
            onChange={(e) => setContact({ ...contact, wechat: e.target.value })}
          />
        </div>
        <div className="field">
          <label>机构</label>
          <input
            className="input"
            value={contact.agency}
            onChange={(e) => setContact({ ...contact, agency: e.target.value })}
          />
        </div>
      </div>
      <div className="field">
        <label>片尾结语</label>
        <input
          className="input"
          value={contact.closing}
          onChange={(e) => setContact({ ...contact, closing: e.target.value })}
        />
      </div>
      <div className="row" style={{ marginBottom: 12 }}>
        <button
          className="btn"
          disabled={!contactChanged}
          onClick={() =>
            void patch({ contact }, "联系人").then(() =>
              onToast("已保存这一套的联系人。", "ok"),
            )
          }
        >
          保存联系人覆盖
        </button>
        <button
          className="btn ghost"
          onClick={() => void patch({ contact: null }, "联系人")}
        >
          恢复全局默认
        </button>
      </div>

      <div className="field">
        <label>朋友圈/现场展板大字（片头标题，留空用全局默认）</label>
        <input
          className="input"
          value={title}
          maxLength={24}
          placeholder={`使用全局默认（${detail.settings.title ?? "房源实拍"}）`}
          onChange={(e) => setTitle(e.target.value)}
          onBlur={(e) => {
            const v = e.target.value.trim();
            if (v !== ((overrides.title as string) ?? "")) {
              void patch({ title: v || null }, "片头标题");
            }
          }}
        />
      </div>

      <div className="row" style={{ flexWrap: "wrap" }}>
        <div className="field" style={{ flex: "0 0 180px" }}>
          <label>画幅</label>
          <select
            className="select"
            value={aspect}
            onChange={(e) => {
              setAspect(e.target.value);
              void patch({ aspect: e.target.value || null }, "画幅");
            }}
          >
            <option value="">使用全局默认（{detail.settings.aspect}）</option>
            {ASPECT_OPTIONS.map((a) => (
              <option key={a.value} value={a.value}>
                {a.text}
              </option>
            ))}
          </select>
        </div>
        <div className="field" style={{ flex: "0 0 200px" }}>
          <label>房间名</label>
          <select
            className="select"
            value={roomLabels}
            onChange={(e) => {
              const v = e.target.value;
              setRoomLabels(v);
              void patch(
                { room_labels: v === "" ? null : v === "on" },
                "房间名开关",
              );
            }}
          >
            <option value="">
              使用全局默认（
              {detail.settings.room_labels === false ? "不印" : "印"}）
            </option>
            <option value="on">印在画面上</option>
            <option value="off">不印</option>
          </select>
        </div>
        <div className="field" style={{ flex: "0 0 200px" }}>
          <label>音乐</label>
          <select
            className="select"
            value={musicMode}
            onChange={(e) => {
              setMusicMode(e.target.value);
              void patch({ music: { mode: e.target.value || null } }, "音乐");
            }}
          >
            <option value="">使用全局默认（{detail.settings.music.mode}）</option>
            <option value="none">无音乐</option>
            <option value="synth">内置合成</option>
            <option value="custom">自定义上传</option>
          </select>
        </div>
        {detail.music_file && (
          <div className="kv" style={{ alignSelf: "flex-end", paddingBottom: 8 }}>
            自定义音乐：{detail.music_file.file_name}
            <button
              className="btn ghost danger"
              onClick={() =>
                void api.deleteMusic(detail.listing_id).then(() => {
                  onToast("已移除自定义音乐。", "ok");
                  onChanged();
                })
              }
            >
              移除
            </button>
          </div>
        )}
        {musicMode === "custom" && !detail.music_file && (
          <div className="field" style={{ flex: "0 0 260px" }}>
            <label>上传音乐（mp3/m4a/wav/ogg，≤20MB）</label>
            <input
              className="input"
              type="file"
              accept=".mp3,.m4a,.wav,.ogg,audio/*"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (!f) return;
                void api
                  .uploadMusic(detail.listing_id, f)
                  .then(() => {
                    onToast("音乐已上传，只对这一套生效。", "ok");
                    onChanged();
                  })
                  .catch((err) => {
                    onToast(
                      err instanceof ApiRequestError
                        ? err.message
                        : "上传音乐失败。",
                      "err",
                    );
                  });
              }}
            />
          </div>
        )}
      </div>
    </div>
  );
}

