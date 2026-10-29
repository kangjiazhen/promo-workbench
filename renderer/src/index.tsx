import { Composition, registerRoot } from "remotion";
import React from "react";
import { PropertyPromo } from "./PropertyPromo";
import type { PromoProps, Storyboard } from "./contracts";

const EMPTY_SHOT = {
  index: 0,
  kind: "opener" as const,
  duration_s: 1,
  move: {
    z0: 1,
    z1: 1,
    x0: 0,
    x1: 0,
    y0: 0,
    y1: 0,
    ease: "lin",
    kind: "static" as const,
  },
};

const EMPTY_BOARD: Storyboard = {
  version: 1,
  listing_id: "",
  name: "",
  aspect: "16:9",
  canvas: { width: 1920, height: 1080 },
  fps: 30,
  created_at: 0,
  contact: {},
  music: { mode: "none" },
  shots: [EMPTY_SHOT],
  audio: { narration: [], bed: null },
};

const EMPTY_PROPS: PromoProps = {
  storyboard: EMPTY_BOARD,
  bed_file: null,
};

const framesOf = (board: Storyboard): number => {
  const fps = board.fps || 30;
  return board.shots.reduce(
    (sum, shot) => sum + Math.max(1, Math.round(shot.duration_s * fps)),
    0,
  );
};

const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="PropertyPromo"
      component={PropertyPromo}
      durationInFrames={90}
      fps={30}
      width={1920}
      height={1080}
      defaultProps={EMPTY_PROPS}
      calculateMetadata={({ props }) => {
        const board = props.storyboard ?? EMPTY_BOARD;
        const fps = board.fps || 30;
        const canvas = board.canvas ?? EMPTY_BOARD.canvas;
        return {
          durationInFrames: Math.max(1, framesOf(board)),
          fps,
          width: canvas.width,
          height: canvas.height,
        };
      }}
    />
  );
};

registerRoot(RemotionRoot);
