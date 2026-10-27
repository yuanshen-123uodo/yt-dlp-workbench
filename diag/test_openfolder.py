"""验证「打开所在目录」真的跳到了目标目录。

踩过的坑：`explorer /select,路径` 在本机（Win11）会把参数整个丢掉，一律打开「文档」，
用户看到的现象就是「点打开所在目录，跳到的不是它的位置」。所以判据不能是
「Popen 没报错」，必须核对资源管理器窗口标题里是不是目标目录名。

用法：python diag/test_openfolder.py
"""
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import server as S  # noqa: E402

u = ctypes.windll.user32
EP = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def cabinets():
    out = []

    def cb(h, _):
        b = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(h, b, 64)
        if b.value in ("CabinetWClass", "ExploreWClass"):
            t = ctypes.create_unicode_buffer(512)
            u.GetWindowTextW(h, t, 512)
            out.append(t.value or "")
        return True

    u.EnumWindows(EP(cb), 0)
    return out


def close_all():
    def cb(h, _):
        b = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(h, b, 64)
        if b.value in ("CabinetWClass", "ExploreWClass"):
            u.PostMessageW(h, 0x0010, 0, 0)
        return True

    u.EnumWindows(EP(cb), 0)
    time.sleep(1.2)


def wait_titles(want, timeout=5.0):
    """窗口刚创建时标题是空的，要一直等到标题出现再判断，否则会误判成失败。"""
    end = time.time() + timeout
    last = []
    while time.time() < end:
        last = cabinets()
        if any(want.lower() in t.lower() for t in last):
            return last
        time.sleep(0.2)
    return last


def case(label, target, want):
    close_all()
    try:
        S.open_in_explorer(target)
    except Exception as e:
        print("  [FAIL] %-28s 抛异常 %s" % (label, e))
        return False
    titles = wait_titles(want)
    ok = any(want.lower() in t.lower() for t in titles)
    print("  [%s] %-28s -> %s" % ("PASS" if ok else "FAIL", label, titles))
    return ok


def main():
    # 造一个带空格、中文、方括号的目录，尽量贴近真实下载文件名
    work = os.path.join(ROOT, "downloads")
    os.makedirs(work, exist_ok=True)
    folder = os.path.join(work, "_目录 测试[1]")
    os.makedirs(folder, exist_ok=True)
    media = os.path.join(folder, "样片 [test].mp4")
    with open(media, "wb") as f:
        f.write(b"\x00" * 2048)

    print("目标目录:", folder)
    ok = True
    ok &= case("文件 -> 所在目录", media, "_目录 测试[1]")
    ok &= case("目录 -> 目录本身", folder, "_目录 测试[1]")
    ok &= case("文件已删除 -> 退父目录", os.path.join(folder, "gone.mp4"), "_目录 测试[1]")
    ok &= case("下载根目录", work, "downloads")

    # 完全不存在的路径应该报错，而不是静默打开别处
    close_all()
    bad = os.path.join(work, "no_such_dir_xx", "no.mp4")
    try:
        S.open_in_explorer(bad)
        print("  [FAIL] 不存在的路径没有报错")
        ok = False
    except FileNotFoundError:
        print("  [PASS] 不存在的路径正确报错")

    # 清理
    close_all()
    try:
        os.remove(media)
        os.rmdir(folder)
    except OSError:
        pass
    print("\n结果:", "全部通过" if ok else "有失败项")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
