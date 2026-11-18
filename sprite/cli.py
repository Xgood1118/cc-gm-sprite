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


def _ratio(text):
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError("需要小数，实际是 %r" % text)
    if not 0.0 <= value <= 1.0:
        raise argparse.ArgumentTypeError("必须落在 0..1，实际是 %r" % value)
    return value


def _max_sheet(text):
    if "x" not in text:
        raise argparse.ArgumentTypeError("格式应为 宽x高，例如 64x64")
    width_text, height_text = text.split("x", 1)
    return (_positive(width_text), _positive(height_text))


def _parser():
    parser = argparse.ArgumentParser(description="缩略图雪碧图与坐标表")
    parser.add_argument("frames", help="帧清单（data/frames.txt）")
    parser.add_argument("--cell", type=_positive, default=32,
                        help="v1 单元格边长，默认 32")
    parser.add_argument("--cols", type=_positive, default=8,
                        help="每行列数（槽位列数），默认 8")
    parser.add_argument("--out", required=True, help="v1 坐标表 JSON 输出路径")
    parser.add_argument("--png", help="可选：同时输出 v1 雪碧图 PNG")
    parser.add_argument("--max-w", type=_positive, default=m.MAX_SHEET_WIDTH,
                        help="v1 图宽上限，默认 %d" % m.MAX_SHEET_WIDTH)
    parser.add_argument("--max-h", type=_positive, default=m.MAX_SHEET_HEIGHT,
                        help="v1 图高上限，默认 %d" % m.MAX_SHEET_HEIGHT)
    parser.add_argument("--pack-json", help="可选：v2 旋转/裁剪打包结果 JSON 路径")
    parser.add_argument("--pack-png", help="可选：v2 打包雪碧图 PNG 路径")
    parser.add_argument("--cell-w", type=_positive, default=16,
                        help="v2 槽位宽，默认 16")
    parser.add_argument("--cell-h", type=_positive, default=16,
                        help="v2 槽位高，默认 16")
    parser.add_argument("--spacing", type=int, default=1,
                        help="v2 槽位最小间距，默认 1")
    parser.add_argument("--max-sheet", type=_max_sheet,
                        help="v2 整图上限，格式 宽x高")
    parser.add_argument("--min-utilization", type=_ratio, default=0.0,
                        help="v2 利用率阈值（0..1），默认 0 即不检查")
    return parser


def _demo_pixel_frames(colors):
    """由占位色生成确定性像素帧：每帧带不同宽度的全透明边与实心内容。

    仅用于 CLI 演示 v2 流水线（trim/旋转/约束），不读取也不修改
    ``data/frames.txt`` 的内容。
    """
    frames = []
    rotations = []
    for index, color in enumerate(colors):
        rgba = m.color_rgba(color)
        pad = 1 + index % 3                      # 1..3 的透明边
        inner = 8 + index % 2 * 2                # 8 或 10 的实心方块
        size = inner + 2 * pad
        pixels = [[(0, 0, 0, 0) for _ in range(size)] for _ in range(size)]
        for y in range(pad, pad + inner):
            for x in range(pad, pad + inner):
                pixels[y][x] = rgba
        frames.append(pixels)
        rotations.append((index % 4) * 90)      # 0/90/180/270 循环
    return frames, rotations


def main(argv=None):
    parser = _parser()
    args = parser.parse_args(argv)

    try:
        frames = m.load_frames(args.frames)

        # ---- v1：固定网格 + 同色复用 + 确定性坐标表 ----
        cells = m.plan_sprite(
            len(frames), args.cell, args.cols,
            max_width=args.max_w, max_height=args.max_h,
        )
        if frames:
            m.pack_cells(frames, cells)
        payload = m.export_json(cells, args.out)
        if args.png:
            m.export_png(cells, args.png)

        # ---- v2：trim + 旋转 + 约束打包（可选演示路径） ----
        pack_payload = None
        if args.pack_json or args.pack_png:
            pixel_frames, rotations = _demo_pixel_frames(frames)
            result = m.pack_rotated(
                pixel_frames, args.cols, args.cell_w, args.cell_h,
                rotations=rotations, max_sheet=args.max_sheet,
                spacing=args.spacing,
                min_utilization=args.min_utilization,
            )
            if not result["ok"]:
                print("v2 打包不可行：%s" % result["reason"], file=sys.stderr)
                return 2
            if args.pack_json:
                pack_payload = m.export_pack_json(result, args.pack_json)
            if args.pack_png:
                m.export_pack_png(result, args.pack_png)
    except (m.SpriteError, OSError) as exc:
        print("错误：%s" % exc, file=sys.stderr)
        return 1

    print(
        "[v1] 格数 %d（已用 %d，复用 %d 帧），图 %dx%d，%d 行 x %d 列"
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
    print("[v1] 坐标表：%s" % args.out)
    if args.png:
        print("[v1] 雪碧图：%s" % args.png)

    if pack_payload is not None:
        print(
            "[v2] 帧 %d，图 %dx%d，利用率 %.4f，旋转角 %s"
            % (
                len(pack_payload["cells"]),
                pack_payload["width"],
                pack_payload["height"],
                pack_payload["utilization"],
                pack_payload["rotations"],
            )
        )
        for cell in pack_payload["cells"]:
            print(
                "  帧 %2d -> 槽 @ (%d, %d, %d, %d) 旋转 %3d 裁边偏移 (%d, %d)"
                % (
                    cell["frame"], cell["x"], cell["y"], cell["w"], cell["h"],
                    cell["rotation"], cell["ox"], cell["oy"],
                )
            )
        if args.pack_json:
            print("[v2] 打包结果：%s" % args.pack_json)
        if args.pack_png:
            print("[v2] 雪碧图：%s" % args.pack_png)
    return 0


if __name__ == "__main__":
    sys.exit(main())
