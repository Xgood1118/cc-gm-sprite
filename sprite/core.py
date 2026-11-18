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



def trim_frame(pixels):
    """裁掉四周全透明像素：返回 dict(pixels=裁剪后像素, ox=偏移x, oy=偏移y)。"""
    raise NotImplementedError("trim_frame 尚未实现")


def pack_rotated(frames, cols, cell_w, cell_h, rotations=None, max_sheet=None):
    """带旋转的打包：返回 dict(cells, rotations, ok, reason)。

    cells 每项为 dict(frame, x, y)；无法满足约束时 ok=False 并给 reason。
    """
    raise NotImplementedError("pack_rotated 尚未实现")


def sheet_extent(cells):
    """返回 (宽, 高)。"""
    raise NotImplementedError("sheet_extent 尚未实现")


def utilization(cells):
    """返回已用面积占整图面积的比例。"""
    raise NotImplementedError("utilization 尚未实现")
