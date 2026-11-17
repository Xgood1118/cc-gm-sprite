# -*- coding: utf-8 -*-
import json
import os
import struct
import subprocess
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import sprite as mod  # noqa: E402
sprite = mod

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRAMES = os.path.join(ROOT, "data", "frames.txt")


def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def read_hex(path):
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def decode_png(data):
    """最简 PNG 解码：只支持本项目使用的 8 位 RGBA、过滤器 0。"""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos = 8
    idat = b""
    width = height = None
    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        tag = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        crc = struct.unpack(">I", data[pos + 8 + length:pos + 12 + length])[0]
        assert crc == zlib.crc32(tag + chunk) & 0xFFFFFFFF
        if tag == b"IHDR":
            width, height, depth, color_type = struct.unpack(">IIBB", chunk[:10])
            assert (depth, color_type) == (8, 6)
        elif tag == b"IDAT":
            idat += chunk
        pos += 12 + length
    raw = zlib.decompress(idat)
    stride = width * 4
    rows = []
    offset = 0
    for _ in range(height):
        assert raw[offset] == 0
        offset += 1
        rows.append(raw[offset:offset + stride])
        offset += stride
    assert offset == len(raw)
    return width, height, rows


def pixel(rows, x, y):
    start = x * 4
    return tuple(rows[y][start:start + 4])


def test_plan_grid():
    cells = sprite.plan_sprite(9, 32, 8)
    assert len(cells) == 9
    assert cells[8].x == 0 and cells[8].y == 32


def test_single_frame():
    cells = sprite.plan_sprite(1, 32, 8)
    assert len(cells) == 1 and cells[0].w == 32


# ---------------------------------------------------------------------------
# 排布稳定与边界
# ---------------------------------------------------------------------------

