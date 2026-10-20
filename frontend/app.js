/* 音乐速度转换器 —— 前端逻辑 */
"use strict";

const $ = (s) => document.querySelector(s);

const TIMBRES = [
  { k: "beep", cn: "电子哔声", en: "Beep" },
  { k: "woodblock", cn: "木鱼", en: "Woodblock" },
  { k: "cowbell", cn: "牛铃", en: "Cowbell" },
  { k: "click", cn: "短打", en: "Click" },
  { k: "sidestick", cn: "边击", en: "Sidestick" },
  { k: "clap", cn: "拍手", en: "Clap" },
];

/* ---------------- 语言（中文 / 英文） ---------------- */
/* 界面默认中文，右上角按钮切换，选择存 localStorage。
   静态文案走 data-t（index.html 里两种语言都写在 DOM 上，由 CSS 决定显示哪个，
   这样即使 app.js 没跑起来也不会中英混杂）；运行期文案（提示、状态、按钮）走 t()。 */
const LANG_KEY = "tmc-lang";

const ZH = {
  drop: "松开以添加文件 / 文件夹",
  badgeMock: "浏览器预览模式",
  connecting: "连接后端中…",
  connected: "后端已连接",
  mockMode: "预览模式",
  cardFiles: "文件",
  cardSettings: "转换设置",
  selPrefix: "已选 {n}",
  selectAll: "全选",
  unselectAll: "取消全选",
  delSelected: "删除选中",
  clearDone: "清除已处理",
  addFiles: "＋ 添加文件",
  addFolder: "＋ 添加文件夹",
  emptyTitle: "拖入音频文件 / 文件夹",
  emptyHint: "或点击右上角按钮添加（wav · mp3 · flac · ogg · m4a 等）",
  labelTarget: "目标速度",
  labelSource: "原速",
  hintAuto: "留空 = 自动检测",
  labelLimit: "变速上限",
  labelMaxSlow: "最多放慢",
  labelMaxFast: "最多加快",
  labelEngine: "变速方式",
  detectOnly: "只测速",
  segments: "分段变速",
  enginePv: "保音高",
  enginePvSub: "变速不变调",
  engineRs: "磁带机式",
  engineRsSub: "变速变调",
  labelFormat: "输出类型",
  formatSource: "原类型",
  formatSourceSub: "与输入相同",
  labelOutDir: "输出目录",
  choose: "选择…",
  restoreDefault: "恢复默认",
  mirrorDirs: "保持原来的文件夹层级",
  metronome: "添加节拍器",
  labelRelVol: "相对音量",
  hintEq: "0 = 与音乐等响",
  start: "开始处理",
  startSel: "开始处理（已选 {n}）",
  startAll: "开始处理（全部 {n}）",
  stop: "停止",
  ready: "就绪",
  openReport: "打开报告",
  logTitle: "处理日志",
  tkHint: "界面显示异常？",
  tkOpen: "改用兼容界面",
  tkFail: "兼容界面需要在桌面版里打开（当前是浏览器预览）",
  tkErr: "兼容界面启动失败，详见 app_error.log",

  // 运行期文案
  limitSlow: "最多放慢 {n}%",
  limitSlowNone: "不放慢",
  limitFast: "最多加快 {n}%",
  limitFastNone: "不加快",
  limitTail: "；超出换方向，两边都超则跳过",
  limitTailNone: "；只有原速=目标的才处理",
  outDirDefault: "默认输出到各文件所在目录的 tempo_converted /",
  outDirMirror: "输出到：{dir}（按原文件夹层级）",
  outDirFlat: "输出到：{dir}（全部平铺，不建子文件夹）",
  restoredDefault: "已恢复默认输出位置",
  needTarget: "请填写目标速度 BPM",
  startFailed: "启动失败：{err}",
  startFailedPlain: "启动失败",
  preparing: "准备处理 {n} 个文件…",
  processing: "处理中…",
  stoppedSummary: "已停止：完成 {ok} · 失败 {fail}{skip}",
  doneSummary: "完成：成功 {ok} · 失败 {fail}{skip}",
  skipPart: " · 跳过 {n}",
  skippedHint: "有 {n} 个文件因超出变速上限被跳过（可在「变速上限」里放宽，或改目标速度）。",
  addedFiles: "已添加 {n} 个文件",
  startedRun: "开始处理，共 {n} 个文件。",
  noAudioInFolder: "该文件夹中没有找到支持的音频文件",
  selectFirst: "先勾选要删除的文件",
  deletedN: "已删除 {n} 个",
  nothingDone: "还没有处理成功的文件",
  clearedDone: "已清除 {n} 个已完成文件，剩余 {left} 个",
  dropInMock: "预览模式不支持拖入，请在桌面应用中体验",
  readyLog: "音乐速度转换器已就绪。",
  mockLog: "预览模式。",
  liveLog: "后端已连接，添加文件即可开始。",
  mockBanner: "浏览器预览模式：未连接桌面后端，按钮为模拟演示。",
  checkTip: "勾选后可批量删除 / 只处理勾选项",
  revealTip: "在资源管理器中显示",
  removeTip: "移除",
  previewTip: "试听",
  themeDark: "主题：夜间（点击切换到日间）",
  themeLight: "主题：日间（点击切换到夜间）",
  themeToggle: "切换日间 / 夜间模式",

  stPending: "待处理",
  stRunning: "处理中",
  stDone: "完成",
  stFailed: "失败",
  stSkipped: "已跳过",
};

