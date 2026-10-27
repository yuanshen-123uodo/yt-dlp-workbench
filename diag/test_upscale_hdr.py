"""HDR 路径验证：PQ 源应被识别并 tonemap 成 BT.709 SDR，不能变黑或变灰。"""
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import server as S  # noqa: E402

SRC = os.path.join(ROOT, "downloads", "_hdr_src.mp4")
DST = os.path.join(ROOT, "downloads", "_hdr_out.mp4")

info = S.probe_video(SRC)
print("源信息:", json.dumps({
    "w": info["w"], "h": info["h"], "pix": info.get("pix_fmt"),
    "trc": info.get("color_transfer"), "prim": info.get("color_primaries"),
}, ensure_ascii=False))
print("is_hdr:", S.is_hdr(info))
print("抽帧滤镜:", ",".join(S.grab_filter_chain(info, True, "light")))
print()


def yavg(p):
    # metadata=print 走 info 日志级别，用 -v error 会把输出吞掉
    r = subprocess.run(
        [S.FFMPEG, "-hide_banner", "-v", "info", "-i", p, "-vf",
         "signalstats,metadata=print:key=lavfi.signalstats.YAVG",
         "-frames:v", "1", "-f", "null", "-"],
        capture_output=True,
    )
    out = (r.stdout or b"").decode("utf-8", "replace") + (r.stderr or b"").decode("utf-8", "replace")
    for line in out.splitlines():
        if "YAVG" in line:
            try:
                return float(line.split("=")[-1])
            except ValueError:
                pass
    return None


t = S.create_task("", {})
t["status"] = "processing"
t0 = time.time()
ok = S.process_upscale(t, SRC, DST, {
    "scale": 2, "model": "realesr-animevideov3",
    "denoise": "light", "sharpen": "medium", "quality": "high",
})
print("结果:", "成功" if ok else "失败", "耗时 %.1fs" % (time.time() - t0))

if ok and os.path.isfile(DST):
    r = subprocess.run(
        [S.FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,pix_fmt,color_space,color_transfer,color_primaries,nb_frames",
         "-of", "json", DST], capture_output=True)
    st = (json.loads((r.stdout or b"{}").decode()) or {}).get("streams", [{}])[0]
    print("输出:", json.dumps(st, ensure_ascii=False))
    checks = [
        ("已转 BT.709", st.get("color_space") == "bt709" and st.get("color_primaries") == "bt709"),
        ("传输特性为 SDR(bt709)", st.get("color_transfer") == "bt709"),
        ("输出 8bit", st.get("pix_fmt") == "yuv420p"),
        ("帧数保持", st.get("nb_frames") == "75"),
    ]
    for name, good in checks:
        print("  [%s] %s" % ("OK" if good else "FAIL", name))
    y_src, y_out = yavg(SRC), yavg(DST)
    print("平均亮度 YAVG：源 %.1f → 输出 %.1f" % (y_src or -1, y_out or -1))
    print("  [%s] 亮度未塌陷（不是全黑）" % ("OK" if (y_out or 0) > 16 else "FAIL"))
