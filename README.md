# 缩略图雪碧图与坐标表

一个能把多帧缩略图排成雪碧图、并输出可逐像素对齐坐标表的打包器。
仅用 Python 标准库（`dataclasses`、`json`、`re`、`struct`、`zlib`、`argparse`），
无第三方依赖；`data/frames.txt` 保持原样未改动。

## 保留签名

- `Cell`
- `plan_sprite(count, cell, cols)`
- `pack_cells(frames, cells)`
- `export_json(cells, path)`

另提供一组辅助接口：

| 接口 | 作用 |
| --- | --- |
| `load_frames(path)` | 读取 `frames.txt`，返回每帧颜色 |
| `sheet_size(cells)` | 返回整图 `(宽, 高)` |
| `render_sheet(cells)` | 生成整图 RGBA 像素缓冲 |
| `export_png(cells, path)` | 输出确定性 PNG（手写编码器） |
| `frame_cell(cells, frame)` | 按帧号反查格子 |
| `normalize_color(value)` | 颜色归一化成小写 `#rrggbb` |
| `SpriteError` | 所有校验失败的异常（`ValueError` 子类） |

## 排布规则

- **固定网格**：第 `i` 个格子恒在 `((i % cols) * cell, (i // cols) * cell)`，
  大小 `cell x cell`。帧号与格子下标一一对应。
- **稳定**：追加一帧只在末尾新增格子，已有格子的 `x/y` 一格都不动；
  帧数变化不会让整张图重排。整图宽高只由帧数、`cell`、`cols` 决定：
  `宽 = min(cols, 帧数) * cell`，`高 = ceil(帧数 / cols) * cell`。
- **图宽高上限**：`MAX_SHEET_WIDTH = MAX_SHEET_HEIGHT = 4096` 像素。
  超限时 **不做自动换行改写**（改 `cols` 会让已有帧整体错位），直接抛
  `SpriteError` 并在消息中给出当前尺寸、上限与可行的 `--cols` / `--cell`
  方向；CLI 返回退出码 1，不打印 traceback。
- **边界**：
  - `0` 帧 → 返回 `[]`；坐标表输出宽高 `0x0`、空 `cells` / `frames`；
    PNG 无内容可出，`export_png` 抛 `SpriteError`。
  - `1` 帧 → 一格，整图 `cell x cell`（例如 32x32）。
  - 恰好铺满一行（`count % cols == 0`）→ 不产生空尾行，整图高度正好是
    `count / cols` 行；`count + 1` 帧才在下一行第 0 列新增格子。

## 复用规则

- `pack_cells(frames, cells)` 接收每帧颜色，**颜色相同（归一化后逐字节相同）
  的帧复用第一帧所在的格子**。返回长度为帧数的列表，`result[帧号]` 永远是
  该帧对应的 `Cell`（复用帧返回与首帧相同的对象），调用方按帧号取值不受
  复用影响。
- 坐标表同时给出两套可追溯记录：
  - `cells[*].frame` 是占用该格的首帧号，`cells[*].reused_by` 是复用它的
    后续帧号列表；
  - `frames[*].cell` 是该帧最终所在的格子下标，`frames[*].reused` 标记是否
    复用，`frames[*].reuse_of` 指向被复用的首帧号（首帧为 `null`）。
- **格数上限不变**：可用的格子总数仍是 `len(cells)`（即 `plan_sprite` 排出的
  格数）。颜色去重后需要的格子数超过上限时抛 `SpriteError`
  （`"…超过总格数上限 N"`）；分配方案在改动 `cells` 之前就已完整算出，
  失败时 `cells` 保持原状，不会留下半打包状态。
- 复用只减少实际被占用的格子，`cell_count`（总格数上限）不变；未占用格在
  PNG 中保持透明。

## 输出

- `export_json` 字段顺序与缩进固定，UTF-8 + `\n` 换行，无时间戳；同一输入
  两次运行 **逐字节一致**。
- `export_png` 手写 PNG（IHDR/IDAT/IEND + CRC，8 位 RGBA，过滤器 0），固定
  压缩级别、无时间戳等可变块，同样逐字节可复现。
- 像素取自 `Cell.x/y/w/h`，与坐标表严格同源：测试会解码 PNG，逐像素检查
  每个坐标矩形整块同色、矩形外紧邻像素不同色，并检查矩形互不重叠、不越界。

## 数据格式

`data/frames.txt`（未改动）：

```
9
```

