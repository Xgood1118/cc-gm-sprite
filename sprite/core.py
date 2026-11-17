# -*- coding: utf-8 -*-
"""缩略图雪碧图与坐标表。

仅使用 Python 标准库（``dataclasses`` / ``json`` / ``re`` / ``struct`` /
``zlib``），无第三方依赖。

公开接口
--------
``Cell``                         一个格子（索引、矩形、占用帧、颜色、复用列表）
``plan_sprite(count, cell, cols)`` 按固定网格排出 ``count`` 个格子
``pack_cells(frames, cells)``     把帧按颜色分配到格子，返回“帧号 -> Cell”
``export_json(cells, path)``      输出确定性坐标表

辅助接口
--------
``load_frames(path)``            读取 ``frames.txt``
``render_sheet(cells)``          生成 RGBA 像素缓冲
``export_png(cells, path)``      输出确定性 PNG
``sheet_size(cells)``            返回整图宽高
``frame_cell(cells, frame)``     按帧号反查格子
``SpriteError``                  所有校验失败的异常（``ValueError`` 子类）

排布规则
--------
第 ``i`` 帧的格子恒为 ``(i % cols, i // cols)``，格子边长 ``cell``，因此
追加帧只会在末尾新增格子，已有格子的坐标不会移动。整图宽高只由帧数、
``cell`` 与 ``cols`` 决定，不存在二次排版。
"""

import json
import re
import struct
import zlib
from dataclasses import dataclass, field

__all__ = [
    "Cell",
    "SpriteError",
    "MAX_SHEET_WIDTH",
    "MAX_SHEET_HEIGHT",
    "PLACEHOLDER_PALETTE",
    "plan_sprite",
    "pack_cells",
    "export_json",
    "load_frames",
    "placeholder_color",
    "normalize_color",
    "color_rgba",
    "sheet_size",
    "render_sheet",
    "export_png",
    "frame_cell",
]

#: 整图宽 / 高上限（像素）。超限时抛 :class:`SpriteError`，不做静默改写。
MAX_SHEET_WIDTH = 4096
MAX_SHEET_HEIGHT = 4096

#: ``frames.txt`` 未给颜色的帧按此色盘（按帧号循环）取占位色。
PLACEHOLDER_PALETTE = ("#e4572e", "#17bebb", "#ffc914", "#2e282a")

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


class SpriteError(ValueError):
    """排布、打包、读取或导出失败。"""


@dataclass
class Cell:
    """一个格子。

    前六个字段保持骨架中的顺序与默认值；``color`` / ``reused_by`` 是打包后
    追加的可选元数据。``frame == -1`` 表示该格未被占用。
    """

    index: int
    x: int
    y: int
    w: int
    h: int
    frame: int = -1
    color: str = ""
    reused_by: tuple = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# 参数校验与颜色归一化
# ---------------------------------------------------------------------------

def _require_int(name, value, minimum):
    if isinstance(value, bool) or not isinstance(value, int):
        raise SpriteError("%s 必须是整数，实际是 %r" % (name, value))
    if value < minimum:
        raise SpriteError("%s 必须 >= %d，实际是 %d" % (name, minimum, value))


def normalize_color(value):
    """把颜色归一化成小写 ``#rrggbb``。

    支持 ``"#RGB"`` / ``"RGB"`` / ``"#RRGGBB"`` / ``"RRGGBB"``、整数
    ``0xRRGGBB`` 与 ``(r, g, b)`` 三元组。
    """
    if isinstance(value, str):
        match = _HEX_RE.match(value.strip())
        if not match:
            raise SpriteError("无法识别的颜色 %r（应为 RGB / RRGGBB 十六进制）" % value)
        digits = match.group(1).lower()
        if len(digits) == 3:
            digits = "".join(ch * 2 for ch in digits)
        return "#" + digits
    if isinstance(value, int) and not isinstance(value, bool):
        if not 0 <= value <= 0xFFFFFF:
            raise SpriteError("颜色整数必须落在 0..0xFFFFFF，实际是 %r" % value)
        return "#%06x" % value
    if isinstance(value, (tuple, list)) and len(value) == 3:
        channels = []
        for channel in value:
            if isinstance(channel, bool) or not isinstance(channel, int):
                raise SpriteError("RGB 分量必须是整数，实际是 %r" % (value,))
            if not 0 <= channel <= 255:
                raise SpriteError("RGB 分量必须落在 0..255，实际是 %r" % (value,))
            channels.append(channel)
        return "#%02x%02x%02x" % tuple(channels)
    raise SpriteError("无法识别的颜色 %r" % (value,))


