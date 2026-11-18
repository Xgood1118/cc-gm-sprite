# -*- coding: utf-8 -*-
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sprite as mod  # noqa: E402

DATA_FRAMES = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "frames.txt"
)


# ===========================================================================
# 骨架自带用例（保持原样）
# ===========================================================================

def test_plan_grid():
    cells = sprite.plan_sprite(9, 32, 8)
    assert len(cells) == 9
    assert cells[8].x == 0 and cells[8].y == 32


def test_single_frame():
    cells = sprite.plan_sprite(1, 32, 8)
    assert len(cells) == 1 and cells[0].w == 32


# ===== v2 契约测试：初始全部失败，实现后必须通过 =====
import sprite  # noqa: E402


def _frame(w, h, pad=1, color=(255, 0, 0, 255)):
    px = [[(0, 0, 0, 0)] * (w + 2 * pad) for _ in range(h + 2 * pad)]
    for y in range(pad, pad + h):
        for x in range(pad, pad + w):
            px[y][x] = color
    return px


def _cells(r):
    return [c for c in r["cells"]]


def test_trim_removes_border():
    t = sprite.trim_frame(_frame(3, 2, pad=2))
    assert len(t["pixels"]) == 2
    assert len(t["pixels"][0]) == 3
    assert (t["ox"], t["oy"]) == (2, 2)


def test_rotation_marked_in_result():
    r = sprite.pack_rotated([_frame(3, 2)], 1, 8, 8, rotations=[90])
    assert r["ok"] is True
    assert r["rotations"][0] in (0, 90, 180, 270)


def test_cells_no_overlap():
    r = sprite.pack_rotated([_frame(3, 3) for _ in range(4)], 2, 8, 8)
    assert r["ok"] is True
    rects = [(c["x"], c["y"], c["x"] + 8, c["y"] + 8) for c in _cells(r)]
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            a, b = rects[i], rects[j]
            assert not (a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3])


def test_trimmed_not_larger():
    plain = sprite.pack_rotated([_frame(3, 3, pad=2) for _ in range(4)], 2, 8, 8,
                                rotations=None)
    trimmed = sprite.pack_rotated([sprite.trim_frame(_frame(3, 3, pad=2))["pixels"]
                                   for _ in range(4)], 2, 8, 8)
    assert sprite.sheet_extent(trimmed["cells"])[0] <= sprite.sheet_extent(plain["cells"])[0]


def test_constraint_violation_reports():
    r = sprite.pack_rotated([_frame(6, 6) for _ in range(9)], 3, 8, 8, max_sheet=(8, 8))
    if r["ok"] is False:
        assert r.get("reason")
    else:
        assert sprite.sheet_extent(r["cells"])[0] <= 8


def test_utilization_reported():
    r = sprite.pack_rotated([_frame(4, 4) for _ in range(4)], 2, 8, 8)
    assert r["ok"] is True
    assert 0.0 < sprite.utilization(r["cells"]) <= 1.0


# ===========================================================================
# 1. 排布：稳定性、边界、逐像素对齐
# ===========================================================================

def test_layout_is_stable_when_a_frame_is_appended():
    """多一帧只在末尾追加，已有格子坐标一个都不能动。"""
    before = sprite.plan_sprite(8, 32, 8)
    after = sprite.plan_sprite(9, 32, 8)
    for i in range(8):
        assert (before[i].x, before[i].y, before[i].w, before[i].h) == \
               (after[i].x, after[i].y, after[i].w, after[i].h)
    assert after[8].x == 0 and after[8].y == 32


def test_zero_frames_has_deterministic_empty_layout():
    assert sprite.plan_sprite(0, 32, 8) == []
    assert sprite.sheet_extent([]) == (0, 0)
    assert sprite.utilization([]) == 0.0


def test_exactly_full_row():
    cells = sprite.plan_sprite(8, 32, 8)
    assert all(c.y == 0 for c in cells)
    assert sprite.sheet_extent(cells) == (256, 32)
    # 再加一帧必须换行而不是挤进行尾
    cells9 = sprite.plan_sprite(9, 32, 8)
    assert cells9[8].x == 0 and cells9[8].y == 32


def test_grid_coordinates_are_explicit():
    cells = sprite.plan_sprite(10, 16, 3)
    expected = [
        (0, 0), (16, 0), (32, 0),
        (0, 16), (16, 16), (32, 16),
        (0, 32), (16, 32), (32, 32),
        (0, 48),
    ]
    assert [(c.x, c.y) for c in cells] == expected
    assert all(c.w == 16 and c.h == 16 for c in cells)