首行是帧数；其后可选地每行给出一个帧颜色（`RGB` / `RRGGBB`，可带 `#`）。
`#ff0000` 是颜色，`# 注释` 是注释。本文件只给了帧数，未给出的帧由
`placeholder_color(frame)` 取占位色（`PLACEHOLDER_PALETTE` 4 色按帧号循环），
因此第 `i` 帧与第 `i + 4k` 帧同色——这不改变输入数据，只是让“同色帧复用”
在纯文本数据上可复现。
颜色行多于帧数、颜色非法、帧数行缺失或为负都会抛 `SpriteError`。

## 运行结果

`python -m sprite.cli data/frames.txt --cell 32 --cols 8 --out sheet.json`：

```
格数 9（已用 4，复用 5 帧），图 256x64，2 行 x 8 列
```

| 指标 | 数值 |
| --- | --- |
| 帧数 | 9 |
| 总格数 `cell_count` | 9（上限，8 列 x 2 行） |
| 实际占用格数 `used_cells` | 4 |
| 复用帧数 `reused_frames` | 5 |
| 图宽 x 高 | 256 x 64 |
| 单元格 | 32 x 32 |

每格坐标（`index: x, y, w, h -> frame / reused_by`）：

| 格 | x | y | w | h | 首帧 | 复用帧 |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | 0 | 0 | 32 | 32 | 0 | 4, 8 |
| 1 | 32 | 0 | 32 | 32 | 1 | 5 |
| 2 | 64 | 0 | 32 | 32 | 2 | 6 |
| 3 | 96 | 0 | 32 | 32 | 3 | 7 |
| 4 | 128 | 0 | 32 | 32 | — | — |
| 5 | 160 | 0 | 32 | 32 | — | — |
| 6 | 192 | 0 | 32 | 32 | — | — |
| 7 | 224 | 0 | 32 | 32 | — | — |
| 8 | 0 | 32 | 32 | 32 | — | — |

按帧号取值：

| 帧 | 格 | x | y | w | h | 颜色 | 复用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 0 | 0 | 0 | 32 | 32 | `#e4572e` | 首帧 |
| 1 | 1 | 32 | 0 | 32 | 32 | `#17bebb` | 首帧 |
| 2 | 2 | 64 | 0 | 32 | 32 | `#ffc914` | 首帧 |
| 3 | 3 | 96 | 0 | 32 | 32 | `#2e282a` | 首帧 |
| 4 | 0 | 0 | 0 | 32 | 32 | `#e4572e` | 复用帧 0 |
| 5 | 1 | 32 | 0 | 32 | 32 | `#17bebb` | 复用帧 1 |
| 6 | 2 | 64 | 0 | 32 | 32 | `#ffc914` | 复用帧 2 |
| 7 | 3 | 96 | 0 | 32 | 32 | `#2e282a` | 复用帧 3 |
| 8 | 0 | 0 | 0 | 32 | 32 | `#e4572e` | 复用帧 0 |

帧 8 是第 2 行唯一的帧，但颜色与帧 0 相同，所以复用格 0；格 8 因此未被占用。

## JSON 格式

```json
{
  "cell_size": 32,
  "cols": 8,
  "rows": 2,
  "width": 256,
  "height": 64,
  "frame_count": 9,
  "cell_count": 9,
  "used_cells": 4,
  "reused_frames": 5,
  "cells": [
    {"index": 0, "x": 0, "y": 0, "w": 32, "h": 32,
     "frame": 0, "color": "#e4572e", "reused_by": [4, 8]}
  ],
  "frames": [
    {"frame": 0, "cell": 0, "x": 0, "y": 0, "w": 32, "h": 32,
     "color": "#e4572e", "reused": false, "reuse_of": null},
    {"frame": 4, "cell": 0, "x": 0, "y": 0, "w": 32, "h": 32,
     "color": "#e4572e", "reused": true, "reuse_of": 0}
  ]
}
```

`cols` / `rows` 是整图实际占用的列数行数（`0` 帧时为 0）。

## 自测

```
python -m pytest tests -q
python -m sprite.cli data/frames.txt --cell 32 --cols 8 --out sheet.json
```

可选 `--png sheet.png` 同时输出雪碧图。`--cell` / `--cols` 必须 >= 1。

## 测试覆盖

`python -m pytest tests -q` → 18 passed。

- 逐像素对齐：解码 PNG 后逐格检查矩形内外像素、互不重叠、不越界
- 复用可追溯：`frames[].reuse_of` / `cells[].reused_by` 与按帧号取值一致，
  复用超限报错且失败时 `cells` 不被改动
- 0 帧与 1 帧：空表、单格整图尺寸
- 满行边界：8 / 9 / 16 帧的宽高与换行位置
- 图宽高上限：宽/高各一条超限用例，错误消息可读
- 两次一致：同进程两次导出、跨进程两次 CLI 运行的 JSON 与 PNG 逐字节相同
- 排布稳定：追加一帧不移动已有格子
