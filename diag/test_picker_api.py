# -*- coding: utf-8 -*-
"""端到端：经 HTTP 接口触发选择器，确认窗口真的弹在用户眼前，取消后接口正常返回。

窗口属于 PowerShell 子进程，不是服务进程，所以不能用服务 PID 过滤，
按标题找（文件对话框的 Title 是我们自己传的，不受系统本地化影响）。
"""
import ctypes
import json
import os
import subprocess
import sys
import time
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

u = ctypes.windll.user32
TITLE = "选择要处理的视频或音频文件"
BASE = "http://127.0.0.1:8766"


def find():
    h = u.FindWindowW(None, TITLE)
    if not h:
        return None
    r = wintypes.RECT()
    u.GetWindowRect(h, ctypes.byref(r))
    cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
    at = u.GetAncestor(u.WindowFromPoint(wintypes.POINT(cx, cy)), 2) or 0
    st = u.GetWindowLongW(h, -16)
    return {
        "h": h,
        "rect": (r.left, r.top, r.right, r.bottom),
        "visible": bool(st & 0x10000000),
        "on_top": at == h,
        "top_class": (lambda: (
            (lambda b: (u.GetClassNameW(at, b, 128), b.value)[1])(ctypes.create_unicode_buffer(128))
        ))(),
    }


# 后台发请求（选择器不关就一直挂着，所以给 90 秒）
proc = subprocess.Popen(
    ["curl", "-s", "-m", "90", "-X", "POST", BASE + "/api/import",
     "-H", "Content-Type: application/json", "-d", '{"pick":true}'],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
)

info = None
for _ in range(60):
    time.sleep(0.3)
    info = find()
    if info:
        break

if not info:
    proc.kill()
    print("[失败] 18 秒内没看到对话框")
    sys.exit(1)

time.sleep(2.0)
info = find()
print("位置尺寸 : %s" % (info["rect"],))
print("WS_VISIBLE : %s" % info["visible"])
print("中心点最上层就是它 : %s  (该处窗口类名 %s)" % (info["on_top"], info["top_class"]))

u.PostMessageW(info["h"], 0x0010, 0, 0)  # 用户点取消
try:
    out, _ = proc.communicate(timeout=20)
except subprocess.TimeoutExpired:
    proc.kill()
    out = b""

body = (out or b"").decode("utf-8", "replace").strip()
print("接口返回 : %s" % (body or "<空/超时>"))

ok = info["visible"] and info["on_top"] and '"cancelled": true' in body.replace('"cancelled":true', '"cancelled": true')
print("结论 : %s" % ("通过" if ok else "不通过"))
sys.exit(0 if ok else 1)