const EN = {
  drop: "Release to add files / folders",
  badgeMock: "Browser preview mode",
  connecting: "Connecting to backend…",
  connected: "Backend connected",
  mockMode: "Preview mode",
  cardFiles: "Files",
  cardSettings: "Settings",
  selPrefix: "{n} selected",
  selectAll: "Select all",
  unselectAll: "Deselect all",
  delSelected: "Delete selected",
  clearDone: "Clear finished",
  addFiles: "＋ Add files",
  addFolder: "＋ Add folder",
  emptyTitle: "Drop audio files / folders here",
  emptyHint: "or use the buttons at the top right (wav · mp3 · flac · ogg · m4a …)",
  labelTarget: "Target tempo",
  labelSource: "Original tempo",
  hintAuto: "Leave blank to auto-detect",
  labelLimit: "Speed change limit",
  labelMaxSlow: "Max slowdown",
  labelMaxFast: "Max speed-up",
  labelEngine: "Stretch method",
  detectOnly: "Detect only",
  segments: "Per-section",
  enginePv: "Keep pitch",
  enginePvSub: "changes tempo, keeps pitch",
  engineRs: "Tape style",
  engineRsSub: "changes tempo and pitch",
  labelFormat: "Output format",
  formatSource: "Same as input",
  formatSourceSub: "unchanged",
  labelOutDir: "Output folder",
  choose: "Choose…",
  restoreDefault: "Restore default",
  mirrorDirs: "Keep folder structure",
  metronome: "Add metronome",
  labelRelVol: "Relative volume",
  hintEq: "0 = as loud as the music",
  start: "Start",
  startSel: "Start ({n} selected)",
  startAll: "Start (all {n} files)",
  stop: "Stop",
  ready: "Ready",
  openReport: "Open report",
  logTitle: "Log",
  tkHint: "Display problems?",
  tkOpen: "Switch to the compatibility interface",
  tkFail: "The compatibility interface is available in the desktop app (this is a browser preview)",
  tkErr: "Could not start the compatibility interface; see app_error.log",

  limitSlow: "Max slowdown {n}%",
  limitSlowNone: "no slowdown",
  limitFast: "Max speed-up {n}%",
  limitFastNone: "no speed-up",
  limitTail: "; if a side exceeds it, the other side is tried; if both do, the file is skipped",
  limitTailNone: "; only files already at the target tempo are processed",
  outDirDefault: "Default: a tempo_converted / folder next to each source file",
  outDirMirror: "Output: {dir} (mirroring the folder structure)",
  outDirFlat: "Output: {dir} (flat, no subfolders)",
  restoredDefault: "Restored the default output location",
  needTarget: "Please enter a target BPM",
  startFailed: "Could not start: {err}",
  startFailedPlain: "Could not start",
  preparing: "Preparing {n} files…",
  processing: "Processing…",
  stoppedSummary: "Stopped: {ok} done · {fail} failed{skip}",
  doneSummary: "Done: {ok} succeeded · {fail} failed{skip}",
  skipPart: " · {n} skipped",
  skippedHint: "{n} file(s) were skipped because both directions exceed the tempo limit — widen the limit or change the target tempo.",
  addedFiles: "Added {n} file(s)",
  startedRun: "Starting, {n} files in total.",
  noAudioInFolder: "No supported audio files found in that folder",
  selectFirst: "Select the files to delete first",
  deletedN: "Deleted {n}",
  nothingDone: "No file has finished successfully yet",
  clearedDone: "Cleared {n} finished file(s), {left} left",
  dropInMock: "Dropping is not supported in preview mode — use the desktop app",
  readyLog: "BeatSync is ready.",
  mockLog: "Preview mode.",
  liveLog: "Backend connected — add files to start.",
  mockBanner: "Browser preview mode: not connected to the desktop backend, buttons are a simulation.",
  checkTip: "Select to delete in bulk / process only the selected ones",
  revealTip: "Show in File Explorer",
  removeTip: "Remove",
  previewTip: "Preview",
  themeDark: "Theme: dark (click for light)",
  themeLight: "Theme: light (click for dark)",
  themeToggle: "Toggle light / dark mode",

  stPending: "Pending",
  stRunning: "Running",
  stDone: "Done",
  stFailed: "Failed",
  stSkipped: "Skipped",
};

const DICTS = { zh: ZH, en: EN };

const state = {
  files: new Map(),   // path -> {path, root, status, info, checked}
  running: false,
  bridge: null,
  mock: false,
  previewAudio: null,
  lang: "zh",         // 当前界面语言；默认中文
};

function curLang() {
  return state.lang === "en" ? "en" : "zh";
}

/* 取一条文案；{name} 占位符用 vars 里的值替换 */
function t(key, vars) {
  const s = (DICTS[curLang()] || ZH)[key];
  if (s == null) return key;
  if (!vars) return s;
  return s.replace(/\{(\w+)\}/g, (m, k) => (vars[k] != null ? String(vars[k]) : m));
}

/* 把 data-t 元素里的静态文案刷成当前语言。
   HTML 里写的是中文（默认语言），所以即使 app.js 被拦住没跑起来，界面也是完整中文，
   不会出现中英混杂或空白标签。双语标签（.t-zh / .t-en）由 CSS 决定显示哪个，
   这里只刷纯文本节点与 title / placeholder / aria-label 这类属性。 */
