/**
 * 批量上传：把「添加的文件夹」变成「可上传的套」和「预检结果」。
 *
 * 规则（设计文档 §3.1）：
 *   * 直接子级含图片 → 该文件夹本身就是一套房源（子文件夹 = 房间）
 *   * 直接子级无图片、子文件夹含图片 → 父文件夹，每个含图的子文件夹 = 一套
 *   * 两边都有 → 红色错误，让用户拆开
 *
 * 父目录段在客户端剥掉：服务端永远只收到 `房间/文件` 或 `房源/房间/文件`。
 */
import { SHARED_CONSTANTS } from "./sharedConstants";

export interface ManifestItem {
  path: string;
  size: number;
}

export interface ListingUnit {
  /** 服务端使用的房源名（父目录模式下是子文件夹名）。 */
  rootDirName: string;
  /**
   * 上传用的相对路径：`房源根/房间/图片`（正好三段）。
   *
   * 父目录段在客户端剥离，但**房源根段必须保留**——服务端契约是三段，
   * 房间名只有在房源根下面才唯一（设计文档 §3.1）。
   */
  manifest: ManifestItem[];
  /** manifest 每一项对应的原始 File，按 manifest 的 path 索引。 */
  files: Map<string, File>;
  rooms: { name: string; count: number }[];
  /** 根目录散图（红色，阻止该套上传）。 */
  loose: string[];
  /** 房间内嵌套（红色，阻止该套上传）。 */
  nested: string[];
  /** 不支持的文件类型（黄色，仅提醒）。 */
  unsupported: string[];
  totalBytes: number;
  /** 拖拽时可见：这个房源下没有任何图片的房间文件夹（§3.2 灰卡）。 */
  emptyRooms?: string[];
}

export interface AddedFolder {
  /** 用户添加时的显示名（父目录名 / 房源目录名）。 */
  displayName: string;
  mode: "listing" | "parent" | "ambiguous" | "empty";
  units: ListingUnit[];
  /** 父目录根层不属于任何套的散图（黄色提醒，忽略）。 */
  stray: string[];
  message: string;
  /** 拖拽才能看到：整套一个图片都没有。 */
  empty?: boolean;
  /**
   * 拖拽才能看到：完全没有图片的房间文件夹（§3.2 灰卡说明）。
   *
   * 选择器看不到空目录（浏览器限制）；拖拽能看到，所以要告诉用户
   * 「这个房间没照片」，否则用户会以为照片被吞了。
   */
  emptyRooms?: string[];
}

const SUPPORTED = SHARED_CONSTANTS.supported_image_exts as readonly string[];
const JUNK_NAMES = SHARED_CONSTANTS.junk_names as readonly string[];
const JUNK_PREFIXES = SHARED_CONSTANTS.junk_prefixes as readonly string[];
/** §3.4：客户端预检扫描文件数上限，防止选中整个盘/下载目录时卡死。 */
export const MAX_SCAN_FILES: number =
  SHARED_CONSTANTS.limits.max_client_scan_files;

export function isJunk(name: string): boolean {
  const lower = name.toLowerCase();
  return (
    JUNK_NAMES.includes(lower) ||
    JUNK_PREFIXES.some((p) => name.startsWith(p))
  );
}

export function isSupportedImage(name: string): boolean {
  const lower = name.toLowerCase();
  const dot = lower.lastIndexOf(".");
  if (dot < 0) return false;
  return SUPPORTED.includes(lower.slice(dot));
}

export interface RawFile {
  path: string; // 相对于「被添加的文件夹」
  size: number;
  file: File;
}

/** 拖拽读到的目录树：文件 + 目录（目录用来提示空房间，§3.2）。 */
export interface DroppedTree {
  files: RawFile[];
  /** 相对路径形式的目录列表，例如 "客厅"、"客厅/实拍"。 */
  dirs: string[];
}

