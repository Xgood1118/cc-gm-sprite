# 缩略图雪碧图与坐标表

一个能把多帧缩略图排成雪碧图、并输出可逐像素对齐坐标表的打包器。**仅使用
Python 标准库**（`dataclasses` / `json` / `re` / `struct` / `zlib`），无第三
方依赖；`data/frames.txt` 不做任何改动。

## 保留签名

| 接口 | 说明 |
| --- | --- |
| `Cell(index, x, y, w, h, frame=-1, color="", reused_by=())` | 一个格子 |
| `plan_sprite(count, cell, cols, max_width=4096, max_height=4096)` | 固定网格排格，前三个参数签名不变 |
| `pack_cells(frames, cells)` | 帧按颜色分配到格子（同色复用），返回“帧号 -> Cell”列表 |
| `export_json(cells, path)` | 输出确定性坐标表 JSON |

v2 接口（本轮新增）：`trim_frame(pixels)`、
`pack_rotated(frames, cols, cell_w, cell_h, rotations=None, max_sheet=None,
spacing=0, min_utilization=0.0)`、`sheet_extent(cells)`、`utilization(cells)`，
以及配套的 `rotate_pixels` / `render_pack` / `export_pack_json` /
`export_pack_png`。

## 排布规则（v1）

1. 第 `i` 个格子恒位于 `(i % cols, i // cols)`，左上角
   `(x, y) = ((i % cols) * cell, (i // cols) * cell)`，矩形 `cell x cell`。
2. **追加帧只在末尾新增格子**，已有格子坐标永不移动——多一帧不会整体错位。
3. 整图尺寸：宽 `min(cols, count) * cell`，高
   `ceil(count / cols) * cell`。
4. **换行规则**：每行固定 `cols` 格，第 `cols` 帧（0 基）换到下一行第 0 列；
   不自动改列数，因为改列数会让已有帧错位。
5. **上限规则**：宽超过 `max_width`（默认 4096）或高超过 `max_height`
   （默认 4096）时抛 `SpriteError` 并给出原因，绝不静默改写、绝不产出越界图。
6. 边界：`count=0` 返回 `[]`（空表，导出宽高 0 的合法 JSON）；`count=1`
   只有原点一格；`count` 恰好是 `cols` 的整数倍时整好铺满一行/多行，
   下一帧才换行。

## 复用规则（v1）

- 颜色归一化后相同的帧复用**同色首帧**的格子：`pack_cells` 返回值
  `result[frame]` 按帧号取到的就是该帧对应格子（复用帧与首帧是同一个
  `Cell` 对象），调用方按帧号取值不受复用影响。
- 格上的 `reused_by` 元组记录“谁复用了我”，导出 JSON 的 `frames` 数组里
  每帧带 `reused` / `reuse_of`，可区分首帧与复用帧。
- 复用**不改变总格数上限**：去重后所需格数超过 `len(cells)` 时抛
  `SpriteError`，且失败时所有格子保持未打包状态（无半打包残留）。

## 逐像素对齐与确定性

- `render_sheet` 严格按 `Cell.x/y/w/h` 把颜色填进 RGBA 缓冲，坐标表与像素
  缓冲同源；`export_png` 手写 PNG（固定 zlib 级别 9、无时间戳块）。
- JSON 字段顺序、缩进（2 空格）、UTF-8 + `\n` 换行全部固定，不含时间戳。
  **同一输入两次运行的 JSON 与 PNG 逐字节一致**（已用 SHA-256 校验）。

## 旋转与紧凑（v2）

`pack_rotated` 对每帧执行固定流水线：

1. **trim**：`trim_frame` 裁掉四周 `alpha == 0` 的全透明边，记录偏移
   `ox/oy`（可把内容贴回原图位置）；整帧全透明时得到 `0x0` 空矩形。
2. **旋转**：按 `rotations[i]`（仅允许 0/90/180/270，顺时针，90 的倍数，
   否则报错）旋转像素；旋转角归一化后写进返回值与导出 JSON 的
   `rotation` 字段。
3. **入槽**：旋转后的矩形左上角对齐放入 `cell_w x cell_h` 槽位，槽位按
   `(i % cols, i // cols)` 固定排列，槽间距 `spacing`。槽位网格保证帧间
   **不重叠、不越界**。

