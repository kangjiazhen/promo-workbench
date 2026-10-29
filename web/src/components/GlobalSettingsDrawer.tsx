/**
 * 全局设置抽屉（§5.3）
 *
 * 生效时机：全局默认在上传时给新套做初始值；真正渲染取值在点击「生成」
 * 那一刻冻结进 Storyboard。所以改这里不会影响已经在队列里的套。
 */
import { useEffect, useState } from "react";
import { api, ApiRequestError } from "../lib/api";
import { ASPECT_OPTIONS, DEFAULT_ASPECT } from "../lib/aspects";
import type { GlobalSettings } from "../lib/types";

interface Props {
  open: boolean;
  settings: GlobalSettings | null;
  onClose: () => void;
  onSaved: () => void;
  onToast: (m: string, k?: "ok" | "err") => void;
}

export default function GlobalSettingsDrawer({
  open,
  settings,
  onClose,
  onSaved,
  onToast,
}: Props) {
  const [contact, setContact] = useState({
    name: "",
    phone: "",
    wechat: "",
    agency: "",
    closing: "",
    qr_note: "",
  });
  const [aspect, setAspect] = useState(DEFAULT_ASPECT);
  const [title, setTitle] = useState("房源实拍");
  const [roomLabels, setRoomLabels] = useState(true);
  const [musicMode, setMusicMode] = useState<"none" | "synth" | "custom">(
    "synth",
  );
  const [renderConcurrency, setRenderConcurrency] = useState(2);
  const [uploadConcurrency, setUploadConcurrency] = useState(4);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!settings) return;
    setContact({
      name: settings.contact.name ?? "",
      phone: settings.contact.phone ?? "",
      wechat: settings.contact.wechat ?? "",
      agency: settings.contact.agency ?? "",
      closing: settings.contact.closing ?? "",
      qr_note: settings.contact.qr_note ?? "",
    });
    setAspect(settings.aspect);
    setTitle(settings.title ?? "房源实拍");
    setRoomLabels(settings.room_labels !== false);
    setMusicMode(settings.music.mode === "none" || settings.music.mode === "custom"
      ? settings.music.mode
      : "synth");
    setRenderConcurrency(settings.render_concurrency);
    setUploadConcurrency(settings.upload_concurrency);
  }, [settings]);

  if (!open) return null;

  const save = async () => {
    setBusy(true);
    try {
      await api.setGlobalSettings({
        contact,
        title,
        aspect,
        room_labels: roomLabels,
        music: { mode: musicMode, track: settings?.music.track ?? "warm" },
        render_concurrency: renderConcurrency,
        upload_concurrency: uploadConcurrency,
      });
      onSaved();
      onToast("全局默认已保存。已在队列中的套不受影响（渲染取值在点生成时冻结）。", "ok");
    } catch (e) {
      onToast(
        e instanceof ApiRequestError ? e.message : "保存全局设置失败。",
        "err",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="drawer">
        <header>
          <h2>全局默认设置</h2>
          <button className="btn ghost" onClick={onClose}>
            关闭
          </button>
        </header>
        <div className="drawer-body">
          <p className="kv" style={{ marginBottom: 14 }}>
            这些是新上传房源的初始值。真正生成视频时用的取值，在点击「批量生成」
            那一刻冻结，所以现在改不会影响已经在渲染的套。
          </p>

          <h3 style={{ fontSize: 13, color: "var(--text-dim)" }}>片尾联系方式</h3>
          <div className="grid2">
            <div className="field">
              <label>姓名</label>
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
          <div className="field">
            <label>二维码说明（可选）</label>
            <input
              className="input"
              value={contact.qr_note}
              onChange={(e) => setContact({ ...contact, qr_note: e.target.value })}
            />
          </div>
          {!contact.name && !contact.phone && !contact.wechat &&
            !contact.agency && !contact.closing && (
              <div className="notice warn">
                还没填联系方式：片尾只会显示一句「欢迎预约看房」。填上姓名和电话，
                客户才知道找谁。
              </div>
            )}

          <h3 style={{ fontSize: 13, color: "var(--text-dim)", marginTop: 18 }}>
            视频参数
          </h3>
          <div className="field">
            <label>片头大字</label>
            <input
              className="input"
              value={title}
              maxLength={24}
              onChange={(e) => setTitle(e.target.value)}
            />
            <div className="kv" style={{ marginTop: 4 }}>
              默认「房源实拍」。这里不要填地址或房号——片子是要发给客户的。
            </div>
          </div>
          <div className="field">
            <label>默认画幅</label>
            <select
              className="select"
              value={aspect}
              onChange={(e) => setAspect(e.target.value)}
            >
              {ASPECT_OPTIONS.map((a) => (
                <option key={a.value} value={a.value}>
                  {a.text}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label
              style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}
            >
              <input
                type="checkbox"
                checked={roomLabels}
                onChange={(e) => setRoomLabels(e.target.checked)}
              />
              画面上印房间名
            </label>
            <div className="kv" style={{ marginTop: 4 }}>
              每个房间的第一张照片左下角显示房间名，名字取上传时的文件夹名（也可在
              详情面板里改）。不想让客户看到房间构成时关掉。
            </div>
          </div>
          <div className="field">
            <label>默认音乐</label>
            <select
              className="select"
              value={musicMode}
              onChange={(e) =>
                setMusicMode(
                  e.target.value === "none" || e.target.value === "custom"
                    ? e.target.value
                    : "synth",
                )
              }
            >
              <option value="none">无音乐</option>
              <option value="synth">内置合成</option>
              <option value="custom">自定义上传（在详情面板里传）</option>
            </select>
          </div>

          <h3 style={{ fontSize: 13, color: "var(--text-dim)", marginTop: 18 }}>
            并发
          </h3>
          <div className="grid2">
            <div className="field">
              <label>渲染并发（同时渲染几套）</label>
              <input
                className="input"
                type="number"
                min={1}
                max={8}
                value={renderConcurrency}
                onChange={(e) =>
                  setRenderConcurrency(Number(e.target.value) || 1)
                }
              />
            </div>
            <div className="field">
              <label>上传并发（全局共享）</label>
              <input
                className="input"
                type="number"
                min={1}
                max={8}
                value={uploadConcurrency}
                onChange={(e) =>
                  setUploadConcurrency(Number(e.target.value) || 1)
                }
              />
            </div>
          </div>

          <button className="btn primary" disabled={busy} onClick={() => void save()}>
            保存全局设置
          </button>
        </div>
      </aside>
    </>
  );
}
