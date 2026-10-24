# -*- coding: utf-8 -*-
"""
yt-dlp 可视化工作台 - 本地服务
仅依赖 Python 标准库，双击 启动工作台.bat 即可使用；也可直接用打包好的 exe。
"""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote

try:
    import numpy as np
except Exception:  # 源码运行且没装 numpy 时降级（exe 版已内置）
    np = None

FROZEN = getattr(sys, "frozen", False)


def _app_dir():
    """可写数据目录：exe 所在目录（打包后）或脚本所在目录（源码运行）。
    config / history / downloads / bin / archive 都在这里。"""
    if FROZEN:
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _resource_dir():
    """只读资源目录：打包后是 PyInstaller 临时解压目录，web/ 静态文件在里面。"""
    if FROZEN:
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.abspath(__file__))


APP_DIR = _app_dir()
RES_DIR = _resource_dir()
BASE_DIR = APP_DIR  # 兼容引用：一律按「可写数据目录」语义
WEB_DIR = os.path.join(RES_DIR, "web")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
HISTORY_PATH = os.path.join(APP_DIR, "history.json")


def find_ytdlp():
    """优先用自带的 bin/yt-dlp.exe，没有则找系统 PATH 里的 yt-dlp。"""
    local = os.path.join(BASE_DIR, "bin", "yt-dlp.exe")
    if os.path.exists(local):
        return local
    w = shutil.which("yt-dlp") or shutil.which("yt-dlp.exe")
    if w:
        return w
    return local


BIN = find_ytdlp()

YTDLP_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"


def ensure_ytdlp():
    """把 yt-dlp.exe 下载到 bin/ 目录（exe 版首次运行用）。
    多重回退适配不同网络 / 证书环境，任一成功即返回 True。"""
    local = os.path.join(APP_DIR, "bin", "yt-dlp.exe")
    tmp = local + ".part"
    try:
        os.makedirs(os.path.dirname(local), exist_ok=True)
    except Exception:
        pass

    def ok():
        return os.path.exists(tmp) and os.path.getsize(tmp) > 1024 * 1024

    attempts = []
    if os.name == "nt":
        # 优先系统自带 curl（走 schannel，信任 Windows 证书存储，能过本地安全软件的 TLS 扫描）
        attempts.append(["curl", "-fL", "-o", tmp, YTDLP_URL])
        attempts.append(["curl", "-fL", "--ssl-no-revoke", "-o", tmp, YTDLP_URL])
        attempts.append(["powershell", "-NoProfile", "-Command",
                         "$ProgressPreference='SilentlyContinue'; try{Invoke-WebRequest '%s' -OutFile '%s'}catch{exit 1}" % (YTDLP_URL, tmp)])
    for cmd in attempts:
        try:
            subprocess.run(cmd, timeout=180, creationflags=CREATE_NO_WINDOW)
        except Exception:
            continue
        if ok():
            break
    else:
        # 通用回退：Python urllib（先尝试验证证书，失败则跳过验证——本地自用工具可接受）
        import urllib.request
        import ssl
        try:
            urllib.request.urlretrieve(YTDLP_URL, tmp)
        except Exception:
            try:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                with urllib.request.urlopen(YTDLP_URL, timeout=180, context=ctx) as r:
                    with open(tmp, "wb") as f:
                        shutil.copyfileobj(r, f)
            except Exception:
                pass
    if ok():
        os.replace(tmp, local)
        return True
    try:
        if os.path.exists(tmp):
            os.remove(tmp)
    except Exception:
        pass
    return False

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

DL_TEMPLATE = (
    "download:DL|%(progress.status)s|%(progress.downloaded_bytes)s|"
    "%(progress.total_bytes)s|%(progress.total_bytes_estimate)s|"
    "%(progress.speed)s|%(progress.eta)s"
)

DEFAULT_CONFIG = {
    "outdir": os.path.join(BASE_DIR, "downloads"),
    "concurrency": 2,
}

# ---------------------------------------------------------------- 基础工具


def read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


cfg_lock = threading.Lock()
config = {**DEFAULT_CONFIG, **read_json(CONFIG_PATH, {})}
if not os.path.isdir(config["outdir"]):
    os.makedirs(config["outdir"], exist_ok=True)


def save_config():
    with cfg_lock:
        write_json(CONFIG_PATH, config)


def find_ffmpeg():
    """优先用 exe 同级 bin/ffmpeg.exe（exe 版首次运行会从内置资源解压过来），
    其次系统 PATH 里的 ffmpeg。"""
    local = os.path.join(BASE_DIR, "bin", "ffmpeg.exe")
    if os.path.exists(local):
        return local
    p = shutil.which("ffmpeg")
    if p:
        return p
    return None


FFMPEG = find_ffmpeg()
FFPROBE = None


def _bundled_ffmpeg():
    """在 PyInstaller 打包资源里找内置的 ffmpeg.exe。"""
    p = os.path.join(RES_DIR, "bin", "ffmpeg.exe")
    if os.path.isfile(p):
        return p
    p = os.path.join(RES_DIR, "ffmpeg.exe")
    if os.path.isfile(p):
        return p
    # 兼容 --add-binary 保留原文件名的情况（如 ffmpeg-win-x86_64-v7.1.exe）
    try:
        for n in sorted(os.listdir(os.path.join(RES_DIR, "bin"))):
            if n.lower().startswith("ffmpeg") and n.lower().endswith(".exe"):
                return os.path.join(RES_DIR, "bin", n)
    except Exception:
        pass
    return None


def ensure_ffmpeg():
    """确保 ffmpeg 可用（exe 模式首次运行把内置二进制解压到 exe 同级 bin/）。
    返回是否就绪。"""
    global FFMPEG, FFPROBE
    local = os.path.join(APP_DIR, "bin", "ffmpeg.exe")
    if os.path.isfile(local):
        FFMPEG = local
        find_ffprobe()
        return True
    if FROZEN:
        src = _bundled_ffmpeg()
        if src:
            try:
                os.makedirs(os.path.dirname(local), exist_ok=True)
                shutil.copyfile(src, local)
                FFMPEG = local
            except Exception:
                FFMPEG = src  # 磁盘只读等场景：直接调用打包资源里的
            find_ffprobe()
            return True
    w = shutil.which("ffmpeg")
    if w:
        FFMPEG = w
        find_ffprobe()
        return True
    FFMPEG = None
    return False


def find_ffprobe():
    global FFPROBE
    cands = []
    if FFMPEG:
        cands.append(os.path.join(os.path.dirname(FFMPEG), "ffprobe.exe"))
        cands.append(os.path.join(os.path.dirname(FFMPEG), "ffprobe.EXE"))
    w = shutil.which("ffprobe")
    if w:
        cands.append(w)
    for c in cands:
        if c and os.path.exists(c):
            FFPROBE = c
            return c
    return None


find_ffprobe()
VERSION = "未检测到"


def detect_version():
    global VERSION
    try:
        out = subprocess.run(
            [BIN, "--version"],
            capture_output=True, text=True, timeout=30,
            creationflags=CREATE_NO_WINDOW,
        )
        VERSION = (out.stdout or "").strip().splitlines()[0] or "未知"
    except Exception:
        VERSION = "未检测到"


def child_env():
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env.pop("PYTHONHOME", None)
    # 让 yt-dlp 子进程也能通过 PATH 找到 ffmpeg（--ffmpeg-location 之外的双保险）
    if FFMPEG:
        d = os.path.dirname(FFMPEG)
        if d and d not in env.get("PATH", ""):
            env["PATH"] = d + os.pathsep + env.get("PATH", "")
    return env


# ---------------------------------------------------------------- 日志总线

log_lock = threading.Lock()
LOG_LINES = []
LOG_SEQ = [0]


def add_log(text, task_id=None, level="info"):
    text = text.rstrip("\r\n")
    if not text:
        return
    with log_lock:
        LOG_SEQ[0] += 1
        LOG_LINES.append(
            {
                "seq": LOG_SEQ[0],
                "t": time.strftime("%H:%M:%S"),
                "task": task_id,
                "level": level,
                "text": text[:2000],
            }
        )
        if len(LOG_LINES) > 1500:
            del LOG_LINES[:500]


# ---------------------------------------------------------------- 任务管理

task_lock = threading.Lock()
TASKS = {}          # id -> dict
TASK_ORDER = []     # 按创建顺序
PROCS = {}          # id -> Popen
history_lock = threading.Lock()
HISTORY = read_json(HISTORY_PATH, [])


def public_task(t):
    d = {k: v for k, v in t.items() if k not in ("proc", "cancel", "log")}
    return d


def snapshot():
    with task_lock:
        items = [public_task(TASKS[i]) for i in TASK_ORDER if i in TASKS]
    stats = {
        "queued": sum(1 for i in items if i["status"] == "queued"),
        "running": sum(1 for i in items if i["status"] in ("downloading", "processing")),
        "done": sum(1 for i in items if i["status"] == "done"),
        "error": sum(1 for i in items if i["status"] == "error"),
        "canceled": sum(1 for i in items if i["status"] == "canceled"),
    }
    return items, stats


def push_history(task):
    item = {
        "url": task["url"],
        "title": task["title"],
        "file": task.get("filename") or "",
        "size": task.get("total") or 0,
        "status": task["status"],
        "time": time.time(),
        "preset": task.get("preset") or "",
    }
    with history_lock:
        HISTORY.insert(0, item)
        del HISTORY[400:]
        write_json(HISTORY_PATH, HISTORY)