**旋转逐像素一致**：从生成的图按坐标抠出矩形、按 `-rotation` 逆向旋转后，
与 trim 后的内容逐像素相同；再借助 `ox/oy` 贴回原图尺寸，与未旋转原帧
完全一致（测试 `test_rotation_pixel_roundtrip_through_sheet` 覆盖四个角度）。

**裁边不增大面积**：trim 只缩小矩形，槽位网格不变，因此裁边方案的整图
宽高都不大于不裁边方案。

### 约束与利用率

- 矩形放不进 `cell_w x cell_h`、或整图超过 `max_sheet=(最大宽, 最大高)`、
  或利用率低于 `min_utilization`（0..1）时，返回
  `{"ok": False, "reason": "..."}`，`cells` 为空且无法渲染——**报不可行并
  给出原因，绝不产出越界图**。
- 利用率 = 各帧矩形面积之和 / 整图面积（整图面积按槽位网格计算），
  返回值与 JSON 中均带 `utilization` 字段。
- `utilization(cells)` 辅助函数同时接受 `Cell` 列表与 v2 的 dict 格表
  （分母取实际矩形并集面积）；`sheet_extent` 返回矩形并集的 `(宽, 高)`。

## 自测

```
python -m pytest tests -q          # 41 个用例全部通过
```

## CLI 验收命令与关键数值

### v1：固定网格 + 同色复用

```
python -m sprite.cli data/frames.txt --cell 32 --cols 8 --out sheet.json --png sheet.png
```

`data/frames.txt` 只含帧数 `9`，颜色由固定 4 色占位盘循环生成
（帧 `i` 与帧 `i+4k` 同色）。实测输出：

| 指标 | 数值 |
| --- | --- |
| 帧数 / 格数 | 9 / 9 |
| 已用格 / 复用帧 | 4 / 5 |
| 整图宽 x 高 | 256 x 64（2 行 x 8 列） |
| 单元格 | 32 x 32 |
| 第 9 帧（index 8）坐标 | `(0, 32, 32, 32)`，换行到第二行首列 |
| 帧 4/8 | 复用帧 0 的格子 `(0, 0, 32, 32)` |
| PNG | 256 x 64，289 字节 |

### v2：trim + 旋转 + 间距约束

```
python -m sprite.cli data/frames.txt --cell 32 --cols 8 --out sheet.json \
  --pack-json pack.json --pack-png pack.png --cell-w 16 --cell-h 16 --spacing 1
```

实测输出（9 个演示像素帧，旋转角按 0/90/180/270 循环）：

| 指标 | 数值 |
| --- | --- |
| 整图宽 x 高 | 135 x 33（8 列槽位，槽 16x16，间距 1） |
| 利用率 | 0.1616（内容面积 720 / 整图面积 4455） |
| 旋转角 | `[0, 90, 180, 270, 0, 90, 180, 270, 0]` |
| 裁边后矩形 | 8x8 与 10x10 两种，带 `ox/oy` 偏移 |
| PNG | 135 x 33，185 字节 |

不可行示例（`--max-sheet 64x64`，8 列槽位本身就需要 135px 宽）：

```
v2 打包不可行：需要整图 135x33，超过 max_sheet 64x64（cols=8, 槽位 16x16, 间距 1, 9 帧）
# 退出码 2，不产生任何越界图
```

### 逐字节一致

同一命令连跑两次，四个产物 SHA-256 前缀两两相同：

| 产物 | 两次运行的 SHA-256 前缀 |
| --- | --- |
| `sheet.json` | `91aafdfc0abd3ba8` |
| `sheet.png` | `23b9d453f321551c` |
| `pack.json` | `cd7559b61059aaa2` |
| `pack.png` | `993ac575ca957360` |

### 其他 CLI 参数

`--max-w/--max-h`（v1 整图上限）、`--min-utilization`（v2 利用率阈值）、
`--max-sheet 宽x高`（v2 整图上限）。参数非法或约束不可行时进程以非 0
退出码结束，stderr 输出中文错误原因，不打印 traceback。
