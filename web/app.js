const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const state = {
  preset: "best",
  mode: "merge",
  wm: false,
  wmSize: "m",
  tasks: new Map(),
  els: new Map(),
  logSeq: 0,
  status: null,
  preview: null,
  tab: "log",
  pollTimer: null,
};

const PRESET_LABEL = {
  best: "最佳画质", "2160": "2160P", "1080": "1080P", "720": "720P",
  "audio-mp3": "MP3 音频", "audio-m4a": "M4A 音频", custom: "自定义",
};

/* ------------------------------------------------ 工具函数 */
const fmtBytes = (n) => {
  if (!n || n <= 0) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return (v >= 100 ? v.toFixed(0) : v.toFixed(1)) + u[i];
};
const fmtSpeed = (n) => (!n || n <= 0 ? "" : fmtBytes(n) + "/s");
const fmtEta = (s) => {
  if (!s || s <= 0 || !isFinite(s)) return "";
  s = Math.round(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`
               : `${m}:${String(sec).padStart(2, "0")}`;
};
const fmtDur = (s) => {
  if (!s || s <= 0) return "";
  s = Math.round(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h > 0 ? `${h} 小时 ${m} 分` : `${m} 分 ${sec} 秒`;
};
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function toast(msg, kind = "", ms = 2600) {
  const el = document.createElement("div");
  el.className = "toast " + kind;
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => {
    el.style.transition = "opacity .25s,transform .25s";
    el.style.opacity = "0";
    el.style.transform = "translateY(6px)";
    setTimeout(() => el.remove(), 260);
  }, ms);
}

async function api(path, body) {
  const opt = body
    ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
    : {};
  const r = await fetch(path, opt);
  return r.json();
}

/* ------------------------------------------------ 初始化 */
async function refreshStatus() {
  const s = await api("/api/status");
  state.status = s;
  const cv = $("#chipVer");
  cv.classList.toggle("on", s.version && s.version !== "未检测到");
  cv.classList.toggle("off", !s.version || s.version === "未检测到");
  cv.innerHTML = `<i class="dot"></i>yt-dlp ${esc(s.version)}`;
  const cf = $("#chipFfmpeg");
  cf.classList.toggle("on", !!s.ffmpeg);
  cf.classList.toggle("off", !s.ffmpeg);
  cf.innerHTML = `<i class="dot"></i>ffmpeg ${s.ffmpeg ? "就绪" : "缺失"}`;
  $("#chipDir").textContent = s.outdir;
  if (!$("#outdir").value) $("#outdir").value = s.outdir;
  return s;
}

async function boot() {
  let s;
  try {
    s = await refreshStatus();
  } catch (e) {
    toast("连不上本地服务，请确认是通过「启动工作台.bat」打开的页面", "err", 8000);
    return;
  }
  if (!s.ffmpeg) toast("未检测到 ffmpeg，合并/转码会失败", "warn", 5000);

  state.pollTimer = setInterval(pollTasks, 700);
  setInterval(pollLogs, 1000);
  pollTasks();
  pollLogs();
  loadHistory();
}

/* ------------------------------------------------ 任务轮询与渲染 */
async function pollTasks() {
  let data;
  try { data = await api("/api/tasks"); } catch (e) { return; }
  if (!data.ok) return;
  const list = data.tasks || [];
  const seen = new Set();

  list.forEach((t) => {
    seen.add(t.id);
    let card = state.els.get(t.id);
    if (!card) card = createCard(t);
    updateCard(card, t);
    state.tasks.set(t.id, t);
  });

  state.els.forEach((card, id) => {
    if (!seen.has(id)) { card.el.remove(); state.els.delete(id); state.tasks.delete(id); }
  });

  // 保持顺序
  const box = $("#tasks");
  list.forEach((t) => {
    const c = state.els.get(t.id);
    if (c && c.el.parentNode === box) box.appendChild(c.el);
  });

  $("#emptyState").classList.toggle("hidden", list.length > 0);
  const st = data.stats || {};
  $("#stRun").textContent = st.running || 0;
  $("#stQueue").textContent = st.queued || 0;
  $("#stDone").textContent = st.done || 0;
  $("#stErr").textContent = (st.error || 0) + (st.canceled || 0);
}

const STATUS_TEXT = {
  queued: "排队中", downloading: "下载中", processing: "处理中",
  done: "已完成", error: "失败", canceled: "已取消",
};

function createCard(t) {
  const el = document.createElement("div");
  el.className = "task";
  el.dataset.id = t.id;
  el.innerHTML = `
    <div class="t-thumb">
      <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round">
        <rect x="3" y="5" width="18" height="14" rx="3"/><path d="M10 9.5l5 2.5-5 2.5v-5Z" fill="currentColor" stroke="none" opacity=".55"/>
      </svg>
    </div>
    <div class="t-main">
      <div class="t-row1">
        <div class="t-title"></div>
        <div class="t-pct"></div>
      </div>
      <div class="t-meta"></div>
      <div class="t-bar"><i></i></div>
      <div class="t-err hidden"></div>
    </div>
    <div class="t-actions"></div>`;
  const c = {
    el,
    thumb: el.querySelector(".t-thumb"),
    title: el.querySelector(".t-title"),
    pct: el.querySelector(".t-pct"),
    meta: el.querySelector(".t-meta"),
    bar: el.querySelector(".t-bar i"),
    err: el.querySelector(".t-err"),
    actions: el.querySelector(".t-actions"),
    lastStatus: null,
    thumbSet: false,
  };
  state.els.set(t.id, c);
  $("#tasks").appendChild(el);
  return c;
}

function updateCard(c, t) {
  const el = c.el;
  el.className = "task " + (t.status === "downloading" || t.status === "processing" ? "running " : "") + t.status;

  // 标题
  if (c.title.textContent !== t.title) c.title.textContent = t.title;

  // 封面（预加载成功后再铺上，避免出现白块）
  if (!c.thumbSet && t.thumb) {
    c.thumbSet = true;
    const probe = new Image();
    probe.onload = () => {
      c.thumb.style.backgroundImage = `url("${t.thumb}")`;
      c.thumb.style.backgroundSize = "cover";
      c.thumb.style.backgroundPosition = "center";
      const icon = c.thumb.querySelector("svg");
      if (icon) icon.style.display = "none";
    };
    probe.src = t.thumb;
  }

  // 百分比
  let pct = "";
  if (t.status === "done") pct = "100%";
  else if (t.status === "error" || t.status === "canceled") pct = "—";
  else if (t.percent > 0) pct = t.percent.toFixed(t.percent >= 99.95 ? 0 : 1) + "%";
  else pct = t.status === "queued" ? "等待" : "…";
  c.pct.textContent = pct;

  // 进度条
  const w = t.status === "done" ? 100 : Math.max(t.percent || 0, t.status === "downloading" ? 1.5 : 0);
  c.bar.style.width = w + "%";

  // 元信息
  const parts = [];
  parts.push(`<span class="badge ${t.status}">${STATUS_TEXT[t.status] || t.status}</span>`);
  if (t.track === "video") parts.push('<span class="tag tag-video">视频轨</span>');
  else if (t.track === "audio") parts.push('<span class="tag tag-audio">音频轨</span>');
  if (t.wm) parts.push(`<span class="tag tag-wm">${t.wm_done ? "已去水印" : "去水印"}</span>`);
  if (t.host) parts.push(`<span class="host">${esc(t.host)}</span>`);
  if (t.status === "downloading") {
    if (t.speed) parts.push(`<span>${fmtSpeed(t.speed)}</span>`);
    if (t.eta) parts.push(`<span>剩余 ${fmtEta(t.eta)}</span>`);
    if (t.total) parts.push(`<span>${fmtBytes(t.downloaded)} / ${fmtBytes(t.total)}</span>`);
    if (t.part > 1) parts.push(`<span>第 ${t.part} 路流</span>`);
    if (t.phase && t.phase !== "获取信息") parts.push(`<span>${esc(t.phase)}</span>`);
  } else if (t.status === "processing") {
    parts.push(`<span>${esc(t.phase || "后处理")}</span>`);
    if (t.total) parts.push(`<span>${fmtBytes(t.total)}</span>`);
  } else if (t.status === "done") {
    if (t.total) parts.push(`<span>${fmtBytes(t.total)}</span>`);
    if (t.started && t.finished) parts.push(`<span>耗时 ${Math.max(1, Math.round(t.finished - t.started))} 秒</span>`);
  } else if (t.status === "queued") {
    parts.push(`<span>${PRESET_LABEL[t.preset] || "最佳画质"}</span>`);
  }
  c.meta.innerHTML = parts.join('<span class="sep">·</span>');

  // 错误
  if (t.error && t.status === "error") {
    c.err.classList.remove("hidden");
    c.err.textContent = t.error;
  } else {
    c.err.classList.add("hidden");
  }

  // 操作按钮
  const acts = [];
  if (["queued", "downloading", "processing"].includes(t.status)) {
    acts.push(`<button class="icon-btn danger" data-act="cancel" title="取消">✕</button>`);
  } else if (t.status === "done") {
    acts.push(`<button class="icon-btn" data-act="openfile" title="播放 / 打开文件">▶</button>`);
    acts.push(`<button class="icon-btn" data-act="wm" title="去水印（框选区域）">✂</button>`);
    acts.push(`<button class="icon-btn" data-act="upscale" title="画质提升（AI 超分）">⬆</button>`);
    acts.push(`<button class="icon-btn" data-act="openfolder" title="打开所在目录">📁</button>`);
  } else {
    acts.push(`<button class="icon-btn" data-act="retry" title="重试">↻</button>`);
    acts.push(`<button class="icon-btn danger" data-act="remove" title="移除">✕</button>`);
  }
  const sig = acts.join("");
  if (c.actions.dataset.sig !== sig) {
    c.actions.dataset.sig = sig;
    c.actions.innerHTML = sig;
  }
}

/* 任务卡片按钮 */
$("#tasks").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-act]");
  if (!btn) return;
  const card = e.target.closest(".task");
  if (!card) return;
  const id = card.dataset.id;
  const t = state.tasks.get(id);
  const act = btn.dataset.act;
  if (act === "cancel") {
    await api("/api/cancel", { id });
    toast("已请求取消", "warn");
  } else if (act === "retry") {
    await api("/api/retry", { id });
    toast("已重新加入队列", "ok");
  } else if (act === "remove") {
    await api("/api/remove", { id });
  } else if (act === "openfile") {
    const r = await api("/api/openfile", { path: t?.filename || "" });
    if (!r.ok) toast(r.error || "文件不存在", "err");
  } else if (act === "wm") {
    wmOpen(id);
  } else if (act === "upscale") {
    upscaleOpen(id);
  } else if (act === "openfolder") {
    const r = await api("/api/open", { path: t?.filename || $("#outdir").value });
    if (!r.ok) toast(r.error || "打开失败", "err");
  }
});

/* ------------------------------------------------ 日志 */
async function pollLogs() {
  let data;
  try { data = await api("/api/logs?after=" + state.logSeq); } catch (e) { return; }
  if (!data.ok) return;
  if (data.lines && data.lines.length) {
    const body = $("#logBody");
    const frag = document.createDocumentFragment();
    data.lines.forEach((l) => {
      const d = document.createElement("div");
      d.className = "log-line " + (l.level || "info");
      d.innerHTML = `<span class="lt">${l.t}</span><span class="lx">${esc(l.text)}</span>`;
      frag.appendChild(d);
    });
    body.appendChild(frag);
    while (body.childElementCount > 700) body.removeChild(body.firstElementChild);
    if ($("#autoScroll").checked) body.scrollTop = body.scrollHeight;
  }
  if (typeof data.last === "number") state.logSeq = data.last;
}

/* ------------------------------------------------ 历史 */
async function loadHistory() {
  const data = await api("/api/history");
  const box = $("#historyBody");
  const items = data.items || [];
  if (!items.length) {
    box.innerHTML = `<div class="log-empty">还没有下载记录</div>`;
    return;
  }
  box.innerHTML = items.map((h) => `
    <div class="hist-item">
      <span class="${h.status === "done" ? "h-ok" : "h-err"}">${h.status === "done" ? "✔" : "✘"}</span>
      <span class="h-t" title="${esc(h.title)}">${esc(h.title || h.url)}</span>
      <span class="h-size">${fmtBytes(h.size)}</span>
      <span class="h-time">${new Date(h.time * 1000).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}</span>
      ${h.file ? `<span class="h-open" data-open="${esc(h.file)}" title="打开文件">↗</span>` : ""}
    </div>`).join("");
}

$("#historyBody").addEventListener("click", async (e) => {
  const el = e.target.closest("[data-open]");
  if (!el) return;
  const r = await api("/api/openfile", { path: el.dataset.open });
  if (!r.ok) toast(r.error || "文件不存在", "err");
});

/* ------------------------------------------------ 预设选择 */
$("#presets").addEventListener("click", (e) => {
  const btn = e.target.closest(".preset");
  if (!btn) return;
  $$(".preset").forEach((b) => b.classList.remove("active"));
  btn.classList.add("active");
  state.preset = btn.dataset.preset;
});

/* ------------------------------------------------ 输出模式 / 去水印 */
const MODE_HINT = {
  merge: "音视频合成一个文件，直接能看",
  split: "拆成「视频轨 + 音频轨」两个文件，丢进剪辑软件用",
};

$("#modeSeg").addEventListener("click", (e) => {
  const b = e.target.closest(".seg-btn");
  if (!b) return;
  $$("#modeSeg .seg-btn").forEach((x) => x.classList.remove("active"));
  b.classList.add("active");
  state.mode = b.dataset.mode;
  $("#modeHint").textContent = MODE_HINT[state.mode];
  $("#audioFmtWrap").classList.toggle("hidden", state.mode !== "split");
});

$("#btnWm").addEventListener("click", () => {
  state.wm = !state.wm;
  $("#btnWm").classList.toggle("on", state.wm);
  $("#wmState").textContent = state.wm ? "已开启 · 下载后自动处理" : "未开启";
  $("#wmOpts").classList.toggle("hidden", !state.wm);
  if (state.wm) {
    // 只补足刚好能看到参数面板所需的距离，不把上面的链接框顶出视野
    setTimeout(() => {
      const sc = document.querySelector(".side-scroll");
      const panel = $("#wmOpts");
      const over = panel.getBoundingClientRect().bottom - sc.getBoundingClientRect().bottom + 10;
      if (over > 0) sc.scrollTo({ top: sc.scrollTop + over, behavior: "smooth" });
    }, 90);
  }
});

$("#wmSize").addEventListener("click", (e) => {
  const b = e.target.closest(".seg-btn");
  if (!b) return;
  $$("#wmSize .seg-btn").forEach((x) => x.classList.remove("active"));
  b.classList.add("active");
  state.wmSize = b.dataset.size;
});

/* ------------------------------------------------ 解析 */
$("#btnParse").addEventListener("click", async () => {
  const url = firstUrl();
  if (!url) return toast("请先粘贴链接", "warn");
  const btn = $("#btnParse");
  btn.disabled = true;
  btn.textContent = "解析中…";
  const pv = $("#preview");
  pv.classList.remove("hidden");
  pv.innerHTML = `<div class="pv-err" style="color:var(--muted)">正在获取视频信息…</div>`;
  try {
    const r = await api("/api/parse", { url });
    if (!r.ok) {
      pv.innerHTML = `<div class="pv-err">${esc(r.error || "解析失败")}</div>`;
    } else {
      state.preview = r;
      renderPreview(r);
      if (r.is_playlist) $("#optPlaylist").checked = true;
    }
  } catch (err) {
    pv.innerHTML = `<div class="pv-err">${esc(err.message)}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "解析信息";
  }
});