def color_rgba(color):
    value = normalize_color(color)
    return (
        int(value[1:3], 16),
        int(value[3:5], 16),
        int(value[5:7], 16),
        255,
    )


# ---------------------------------------------------------------------------
# 排布
# ---------------------------------------------------------------------------

def plan_sprite(count, cell, cols):
    """排出格子位置。

    第 ``i`` 个格子位于 ``((i % cols) * cell, (i // cols) * cell)``，大小为
    ``cell x cell``。返回长度为 ``count`` 的列表；``count == 0`` 时返回 ``[]``。

    整图尺寸为 ``min(cols, count) * cell`` 宽、``ceil(count / cols) * cell`` 高。
    宽或高超过 :data:`MAX_SHEET_WIDTH` / :data:`MAX_SHEET_HEIGHT` 时抛
    :class:`SpriteError`；不会自动改变 ``cols``，因为那会让已有帧错位。
    """
    _require_int("count", count, 0)
    _require_int("cell", cell, 1)
    _require_int("cols", cols, 1)

    if count == 0:
        return []

    used_cols = min(cols, count)
    rows = (count + cols - 1) // cols
    width = used_cols * cell
    height = rows * cell

    if width > MAX_SHEET_WIDTH:
        raise SpriteError(
            "图宽 %d x %d 超过上限 %d：请减小 --cell（当前 %d）或 --cols（当前 %d）"
            % (width, height, MAX_SHEET_WIDTH, cell, cols)
        )
    if height > MAX_SHEET_HEIGHT:
        max_frames = (MAX_SHEET_HEIGHT // cell) * cols
        raise SpriteError(
            "图高 %d x %d 超过上限 %d：cols=%d、cell=%d 时最多容纳 %d 帧；"
            "请增大 --cols 或减小 --cell / 帧数"
            % (width, height, MAX_SHEET_HEIGHT, cols, cell, max_frames)
        )

    cells = []
    for index in range(count):
        col = index % cols
        row = index // cols
        cells.append(Cell(index, col * cell, row * cell, cell, cell))
    return cells


def sheet_size(cells):
    """返回整图 ``(宽, 高)``；空表返回 ``(0, 0)``。"""
    cells = _unique_cells(cells)
    if not cells:
        return (0, 0)
    width = max(c.x + c.w for c in cells)
    height = max(c.y + c.h for c in cells)
    return (width, height)


# ---------------------------------------------------------------------------
# 复用打包
# ---------------------------------------------------------------------------

def pack_cells(frames, cells):
    """把帧按颜色分配到格子。

    ``frames`` 是每帧的颜色（见 :func:`normalize_color`）。颜色相同的帧复用
    第一帧所在的格子。新颜色按顺序占用下一个空格；格子总数上限就是
    ``len(cells)``，去重后所需格数超过上限时抛 :class:`SpriteError`，且不会
    留下半打包状态。

    返回长度为 ``len(frames)`` 的列表：``result[frame]`` 就是该帧对应的
    :class:`Cell`（复用帧返回与首帧相同的对象），调用方按帧号取值不受复用
    影响。``cells`` 本身被就地写入 ``frame`` / ``color`` / ``reused_by``。
    """
    if not isinstance(cells, list):
        raise SpriteError("cells 必须是 plan_sprite 返回的可变列表")
    frames = list(frames)
    capacity = len(cells)

    colors = []
    for frame_index, value in enumerate(frames):
        try:
            colors.append(normalize_color(value))
        except SpriteError as exc:
            raise SpriteError("帧 %d：%s" % (frame_index, exc))

    # 先完整计算分配方案，确认放得下再改 cells，保证失败时状态不变。
    owner = {}
    assigned = []          # [(frame_index, cell_index)]
    reused = {}            # cell_index -> [frame_index, ...]
    next_free = 0
    for frame_index, color in enumerate(colors):
        cell_index = owner.get(color)
        if cell_index is None:
            if next_free >= capacity:
                raise SpriteError(
                    "颜色去重后需要 %d 个格子，超过总格数上限 %d"
                    % (next_free + 1, capacity)
                )
            cell_index = next_free
            owner[color] = cell_index
            reused[cell_index] = []
            next_free += 1
        else:
            reused[cell_index].append(frame_index)
        assigned.append((frame_index, cell_index))

    for cell in cells:
        cell.frame = -1
        cell.color = ""
        cell.reused_by = ()

    first_frame = {}
    for frame_index, cell_index in assigned:
        first_frame.setdefault(cell_index, frame_index)
    for cell_index, frame_index in first_frame.items():
        cells[cell_index].frame = frame_index
        cells[cell_index].color = colors[frame_index]
    for cell_index, frame_indices in reused.items():
        cells[cell_index].reused_by = tuple(frame_indices)

    return [cells[cell_index] for _, cell_index in assigned]


def frame_cell(cells, frame):
    """按帧号反查格子；帧号不存在时抛 :class:`SpriteError`。"""
    _require_int("frame", frame, 0)
    for cell in cells:
        if cell.frame == frame or frame in cell.reused_by:
            return cell
    raise SpriteError("帧号 %d 不在坐标表中" % frame)


# ---------------------------------------------------------------------------
# 坐标表
# ---------------------------------------------------------------------------

def _unique_cells(cells):
    """按 ``index`` 去重并保持首次出现顺序。

    ``export_json`` 既接受 ``plan_sprite`` 的格表，也接受 ``pack_cells``
    返回的“帧号 -> Cell”列表；后者含有重复引用，这里统一成格表。
    """
    unique = []
    seen = {}
    for cell in cells:
        previous = seen.get(cell.index)
        if previous is None:
            seen[cell.index] = cell
            unique.append(cell)
            continue
        if (previous.x, previous.y, previous.w, previous.h) != (
            cell.x, cell.y, cell.w, cell.h
        ):
            raise SpriteError("格子 %d 出现两个不同位置" % cell.index)
    return unique


def _build_payload(cells):
    cells = sorted(_unique_cells(cells), key=lambda c: c.index)
    width, height = sheet_size(cells)
    cell_size = cells[0].w if cells else 0
    cols = sum(1 for c in cells if c.y == 0) if cells else 0
    rows = len({c.y for c in cells}) if cells else 0

    cell_records = []
    frame_records = {}
    used_cells = 0
    for cell in cells:
        if cell.frame >= 0:
            used_cells += 1
            if cell.frame in frame_records:
                raise SpriteError("帧 %d 被多个格子占用" % cell.frame)
            frame_records[cell.frame] = {
                "frame": cell.frame,
                "cell": cell.index,
                "x": cell.x,
                "y": cell.y,
                "w": cell.w,
                "h": cell.h,
                "color": cell.color,
                "reused": False,
                "reuse_of": None,
            }
        for reused_frame in cell.reused_by:
            if reused_frame in frame_records:
                raise SpriteError("帧 %d 被重复记录" % reused_frame)
            if cell.frame < 0:
                raise SpriteError("格子 %d 有复用帧但没有首帧" % cell.index)
            frame_records[reused_frame] = {
                "frame": reused_frame,
                "cell": cell.index,
                "x": cell.x,
                "y": cell.y,
                "w": cell.w,
                "h": cell.h,
                "color": cell.color,
                "reused": True,
                "reuse_of": cell.frame,
            }
        cell_records.append({
            "index": cell.index,
            "x": cell.x,
            "y": cell.y,
            "w": cell.w,
            "h": cell.h,
            "frame": cell.frame,
            "color": cell.color,
            "reused_by": list(cell.reused_by),
        })

    frame_count = max(frame_records) + 1 if frame_records else 0
    missing = [i for i in range(frame_count) if i not in frame_records]
    if missing:
        raise SpriteError("坐标表缺少帧 %s" % (missing,))

    frames = [frame_records[i] for i in range(frame_count)]
    payload = {
        "cell_size": cell_size,
        "cols": cols,
        "rows": rows,
        "width": width,
        "height": height,
        "frame_count": frame_count,
        "cell_count": len(cells),
        "used_cells": used_cells,
        "reused_frames": frame_count - used_cells,
        "cells": cell_records,
        "frames": frames,
    }
    return payload


def export_json(cells, path):
    """输出坐标表并返回该字典。

    字段顺序、缩进固定，写入 UTF-8（``\\n`` 换行），不含时间戳；同一输入
    两次运行逐字节一致。0 帧时输出宽高为 0 的合法空表。
    """
    payload = _build_payload(cells)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return payload


# ---------------------------------------------------------------------------
# 像素渲染与 PNG
# ---------------------------------------------------------------------------

def render_sheet(cells):
    """生成整图 RGBA 缓冲，返回 ``(宽, 高, bytes)``。

    每个已占用格的矩形被填成该格颜色；未占用格保持透明。像素坐标严格取自
    ``Cell.x/y/w/h``，保证与坐标表逐像素对齐。
    """
    cells = _unique_cells(cells)
    width, height = sheet_size(cells)
    if width <= 0 or height <= 0:
        return (width, height, b"")
    stride = width * 4
    buffer = bytearray(stride * height)  # 全 0，即透明背景
    for cell in cells:
        if cell.frame < 0:
            continue
        if cell.x < 0 or cell.y < 0 or cell.x + cell.w > width or cell.y + cell.h > height:
            raise SpriteError("格子 %d 越出整图范围" % cell.index)
        pixel = bytes(color_rgba(cell.color))
        span = pixel * cell.w
        for row in range(cell.y, cell.y + cell.h):
            start = row * stride + cell.x * 4
            buffer[start:start + len(span)] = span
    return (width, height, bytes(buffer))


def _png_chunk(tag, data):
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def export_png(cells, path):
    """输出 RGBA PNG 并返回 ``(宽, 高)``。

    PNG 手写编码（zlib + CRC），固定压缩级别、无时间戳等可变块，同一输入
    逐字节一致。0 帧没有可输出的图，抛 :class:`SpriteError`。
    """
    width, height, pixels = render_sheet(cells)
    if width <= 0 or height <= 0:
        raise SpriteError("0 帧没有可输出的雪碧图")
    stride = width * 4
    raw = bytearray()
    for row in range(height):
        raw.append(0)  # 过滤器类型 0（None）
        raw.extend(pixels[row * stride:(row + 1) * stride])

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + _png_chunk(b"IEND", b"")
    )
    with open(path, "wb") as handle:
        handle.write(png)
    return (width, height)