function applyStaticI18n() {
  document.documentElement.dataset.lang = curLang();
  document.documentElement.lang = curLang() === "en" ? "en" : "zh-CN";
  document.querySelectorAll("[data-t]").forEach(el => {
    // 元素里如果放了双语 span（.t-zh/.t-en），交给 CSS 切换，不要覆写 innerHTML
    if (el.querySelector(".t-zh, .t-en")) return;
    const s = t(el.dataset.t);
    if (s && s !== el.dataset.t) el.textContent = s;
  });
  document.querySelectorAll("[data-title-zh]").forEach(el => {
    el.title = el.dataset[curLang() === "en" ? "titleEn" : "titleZh"] || el.title;
  });
  document.querySelectorAll("[data-ph-zh]").forEach(el => {
    el.placeholder = el.dataset[curLang() === "en" ? "phEn" : "phZh"] || el.placeholder;
  });
  document.querySelectorAll("[data-aria-zh]").forEach(el => {
    el.setAttribute("aria-label",
      el.dataset[curLang() === "en" ? "ariaEn" : "ariaZh"] || el.getAttribute("aria-label"));
  });
}

/* 语言切换后，把所有"运行期写死"的文案整体重刷一遍 */
function refreshDynamicText() {
  applyStaticI18n();
  // 桥接状态指示灯的文字要按**当前真实状态**刷，不能交给 applyStaticI18n：
  // 它曾带 data-t="connecting"，切语言时被无条件覆写成「连接后端中…」，
  // 而绿点 class 不会被撤 → 出现"绿点 + 连接中"的矛盾显示。
  const bs = $("#bridgeStatus");
  if (bs) {
    const span = bs.querySelector("span");
    if (span) span.textContent = state.mock ? t("mockMode")
      : (state.bridge ? t("connected") : t("connecting"));
  }
  $("#btnLang").title = curLang() === "en"
    ? "Language: English (click to switch to 中文)"
    : "界面语言：中文（点击切换到 English）";
  renderFiles();
  updateSelCount();
  refreshStartBtn();
  limitHintUpdate();
  showOutDir();
  renderTimbres();
  syncThemeIcons();
  if (!state.running) {
    $("#progressLabel").textContent = t("ready");
  }
}

function initLang() {
  let l = "zh";
  try { l = localStorage.getItem(LANG_KEY) === "en" ? "en" : "zh"; } catch (e) { }
  state.lang = l;
  applyStaticI18n();
  $("#btnLang").onclick = () => {
    state.lang = curLang() === "en" ? "zh" : "en";
    try { localStorage.setItem(LANG_KEY, state.lang); } catch (e) { }
    refreshDynamicText();
  };
}

// 「清除已处理」只移除**成功完成**的（done）。失败/跳过的不删——它们要么没产出
// 文件（跳过）、要么需要用户看一眼原因（失败），删掉反而丢信息。
const DONE_STATES = new Set(["done"]);

/* ---------------- 桥接 ---------------- */
const realBridge = () => ({
  selectFiles: () => window.pywebview.api.select_files(curLang()),
  selectFolder: () => window.pywebview.api.select_folder(),
  selectOutDir: () => window.pywebview.api.select_out_dir(),
  expand: (paths) => window.pywebview.api.expand(paths),
  preview: (t) => window.pywebview.api.preview_timbre(t),
  // 第三个参数 roots（每个文件所属的根）**必须透传**：后端靠它在指定输出目录时
  // 重建原文件夹层级。少了它，后端会把每个文件当成单独添加（根=各自父目录）→ 全平铺。
  start: (paths, opts, roots) => window.pywebview.api.start(paths, opts, roots),
  stop: () => window.pywebview.api.stop(),
  reveal: (p) => window.pywebview.api.reveal_file(p),
  openFolder: (p) => window.pywebview.api.open_folder(p),
  // 启动 tkinter 兼容界面（功能一致、不依赖 .NET / WebView2）。
  launchTk: () => window.pywebview.api.launch_tk(),
});

function mockBridge() {
  const beep = (freq, dur) => {
    const ac = new (window.AudioContext || window.webkitAudioContext)();
    const o = ac.createOscillator(), g = ac.createGain();
    o.frequency.value = freq; o.type = "sine";
    g.gain.setValueAtTime(0.4, ac.currentTime);
    g.gain.exponentialRampToValueAtTime(0.001, ac.currentTime + dur);
    o.connect(g); g.connect(ac.destination);
    o.start(); o.stop(ac.currentTime + dur);
  };
  return {
    selectFiles: async () => ["D:\\音乐\\示例一.mp3", "D:\\音乐\\示例二.flac", "D:\\音乐\\示例三.wav"],
    selectFolder: async () => "D:\\音乐\\待转换",
    selectOutDir: async () => "D:\\音乐\\已转换",
    expand: async (paths) => ["D:\\音乐\\待转换\\A.mp3", "D:\\音乐\\待转换\\B.wav", "D:\\音乐\\待转换\\C.flac"],
    preview: async (t) => { beep(t === "beep" ? 1000 : 900, 0.18); return ""; },
    // 与真实桥接签名一致（第三个参数 roots 也要收下），预览模式下存到
    // window.__lastRoots，便于在浏览器里核对"根是否真的传到了后端"。
    start: (paths, opts, roots) => {
      window.__lastRoots = roots || [];
      const files = paths.slice(0, 5);
      // 预览模式的假数据：与真实方案保持一致（目标 180；每拍点击数取整数 1~20
      // 或 1/2…1/20，受默认变速上限 20%/50% 约束）。文案只用两种说法：
      // 「每拍敲 N 下」/「每 N 拍敲 1 下」。
      const mock = [
        { status: "done", detected: "95.1", a2: "90.0", rate: "0.9464", relation: "每拍敲 2 下", clickBpm: "180" },
        { status: "done", detected: "108.9", a2: "90.0", rate: "0.8264", relation: "每拍敲 2 下", clickBpm: "180" },
        { status: "done", detected: "122.5", a2: "180.0", rate: "1.4694", relation: "每拍敲 1 下", clickBpm: "180" },
        { status: "done", detected: "140.2", a2: "180.0", rate: "1.2839", relation: "每拍敲 1 下", clickBpm: "180" },
        { status: "done", detected: "163.9", a2: "180.0", rate: "1.0982", relation: "每拍敲 1 下", clickBpm: "180" },
      ];
      files.forEach((p, i) => setTimeout(() => {
        __appEvent({ type: "file-status", path: p, status: "running", index: i + 1 });
      }, i * 700));
      files.forEach((p, i) => setTimeout(() => {
        const m = mock[i % mock.length];
        __appEvent({
          type: "file-status", path: p, status: m.status, index: i + 1,
          detected: m.detected, a2: m.a2, rate: m.rate,
          relation: m.relation, clickBpm: m.clickBpm, note: m.note || "",
          out: m.status === "done" ? p.replace(/\.(\w+)$/, "_180bpm.wav") : "",
        });
        __appEvent({ type: "progress", done: i + 1, total: files.length });
      }, 600 + i * 700));
      setTimeout(() => __appEvent({ type: "done", ok: files.length, fail: 0, skipped: 0, stopped: false, report: "conversion_report.csv" }),
        800 + files.length * 700);
      return Promise.resolve({ ok: true, count: files.length });
    },
    stop: async () => true,
    reveal: async () => true,
    openFolder: async () => true,
  };
}