function renderPreview(r) {
  const pv = $("#preview");
  pv.classList.remove("hidden");
  const chips = (r.heights || []).map((h) => `<button class="h-chip" data-h="${h}">${h}P</button>`).join("");
  const entries = (r.entries || []).slice(0, 40).map((e, i) =>
    `<div class="pv-entry" data-url="${esc(e.url)}"><b>${i + 1}</b><span>${esc(e.title)}</span></div>`).join("");
  pv.innerHTML = `
    <div class="pv-main">
      ${r.thumbnail ? `<img class="pv-thumb" src="${esc(r.thumbnail)}" onerror="this.style.display='none'"/>` : `<div class="pv-thumb"></div>`}
      <div class="pv-meta">
        <div class="pv-title">${esc(r.title)}</div>
        <div class="pv-sub">
          ${r.uploader ? `<span>${esc(r.uploader)}</span>` : ""}
          ${r.duration ? `<span>${fmtDur(r.duration)}</span>` : ""}
          ${r.is_playlist ? `<span class="pv-badge">播放列表 ${r.playlist_count || ""}</span>` : ""}
          ${r.extractor ? `<span>${esc(r.extractor)}</span>` : ""}
        </div>
      </div>
    </div>
    ${chips ? `<div class="pv-heights">${chips}</div>` : ""}
    ${entries ? `<div class="pv-entries">${entries}</div>` : ""}`;
}