def test_layout_is_stable_when_a_frame_is_appended():
    for count in (1, 7, 8, 9, 15, 16, 17):
        smaller = sprite.plan_sprite(count, 32, 8)
        larger = sprite.plan_sprite(count + 1, 32, 8)
        assert [(c.x, c.y) for c in larger[:count]] == [(c.x, c.y) for c in smaller]
        assert (larger[-1].x, larger[-1].y) == (
            (count % 8) * 32,
            (count // 8) * 32,
        )


def test_zero_frames_has_deterministic_empty_layout(tmp_path):
    cells = sprite.plan_sprite(0, 32, 8)
    assert cells == []
    assert sprite.sheet_size(cells) == (0, 0)

    out = tmp_path / "empty.json"
    payload = sprite.export_json(cells, str(out))
    assert payload["frame_count"] == 0
    assert payload["cell_count"] == 0
    assert payload["used_cells"] == 0
    assert payload["reused_frames"] == 0
    assert (payload["width"], payload["height"]) == (0, 0)
    assert payload["cells"] == []
    assert payload["frames"] == []
    assert json.loads(out.read_text(encoding="utf-8")) == payload

    assert sprite.pack_cells([], []) == []
    with pytest.raises(sprite.SpriteError):
        sprite.export_png([], str(tmp_path / "empty.png"))


def test_single_frame_layout(tmp_path):
    cells = sprite.plan_sprite(1, 32, 8)
    assert len(cells) == 1
    assert (cells[0].x, cells[0].y, cells[0].w, cells[0].h) == (0, 0, 32, 32)
    assert sprite.sheet_size(cells) == (32, 32)
    payload = sprite.export_json(cells, str(tmp_path / "one.json"))
    assert payload["cols"] == 1 and payload["rows"] == 1
    assert (payload["width"], payload["height"]) == (32, 32)


def test_exactly_full_row():
    cells = sprite.plan_sprite(8, 32, 8)
    assert len(cells) == 8
    assert [(c.x, c.y) for c in cells] == [(i * 32, 0) for i in range(8)]
    assert sprite.sheet_size(cells) == (256, 32)

    one_more = sprite.plan_sprite(9, 32, 8)
    assert (one_more[8].x, one_more[8].y) == (0, 32)
    assert sprite.sheet_size(one_more) == (256, 64)

    two_rows = sprite.plan_sprite(16, 32, 8)
    assert sprite.sheet_size(two_rows) == (256, 64)


def test_sheet_limits_are_explicit():
    # 宽超限：9 列 x 512 = 4608 > 4096
    with pytest.raises(sprite.SpriteError, match="图宽"):
        sprite.plan_sprite(9, 512, 9)
    # 高超限：1024 帧 / 8 列 = 128 行 x 32 = 4096 恰好在上限；再来一帧 131072 > 上限
    sprite.plan_sprite(1024, 32, 8)
    with pytest.raises(sprite.SpriteError, match="图高"):
        sprite.plan_sprite(1025, 32, 8)
    # 非法参数
    with pytest.raises(sprite.SpriteError):
        sprite.plan_sprite(-1, 32, 8)
    with pytest.raises(sprite.SpriteError):
        sprite.plan_sprite(1, 0, 8)
    with pytest.raises(sprite.SpriteError):
        sprite.plan_sprite(1, 32, 0)


# ---------------------------------------------------------------------------
# 复用
# ---------------------------------------------------------------------------

def test_reuse_is_traceable_and_per_frame_lookup_is_stable():
    frames = ["#ff0000", "#00ff00", "#ff0000", "#0000ff", "#f00", "#00ff00"]
    cells = sprite.plan_sprite(len(frames), 16, 4)
    assigned = sprite.pack_cells(frames, cells)

    assert len(assigned) == len(frames)
    assert assigned[0] is assigned[2] is assigned[4]
    assert assigned[1] is assigned[5]
    assert assigned[3] is not assigned[0]

    assert assigned[0].frame == 0
    assert assigned[0].reused_by == (2, 4)
    assert assigned[0].color == "#ff0000"
    assert assigned[3].frame == 3
    assert assigned[3].reused_by == ()
    assert assigned[2].x == assigned[0].x and assigned[2].y == assigned[0].y
    # 复用只占 3 格，剩下的格子未占用
    assert [c.frame for c in cells] == [0, 1, 3, -1, -1, -1]
    assert [c.reused_by for c in cells] == [(2, 4), (5,), (), (), (), ()]

    assert sprite.frame_cell(cells, 4) is cells[0]
    assert sprite.frame_cell(cells, 5) is cells[1]
    with pytest.raises(sprite.SpriteError):
        sprite.frame_cell(cells, 99)


def test_reuse_cannot_exceed_cell_capacity_and_leaves_cells_untouched():
    frames = ["#111111", "#222222", "#333333"]
    cells = sprite.plan_sprite(2, 32, 8)
    before = [(c.x, c.y, c.frame, c.color) for c in cells]
    with pytest.raises(sprite.SpriteError, match="上限"):
        sprite.pack_cells(frames, cells)
    assert [(c.x, c.y, c.frame, c.color) for c in cells] == before


def test_duplicate_frames_fit_within_original_capacity():
    frames = ["#abc"] * 12
    cells = sprite.plan_sprite(12, 32, 8)
    assigned = sprite.pack_cells(frames, cells)
    assert cells[0].reused_by == tuple(range(1, 12))
    assert all(cell is cells[0] for cell in assigned)
    assert [c.frame for c in cells] == [0] + [-1] * 11


# ---------------------------------------------------------------------------
# 逐像素对齐
# ---------------------------------------------------------------------------

def test_every_cell_is_pixel_aligned_in_json_and_png(tmp_path):
    frames = ["#ff0000", "#00ff00", "#0000ff", "#ff0000", "#ffff00"]
    cells = sprite.plan_sprite(len(frames), 16, 2)
    sprite.pack_cells(frames, cells)

    json_path = tmp_path / "sheet.json"
    png_path = tmp_path / "sheet.png"
    payload = sprite.export_json(cells, str(json_path))
    width, height = sprite.export_png(cells, str(png_path))
    assert (width, height) == (payload["width"], payload["height"])

    decoded_w, decoded_h, rows = decode_png(read_bytes(str(png_path)))
    assert (decoded_w, decoded_h) == (width, height)

    # 坐标表里每个已用格子的矩形，在图上必须整块是同一个颜色；
    # 矩形外紧邻的一圈像素必须不是该颜色（或超出画布）。
    for record in payload["frames"]:
        rgba = sprite.color_rgba(record["color"])
        for yy in range(record["y"], record["y"] + record["h"]):
            for xx in range(record["x"], record["x"] + record["w"]):
                assert pixel(rows, xx, yy) == rgba, (record["frame"], xx, yy)
        outside = []
        if record["x"] > 0:
            outside.append((record["x"] - 1, record["y"]))
        if record["x"] + record["w"] < width:
            outside.append((record["x"] + record["w"], record["y"]))
        if record["y"] > 0:
            outside.append((record["x"], record["y"] - 1))
        if record["y"] + record["h"] < height:
            outside.append((record["x"], record["y"] + record["h"]))
        for xx, yy in outside:
            assert pixel(rows, xx, yy) != rgba

    # 未被占用的格子（1 列 x 3 行中的第三行第二列）必须保持透明。
    assert pixel(rows, 16, 32) == (0, 0, 0, 0)


def test_cell_rectangles_do_not_overlap_and_fit_sheet():
    cells = sprite.plan_sprite(21, 8, 5)
    width, height = sprite.sheet_size(cells)
    for cell in cells:
        assert 0 <= cell.x and 0 <= cell.y
        assert cell.x + cell.w <= width and cell.y + cell.h <= height
    seen = set()
    for cell in cells:
        for yy in range(cell.y, cell.y + cell.h):
            for xx in range(cell.x, cell.x + cell.w):
                assert (xx, yy) not in seen
                seen.add((xx, yy))


# ---------------------------------------------------------------------------
# 数据读取与确定性
# ---------------------------------------------------------------------------

def test_frames_file_is_unchanged_and_loads_nine_frames():
    assert read_hex(FRAMES) == "9"
    frames = sprite.load_frames(FRAMES)
    assert len(frames) == 9
    assert frames[0] != frames[1]  # 占位色盘循环，前两帧不同色
    assert frames[0] == frames[4]  # 9 帧 4 色 => 第 5 帧复用第 1 帧


def test_export_json_is_byte_identical_across_runs(tmp_path):
    frames = sprite.load_frames(FRAMES)
    cells = sprite.plan_sprite(len(frames), 32, 8)
    sprite.pack_cells(frames, cells)

    first = tmp_path / "a.json"
    second = tmp_path / "b.json"
    sprite.export_json(cells, str(first))
    sprite.export_json(cells, str(second))
    assert read_bytes(str(first)) == read_bytes(str(second))

    png_a, png_b = tmp_path / "a.png", tmp_path / "b.png"
    sprite.export_png(cells, str(png_a))
    sprite.export_png(cells, str(png_b))
    assert read_bytes(str(png_a)) == read_bytes(str(png_b))


def test_cli_output_is_byte_identical_across_processes(tmp_path):
    outs = []
    for run in (1, 2):
        out = tmp_path / ("sheet%d.json" % run)
        png = tmp_path / ("sheet%d.png" % run)
        proc = subprocess.run(
            [sys.executable, "-m", "sprite.cli", FRAMES,
             "--cell", "32", "--cols", "8", "--out", str(out),
             "--png", str(png)],
            cwd=ROOT, capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        outs.append((read_bytes(str(out)), read_bytes(str(png))))

    assert outs[0][0] == outs[1][0]
    assert outs[0][1] == outs[1][1]

    payload = json.loads(outs[0][0].decode("utf-8"))
    assert payload["frame_count"] == 9
    assert payload["cell_count"] == 9
    assert payload["used_cells"] == 4
    assert payload["reused_frames"] == 5
    assert (payload["width"], payload["height"]) == (256, 64)
    assert [(f["x"], f["y"], f["w"], f["h"]) for f in payload["frames"]] == [
        (0, 0, 32, 32), (32, 0, 32, 32), (64, 0, 32, 32), (96, 0, 32, 32),
        (0, 0, 32, 32), (32, 0, 32, 32), (64, 0, 32, 32), (96, 0, 32, 32),
        (0, 0, 32, 32),
    ]
    reused = [f["frame"] for f in payload["frames"] if f["reused"]]
    assert reused == [4, 5, 6, 7, 8]
    assert [f["reuse_of"] for f in payload["frames"] if f["reused"]] == [0, 1, 2, 3, 0]
    assert [f["cell"] for f in payload["frames"]] == [0, 1, 2, 3, 0, 1, 2, 3, 0]


def test_cli_reports_errors_without_traceback(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "sprite.cli", FRAMES,
         "--cell", "512", "--cols", "9", "--out", str(tmp_path / "x.json")],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert proc.returncode == 1
    assert "图宽" in proc.stderr
    assert "Traceback" not in proc.stderr


def test_load_frames_rejects_malformed_input(tmp_path):
    too_many = tmp_path / "many.txt"
    too_many.write_text("1\n#ff0000\n#00ff00\n", encoding="utf-8")
    with pytest.raises(sprite.SpriteError, match="多于帧数"):
        sprite.load_frames(str(too_many))

    bad_color = tmp_path / "bad.txt"
    bad_color.write_text("1\nnot-a-color\n", encoding="utf-8")
    with pytest.raises(sprite.SpriteError, match="颜色"):
        sprite.load_frames(str(bad_color))

    empty = tmp_path / "empty.txt"
    empty.write_text("\n# only comment\n", encoding="utf-8")
    with pytest.raises(sprite.SpriteError, match="帧数"):
        sprite.load_frames(str(empty))


def test_load_frames_accepts_colors_comments_and_zero(tmp_path):
    path = tmp_path / "frames.txt"
    path.write_text("# 帧清单\n0\n", encoding="utf-8")
    assert sprite.load_frames(str(path)) == []

    path.write_text("3\n#f00\n00ff00\n# 注释\n0000FF\n", encoding="utf-8")
    assert sprite.load_frames(str(path)) == ["#ff0000", "#00ff00", "#0000ff"]

    with pytest.raises(sprite.SpriteError, match="帧数不能为负"):
        path.write_text("-1\n", encoding="utf-8")
        sprite.load_frames(str(path))
