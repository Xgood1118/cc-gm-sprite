# -*- coding: utf-8 -*-
"""缩略图雪碧图与坐标表。

仅使用 Python 标准库（``dataclasses`` / ``json`` / ``re`` / ``struct`` /
``zlib``），无第三方依赖。

公开接口（骨架保留的名字与签名）
----------------------------------
``Cell``                          一个格子（索引、矩形、占用帧、颜色、复用列表）
``plan_sprite(count, cell, cols)`` 按固定网格排出 ``count`` 个格子
``pack_cells(frames, cells)``      把帧按颜色分配到格子，返回“帧号 -> Cell”
``export_json(cells, path)``       输出确定性坐标表
``trim_frame(pixels)``             裁掉四周全透明像素
``pack_rotated(frames, cols, cell_w, cell_h, ...)`` 带旋转/裁剪/约束的打包
``sheet_extent(cells)``            返回 ``(宽, 高)``
``utilization(cells)``             返回已用面积占整图面积的比例

排布规则
--------
第 ``i`` 帧的格子恒为 ``(i % cols, i // cols)``，格子边长 ``cell``，因此
追加帧只会在末尾新增格子，已有格子的坐标不会移动。整图宽高只由帧数、
``cell`` 与 ``cols`` 决定，不存在二次排版。宽或高超过
:data:`MAX_SHEET_WIDTH` / :data:`MAX_SHEET_HEIGHT` 时抛 :class:`SpriteError`，
不会自动改变 ``cols``——自动换列会让已有帧整体错位。
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
    "trim_frame",
    "pack_rotated",
    "sheet_extent",
    "utilization",
    "load_frames",
    "placeholder_color",
    "normalize_color",
    "color_rgba",
    "rotate_pixels",
    "render_sheet",
    "export_png",
    "render_pack",
    "export_pack_json",
    "export_pack_png",
    "frame_cell",
    "sheet_size",
]

#: 整图宽 / 高上限（像素）。超限时抛 :class:`SpriteError`，不做静默改写。
MAX_SHEET_WIDTH = 4096
MAX_SHEET_HEIGHT = 4096

#: ``frames.txt`` 未给颜色的帧按此色盘（按帧号循环）取占位色。
PLACEHOLDER_PALETTE = ("#e4572e", "#17bebb", "#ffc914", "#2e282a")

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_ROTATIONS = (0, 90, 180, 270)
_TRANSPARENT = (0, 0, 0, 0)


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
    """``#rrggbb`` -> ``(r, g, b, 255)``。"""
    value = normalize_color(color)
    return (
        int(value[1:3], 16),
        int(value[3:5], 16),
        int(value[5:7], 16),
        255,
    )


def placeholder_color(frame_index):
    """第 ``frame_index`` 帧的占位色，按 :data:`PLACEHOLDER_PALETTE` 循环取色。

    循环长度固定为 4，因此帧号 ``i`` 与 ``i + 4k`` 同色，会复用同一格。
    """
    _require_int("frame_index", frame_index, 0)
    return PLACEHOLDER_PALETTE[frame_index % len(PLACEHOLDER_PALETTE)]


# ---------------------------------------------------------------------------
# 排布（v1：固定网格）
# ---------------------------------------------------------------------------