$("#preview").addEventListener("click", (e) => {
  const h = e.target.closest(".h-chip");
  if (h) {
    const height = Number(h.dataset.h);
    const map = { 2160: "2160", 1440: "custom", 1080: "1080", 720: "720", 480: "custom", 360: "custom" };
    const preset = map[height] || "custom";
    $$(".preset").forEach((b) => b.classList.toggle("active", b.dataset.preset === preset));
    state.preset = preset;
    if (preset === "custom") {
      $("#customFormat").value = `bv*[height<=${height}]+ba/b[height<=${height}]`;
      toast(`已设为 ${height}P（自定义格式）`, "ok");
    } else {
      $("#customFormat").value = "";
      toast(`已切换画质：${height}P`, "ok");
    }
    return;
  }
  const entry = e.target.closest(".pv-entry");
  if (entry && entry.dataset.url) {
    const ta = $("#urls");
    ta.value = (ta.value.trim() ? ta.value.trim() + "\n" : "") + entry.dataset.url;
    toast("已加入链接框", "ok", 1500);
  }
});

/* ------------------------------------------------ 输入辅助 */
const firstUrl = () => ($("#urls").value.split("\n").map((s) => s.trim()).filter(Boolean)[0] || "");

$("#btnPaste").addEventListener("click", async () => {
  try {
    const t = await navigator.clipboard.readText();
    if (!t) return toast("剪贴板是空的", "warn");
    const ta = $("#urls");
    ta.value = (ta.value.trim() ? ta.value.trim() + "\n" : "") + t.trim();
    toast("已粘贴", "ok", 1400);
  } catch (e) {
    toast("浏览器不允许读取剪贴板，请手动 Ctrl+V", "warn");
  }
});
$("#btnClearUrls").addEventListener("click", () => {
  $("#urls").value = "";
  $("#preview").classList.add("hidden");
  state.preview = null;
});
$("#btnToggleAdv").addEventListener("click", (e) => {
  const body = $("#advBody");
  const hidden = body.classList.toggle("collapsed");
  e.target.textContent = hidden ? "展开" : "收起";
});
$("#btnClearLog").addEventListener("click", async () => {
  if (state.tab === "history") {
    await api("/api/clearhistory");
    loadHistory();
  } else {
    await api("/api/clearlog");
    $("#logBody").innerHTML = "";
    state.logSeq = 0;
  }
});
$("#btnImport").addEventListener("click", async () => {
  const b = $("#btnImport");
  const old = b.innerHTML;
  b.disabled = true;
  b.textContent = "选择文件…";
  // 系统对话框要等 PowerShell 起来，约 1–2 秒；超时还没回来就提示一句，
  // 免得用户以为又点了没反应
  const hint = setTimeout(() => {
    toast("文件选择窗口已打开，没看到的话按 Alt+Tab 找一下", "warn", 4200);
  }, 1600);
  try {
    const r = await api("/api/import", { pick: true });
    if (r.cancelled) return;
    if (!r.ok) return toast(r.error || "导入失败", "err", 3600);
    toast(`已导入 ${r.ids.length} 个文件`, "ok", 2200);
    if (r.errors && r.errors.length) toast(r.errors[0], "warn", 3600);
    pollTasks();
  } catch (e) {
    toast("导入失败：" + e.message, "err", 3600);
  } finally {
    clearTimeout(hint);
    b.disabled = false;
    b.innerHTML = old;
  }
});
$("#btnClearDone").addEventListener("click", async () => {
  await api("/api/clear", { mode: "finished" });
  toast("已清理结束的任务", "ok", 1600);
});
$("#btnOpenDir").addEventListener("click", () => api("/api/open", { path: $("#outdir").value }));
$("#btnUpdate").addEventListener("click", async () => {
  const b = $("#btnUpdate");
  const old = b.innerHTML;
  b.disabled = true;
  b.textContent = "更新中…";
  try {
    const r = await api("/api/update", {});
    await refreshStatus();
    if (r.ok) toast(`yt-dlp 已检查/更新完成，当前版本 ${r.version}`, "ok", 4200);
    else toast(r.error || "更新失败", "err", 4000);
  } catch (e) {
    toast("更新请求失败：" + e.message, "err");
  } finally {
    b.disabled = false;
    b.innerHTML = old;
  }
});
$("#chipDir").addEventListener("click", () => api("/api/open", { path: $("#outdir").value }));
$("#btnPick").addEventListener("click", async () => {
  toast("正在打开目录选择窗口…", "", 1500);
  const r = await api("/api/pickfolder", {});
  if (r.ok && r.path) {
    $("#outdir").value = r.path;
    $("#chipDir").textContent = r.path;
    await api("/api/config", { outdir: r.path });
    toast("保存位置已更新", "ok");
  } else {
    toast("未选择目录，可直接手动输入路径", "warn");
  }
});