def test_sheet_limits_are_explicit():
    # 宽度越界：报错而不是静默换列
    try:
        sprite.plan_sprite(4, 32, 8, max_width=64, max_height=4096)
    except sprite.SpriteError:
        pass
    else:
        raise AssertionError("超宽应当抛 SpriteError")
    # 高度越界同样报错
    try:
        sprite.plan_sprite(100, 32, 2, max_width=4096, max_height=64)
    except sprite.SpriteError:
        pass
    else:
        raise AssertionError("超高应当抛 SpriteError")


def test_every_cell_is_pixel_aligned_in_json_and_png(tmp_path):
    colors = ["#ff0000", "#00ff00", "#0000ff", "#ff0000"]
    cells = sprite.plan_sprite(4, 8, 2)
    sprite.pack_cells(colors, cells)
    payload = sprite.export_json(cells, str(tmp_path / "sheet.json"))
    width, height, pixels = sprite.render_sheet(cells)
    assert (width, height) == (payload["width"], payload["height"])

    stride = width * 4
    for record in payload["cells"]:
        if record["frame"] < 0:
            continue
        rgba = sprite.color_rgba(record["color"])
        x, y, w, h = record["x"], record["y"], record["w"], record["h"]
        # 矩形内每个像素都必须是该格颜色
        for py in range(y, y + h):
            for px in range(x, x + w):
                offset = py * stride + px * 4
                assert tuple(pixels[offset:offset + 4]) == rgba
    # PNG 与渲染缓冲同源，尺寸一致
    pw, ph = sprite.export_png(cells, str(tmp_path / "sheet.png"))
    assert (pw, ph) == (width, height)


def test_plan_rejects_bad_arguments():
    for kwargs in (
        dict(count=-1, cell=8, cols=2),
        dict(count=4, cell=0, cols=2),
        dict(count=4, cell=8, cols=0),
    ):
        try:
            sprite.plan_sprite(**kwargs)
        except sprite.SpriteError:
            pass
        else:
            raise AssertionError("非法参数 %r 应当报错" % kwargs)


# ===========================================================================
# 2. 复用：可追溯、按帧取值、容量上限不变
# ===========================================================================

def test_reuse_is_traceable_and_per_frame_lookup_is_stable():
    colors = ["#ff0000", "#00ff00", "#ff0000", "#00ff00", "#ff0000"]
    cells = sprite.plan_sprite(5, 8, 4)
    per_frame = sprite.pack_cells(colors, cells)

    # 按帧号取值：复用帧拿到与首帧同一个格子对象
    assert per_frame[0] is per_frame[2] is per_frame[4] is cells[0]
    assert per_frame[1] is per_frame[3] is cells[1]
    assert cells[0].frame == 0 and cells[0].reused_by == (2, 4)
    assert cells[1].frame == 1 and cells[1].reused_by == (3,)
    # 反查不区分首帧/复用帧
    assert sprite.frame_cell(cells, 4) is cells[0]


def test_reuse_cannot_exceed_cell_capacity_and_leaves_cells_untouched():
    cells = sprite.plan_sprite(3, 8, 3)
    try:
        sprite.pack_cells(["#ff0000", "#00ff00", "#0000ff", "#ffffff"], cells)
    except sprite.SpriteError:
        pass
    else:
        raise AssertionError("去重后 4 色超过 3 格上限应当报错")
    # 失败后不能留下半打包状态
    assert all(c.frame == -1 and c.color == "" and c.reused_by == () for c in cells)


def test_duplicate_frames_fit_within_original_capacity():
    # 复用不改变总格数上限：100 帧但只有 2 种颜色，2 格就够
    cells = sprite.plan_sprite(2, 8, 2)
    per_frame = sprite.pack_cells(["#ff0000", "#00ff00"] * 50, cells)
    assert len(per_frame) == 100
    assert sum(1 for c in cells if c.frame >= 0) == 2


# ===========================================================================
# 3/4. 确定性输出
# ===========================================================================

def test_export_json_is_byte_identical_across_runs(tmp_path):
    colors = ["#e4572e", "#17bebb", "#ffc914", "#2e282a",
              "#e4572e", "#17bebb", "#ffc914", "#2e282a", "#e4572e"]
    paths = []
    for suffix in ("a", "b"):
        cells = sprite.plan_sprite(9, 32, 8)
        sprite.pack_cells(colors, cells)
        path = str(tmp_path / ("sheet_%s.json" % suffix))
        sprite.export_json(cells, path)
        paths.append(path)
    with open(paths[0], "rb") as fa, open(paths[1], "rb") as fb:
        assert fa.read() == fb.read()


