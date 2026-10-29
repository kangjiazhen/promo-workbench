/**
 * Bundle cache bookkeeping — «is the webpack build on disk the build of the
 * source on disk, and is anyone else using it right now».
 *
 * Split out of `render-job.mjs` so it can be tested without launching a
 * render: the two bugs this file exists to prevent were both silent.
 *
 *  1. A hard-killed render left `.active-<pid>` behind. Counting that as
 *     «someone is rendering» made every later job skip the rebuild and log
 *     it as «另一路渲染正在运行» — editing the composition produced
 *     byte-identical films and nothing anywhere reported an error.
 *  2. A job that lost the build lock to a *finishing* rebuild used the bundle
 *     it had read before the stamp was written, throwing a fresh build away.
 *
 * The rule: ownership is by pid, never by file age, and «fresh» is decided by
 * the stamp, not by who holds the lock.
 */
import fs from "node:fs";
import path from "node:path";
import process from "node:process";

/** `path:size:mtimeMs` for every source file, so any edit changes the stamp. */
export const sourceStamp = (srcDir) => {
  const walk = (dir) =>
    fs
      .readdirSync(dir, { withFileTypes: true })
      .flatMap((entry) => {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) return walk(full);
        const st = fs.statSync(full);
        return [`${path.relative(srcDir, full)}:${st.size}:${st.mtimeMs}`];
      })
      .sort();
  return walk(srcDir).join("|");
};

export const activeMarker = (bundleDir, pid = process.pid) =>
  path.join(bundleDir, `.active-${pid}`);

/** Is a pid still there? Signal 0 only asks the OS; EPERM means «exists». */
export const pidAlive = (pid) => {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    return Boolean(err) && err.code === "EPERM";
  }
};

/**
 * Live `.active-<pid>` markers, reaping the dead ones on the way past.
 *
 * Reap by pid, not by age: a genuinely long render must keep its claim, and a
 * render killed an hour ago must not keep one forever.
 */
export const liveMarkers = (bundleDir, selfPid = process.pid) => {
  if (!fs.existsSync(bundleDir)) return [];
  const live = [];
  for (const name of fs.readdirSync(bundleDir)) {
    if (!name.startsWith(".active-")) continue;
    if (name === `.active-${selfPid}`) continue;
    const pid = Number(name.slice(".active-".length));
    if (pidAlive(pid)) {
      live.push(name);
      continue;
    }
    try {
      fs.unlinkSync(path.join(bundleDir, name));
    } catch {
      /* another job is reaping it too — nothing to do */
    }
  }
  return live;
};

export const otherRendersActive = (bundleDir) => liveMarkers(bundleDir).length > 0;

/** True when the build on disk matches the source on disk. */
export const bundleIsFresh = (bundleDir, stamp) => {
  const stampFile = path.join(bundleDir, ".stamp");
  return (
    fs.existsSync(path.join(bundleDir, "index.html")) &&
    fs.existsSync(stampFile) &&
    fs.readFileSync(stampFile, "utf8") === stamp
  );
};

export const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
