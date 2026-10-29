/**
 * Storyboard contract shared by the renderer and the Python backend.
 *
 * Deliberately **snake_case**, byte-for-byte the same keys the frozen
 * storyboard JSON uses (`property_promo/storyboard/schema.py`, version 1).
 * No translation layer means the renderer cannot drift from the board:
 * what the backend freezes is literally what Remotion reads.
 */

export type MoveKind =
  | "static"
  | "push"
  | "pull"
  | "truck"
  | "truck-native"
  | "tilt-native";

export interface Move {
  z0: number;
  z1: number;
  x0: number;
  x1: number;
  y0: number;
  y1: number;
  ease: string;
  kind: MoveKind;
}

export interface Contact {
  name?: string;
  phone?: string;
  wechat?: string;
  agency?: string;
  closing?: string;
  qr_note?: string;
}

export interface Shot {
  index: number;
  kind: "opener" | "shot" | "outro";
  photo_id?: string;
  room?: string;
  /** 画面上要印的房间名；null / 缺省 = 这一张不印（每个房间只在首张印）。 */
  room_label?: string | null;
  file_name?: string;
  rel_path?: string;
  /** Listing-media-relative path of the sanitised file (§4). */
  media_path?: string;
  width?: number;
  height?: number;
  duration_s: number;
  title?: string;
  aspect?: string;
  contact?: Contact;
  move: Move;
}

export type Storyboard = {
  version: number;
  listing_id: string;
  name: string;
  aspect: string;
  canvas: { width: number; height: number };
  fps: number;
  created_at: number;
  contact: Contact;
  music: { mode: string; track?: string; file?: string };
  shots: Shot[];
  audio: { narration: unknown[]; bed: string | null };
}

/** Props handed to the composition by the Node driver. */
export type PromoProps = {
  storyboard: Storyboard;
  /** Listing-media-relative path of the music bed, or null for silence. */
  bed_file: string | null;
}