$$(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    $$(".tab").forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    state.tab = tab.dataset.tab;
    $("#logBody").classList.toggle("hidden", state.tab !== "log");
    $("#historyBody").classList.toggle("hidden", state.tab !== "history");
    if (state.tab === "history") loadHistory();
  });
});

/* ------------------------------------------------ 开始下载 */
function collectOpts() {
  const heightMap = { "2160": 2160, "1080": 1080, "720": 720 };
  return {
    preset: state.preset,
    mode: state.mode,
    height: heightMap[state.preset] || 0,
    format: $("#customFormat").value.trim(),
    audioFmt: $("#audioFmt").value,
    watermark: {
      enabled: state.wm,
      mode: $("#wmMode").value,
      pos: $("#wmPos").value,
      size: state.wmSize,
    },
    outdir: $("#outdir").value.trim(),
    playlist: $("#optPlaylist").checked,
    subs: $("#optSubs").checked,
    autoSubs: $("#optAutoSubs").checked,
    embedSubs: $("#optEmbedSubs").checked,
    embedThumb: $("#optEmbedThumb").checked,
    metadata: $("#optMetadata").checked,
    chapters: $("#optChapters").checked,
    archive: $("#optArchive").checked,
    subLangs: $("#subLangs").value.trim() || "zh.*,en.*",
    cookies: $("#cookies").value,
    proxy: $("#proxy").value.trim(),
    rateLimit: $("#rateLimit").value.trim(),
    fragments: Number($("#fragments").value) || 4,
    _thumb: state.preview && state.preview.thumbnail ? state.preview.thumbnail : "",
  };
}

