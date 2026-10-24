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
from urllib.parse import urlparse

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
    p = shutil.which("ffmpeg")
    if p:
        return p
    local = os.path.join(BASE_DIR, "bin", "ffmpeg.exe")
    if os.path.exists(local):
        return local
    return None


FFMPEG = find_ffmpeg()
FFPROBE = None


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

    if track == "video":
        # 只取视频轨，优先 h264 以便剪辑软件直接吃
        if fmt:
            cmd += ["-f", fmt]
        elif h:
            cmd += ["-f", "bv*[height<=%d][vcodec^=avc1]/bv*[height<=%d]/bv[height<=%d]" % (h, h, h)]
        else:
            cmd += ["-f", "bv*[vcodec^=avc1]/bv*/b"]
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
            cmd += ["-f", "bv*[height<=%d]+ba/b[height<=%d]/bv*+ba/b" % (h, h)]
        else:
            cmd += ["-f", "bv*+ba/b"]
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
    if not FFPROBE:
        return None
    try:
        r = subprocess.run(
            [FFPROBE, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-show_entries", "format=duration",
             "-of", "json", path],
            capture_output=True, timeout=60, creationflags=CREATE_NO_WINDOW,
        )
        d = json.loads((r.stdout or b"{}").decode("utf-8", "replace"))
        st = (d.get("streams") or [{}])[0]
        return {
            "w": int(st.get("width") or 0),
            "h": int(st.get("height") or 0),
            "duration": float((d.get("format") or {}).get("duration") or 0),
        }
    except Exception:
        return None


def watermark_filter(info, wm):
    """根据位置/尺寸生成 ffmpeg 滤镜：delogo 消除，或直接裁掉边条。"""
    W, H = info.get("w") or 0, info.get("h") or 0
    if W < 32 or H < 32:
        return None
    pos = wm.get("pos") or "tr"
    rw_p, rh_p = WM_SIZE.get(wm.get("size") or "m", WM_SIZE["m"])
    rw = max(16, int(W * rw_p) // 2 * 2)
    rh = max(16, int(H * rh_p) // 2 * 2)
    m = max(4, int(W * 0.012))

    if pos == "tl":
        x, y = m, m
    elif pos == "bl":
        x, y = m, H - rh - m
    elif pos == "br":
        x, y = W - rw - m, H - rh - m
    else:
        x, y = W - rw - m, m

    if (wm.get("mode") or "delogo") == "crop":
        strip = rh + m * 2
        strip = strip // 2 * 2
        if strip >= H - 32:
            strip = max(2, (H // 4) // 2 * 2)
        if pos in ("tl", "tr"):
            return "crop=iw:ih-%d:0:%d" % (strip, strip)
        return "crop=iw:ih-%d:0:0" % strip

    x = max(0, min(x, W - rw - 1))
    y = max(0, min(y, H - rh - 1))
    return "delogo=x=%d:y=%d:w=%d:h=%d:show=0" % (x, y, rw, rh)


STREAM_TYPES = ("video", "audio", "subtitle", "data", "attachment")


def probe_streams(path):
    """返回 [(type, codec), …]，失败返回 None。"""
    if not FFPROBE:
        return None
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
    """下载完成后用 ffmpeg 重编码去水印，成功则替换原文件。"""
    tid = task["id"]
    path = task.get("filename") or ""
    wm = task["opts"].get("watermark") or {}
    if not path or not os.path.isfile(path):
        add_log("去水印跳过：找不到源文件", tid, "warn")
        return False
    if not FFMPEG or not FFPROBE:
        add_log("去水印跳过：缺少 ffmpeg/ffprobe", tid, "warn")
        return False

    info = probe_video(path)
    if not info or not info.get("w"):
        add_log("去水印跳过：无法读取视频尺寸", tid, "warn")
        return False
    vf = watermark_filter(info, wm)
    if not vf:
        add_log("去水印跳过：视频尺寸异常", tid, "warn")
        return False

    base = os.path.splitext(path)[0]
    tmp = base + ".__wm.mp4"
    final = base + ".mp4"
    dur = info.get("duration") or 0

    add_log("开始去水印：%s（%dx%d）" % (vf, info["w"], info["h"]), tid)
    with task_lock:
        task["status"] = "processing"
        task["phase"] = "去水印处理中"
        task["percent"] = 0.0
        task["speed"] = 0
        task["eta"] = 0

    cmd = [
        FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
        "-i", path, "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-c:a", "copy",
        "-progress", "pipe:1", "-nostats", tmp,
    ]
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
        BIN, "-J", "--no-warnings", "--no-playlist", "--flat-playlist",
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
    for f in data.get("formats") or []:
        h = f.get("height")
        if h:
            heights.add(int(h))
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
        if path == "/api/status":
            return self._send(200, {
                "ok": True,
                "version": VERSION,
                "ffmpeg": bool(FFMPEG),
                "ffmpegPath": FFMPEG or "",
                "outdir": config.get("outdir"),
                "concurrency": config.get("concurrency"),
                "bin": BIN,
                "baseDir": BASE_DIR,
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
