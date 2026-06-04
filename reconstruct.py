# -*- coding: utf-8 -*-
"""从 OCR 文本框坐标直接重建表格（不依赖表格模型的内部文本归位）。

核心思路（针对银行开户清单这类有表头、一行多行折行的表）：
1. 把 OCR 文本框按 y 聚成多个物理行。
2. 取“框最多”的那一行作为表头；表头如果自己换行成多行（同一列上下两个框），
   先按 x 重叠把它们并回同一列，避免多出一个空列。
3. 用表头每个框的 x 位置定列边界。
4. 表头下面的数据区：用“图像横线 + 文字竖直投影留白 + 表底截断”三路合并定“逻辑行”边界
   （一行可能占好几行文字）。不再依赖序号列做锡点——OCR 经常漏识“1、3”这种孤立数字。
5. 每个文本框按中心点落在哪列 + 哪行区，就归到那个单元格；同一格多行从上到下拼接。
6. 关键：OCR 经常把同一横排、跨好几列的文字识别成一个超宽的框
   （例如“账号+账户名称+账户性质”连在一起）。对这种跨多列的框，按
   “数字串 / 中文串”切成片段，每段按自己的中心 x 归到对应列。
7. 表头上面的行（存款人名称/核准号这类 label-value）单独处理。

这个模块不依赖 OCR / 模型，可单独单测（row_lines 为空时仅用投影与截断）。
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


def _recover_columns(header: List[Box], data_boxes: List[Box], med_h: float) -> List[Box]:
    """OCR 有时整列表头都没认出来（比如“账号”），导致该列数据挤进相邻列。
    若相邻表头之间出现异常大的空档，且空档中部稳定地落着跨多行的数据框，
    就认定这是一列被漏识表头的隐藏列，补一个空表头占位，让数据各归各列。
    仅在空档明显过宽 + 中部有≥2个、且跨多行的数据框时才触发，
    表头识别完整的表不会受影响。
    """
    if len(header) < 2 or not data_boxes:
        return header
    hs = sorted(header, key=lambda b: b.cx)
    widths = [b.x1 - b.x0 for b in hs if b.x1 > b.x0]
    med_w = float(np.median(widths)) if widths else 200.0
    hy0 = min(b.y0 for b in hs)
    hy1 = max(b.y1 for b in hs)
    extra: List[Box] = []
    for i in range(len(hs) - 1):
        L, R = hs[i].x1, hs[i + 1].x0
        gap = R - L
        if gap <= med_w * 1.5:
            continue
        lo, hi = L + gap * 0.15, R - gap * 0.15  # 只看空档中部，避免接近邻列的碎片
        gb = [b for b in data_boxes if lo < b.cx < hi]
        if len(gb) < 2:
            continue
        if max(b.cy for b in gb) - min(b.cy for b in gb) < med_h * 1.5:
            continue  # 需跨多行，排除单个零散框误触发
        x0 = min(b.x0 for b in gb)
        x1 = max(b.x1 for b in gb)
        extra.append(Box("", x0, hy0, x1, hy1, 1.0))
    if not extra:
        return header
    return sorted(hs + extra, key=lambda b: b.cx)


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
    if len(_spanned_cols(b, seps)) < 2:
        return [b]
    text = b.text
    n = len(text)
    if n == 0:
        return [b]
    width = b.x1 - b.x0
    weights = [0.6 if _is_digitish(c) else 1.0 for c in text]
    total = sum(weights) or 1.0
    cum = 0.0
    centers = []
    for w in weights:
        centers.append(b.x0 + (cum + w / 2) / total * width)
        cum += w
    cols = [_col_of(cx, seps) for cx in centers]
    for sub, i0, i1 in _split_text_runs(text):
        if sub and all(_is_digitish(ch) for ch in sub):
            seg = cols[i0:i1 + 1]
            mc = max(set(seg), key=seg.count)
            for k in range(i0, i1 + 1):
                cols[k] = mc
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
    cols_boxes = {}
    for b in data_boxes:
        sc = _spanned_cols(b, seps)
        if len(sc) == 1:
            cols_boxes.setdefault(sc[0], []).append(b)
    new = list(seps)
    margin = 3.0
    for i in range(1, len(seps) - 1):
        right = cols_boxes.get(i)
        if not right:
            continue
        right_left = min(b.x0 for b in right) - margin
        left = cols_boxes.get(i - 1)
        left_right = max(b.x1 for b in left) + margin if left else None
        if left_right is not None and right_left <= left_right:
            new[i] = (left_right + right_left) / 2
        else:
            new[i] = right_left
    return new


def _group_lines(items: List[Box]) -> str:
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


def _row_bands(data_boxes: List[Box], seps: List[float], header_bottom: float,
               med_h: float, row_lines: Optional[List[float]] = None
               ) -> Tuple[List[float], float]:
    """确定数据区每个逻辑行的 y 边界，返回 (rsep, table_bottom)。

    扫描件三类麻烦：行高不等、横线被印章/水印冲淡、表下方还有印章落款。
    单靠序号列做锡点不稳（OCR 经常漏掉“1、3”这种孤立数字）。这里三路合并：
      1. 图像横线 row_lines：能切“被内容填满、几乎不留白”的相邻行；
      2. 文字竖直投影的成片大留白：补横线缺失处；
      3. 表底截断：遇到超大留白就认为下面是印章/落款，整体截断。
    """
    in_table = [b for b in data_boxes if seps[1] - 1 <= b.cx <= seps[-2] + 1]
    if not in_table:
        in_table = list(data_boxes)
    if not in_table:
        return [header_bottom, 1e9], 1e9
    ivs = sorted([(b.y0, b.y1) for b in in_table])
    bands = [list(ivs[0])]
    for a, c in ivs[1:]:
        if a <= bands[-1][1] + 2:
            bands[-1][1] = max(bands[-1][1], c)
        else:
            bands.append([a, c])
    GAP_ROW = 0.45 * med_h     # 成片留白 > 此值才算换逻辑行
    GAP_STAMP = 1.8 * med_h    # 超大留白：下面多半是印章/落款，截断
    kept = [bands[0]]
    for b in bands[1:]:
        if b[0] - kept[-1][1] > GAP_STAMP:
            break
        kept.append(b)
    table_bottom = kept[-1][1]
    pgaps = [(kept[i - 1][1] + kept[i][0]) / 2
             for i in range(1, len(kept))
             if kept[i][0] - kept[i - 1][1] > GAP_ROW]
    hlines = [y for y in (row_lines or [])
              if header_bottom + 8 < y < table_bottom - 5]
    cand = sorted(pgaps + hlines)
    merged: List[float] = []
    for y in cand:
        if merged and abs(y - merged[-1]) < med_h * 0.8:
            merged[-1] = (merged[-1] + y) / 2   # 横线与留白指向同一处，合一
        else:
            merged.append(y)
    return [header_bottom] + merged + [table_bottom], table_bottom


def reconstruct_from_ocr(ocr_result: list, min_score: float = 0.5,
                         debug: Optional[dict] = None,
                         row_lines: Optional[List[float]] = None):
    """返回 (cells, n_rows, n_cols, covered_keys) 或 None。
    cells: [{r0,r1,c0,c1,text}]。0-based 闭区间。
    找不到明确表头（>=3 列）返回 None。
    row_lines: 图像检测到的水平表格线 y 坐标（与 OCR 同坐标系），可为空。
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

    header_bottom = max(b.y1 for b in header)
    pre_rows = rows[:hi]
    data_boxes = [b for b in boxes if b.cy > header_bottom + 1]

    # 逻辑行边界：横线 + 投影留白 + 表底截断（不再依赖序号列锡点）
    med_h = float(np.median([b.h for b in data_boxes if b.h > 0]) or 12)

    # 补回被漏识表头的隐藏列（如“账号”），避免数据挤进相邻列
    header = _recover_columns(header, data_boxes, med_h)
    seps = _col_seps(header)
    ncols = len(header)
    rsep, table_bottom = _row_bands(data_boxes, seps, header_bottom, med_h, row_lines)
    nbands = len(rsep) - 1
    # 表底以下（印章/落款）不进表格，留给“未归入文字”兑底区
    data_boxes = [b for b in data_boxes if b.cy < table_bottom + med_h * 0.5]

    def band_of(y):
        for i in range(nbands):
            if rsep[i] <= y < rsep[i + 1]:
                return i
        return nbands - 1

    cells = []
    covered = set()
    r = 0

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

    for ci, b in enumerate(header):
        covered.add(_norm(b.text))
        cells.append({"r0": r, "r1": r, "c0": ci, "c1": ci, "text": b.text})
    r += 1

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
        debug["row_seps"] = rsep
        debug["row_lines_detected"] = list(row_lines or [])
        debug["table_bottom"] = table_bottom
        debug["n_rows"] = n_rows
        debug["n_cols"] = ncols
        debug["cells"] = cells

    return cells, n_rows, ncols, covered