/** 读完一个拖拽进来的目录树。 */
async function readEntry(
  entry: FileSystemEntry,
  prefix: string,
  out: RawFile[],
  dirs: string[],
  limit = MAX_SCAN_FILES,
): Promise<void> {
  if (out.length >= limit) return;      // §3.4: 扫描上限，防止选中整个盘卡死
  if (entry.isFile) {
    const file = await new Promise<File>((resolve, reject) => {
      (entry as FileSystemFileEntry).file(resolve, reject);
    });
    out.push({ path: prefix ? `${prefix}/${file.name}` : file.name, size: file.size, file });
    return;
  }
  if (!entry.isDirectory) return;
  if (prefix) dirs.push(prefix);
  const reader = (entry as FileSystemDirectoryEntry).createReader();
  // readEntries 每次最多返回 100 条，必须循环到空为止。
  const children: FileSystemEntry[] = [];
  for (;;) {
    if (out.length >= limit) return;
    const batch = await new Promise<FileSystemEntry[]>((resolve, reject) => {
      reader.readEntries(resolve, reject);
    });
    if (!batch.length) break;
    children.push(...batch);
  }
  for (const child of children) {
    if (out.length >= limit) return;
    await readEntry(child, prefix ? `${prefix}/${child.name}` : child.name,
                    out, dirs, limit);
  }
}

/** 把拖拽事件里的文件夹读成扁平的路径表 + 目录表（路径相对被拖入的文件夹）。 */
export async function readDropTree(dataTransfer: DataTransfer): Promise<DroppedTree> {
  const items = Array.from(dataTransfer.items || []);
  const entries = items
    .map((it) =>
      typeof it.webkitGetAsEntry === "function" ? it.webkitGetAsEntry() : null,
    )
    .filter((e): e is FileSystemEntry => e !== null);
  if (!entries.length) return { files: [], dirs: [] };

  const files: RawFile[] = [];
  const dirs: string[] = [];
  for (const entry of entries) {
    if (files.length >= MAX_SCAN_FILES) break;
    await readEntry(entry, "", files, dirs);
  }
  return { files, dirs };
}

/** 兼容旧入口：只要文件。 */
export async function filesFromDrop(dataTransfer: DataTransfer): Promise<RawFile[]> {
  return (await readDropTree(dataTransfer)).files;
}

/**
 * §3.4：拖进的目录是否超过扫描上限。
 *
 * 到达上限就停止读取（宁可少读也不要卡死浏览器），由调用方给出
 * 「请选择更具体的目录」的提示。
 */
export function scanLimitReached(files: RawFile[]): boolean {
  return files.length >= MAX_SCAN_FILES;
}

/** 选择器选中的目录：webkitRelativePath 的第一段是目录名。 */
export function filesFromPicker(fileList: FileList): RawFile[] {
  const out: RawFile[] = [];
  for (const file of Array.from(fileList)) {
    const rel = file.webkitRelativePath;
    if (!rel) continue;
    const parts = rel.split("/");
    const name = parts.pop() as string;
    out.push({ path: parts.join("/") ? `${parts.join("/")}/${name}` : name,
               size: file.size, file });
  }
  return out;
}

interface Bucket {
  loose: string[];
  nested: string[];
  unsupported: string[];
  rooms: Map<string, { count: number; bytes: number }>;
}

function newBucket(): Bucket {
  return { loose: [], nested: [], unsupported: [], rooms: new Map() };
}

function pushFile(bucket: Bucket, path: string, size: number): void {
  const segs = path.split("/").filter(Boolean);
  const name = segs[segs.length - 1];
  if (isJunk(name)) return;
  if (!isSupportedImage(name)) {
    bucket.unsupported.push(path);
    return;
  }
  if (segs.length === 1) {
    bucket.loose.push(path);
    return;
  }
  if (segs.length > 2) {
    bucket.nested.push(path);
    return;
  }
  const room = segs[0];
  const entry = bucket.rooms.get(room) ?? { count: 0, bytes: 0 };
  entry.count += 1;
  entry.bytes += size;
  bucket.rooms.set(room, entry);
}

