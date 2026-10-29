/**
 * 画幅选项（§5.2）——唯一来源是 `shared/constants.json`。
 *
 * 三个地方要展示画幅（全局设置抽屉 / 单套覆盖 / 底栏批量生成），以前各写了
 * 一份「16:9 横版 1920×1080」的字符串表，加一个画幅就得改三处、还容易改漏。
 * 现在全部从生成的 `sharedConstants.ts` 推出来。
 */
import { SHARED_CONSTANTS } from "./sharedConstants";

export interface AspectOption {
  /** 传给 API 的值，也是键，如 "16:9"。 */
  value: string;
  /** 「16:9 横版」。 */
  label: string;
  /** 「16:9 横版 1920×1080」，下拉框用。 */
  text: string;
  width: number;
  height: number;
}

export const ASPECT_OPTIONS: AspectOption[] = Object.entries(
  SHARED_CONSTANTS.aspects,
).map(([value, a]) => ({
  value,
  label: a.label,
  text: `${a.label} ${a.width}×${a.height}`,
  width: a.width,
  height: a.height,
}));

/** value → 「16:9 横版 1920×1080」。 */
export const ASPECT_TEXT: Record<string, string> = Object.fromEntries(
  ASPECT_OPTIONS.map((a) => [a.value, a.text]),
);

export const DEFAULT_ASPECT: string = SHARED_CONSTANTS.default_aspect;
