/**
 * One render, one process — and N of these run at the same time.
 *
 * The Python worker pool spawns this driver per job:
 *
 *     node render-job.mjs --job <job.json>
 *
 * and reads newline-delimited JSON from stdout:
 *
 *     {"type":"progress","progress":0.42}
 *     {"type":"log","message":"..."}
 *     {"type":"done","duration_s":52.3}
 *     {"type":"error","message":"..."}
 *
 * Why a separate process per job, instead of one long-lived renderer:
 *  - **多开**: every job gets its own Chromium. Two films really encode at
 *    twice the rate on two sets of cores instead of queuing behind one
 *    browser that renders one frame at a time;
 *  - a render that hangs or dies can be killed without touching the API;
 *  - cancellation is a real process kill, not a flag someone might ignore.
 *
 * Static assets: `staticFile()` only serves from the bundle folder, so each
 * job mounts its own media folder there as a Windows junction (a link, no
 * copy, no admin rights) under a **unique name**. Two listings that both
 * contain `客厅/01.jpg` therefore cannot overwrite each other — the old stack
 * did exactly that and shipped the wrong room's photo.
 *
 * The bundle itself is built once and reused. A rebuild is only attempted
 * when no other job is mid-render, so a job never has its asset junction
 * deleted from under it by a concurrent webpack run.
 */
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { bundle } from "@remotion/bundler";
import {
  makeCancelSignal,
  renderMedia,
  selectComposition,
} from "@remotion/renderer";
import {
  activeMarker,
  bundleIsFresh,
  liveMarkers,
  otherRendersActive,
  sleep,
  sourceStamp,
} from "./bundle-cache.mjs";

const argOf = (name) => {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 ? process.argv[i + 1] : undefined;
};

const say = (obj) => {
  process.stdout.write(`${JSON.stringify(obj)}\n`);
};

/* ------------------------------------------------------------ the bundle */

/**
 * Return a usable bundle directory, building it only when it is safe.
 *
 * A stale bundle is harmless (it is still the same composition, one edit
 * behind); a bundle that is yanked away mid-render is not. So when another
 * job is rendering we keep the bundle we have rather than rebuilding.
 */
const ensureBundle = async ({ srcDir, bundleDir, onProgress }) => {
  const stamp = sourceStamp(srcDir);
  const built = fs.existsSync(path.join(bundleDir, "index.html"));

  if (bundleIsFresh(bundleDir, stamp)) {
    return { bundleDir, rebuilt: false, stale: false };
  }

  if (built) {
    // Try the lock for up to ~30 s, then decide.
    const lock = path.join(bundleDir, ".build.lock");
    let locked = false;
    for (let i = 0; i < 60; i += 1) {
      if (otherRendersActive(bundleDir)) break;
      try {
        fs.writeFileSync(lock, String(process.pid), { flag: "wx" });
        locked = true;
        break;
      } catch {
        await sleep(500);
      }
    }
    if (!locked) {
      // The other job may have finished rebuilding while we waited: re-check
      // the stamp before falling back, or a fresh build is wasted.
      if (bundleIsFresh(bundleDir, stamp)) {
        return { bundleDir, rebuilt: false, stale: false };
      }
      // Someone else is rendering or building: use what is on disk.
      if (built) return { bundleDir, rebuilt: false, stale: true };
      await sleep(1000);
      if (fs.existsSync(path.join(bundleDir, "index.html"))) {
        return { bundleDir, rebuilt: false, stale: true };
      }
    } else {
      try {
        if (fs.existsSync(lock)) fs.unlinkSync(lock);
        fs.mkdirSync(bundleDir, { recursive: true });
        const tmp = `${bundleDir}.build-${process.pid}`;
        fs.rmSync(tmp, { recursive: true, force: true });
        await bundle({
          entryPoint: path.join(srcDir, "index.tsx"),
          outDir: tmp,
          onProgress: (p) => onProgress && onProgress(Math.min(0.1, 0.1 * (p || 0))),
          webpackOverride: (config) => config,
        });
        // Swap the finished build in: delete the old webpack output, keep
        // the marks, the mounts other jobs may still point at, and the
        // `public/` folder that holds them.
        const keep = new Set([".stamp", ".build.lock", "public"]);
        for (const entry of fs.readdirSync(bundleDir)) {
          if (keep.has(entry) || entry.startsWith(".active-")) continue;
          if (fs.lstatSync(path.join(bundleDir, entry)).isSymbolicLink()) continue;
          fs.rmSync(path.join(bundleDir, entry), { recursive: true, force: true });
        }
        for (const entry of fs.readdirSync(tmp)) {
          if (entry === "public") {
            // Merge, never replace: live junctions live in here.
            fs.mkdirSync(path.join(bundleDir, "public"), { recursive: true });
            for (const child of fs.readdirSync(path.join(tmp, "public"))) {
              const target = path.join(bundleDir, "public", child);
              if (fs.existsSync(target)) continue;
              fs.renameSync(path.join(tmp, "public", child), target);
            }
            continue;
          }
          fs.renameSync(path.join(tmp, entry), path.join(bundleDir, entry));
        }
        fs.rmSync(tmp, { recursive: true, force: true });
        fs.writeFileSync(path.join(bundleDir, ".stamp"), stamp, "utf8");
        return { bundleDir, rebuilt: true, stale: false };
      } finally {
        try {
          fs.unlinkSync(lock);
        } catch {
          /* already gone */
        }
      }
    }
  }

  // Nothing built yet: this job must build, it cannot render without one.
  fs.mkdirSync(bundleDir, { recursive: true });
  const tmp = `${bundleDir}.build-${process.pid}`;
  fs.rmSync(tmp, { recursive: true, force: true });
  await bundle({
    entryPoint: path.join(srcDir, "index.tsx"),
    outDir: tmp,
    onProgress: (p) => onProgress && onProgress(Math.min(0.1, 0.1 * (p || 0))),
    webpackOverride: (config) => config,
  });
  for (const entry of fs.readdirSync(tmp)) {
    fs.renameSync(path.join(tmp, entry), path.join(bundleDir, entry));
  }
  fs.rmSync(tmp, { recursive: true, force: true });
  fs.writeFileSync(path.join(bundleDir, ".stamp"), stamp, "utf8");
  return { bundleDir, rebuilt: true, stale: false };
};