async function initBridge() {
  const t0 = Date.now();
  while (Date.now() - t0 < 6000) {
    if (window.pywebview && window.pywebview.api) break;
    await new Promise(r => setTimeout(r, 120));
  }
  if (window.pywebview && window.pywebview.api) {
    state.bridge = realBridge();
    state.mock = false;
    $("#bridgeStatus").classList.add("ok");
    $("#bridgeStatus").querySelector("span").textContent = t("connected");
  } else {
    state.bridge = mockBridge();
    state.mock = true;
    $("#modeBadge").classList.remove("hidden");
    $("#bridgeStatus").classList.add("mock");
    $("#bridgeStatus").querySelector("span").textContent = t("mockMode");
    logLine(t("mockBanner"), "l-hl");
  }
  window.__bridgeReady = true;
  setButtonsEnabled(true);
}

/* ---------------- 工具 ---------------- */
function basename(p) { return String(p).replace(/[\\/]+$/, "").split(/[\\/]/).pop(); }
function dirname(p) {
  const i = Math.max(String(p).lastIndexOf("\\"), String(p).lastIndexOf("/"));
  return i > 0 ? String(p).slice(0, i) : "";
}
/* 后端来的备注文字要塞进 innerHTML，必须先转义（备注里可能有 & < > 或文件名） */
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

let toastTimer = null;
function toast(msg, isErr = false) {
  const el = $("#toast");
  el.textContent = msg;
  el.className = "toast" + (isErr ? " err" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), 3200);
}

function logLine(text, cls = "") {
  const el = $("#log");
  const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 30;
  const span = document.createElement("span");
  if (cls) span.className = cls;
  span.textContent = text + "\n";
  el.appendChild(span);
  while (el.childNodes.length > 600) el.removeChild(el.firstChild);
  if (atBottom) el.scrollTop = el.scrollHeight;
}

function fmtDb(v) {
  const n = parseFloat(v);
  if (!isFinite(n)) return "";
  return (n > 0 ? "+" : n < 0 ? "\u2212" : "") + Math.abs(n) + " dB";
}

function fmtPercent(rateStr) {
  const r = parseFloat(rateStr);
  if (!isFinite(r)) return "";
  const d = (r - 1) * 100;
  return (d >= 0 ? "+" : "") + d.toFixed(1) + "%";
}

/* ---------------- 文件列表 ---------------- */
/* root = 这个文件是从哪个「用户添加的根」来的（添加文件夹时=文件夹本身，添加文件时=其父目录）。
   指定输出目录后要靠它算相对层级，把原来的文件夹结构在输出目录里重建。 */
function parentOf(p) {
  const s = String(p).replace(/[\\/]+$/, "");
  const i = Math.max(s.lastIndexOf("\\"), s.lastIndexOf("/"));
  return i > 0 ? s.slice(0, i) : s;
}

function addPaths(paths, root) {
  let added = 0;
  for (const p of paths || []) {
    const key = String(p).toLowerCase();
    const r = root || parentOf(p);
    if (!state.files.has(key)) {
      state.files.set(key, { path: p, root: r, status: "pending", info: null, checked: false });
      added++;
    } else {
      // 已存在的文件：若这次带来了更精确（更深）的根，用更深的那个
      const cur = state.files.get(key);
      if (r && (!cur.root || r.length > cur.root.length)) cur.root = r;
    }
  }
  if (added) { renderFiles(); }
  return added;
}

function renderFiles() {
  const ul = $("#fileList");
  ul.innerHTML = "";
  for (const item of state.files.values()) {
    ul.appendChild(fileRow(item));
  }
  $("#fileCount").textContent = state.files.size;
  $("#emptyState").style.display = state.files.size ? "none" : "flex";
  updateSelCount();
  refreshStartBtn();
}

/* 勾选状态：右上角显示"已选 N"，并切换全选按钮文案 */
function selList() {
  return [...state.files.values()].filter(f => f.checked);
}
function updateSelCount() {
  const n = selList().length;
  const badge = $("#selCount");
  badge.textContent = t("selPrefix", { n });
  badge.classList.toggle("hidden", n === 0);
  const allSel = state.files.size > 0 && n === state.files.size;
  $("#btnSelectAll").textContent = allSel ? t("unselectAll") : t("selectAll");
  const busy = state.running;
  $("#btnSelectAll").disabled = busy || state.files.size === 0;
  $("#btnDelSel").disabled = busy || n === 0;
  // 「清除已处理」：始终清**全部**已完成的，与勾选无关；按钮也始终保持可点
  // （只在处理中禁用），没有已完成的文件时点击给提示即可 —— 用户要求
  // 「在任何时候都能点」，不要在没勾/没完成时把它变灰。
  $("#btnClearDone").disabled = busy;
}