async function startDownload() {
  const urls = $("#urls").value.split("\n").map((s) => s.trim()).filter(Boolean);
  if (!urls.length) return toast("请先粘贴至少一个链接", "warn");
  const opts = collectOpts();
  if (urls.length === 1 && state.preview && state.preview.title) {
    opts._title = state.preview.title;
  }
  if (!opts.outdir) return toast("请设置保存目录", "warn");
  const btn = $("#btnStart");
  btn.disabled = true;
  try {
    const r = await api("/api/download", { urls, opts });
    if (r.ok) {
      const extra = opts.mode === "split" ? "（视频轨 + 音频轨）" : "";
      toast(`已加入 ${r.ids.length} 个任务${extra}`, "ok", 3200);
      $("#preview").classList.add("hidden");
      if (!$("#optPlaylist").checked) $("#urls").value = "";
      await api("/api/config", { outdir: opts.outdir });
    } else {
      toast(r.error || "提交失败", "err", 4000);
    }
  } catch (e) {
    toast("请求失败：" + e.message, "err");
  } finally {
    btn.disabled = false;
    pollTasks();
  }
}

$("#btnStart").addEventListener("click", startDownload);
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); startDownload(); }
});

boot();

/* ================================================ 去水印编辑器 */
const wmS = {
  taskId: "", W: 0, H: 0, regions: [], sel: -1, drag: null, aiReady: false, cw: 0, ch: 0,
};

function wmPosAt(r, t) {
  const tr = r.track || [];
  if (tr.length < 2) return { x: r.x, y: r.y };
  if (t <= tr[0].t) return { x: tr[0].x, y: tr[0].y };
  const last = tr[tr.length - 1];
  if (t >= last.t) return { x: last.x, y: last.y };
  for (let i = 0; i < tr.length - 1; i++) {
    const a = tr[i], b = tr[i + 1];
    if (t >= a.t && t <= b.t) {
      const k = (t - a.t) / ((b.t - a.t) || 1);
      return { x: a.x + (b.x - a.x) * k, y: a.y + (b.y - a.y) * k };
    }
  }
  return { x: r.x, y: r.y };
}

function wmSyncCanvas() {
  const v = $("#wmVideo"), c = $("#wmCanvas");
  const vb = v.getBoundingClientRect();
  if (!vb.width || !vb.height) return;
  const dpr = window.devicePixelRatio || 1;
  c.width = Math.round(vb.width * dpr);
  c.height = Math.round(vb.height * dpr);
  wmS.cw = vb.width;
  wmS.ch = vb.height;
}

function fmtClock(s) {
  s = Math.max(0, Math.floor(s || 0));
  const m = Math.floor(s / 60), sec = s % 60;
  return m + ":" + String(sec).padStart(2, "0");
}

function wmRender() {
  const c = $("#wmCanvas");
  if (!c || !wmS.cw) return;
  const ctx = c.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, wmS.cw, wmS.ch);
  const t = $("#wmVideo").currentTime || 0;

  wmS.regions.forEach((r, i) => {
    const p = wmPosAt(r, t);
    const x = p.x * wmS.cw, y = p.y * wmS.ch;
    const w = r.w * wmS.cw, h = r.h * wmS.ch;
    const on = i === wmS.sel;
    ctx.setLineDash((r.track || []).length > 1 ? [] : [5, 4]);
    ctx.lineWidth = on ? 2 : 1.5;
    ctx.strokeStyle = on ? "#4c8dff" : "rgba(76,141,255,.62)";
    ctx.strokeRect(x, y, w, h);
    ctx.fillStyle = on ? "rgba(76,141,255,.17)" : "rgba(76,141,255,.06)";
    ctx.fillRect(x, y, w, h);
    ctx.setLineDash([]);
    ctx.fillStyle = on ? "#8fb6ff" : "rgba(140,175,235,.8)";
    ctx.font = "600 11px Inter, system-ui, sans-serif";
    ctx.fillText(String(i + 1), x + 4, Math.max(12, y - 5));
    // 轨迹点
    if (on && (r.track || []).length) {
      r.track.forEach((k) => {
        ctx.beginPath();
        ctx.arc(k.x * wmS.cw, k.y * wmS.ch, 3, 0, Math.PI * 2);
        ctx.fillStyle = "#ffb020";
        ctx.fill();
      });
    }
  });

  if (wmS.drag) {
    const d = wmS.drag;
    let x, y, w, h;
    if (d.mode === "new") {
      x = Math.min(d.x0, d.x1); y = Math.min(d.y0, d.y1);
      w = Math.abs(d.x1 - d.x0); h = Math.abs(d.y1 - d.y0);
    } else {
      x = d.cur.x; y = d.cur.y; w = d.w0; h = d.h0;
    }
    ctx.setLineDash([4, 3]);
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = "#35d07f";
    ctx.strokeRect(x * wmS.cw, y * wmS.ch, w * wmS.cw, h * wmS.ch);
    ctx.fillStyle = "rgba(53,208,127,.13)";
    ctx.fillRect(x * wmS.cw, y * wmS.ch, w * wmS.cw, h * wmS.ch);
  }
}

function wmList() {
  const box = $("#wmRegions");
  $("#wmCount").textContent = wmS.regions.length;
  if (!wmS.regions.length) {
    box.innerHTML = '<p class="wm-empty">还没有区域，在左侧画面上拖拽框选</p>';
    return;
  }
  box.innerHTML = wmS.regions.map((r, i) => {
    const mv = (r.track || []).length > 1;
    return `<div class="wm-reg ${i === wmS.sel ? "on" : ""}" data-i="${i}">
      <span class="dot"></span>
      <span class="nm">区域 ${i + 1} · ${Math.round(r.w * 100)}%×${Math.round(r.h * 100)}%</span>
      ${mv ? `<span class="mv">轨迹 ${r.track.length}</span>` : ""}
      <button class="x" data-del="${i}" title="删除">✕</button>
    </div>`;
  }).join("");
}

function wmUpdateTime() {
  const v = $("#wmVideo");
  if (!v.duration || !isFinite(v.duration)) return;
  $("#wmSeek").value = Math.round((v.currentTime / v.duration) * 1000);
  $("#wmTime").textContent = fmtClock(v.currentTime) + " / " + fmtClock(v.duration);
}

