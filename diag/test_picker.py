# -*- coding: utf-8 -*-
"""验证系统文件/文件夹选择器是否真的出现在用户眼前。

判定不能只看 IsWindowVisible——它只反映 WS_VISIBLE 标志，被完全遮住的窗口照样返回 True。
这里的硬判据是：窗口中心点 WindowFromPoint 得到的顶层窗口就是它自己。

窗口定位按「进程 ID + 类名 #32770」找，不用标题：
FolderBrowserDialog 的标题是系统本地化的（中文系统叫"浏览文件夹"），按标题找不到。

用法：
    python diag/test_picker.py          # 测文件多选
    python diag/test_picker.py folder   # 测文件夹
会弹一个选择器，几秒后自动关掉（等同用户点取消），不需要手动操作。
"""
import ctypes
import os
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server as S

u = ctypes.windll.user32
EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def find_dialogs(pid):
    """找属于指定进程的系统对话框窗口（类名 #32770）。"""
    out = []

    def cb(h, _):
        p = wintypes.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value == pid:
            buf = ctypes.create_unicode_buffer(128)
            u.GetClassNameW(h, buf, 128)
            if buf.value == "#32770":
                out.append(h)
        return True

    u.EnumWindows(EnumProc(cb), 0)
    return out


def inspect(h):
    r = wintypes.RECT()
    u.GetWindowRect(h, ctypes.byref(r))
    st = u.GetWindowLongW(h, -16)
    ex = u.GetWindowLongW(h, -20)
    cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
    at = u.GetAncestor(u.WindowFromPoint(wintypes.POINT(cx, cy)), 2) or 0
    return {
        "rect": (r.left, r.top, r.right, r.bottom),
        "size": (r.right - r.left, r.bottom - r.top),
        "size_ok": (r.right - r.left) > 300 and (r.bottom - r.top) > 200,
        "visible": bool(st & 0x10000000),
        "topmost": bool(ex & 0x8),
        "on_top": at == h,
    }


def run(mode):
    tmpd = tempfile.mkdtemp(prefix="testpick_")
    ps1 = os.path.join(tmpd, "picker.ps1")
    out = os.path.join(tmpd, "out.txt")
    with open(ps1, "w", encoding="utf-8-sig") as f:
        f.write(S._PS_PICKER)

    p = subprocess.Popen(
        ["powershell", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-File", ps1,
         "-Mode", mode, "-OutFile", out,
         "-Title", "选择要处理的视频或音频文件", "-Filter", S.MEDIA_FILTER],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=S.CREATE_NO_WINDOW,
    )

    h = 0
    for _ in range(60):
        time.sleep(0.25)
        ws = find_dialogs(p.pid)
        if ws:
            h = ws[0]
            break

    if not h:
        se = b""
        if p.poll() is not None:
            se = p.communicate()[1] or b""
        print("[失败] 15 秒内没有出现对话框窗口")
        if se:
            print("PowerShell stderr:", se.decode("utf-8", "replace")[:600])
        p.kill()
        return 1

    time.sleep(2.0)  # 等置顶完成
    info = inspect(h)

    print("模式     : %s" % mode)
    print("位置尺寸 : %s  宽高 %dx%d" % (info["rect"], info["size"][0], info["size"][1]))
    print("WS_VISIBLE : %s" % info["visible"])
    print("TOPMOST    : %s" % info["topmost"])
    print("尺寸正常   : %s" % info["size_ok"])
    print("中心点最上层就是它 : %s" % info["on_top"])

    u.PostMessageW(h, 0x0010, 0, 0)  # WM_CLOSE = 用户点取消
    p.wait(timeout=15)
    body = open(out, encoding="utf-8-sig").read().strip() if os.path.isfile(out) else "<无结果文件>"

    ok = info["size_ok"] and info["visible"] and info["on_top"] and body == ""
    print("-" * 52)
    print("取消后结果文件 : %r （应为空）" % body)
    print("结论 : %s" % ("通过——对话框真的出现在用户眼前" if ok else "不通过"))
    return 0 if ok else 1


if __name__ == "__main__":
    mode = "folder" if len(sys.argv) > 1 and sys.argv[1] == "folder" else "files"
    sys.exit(run(mode))
