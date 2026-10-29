/**
 * One still frame — the dev/QA companion to `render-job.mjs`.
 *
 *     node still-job.mjs <props.json> <out.png> [frame]
 *
 * `props.json` is the same shape `render-job.mjs` hands the composition
 * (`storyboard` + `bed_file` + `asset_roots` + `chromium`), except that
 * `media_path` values are already prefixed with the mount name — this tool
 * renders exactly what the board says instead of rewriting it.
 *
 * Used to eyeball a frame, and by `tests/test_room_label_still.py` to prove a
 * drawn element (the room name) really reaches the pixels.
 */
import { bundle } from "@remotion/bundler";
import { renderStill, selectComposition } from "@remotion/renderer";
import fs from "node:fs";
import path from "node:path";

/**
 * Remove a junction/symlink, never the directory behind it.
 *
 * `rmSync(recursive)` on a Windows junction walks *through* the link and
 * deletes the listing's photos — that is exactly how the old stack lost a
 * folder's worth of photos. `rmdir` drops the reparse point itself.
 */
const dropLink = (link) => {
  if (!fs.existsSync(link) && !fs.lstatSync(link, { throwIfNoEntry: false })) {
    return false;
  }
  try {
    fs.rmdirSync(link);
    return true;
  } catch {
    /* not an empty dir — maybe a file symlink */
  }
  try {
    fs.unlinkSync(link);
    return true;
  } catch {
    return false;
  }
};

const [, , propsPath, outPath, frameArg] = process.argv;
if (!propsPath || !outPath) {
  console.error("usage: node still-job.mjs <props.json> <out.png> [frame]");
  process.exit(2);
}
const inputProps = JSON.parse(fs.readFileSync(propsPath, "utf8"));
const bundleDir = path.resolve(".cache/bundle");
if (!fs.existsSync(path.join(bundleDir, "index.html"))) {
  await bundle({ entryPoint: path.resolve("src/index.tsx"), outDir: bundleDir });
}
const publicDir = path.join(bundleDir, "public");
fs.mkdirSync(publicDir, { recursive: true });
for (const root of inputProps.asset_roots ?? []) {
  const link = path.join(publicDir, root.name);
  dropLink(link);
  fs.symlinkSync(root.path, link, "junction");
}
const composition = await selectComposition({
  serveUrl: bundleDir,
  id: "PropertyPromo",
  inputProps,
  browserExecutable: inputProps.chromium,
  chromeMode: "headless-shell",
});
await renderStill({
  composition,
  serveUrl: bundleDir,
  output: outPath,
  frame: Number(frameArg ?? 60),
  inputProps,
  browserExecutable: inputProps.chromium,
  chromeMode: "headless-shell",
});
console.log("STILL OK", outPath);