# ---------------------------------------------------------------------------
# 数据读取
# ---------------------------------------------------------------------------

def load_frames(path):
    """读取 ``frames.txt``，返回每帧颜色（``#rrggbb`` 列表）。

    格式：

    * 空行忽略；
    * 以 ``#`` 开头、但内容不是合法颜色的行是注释（``#ff0000`` 是颜色，
      ``# 第 1 帧`` 是注释）；
    * 第一条有效行是帧数（非负整数）；
    * 其后每行一帧颜色（``RGB`` / ``RRGGBB``，可带 ``#``），按帧号顺序给出，
      可只给前若干帧，不能多于帧数。

    未给出颜色的帧使用 :func:`placeholder_color` 生成的占位色：``data/frames.txt``
    只带帧数、没有像素数据，占位色让“同色帧复用”在纯文本数据上可复现。
    """
    entries = []
    with open(path, "r", encoding="utf-8") as handle:
        for line_no, raw_line in enumerate(handle, 1):
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("#") and not _HEX_RE.match(line):
                continue  # 注释
            entries.append((line_no, line))

    if not entries:
        raise SpriteError("%s: 缺少帧数行" % path)

    first_no, first_line = entries[0]
    try:
        count = int(first_line)
    except ValueError:
        raise SpriteError("%s:%d: 首行应为帧数，实际是 %r" % (path, first_no, first_line))
    if count < 0:
        raise SpriteError("%s:%d: 帧数不能为负" % (path, first_no))

    colors = []
    for line_no, token in entries[1:]:
        if len(colors) >= count:
            raise SpriteError("%s:%d: 颜色行多于帧数 %d" % (path, line_no, count))
        try:
            colors.append(normalize_color(token))
        except SpriteError as exc:
            raise SpriteError("%s:%d: %s" % (path, line_no, exc))

    while len(colors) < count:
        colors.append(placeholder_color(len(colors)))
    return colors


def placeholder_color(frame_index):
    """第 ``frame_index`` 帧的占位色，按 :data:`PLACEHOLDER_PALETTE` 循环取色。

    循环长度固定为 4，因此帧号 ``i`` 与 ``i + 4k`` 同色，会复用同一格。
    """
    _require_int("frame_index", frame_index, 0)
    return PLACEHOLDER_PALETTE[frame_index % len(PLACEHOLDER_PALETTE)]