function fileRow(item) {
  const li = document.createElement("li");
  li.className = "file-item" + (item.checked ? " checked" : "");
  li.dataset.path = item.path;

  const top = document.createElement("div");
  top.className = "fi-top";

  // 勾选框（批量操作）
  const cb = document.createElement("input");
  cb.type = "checkbox";
  cb.className = "fi-check";
  cb.checked = !!item.checked;
  cb.title = t("checkTip");
  cb.onclick = (ev) => {
    ev.stopPropagation();
    item.checked = cb.checked;
    li.classList.toggle("checked", cb.checked);
    updateSelCount();
    refreshStartBtn();
  };

  const icon = document.createElement("span");
  icon.className = "fi-icon";
  icon.innerHTML = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>';

  const nameBox = document.createElement("div");
  nameBox.style.minWidth = "0";
  nameBox.style.flex = "1";
  const name = document.createElement("div");
  name.className = "fi-name";
  name.textContent = basename(item.path);
  name.title = item.path;
  const dir = document.createElement("span");
  dir.className = "fi-dir";
  dir.textContent = dirname(item.path);
  dir.title = dirname(item.path);
  nameBox.append(name, dir);

  const pill = document.createElement("span");
  pill.className = "pill " + item.status;
  const statusText = {
    pending: t("stPending"), running: t("stRunning"), done: t("stDone"),
    failed: t("stFailed"), skipped: t("stSkipped"),
  }[item.status];
  pill.innerHTML = "<i></i>" + statusText;

  const btnReveal = document.createElement("button");
  btnReveal.className = "fi-btn";
  btnReveal.title = t("revealTip");
  btnReveal.innerHTML = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>';
  btnReveal.style.visibility = item.info && item.info.out ? "visible" : "hidden";
  btnReveal.onclick = () => state.bridge.reveal(item.info.out);

  const btnDel = document.createElement("button");
  btnDel.className = "fi-btn";
  btnDel.title = t("removeTip");
  btnDel.innerHTML = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
  btnDel.onclick = () => {
    state.files.delete(String(item.path).toLowerCase());
    renderFiles();
  };

  top.append(cb, icon, nameBox, pill, btnReveal, btnDel);

  li.appendChild(top);

  // 只保留必要信息：检测速度、拉伸后速度（变速比）、节拍关系。
  // 失败/跳过原因例外——那是必须让用户看到的信息。其余备注（峰值限制等）不再显示，
  // 完整细节在 CSV 报告的「备注」列里。
  const parts = [];
  if (item.info && item.info.detected) {
    parts.push(`<span class="accent">${item.info.detected} BPM</span>`);
    if (item.info.a2) parts.push(`${item.info.a2}（${fmtPercent(item.info.rate)}）`);
    if (item.info.relation) parts.push(item.info.relation);
  }
  if (item.info && item.info.note && (item.status === "failed" || item.status === "skipped")) {
    const cls = item.status === "failed" ? "fi-note err" : "fi-note";
    parts.push(`<span class="${cls}">${escapeHtml(item.info.note)}</span>`);
  }
  if (parts.length) {
    const sub = document.createElement("div");
    sub.className = "fi-sub";
    sub.innerHTML = parts.join(" · ");
    li.appendChild(sub);
  }
  return li;
}

function updateFileStatus(payload) {
  const key = String(payload.path).toLowerCase();
  const item = state.files.get(key);
  if (!item) {
    addPaths([payload.path]);
  }
  const it = state.files.get(key);
  it.status = payload.status;
  it.info = payload;
  const li = document.querySelector(`.file-item[data-path="${CSS.escape(payload.path)}"]`);
  if (li) {
    const fresh = fileRow(it);
    li.replaceWith(fresh);
  } else {
    renderFiles();
  }
}

/* ---------------- 设置 ---------------- */
function readOptions() {
  const metroOn = $("#metronome").checked;
  const timbre = (document.querySelector(".timbre.on") || {}).dataset?.v || "woodblock";
  return {
    target: $("#targetBpm").value,
    manualBpm: $("#manualBpm").value.trim(),
    detectOnly: $("#detectOnly").checked,
    segments: $("#segments").checked,
    engine: document.querySelector("#engineSeg .on")?.dataset.v || "phasevocoder",
    outputFormat: document.querySelector("#formatSeg .on")?.dataset.v || "source",
    outDir: state.outDir || "",
    mirrorDirs: $("#mirrorDirs").checked,
    metronome: metroOn ? timbre : "",
    clickRelDb: parseFloat($("#clickRelDb").value),
    maxSlowdown: parseFloat($("#maxSlowdown").value),
    maxSpeedup: parseFloat($("#maxSpeedup").value),
    lang: curLang(),
  };
}

function saveSettings() {
  try { localStorage.setItem("tmc-settings", JSON.stringify(readOptions())); } catch (e) { }
}

