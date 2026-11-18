# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sprite as mod  # noqa: E402


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
