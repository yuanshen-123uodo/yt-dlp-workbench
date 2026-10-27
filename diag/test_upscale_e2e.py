"""端到端验证：完整跑一遍 process_upscale，检查滤镜链、色彩标记、音频与帧数。"""
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import server as S  # noqa: E402

SRC = os.path.join(ROOT, "downloads", "mov_bbb [mov_bbb].mp4")
DST = os.path.join(ROOT, "downloads", "_qc_test.mp4")


def probe(p):
    r = subprocess.run(
        [S.FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries",
         "stream=width,height,pix_fmt,color_space,color_transfer,color_primaries,"
         "color_range,nb_frames,r_frame_rate",
         "-show_entries", "format=duration,nb_streams",
         "-of", "json", p],
        capture_output=True,
    )
    d = json.loads((r.stdout or b"{}").decode("utf-8", "replace"))
    st = (d.get("streams") or [{}])[0]
    fm = d.get("format") or {}
    return {
        "w": st.get("width"), "h": st.get("height"),
        "pix": st.get("pix_fmt"),
        "cs": st.get("color_space"), "trc": st.get("color_transfer"),
        "prim": st.get("color_primaries"), "rng": st.get("color_range"),
        "frames": st.get("nb_frames"), "fps": st.get("r_frame_rate"),
        "dur": fm.get("duration"), "streams": fm.get("nb_streams"),
    }


before = probe(SRC)
print("源   :", json.dumps(before, ensure_ascii=False))

t = S.create_task("", {})
t["status"] = "processing"
t0 = time.time()
ok = S.process_upscale(t, SRC, DST, {
    "scale": 2,
    "model": "realesr-animevideov3",
    "denoise": "medium",
    "sharpen": "medium",
    "quality": "high",
})
cost = time.time() - t0
print("结果 :", "成功" if ok else "失败", "耗时 %.1fs" % cost)

if ok and os.path.isfile(DST):
    after = probe(DST)
    print("输出 :", json.dumps(after, ensure_ascii=False))
    print("体积 : %.2f MB -> %.2f MB" % (
        os.path.getsize(SRC) / 1048576.0, os.path.getsize(DST) / 1048576.0))
    print()
    checks = [
        ("分辨率翻倍", after["w"] == before["w"] * 2 and after["h"] == before["h"] * 2),
        ("色彩标记已写入", bool(after["cs"]) and bool(after["prim"]) and bool(after["trc"])),
        ("色彩与源一致(BT.601源)", after["cs"] == "bt470bg"),
        ("音轨保留", (after["streams"] or 0) >= 2),
        ("时长未明显漂移", abs(float(after["dur"]) - float(before["dur"])) < 0.2),
    ]
    for name, good in checks:
        print("  [%s] %s" % ("OK" if good else "FAIL", name))
    print()
    print("音频包数核对：")
    for p in (SRC, DST):
        r = subprocess.run([S.FFPROBE, "-v", "error", "-select_streams", "a:0",
                            "-count_packets", "-show_entries", "stream=nb_read_packets",
                            "-of", "csv=p=0", p], capture_output=True)
        print("   %-40s %s" % (os.path.basename(p), (r.stdout or b"").decode().strip()))