def plan_sprite(count, cell, cols, max_width=MAX_SHEET_WIDTH,
                max_height=MAX_SHEET_HEIGHT):
    """排出格子位置。

    第 ``i`` 个格子位于 ``((i % cols) * cell, (i // cols) * cell)``，大小为
    ``cell x cell``。返回长度为 ``count`` 的列表；``count == 0`` 时返回
    ``[]``。前三个位置参数的签名保持不变；``max_width/max_height`` 是可选
    的整图上限覆盖项。

    整图尺寸为 ``min(cols, count) * cell`` 宽、``ceil(count / cols) * cell``
    高。宽或高超过上限时抛 :class:`SpriteError`；不会自动改变 ``cols``，
    因为那会让已有帧错位。
    """
    _require_int("count", count, 0)
    _require_int("cell", cell, 1)
    _require_int("cols", cols, 1)
    _require_int("max_width", max_width, 1)
    _require_int("max_height", max_height, 1)

    if count == 0:
        return []

    used_cols = min(cols, count)
    rows = (count + cols - 1) // cols
    width = used_cols * cell
    height = rows * cell

    if width > max_width:
        raise SpriteError(
            "图宽 %d 超过上限 %d（cell=%d, cols=%d）：请减小 --cell 或 --cols"
            % (width, max_width, cell, cols)
        )
    if height > max_height:
        max_frames = (max_height // cell) * cols
        raise SpriteError(
            "图高 %d 超过上限 %d（cell=%d, cols=%d）：该参数下最多容纳 %d 帧；"
            "请增大 --cols 或减小 --cell / 帧数"
            % (height, max_height, cell, cols, max_frames)
        )

    cells = []
    for index in range(count):
        col = index % cols
        row = index // cols
        cells.append(Cell(index, col * cell, row * cell, cell, cell))
    return cells


def _cell_rect(cell):
    """统一从 ``Cell`` / dict 取矩形。"""
    if isinstance(cell, dict):
        return cell["x"], cell["y"], cell["w"], cell["h"]
    return cell.x, cell.y, cell.w, cell.h


def sheet_extent(cells):
    """返回 ``(宽, 高)``：所有格子矩形并集的右下角坐标；空表返回 ``(0, 0)``。"""
    width = 0
    height = 0
    for cell in cells:
        x, y, w, h = _cell_rect(cell)
        width = max(width, x + w)
        height = max(height, y + h)
    return (width, height)


#: 别名：v1 坐标表语义下的整图尺寸。
sheet_size = sheet_extent


# ---------------------------------------------------------------------------
# 复用打包（v1：按颜色）
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
    """按帧号反查格子（首帧或复用帧均可）；帧号不存在时抛 :class:`SpriteError`。"""
    _require_int("frame", frame, 0)
    for cell in cells:
        if cell.frame == frame or frame in cell.reused_by:
            return cell
    raise SpriteError("帧号 %d 不在坐标表中" % frame)


# ---------------------------------------------------------------------------
# 坐标表（v1：确定性 JSON）
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
    width, height = sheet_extent(cells)
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
    return {
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
# 像素校验、裁剪与旋转（v2）
# ---------------------------------------------------------------------------

def _validate_pixels(pixels):
    if not isinstance(pixels, (list, tuple)):
        raise SpriteError("像素缓冲必须是二维列表")
    width = None
    for row in pixels:
        if not isinstance(row, (list, tuple)):
            raise SpriteError("像素缓冲必须是二维列表")
        if width is None:
            width = len(row)
        elif len(row) != width:
            raise SpriteError("像素缓冲各行宽度不一致")
        for px in row:
            if not isinstance(px, (list, tuple)) or len(px) != 4:
                raise SpriteError("像素必须是 (r, g, b, a) 四元组")
            for channel in px:
                if isinstance(channel, bool) or not isinstance(channel, int):
                    raise SpriteError("像素分量必须是整数，实际是 %r" % (px,))
                if not 0 <= channel <= 255:
                    raise SpriteError("像素分量必须落在 0..255，实际是 %r" % (px,))


def trim_frame(pixels):
    """裁掉四周全透明（``alpha == 0``）像素。

    返回 ``dict(pixels=裁剪后像素, ox=偏移x, oy=偏移y)``，``ox/oy`` 是裁剪
    内容左上角相对原图的偏移，调用方可据此把内容贴回原位。整帧全透明时
    返回空缓冲（``pixels == []``），偏移为 ``(0, 0)``。
    """
    _validate_pixels(pixels)
    height = len(pixels)
    width = len(pixels[0]) if height else 0

    min_x, min_y = width, height
    max_x = max_y = -1
    for y, row in enumerate(pixels):
        for x, px in enumerate(row):
            if px[3] != 0:
                if x < min_x:
                    min_x = x
                if x > max_x:
                    max_x = x
                if y < min_y:
                    min_y = y
                if y > max_y:
                    max_y = y

    if max_x < 0:
        return {"pixels": [], "ox": 0, "oy": 0}

    cropped = [
        [px for px in row[min_x:max_x + 1]]
        for row in pixels[min_y:max_y + 1]
    ]
    return {"pixels": cropped, "ox": min_x, "oy": min_y}


def rotate_pixels(pixels, rotation):
    """按 90 度倍数顺时针旋转像素缓冲，返回新缓冲。

    90/270 度交换宽高；空缓冲原样返回（仍是新列表）。
    """
    _validate_pixels(pixels)
    rotation = _normalize_rotation(rotation)
    height = len(pixels)
    width = len(pixels[0]) if height else 0
    if height == 0 or width == 0:
        return [list(row) for row in pixels]

    if rotation == 0:
        return [[px for px in row] for row in pixels]
    if rotation == 90:
        # 目标尺寸 w x h：dst[Y][X] = src[h-1-X][Y]
        return [
            [pixels[height - 1 - x][y] for x in range(height)]
            for y in range(width)
        ]
    if rotation == 180:
        return [
            [pixels[height - 1 - y][width - 1 - x] for x in range(width)]
            for y in range(height)
        ]
    # 270 度顺时针（=90 度逆时针），目标尺寸 w x h
    return [
        [pixels[x][width - 1 - y] for x in range(height)]
        for y in range(width)
    ]


def _normalize_rotation(rotation):
    if isinstance(rotation, bool) or not isinstance(rotation, int):
        raise SpriteError("旋转角必须是整数，实际是 %r" % (rotation,))
    if rotation % 90 != 0:
        raise SpriteError("旋转角必须是 90 的倍数，实际是 %d" % rotation)
    return rotation % 360


def _prepare_frame(pixels, rotation):
    """单帧流水线：trim -> 旋转。返回打包所需的全部中间产物。"""
    trimmed = trim_frame(pixels)
    rotated = rotate_pixels(trimmed["pixels"], rotation)
    rect_h = len(rotated)
    rect_w = len(rotated[0]) if rect_h else 0
    return {
        "pixels": rotated,
        "w": rect_w,
        "h": rect_h,
        "ox": trimmed["ox"],
        "oy": trimmed["oy"],
        "src_w": len(pixels[0]) if pixels else 0,
        "src_h": len(pixels),
        "rotation": _normalize_rotation(rotation),
    }


# ---------------------------------------------------------------------------
# 带旋转/裁剪/约束的打包（v2）
# ---------------------------------------------------------------------------

def pack_rotated(frames, cols, cell_w, cell_h, rotations=None, max_sheet=None,
                 spacing=0, min_utilization=0.0):
    """带旋转的打包：返回 ``dict(cells, rotations, ok, reason, ...)``。

    处理流程（每帧）：先 :func:`trim_frame` 裁掉全透明边，再按
    ``rotations[i]``（0/90/180/270，顺时针）旋转，然后把结果矩形放进
    ``cell_w x cell_h`` 的固定槽位网格：第 ``i`` 帧位于
    ``(i % cols, i // cols)`` 号槽位，槽位间距为 ``spacing``，帧在槽位内
    左上角对齐。槽位网格保证帧间不重叠、不越界。

    约束（任一不满足则 ``ok=False`` 且 ``reason`` 说明原因，绝不产出越界
    图）：

    1. 裁剪旋转后的矩形必须放得下 ``cell_w x cell_h``；
    2. 整图尺寸不得超过 ``max_sheet=(最大宽, 最大高)``；
    3. 面积利用率不得低于 ``min_utilization``（0..1）。

    ``cells`` 每项为 ``dict(frame, x, y, w, h, rotation, ox, oy, src_w,
    src_h)``；``rotations`` 是归一化后的每帧旋转角。裁剪只会让矩形变小，
    因此同等输入下裁边方案的图面积不大于不裁边方案。
    """
    _require_int("cols", cols, 1)
    _require_int("cell_w", cell_w, 1)
    _require_int("cell_h", cell_h, 1)
    _require_int("spacing", spacing, 0)
    if isinstance(min_utilization, bool) or not isinstance(min_utilization, (int, float)):
        raise SpriteError("min_utilization 必须是数值，实际是 %r" % (min_utilization,))
    if not 0.0 <= float(min_utilization) <= 1.0:
        raise SpriteError("min_utilization 必须落在 0..1，实际是 %r" % (min_utilization,))
    if max_sheet is not None:
        if (not isinstance(max_sheet, (tuple, list)) or len(max_sheet) != 2
                or any(isinstance(v, bool) or not isinstance(v, int) or v < 1
                       for v in max_sheet)):
            raise SpriteError("max_sheet 必须是 (正整数宽, 正整数高)")
    frames = list(frames)
    if rotations is None:
        rotations = [0] * len(frames)
    rotations = list(rotations)
    if len(rotations) != len(frames):
        raise SpriteError(
            "rotations 长度 %d 与帧数 %d 不一致" % (len(rotations), len(frames))
        )

    def fail(reason):
        return {
            "ok": False,
            "reason": reason,
            "cells": [],
            "rotations": [_normalize_rotation(r) for r in rotations],
            "width": 0,
            "height": 0,
            "utilization": 0.0,
            "cell_pixels": [],
        }

    prepared = []
    for index, (pixels, rotation) in enumerate(zip(frames, rotations)):
        angle = _normalize_rotation(rotation)
        info = _prepare_frame(pixels, angle)
        if info["w"] > cell_w or info["h"] > cell_h:
            return fail(
                "帧 %d 裁剪旋转后尺寸 %dx%d 超过槽位 %dx%d"
                % (index, info["w"], info["h"], cell_w, cell_h)
            )
        prepared.append(info)

    count = len(prepared)
    used_cols = min(cols, count)
    rows = (count + cols - 1) // cols if count else 0
    pitch_x = cell_w + spacing
    pitch_y = cell_h + spacing
    width = used_cols * cell_w + max(0, used_cols - 1) * spacing
    height = rows * cell_h + max(0, rows - 1) * spacing

    if max_sheet is not None and (width > max_sheet[0] or height > max_sheet[1]):
        return fail(
            "需要整图 %dx%d，超过 max_sheet %dx%d（cols=%d, 槽位 %dx%d, 间距 %d, %d 帧）"
            % (width, height, max_sheet[0], max_sheet[1],
               cols, cell_w, cell_h, spacing, count)
        )

    cells = []
    cell_pixels = []
    used_area = 0
    for index, info in enumerate(prepared):
        col = index % cols
        row = index // cols
        x = col * pitch_x
        y = row * pitch_y
        cells.append({
            "frame": index,
            "x": x,
            "y": y,
            "w": info["w"],
            "h": info["h"],
            "rotation": info["rotation"],
            "ox": info["ox"],
            "oy": info["oy"],
            "src_w": info["src_w"],
            "src_h": info["src_h"],
        })
        cell_pixels.append(info["pixels"])
        used_area += info["w"] * info["h"]

    sheet_area = width * height
    ratio = (used_area / sheet_area) if sheet_area else 0.0
    if ratio + 1e-12 < float(min_utilization):
        return fail(
            "利用率 %.4f 低于阈值 %.4f（已用面积 %d / 整图面积 %d）"
            % (ratio, float(min_utilization), used_area, sheet_area)
        )

    return {
        "ok": True,
        "reason": "",
        "cells": cells,
        "rotations": [info["rotation"] for info in prepared],
        "width": width,
        "height": height,
        "utilization": ratio,
        "cell_pixels": cell_pixels,
    }


def utilization(cells):
    """返回已用面积占整图面积的比例；空表返回 ``0.0``。"""
    width, height = sheet_extent(cells)
    sheet_area = width * height
    if sheet_area <= 0:
        return 0.0
    used = 0
    for cell in cells:
        _, _, w, h = _cell_rect(cell)
        used += w * h
    return used / sheet_area


# ---------------------------------------------------------------------------
# 渲染与 PNG（手写编码，确定性输出）
# ---------------------------------------------------------------------------

def render_sheet(cells):
    """生成 v1 整图 RGBA 缓冲，返回 ``(宽, 高, bytes)``。

    每个已占用格的矩形被填成该格颜色；未占用格保持透明。像素坐标严格取自
    ``Cell.x/y/w/h``，保证与坐标表逐像素对齐。
    """
    cells = _unique_cells(cells)
    width, height = sheet_extent(cells)
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


def render_pack(result):
    """把 :func:`pack_rotated` 的结果渲染成二维 RGBA 像素表。

    每帧旋转后的像素按 ``cell.x/y`` 贴到透明画布上；槽位网格保证互不覆盖。
    空帧（全透明被裁空）不贴任何像素。
    """
    if not result.get("ok"):
        raise SpriteError("打包未成功（ok=False），无法渲染：%s" % result.get("reason"))
    width = result["width"]
    height = result["height"]
    canvas = [[_TRANSPARENT for _ in range(width)] for _ in range(height)]
    for cell, pixels in zip(result["cells"], result["cell_pixels"]):
        for y, row in enumerate(pixels):
            for x, px in enumerate(row):
                canvas[cell["y"] + y][cell["x"] + x] = px
    return canvas


def _png_chunk(tag, data):
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def _encode_png(width, height, rgba_bytes):
    stride = width * 4
    raw = bytearray()
    for row in range(height):
        raw.append(0)  # 过滤器类型 0（None）
        raw.extend(rgba_bytes[row * stride:(row + 1) * stride])
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + _png_chunk(b"IEND", b"")
    )


def export_png(cells, path):
    """输出 v1 RGBA PNG 并返回 ``(宽, 高)``。

    固定压缩级别、无时间戳等可变块，同一输入逐字节一致。0 帧没有可输出的
    图，抛 :class:`SpriteError`。
    """
    width, height, pixels = render_sheet(cells)
    if width <= 0 or height <= 0:
        raise SpriteError("0 帧没有可输出的雪碧图")
    png = _encode_png(width, height, pixels)
    with open(path, "wb") as handle:
        handle.write(png)
    return (width, height)


def export_pack_json(result, path):
    """把 :func:`pack_rotated` 结果写成确定性 JSON（含旋转标记），返回字典。"""
    payload = {
        "ok": bool(result.get("ok")),
        "reason": result.get("reason", ""),
        "width": result.get("width", 0),
        "height": result.get("height", 0),
        "utilization": result.get("utilization", 0.0),
        "rotations": list(result.get("rotations", [])),
        "cells": [
            {
                "frame": cell["frame"],
                "x": cell["x"],
                "y": cell["y"],
                "w": cell["w"],
                "h": cell["h"],
                "rotation": cell["rotation"],
                "ox": cell["ox"],
                "oy": cell["oy"],
                "src_w": cell["src_w"],
                "src_h": cell["src_h"],
            }
            for cell in result.get("cells", [])
        ],
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return payload


def export_pack_png(result, path):
    """把 :func:`pack_rotated` 结果渲染并写成确定性 PNG，返回 ``(宽, 高)``。"""
    canvas = render_pack(result)
    height = len(canvas)
    width = len(canvas[0]) if height else 0
    if width <= 0 or height <= 0:
        raise SpriteError("0 帧没有可输出的雪碧图")
    rgba = bytearray()
    for row in canvas:
        for px in row:
            rgba.extend(px)
    png = _encode_png(width, height, bytes(rgba))
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

    未给出颜色的帧使用 :func:`placeholder_color` 生成的占位色：
    ``data/frames.txt`` 只带帧数、没有像素数据，占位色让“同色帧复用”在纯
    文本数据上可复现。
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
