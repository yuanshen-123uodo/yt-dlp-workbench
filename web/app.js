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