function unitFromBucket(rootDirName: string, bucket: Bucket,
                        manifest: ManifestItem[],
                        files: Map<string, File>): ListingUnit {
  return {
    rootDirName,
    manifest,
    files,
    rooms: [...bucket.rooms.entries()].map(([name, v]) => ({
      name,
      count: v.count,
    })),
    loose: bucket.loose,
    nested: bucket.nested,
    unsupported: bucket.unsupported,
    totalBytes: manifest.reduce((sum, m) => sum + m.size, 0),
  };
}

/**
 * 判断一个被添加的文件夹属于哪种模式，并拆成套。
 *
 * `files` 的路径已经相对该文件夹（不含文件夹自身名字）。
 */
/** 直接子级目录（不含更深的）里，哪些是空房间（§3.2 灰卡）。 */
function emptyRoomsOf(dirs: string[], rooms: Set<string>,
                      prefix = ""): string[] {
  const out: string[] = [];
  for (const d of dirs) {
    if (prefix) {
      if (!d.startsWith(`${prefix}/`)) continue;
      const rest = d.slice(prefix.length + 1);
      if (rest.includes("/")) continue;   // 更深层按「嵌套」处理，不算空房间
      if (!rooms.has(rest)) out.push(rest);
    } else {
      if (d.includes("/")) continue;
      if (!rooms.has(d)) out.push(d);
    }
  }
  return [...new Set(out)].sort();
}

export function classifyFolder(displayName: string, files: RawFile[],
                               dirs: string[] = []): AddedFolder {
  const useful = files.filter((f) => !isJunk(f.path.split("/").pop() as string));
  const images = useful.filter((f) =>
    isSupportedImage(f.path.split("/").pop() as string),
  );
  if (!images.length) {
    return {
      displayName,
      mode: "empty",
      units: [],
      stray: [],
      message: dirs.length
        ? `这个文件夹里没有照片，但有 ${dirs.length} 个空文件夹` +
          `（如 ${dirs.slice(0, 3).join("、")}）。` +
          "浏览器不会上传空文件夹，请先把照片放进对应的房间文件夹。"
        : "这个文件夹里没有照片（空文件夹不会被浏览器上传）。",
      empty: true,
    };
  }

  // 判据是**图片所在的深度**，不是「根目录有没有图片」。
  //
  // 用户真实的结构是 `房源/房间/图片`（房源根目录里根本没有图片），
  // 旧规则「直接子级含图片才是房源」会把它当成父文件夹拆成 N 套
  // ——每个房间变成一套房源。这里按深度判：
  //   有 `子/图.jpg`（深度 2）     → 这个文件夹是房源，子文件夹是房间
  //   只有 `子/孙/图.jpg`（深度 3）→ 这个文件夹是父文件夹，子文件夹是房源
  //   只有根层图片（深度 1）       → 房源，根目录散图 → 红色阻止
  const depthOf = (p: string) => p.split("/").filter(Boolean).length;
  const depths = images.map((f) => depthOf(f.path));
  const roomLevel = depths.some((d) => d === 2);   // 子文件夹里直接是图片
  const deepLevel = depths.some((d) => d >= 3);    // 子文件夹里还有一层

  if (roomLevel && deepLevel) {
    const withRooms = [...new Set(
      images.filter((f) => depthOf(f.path) === 2).map((f) => f.path.split("/")[0]),
    )].sort();
    const withDeeper = [...new Set(
      images.filter((f) => depthOf(f.path) >= 3).map((f) => f.path.split("/")[0]),
    )].sort();
    return {
      displayName,
      mode: "ambiguous",
      units: [],
      stray: [],
      message:
        `「${withRooms.slice(0, 3).join("、")}」里直接就有图片，` +
        `而「${withDeeper.slice(0, 3).join("、")}」里还有一层文件夹，` +
        "分不清哪一层是房间。请按「房源/房间/图片」整理：" +
        "图片直接放进房间文件夹，房间下面不要再套文件夹。",
    };
  }

  const directImages = images.filter((f) => !f.path.includes("/"));

  if (roomLevel || directImages.length) {
    // 单套：房间是子文件夹，图片直接放在房间这一层。
    // （根目录若还有散图，会被 pushFile 记为 loose，由 unitBlocked 红色拦下。）
    const bucket = newBucket();
    const manifest: ManifestItem[] = [];
    const fileMap = new Map<string, File>();
    for (const f of files) {
      const segs = f.path.split("/").filter(Boolean);
      if (segs.length < 2) {
        // 根层文件
        pushFile(bucket, f.path, f.size);
        continue;
      }
      const room = segs[0];
      const rest = segs.slice(1).join("/");
      pushFile(bucket, `${room}/${rest}`, f.size);
      // 房源根段必须带上：服务端契约是 `房源根/房间/图片`（三段）。
      const key = `${displayName}/${room}/${rest}`;
      manifest.push({ path: key, size: f.size });
      fileMap.set(key, f.file);
    }
    const unit = unitFromBucket(displayName, bucket, manifest, fileMap);
    unit.emptyRooms = emptyRoomsOf(dirs, new Set(bucket.rooms.keys()));
    return {
      displayName,
      mode: "listing",
      units: [unit],
      stray: [],
      message: "",
    };
  }

  // 父文件夹：每个含图的子文件夹 = 一套（图片在子文件夹下一层：子/图.jpg）。
  const childDirsWithImages = new Set(
    images
      .filter((f) => depthOf(f.path) >= 2)
      .map((f) => f.path.split("/")[0]),
  );
  const units: ListingUnit[] = [];
  const stray: string[] = [];
  for (const dir of [...childDirsWithImages].sort()) {
    const bucket = newBucket();
    const manifest: ManifestItem[] = [];
    const fileMap = new Map<string, File>();
    for (const f of files) {
      if (!f.path.includes("/")) continue;
      if (f.path.split("/")[0] !== dir) continue;
      const rest = f.path.split("/").slice(1).join("/");
      pushFile(bucket, rest, f.size);
      const key = `${dir}/${rest}`;
      manifest.push({ path: key, size: f.size });
      fileMap.set(key, f.file);
    }
    const unit = unitFromBucket(dir, bucket, manifest, fileMap);
    unit.emptyRooms = emptyRoomsOf(dirs, new Set(bucket.rooms.keys()), dir);
    units.push(unit);
  }
  for (const f of images) {
    if (!f.path.includes("/")) stray.push(f.path);
  }
  return {
    displayName,
    mode: "parent",
    units,
    stray,
    message: "",
  };
}