def test_export_png_is_byte_identical_across_runs(tmp_path):
    colors = ["#112233", "#445566", "#112233"]
    blobs = []
    for suffix in ("a", "b"):
        cells = sprite.plan_sprite(3, 8, 2)
        sprite.pack_cells(colors, cells)
        path = str(tmp_path / ("sheet_%s.png" % suffix))
        sprite.export_png(cells, path)
        with open(path, "rb") as handle:
            blobs.append(handle.read())
    assert blobs[0] == blobs[1]
    assert blobs[0].startswith(b"\x89PNG\r\n\x1a\n")


def test_empty_export_is_deterministic(tmp_path):
    path = str(tmp_path / "empty.json")
    payload = sprite.export_json([], path)
    assert payload["width"] == 0 and payload["height"] == 0
    assert payload["frame_count"] == 0 and payload["cells"] == []
    with open(path, "rb") as handle:
        assert handle.read() == (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode()


def test_frames_file_is_unchanged_and_loads_nine_frames():
    with open(DATA_FRAMES, "r", encoding="utf-8") as handle:
        assert handle.read() == "9\n"
    colors = sprite.load_frames(DATA_FRAMES)
    assert len(colors) == 9
    # 占位色盘周期 4：帧 i 与帧 i+4 同色（复用的前提）
    assert colors[0] == colors[4] == colors[8]
    assert colors[1] == colors[5]


# ===========================================================================
# 5. 旋转与紧凑
# ===========================================================================

def test_trim_fully_transparent_frame():
    blank = [[(0, 0, 0, 0)] * 4 for _ in range(3)]
    t = sprite.trim_frame(blank)
    assert t["pixels"] == [] and (t["ox"], t["oy"]) == (0, 0)


def test_trim_keeps_partial_alpha_pixels():
    # alpha 非 0 即内容，哪怕半透明
    px = [[(0, 0, 0, 0), (0, 0, 0, 1)],
          [(0, 0, 0, 0), (255, 255, 255, 128)]]
    t = sprite.trim_frame(px)
    assert len(t["pixels"]) == 2 and len(t["pixels"][0]) == 1
    assert (t["ox"], t["oy"]) == (1, 0)


def test_rotate_pixels_all_angles_roundtrip():
    src = _frame(3, 2, pad=1)  # 4x5 的非对称帧
    for angle in (0, 90, 180, 270):
        rotated = sprite.rotate_pixels(src, angle)
        # 90/270 交换宽高
        if angle in (90, 270):
            assert (len(rotated), len(rotated[0])) == (len(src[0]), len(src))
        else:
            assert (len(rotated), len(rotated[0])) == (len(src), len(src[0]))
        # 逆向旋转必须逐像素还原
        back = sprite.rotate_pixels(rotated, (360 - angle) % 360)
        assert back == src


def test_rotation_pixel_roundtrip_through_sheet():
    """按旋转标记从生成的图取图，逆向旋转后与未旋转原帧逐像素一致。"""
    original = _frame(4, 3, pad=2, color=(10, 20, 30, 255))
    for angle in (0, 90, 180, 270):
        result = sprite.pack_rotated([original], 1, 16, 16, rotations=[angle])
        assert result["ok"] is True
        assert result["rotations"] == [angle]
        canvas = sprite.render_pack(result)
        cell = result["cells"][0]

        # 1) 从图上按坐标抠出旋转后的矩形
        extracted = [
            row[cell["x"]:cell["x"] + cell["w"]]
            for row in canvas[cell["y"]:cell["y"] + cell["h"]]
        ]
        # 2) 逆向旋转，应与 trim 后的内容逐像素一致
        trimmed = sprite.trim_frame(original)
        assert sprite.rotate_pixels(extracted, (360 - angle) % 360) == trimmed["pixels"]

        # 3) 借助 ox/oy 贴回原图尺寸，应与原帧完全一致
        restored = [[(0, 0, 0, 0)] * cell["src_w"] for _ in range(cell["src_h"])]
        upright = sprite.rotate_pixels(extracted, (360 - angle) % 360)
        for y, row in enumerate(upright):
            for x, px in enumerate(row):
                restored[cell["oy"] + y][cell["ox"] + x] = px
        assert restored == original


def test_rotation_marked_in_export_json(tmp_path):
    result = sprite.pack_rotated([_frame(3, 2), _frame(2, 3)], 2, 8, 8,
                                 rotations=[90, 270])
    assert result["ok"] is True
    path = str(tmp_path / "pack.json")
    payload = sprite.export_pack_json(result, path)
    assert payload["rotations"] == [90, 270]
    assert all("rotation" in cell for cell in payload["cells"])


def test_invalid_rotation_raises():
    try:
        sprite.pack_rotated([_frame(2, 2)], 1, 8, 8, rotations=[45])
    except sprite.SpriteError:
        pass
    else:
        raise AssertionError("非 90 倍数旋转应当报错")
    try:
        sprite.pack_rotated([_frame(2, 2)], 1, 8, 8, rotations=[0, 90])
    except sprite.SpriteError:
        pass
    else:
        raise AssertionError("rotations 长度不匹配应当报错")


def test_trimmed_frames_no_overlap_with_spacing():
    frames = [_frame(3, 3, pad=2) for _ in range(4)]  # 原图 7x7，trim 后 3x3
    result = sprite.pack_rotated(frames, 2, 8, 8, spacing=2)
    assert result["ok"] is True
    # 间距写进坐标：第二列起点 = 槽位宽 + spacing
    assert result["cells"][1]["x"] == 10
    assert result["cells"][2]["y"] == 10
    rects = [(c["x"], c["y"], c["x"] + c["w"], c["y"] + c["h"])
             for c in result["cells"]]
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            a, b = rects[i], rects[j]
            assert not (a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3])
    # 实际矩形并集（含间距留白）；整图尺寸由槽位网格决定
    assert sprite.sheet_extent(result["cells"]) == (13, 13)
    assert (result["width"], result["height"]) == (18, 18)


