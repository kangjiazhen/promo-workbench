/**
 * PropertyPromo composition — renders one frozen storyboard.
 *
 * Rules that came out of the verified old stack, kept because each one was
 * learned from a real complaint:
 *
 *  - movement is a *budgeted* zoom + drift, never a pan over spare pixels
 *    (a pan reads as static and the film felt dead);
 *  - `kind: "static"` is an explicit locked frame — it must never fall
 *    through to a legacy pan path;
 *  - the horizon is measured against the element's own size, so the frame
 *    is always fully covered: no letterboxing, ever;
 *  - nothing on screen names the address or the local path (§4). Room names
 *    *are* printable — they come from the upload folder, they appear on every
 *    photo of that room, and they are frozen per shot by the planner
 *    (`shot.room_label`), so the renderer never invents or looks up a name. */
import React from "react";
import {
  AbsoluteFill,
  Audio,
  Img,
  Sequence,
  interpolate,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import type { Move, PromoProps, Shot, Storyboard } from "./contracts";

/* ------------------------------------------------------------------ type */

const SANS =
  '"Microsoft YaHei Light","Microsoft YaHei","PingFang SC","Hiragino Sans GB",' +
  '-apple-system,"Segoe UI",sans-serif';

const INK = "#0b0b0d";
const PAPER = "#f5f2ec";
const BRASS = "#c8a24a";
const MUTED = "#9a958a";

/** Seconds → frames, rounded, never zero. */
const toFrames = (seconds: number, fps: number): number =>
  Math.max(1, Math.round(seconds * fps));

const EASE: Record<string, (t: number) => number> = {
  lin: (t) => t,
  in: (t) => t * t,
  out: (t) => 1 - (1 - t) * (1 - t),
  "in-out": (t) => (t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2),
};

const clamp01 = (t: number): number => Math.min(1, Math.max(0, t));

const ease = (name: string, t: number): number =>
  (EASE[name] ?? EASE.lin)(clamp01(t));

/* ----------------------------------------------------------------- scrims */

/** Vignette. Purely depth — it also hides the soft corners of a big zoom. */
const Vignette: React.FC = () => (
  <AbsoluteFill
    style={{
      pointerEvents: "none",
      background:
        "radial-gradient(120% 120% at 50% 48%, rgba(0,0,0,0) 46%, " +
        "rgba(0,0,0,0.26) 76%, rgba(0,0,0,0.55) 100%)",
    }}
  />
);

/* ------------------------------------------------------------- room label */

/**
 * The room name, bottom-left, on **every** photo of that room.
 *
 * Sized off the canvas width (like every other card) so 9:16 gets the same
 * optical weight as 16:9. It stays put across a cut when the room did not
 * change (a recurring fade would strobe), and fades in only when the name
 * itself is new. A soft shadow instead of a plate: a solid bar over a bright
 * window shot looks pasted on, a shadow just reads darker.
 */
const RoomLabel: React.FC<{
  text: string;
  canvas: { width: number; height: number };
  fade: boolean;
}> = ({ text, canvas, fade }) => {
  const frame = useCurrentFrame();
  const u = canvas.width / 1920;
  // Every photo carries its room's name, so on a hard cut the label must NOT
  // re-fade: a lower-third that blinks at every cut reads as a glitch. It
  // fades in only when the name itself is new (the tour entered a room).
  const appear = fade
    ? interpolate(frame, [0, 9], [0, 1], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      })
    : 1;

  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      <div
        style={{
          position: "absolute",
          left: Math.round(84 * u),
          bottom: Math.round(86 * u),
          display: "flex",
          alignItems: "center",
          opacity: appear,
          transform: `translateY(${(1 - appear) * 8 * u}px)`,
        }}
      >
        <div
          style={{
            width: Math.max(2, Math.round(3 * u)),
            height: Math.round(42 * u),
            backgroundColor: BRASS,
            opacity: 0.9,
            marginRight: Math.round(20 * u),
          }}
        />
        <div
          style={{
            fontFamily: SANS,
            fontWeight: 400,
            fontSize: Math.round(38 * u),
            lineHeight: 1.1,
            letterSpacing: "0.16em",
            textIndent: "0.16em",
            color: PAPER,
            whiteSpace: "nowrap",
            maxWidth: Math.round(canvas.width * 0.62),
            overflow: "hidden",
            textOverflow: "ellipsis",
            textShadow:
              "0 2px 18px rgba(0,0,0,0.72), 0 0 6px rgba(0,0,0,0.55)",
          }}
        >
          {text}
        </div>
      </div>
    </AbsoluteFill>
  );
};

