"""画质管线参数自检：probe 字段、HDR 判断、滤镜链、色彩标记。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import server as S  # noqa: E402

DL = os.path.join(ROOT, "downloads")
files = [
    "mov_bbb [mov_bbb].mp4",
    "“八千里路明月照” [BV1djHw6REx5].mp4",
    "【官方 MV】Never Gonna Give You Up - Rick Astley [BV1GJ411x7h7].mp4",
]

for name in files:
    p = os.path.join(DL, name)
    if not os.path.isfile(p):
        print("跳过（不存在）:", name)
        continue
    info = S.probe_video(p)
    print("=" * 60)
    print(name)
    if not info:
        print("  probe 失败")
        continue
    print("  尺寸/时长: %dx%d %.2fs" % (info["w"], info["h"], info.get("duration") or 0))
    print("  codec/pix: %s / %s" % (info.get("codec"), info.get("pix_fmt")))
    print("  色彩: space=%s trc=%s prim=%s range=%s" % (
        info.get("color_space"), info.get("color_transfer"),
        info.get("color_primaries"), info.get("color_range")))
    hdr = S.is_hdr(info)
    print("  HDR: %s" % hdr)
    print("  输出色彩参数: %s" % " ".join(S.out_color_args(info, hdr)))
    for dn in ("off", "medium"):
        chain = S.grab_filter_chain(info, hdr, dn)
        print("  抽帧滤镜[%s]: %s" % (dn, ",".join(chain) if chain else "（无）"))
    print("  降噪参数: %s" % S.DENOISE_PRESET)
    print("  锐化参数: %s" % S.SHARPEN_PRESET)
    print("  空间估算(900帧): %.2f GB" % (
        S.upscale_space_estimate(info["w"], info["h"], 2, 900) / 1073741824.0))