/** 一套是否可以上传（红色问题会阻止它，但不影响别的套）。 */
export function unitBlocked(unit: ListingUnit): string | null {
  if (unit.loose.length) {
    return `有 ${unit.loose.length} 张图片直接放在根目录` +
      `（如 ${unit.loose.slice(0, 3).join("、")}），请移入房间子文件夹。`;
  }
  if (unit.nested.length) {
    return `房间下多嵌套了一层` +
      `（如 ${unit.nested.slice(0, 3).join("、")}），请把图片直接放进房间文件夹。`;
  }
  if (!unit.manifest.length) {
    return "没有可上传的照片。";
  }
  return null;
}

/**
 * 两个拖进来的房源同名时，后来者自动加后缀 `房源A (2)`（§12.2）。
 *
 * 重命名必须同时改写 manifest 的三段路径与 files 索引，否则服务端收到的
 * 房源根段还是旧名字，两套会在磁盘/数据库里撞在一起。
 */
export function withUniqueRootName(
  unit: ListingUnit,
  used: Set<string>,
): ListingUnit {
  if (!used.has(unit.rootDirName)) {
    used.add(unit.rootDirName);
    return unit;
  }
  let n = 2;
  while (used.has(`${unit.rootDirName} (${n})`)) n += 1;
  const name = `${unit.rootDirName} (${n})`;
  used.add(name);
  const prefix = `${unit.rootDirName}/`;
  const manifest = unit.manifest.map((m) => ({
    path: name + "/" + m.path.slice(prefix.length),
    size: m.size,
  }));
  const files = new Map<string, File>();
  unit.manifest.forEach((m, i) => {
    const file = unit.files.get(m.path);
    if (file) files.set(manifest[i].path, file);
  });
  return { ...unit, rootDirName: name, manifest, files };
}