/* ------------------------------------------------------------- mountings */

/**
 * Remove a junction/symlink *without* touching what it points at.
 *
 * `fs.unlinkSync` fails with EPERM on a Windows directory junction and
 * `fs.rmSync(recursive)` would delete the listing's photos, so the only
 * safe call is `fs.rmdirSync` — it drops the link and stops there. This
 * used to be `unlinkSync` in a try/catch, which silently left every mount
 * behind until a later render died on launch.
 */
const dropLink = (linkPath) => {
  try {
    const st = fs.lstatSync(linkPath);
    if (!st.isSymbolicLink()) return false;
  } catch {
    return false;
  }
  try {
    fs.rmdirSync(linkPath);
    return true;
  } catch {
    try {
      fs.unlinkSync(linkPath);
      return true;
    } catch {
      return false;
    }
  }
};

const mount = (linkPath, target) => {
  let existing = null;
  try {
    const st = fs.lstatSync(linkPath);
    if (st.isSymbolicLink()) {
      existing = fs.readlinkSync(linkPath);
    } else {
      fs.rmSync(linkPath, { recursive: true, force: true });
    }
  } catch {
    /* first run */
  }
  if (existing === target) return;
  if (existing !== null) dropLink(linkPath);
  fs.mkdirSync(path.dirname(linkPath), { recursive: true });
  fs.symlinkSync(target, linkPath, "junction");
};

/**
 * Drop mounts left behind by a crashed/killed job (their media folder is
 * gone). Only dangling links are touched, so a live job is never disturbed.
 */
const pruneMounts = (publicDir) => {
  let entries;
  try {
    entries = fs.readdirSync(publicDir, { withFileTypes: true });
  } catch {
    return [];
  }
  const removed = [];
  for (const entry of entries) {
    if (!entry.name.startsWith("m_") || !entry.isSymbolicLink()) continue;
    const full = path.join(publicDir, entry.name);
    try {
      if (fs.existsSync(full)) continue; // resolves: a live job owns it
    } catch {
      /* treat as dangling */
    }
    if (dropLink(full)) removed.push(entry.name);
  }
  return removed;
};

/**
 * Rewrite each shot's `media_path` to sit under this job's mount.
 *
 * The storyboard is the frozen contract and carries a listing-relative path
 * (`客厅/01.jpg`) — it must never learn an absolute path (§4) and it must
 * never learn a mount name either, because the mount changes per job. So
 * the prefix is added here, at the very edge, on a copy.
 */