def build_cmd(task):
    o = task["opts"]
    track = o.get("track") or ""
    if track == "video":
        template = "%(title).120B [%(id)s].video.%(ext)s"
    elif track == "audio":
        template = "%(title).120B [%(id)s].audio.%(ext)s"
    else:
        template = "%(title).150B [%(id)s].%(ext)s"

    cmd = [
        BIN,
        "--newline",
        "--no-mtime",
        "--windows-filenames",
        "--retries", "5",
        "--fragment-retries", "10",
        "--concurrent-fragments", str(int(o.get("fragments") or 4)),
        "-P", o["outdir"],
        "-o", o.get("template") or template,
        "--progress-template", DL_TEMPLATE,
        "--no-simulate",
    ]
    if FFMPEG:
        cmd += ["--ffmpeg-location", os.path.dirname(FFMPEG)]

    preset = o.get("preset") or "best"
    fmt = (o.get("format") or "").strip()
    height = o.get("height")
    h = int(height) if height and int(height) > 0 else 0

    # 选流排序：同分辨率下按码率优先。
    # 不加这个的话 yt-dlp 默认的编码偏好会选低码率版本
    # （实测 B站 1080P：AV1 604k vs H.264 2629k，画质差 3.5 倍）
    SORT = "res,tbr"

    if track == "video":
        # 只取视频轨，优先 h264 以便剪辑软件直接吃
        if fmt:
            cmd += ["-f", fmt]
        elif h:
            cmd += ["-f", "bv*[height<=%d][vcodec^=avc1]/bv*[height<=%d]/bv[height<=%d]" % (h, h, h),
                    "-S", SORT]
        else:
            cmd += ["-f", "bv*[vcodec^=avc1]/bv*/b", "-S", SORT]
        cmd += ["--remux-video", "mp4"]
    elif track == "audio":
        afmt = (o.get("audioFmt") or "orig").lower()
        if afmt in ("mp3", "wav", "m4a", "flac"):
            cmd += ["-f", "ba*/b", "-x", "--audio-format", afmt, "--audio-quality", "0"]
        else:
            cmd += ["-f", "ba*[acodec^=mp4a]/ba*/b"]
    elif preset == "audio-mp3":
        cmd += ["-x", "--audio-format", "mp3", "--audio-quality", str(o.get("audioQuality") or 0)]
    elif preset == "audio-m4a":
        cmd += ["-x", "--audio-format", "m4a", "--audio-quality", "0"]
    elif preset == "audio-best":
        cmd += ["-x", "--audio-format", "best"]
    elif fmt:
        cmd += ["-f", fmt]
    else:
        if h:
            cmd += ["-f", "bv*[height<=%d]+ba/b[height<=%d]/bv*+ba/b" % (h, h), "-S", SORT]
        else:
            cmd += ["-f", "bv*+ba/b", "-S", SORT]
        cmd += ["--merge-output-format", "mp4"]

    if o.get("playlist"):
        cmd += ["--yes-playlist"]
    else:
        cmd += ["--no-playlist"]

    # 拆轨模式下视频轨不写字幕/封面（对剪辑无意义，还会多出文件）
    if track != "video":
        if o.get("subs"):
            cmd += ["--write-subs", "--sub-langs", o.get("subLangs") or "zh.*,en.*", "--convert-subs", "srt"]
            if o.get("embedSubs") and track != "audio":
                cmd += ["--embed-subs"]
        if o.get("autoSubs"):
            cmd += ["--write-auto-subs", "--sub-langs", o.get("subLangs") or "zh.*,en.*"]
        if o.get("thumb"):
            cmd += ["--write-thumbnail"]
        if o.get("embedThumb") and track != "audio":
            cmd += ["--embed-thumbnail"]
        if o.get("metadata") and track != "audio":
            cmd += ["--embed-metadata"]
        if o.get("chapters") and track != "audio":
            cmd += ["--embed-chapters"]

    if o.get("cookies"):
        cmd += ["--cookies-from-browser", o["cookies"]]
    if o.get("proxy"):
        cmd += ["--proxy", o["proxy"]]
    if o.get("rateLimit"):
        cmd += ["-r", o["rateLimit"]]
    if o.get("sponsorblock"):
        cmd += ["--sponsorblock-remove", "sponsor"]
    if o.get("archive"):
        cmd += ["--download-archive", os.path.join(BASE_DIR, "archive.txt")]

    cmd.append(task["url"])
    return cmd


WM_SIZE = {"s": (0.16, 0.07), "m": (0.22, 0.10), "l": (0.30, 0.14)}


def probe_video(path):
    """读取视频宽高与时长。优先 ffprobe；没有则用 ffmpeg 解析 stderr 回退。"""
    if FFPROBE:
        try:
            r = subprocess.run(
                [FFPROBE, "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-show_entries", "format=duration",
                 "-of", "json", path],
                capture_output=True, timeout=60, creationflags=CREATE_NO_WINDOW,
            )
            d = json.loads((r.stdout or b"{}").decode("utf-8", "replace"))
            st = (d.get("streams") or [{}])[0]
            w, h = int(st.get("width") or 0), int(st.get("height") or 0)
            dur = float((d.get("format") or {}).get("duration") or 0)
            if w and h:
                return {"w": w, "h": h, "duration": dur}
        except Exception:
            pass
    # 回退：ffmpeg -i 的 stderr 里就有分辨率和时长
    if not FFMPEG:
        return None
    try:
        r = subprocess.run(
            [FFMPEG, "-hide_banner", "-i", path],
            capture_output=True, timeout=60, creationflags=CREATE_NO_WINDOW,
        )
        err = (r.stderr or b"").decode("utf-8", "replace")
        m = re.search(r"Stream #\d+:\d+.*?: Video:.*?,\s*(\d{2,5})x(\d{2,5})", err)
        if not m:
            return None
        dur = 0.0
        dm = re.search(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)", err)
        if dm:
            dur = int(dm.group(1)) * 3600 + int(dm.group(2)) * 60 + float(dm.group(3))
        return {"w": int(m.group(1)), "h": int(m.group(2)), "duration": dur}
    except Exception:
        return None


# ---------------------------------------------------------------- 水印修补
# 设计：区域坐标一律归一化到 0~1（与分辨率无关），支持多个区域、时间范围、
# 移动轨迹（关键帧线性插值）。静态区域走 ffmpeg 滤镜（快）；含移动轨迹或
# AI 模式走逐帧管线（Python + numpy）。

def norm_regions(wm):
    """把新旧两种配置格式统一成区域列表。"""
    regs = wm.get("regions")
    if isinstance(regs, list) and regs:
        out = []
        for r in regs:
            if not isinstance(r, dict):
                continue
            try:
                d = {
                    "x": float(r.get("x") if r.get("x") is not None else 0),
                    "y": float(r.get("y") if r.get("y") is not None else 0),
                    "w": float(r.get("w") if r.get("w") is not None else 0.1),
                    "h": float(r.get("h") if r.get("h") is not None else 0.1),
                    "t0": float(r.get("t0") or 0),
                    "t1": float(r.get("t1") or 0),
                    "mode": r.get("mode") or "delogo",
                    "track": [],
                }
            except Exception:
                continue
            for k in (r.get("track") or []):
                try:
                    d["track"].append({
                        "t": float(k.get("t") or 0),
                        "x": float(k.get("x") if k.get("x") is not None else 0),
                        "y": float(k.get("y") if k.get("y") is not None else 0),
                    })
                except Exception:
                    pass
            d["track"].sort(key=lambda k: k["t"])
            if d["w"] > 0.002 and d["h"] > 0.002:
                out.append(d)
        return out
    # 旧格式：pos + size → 转成单个区域
    rw, rh = WM_SIZE.get(wm.get("size") or "m", WM_SIZE["m"])
    pos = wm.get("pos") or "tr"
    m = 0.012
    x = m if pos in ("tl", "bl") else 1 - rw - m
    y = m if pos in ("tl", "tr") else 1 - rh - m
    return [{"x": x, "y": y, "w": rw, "h": rh, "t0": 0, "t1": 0,
             "mode": wm.get("mode") or "delogo", "track": []}]


def region_at(r, t):
    """轨迹线性插值：返回 t 时刻的归一化 (x, y, w, h)。"""
    tr = r.get("track") or []
    if len(tr) < 2:
        return r["x"], r["y"], r["w"], r["h"]
    if t <= tr[0]["t"]:
        return tr[0]["x"], tr[0]["y"], r["w"], r["h"]
    if t >= tr[-1]["t"]:
        return tr[-1]["x"], tr[-1]["y"], r["w"], r["h"]
    for i in range(len(tr) - 1):
        a, b = tr[i], tr[i + 1]
        if a["t"] <= t <= b["t"]:
            span = (b["t"] - a["t"]) or 1.0
            k = (t - a["t"]) / span
            return (a["x"] + (b["x"] - a["x"]) * k,
                    a["y"] + (b["y"] - a["y"]) * k,
                    r["w"], r["h"])
    return r["x"], r["y"], r["w"], r["h"]


def region_active(r, t):
    t0, t1 = r.get("t0") or 0, r.get("t1") or 0
    if t0 and t < t0:
        return False
    if t1 and t > t1:
        return False
    return True


def region_moving(r):
    tr = r.get("track") or []
    if len(tr) < 2:
        return False
    x0, y0 = tr[0]["x"], tr[0]["y"]
    return any(abs(k["x"] - x0) > 0.002 or abs(k["y"] - y0) > 0.002 for k in tr[1:])