def test_trimmed_sheet_not_larger_in_both_dimensions():
    raw = [_frame(2, 2, pad=3) for _ in range(2)]  # 8x8 -> trim 后 2x2
    plain = sprite.pack_rotated(raw, 2, 8, 8)
    pre_trimmed = [sprite.trim_frame(f)["pixels"] for f in raw]
    trimmed = sprite.pack_rotated(pre_trimmed, 2, 8, 8)
    pw, ph = sprite.sheet_extent(plain["cells"])
    tw, th = sprite.sheet_extent(trimmed["cells"])
    assert tw <= pw and th <= ph


def test_frame_too_big_for_slot_is_infeasible():
    result = sprite.pack_rotated([_frame(10, 10)], 1, 8, 8)
    assert result["ok"] is False
    assert result["cells"] == [] and result["reason"]


def test_max_sheet_height_violation_is_infeasible():
    result = sprite.pack_rotated([_frame(2, 2) for _ in range(4)], 1, 8, 8,
                                 max_sheet=(8, 16))
    assert result["ok"] is False
    assert "max_sheet" in result["reason"]


def test_never_emits_out_of_bounds_sheet_on_failure(tmp_path):
    result = sprite.pack_rotated([_frame(6, 6) for _ in range(9)], 3, 8, 8,
                                 max_sheet=(8, 8))
    assert result["ok"] is False
    try:
        sprite.render_pack(result)
    except sprite.SpriteError:
        pass
    else:
        raise AssertionError("失败结果不应当能渲染出图")


# ===========================================================================
# 6. 约束与利用率
# ===========================================================================

def test_utilization_threshold_enforced():
    # 1 个 2x2 内容放进 16x16 槽：利用率 4/256 = 0.015625
    ok = sprite.pack_rotated([_frame(2, 2)], 1, 16, 16, min_utilization=0.01)
    assert ok["ok"] is True
    bad = sprite.pack_rotated([_frame(2, 2)], 1, 16, 16, min_utilization=0.5)
    assert bad["ok"] is False
    assert "利用率" in bad["reason"]


def test_utilization_value_matches_areas():
    result = sprite.pack_rotated([_frame(4, 4) for _ in range(4)], 2, 8, 8)
    assert result["ok"] is True
    # 整图 16x16=256（槽位网格），内容 4*16=64
    assert abs(result["utilization"] - 64 / 256) < 1e-12
    # helper：已用面积 / 实际矩形并集面积（无间距时与整图面积相同）
    assert abs(sprite.utilization(result["cells"]) - 64 / (12 * 12)) < 1e-12
    # v1 网格格表同样可算
    cells = sprite.plan_sprite(4, 8, 2)
    sprite.pack_cells(["#ff0000"] * 4, cells)
    assert sprite.utilization(cells) == 1.0


