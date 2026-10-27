"""创建 GitHub Release 并上传 exe。

token 从 git 凭据管理器取（git credential fill），不落盘、不回显。
"""
import json
import os
import ssl
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TAG = "v1.6"
EXE = os.path.join(ROOT, "dist", "yt-dlp-workbench.exe")
REPO = "yuanshen-123uodo/yt-dlp-workbench"

NAME = "v1.6 — 超分画质全链路修复（色彩 / HDR / 降噪 / 锐化 / 编码）"
BODY = """上一版把超分改成分块之后，处理速度能用了，但画质链路上还有一堆问题没处理。
这一版把它们一次清掉。

## 修了一个真 bug：输出颜色发灰

之前合帧只有 `-pix_fmt yuv420p`，没写色彩标记。ffmpeg 转 YUV 时按 BT.709 算的数值，
但 mp4 容器里 `color_space` 是 `unknown`——播放器遇到空值普遍按 BT.601 解码。
结果就是 BT.709 的片源发灰、肤色偏绿。

现在每段编码和最终合并都按源的实际色彩空间写入 `colorspace / color_primaries /
color_trc / color_range`，播放器用同一套矩阵解码。

## HDR 片源不再变黑

10bit PQ / HLG 直接截断成 8bit JPEG，高光会整片压平、整体发暗。现在识别到 HDR
会在抽帧前先 tonemap：`zscale` 转线性光 → `tonemap=hable` → 回 BT.709。

## 抽帧改 4:4:4

JPEG 默认存 4:2:0，色度被压两次（抽帧一次、编码一次），红色字幕和彩色渐变的边缘
会糊两次。改存 4:4:4 只多 18% 体积。抽帧质量也从 q2 提到 q1。

## 新增三组画质选项

| 选项 | 默认 | 说明 |
|---|---|---|
| 降噪 | 按分辨率自动 | AI 会把压缩块和噪点当细节一起放大，所以降噪必须在超分**之前**。480P 以下默认强力、720P 中等、1080P 轻微 |
| 锐化 | 中等 | Real-ESRGAN 是回归式超分，输出干净但偏软，`cas` 只提边缘不放大噪点 |
| 编码质量 | 高（crf16） | 超分后细节密度高，crf18 会把刚补出来的细节再糊回去 |

面板上还会显示源信息（分辨率 · 编码 · 位深 · 色彩空间），HDR 会标黄提示。

## 其他

- exe 体积回到 74.8MB（打包时排除了被 hook 误收的 pandas / pyarrow / scipy，
  否则会涨到 160MB）
- 新增 `diag/` 四个测试脚本，覆盖源码 ffmpeg 9.0、内置 ffmpeg 7.1、无 ffprobe
  回退、HDR 四种环境

> 分块处理、音频直通、时长帧率保持这些上一版的行为不变。
"""

ctx = ssl._create_unverified_context()


def token():
    p = subprocess.run(["git", "credential", "fill"], cwd=ROOT,
                       input=b"protocol=https\nhost=github.com\n\n",
                       capture_output=True)
    for line in (p.stdout or b"").decode("utf-8", "replace").splitlines():
        if line.startswith("password="):
            return line[9:].strip()
    return ""


def api(method, url, data=None, ctype="application/json"):
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "token " + TOK)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Content-Type", ctype)
    req.add_header("User-Agent", "yt-dlp-workbench-release")
    with urllib.request.urlopen(req, timeout=120, context=ctx) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


TOK = token()
if not TOK:
    print("拿不到 GitHub token（git credential fill 无 password）")
    sys.exit(1)
print("token 长度 %d，前缀 %s***" % (len(TOK), TOK[:4]))

# 已存在就复用
rel = None
try:
    rel = api("GET", "https://api.github.com/repos/%s/releases/tags/%s" % (REPO, TAG))
    print("release 已存在:", rel.get("html_url"))
except Exception:
    rel = None

if not rel:
    rel = api("POST", "https://api.github.com/repos/%s/releases" % REPO,
              json.dumps({"tag_name": TAG, "name": NAME, "body": BODY,
                          "draft": False, "prerelease": False}).encode("utf-8"))
    print("创建 release:", rel.get("html_url"))

size = os.path.getsize(EXE)
print("上传 %s (%.1f MB)…" % (os.path.basename(EXE), size / 1048576.0))
up = ("https://uploads.github.com/repos/%s/releases/%s/assets?name=%s"
      % (REPO, rel["id"], os.path.basename(EXE)))
with open(EXE, "rb") as f:
    blob = f.read()
asset = api("POST", up, blob, ctype="application/octet-stream")
print("上传完成: %s (%.1f MB)" % (asset.get("name"), asset.get("size", 0) / 1048576.0))
print("下载地址:", asset.get("browser_download_url"))