async function wmOpen(taskId) {
  const t = state.tasks.get(taskId);
  if (!t || !t.filename) return toast("找不到文件，可能已被移动", "err");
  wmS.taskId = taskId;
  wmS.regions = [];
  wmS.sel = -1;
  wmS.drag = null;
  $("#wmModal").classList.remove("hidden");
  $("#wmSub").textContent = t.title || "";
  $("#wmRegions").innerHTML = '<p class="wm-empty">正在加载视频…</p>';
  const v = $("#wmVideo");
  const enc = encodeURIComponent(t.filename);
  v.poster = "/api/thumb?path=" + enc;   // 首帧缩略图，打开即有画面
  v.src = "/api/media?path=" + enc;
  $("#wmPlay").textContent = "▶";
  v.onloadedmetadata = () => {
    wmS.W = v.videoWidth;
    wmS.H = v.videoHeight;
    $("#wmInfo").textContent = v.videoWidth + "×" + v.videoHeight + " · " + fmtClock(v.duration);
    // 浏览器在无用户交互时只拉元数据不拉画面（readyState 停在 1），
    // 这里静音播一下再暂停，强制渲染首帧，避免编辑器一打开是黑屏
    v.muted = true;
    const kick = v.play();
    if (kick && kick.then) {
      kick.then(() => {
        setTimeout(() => {
          try { v.pause(); v.currentTime = 0.05; } catch (e) {}
          v.muted = false;
          wmSyncCanvas(); wmRender(); wmUpdateTime();
        }, 200);
      }).catch(() => {
        v.muted = false;
        setTimeout(() => { wmSyncCanvas(); wmRender(); wmUpdateTime(); }, 100);
      });
    }
    setTimeout(() => { wmSyncCanvas(); wmRender(); wmList(); wmUpdateTime(); }, 150);
  };
  v.oncanplay = () => {
    $("#wmInfo").textContent = v.videoWidth + "×" + v.videoHeight + " · " + fmtClock(v.duration);
    wmSyncCanvas(); wmRender();
  };
  v.onloadeddata = () => { wmSyncCanvas(); wmRender(); };
  v.onerror = () => {
    const err = v.error ? ("代码 " + v.error.code) : "";
    $("#wmInfo").textContent = "加载失败 " + err;
    toast("视频加载失败" + (err ? "（" + err + "）" : "") + "，可能是编码不被浏览器支持", "err", 6000);
  };
  const st = await api("/api/ai/status");
  wmS.aiReady = !!st.ready;
  $("#wmAiState").textContent = st.ready ? "AI 引擎已就绪" : (st.error || "AI 引擎未就绪");
  $("#wmAiSetup").classList.toggle("hidden", !!st.ready);
  $("#wmAiRow").classList.toggle("hidden", $("#wmEngine").value !== "ai");
}

function wmClose() {
  $("#wmModal").classList.add("hidden");
  const v = $("#wmVideo");
  try { v.pause(); } catch (e) {}
  v.removeAttribute("src");
  $("#wmSeek").value = 0;
  $("#wmTime").textContent = "0:00 / 0:00";
  $("#wmPlay").textContent = "▶";
}

/* 画布交互 */
(function wmBind() {
  const c = $("#wmCanvas");
  if (!c) return;
  const rel = (e) => {
    const r = c.getBoundingClientRect();
    return { x: (e.clientX - r.left) / r.width, y: (e.clientY - r.top) / r.height };
  };

  c.addEventListener("mousedown", (e) => {
    if (!wmS.cw) return;
    const p = rel(e);
    const t = $("#wmVideo").currentTime || 0;
    let hit = -1;
    wmS.regions.forEach((rg, i) => {
      const q = wmPosAt(rg, t);
      if (p.x >= q.x && p.x <= q.x + rg.w && p.y >= q.y && p.y <= q.y + rg.h) hit = i;
    });
    if (hit >= 0) {
      wmS.sel = hit;
      const rg = wmS.regions[hit], q = wmPosAt(rg, t);
      wmS.drag = { mode: "move", i: hit, ox: p.x - q.x, oy: p.y - q.y, w0: rg.w, h0: rg.h,
                   cur: { x: q.x, y: q.y } };
      wmList(); wmRender();
      return;
    }
    wmS.drag = { mode: "new", x0: p.x, y0: p.y, x1: p.x, y1: p.y };
    wmRender();
  });

  window.addEventListener("mousemove", (e) => {
    if (!wmS.drag) return;
    const p = rel(e);
    if (wmS.drag.mode === "new") {
      wmS.drag.x1 = Math.max(0, Math.min(1, p.x));
      wmS.drag.y1 = Math.max(0, Math.min(1, p.y));
    } else {
      const d = wmS.drag;
      d.cur = {
        x: Math.max(0, Math.min(1 - d.w0, p.x - d.ox)),
        y: Math.max(0, Math.min(1 - d.h0, p.y - d.oy)),
      };
    }
    wmRender();
  });

  window.addEventListener("mouseup", () => {
    const d = wmS.drag;
    if (!d) return;
    wmS.drag = null;
    if (d.mode === "new") {
      const x = Math.min(d.x0, d.x1), y = Math.min(d.y0, d.y1);
      const w = Math.abs(d.x1 - d.x0), h = Math.abs(d.y1 - d.y0);
      if (w > 0.008 && h > 0.008) {
        wmS.regions.push({ x, y, w, h, track: [] });
        wmS.sel = wmS.regions.length - 1;
        wmList();
      }
    } else {
      const rg = wmS.regions[d.i];
      if (rg) {
        rg.x = d.cur.x;
        rg.y = d.cur.y;
        // 已有多点轨迹时，同步移动当前时刻对应的点
        const t = $("#wmVideo").currentTime || 0;
        if ((rg.track || []).length > 1) {
          let best = -1, bd = 1e9;
          rg.track.forEach((k, i) => {
            const dd = Math.abs(k.t - t);
            if (dd < bd) { bd = dd; best = i; }
          });
          if (best >= 0 && bd < 0.35) {
            rg.track[best].x = d.cur.x;
            rg.track[best].y = d.cur.y;
          }
        }
        wmList();
      }
    }
    wmRender();
  });

  $("#wmRegions").addEventListener("click", (e) => {
    const del = e.target.closest("[data-del]");
    if (del) {
      wmS.regions.splice(+del.dataset.del, 1);
      wmS.sel = Math.min(wmS.sel, wmS.regions.length - 1);
      wmList(); wmRender();
      return;
    }
    const row = e.target.closest(".wm-reg");
    if (row) { wmS.sel = +row.dataset.i; wmList(); wmRender(); }
  });

  // ---- 播放控制（画布只盖画面，控制条独立，互不遮挡）
  const v = $("#wmVideo");
  $("#wmPlay").addEventListener("click", () => {
    if (v.paused) {
      v.play().catch(() => toast("无法播放：编码可能不被浏览器支持", "err", 5000));
    } else {
      v.pause();
    }
  });
  v.addEventListener("play", () => { $("#wmPlay").textContent = "❚❚"; });
  v.addEventListener("pause", () => { $("#wmPlay").textContent = "▶"; });
  v.addEventListener("ended", () => { $("#wmPlay").textContent = "▶"; });
  v.addEventListener("timeupdate", () => { wmUpdateTime(); wmRender(); });
  v.addEventListener("seeked", () => { wmUpdateTime(); wmRender(); });
  $("#wmSeek").addEventListener("input", (e) => {
    if (v.duration && isFinite(v.duration)) {
      v.currentTime = (e.target.value / 1000) * v.duration;
    }
  });

  window.addEventListener("resize", () => {
    if (!$("#wmModal").classList.contains("hidden")) { wmSyncCanvas(); wmRender(); }
  });
})();

