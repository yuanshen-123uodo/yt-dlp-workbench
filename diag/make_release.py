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
TAG = "v1.8"
EXE = os.path.join(ROOT, "dist", "yt-dlp-workbench.exe")
REPO = "yuanshen-123uodo/yt-dlp-workbench"

NAME = "v1.8 — 修复「打开所在目录」跳到错误位置"
BODY = """点任务卡片上的 📁「打开所在目录」，资源管理器打开的是「文档」，不是视频真正所在的文件夹。
这一版修的就是它。

## 为什么跳错地方

原来的写法是 `explorer /select,<路径>`。实测在本机（Win11）上，**给 explorer.exe 传的参数被整条丢弃**，
它一律打开默认目录。四种写法逐个试过：

| 写法 | 实际打开 |
|---|---|
| `Popen(["explorer", "/select," + path])` | 文档 |
| `Popen('explorer /select,"path"')` | 桌面 |
| `Popen(["explorer", folder])` | 文档 |
| `ShellExecuteW("explorer.exe", '/select,"path"')` | 桌面 |
| `os.startfile(folder)` | ✅ 正确 |

也就是说与引号、空格、中文都无关，是「给 explorer 传参数」这条路本身不通。
只要不传参数、让 ShellExecute 自己去开目录，位置就完全正确。

## 怎么修的

1. 文件目标优先用 Shell 官方接口 `SHOpenFolderAndSelectItems`，路径通过 PIDL 传递，
   绕开 explorer 的命令行解析，顺带能把文件选中高亮。
2. 接口不可用时退化为 `os.startfile(目录)`——位置保证正确，只是不高亮文件。
3. 加了结果校验：接口返回 S_OK 不代表窗口真的开了，必须轮询到资源管理器窗口标题里
   出现目标目录名才算成功，否则继续走退化路径。防的就是「点了没反应」这类静默失败。
4. 文件已被删除但目录还在时，退到父目录打开，不再直接报「路径不存在」。

## 测试

新增 `diag/test_openfolder.py`，判据是**窗口标题里必须出现目标目录名**，
不是「Popen 没抛异常」——后者在原来的 bug 下也是通过的，等于没测。

| 场景 | 结果 |
|---|---|
| 文件 → 所在目录 | 通过 |
| 目录 → 目录本身 | 通过 |
| 文件已删除 → 退父目录 | 通过 |
| 下载根目录 | 通过 |
| 不存在的路径 | 正确报错 |
| 打包 exe · `/api/open` | 通过 |

测试路径故意带中文、空格和方括号（`_目录 测试[1]/样片 [test].mp4`），
因为这三样最容易在命令行传参时被吃掉。
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
