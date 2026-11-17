# -*- coding: utf-8 -*-
"""雪碧图命令行入口。"""

import argparse
import sys

import sprite as m


def _positive(text):
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("需要整数，实际是 %r" % text)
    if value < 1:
        raise argparse.ArgumentTypeError("必须 >= 1，实际是 %d" % value)
    return value


def _parser():
    parser = argparse.ArgumentParser(description="缩略图雪碧图与坐标表")
    parser.add_argument("frames", help="帧清单（data/frames.txt）")
    parser.add_argument("--cell", type=_positive, default=32, help="单元格边长，默认 32")
    parser.add_argument("--cols", type=_positive, default=8, help="每行列数，默认 8")
    parser.add_argument("--out", required=True, help="坐标表 JSON 输出路径")
    parser.add_argument("--png", help="可选：同时输出雪碧图 PNG")
    return parser


def main(argv=None):
    parser = _parser()
    args = parser.parse_args(argv)

    try:
        frames = m.load_frames(args.frames)
        cells = m.plan_sprite(len(frames), args.cell, args.cols)
        if frames:
            m.pack_cells(frames, cells)
        payload = m.export_json(cells, args.out)
        if args.png:
            m.export_png(cells, args.png)
    except (m.SpriteError, OSError) as exc:
        print("错误：%s" % exc, file=sys.stderr)
        return 1

    print(
        "格数 %d（已用 %d，复用 %d 帧），图 %dx%d，%d 行 x %d 列"
        % (
            payload["cell_count"],
            payload["used_cells"],
            payload["reused_frames"],
            payload["width"],
            payload["height"],
            payload["rows"],
            payload["cols"],
        )
    )
    for frame in payload["frames"]:
        origin = "复用帧 %d" % frame["reuse_of"] if frame["reused"] else "首帧"
        print(
            "  帧 %2d -> 格 %2d @ (%d, %d, %d, %d) %s %s"
            % (
                frame["frame"],
                frame["cell"],
                frame["x"],
                frame["y"],
                frame["w"],
                frame["h"],
                frame["color"],
                origin,
            )
        )
    print("坐标表：%s" % args.out)
    if args.png:
        print("雪碧图：%s" % args.png)
    return 0


if __name__ == "__main__":
    sys.exit(main())