/* 轨迹点记录（按钮动态插入到区域列表上方） */
async function wmAddTrack() {
  if (wmS.sel < 0) return toast("先点选一个区域", "warn");
  const r = wmS.regions[wmS.sel];
  const t = +($("#wmVideo").currentTime || 0).toFixed(2);
  const p = wmPosAt(r, t);
  r.track = (r.track || []).filter((k) => Math.abs(k.t - t) > 0.05);
  r.track.push({ t, x: p.x, y: p.y });
  r.track.sort((a, b) => a.t - b.t);
  wmList(); wmRender();
  toast(`已记录轨迹点 t=${t}s（共 ${r.track.length} 个）`, "ok");
}

async function wmDetect() {
  const btn = $("#wmDetect");
  btn.disabled = true;
  btn.textContent = "检测中…";
  try {
    const r = await api("/api/wm/detect", { id: wmS.taskId });
    if (!r.ok) return toast(r.error || "检测失败", "err");
    const regs = r.regions || [];
    if (!regs.length) return toast("没找到明显的水印区域，请手动框选", "warn");
    regs.forEach((g) => wmS.regions.push({ x: g.x, y: g.y, w: g.w, h: g.h, track: [] }));
    wmList(); wmRender();
    toast(`检测到 ${regs.length} 个候选区域，多余的可以删掉`, "ok");
  } finally {
    btn.disabled = false;
    btn.textContent = "自动检测";
  }
}

async function wmRun() {
  if (!wmS.regions.length) return toast("请先框选水印区域", "warn");
  const engine = $("#wmEngine").value;
  if (engine === "ai" && !wmS.aiReady) return toast("AI 引擎未就绪，先点「下载引擎」", "warn");
  const payload = {
    id: wmS.taskId,
    watermark: {
      enabled: true,
      engine,
      regions: wmS.regions.map((r) => ({
        x: r.x, y: r.y, w: r.w, h: r.h,
        track: (r.track || []).length > 1 ? r.track : [],
      })),
    },
  };
  const r = await api("/api/wm/apply", payload);
  if (r.ok) {
    toast("已开始去水印处理，进度看任务列表", "ok", 4000);
    wmClose();
  } else {
    toast(r.error || "提交失败", "err");
  }
}

$("#wmClose")?.addEventListener("click", wmClose);
$("#wmDetect")?.addEventListener("click", wmDetect);
$("#wmRun")?.addEventListener("click", wmRun);
$("#wmEngine")?.addEventListener("change", (e) => {
  $("#wmAiRow").classList.toggle("hidden", e.target.value !== "ai");
  $("#wmNote").textContent = e.target.value === "ai"
    ? "AI 修补：CPU 上较慢（每帧约 2-3 秒），适合半透明小面积水印"
    : "静态水印几秒到一两分钟；移动水印需要逐帧处理，稍慢";
});
$("#wmAiSetup")?.addEventListener("click", async () => {
  const b = $("#wmAiSetup");
  b.disabled = true;
  b.textContent = "下载中…";
  const r = await api("/api/ai/setup", {});
  b.disabled = false;
  b.textContent = "下载引擎";
  toast(r.message || (r.ok ? "完成" : "失败"), r.ok ? "ok" : "err", 5000);
  if (r.ok) {
    wmS.aiReady = true;
    $("#wmAiState").textContent = "AI 引擎已就绪";
    b.classList.add("hidden");
  }
});

$("#wmTrack")?.addEventListener("click", wmAddTrack);

