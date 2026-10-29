/**
 * The workbench payload shapes — mirrored from the server's
 * api/payloads.py. Keep the two in sync: one payload serves the task
 * list, the detail panel and the poll endpoint.
 */

export type ListingState =
  | "created"
  | "uploading"
  | "awaiting_confirm"
  | "queued"
  | "rendering"
  | "complete"
  | "failed"
  | "cancelled";

export interface JobState {
  job_id: string;
  listing_id: string;
  listing_name: string;
  state: "queued" | "rendering" | "complete" | "failed" | "cancelled";
  progress: number;
  stage: "preparing" | "rendering" | "checking" | null;
  error: string | null;
  aspect: string;
  batch_id: string | null;
  created_at: number;
  updated_at: number;
  started_at: number | null;
  finished_at: number | null;
}

export interface Photo {
  photo_id: string;
  file_name: string;
  rel_path: string;
  room: string;
  size_bytes: number;
  width: number;
  height: number;
  selected: boolean;
  manual: boolean;
  manual_order: number | null;
  flags: string[];
}

export interface Room {
  room_dir: string;
  display_name: string;
  order: number;
  photos: Photo[];
}

export interface VersionInfo {
  version_id: string;
  job_id: string;
  aspect: string;
  duration_s: number;
  size_bytes: number;
  created_at: number;
}

export interface ListingSummary {
  listing_id: string;
  name: string;
  root_dir_name: string;
  state: ListingState;
  state_label: string;
  progress: number;
  stage: string | null;
  error: string | null;
  room_count: number;
  photos: number;
  selected: number;
  expected: number;
  batch_id: string | null;
  created_at: number;
  updated_at: number;
  can_render: boolean;
  duration_estimate_s: number;
}

export interface ListingDetail extends ListingSummary {
  rooms: Room[];
  settings: GlobalSettings;
  overrides: Record<string, unknown>;
  versions: VersionInfo[];
  music_file: { file_name: string; size_bytes: number } | null;
  missing_media: string[];
  media_warning: string;
}

export interface Contact {
  name: string;
  phone: string;
  wechat: string;
  agency: string;
  closing: string;
  qr_note: string;
}

export interface MotionSettings {
  enabled: boolean;
  pattern: string;
  zoom: number;
  drift: number;
  max_upscale: number;
  ease: string;
}

export interface MusicSettings {
  mode: "none" | "synth" | "custom";
  track: string;
}

export interface GlobalSettings {
  contact: Contact;
  /** 片头大字。默认「房源实拍」——绝不用文件夹名，否则会把地址印上画面。 */
  title: string;
  aspect: string;
  /** 每个房间的第一张照片左下角印房间名（名字=上传文件夹名或改过的显示名）。 */
  room_labels: boolean;
  music: MusicSettings;
  motion: MotionSettings;
  render_concurrency: number;
  upload_concurrency: number;
  max_versions_per_listing: number;
}

export interface WorkbenchPayload {
  listings: ListingSummary[];
  counts: Record<string, number>;
  settings: GlobalSettings;
  queue: {
    active: number;
    concurrency: number;
    jobs: JobState[];
  };
  batches: { batch_id: string; label: string; state: string; created_at: number }[];
}

export interface ApiError {
  code: string;
  message: string;
  listing_id?: string;
  rel_path?: string;
  hint?: string;
}
