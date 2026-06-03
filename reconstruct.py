# -*- coding: utf-8 -*-
"""从 OCR 文本框坐标直接重建表格（不依赖表格模型的内部文本归位）。

核心思路（针对银行开户清单这类有表头、一行多行折行的表）：
1. 把 OCR 文本框按 y 聚成多个物理行。
2. 取“框最多”的那一行作为表头；表头如果自己换行成多行（同一列上下两个框），
   先按 x 重叠把它们并回同一列，避免多出一个空列。
3. 用表头每个框的 x 位置定列边界。
4. 表头下面的数据区：用最左列（序号）的框定“逻辑行”边界（一行可能占好几行文字）。
5. 每个文本框按中心点落在哪列 + 哪行区，就归到那个单元格；同一格多行从上到下拼接。
6. 关键：OCR 经常把同一横排、跨好几列的文字识别成一个超宽的框
   （例如“账号+账户名称+账户性质”连在一起）。对这种跨多列的框，按
   “数字串 / 中文串”切成片段，每段按自己的中心 x 归到对应列——这样账号
   这种连续数字串会被整段保留、不会被拆断或塞错列。
7. 表头上面的行（存款人名称/核准号这类 label-value）单独处理。

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


def _merge_header_columns(header: List[Box]) -> List[Box]:
    """合并同一列因换行被拆成上下两个框的表头（x 区间高度重叠的相邻框）。
    例如“开户银”/“行名称”应并成一个表头列“开户银行名称”，否则会多出一空列。
    """
    if not header:
        return header
    hs = sorted(header, key=lambda b: b.cx)
    merged: List[Box] = [hs[0]]
    for b in hs[1:]:
        last = merged[-1]
        ov = min(last.x1, b.x1) - max(last.x0, b.x0)
        narrow = min(last.x1 - last.x0, b.x1 - b.x0)
        if narrow > 0 and ov >= 0.5 * narrow:
            top, bot = sorted([last, b], key=lambda z: z.y0)
            merged[-1] = Box(
                top.text + bot.text,
                min(last.x0, b.x0), min(last.y0, b.y0),
                max(last.x1, b.x1), max(last.y1, b.y1),
                min(last.score, b.score),
            )
        else:
            merged.append(b)
    return merged


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


def _is_digitish(ch: str) -> bool:
    return ch in "0123456789.-/:：,， "


def _split_text_runs(text: str) -> List[Tuple[str, int, int]]:
    """把字符串切成“数字串 / 文字串”交替的片段。
    返回 [(子串, 起始字符下标, 结束字符下标含), ...]。
    去掉两端空白后再判定类别，保证“账号”这种连续数字整段不被拆开。
    """
    runs: List[Tuple[str, int, int]] = []
    i, n = 0, len(text)
    while i < n:
        cls = _is_digitish(text[i])
        j = i
        while j < n and _is_digitish(text[j]) == cls:
            j += 1
        sub = text[i:j]
        if sub.strip():
            runs.append((sub, i, j - 1))
        i = j
    return runs


def _spanned_cols(b: Box, seps: List[float]) -> List[int]:
    """这个框的 x 区间实际压住了哪几列（重叠量足够才算）。"""
    w = max(1.0, b.x1 - b.x0)
    cols = []
    for i in range(len(seps) - 1):
        lo, hi = seps[i], seps[i + 1]
        ov = min(b.x1, hi) - max(b.x0, lo)
        colw = hi - lo if hi < 1e8 and lo > -1e8 else w
        if ov > 0.2 * min(w, colw) or (lo <= b.cx < hi):
            cols.append(i)
    return cols


def _split_if_multicol(b: Box, seps: List[float]) -> List[Box]:
    """若一个数据框横跨多列（OCR 把多列文字连成一个框），按字符拆开归列。

    要点：
    - 汉字比数字宽，用加权字宽（数字 0.6 / 其他 1.0）估算每个字的 x，比均匀划分准。
    - 保护连续数字串（如账号）：整段归到多数字符所在列，绝不拆断。
    - 连续同列的字符合成一个子框。
    单列框原样返回。"""
    if len(_spanned_cols(b, seps)) < 2:
        return [b]
    text = b.text
    n = len(text)
    if n == 0:
        return [b]
    width = b.x1 - b.x0
    weights = [0.6 if _is_digitish(c) else 1.0 for c in text]
    total = sum(weights) or 1.0

    # 每个字符的加权中心 x 与所属列
    cum = 0.0
    centers = []
    for w in weights:
        centers.append(b.x0 + (cum + w / 2) / total * width)
        cum += w
    cols = [_col_of(cx, seps) for cx in centers]

    # 保护连续数字串：整段统一到多数列
    for sub, i0, i1 in _split_text_runs(text):
        if sub and all(_is_digitish(ch) for ch in sub):
            seg = cols[i0:i1 + 1]
            mc = max(set(seg), key=seg.count)
            for k in range(i0, i1 + 1):
                cols[k] = mc

    # 边界上的累计宽度（用于子框 x0/x1）
    cumw = [0.0]
    for w in weights:
        cumw.append(cumw[-1] + w)

    out: List[Box] = []
    k = 0
    while k < n:
        c = cols[k]
        j = k
        while j < n and cols[j] == c:
            j += 1
        sub = text[k:j]
        sx0 = b.x0 + cumw[k] / total * width
        sx1 = b.x0 + cumw[j] / total * width
        out.append(Box(sub, sx0, b.y0, sx1, b.y1, b.score))
        k = j
    return out


def _refine_seps(seps: List[float], data_boxes: List[Box]) -> List[float]:
    """用数据区里“只占一列”的框修正列边界。

    表头文字常比数据窄，靠表头算的边界会把“名称”这种宽列的末尾几个字
    划到右边列。这里以“右边列内容的最左 x”作为两列间的边界，把边界之左
    都归给左列，避免误切。只在不与左列已知内容冲突时才采用。
    """
    cols_boxes = {}
    for b in data_boxes:
        sc = _spanned_cols(b, seps)
        if len(sc) == 1:
            cols_boxes.setdefault(sc[0], []).append(b)
    new = list(seps)
    margin = 3.0
    for i in range(1, len(seps) - 1):  # 第 i-1 列与第 i 列之间的边界
        right = cols_boxes.get(i)
        if not right:
            continue
        right_left = min(b.x0 for b in right) - margin
        left = cols_boxes.get(i - 1)
        left_right = max(b.x1 for b in left) + margin if left else None
        if left_right is not None and right_left <= left_right:
            # 左右内容重叠，退回取中点
            new[i] = (left_right + right_left) / 2
        else:
            new[i] = right_left
    return new


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
    header = _merge_header_columns(header)
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
    r += 1

    # 数据区：先用数据本身修正列边界，再把跨多列的超宽框按片段拆开，各自归列
    dseps = _refine_seps(seps, data_boxes)
    grid = {}
    for b in data_boxes:
        covered.add(_norm(b.text))
        for sb in _split_if_multicol(b, dseps):
            ci = _col_of(sb.cx, dseps)
            bi = band_of(b.cy)
            grid.setdefault((bi, ci), []).append(sb)
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