def px_region(r, t, W, H, pad=0):
    """归一化区域 → 像素坐标（偶数对齐、边界内收，可外扩 pad 像素）。"""
    x, y, w, h = region_at(r, t)
    pw = max(16, int(w * W) // 2 * 2) + pad * 2
    ph = max(16, int(h * H) // 2 * 2) + pad * 2
    px = max(0, min(int(x * W) - pad, max(0, W - pw - 2)))
    py = max(0, min(int(y * H) - pad, max(0, H - ph - 2)))
    return px, py, pw, ph


def _inpaint_patch(img, x, y, w, h, pad=3):
    """对 img 的 (x,y,w,h) 做四方向渐变插值（比 delogo 的逐行插值更平滑）。

    采样源取区域外 pad 像素处：用户框通常刚好贴合水印，
    紧贴边缘的像素仍是水印本身，直接采样会把水印颜色插值回来。
    """
    H, W = img.shape[:2]
    x2, y2 = min(x + w, W), min(y + h, H)
    w, h = x2 - x, y2 - y
    if w < 3 or h < 3:
        return
    ty0 = max(0, y - pad)
    ty1 = min(H - 1, y2 + pad - 1)
    tx0 = max(0, x - pad)
    tx1 = min(W - 1, x2 + pad - 1)
    top = img[ty0, x:x2].astype(np.float32)
    bot = img[ty1, x:x2].astype(np.float32)
    left = img[y:y2, tx0].astype(np.float32)
    right = img[y:y2, tx1].astype(np.float32)
    ty = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None, None]
    v = top[None, :, :] * (1 - ty) + bot[None, :, :] * ty
    tx = np.linspace(0.0, 1.0, w, dtype=np.float32)[None, :, None]
    hz = left[:, None, :] * (1 - tx) + right[:, None, :] * tx
    # 到四边的距离越小权重越高，避免两个方向的插值互相冲淡
    dy = np.minimum(np.arange(h, dtype=np.float32), np.arange(h - 1, -1, -1, dtype=np.float32))
    dx = np.minimum(np.arange(w, dtype=np.float32), np.arange(w - 1, -1, -1, dtype=np.float32))
    wy = (dy + 1.0)[:, None, None]
    wx = (dx + 1.0)[None, :, None]
    out = (v * wx + hz * wy) / (wx + wy)
    img[y:y2, x:x2] = np.clip(out, 0, 255).astype(np.uint8)


def _temporal_fill(frames, idx, x, y, w, h, radius=3):
    """时域中值填充：移动水印在前后帧同一位置是干净背景，取中值即可还原。

    不按方差放弃——移动水印场景下背景本来就在动，
    中值仍比保留水印好得多。
    """
    H, W = frames[idx].shape[:2]
    x2, y2 = min(x + w, W), min(y + h, H)
    if x2 - x <= 2 or y2 - y <= 2:
        return False
    stack = []
    for j in range(0, len(frames)):
        if j == idx:
            continue
        stack.append(frames[j][y:y2, x:x2])
    if len(stack) < 2:
        return False
    frames[idx][y:y2, x:x2] = np.median(np.stack(stack), axis=0).astype(np.uint8)
    return True


def static_wm_filters(info, regions):
    """全静态区域 → ffmpeg 滤镜串（delogo 串联 + 锐化补偿）。"""
    W, H = info.get("w") or 0, info.get("h") or 0
    if W < 32 or H < 32:
        return None
    parts = []
    for r in regions:
        if region_moving(r) or (r.get("t0") or 0) or (r.get("t1") or 0):
            return None  # 有轨迹/时间范围 → 必须走逐帧管线
        # 外扩 2px：用户框通常刚好贴合水印，delogo 采样到边缘仍是水印像素
        x, y, w, h = px_region(r, 0, W, H, pad=2)
        parts.append("delogo=x=%d:y=%d:w=%d:h=%d:show=0" % (x, y, w, h))
    if not parts:
        return None
    # 锐化补偿：delogo 插值会糊，轻微 unsharp 把边缘拉回来
    parts.append("unsharp=5:5:0.6:5:5:0.0")
    return ",".join(parts)


def probe_fps(path):
    """读视频帧率（ffprobe 或 ffmpeg 回退）。"""
    try:
        if FFPROBE:
            r = subprocess.run(
                [FFPROBE, "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=r_frame_rate,avg_frame_rate", "-of", "json", path],
                capture_output=True, timeout=60, creationflags=CREATE_NO_WINDOW,
            )
            d = json.loads((r.stdout or b"{}").decode("utf-8", "replace"))
            st = (d.get("streams") or [{}])[0]
            for key in ("avg_frame_rate", "r_frame_rate"):
                v = st.get(key) or ""
                if "/" in v:
                    a, b = v.split("/", 1)
                    if float(b or 0) > 0:
                        fps = float(a) / float(b)
                        if 1 <= fps <= 240:
                            return round(fps, 3)
        if FFMPEG:
            r = subprocess.run([FFMPEG, "-hide_banner", "-i", path],
                               capture_output=True, timeout=60, creationflags=CREATE_NO_WINDOW)
            err = (r.stderr or b"").decode("utf-8", "replace")
            m = re.search(r"(\d+(?:\.\d+)?)\s+fps", err)
            if m:
                fps = float(m.group(1))
                if 1 <= fps <= 240:
                    return round(fps, 3)
    except Exception:
        pass
    return 30.0


def process_frames_wm(task, src, dst, regions, info, engine):
    """逐帧修补管线：解码 → 每帧按轨迹位置修补 → 编码（音频直通）。
    返回 True 表示成功。"""
    tid = task["id"]
    W, H = int(info["w"]), int(info["h"])
    fps = info.get("fps") or probe_fps(src)
    dur = info.get("duration") or 0
    total_frames = int(dur * fps) if dur else 0
    frame_size = W * H * 3
    radius = 3

    # 是否需要时域中值：仅当水印移动较快时才有意义（慢速移动时前后帧水印重叠，
    # 中值仍是水印本身）。默认用空间插值（每帧跟随位置，与移动速度无关）。
    use_temporal = engine == "fast" and any(region_moving(r) for r in regions)

    dec = subprocess.Popen(
        [FFMPEG, "-v", "error", "-i", src, "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW,
    )
    enc = subprocess.Popen(
        [FFMPEG, "-y", "-v", "error",
         "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", "%dx%d" % (W, H),
         "-r", str(fps), "-i", "pipe:0",
         "-i", src, "-map", "0:v:0", "-map", "1:a?",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart",
         "-c:a", "copy", dst],
        stdin=subprocess.PIPE, stderr=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW, env=child_env(), cwd=BASE_DIR,
    )
    PROCS[tid] = enc

    def read_frame():
        buf = dec.stdout.read(frame_size)
        if len(buf) < frame_size:
            return None
        return np.frombuffer(buf, dtype=np.uint8).reshape(H, W, 3).copy()

    # 时域中值需要预读：先填满 radius 帧缓冲
    buf = []
    try:
        while len(buf) < radius + 1:
            f = read_frame()
            if f is None:
                break
            buf.append(f)
        idx = 0
        while buf:
            cur = buf[0]
            t = idx / fps if fps else 0
            for r in regions:
                if not region_active(r, t):
                    continue
                x, y, w, h = px_region(r, t, W, H)
                # 空间插值为主：不依赖水印移动速度，每帧按轨迹位置重新插值
                _inpaint_patch(cur, x, y, w, h)
            try:
                enc.stdin.write(cur.tobytes())
            except Exception:
                break
            buf.pop(0)
            nf = read_frame()
            if nf is not None:
                buf.append(nf)
            idx += 1
            if total_frames and idx % 10 == 0:
                with task_lock:
                    task["percent"] = min(99.0, idx / total_frames * 100.0)
                if task.get("cancel"):
                    break
    finally:
        try:
            dec.stdout.close()
        except Exception:
            pass
        try:
            dec.kill()
        except Exception:
            pass
        try:
            enc.stdin.close()
        except Exception:
            pass
        code = enc.wait()
        PROCS.pop(tid, None)
    with task_lock:
        task["percent"] = 100.0
    return code == 0


# ---- AI 修补引擎（可选组件：onnxruntime + LaMa 模型）
AI_DIR = os.path.join(APP_DIR, "ai")
AI_MODEL = os.path.join(AI_DIR, "lama_fp32.onnx")
AI_STATE = {"ready": None, "error": "", "session": None}


def ai_status():
    """检查 AI 引擎是否可用（onnxruntime + 模型文件）。"""
    global AI_STATE
    if AI_STATE["ready"] is not None:
        return AI_STATE
    try:
        import onnxruntime  # noqa: F401
    except Exception:
        AI_STATE = {"ready": False, "error": "未安装 onnxruntime", "session": None}
        return AI_STATE
    if not os.path.isfile(AI_MODEL):
        AI_STATE = {"ready": False, "error": "未下载 LaMa 模型", "session": None}
        return AI_STATE
    AI_STATE = {"ready": True, "error": "", "session": None}
    return AI_STATE


def _ai_session():
    """懒加载 onnxruntime 会话。"""
    if AI_STATE.get("session"):
        return AI_STATE["session"]
    import onnxruntime as ort
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = max(1, (os.cpu_count() or 2) // 2)
    sess = ort.InferenceSession(AI_MODEL, opts, providers=["CPUExecutionProvider"])
    AI_STATE["session"] = sess
    return sess


AI_MODEL_URLS = [
    "https://hf-mirror.com/Carve/LaMa-ONNX/resolve/main/lama_fp32.onnx",
    "https://huggingface.co/Carve/LaMa-ONNX/resolve/main/lama_fp32.onnx",
]


def setup_ai_engine():
    """下载 AI 修补模型（onnxruntime 已随程序内置）。返回 (ok, message)。"""
    global AI_STATE
    try:
        import onnxruntime  # noqa: F401
    except Exception:
        return False, "当前版本未内置 onnxruntime（源码运行请 pip install onnxruntime onnx）"
    if os.path.isfile(AI_MODEL) and os.path.getsize(AI_MODEL) > 1024 * 1024:
        AI_STATE = {"ready": True, "error": "", "session": None}
        return True, "AI 引擎已就绪"
    try:
        os.makedirs(AI_DIR, exist_ok=True)
    except Exception:
        pass
    tmp = AI_MODEL + ".part"
    import urllib.request
    last_err = ""
    for url in AI_MODEL_URLS:
        try:
            with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
                shutil.copyfileobj(r, f)
            if os.path.getsize(tmp) > 1024 * 1024:
                os.replace(tmp, AI_MODEL)
                AI_STATE = {"ready": True, "error": "", "session": None}
                return True, "AI 模型下载完成（%.0f MB）" % (os.path.getsize(AI_MODEL) / 1048576)
            last_err = "下载文件异常"
        except Exception as e:
            last_err = str(e)
        finally:
            if os.path.exists(tmp) and not os.path.isfile(AI_MODEL):
                try:
                    os.remove(tmp)
                except Exception:
                    pass
    return False, "模型下载失败：%s" % last_err


def _resize_np(img, nw, nh):
    """numpy 双线性缩放（避免引入 OpenCV）。支持 2D（mask）与 3D（图像）。"""
    h, w = img.shape[:2]
    if w == nw and h == nh:
        return img
    ys = np.linspace(0, h - 1, nh, dtype=np.float32)
    xs = np.linspace(0, w - 1, nw, dtype=np.float32)
    y0 = np.floor(ys).astype(np.int32)
    x0 = np.floor(xs).astype(np.int32)
    y1 = np.minimum(y0 + 1, h - 1)
    x1 = np.minimum(x0 + 1, w - 1)
    wy = (ys - y0)[:, None]
    wx = (xs - x0)[None, :]
    a = img[y0][:, x0].astype(np.float32)
    b = img[y0][:, x1].astype(np.float32)
    c = img[y1][:, x0].astype(np.float32)
    d = img[y1][:, x1].astype(np.float32)
    if a.ndim == 3:  # 彩色图：额外补一维通道
        wy = wy[..., None]
        wx = wx[..., None]
    top = a * (1 - wx) + b * wx
    bot = c * (1 - wx) + d * wx
    return (top * (1 - wy) + bot * wy).astype(np.uint8)


def _ai_inpaint_patch(sess, img, x, y, w, h, pad=24):
    """对 img 的指定区域做 LaMa 推理修补（只处理该区域，速度快）。"""
    H, W = img.shape[:2]
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(W, x + w + pad), min(H, y + h + pad)
    cw, ch = x1 - x0, y1 - y0
    if cw < 16 or ch < 16:
        return False
    crop = img[y0:y1, x0:x1]
    # 模型输入尺寸（LaMa onnx 通常是 512x512）
    try:
        ishape = sess.get_inputs()[0].shape
        size = int(ishape[2]) if len(ishape) == 4 and isinstance(ishape[2], int) else 512
    except Exception:
        size = 512
    scale = min(size / cw, size / ch)
    nw, nh = max(16, int(cw * scale)), max(16, int(ch * scale))
    canvas = np.zeros((size, size, 3), dtype=np.uint8)
    canvas[:nh, :nw] = _resize_np(crop, nw, nh)
    mask = np.zeros((size, size), dtype=np.float32)
    mx0, my0 = int((x - x0) * scale), int((y - y0) * scale)
    mx1, my1 = int((x + w - x0) * scale), int((y + h - y0) * scale)
    mask[max(0, my0):min(size, my1), max(0, mx0):min(size, mx1)] = 1.0
    inp = np.transpose(canvas.astype(np.float32) / 255.0, (2, 0, 1))[None]
    msk = mask[None, None]
    feed = {}
    for i, spec in enumerate(sess.get_inputs()):
        feed[spec.name] = inp if i == 0 else msk
    out = sess.run(None, feed)[0][0]
    o = np.transpose(out, (1, 2, 0))
    # 模型输出尺度不统一：有的返回 0~1，有的直接返回 0~255
    if float(o.max()) <= 1.5:
        o = o * 255.0
    out_img = np.clip(o, 0, 255).astype(np.uint8)
    out_crop = _resize_np(out_img[:nh, :nw], cw, ch)
    # 只贴回水印区域本身（外圈保留原像素，避免整块重编码痕迹）
    rx0, ry0 = x - x0, y - y0
    rx1, ry1 = min(cw, rx0 + w), min(ch, ry0 + h)
    if rx1 <= rx0 or ry1 <= ry0:
        return False
    img[y:y + (ry1 - ry0), x:x + (rx1 - rx0)] = out_crop[ry0:ry1, rx0:rx1]
    return True


def process_frames_ai(task, src, dst, regions, info):
    """AI 修补管线。引擎不可用时自动回退轻量修补。"""
    tid = task["id"]
    st = ai_status()
    if not st.get("ready"):
        add_log("AI 引擎不可用（%s），本次使用轻量修补" % (st.get("error") or "未知原因"), tid, "warn")
        return process_frames_wm(task, src, dst, regions, info, "fast")
    try:
        sess = _ai_session()
    except Exception as e:
        add_log("AI 引擎加载失败（%s），回退轻量修补" % e, tid, "warn")
        return process_frames_wm(task, src, dst, regions, info, "fast")

    W, H = int(info["w"]), int(info["h"])
    fps = info.get("fps") or 30.0
    dur = info.get("duration") or 0
    total_frames = int(dur * fps) if dur else 0
    frame_size = W * H * 3

    dec = subprocess.Popen(
        [FFMPEG, "-v", "error", "-i", src, "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW,
    )
    enc = subprocess.Popen(
        [FFMPEG, "-y", "-v", "error",
         "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", "%dx%d" % (W, H),
         "-r", str(fps), "-i", "pipe:0",
         "-i", src, "-map", "0:v:0", "-map", "1:a?",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart",
         "-c:a", "copy", dst],
        stdin=subprocess.PIPE, stderr=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW, env=child_env(), cwd=BASE_DIR,
    )
    PROCS[tid] = enc
    idx = 0
    try:
        while True:
            buf = dec.stdout.read(frame_size)
            if len(buf) < frame_size:
                break
            frame = np.frombuffer(buf, dtype=np.uint8).reshape(H, W, 3).copy()
            t = idx / fps if fps else 0
            for r in regions:
                if not region_active(r, t):
                    continue
                x, y, w, h = px_region(r, t, W, H)
                try:
                    if not _ai_inpaint_patch(sess, frame, x, y, w, h):
                        _inpaint_patch(frame, x, y, w, h)
                except Exception:
                    _inpaint_patch(frame, x, y, w, h)
            try:
                enc.stdin.write(frame.tobytes())
            except Exception:
                break
            idx += 1
            if total_frames and idx % 5 == 0:
                with task_lock:
                    task["percent"] = min(99.0, idx / total_frames * 100.0)
                if task.get("cancel"):
                    break
    finally:
        for p, attr in ((dec, "stdout"), (enc, "stdin")):
            try:
                getattr(p, attr).close()
            except Exception:
                pass
        try:
            dec.kill()
        except Exception:
            pass
        code = enc.wait()
        PROCS.pop(tid, None)
    with task_lock:
        task["percent"] = 100.0
    return code == 0


def _sample_gray_frames(path, info, count=8, tw=320):
    """均匀抽 count 帧缩略灰度图，返回 (frames, th, tw)。"""
    W, H = int(info["w"]), int(info["h"])
    th = max(16, int(H * tw / max(1, W) / 2) * 2)
    dur = max(0.1, info.get("duration") or 1)
    rate = max(0.05, count / dur)
    r = subprocess.run(
        [FFMPEG, "-v", "error", "-i", path,
         "-vf", "fps=%.4f,scale=%d:%d" % (rate, tw, th),
         "-frames:v", str(count),
         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True, timeout=180, creationflags=CREATE_NO_WINDOW,
    )
    fs = tw * th
    buf = r.stdout or b""
    out = []
    for i in range(len(buf) // fs):
        out.append(np.frombuffer(buf[i * fs:(i + 1) * fs], np.uint8).reshape(th, tw).astype(np.float32))
    return out, th, tw


def detect_watermarks(path):
    """自动检测疑似水印区域。

    原理：水印 = 边缘清晰（Sobel 强）+ 时域稳定（多帧间波动小，不随内容变化），
    且多分布在画面边缘。用网格聚合 + 相邻框合并给出候选，供用户确认/微调。
    """
    if np is None:
        return []
    info = probe_video(path)
    if not info or not info.get("w"):
        return []
    frames, th, tw = _sample_gray_frames(path, info, 8)
    if len(frames) < 2:
        return []
    stack = np.stack(frames)
    med = np.median(stack, axis=0)
    std = stack.std(axis=0)

    gx = np.zeros_like(med)
    gy = np.zeros_like(med)
    gx[:, 1:-1] = med[:, 2:] - med[:, :-2]
    gy[1:-1, :] = med[2:, :] - med[:-2, :]
    edge = np.sqrt(gx * gx + gy * gy)

    def norm(a):
        p = float(np.percentile(a, 95)) or 1.0
        return np.clip(a / p, 0.0, 1.0)

    # 水印三要素：边缘清晰 + 时域稳定（不随画面内容变化）+ 多位于画面边缘
    edge_n = norm(edge)
    stab = 1.0 - norm(std)
    prior = np.ones_like(med)
    bx, by = max(2, int(tw * 0.15)), max(2, int(th * 0.15))
    prior[:by, :] = 1.7
    prior[-by:, :] = 1.7
    prior[:, :bx] = 1.7
    prior[:, -bx:] = 1.7
    score = edge_n * (stab ** 1.5) * prior

    GH, GW = 9, 16
    gh, gw = max(1, th // GH), max(1, tw // GW)
    cells = []
    for r in range(GH):
        for c in range(GW):
            blk = score[r * gh:(r + 1) * gh, c * gw:(c + 1) * gw]
            if blk.size:
                cells.append((float(blk.mean()), r, c))
    cells.sort(reverse=True)

    boxes = []
    for s, r, c in cells[:20]:
        if s < 0.10:
            break
        boxes.append([c / GW, r / GH, 1.0 / GW, 1.0 / GH, s])
    # 合并相邻/重叠框
    merged = []
    for b in boxes:
        hit = None
        for m in merged:
            if (abs(m[0] - b[0]) <= 1.5 / GW and abs(m[1] - b[1]) <= 1.5 / GH):
                hit = m
                break
        if hit:
            x0 = min(hit[0], b[0])
            y0 = min(hit[1], b[1])
            x1 = max(hit[0] + hit[2], b[0] + b[2])
            y1 = max(hit[1] + hit[3], b[1] + b[3])
            hit[0], hit[1], hit[2], hit[3] = x0, y0, x1 - x0, y1 - y0
            hit[4] = max(hit[4], b[4])
        else:
            merged.append(b[:])
    merged.sort(key=lambda x: -x[4])
    out = []
    for b in merged[:4]:
        x0 = max(0.0, min(1.0, b[0]))
        y0 = max(0.0, min(1.0, b[1]))
        x1 = max(0.0, min(1.0, b[0] + b[2]))
        y1 = max(0.0, min(1.0, b[1] + b[3]))
        if x1 - x0 < 0.01 or y1 - y0 < 0.01:
            continue
        out.append({
            "x": round(x0, 4), "y": round(y0, 4),
            "w": round(x1 - x0, 4), "h": round(y1 - y0, 4),
            "score": round(b[4], 3),
        })
    return out


STREAM_TYPES = ("video", "audio", "subtitle", "data", "attachment")


def probe_streams(path):
    """返回 [(type, codec), …]，失败返回 None。优先 ffprobe，没有则用 ffmpeg 解析。"""
    if FFPROBE:
        try:
            r = subprocess.run(
                [FFPROBE, "-v", "error", "-show_entries", "stream=codec_type,codec_name",
                 "-of", "csv=p=0", path],
                capture_output=True, timeout=60, creationflags=CREATE_NO_WINDOW,
            )
            out = []
            for line in (r.stdout or b"").decode("utf-8", "replace").splitlines():
                parts = [p.strip() for p in line.split(",") if p.strip()]
                if not parts:
                    continue
                # ffprobe 的 CSV 列序并不固定（实测为 codec_name,codec_type），按值判断
                kind = next((p for p in parts if p in STREAM_TYPES), "")
                codec = next((p for p in parts if p not in STREAM_TYPES), "")
                if kind:
                    out.append((kind, codec))
            if out:
                return out
        except Exception:
            pass
    # 回退：ffmpeg -i 的 stderr 里就有流类型和编码
    if not FFMPEG:
        return None
    try:
        r = subprocess.run(
            [FFMPEG, "-hide_banner", "-i", path],
            capture_output=True, timeout=60, creationflags=CREATE_NO_WINDOW,
        )
        err = (r.stderr or b"").decode("utf-8", "replace")
        out = []
        for m in re.finditer(
                r"Stream #\d+:\d+(?:\([^)]*\))?[^:]*:\s*(Video|Audio|Subtitle|Data)\s*:\s*([A-Za-z0-9]+)", err):
            kind = m.group(1).lower()
            if kind in STREAM_TYPES:
                out.append((kind, m.group(2)))
        return out or None
    except Exception:
        return None


AUDIO_EXT = {
    "aac": ".m4a",
    "mp3": ".mp3",
    "opus": ".webm",
    "vorbis": ".webm",
    "flac": ".flac",
    "alac": ".m4a",
}


def ensure_single_track(task):
    """剪辑双轨模式下，若源本身是音视频混流的单文件，用 ffmpeg 无损抽出纯轨。"""
    tid = task["id"]
    track = task["opts"].get("track") or ""
    if track not in ("video", "audio"):
        return False
    path = task.get("filename") or ""
    if not path or not os.path.isfile(path) or not FFMPEG:
        return False

    streams = probe_streams(path)
    if not streams:
        return False
    has_v = any(t == "video" for t, _ in streams)
    has_a = any(t == "audio" for t, _ in streams)
    if track == "video" and not has_a:
        return False
    if track == "audio" and not has_v:
        return False

    base, ext = os.path.splitext(path)
    if track == "video":
        final = path
        tmp = base + ".__track" + (ext or ".mp4")
        map_arg = "0:v:0"
        label = "视频"
    else:
        acodec = next((c for t, c in streams if t == "audio"), "")
        ext2 = AUDIO_EXT.get(acodec) or ".mka"
        final = base + ext2
        tmp = base + ".__track" + ext2
        map_arg = "0:a:0"
        label = "音频"

    add_log("源为混流文件，正在无损抽出纯%s轨…" % label, tid)
    with task_lock:
        task["phase"] = "拆分%s轨" % label
    cmd = [
        FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
        "-i", path, "-map", map_arg, "-c", "copy",
        "-map_metadata", "0", tmp,
    ]
    try:
        r = subprocess.run(
            cmd, capture_output=True, timeout=3600,
            creationflags=CREATE_NO_WINDOW, env=child_env(), cwd=BASE_DIR,
        )
    except Exception as e:
        add_log("拆轨失败：%s（保留原文件）" % e, tid, "warn")
        return False

    if r.returncode != 0 or not os.path.isfile(tmp) or os.path.getsize(tmp) == 0:
        msg = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        add_log("拆轨失败：%s（保留原文件）" % (msg[-1] if msg else "ffmpeg 错误"), tid, "warn")
        if os.path.isfile(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass
        return False

    try:
        os.replace(tmp, final)
        if os.path.normcase(path) != os.path.normcase(final) and os.path.isfile(path):
            os.remove(path)
        with task_lock:
            task["filename"] = final
            task["total"] = os.path.getsize(final)
            task["track_ok"] = True
        add_log("✔ 已抽出纯%s轨：%s" % (label, os.path.basename(final)), tid, "ok")
        return True
    except Exception as e:
        add_log("拆轨文件替换失败：%s" % e, tid, "error")
        return False


def apply_watermark(task):
    """去水印：静态区域走 ffmpeg 滤镜，含移动轨迹/时间范围走逐帧修补管线。"""
    tid = task["id"]
    path = task.get("filename") or ""
    wm = task["opts"].get("watermark") or {}
    if not path or not os.path.isfile(path):
        add_log("去水印跳过：找不到源文件", tid, "warn")
        return False
    if not FFMPEG:
        add_log("去水印跳过：缺少 ffmpeg", tid, "warn")
        return False

    regions = norm_regions(wm)
    if not regions:
        add_log("去水印跳过：未指定水印区域", tid, "warn")
        return False

    info = probe_video(path)
    if not info or not info.get("w"):
        add_log("去水印跳过：无法读取视频尺寸", tid, "warn")
        return False
    info["fps"] = probe_fps(path)

    engine = (wm.get("engine") or "fast").lower()
    dynamic = any(region_moving(r) or (r.get("t0") or 0) or (r.get("t1") or 0) for r in regions)

    base = os.path.splitext(path)[0]
    tmp = base + ".__wm.mp4"
    final = base + ".mp4"
    dur = info.get("duration") or 0

    with task_lock:
        task["status"] = "processing"
        task["phase"] = "去水印处理中"
        task["percent"] = 0.0
        task["speed"] = 0
        task["eta"] = 0

    # ---- 逐帧管线（移动水印 / AI 引擎）
    if dynamic or engine == "ai":
        if np is None:
            add_log("去水印失败：该模式需要 numpy（源码运行请先 pip install numpy）", tid, "error")
            return False
        n_moving = sum(1 for r in regions if region_moving(r))
        add_log("开始去水印（逐帧修补）：%d 个区域%s（%dx%d @ %gfps）"
                % (len(regions), "，%d 个带移动轨迹" % n_moving if n_moving else "",
                   info["w"], info["h"], info.get("fps") or 30), tid)
        if engine == "ai":
            ok = process_frames_ai(task, path, tmp, regions, info)
        else:
            ok = process_frames_wm(task, path, tmp, regions, info, engine)
        code = 0 if ok else 1
    else:
        # ---- 静态区域：纯 ffmpeg 滤镜
        vf = static_wm_filters(info, regions)
        if not vf:
            add_log("去水印跳过：视频尺寸异常", tid, "warn")
            return False
        add_log("开始去水印（滤镜）：%d 个区域 · %s（%dx%d）" % (len(regions), vf, info["w"], info["h"]), tid)
        cmd = [
            FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
            "-i", path, "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            "-c:a", "copy",
            "-progress", "pipe:1", "-nostats", tmp,
        ]
        code = 1
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, bufsize=0,
                creationflags=CREATE_NO_WINDOW, env=child_env(), cwd=BASE_DIR,
            )
            PROCS[tid] = proc
            for raw in proc.stdout:
                line = decode_line(raw)
                if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                    try:
                        us = int(line.split("=", 1)[1])
                    except Exception:
                        continue
                    if dur > 0:
                        with task_lock:
                            task["percent"] = min(99.5, us / 1e6 / dur * 100.0)
                elif line.startswith("progress=end"):
                    with task_lock:
                        task["percent"] = 100.0
                elif line.strip() and not line.startswith((
                        "frame=", "fps=", "bitrate=", "total_size=", "out_time=", "out_time_us=",
                        "out_time_ms=", "dup_frames=", "drop_frames=", "speed=", "progress=", "stream_")):
                    add_log(line, tid, "warn")
            code = proc.wait()
            PROCS.pop(tid, None)
        except Exception as e:
            add_log("去水印失败：%s" % e, tid, "error")
            return False

    if code == 0 and os.path.isfile(tmp) and os.path.getsize(tmp) > 0:
        try:
            os.replace(tmp, final)
            if os.path.normcase(path) != os.path.normcase(final) and os.path.isfile(path):
                os.remove(path)
            with task_lock:
                task["filename"] = final
                task["total"] = os.path.getsize(final)
                task["wm_done"] = True
            add_log("✔ 去水印完成：%s" % os.path.basename(final), tid, "ok")
            return True
        except Exception as e:
            add_log("去水印替换失败：%s" % e, tid, "error")
            return False
    else:
        if os.path.isfile(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass
        add_log("去水印失败，保留原文件", tid, "error")
        return False


POST_PHASE = {
    "ExtractAudio": "提取音频",
    "FixupM3u8": "修复容器",
    "FixupM4a": "修复容器",
    "MoveFiles": "整理文件",
    "EmbedThumbnail": "嵌入封面",
    "Metadata": "写入元数据",
    "EmbedSubtitle": "嵌入字幕",
}

PHASE_PATTERNS = [    (re.compile(r"^\[Merger\]"), "合并音视频"),
    (re.compile(r"^\[ExtractAudio\]"), "提取音频"),
    (re.compile(r"^\[VideoConvertor\]|^\[VideoRemuxer\]"), "转换格式"),
    (re.compile(r"^\[EmbedThumbnail\]"), "嵌入封面"),
    (re.compile(r"^\[Metadata\]"), "写入元数据"),
    (re.compile(r"^\[EmbedSubtitle\]"), "嵌入字幕"),
    (re.compile(r"^\[ThumbnailsConvertor\]"), "处理封面"),
    (re.compile(r"^\[FixupM3u8\]|^\[FixupM4a\]"), "修复容器"),
    (re.compile(r"^\[SponsorBlock\]"), "处理赞助片段"),
    (re.compile(r"^\[SubtitlesConvertor\]"), "转换字幕"),
]


def parse_line(task, line):
    """解析 yt-dlp 输出，更新任务状态。"""
    if line.startswith("DL|"):
        parts = line.split("|")
        if len(parts) >= 7:
            def num(x):
                try:
                    v = float(x)
                    return v if v >= 0 else None
                except Exception:
                    return None

            downloaded = num(parts[2])
            total = num(parts[3]) or num(parts[4])
            speed = num(parts[5])
            eta = num(parts[6])
            with task_lock:
                if downloaded is not None:
                    task["downloaded"] = downloaded
                if total:
                    task["total"] = total
                if speed:
                    task["speed"] = speed
                if eta:
                    task["eta"] = eta
                if downloaded and total:
                    new_pct = min(100.0, downloaded * 100.0 / total)
                    # 视频/音频分流下载时进度会回落，标记为第 N 路
                    if task.get("percent", 0) > 90 and new_pct < 8:
                        task["part"] = task.get("part", 1) + 1
                    task["percent"] = new_pct
                if parts[1] == "finished":
                    task["percent"] = 100.0
                    task["speed"] = 0
            return True

    m = re.match(r'^\[download\] Destination: (.+)$', line)
    if m:
        with task_lock:
            task["filename"] = m.group(1).strip()
            if task.get("title_auto"):
                task["title"] = clean_title(m.group(1))
        return True

    m = re.match(r'^\[Merger\] Merging formats into "(.+)"$', line)
    if m:
        with task_lock:
            task["filename"] = m.group(1).strip()
            if task.get("title_auto"):
                task["title"] = clean_title(m.group(1))
            task["phase"] = "合并音视频"
            if task["status"] == "downloading":
                task["status"] = "processing"
        return True

    m = re.match(r'^\[(ExtractAudio|VideoConvertor|FixupM3u8|FixupM4a|MoveFiles|EmbedThumbnail|Metadata|EmbedSubtitle)\] .*?"?(.+?)"?$', line)
    if m:
        with task_lock:
            task["filename"] = m.group(2).strip()
            if task.get("title_auto"):
                task["title"] = clean_title(m.group(2))
            task["phase"] = POST_PHASE.get(m.group(1), "后处理")
            if task["status"] == "downloading":
                task["status"] = "processing"
        return True

    for rx, phase in PHASE_PATTERNS:
        if rx.match(line):
            with task_lock:
                task["phase"] = phase
                if task["status"] == "downloading":
                    task["status"] = "processing"
            return False

    m = re.match(r'^\[download\] (.+?) has already been downloaded', line)
    if m:
        with task_lock:
            task["filename"] = m.group(1).strip()
            if task.get("title_auto"):
                task["title"] = clean_title(m.group(1))
            task["percent"] = 100.0
        return True

    return False


def clean_title(path):
    name = os.path.basename(path)
    name = re.sub(r"\.[a-zA-Z0-9]{2,5}$", "", name)
    name = re.sub(r"\.f\d+$", "", name)
    name = re.sub(r"\.(mp4|m4a|webm|mkv|mp3|opus|aac)$", "", name, flags=re.I)
    name = re.sub(r"\.(video|audio|wm)$", "", name, flags=re.I)
    name = re.sub(r"\s*\[[^\[\]]{3,20}\]$", "", name)
    return name.strip() or "未命名"


def decode_line(raw):
    """yt-dlp 在 Windows 上可能用 UTF-8 或 GBK 输出，这里两种都兜住。"""
    if isinstance(raw, str):
        return raw.rstrip("\r\n")
    b = raw.rstrip(b"\r\n")
    for enc in ("utf-8", "gbk"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", "replace")


def run_task(task):
    tid = task["id"]
    # 取消可能发生在「调度器选中」与「线程真正启动」之间，这里补一道闸
    if task.get("cancel"):
        with task_lock:
            task["status"] = "canceled"
            task["phase"] = "已取消"
            task["finished"] = time.time()
        return
    cmd = build_cmd(task)
    add_log("开始任务：" + task["url"], tid)
    add_log("$ " + " ".join(cmd), tid, "cmd")
    with task_lock:
        task["status"] = "downloading"
        task["started"] = time.time()
        task["phase"] = "获取信息"
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            bufsize=0,
            creationflags=CREATE_NO_WINDOW,
            env=child_env(),
            cwd=BASE_DIR,
        )
    except Exception as e:
        with task_lock:
            task["status"] = "error"
            task["error"] = "无法启动 yt-dlp：%s" % e
            task["finished"] = time.time()
        add_log("启动失败：%s" % e, tid, "error")
        return

    PROCS[tid] = proc
    last_error = ""
    tail = []
    try:
        for raw in proc.stdout:
            line = decode_line(raw)
            if not line.strip():
                continue
            tail.append(line)
            del tail[:-40]
            if line.startswith("ERROR:"):
                last_error = line
                add_log(line, tid, "error")
                with task_lock:
                    task["error"] = line[6:].strip()
                continue
            if line.startswith("WARNING:"):
                add_log(line, tid, "warn")
                continue
            if parse_line(task, line):
                continue
            add_log(line, tid, "dim" if line.startswith("[") else "info")
    finally:
        code = proc.wait()
        PROCS.pop(tid, None)

    # 剪辑双轨：确保是纯轨（源为混流单文件时无损抽出）
    if code == 0 and not task.get("cancel") and task["opts"].get("track") in ("video", "audio"):
        try:
            ensure_single_track(task)
        except Exception as e:
            add_log("拆轨异常：%s" % e, tid, "warn")

    # 去水印（视频轨 / 成片，音频轨不处理）
    if (code == 0
            and not task.get("cancel")
            and (task["opts"].get("watermark") or {}).get("enabled")
            and task["opts"].get("track") != "audio"):
        try:
            apply_watermark(task)
        except Exception as e:
            add_log("去水印异常：%s" % e, tid, "error")

    with task_lock:
        canceled = task.get("cancel")
        task["finished"] = time.time()
        if canceled:
            task["status"] = "canceled"
            task["phase"] = "已取消"
        elif code == 0:
            task["status"] = "done"
            task["percent"] = 100.0
            task["phase"] = "已完成"
            task["speed"] = 0
            task["eta"] = 0
            f = task.get("filename") or ""
            if f and os.path.isfile(f):
                try:
                    task["total"] = os.path.getsize(f)
                    task["downloaded"] = task["total"]
                except Exception:
                    pass
        else:
            task["status"] = "error"
            task["phase"] = "失败"
            if task.get("title_auto") and task.get("title") in ("待下载", ""):
                task["title"] = task["url"][:90]
            if not task.get("error"):
                task["error"] = last_error or ("yt-dlp 退出码 %s" % code)

    if task["status"] == "done":
        add_log("✔ 完成：" + (task.get("title") or ""), tid, "ok")
    elif task["status"] == "error":
        add_log("✘ 失败：" + (task.get("error") or ""), tid, "error")
    push_history(task)


def scheduler():
    while True:
        try:
            with task_lock:
                running = sum(
                    1 for t in TASKS.values()
                    if t["status"] in ("downloading", "processing")
                )
                limit = int(config.get("concurrency") or 2)
                nxt = None
                if running < limit:
                    for i in TASK_ORDER:
                        t = TASKS.get(i)
                        if t and t["status"] == "queued":
                            nxt = t
                            t["status"] = "downloading"
                            break
            if nxt:
                threading.Thread(target=run_task, args=(nxt,), daemon=True).start()
                time.sleep(0.3)
            else:
                time.sleep(0.5)
        except Exception as e:
            add_log("调度异常：%s" % e, None, "error")
            time.sleep(1)


def create_task(url, opts):
    tid = uuid.uuid4().hex[:10]
    host = ""
    try:
        host = urlparse(url).netloc.replace("www.", "")
    except Exception:
        pass
    task = {
        "id": tid,
        "url": url,
        "host": host,
        "title": opts.get("_title") or "待下载",
        "title_auto": not bool(opts.get("_title")),
        "status": "queued",
        "phase": "排队中",
        "percent": 0.0,
        "part": 1,
        "downloaded": 0,
        "total": 0,
        "speed": 0,
        "eta": 0,
        "filename": "",
        "error": "",
        "created": time.time(),
        "started": 0,
        "finished": 0,
        "preset": opts.get("preset"),
        "track": opts.get("track") or "",
        "wm": bool((opts.get("watermark") or {}).get("enabled")) and opts.get("track") != "audio",
        "wm_done": False,
        "track_ok": False,
        "opts": opts,
        "cancel": False,
        "thumb": opts.get("_thumb") or "",
    }
    with task_lock:
        TASKS[tid] = task
        TASK_ORDER.append(tid)
    return task


# ---------------------------------------------------------------- 元信息解析

INFO_LOCK = threading.Lock()


def run_parse(url, extra=None):
    cmd = [
        BIN, "-J", "--no-playlist", "--flat-playlist",
        "--socket-timeout", "15", "--retries", "2",
    ]
    if FFMPEG:
        cmd += ["--ffmpeg-location", os.path.dirname(FFMPEG)]
    if extra:
        cmd += extra
    cmd.append(url)
    out = subprocess.run(
        cmd, capture_output=True, timeout=90, creationflags=CREATE_NO_WINDOW,
        env=child_env(), cwd=BASE_DIR,
    )
    raw_out = out.stdout or b""
    if out.returncode != 0:
        err_txt = decode_line(out.stderr or b"")
        lines = [l for l in err_txt.strip().splitlines() if l.strip()]
        raise RuntimeError((lines[-1] if lines else "解析失败").replace("ERROR:", "").strip())
    text = None
    for enc in ("utf-8", "gbk"):
        try:
            text = raw_out.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw_out.decode("utf-8", "replace")
    data = json.loads(text)
    heights = set()
    levels = {}
    for f in data.get("formats") or []:
        h = f.get("height")
        if not h:
            continue
        h = int(h)
        heights.add(h)
        # 同一分辨率可能有多档码率（不同编码），取最高的那档
        tbr = float(f.get("tbr") or f.get("vbr") or 0)
        if tbr > levels.get(h, 0):
            levels[h] = tbr
    level_list = [{"h": h, "tbr": round(t, 1)} for h, t in sorted(levels.items(), reverse=True)]
    # 平台侧登录/会员墙提示（不登录时更高画质不可用，属平台限制）
    hint = ""
    err_all = decode_line(out.stderr or b"")
    low = err_all.lower()
    if "premium" in low or "大会员" in err_all:
        hint = "更高画质（1080P 高码率 / 4K）需要平台会员登录，可在「高级选项 → Cookie 来源」选择已登录的浏览器"
    elif "login" in low or "sign in" in low or "cookies" in low:
        hint = "该站点更高画质需要登录，可在「高级选项 → Cookie 来源」选择已登录的浏览器"
    entries = []
    for e in (data.get("entries") or [])[:60]:
        if not e:
            continue
        entries.append({
            "id": e.get("id"),
            "title": e.get("title") or e.get("id"),
            "duration": e.get("duration") or 0,
            "url": e.get("url") or e.get("webpage_url") or "",
        })
    return {
        "ok": True,
        "id": data.get("id"),
        "title": data.get("title") or "未命名",
        "uploader": data.get("uploader") or data.get("channel") or "",
        "duration": data.get("duration") or 0,
        "thumbnail": data.get("thumbnail") or "",
        "webpage_url": data.get("webpage_url") or url,
        "extractor": data.get("extractor_key") or data.get("extractor") or "",
        "view_count": data.get("view_count") or 0,
        "heights": sorted(heights, reverse=True)[:12],
        "levels": level_list[:12],
        "login_hint": hint,
        "is_playlist": data.get("_type") == "playlist",
        "playlist_count": data.get("playlist_count") or (len(data.get("entries") or []) if data.get("_type") == "playlist" else 0),
        "entries": entries,
        "description": (data.get("description") or "")[:400],
    }


# ---------------------------------------------------------------- HTTP

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".json": "application/json; charset=utf-8",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "ytdlp-workbench/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    # --- helpers
    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(data)
        except Exception:
            pass

    def _serve_media(self, f):
        """流式返回视频/音频，支持 Range 请求（拖动进度条）。"""
        try:
            size = os.path.getsize(f)
        except Exception:
            return self._send(404, {"ok": False, "error": "文件不可读"})
        ext = os.path.splitext(f)[1].lower()
        ctype = {
            ".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm",
            ".mkv": "video/x-matroska", ".mov": "video/quicktime",
            ".m4a": "audio/mp4", ".mp3": "audio/mpeg", ".opus": "audio/ogg",
        }.get(ext, "application/octet-stream")
        start, end = 0, size - 1
        rng = self.headers.get("Range") or ""
        m = re.match(r"bytes=(\d*)-(\d*)", rng)
        if m and size > 0:
            if m.group(1):
                start = int(m.group(1))
            if m.group(2):
                end = int(m.group(2))
            start = max(0, min(start, size - 1))
            end = max(start, min(end, size - 1))
            self.send_response(206)
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(max(0, end - start + 1)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            with open(f, "rb") as fh:
                fh.seek(start)
                remain = end - start + 1
                while remain > 0:
                    chunk = fh.read(min(65536, remain))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remain -= len(chunk)
        except Exception:
            pass

    def _json_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith("/api/"):
            return self.api_get(path)
        if path == "/":
            path = "/index.html"
        fp = os.path.normpath(os.path.join(WEB_DIR, path.lstrip("/")))
        if not fp.startswith(WEB_DIR) or not os.path.isfile(fp):
            return self._send(404, {"ok": False, "error": "not found"})
        with open(fp, "rb") as f:
            data = f.read()
        self._send(200, data, MIME.get(os.path.splitext(fp)[1].lower(), "application/octet-stream"))

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            return self.api_post(path, self._json_body())
        except Exception as e:
            return self._send(500, {"ok": False, "error": str(e)})

    # --- API
    def api_get(self, path):
        if path == "/api/thumb":
            # 视频首帧缩略图（编辑器 poster，打开即有画面）
            q = urlparse(self.path).query
            target = ""
            for part in q.split("&"):
                if part.startswith("path="):
                    target = unquote(part[5:])
                    break
            if not target or not os.path.isfile(target) or not FFMPEG:
                return self._send(404, {"ok": False, "error": "不可用"})
            try:
                import hashlib
                key = hashlib.md5(target.encode("utf-8")).hexdigest()[:16]
                cache = os.path.join(APP_DIR, "cache")
                os.makedirs(cache, exist_ok=True)
                out = os.path.join(cache, "thumb_" + key + ".jpg")
                if not os.path.isfile(out):
                    r = subprocess.run(
                        [FFMPEG, "-y", "-v", "error", "-ss", "1", "-i", target,
                         "-frames:v", "1", "-vf", "scale=960:-2", "-q:v", "4", out],
                        capture_output=True, timeout=90, creationflags=CREATE_NO_WINDOW,
                    )
                    if r.returncode != 0 or not os.path.isfile(out):
                        return self._send(404, {"ok": False, "error": "抽帧失败"})
                with open(out, "rb") as f:
                    return self._send(200, f.read(), "image/jpeg")
            except Exception as e:
                return self._send(500, {"ok": False, "error": str(e)})
        if path == "/api/media":
            # 视频预览流（支持 Range，让浏览器能拖时间轴）
            q = urlparse(self.path).query
            target = ""
            for part in q.split("&"):
                if part.startswith("path="):
                    target = unquote(part[5:])
                    break
            if not target or not os.path.isfile(target):
                return self._send(404, {"ok": False, "error": "文件不存在"})
            return self._serve_media(target)
        if path == "/api/status":
            st = ai_status()
            return self._send(200, {
                "ok": True,
                "version": VERSION,
                "ffmpeg": bool(FFMPEG),
                "ffmpegPath": FFMPEG or "",
                "outdir": config.get("outdir"),
                "concurrency": config.get("concurrency"),
                "bin": BIN,
                "baseDir": BASE_DIR,
                "numpy": np is not None,
                "ai": {"ready": bool(st.get("ready")), "error": st.get("error") or ""},
            })
        if path == "/api/tasks":
            items, stats = snapshot()
            return self._send(200, {"ok": True, "tasks": items, "stats": stats})
        if path.startswith("/api/logs"):
            q = urlparse(self.path).query
            after = 0
            m = re.search(r"after=(\d+)", q)
            if m:
                after = int(m.group(1))
            with log_lock:
                lines = [l for l in LOG_LINES if l["seq"] > after]
                last = LOG_SEQ[0]
            return self._send(200, {"ok": True, "lines": lines[-400:], "last": last})
        if path == "/api/history":
            with history_lock:
                return self._send(200, {"ok": True, "items": HISTORY[:200]})
        return self._send(404, {"ok": False, "error": "unknown api"})

    def api_post(self, path, body):
        if path == "/api/parse":
            url = (body.get("url") or "").strip()
            if not url:
                return self._send(400, {"ok": False, "error": "请输入链接"})
            try:
                info = run_parse(url)
            except subprocess.TimeoutExpired:
                return self._send(200, {"ok": False, "error": "解析超时，请检查网络或链接"})
            except Exception as e:
                return self._send(200, {"ok": False, "error": str(e)})
            return self._send(200, info)

        if path == "/api/download":
            urls = body.get("urls") or []
            opts = body.get("opts") or {}
            opts["outdir"] = os.path.normpath(opts.get("outdir") or config.get("outdir"))
            if not os.path.isdir(opts["outdir"]):
                try:
                    os.makedirs(opts["outdir"], exist_ok=True)
                except Exception as e:
                    return self._send(200, {"ok": False, "error": "输出目录不可用：%s" % e})
            if not urls:
                return self._send(400, {"ok": False, "error": "没有可下载的链接"})
            ids = []
            mode = opts.get("mode") or "merge"
            for u in urls:
                u = (u or "").strip()
                if not u or u.startswith("#"):
                    continue
                if mode == "split":
                    vo = dict(opts)
                    vo["track"] = "video"
                    if vo.get("preset") in ("audio-mp3", "audio-m4a", "audio-best"):
                        vo["preset"] = "best"
                        vo["format"] = ""
                    ids.append(create_task(u, vo)["id"])
                    ao = dict(opts)
                    ao["track"] = "audio"
                    ids.append(create_task(u, ao)["id"])
                else:
                    t = create_task(u, dict(opts))
                    ids.append(t["id"])
            add_log("已加入队列 %d 个任务%s" % (len(ids), "（双轨拆分）" if mode == "split" else ""))
            return self._send(200, {"ok": True, "ids": ids})

        if path == "/api/cancel":
            tid = body.get("id")
            t = TASKS.get(tid)
            if not t:
                return self._send(404, {"ok": False, "error": "任务不存在"})
            with task_lock:
                t["cancel"] = True
                if t["status"] == "queued":
                    t["status"] = "canceled"
                    t["phase"] = "已取消"
            p = PROCS.get(tid)
            if p:
                try:
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                                   capture_output=True, creationflags=CREATE_NO_WINDOW)
                except Exception:
                    try:
                        p.kill()
                    except Exception:
                        pass
            add_log("已请求取消", tid, "warn")
            return self._send(200, {"ok": True})

        if path == "/api/retry":
            tid = body.get("id")
            t = TASKS.get(tid)
            if not t:
                return self._send(404, {"ok": False, "error": "任务不存在"})
            new = create_task(t["url"], dict(t["opts"]))
            return self._send(200, {"ok": True, "id": new["id"]})

        if path == "/api/remove":
            tid = body.get("id")
            with task_lock:
                t = TASKS.pop(tid, None)
                if tid in TASK_ORDER:
                    TASK_ORDER.remove(tid)
            if t and t["status"] in ("downloading", "processing"):
                p = PROCS.get(tid)
                if p:
                    try:
                        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                                       capture_output=True, creationflags=CREATE_NO_WINDOW)
                    except Exception:
                        pass
            return self._send(200, {"ok": True})

        if path == "/api/clear":
            mode = body.get("mode") or "finished"
            with task_lock:
                keep = []
                for i in TASK_ORDER:
                    t = TASKS.get(i)
                    if not t:
                        continue
                    if mode == "all" and t["status"] not in ("downloading", "processing"):
                        TASKS.pop(i, None)
                    elif mode == "finished" and t["status"] in ("done", "error", "canceled"):
                        TASKS.pop(i, None)
                    else:
                        keep.append(i)
                TASK_ORDER[:] = keep
            return self._send(200, {"ok": True})

        if path == "/api/config":
            if "outdir" in body and body["outdir"]:
                config["outdir"] = body["outdir"]
            if "concurrency" in body:
                try:
                    config["concurrency"] = max(1, min(6, int(body["concurrency"])))
                except Exception:
                    pass
            save_config()
            return self._send(200, {"ok": True, "config": config})

        if path == "/api/pickfolder":
            p = pick_folder()
            if p:
                return self._send(200, {"ok": True, "path": p})
            return self._send(200, {"ok": False, "error": "未选择目录"})

        if path == "/api/open":
            target = body.get("path") or config.get("outdir")
            try:
                if not os.path.exists(target):
                    return self._send(200, {"ok": False, "error": "路径不存在：%s" % target})
                open_in_explorer(target)
                return self._send(200, {"ok": True})
            except Exception as e:
                return self._send(200, {"ok": False, "error": str(e)})

        if path == "/api/openfile":
            f = body.get("path") or ""
            if f and os.path.isfile(f):
                try:
                    open_file(f)
                    return self._send(200, {"ok": True})
                except Exception as e:
                    return self._send(200, {"ok": False, "error": str(e)})
            return self._send(200, {"ok": False, "error": "文件不存在"})

        if path == "/api/clearlog":
            with log_lock:
                LOG_LINES.clear()
            return self._send(200, {"ok": True})

        if path == "/api/update":
            try:
                r = subprocess.run(
                    [BIN, "-U"], capture_output=True, timeout=180,
                    creationflags=CREATE_NO_WINDOW, env=child_env(), cwd=BASE_DIR,
                )
                out = (decode_line(r.stdout or b"") + "\n" + decode_line(r.stderr or b"")).strip()
                for l in out.splitlines():
                    if l.strip():
                        add_log(l.strip(), None, "info")
                detect_version()
                return self._send(200, {"ok": True, "output": out[:1500], "version": VERSION})
            except Exception as e:
                return self._send(200, {"ok": False, "error": str(e)})

        if path == "/api/clearhistory":
            with history_lock:
                HISTORY.clear()
                write_json(HISTORY_PATH, HISTORY)
            return self._send(200, {"ok": True})

        if path == "/api/ai/status":
            st = ai_status()
            return self._send(200, {
                "ok": True,
                "ready": bool(st.get("ready")),
                "error": st.get("error") or "",
                "model": AI_MODEL,
                "modelExists": os.path.isfile(AI_MODEL),
            })
        if path == "/api/ai/setup":
            ok, msg = setup_ai_engine()
            return self._send(200, {"ok": ok, "message": msg})
        if path == "/api/wm/detect":
            target = (body.get("path") or "").strip()
            tid = (body.get("id") or "").strip()
            if not target and tid:
                with task_lock:
                    t = TASKS.get(tid)
                    target = (t or {}).get("filename") or ""
            if not target or not os.path.isfile(target):
                return self._send(200, {"ok": False, "error": "找不到视频文件（请先下载完成）"})
            try:
                regs = detect_watermarks(target)
            except Exception as e:
                return self._send(200, {"ok": False, "error": str(e)})
            info = probe_video(target) or {}
            return self._send(200, {"ok": True, "regions": regs, "width": info.get("w"),
                                    "height": info.get("h"), "duration": info.get("duration")})

        if path == "/api/wm/apply":
            src_id = (body.get("id") or "").strip()
            with task_lock:
                src_task = dict(TASKS.get(src_id) or {})
            src_path = src_task.get("filename") or ""
            if not src_path or not os.path.isfile(src_path):
                return self._send(200, {"ok": False, "error": "任务不存在或文件已删除"})
            wm = body.get("watermark") or {}
            if not (wm.get("regions") or wm.get("enabled")):
                return self._send(200, {"ok": False, "error": "未指定水印区域"})
            opts = dict(src_task.get("opts") or {})
            opts["watermark"] = {**wm, "enabled": True}
            t = create_task(src_task.get("url") or "", opts)
            t["filename"] = src_path
            t["title"] = (src_task.get("title") or "视频") + " · 去水印"
            t["title_auto"] = False
            t["status"] = "processing"
            t["phase"] = "去水印处理中"
            t["wm"] = True
            t["started"] = time.time()

            def worker():
                try:
                    ok = apply_watermark(t)
                except Exception as e:
                    add_log("去水印异常：%s" % e, t["id"], "error")
                    ok = False
                with task_lock:
                    t["finished"] = time.time()
                    if ok and t.get("wm_done"):
                        t["status"] = "done"
                        t["percent"] = 100.0
                        t["phase"] = "已完成"
                    elif t.get("cancel"):
                        t["status"] = "canceled"
                    else:
                        t["status"] = "error"
                        t["error"] = t.get("error") or "去水印失败"

            threading.Thread(target=worker, daemon=True).start()
            return self._send(200, {"ok": True, "id": t["id"]})

        return self._send(404, {"ok": False, "error": "unknown api"})


def open_in_explorer(target):
    """打开目录；若 target 是文件则选中该文件。"""
    target = os.path.normpath(target)
    if os.path.isfile(target):
        if os.name == "nt":
            # explorer 要求 /select, 与路径是同一个参数，拆开传会失效
            subprocess.Popen(["explorer", "/select," + target])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(target) or "."])
        return
    if os.name == "nt":
        os.startfile(target)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target])


def open_file(f):
    """用系统默认程序打开文件。"""
    if os.name == "nt":
        os.startfile(f)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", f])
    else:
        subprocess.Popen(["xdg-open", f])


def pick_folder():
    """优先用 PowerShell 的系统文件夹选择器（不依赖 Python 是否带 tkinter）。"""
    try:
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$d = New-Object System.Windows.Forms.FolderBrowserDialog;"
            "$d.Description = '选择下载保存目录';"
            "$d.ShowNewFolderButton = $true;"
            "if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK)"
            " { Write-Output $d.SelectedPath }"
        )
        r = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", ps],
            capture_output=True, text=True, timeout=240,
            creationflags=CREATE_NO_WINDOW,
        )
        if r.returncode == 0:
            lines = [l.strip() for l in (r.stdout or "").splitlines() if l.strip()]
            return lines[-1] if lines else None
    except Exception:
        pass
    try:
        import tkinter as tk
        from tkinter import filedialog

        result = {"path": None}

        def work():
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            result["path"] = filedialog.askdirectory(title="选择下载保存目录")
            root.destroy()

        th = threading.Thread(target=work, daemon=True)
        th.start()
        th.join(timeout=240)
        return result["path"]
    except Exception:
        return None


def pick_port(start=8765):
    import socket
    for p in range(start, start + 30):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p))
            s.close()
            return p
        except OSError:
            s.close()
    return start