function loadSettings() {
  let s = {};
  try { s = JSON.parse(localStorage.getItem("tmc-settings") || "{}"); } catch (e) { }
  if (s.target) $("#targetBpm").value = s.target;
  if (s.manualBpm) $("#manualBpm").value = s.manualBpm;
  if (s.detectOnly) $("#detectOnly").checked = true;
  if (s.segments) $("#segments").checked = true;
  if (s.engine) segSelect("#engineSeg", s.engine);
  // 旧版本存的 wav/mp3/flac 仍照用；没存过则保持 HTML 默认的"原类型"
  if (s.outputFormat) segSelect("#formatSeg", s.outputFormat);
  if (s.outDir) { state.outDir = s.outDir; }
  // 默认勾选；只有用户显式存过 false 才取消
  if (s.mirrorDirs === false) $("#mirrorDirs").checked = false;
  showOutDir();
  if (s.metronome) {
    $("#metronome").checked = true;
    timbreSelect(s.metronome);
  }
  if (s.clickRelDb !== undefined) $("#clickRelDb").value = s.clickRelDb;
  if (s.maxSlowdown !== undefined) $("#maxSlowdown").value = s.maxSlowdown;
  if (s.maxSpeedup !== undefined) $("#maxSpeedup").value = s.maxSpeedup;
  limitHintUpdate();
  metroPanelUpdate();
}

/* 变速上限：输入框数值与说明文字实时联动（一行短文案，避免换行占高） */
function limitHintUpdate() {
  const slow = clampPct($("#maxSlowdown").value);
  const fast = clampPct($("#maxSpeedup").value);
  const parts = [];
  parts.push(slow > 0 ? t("limitSlow", { n: slow }) : t("limitSlowNone"));
  parts.push(fast > 0 ? t("limitFast", { n: fast }) : t("limitFastNone"));
  let tail = t("limitTail");
  if (slow === 0 && fast === 0) tail = t("limitTailNone");
  $("#limitHint").textContent = parts.join(" · ") + tail;
}

/* 上限输入框：夹到 0~100 的整数；空/非法值回落到默认 */
function clampPct(v) {
  const n = parseFloat(v);
  if (!isFinite(n)) return 0;
  return Math.max(0, Math.min(100, Math.round(n)));
}

function segSelect(sel, v) {
  document.querySelectorAll(sel + " button").forEach(b => b.classList.toggle("on", b.dataset.v === v));
}

function timbreSelect(k) {
  document.querySelectorAll(".timbre").forEach(b => b.classList.toggle("on", b.dataset.v === k));
}

function metroPanelUpdate() {
  $("#metroPanel").classList.toggle("disabled", !$("#metronome").checked);
}

/* 音色卡片：中英两种名字都写进 DOM，由 CSS 按当前语言显示其中一个。
   切换语言时整块重建，这样 title（试听）也是当前语言。 */
function renderTimbres() {
  const box = $("#timbres");
  const cur = (document.querySelector(".timbre.on") || {}).dataset?.v || "woodblock";
  box.innerHTML = "";
  for (const tb of TIMBRES) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "timbre" + (tb.k === cur ? " on" : "");
    b.dataset.v = tb.k;
    b.innerHTML = `<span class="t-cn">${tb.cn}</span><span class="t-en">${tb.en}</span>` +
      `<i class="play" title="${t("previewTip")}"><svg viewBox="0 0 24 24" width="11" height="11" fill="currentColor"><polygon points="6 3 20 12 6 21"/></svg></i>`;
    b.onclick = () => { timbreSelect(tb.k); saveSettings(); playPreview(b, tb.k); };
    b.querySelector(".play").onclick = (e) => {
      e.stopPropagation();
      timbreSelect(tb.k); saveSettings();
      playPreview(b, tb.k);
    };
    box.appendChild(b);
  }
}

function showOutDir() {
  const el = $("#outDirInfo");
  const mirror = $("#mirrorDirs").checked;
  if (state.outDir) {
    el.textContent = mirror
      ? t("outDirMirror", { dir: state.outDir })
      : t("outDirFlat", { dir: state.outDir });
  } else {
    el.textContent = t("outDirDefault");
  }
  el.classList.toggle("strong", !!state.outDir);
  $("#btnOutDirReset").classList.toggle("hidden", !state.outDir);
  // 层级镜像只在指定了输出目录时才有意义；默认模式下按原目录就近输出，天然保持层级
  $("#mirrorWrap").classList.toggle("disabled", !state.outDir);
  $("#mirrorDirs").disabled = !state.outDir;
}

function setButtonsEnabled(on) {
  for (const id of ["#btnAddFiles", "#btnAddFolder", "#btnSelectAll", "#btnDelSel", "#btnClearDone"]) {
    $(id).disabled = !on;
  }
  updateSelCount();
  refreshStartBtn();
}

function refreshStartBtn() {
  const total = state.files.size;
  const sel = selList().length;
  // 勾了就只处理勾选的；一个都没勾则处理全部（保持原来的默认行为）
  const n = sel > 0 ? sel : total;
  $("#btnStart").disabled = !state.bridge || state.running || n === 0;
  $("#btnStart").textContent = (sel > 0 && sel < total) ? t("startSel", { n: sel })
    : (sel > 0 && sel === total) ? t("startAll", { n: total }) : t("start");
}

/* ---------------- 处理流程 ---------------- */
async function startRun() {
  if (state.running) return;
  const opts = readOptions();
  if (!opts.detectOnly) {
    const t = parseFloat(opts.target);
    if (!(t > 0)) { toast(t("needTarget"), true); return; }
  }
  // 有勾选 → 只处理勾选的；没勾选 → 处理全部
  const sel = selList();
  const list = sel.length ? sel : [...state.files.values()];
  const paths = list.map(f => f.path);
  // 每个文件对应的「添加时的根」——后端靠它把原文件夹层级在输出目录里重建
  const roots = list.map(f => f.root || parentOf(f.path));
  let res;
  try {
    res = await state.bridge.start(paths, opts, roots);
  } catch (e) {
    toast(t("startFailed", { err: e }), true);
    return;
  }
  if (!res || !res.ok) {
    toast((res && res.error) || t("startFailedPlain"), true);
    return;
  }
  state.running = true;
  $("#btnStart").disabled = true;
  $("#btnStop").disabled = false;
  $("#btnOpenReport").classList.add("hidden");
  $("#progressBar").style.width = "0%";
  $("#progressLabel").textContent = t("preparing", { n: res.count });
  $("#progressCount").textContent = "";
  $("#progressBar").classList.add("indeterminate");
  saveSettings();
}