const withMountedMedia = (board, root) => {
  if (!root) return board;
  return {
    ...board,
    shots: (board.shots || []).map((shot) =>
      shot.media_path
        ? { ...shot, media_path: `${root}/${shot.media_path}` }
        : shot,
    ),
  };
};

/* ------------------------------------------------------------------- run */

const main = async () => {
  const jobFile = argOf("job");
  if (!jobFile) {
    say({ type: "error", message: "缺少 --job 参数" });
    process.exit(2);
  }
  const job = JSON.parse(fs.readFileSync(jobFile, "utf8"));
  const storyboard = job.storyboard;
  const outPath = job.out_path;
  const concurrency = Number(job.concurrency || 2);
  const bundleDir = job.bundle_dir;
  const srcDir = job.src_dir;

  fs.mkdirSync(path.dirname(outPath), { recursive: true });

  const { rebuilt, stale } = await ensureBundle({
    srcDir,
    bundleDir,
    onProgress: (p) => say({ type: "progress", progress: p }),
  });
  say({
    type: "log",
    message: rebuilt
      ? "已重建合成工程"
      : stale
        ? `沿用上一版合成工程：还有 ${liveMarkers(bundleDir).length} 路渲染在跑` +
          "（改过渲染代码的话，要等它们结束才生效）"
        : "复用已构建的合成工程",
  });

  const mounted = [];
  try {
    // `staticFile('x')` resolves to `<serveUrl>/public/x`, and Remotion
    // serves the bundle's `public/` folder. Mounting at the bundle root
    // therefore 404s every photo — the junction must live under public/.
    const publicDir = path.join(bundleDir, "public");
    fs.mkdirSync(publicDir, { recursive: true });
    const pruned = pruneMounts(publicDir);
    if (pruned.length) {
      say({
        type: "log",
        message: `清理了 ${pruned.length} 个失效素材挂载（上次渲染异常退出残留）`,
      });
    }
    for (const root of job.asset_roots || []) {
      const link = path.join(publicDir, root.name);
      mount(link, root.path);
      mounted.push(link);
    }
    fs.writeFileSync(activeMarker(bundleDir), String(process.pid), "utf8");

    // Remotion downloads a browser on first use; if this machine already
    // has one, point at it so an offline render still works.
    const browserExecutable =
      job.chromium && fs.existsSync(job.chromium) ? job.chromium : null;

    // 4.x returns {cancelSignal, cancel}; older docs show `cancelSignal.cancel()`.
    const { cancelSignal, cancel } = makeCancelSignal();
    let cancelled = false;
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => {
      if (String(chunk).includes("cancel")) {
        cancelled = true;
        say({ type: "log", message: "收到取消指令，正在终止渲染" });
        cancel();
      }
    });
    process.stdin.resume();

    const inputProps = {
      storyboard: withMountedMedia(storyboard, job.media_root),
      bed_file: job.bed_file ?? null,
    };
    const composition = await selectComposition({
      serveUrl: bundleDir,
      id: job.composition_id || "PropertyPromo",
      inputProps,
      browserExecutable,
      chromeMode: "headless-shell",
      logLevel: "error",
      timeoutInMilliseconds: 120000,
    });

    await renderMedia({
      composition,
      serveUrl: bundleDir,
      codec: "h264",
      outputLocation: outPath,
      inputProps,
      crf: Number(job.crf ?? 18),
      imageFormat: "jpeg",
      jpegQuality: 92,
      concurrency,
      overwrite: true,
      browserExecutable,
      chromeMode: "headless-shell",
      logLevel: "error",
      timeoutInMilliseconds: 180000,
      cancelSignal,
      onProgress: ({ progress }) => {
        if (cancelled) return;
        // Bundling owns 0–10%, frames 10–98%, the mux is the last 2%.
        say({ type: "progress", progress: 0.1 + 0.88 * Math.min(1, progress) });
      },
    });

    say({
      type: "done",
      out_path: outPath,
      duration_s: composition.durationInFrames / composition.fps,
      width: composition.width,
      height: composition.height,
      fps: composition.fps,
    });
  } finally {
    // Never leave a junction behind: a later job would find a stale path
    // and paint the wrong album into its film.
    try {
      fs.unlinkSync(activeMarker(bundleDir));
    } catch {
      /* gone already */
    }
    for (const link of mounted) {
      dropLink(link);
    }
  }
};

main().catch((err) => {
  say({
    type: "error",
    message: String((err && err.message) || err || "渲染失败").slice(0, 4000),
  });
  process.exit(1);
});
