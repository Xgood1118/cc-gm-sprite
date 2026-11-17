# -*- coding: utf-8 -*-
"""缩略图雪碧图与坐标表"""

from dataclasses import dataclass


@dataclass
class Cell:
    """一个格子。"""
    index: int
    x: int
    y: int
    w: int
    h: int
    frame: int = -1


def plan_sprite(count, cell, cols):
    """排出格子位置"""
    raise NotImplementedError("plan_sprite 尚未实现")


def pack_cells(frames, cells):
    """把帧分配到格子"""
    raise NotImplementedError("pack_cells 尚未实现")


def export_json(cells, path):
    """输出坐标表"""
    raise NotImplementedError("export_json 尚未实现")

