# 缩略图雪碧图与坐标表

一个能把多帧缩略图排成雪碧图、并输出可逐像素对齐坐标表的打包器

## 保留签名

- `Cell`
- `plan_sprite(count, cell, cols)`
- `pack_cells(frames, cells)`
- `export_json(cells, path)`

## 自测

```
python -m pytest tests -q
python -m sprite.cli data/frames.txt --cell 32 --cols 8 --out sheet.json
```
