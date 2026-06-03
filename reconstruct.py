# -*- coding: utf-8 -*-
"""从 OCR 文本框坐标直接重建表格（不依赖表格模型的内部文本归位）。

核心思路（针对银行开户清单这类有表头、一行多行折行的表）：
1. 把 OCR 文本框按 y 聚成多个物理行。
2. 取“框最多”的那一行作为表头，用表头每个框的 x 位置定列边界。
3. 表头下面的数据区：用最左列（序号）的框定“逻辑行”边界（一行可能占好几行文字）。
4. 每个文本框按中心点落在哪列 + 哪行区，就归到那个单元格；同一格多行从上到下拼接。
5. 表头上面的行（存款人名称/核准号这类 label-value）单独处理。

这个模块不依赖 OCR / 模型，可单独单测。
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional, Tuple
import re

import numpy as np


@dataclass
class Box:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    score: float = 1.0

    @property
    def cx(self):
        return (self.x0 + self.x1) / 2

    @property
    def cy(self):
        return (self.y0 + self.y1) / 2

    @property
    def h(self):
        return self.y1 - self.y0


def _norm(s) -> str:
    return re.sub(r"\s+", "", str(s))


def to_boxes(ocr_result: list, min_score: float = 0.0) -> List[Box]:
    """RapidOCR 格式 [[quad, text, score], ...] -> List[Box]，低于 min_score 的丢弃。"""
    boxes = []
    for item in ocr_result:
        try:
            quad, text = item[0], item[1]
            score = float(item[2]) if len(item) > 2 else 1.0
            if score < min_score:
                continue
            xs = [p[0] for p in quad]
            ys = [p[1] for p in quad]
            boxes.append(Box(str(text), min(xs), min(ys), max(xs), max(ys), score))
        except Exception:
            continue
    return boxes


def _cluster_rows(boxes: List[Box]) -> List[List[Box]]:
    if not boxes:
        return []
    med_h = float(np.median([b.h for b in boxes if b.h > 0]) or 12)
    bs = sorted(boxes, key=lambda b: b.cy)
    rows: List[List[Box]] = [[bs[0]]]
    for b in bs[1:]:
        ref = np.mean([x.cy for x in rows[-1]])
        if abs(b.cy - ref) <= med_h * 0.7:
            rows[-1].append(b)
        else:
            rows.append([b])
    return rows


def _col_seps(header: List[Box]) -> List[float]:
    seps = [-1e9]
    for i in range(len(header) - 1):
        seps.append((header[i].x1 + header[i + 1].x0) / 2)
    seps.append(1e9)
    return seps


def _col_of(x: float, seps: List[float]) -> int:
    for i in range(len(seps) - 1):
        if seps[i] <= x < seps[i + 1]:
            return i
    return len(seps) - 2


def _group_lines(items: List[Box]) -> str:
    """单元格内多框按从上到下、从左到右拼接（中文/数字不加空格）。"""
    if not items:
        return ""
    med_h = float(np.median([b.h for b in items if b.h > 0]) or 12)
    items = sorted(items, key=lambda b: b.cy)
    lines: List[List[Box]] = [[items[0]]]
    for b in items[1:]:
        if abs(b.cy - lines[-1][-1].cy) <= med_h * 0.6:
            lines[-1].append(b)
        else:
            lines.append([b])
    parts = []
    for ln in lines:
        ln.sort(key=lambda b: b.x0)
        parts.append("".join(b.text for b in ln))
    return "".join(parts)


def reconstruct_from_ocr(ocr_result: list, min_score: float = 0.5, debug: Optional[dict] = None):
    """返回 (cells, n_rows, n_cols, covered_keys) 或 None。
    cells: [{r0,r1,c0,c1,text}]。0-based 闭区间。
    找不到明确表头（>=3 列）返回 None。
    """
    boxes = to_boxes(ocr_result, min_score=min_score)
    if len(boxes) < 4:
        return None

    rows = _cluster_rows(boxes)
    if not rows:
        return None
    hi = max(range(len(rows)), key=lambda i: len(rows[i]))
    header = sorted(rows[hi], key=lambda b: b.cx)
    if len(header) < 3:
        return None

    seps = _col_seps(header)
    ncols = len(header)
    header_bottom = max(b.y1 for b in header)
    pre_rows = rows[:hi]
    data_boxes = [b for b in boxes if b.cy > header_bottom + 1]

    # 行区：用最左列（第 0 列）的框做锚点
    anchors = sorted([b for b in data_boxes if _col_of(b.cx, seps) == 0], key=lambda b: b.cy)
    if anchors:
        rsep = [header_bottom]
        for i in range(len(anchors) - 1):
            rsep.append((anchors[i].cy + anchors[i + 1].cy) / 2)
        rsep.append(1e9)
    else:
        rsep = [header_bottom, 1e9]
    nbands = len(rsep) - 1

    def band_of(y):
        for i in range(nbands):
            if rsep[i] <= y < rsep[i + 1]:
                return i
        return nbands - 1

    cells = []
    covered = set()
    r = 0

    # 表头上面的 label-value 行
    for pr in pre_rows:
        sb = sorted(pr, key=lambda b: b.cx)
        for b in sb:
            covered.add(_norm(b.text))
        if len(sb) == 1:
            cells.append({"r0": r, "r1": r, "c0": 0, "c1": ncols - 1, "text": sb[0].text})
        else:
            label = sb[0]
            valboxes = sb[1:]
            vstart = max(1, _col_of(valboxes[0].cx, seps))
            cells.append({"r0": r, "r1": r, "c0": 0, "c1": vstart - 1, "text": label.text})
            cells.append({"r0": r, "r1": r, "c0": vstart, "c1": ncols - 1,
                          "text": _group_lines(valboxes)})
        r += 1

    # 表头
    for ci, b in enumerate(header):
        covered.add(_norm(b.text))
        cells.append({"r0": r, "r1": r, "c0": ci, "c1": ci, "text": b.text})
    header_row = r
    r += 1

    # 数据区
    grid = {}
    for b in data_boxes:
        ci = _col_of(b.cx, seps)
        bi = band_of(b.cy)
        grid.setdefault((bi, ci), []).append(b)
        covered.add(_norm(b.text))
    for bi in range(nbands):
        for ci in range(ncols):
            items = grid.get((bi, ci))
            if items:
                cells.append({"r0": r + bi, "r1": r + bi, "c0": ci, "c1": ci,
                              "text": _group_lines(items)})
    n_rows = r + nbands

    if debug is not None:
        debug["header"] = [b.text for b in header]
        debug["col_seps"] = seps
        debug["n_rows"] = n_rows
        debug["n_cols"] = ncols
        debug["cells"] = cells

    return cells, n_rows, ncols, covered