/* ------------------------------------------------------------- photo shot */

const ShotFrame: React.FC<{
  shot: Shot;
  canvas: { width: number; height: number };
  index: number;
  count: number;
  freshRoom: boolean;
}> = ({ shot, canvas, index, count, freshRoom }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const total = toFrames(shot.duration_s, fps);
  const t = ease(shot.move?.ease ?? "lin", frame / total);
  const m: Move = shot.move;

  // Cover is computed here, not by object-fit: the element itself is sized
  // to the scaled image so a translate of a fraction of the surplus is
  // exact — object-fit would crop without exposing the surplus.
  const pw = Math.max(1, shot.width ?? canvas.width);
  const ph = Math.max(1, shot.height ?? canvas.height);
  const cover = Math.max(canvas.width / pw, canvas.height / ph);
  const z = m.z0 + (m.z1 - m.z0) * t;
  const dw = pw * cover * z;
  const dh = ph * cover * z;
  const surplusX = Math.max(0, dw - canvas.width);
  const surplusY = Math.max(0, dh - canvas.height);
  const x = Math.max(-1, Math.min(1, m.x0 + (m.x1 - m.x0) * t));
  const y = Math.max(-1, Math.min(1, m.y0 + (m.y1 - m.y0) * t));

  // Boundary dip: 5 frames at each edge. Short enough to read as a cut with
  // weight rather than a dissolve, and it never changes the timing.
  const dip = Math.min(5, Math.floor(total / 6));
  const opacity =
    dip > 1
      ? interpolate(frame, [0, dip, total - dip, total], [0, 1, 1, 0], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        })
      : 1;

  return (
    <AbsoluteFill style={{ backgroundColor: INK, overflow: "hidden" }}>
      <AbsoluteFill
        style={{
          opacity,
          transform: `translate(${(x * surplusX) / 2}px, ${(y * surplusY) / 2}px)`,
        }}
      >
        <Img
          src={staticFile(shot.media_path ?? "")}
          style={{
            position: "absolute",
            left: (canvas.width - dw) / 2,
            top: (canvas.height - dh) / 2,
            width: dw,
            height: dh,
          }}
        />
      </AbsoluteFill>
      <Vignette />
      {(shot.room_label ?? "").trim() ? (
        <RoomLabel
          text={(shot.room_label as string).trim()}
          canvas={canvas}
          fade={freshRoom}
        />
      ) : null}
      {/* a hairline progress rule: gives the eye a fixed reference, so the
          camera move is felt instead of being lost in a still frame */}
      <AbsoluteFill style={{ justifyContent: "flex-end", opacity: 0.5 }}>
        <div
          style={{
            height: Math.max(1, Math.round(canvas.height * 0.0016)),
            width: `${((index / Math.max(1, count)) * 100).toFixed(2)}%`,
            backgroundColor: BRASS,
          }}
        />
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

/* ---------------------------------------------------------------- opener */

const Opener: React.FC<{
  shot: Shot;
  canvas: { width: number; height: number };
}> = ({ shot, canvas }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const total = toFrames(shot.duration_s, fps);
  const u = canvas.width / 1920;

  const kicker = interpolate(frame, [4, 22], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const title = interpolate(frame, [12, 34], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const rule = interpolate(frame, [26, 52], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const out = interpolate(frame, [total - 12, total], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill
      style={{
        backgroundColor: INK,
        opacity: out,
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <div
        style={{
          fontFamily: SANS,
          fontSize: Math.round(19 * u),
          letterSpacing: "0.42em",
          color: BRASS,
          opacity: kicker,
          transform: `translateY(${(1 - kicker) * 10 * u}px)`,
          marginBottom: Math.round(30 * u),
          textIndent: "0.42em",
        }}
      >
        实 景 看 房
      </div>
      <div
        style={{
          fontFamily: SANS,
          fontWeight: 300,
          fontSize: Math.round(96 * u),
          letterSpacing: "0.14em",
          color: PAPER,
          opacity: title,
          transform: `translateY(${(1 - title) * 18 * u}px)`,
          textIndent: "0.14em",
          lineHeight: 1.15,
        }}
      >
        {shot.title ?? ""}
      </div>
      <div
        style={{
          marginTop: Math.round(34 * u),
          width: Math.round(220 * u * rule),
          height: Math.max(1, Math.round(canvas.width * 0.0014)),
          backgroundColor: BRASS,
          opacity: rule * 0.85,
        }}
      />
    </AbsoluteFill>
  );
};

/* ----------------------------------------------------------------- outro */

const Outro: React.FC<{
  shot: Shot;
  canvas: { width: number; height: number };
}> = ({ shot, canvas }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const total = toFrames(shot.duration_s, fps);
  const u = canvas.width / 1920;
  const c = shot.contact ?? {};

  const head = interpolate(frame, [0, 16], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const rule = interpolate(frame, [10, 32], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const rowsIn = interpolate(frame, [18, 46], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const fade = interpolate(frame, [0, 8], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const big = (c.name || "").trim();
  const rows: string[] = [];
  if ((c.phone || "").trim()) rows.push(`电话　${c.phone}`);
  if ((c.wechat || "").trim()) rows.push(`微信　${c.wechat}`);
  if ((c.agency || "").trim()) rows.push(c.agency as string);
  if ((c.closing || "").trim()) rows.push(c.closing as string);

  return (
    <AbsoluteFill
      style={{
        backgroundColor: INK,
        opacity: fade,
        alignItems: "center",
        justifyContent: "center",
        flexDirection: "column",
      }}
    >
      <div
        style={{
          fontFamily: SANS,
          fontSize: Math.round(17 * u),
          letterSpacing: "0.4em",
          color: BRASS,
          opacity: head,
          textIndent: "0.4em",
          marginBottom: Math.round(26 * u),
        }}
      >
        咨 询 与 看 房
      </div>
      {big ? (
        <div
          style={{
            fontFamily: SANS,
            fontWeight: 300,
            fontSize: Math.round(56 * u),
            letterSpacing: "0.12em",
            color: PAPER,
            opacity: head,
            textIndent: "0.12em",
          }}
        >
          {big}
        </div>
      ) : null}
      <div
        style={{
          margin: `${Math.round(26 * u)}px 0`,
          width: Math.round(160 * u * rule),
          height: Math.max(1, Math.round(canvas.width * 0.0014)),
          backgroundColor: BRASS,
          opacity: rule * 0.8,
        }}
      />
      {rows.map((line, i) => (
        <div
          key={`${line}-${i}`}
          style={{
            fontFamily: SANS,
            fontSize: Math.round(27 * u),
            letterSpacing: "0.08em",
            color: i === 0 ? PAPER : MUTED,
            opacity: rowsIn,
            marginBottom: Math.round(14 * u),
            whiteSpace: "pre",
          }}
        >
          {line}
        </div>
      ))}
      {(c.qr_note || "").trim() ? (
        <div
          style={{
            fontFamily: SANS,
            fontSize: Math.round(18 * u),
            letterSpacing: "0.14em",
            color: "#6f6a60",
            opacity: rowsIn * 0.9,
            marginTop: Math.round(18 * u),
          }}
        >
          {c.qr_note}
        </div>
      ) : null}
    </AbsoluteFill>
  );
};

/* ------------------------------------------------------------------- film */

export const PropertyPromo: React.FC<PromoProps> = ({ storyboard, bed_file }) => {
  const { fps } = useVideoConfig();
  const canvas = storyboard.canvas;
  const shots = storyboard.shots ?? [];
  const photoCount = shots.filter((s) => s.kind === "shot").length;

  let offset = 0;
  let photoIndex = 0;

  return (
    <AbsoluteFill style={{ backgroundColor: INK }}>
      {shots.map((shot, i) => {
        const from = offset;
        const duration = toFrames(shot.duration_s, fps);
        offset += duration;
        const idx = shot.kind === "shot" ? photoIndex++ : 0;
        // New name on screen = fade the lower-third in; same name as the
        // previous photo = hold it steady across the cut.
        const freshRoom = i === 0 || shots[i - 1].room !== shot.room;
        return (
          <Sequence key={shot.index} from={from} durationInFrames={duration}>
            {shot.kind === "opener" ? (
              <Opener shot={shot} canvas={canvas} />
            ) : shot.kind === "outro" ? (
              <Outro shot={shot} canvas={canvas} />
            ) : (
              <ShotFrame
                shot={shot}
                canvas={canvas}
                index={idx}
                count={photoCount}
                freshRoom={freshRoom}
              />
            )}
          </Sequence>
        );
      })}
      {bed_file ? <Audio src={staticFile(bed_file)} /> : null}
    </AbsoluteFill>
  );
};

export type { Storyboard };