def test_pack_rotated_edge_counts():
    # 0 帧：确定的空排布
    empty = sprite.pack_rotated([], 2, 8, 8)
    assert empty["ok"] is True and empty["cells"] == []
    assert (empty["width"], empty["height"]) == (0, 0)
    # 1 帧
    one = sprite.pack_rotated([_frame(3, 3)], 2, 8, 8)
    assert one["ok"] is True and len(one["cells"]) == 1
    # 恰好铺满一行
    full = sprite.pack_rotated([_frame(3, 3) for _ in range(4)], 4, 8, 8)
    assert full["ok"] is True
    assert all(c["y"] == 0 for c in full["cells"])
    wrap = sprite.pack_rotated([_frame(3, 3) for _ in range(5)], 4, 8, 8)
    assert wrap["cells"][4]["y"] == 8 and wrap["cells"][4]["x"] == 0


def test_fully_transparent_frame_packs_as_empty_rect():
    blank = [[(0, 0, 0, 0)] * 6 for _ in range(6)]
    result = sprite.pack_rotated([blank, _frame(2, 2)], 2, 8, 8)
    assert result["ok"] is True
    assert result["cells"][0]["w"] == 0 and result["cells"][0]["h"] == 0
    canvas = sprite.render_pack(result)
    # 透明帧所在槽位仍是全透明背景
    assert all(px == (0, 0, 0, 0) for row in canvas[:8] for px in row[:8])


def test_pack_result_is_deterministic(tmp_path):
    frames = [_frame(3, 2 + i % 2, pad=1 + i % 2) for i in range(6)]
    rotations = [0, 90, 180, 270, 0, 90]
    r1 = sprite.pack_rotated(frames, 3, 12, 12, rotations=rotations, spacing=1)
    r2 = sprite.pack_rotated(frames, 3, 12, 12, rotations=rotations, spacing=1)
    assert r1["cells"] == r2["cells"]
    assert r1["rotations"] == r2["rotations"]
    p1 = str(tmp_path / "p1.png")
    p2 = str(tmp_path / "p2.png")
    sprite.export_pack_png(r1, p1)
    sprite.export_pack_png(r2, p2)
    with open(p1, "rb") as fa, open(p2, "rb") as fb:
        assert fa.read() == fb.read()
    j1 = str(tmp_path / "p1.json")
    j2 = str(tmp_path / "p2.json")
    sprite.export_pack_json(r1, j1)
    sprite.export_pack_json(r2, j2)
    with open(j1, "rb") as fa, open(j2, "rb") as fb:
        assert fa.read() == fb.read()


# ===========================================================================
# CLI 验收
# ===========================================================================

def _run_cli(tmp_path, *extra):
    out = str(tmp_path / "sheet.json")
    cmd = [sys.executable, "-m", "sprite.cli", DATA_FRAMES,
           "--cell", "32", "--cols", "8", "--out", out] + list(extra)
    proc = subprocess.run(cmd, cwd=os.path.dirname(os.path.dirname(DATA_FRAMES)),
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return out


def test_cli_output_is_byte_identical_across_processes(tmp_path):
    first = _run_cli(tmp_path)
    second = _run_cli(tmp_path)
    with open(first, "rb") as fa, open(second, "rb") as fb:
        assert fa.read() == fb.read()
    with open(first, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    assert payload["frame_count"] == 9
    assert payload["used_cells"] == 4 and payload["reused_frames"] == 5


def test_cli_v2_path_and_determinism(tmp_path):
    paths = []
    for suffix in ("a", "b"):
        out = str(tmp_path / ("pack_%s.json" % suffix))
        png = str(tmp_path / ("pack_%s.png" % suffix))
        cmd = [sys.executable, "-m", "sprite.cli", DATA_FRAMES,
               "--cell", "32", "--cols", "8",
               "--out", str(tmp_path / ("sheet_%s.json" % suffix)),
               "--pack-json", out, "--pack-png", png,
               "--cell-w", "16", "--cell-h", "16", "--spacing", "1"]
        proc = subprocess.run(cmd, cwd=os.path.dirname(os.path.dirname(DATA_FRAMES)),
                              capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        paths.append((out, png))
    for kind in (0, 1):
        with open(paths[0][kind], "rb") as fa, open(paths[1][kind], "rb") as fb:
            assert fa.read() == fb.read()


def test_cli_reports_limit_error_without_traceback(tmp_path):
    out = str(tmp_path / "x.json")
    cmd = [sys.executable, "-m", "sprite.cli", DATA_FRAMES,
           "--cell", "32", "--cols", "8", "--out", out, "--max-w", "8"]
    proc = subprocess.run(cmd, cwd=os.path.dirname(os.path.dirname(DATA_FRAMES)),
                          capture_output=True, text=True)
    assert proc.returncode == 1
    assert "Traceback" not in proc.stderr and "错误" in proc.stderr
