"""模拟 exe 运行环境：内置 ffmpeg 7.1 + 无 ffprobe（走 stderr 回退解析）。

源码运行时用的是系统 ffmpeg 9.0.2，和打包后不是同一个二进制，必须单独验一遍。
"""
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import server as S  # noqa: E402

BUILTIN = (r"C:\Users\yujiayi\.workbuddy\binaries\python\envs\default"
           r"\Lib\site-packages\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe")
SYS_FFPROBE = shutil.which("ffprobe")

if not os.path.isfile(BUILTIN):
    print("找不到内置 ffmpeg，跳过")
    sys.exit(0)

# 切到 exe 环境
S.FFMPEG = BUILTIN
S.FFPROBE = None
print("内置 ffmpeg:", os.path.basename(BUILTIN))
r = subprocess.run([BUILTIN, "-hide_banner", "-version"], capture_output=True)
print("版本:", (r.stdout or b"").decode().splitlines()[0])
print("ffprobe: 无（模拟纯 exe 环境）")
print()

DL = os.path.join(ROOT, "downloads")
SDR = os.path.join(DL, "_bi_sdr.mp4")
HDR = os.path.join(DL, "_bi_hdr.mp4")

subprocess.run([BUILTIN, "-y", "-v", "error", "-f", "lavfi",
                "-i", "testsrc2=size=640x360:rate=25:duration=3",
                "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", SDR],
               capture_output=True)
subprocess.run([BUILTIN, "-y", "-v", "error", "-f", "lavfi",
                "-i", "testsrc2=size=640x360:rate=25:duration=3",
                "-c:v", "libx265", "-pix_fmt", "yuv420p10le", "-crf", "18",
                "-x265-params", "colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc",
                HDR], capture_output=True)

for name, p in (("SDR", SDR), ("HDR", HDR)):
    info = S.probe_video(p)
    print("[%s] 回退探测: %s" % (name, json.dumps({
        "w": info and info.get("w"), "h": info and info.get("h"),
        "pix": info and info.get("pix_fmt"),
        "cs": info and info.get("color_space"),
        "trc": info and info.get("color_transfer"),
        "prim": info and info.get("color_primaries"),
        "dur": info and info.get("duration"),
    }, ensure_ascii=False)))
    print("     is_hdr=%s  滤镜=%s" % (
        S.is_hdr(info), ",".join(S.grab_filter_chain(info, S.is_hdr(info), "light")) or "（无）"))

print()
for name, p in (("SDR", SDR), ("HDR", HDR)):
    out = os.path.join(DL, "_bi_%s_out.mp4" % name.lower())
    t = S.create_task("", {})
    t["status"] = "processing"
    t0 = time.time()
    ok = S.process_upscale(t, p, out, {
        "scale": 2, "model": "realesr-animevideov3",
        "denoise": "light", "sharpen": "medium", "quality": "high"})
    print("[%s] process_upscale: %s  耗时 %.1fs" % (
        name, "成功" if ok else "失败", time.time() - t0))

    if ok and SYS_FFPROBE:
        r = subprocess.run(
            [SYS_FFPROBE, "-v", "error", "-select_streams", "v:0",
             "-show_entries",
             "stream=width,height,pix_fmt,color_space,color_transfer,color_primaries,nb_frames",
             "-of", "json", out], capture_output=True)
        st = (json.loads((r.stdout or b"{}").decode()) or {}).get("streams", [{}])[0]
        print("     输出: %s" % json.dumps(st, ensure_ascii=False))

for f in (SDR, HDR, os.path.join(DL, "_bi_sdr_out.mp4"), os.path.join(DL, "_bi_hdr_out.mp4")):
    try:
        os.remove(f)
    except OSError:
        pass