function onProgress(p) {
  $("#progressBar").classList.remove("indeterminate");
  const pct = p.total ? Math.round(p.done / p.total * 100) : 0;
  $("#progressBar").style.width = pct + "%";
  $("#progressCount").textContent = `${p.done} / ${p.total}`;
  $("#progressLabel").textContent = t("processing");
}

function onDone(d) {
  state.running = false;
  $("#btnStop").disabled = true;
  refreshStartBtn();
  $("#progressBar").classList.remove("indeterminate");
  $("#progressBar").style.width = "100%";
  const skip = d.skipped || 0;
  const skipTxt = skip ? t("skipPart", { n: skip }) : "";
  const summary = d.stopped ? t("stoppedSummary", { ok: d.ok, fail: d.fail, skip: skipTxt })
    : t("doneSummary", { ok: d.ok, fail: d.fail, skip: skipTxt });
  $("#progressLabel").textContent = summary;
  logLine(summary, d.fail ? "l-err" : "l-ok");
  if (skip) logLine(t("skippedHint", { n: skip }), "l-err");
  toast(summary, !!(d.fail || skip));
  if (d.report) {
    state.report = d.report;
    $("#btnOpenReport").classList.remove("hidden");
  }
}

/* 后端事件入口 */
function __appEvent(payload) {
  switch (payload.type) {
    case "files-added": {
      // 后端逐文件回传所属根（拖入文件夹时才有意义），按文件对齐
      const roots = payload.roots || [];
      let n = 0;
      for (let i = 0; i < (payload.files || []).length; i++) {
        n += addPaths([payload.files[i]], roots[i]);
      }
      if (n) toast(t("addedFiles", { n }));
      break;
    }
    case "file-status":
      updateFileStatus(payload);
      break;
    case "progress":
      onProgress(payload);
      break;
    case "log":
      logLine(payload.text);
      break;
    case "started":
      logLine(t("startedRun", { n: payload.total }), "l-hl");
      break;
    case "done":
      onDone(payload);
      break;
  }
}
window.__appEvent = __appEvent;

