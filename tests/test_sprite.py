# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sprite as mod  # noqa: E402
sprite = mod

def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def read_hex(path):
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def test_plan_grid():
    cells = sprite.plan_sprite(9, 32, 8)
    assert len(cells) == 9
    assert cells[8].x == 0 and cells[8].y == 32


def test_single_frame():
    cells = sprite.plan_sprite(1, 32, 8)
    assert len(cells) == 1 and cells[0].w == 32