def _probe_instance(port):
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/status" % port, timeout=0.8) as r:
            data = json.loads(r.read().decode("utf-8"))
            return port if data.get("ok") else None
    except Exception:
        return None


def existing_instance(start=8765):
    """如果工作台已经在跑（端口范围内任意一个），直接复用，避免开两份。
    并行探测：某些网络环境下关闭端口会被过滤而不拒绝，串行扫会拖到几十秒。"""
    from concurrent.futures import ThreadPoolExecutor
    ports = list(range(start, start + 30))
    with ThreadPoolExecutor(max_workers=len(ports)) as pool:
        for res in pool.map(_probe_instance, ports):
            if res:
                return res
    return None


def main():
    global BIN
    if not os.path.exists(BIN):
        if FROZEN:
            # exe 版：没有 bin/yt-dlp.exe 时自动下载（PATH 上也没有才会走到这）
            print("首次运行：正在获取 yt-dlp（约 15MB，只需这一次）...")
            if ensure_ytdlp():
                BIN = find_ytdlp()
                print("yt-dlp 已就绪。")
            else:
                print("!! yt-dlp 自动下载失败（网络问题？）")
                print("   请手动下载后放到本程序同目录的 bin\\ 中：")
                print("   https://github.com/yt-dlp/yt-dlp/releases/latest")
                print("   或安装 yt-dlp 并加入 PATH，重新运行本程序")
                input("按回车退出…")
                return
        else:
            print("!! 未找到 yt-dlp 可执行文件")
            print("   请任选一种方式：")
            print("   1. 下载 yt-dlp.exe 放到 bin\\ 目录：")
            print("      https://github.com/yt-dlp/yt-dlp/releases/latest")
            print("   2. 或安装 yt-dlp 并加入 PATH（pip install yt-dlp / winget install yt-dlp）")
            input("按回车退出…")
            return
    detect_version()
    ff_ok = ensure_ffmpeg()
    if not ff_ok:
        print("!! 未找到 ffmpeg（高清合并 / 双轨拆分 / 去水印需要它）")
        print("   本 exe 已内置 ffmpeg，若此提示仍出现，请检查 bin 目录写入权限")
    running = existing_instance()
    if running:
        url = "http://127.0.0.1:%d" % running
        print("工作台已在运行：%s（已为你打开浏览器）" % url)
        if "--no-browser" not in sys.argv:
            webbrowser.open(url)
        time.sleep(1.5)
        return
    port = pick_port()
    url = "http://127.0.0.1:%d" % port
    threading.Thread(target=scheduler, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.daemon_threads = True
    print("=" * 56)
    print("  yt-dlp 可视化工作台")
    print("  版本 %s | ffmpeg %s" % (VERSION, "已就绪" if FFMPEG else "未找到"))
    print("  地址 %s" % url)
    print("  目录 %s" % config.get("outdir"))
    print("  关闭本窗口即可停止服务")
    print("=" * 56)
    if "--no-browser" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