/* 支持 #wm=<taskId> 直接打开编辑器 */
(function wmHash(){
  const m = location.hash.match(/^#wm=(.+)$/);
  if (m) setTimeout(() => { if (state.tasks.has(m[1])) wmOpen(m[1]); }, 900);
})();

/* Delete 删除选中区域 */
window.addEventListener("keydown", (e) => {
  if ($("#wmModal").classList.contains("hidden")) return;
  const tag = (e.target.tagName || "").toUpperCase();
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
  if ((e.key === "Delete" || e.key === "Backspace") && wmS.sel >= 0) {
    e.preventDefault();
    wmS.regions.splice(wmS.sel, 1);
    wmS.sel = Math.min(wmS.sel, wmS.regions.length - 1);
    wmList(); wmRender();
  }
  if (e.key === " " || e.code === "Space") {
    const v = $("#wmVideo");
    if (v && v.duration && isFinite(v.duration)) {
      e.preventDefault();
      if (v.paused) v.play().catch(() => {});
      else v.pause();
    }
  }
  if (e.key === "Escape") wmClose();
});

/* ================================================ 画质提升 */
const upS = { taskId: "", W: 0, H: 0, scale: 2, ready: false, models: [] };

function upUpdateBadge() {
  const s = upS.scale;
  $("#upBadge").textContent = upS.W
    ? `${upS.W}×${upS.H} → ${upS.W * s}×${upS.H * s}`
    : `${s}× 放大`;
}

/* 源信息行：把「会不会因为 HDR 变灰」「该不该开降噪」提前说清楚 */
function upApplyProbe(p) {
  const note = $("#upSrcNote");
  if (!p || !p.ok) {
    note.textContent = p?.error || "读不到源信息，将使用默认参数";
    note.classList.remove("warn");
    return;
  }
  const bits = [`${p.w}×${p.h}`];
  if (p.codec) bits.push(p.codec.toUpperCase());
  if (p.pix_fmt) bits.push(p.tenbit ? "10bit" : "8bit");
  if (p.color_space) bits.push(p.color_space.toUpperCase());
  if (p.hdr) bits.push("HDR");
  note.textContent = bits.join(" · ") + (p.hdr ? "　→ 抽帧前自动转 SDR" : "");
  note.classList.toggle("warn", !!p.hdr);
  // 分辨率越低压缩痕迹越重，跟后端建议走；用户随时可以改回来
  if (p.suggest_denoise && $("#upDenoise")) $("#upDenoise").value = p.suggest_denoise;
}

async function upscaleOpen(taskId) {
  const t = state.tasks.get(taskId);
  if (!t || !t.filename) return toast("找不到文件，可能已被移动", "err");
  upS.taskId = taskId;
  upS.scale = 2;
  upS.W = 0;
  upS.H = 0;
  $("#upModal").classList.remove("hidden");
  $("#upSub").textContent = t.title || "";
  const thumb = $("#upThumb");
  thumb.classList.remove("hidden");
  thumb.src = "/api/thumb?path=" + encodeURIComponent(t.filename);
  $$("#upScale .seg-btn").forEach((b) => b.classList.toggle("active", b.dataset.scale === "2"));
  upUpdateBadge();

  // 读源信息：HDR / 位深 / 色彩，并按分辨率给出建议降噪档
  $("#upSrcNote").textContent = "正在读取源信息…";
  $("#upSrcNote").classList.remove("warn");
  api("/api/probe?path=" + encodeURIComponent(t.filename))
    .then(upApplyProbe)
    .catch(() => {
      $("#upSrcNote").textContent = "读不到源信息，将使用默认参数";
    });

  const info = await api("/api/upscale/status");
  upS.ready = !!info.ready;
  upS.models = info.models || [];
  $("#upModel").innerHTML = upS.models.length
    ? upS.models.map((m) => `<option value="${esc(m.name)}">${esc(m.label)}</option>`).join("")
    : '<option value="">（引擎未安装）</option>';
  const box = $("#upEngine");
  box.classList.toggle("ready", upS.ready);
  $("#upEngineState").textContent = upS.ready
    ? `引擎就绪 · ${upS.models.length} 个模型`
    : (info.error || "引擎未安装");

  // 读视频真实尺寸
  const v = document.createElement("video");
  v.preload = "metadata";
  v.muted = true;
  v.src = "/api/media?path=" + encodeURIComponent(t.filename);
  v.onloadedmetadata = () => {
    upS.W = v.videoWidth;
    upS.H = v.videoHeight;
    upUpdateBadge();
  };
}

function upscaleClose() {
  $("#upModal").classList.add("hidden");
  $("#upThumb").removeAttribute("src");
}

async function upscaleRun() {
  if (!upS.ready) return toast("请先点「下载引擎」", "warn");
  const model = $("#upModel").value;
  const denoise = $("#upDenoise").value;
  const sharpen = $("#upSharpen").value;
  const quality = $("#upQuality").value;
  const btn = $("#upRun");
  btn.disabled = true;
  const r = await api("/api/upscale/apply", {
    id: upS.taskId, scale: upS.scale, model, denoise, sharpen, quality,
  });
  btn.disabled = false;
  if (r.ok) {
    toast(`已开始 ${upS.scale}× 画质提升，进度看任务列表`, "ok", 4500);
    upscaleClose();
  } else {
    toast(r.error || "提交失败", "err");
  }
}

$("#upClose")?.addEventListener("click", upscaleClose);
$("#upRun")?.addEventListener("click", upscaleRun);
$("#upScale")?.addEventListener("click", (e) => {
  const b = e.target.closest("[data-scale]");
  if (!b) return;
  upS.scale = +b.dataset.scale;
  $$("#upScale .seg-btn").forEach((x) => x.classList.toggle("active", x === b));
  upUpdateBadge();
});
$("#upSetup")?.addEventListener("click", async () => {
  const b = $("#upSetup");
  b.disabled = true;
  b.textContent = "下载中…（约 45MB）";
  const r = await api("/api/upscale/setup", {});
  b.disabled = false;
  b.textContent = "下载引擎（45MB）";
  toast(r.message || (r.ok ? "完成" : "失败"), r.ok ? "ok" : "err", 6000);
  if (r.ok) {
    const info = await api("/api/upscale/status");
    upS.ready = !!info.ready;
    upS.models = info.models || [];
    $("#upModel").innerHTML = upS.models.map((m) => `<option value="${esc(m.name)}">${esc(m.label)}</option>`).join("");
    $("#upEngine").classList.add("ready");
    $("#upEngineState").textContent = `引擎就绪 · ${upS.models.length} 个模型`;
  }
});