/* ---------------- 事件绑定 ---------------- */
function bindUI() {
  // 文件
  $("#btnAddFiles").onclick = async () => {
    const files = await state.bridge.selectFiles();
    // 直接选的文件：根 = 各自所在目录（addPaths 里兜底算）
    if (files && files.length) addPaths(files);
  };
  $("#btnAddFolder").onclick = async () => {
    const d = await state.bridge.selectFolder();
    if (!d) return;
    let files = [d];
    try { files = await state.bridge.expand([d]) || [d]; } catch (e) { }
    // 关键：把这个文件夹本身作为根传进去，输出时才能重建它下面的层级
    const n = addPaths(files.length ? files : [d], d);
    if (!n) toast(t("noAudioInFolder"), true);
  };
  // 批量勾选操作
  $("#btnSelectAll").onclick = () => {
    const allSel = state.files.size > 0 && [...state.files.values()].every(f => f.checked);
    for (const f of state.files.values()) f.checked = !allSel;
    renderFiles();
  };
  $("#btnDelSel").onclick = () => {
    const n = [...state.files.values()].filter(f => f.checked).length;
    if (!n) { toast(t("selectFirst"), true); return; }
    for (const [k, f] of [...state.files.entries()]) if (f.checked) state.files.delete(k);
    renderFiles();
    toast(t("deletedN", { n }));
  };
  // 清除已处理：无条件清**当前所有**已完成（done）的，失败/跳过的留在列表里供查看。
  // 与勾选无关 —— 勾选只作用于「删除选中」和「开始处理」，不作用于这里。
  // 关键：每个文件各自带着自己的 root，所以清掉一部分后再处理剩下的，
  // 输出目录里的层级依然正确（不依赖"整批一起处理"）。
  $("#btnClearDone").onclick = () => {
    const doomed = [...state.files.values()]
      .filter(f => DONE_STATES.has(f.status))
      .map(f => String(f.path).toLowerCase());
    if (!doomed.length) {
      toast(t("nothingDone"), true);
      return;
    }
    for (const k of doomed) state.files.delete(k);
    renderFiles();
    toast(t("clearedDone", { n: doomed.length, left: state.files.size }));
  };

  // BPM 步进（去掉了滑块，只保留 ± 与文本框）
  const bpmInput = $("#targetBpm");
  const clampBpm = v => Math.min(300, Math.max(20, Math.round(parseFloat(v) * 10) / 10 || 20));
  const setBpm = (v) => {
    v = clampBpm(v);
    bpmInput.value = String(v);
    saveSettings();
  };
  $("#bpmMinus").onclick = () => setBpm(clampBpm(bpmInput.value) - 1);
  $("#bpmPlus").onclick = () => setBpm(clampBpm(bpmInput.value) + 1);
  bpmInput.onchange = () => setBpm(bpmInput.value);
  bpmInput.onblur = () => setBpm(bpmInput.value);

  // 分段选择
  document.querySelectorAll("#engineSeg button").forEach(b => b.onclick = () => { segSelect("#engineSeg", b.dataset.v); saveSettings(); });
  document.querySelectorAll("#formatSeg button").forEach(b => b.onclick = () => { segSelect("#formatSeg", b.dataset.v); saveSettings(); });

  // 节拍器
  $("#metronome").onchange = () => { metroPanelUpdate(); saveSettings(); };
  $("#clickRelDb").onchange = saveSettings;

  // 变速上限（文本框）
  $("#maxSlowdown").oninput = limitHintUpdate;
  $("#maxSlowdown").onchange = () => { $("#maxSlowdown").value = clampPct($("#maxSlowdown").value); limitHintUpdate(); saveSettings(); };
  $("#maxSpeedup").oninput = limitHintUpdate;
  $("#maxSpeedup").onchange = () => { $("#maxSpeedup").value = clampPct($("#maxSpeedup").value); limitHintUpdate(); saveSettings(); };

  $("#detectOnly").onchange = saveSettings;
  $("#segments").onchange = saveSettings;
  $("#manualBpm").onchange = saveSettings;

  // 音色渲染与试听（抽成 renderTimbres，切换语言时整块重刷）
  renderTimbres();

  // 输出目录
  $("#btnOutDir").onclick = async () => {
    const d = await state.bridge.selectOutDir();
    if (d !== undefined && d !== "") { state.outDir = d; }
    else if (d === "") { state.outDir = ""; }
    showOutDir(); saveSettings();
  };
  $("#btnOutDirReset").onclick = () => {
    state.outDir = "";
    showOutDir(); saveSettings();
    toast(t("restoredDefault"));
  };
  $("#mirrorDirs").onchange = () => { showOutDir(); saveSettings(); };

  // 开始/停止
  $("#btnStart").onclick = startRun;
  $("#btnStop").onclick = () => state.bridge.stop();

  // 报告
  $("#btnOpenReport").onclick = () => state.report && state.bridge.reveal(state.report);

  // 兼容界面（tkinter）：桥接不可用（浏览器预览）时给一句提示，不静默失败
  $("#btnTk").onclick = async () => {
    if (!state.bridge.launchTk) { toast(t("tkFail")); return; }
    // 后端返回 false 表示子进程没起来（它自己已经弹窗+写 app_error.log），
    // 这里再提示一次，免得用户点了没反应却以为程序卡住。
    try {
      const ok = await state.bridge.launchTk();
      if (ok === false) toast(t("tkErr"));
    } catch (e) {
      toast(t("tkErr"));
    }
  };

  // 日志折叠
  $("#logToggle").onclick = () => $("#logCard").classList.toggle("collapsed");

  // 拖拽（UI 反馈 + preventDefault；文件路径由后端 DOM 事件通道推送）
  let dragDepth = 0;
  window.addEventListener("dragenter", (e) => { e.preventDefault(); dragDepth++; $("#dropOverlay").classList.add("show"); });
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("dragleave", (e) => {
    e.preventDefault();
    if (--dragDepth <= 0) { dragDepth = 0; $("#dropOverlay").classList.remove("show"); }
  });
  window.addEventListener("drop", (e) => {
    e.preventDefault();
    dragDepth = 0;
    $("#dropOverlay").classList.remove("show");
    if (state.mock && e.dataTransfer && e.dataTransfer.files.length) {
      toast(t("dropInMock"), true);
    }
    // 真实环境：pywebview 的 document drop 通道会把完整路径推给后端，
    // 后端再通过 files-added 事件回传到这里。
  });
}

/* 音色试听 */
function playPreview(chip, timbre) {
  chip.classList.add("playing");
  setTimeout(() => chip.classList.remove("playing"), 900);
  Promise.resolve(state.bridge.preview(timbre)).then(url => {
    if (!url) return;
    if (!state.previewAudio) state.previewAudio = new Audio();
    state.previewAudio.src = url;
    state.previewAudio.play().catch(() => { });
  });
}

/* ---------------- 启动 ---------------- */
/* 主题：只有日间/夜间两态，**默认日间**（2026-10-13 用户要求删掉"跟随系统"）。
   点按钮在 light ↔ dark 之间来回切，选择存 localStorage。
   图标规则：当前实际是夜间 → 显示太阳（点它切走）；否则显示月亮。 */
function initTheme() {
  const root = document.documentElement;

  // 兼容老用户：以前存过跟随系统那个模式值，以及 <html> 上遗留的旧值，一律当 light
  const cur = () => (root.dataset.themeMode === "dark" ? "dark" : "light");

  const apply = (mode) => {
    root.dataset.themeMode = mode;
    root.dataset.theme = mode;
    syncThemeIcons();
  };

  $("#btnTheme").onclick = () => {
    const next = cur() === "dark" ? "light" : "dark";
    try { localStorage.setItem("tmc-theme", next); } catch (e) { }
    apply(next);
  };

  apply(cur());
}

/* 主题图标与提示语：抽成全局函数，切语言时也要跟着重刷 */
function syncThemeIcons() {
  const root = document.documentElement;
  // 图标表达"点下去会切到哪"：夜间给太阳、日间给月亮
  const dark = root.dataset.theme === "dark";
  $("#iconSun").classList.toggle("hidden", !dark);
  $("#iconMoon").classList.toggle("hidden", dark);
  $("#btnTheme").title = dark ? t("themeDark") : t("themeLight");
}

document.addEventListener("DOMContentLoaded", async () => {
  initLang();
  initTheme();
  bindUI();
  loadSettings();
  logLine(t("readyLog"), "l-hl");
  await initBridge();
  logLine(state.mock ? t("mockLog") : t("liveLog"));
});
