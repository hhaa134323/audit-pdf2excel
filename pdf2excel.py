#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
audit-pdf2excel
================
批量把扫描件 PDF 通过 OCR 转成 Excel：一个 PDF 一个工作表，尽量还原表格行列。

设计约束（为“能跑脚本、但不确定能装软件”的 Windows + VSCode 环境）：
    全部依赖都用 pip 装，不需要管理员权限、不需要装 Tesseract / poppler 等外部软件。
        pip install pymupdf rapidocr-onnxruntime openpyxl numpy

用法：
    python pdf2excel.py -i ./pdfs -o ./out.xlsx
    python pdf2excel.py -i ./pdfs -o ./out.xlsx --dpi 300

OCR 引擎：RapidOCR（rapidocr-onnxruntime），纯pip包、自带中英文模型、首次运行后可完全离线。
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import glob
from dataclasses import dataclass
from typing import List, Sequence, Tuple


# ----------------------------------------------------------------------------
# 数据结构
# ----------------------------------------------------------------------------
@dataclass
class Cell:
    """一个 OCR 识别出的文本框。"""
    text: str
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def h(self) -> float:
        return max(self.y1 - self.y0, 1.0)


def _median(values: Sequence[float]) -> float:
    s = sorted(values)
    n = len(s)
    if n == 0:
        return 0.0
    if n % 2:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2


# ----------------------------------------------------------------------------
# OCR 结果 -> 表格重建（不依赖任何 OCR 库，可单独单测）
# ----------------------------------------------------------------------------
def boxes_to_cells(ocr_result: Sequence) -> List[Cell]:
    """把 RapidOCR 的 [box, text, score] 列表转成 Cell 列表。

    box 是四个角点 [[x,y],[x,y],[x,y],[x,y]]。
    """
    cells: List[Cell] = []
    for item in ocr_result:
        box, text = item[0], item[1]
        t = (text or "").strip()
        if not t:
            continue
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        cells.append(Cell(t, min(xs), min(ys), max(xs), max(ys)))
    return cells


def cluster_rows(cells: List[Cell]) -> List[List[Cell]]:
    """按垂直位置把文本框聚成一行一行。"""
    if not cells:
        return []
    med_h = _median([c.h for c in cells]) or 1.0
    ordered = sorted(cells, key=lambda c: c.cy)
    rows: List[List[Cell]] = []
    cur: List[Cell] = [ordered[0]]
    cur_cy = ordered[0].cy
    for c in ordered[1:]:
        if abs(c.cy - cur_cy) <= 0.6 * med_h:
            cur.append(c)
            cur_cy = sum(x.cy for x in cur) / len(cur)
        else:
            rows.append(cur)
            cur = [c]
            cur_cy = c.cy
    rows.append(cur)
    for r in rows:
        r.sort(key=lambda c: c.x0)
    return rows


def detect_columns(cells: List[Cell]) -> List[float]:
    """根据所有文本框的左边界聚类，推断出列的起始 x 坐标。"""
    if not cells:
        return [0.0]
    med_h = _median([c.h for c in cells]) or 1.0
    gap = 1.5 * med_h
    xs = sorted(c.x0 for c in cells)
    col_starts: List[float] = []
    cluster = [xs[0]]
    prev = xs[0]
    for x in xs[1:]:
        if x - prev > gap:
            col_starts.append(min(cluster))
            cluster = [x]
        else:
            cluster.append(x)
        prev = x
    col_starts.append(min(cluster))
    return col_starts


def _assign_col(x0: float, col_starts: List[float]) -> int:
    return min(range(len(col_starts)), key=lambda i: abs(col_starts[i] - x0))


def reconstruct_table(cells: List[Cell]) -> List[List[str]]:
    """把 OCR 文本框重建成二维表格（行 x 列）。"""
    if not cells:
        return []
    rows = cluster_rows(cells)
    col_starts = detect_columns(cells)
    ncols = len(col_starts)
    grid: List[List[str]] = []
    for r in rows:
        line = [""] * ncols
        for c in r:
            ci = _assign_col(c.x0, col_starts)
            line[ci] = (line[ci] + " " + c.text).strip() if line[ci] else c.text
        grid.append(line)
    return grid


# ----------------------------------------------------------------------------
# PDF 渲染 + OCR（需要 pymupdf / rapidocr / numpy）
# ----------------------------------------------------------------------------
def render_pdf_pages(path: str, dpi: int):
    """把 PDF 逐页渲染成 BGR 图像（numpy 数组）。yield (page_no, total, img)。"""
    import fitz  # PyMuPDF
    import numpy as np

    doc = fitz.open(path)
    total = doc.page_count
    scale = dpi / 72.0
    mat = fitz.Matrix(scale, scale)
    for i, page in enumerate(doc, 1):
        pix = page.get_pixmap(matrix=mat)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n == 4:
            img = img[:, :, :3]
        elif pix.n == 1:
            img = np.repeat(img, 3, axis=2)
        # PyMuPDF 是 RGB，RapidOCR/cv2 习惯 BGR，转一下更稳
        yield i, total, img[:, :, ::-1].copy()
    doc.close()


_ENGINE = None


def get_engine():
    global _ENGINE
    if _ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR
        _ENGINE = RapidOCR()
    return _ENGINE


def ocr_image(img) -> List:
    engine = get_engine()
    result, _ = engine(img)
    return result or []


# ----------------------------------------------------------------------------
# Excel 输出
# ----------------------------------------------------------------------------
def sanitize_sheet_name(name: str, used: set) -> str:
    name = re.sub(r"[\[\]\:\*\?\/\\]", "_", name).strip()[:31] or "Sheet"
    base = name
    i = 1
    while name in used:
        suffix = f"_{i}"
        name = base[: 31 - len(suffix)] + suffix
        i += 1
    used.add(name)
    return name


def find_pdfs(input_path: str) -> List[str]:
    if os.path.isfile(input_path) and input_path.lower().endswith(".pdf"):
        return [input_path]
    pdfs = sorted(glob.glob(os.path.join(input_path, "**", "*.pdf"), recursive=True))
    return pdfs


def convert(input_path: str, output_path: str, dpi: int = 300) -> None:
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter
    from openpyxl.styles import Alignment, Font

    pdfs = find_pdfs(input_path)
    if not pdfs:
        print(f"[!] 在 {input_path} 下没找到 PDF")
        sys.exit(1)

    print(f"[i] 共 {len(pdfs)} 个 PDF，DPI={dpi}")
    wb = Workbook()
    wb.remove(wb.active)
    used: set = set()

    for idx, pdf in enumerate(pdfs, 1):
        name = os.path.splitext(os.path.basename(pdf))[0]
        sheet = sanitize_sheet_name(name, used)
        ws = wb.create_sheet(sheet)
        print(f"  ({idx}/{len(pdfs)}) {os.path.basename(pdf)} -> 工作表「{sheet}」")
        rowptr = 1
        max_cols = 1
        try:
            for pno, total, img in render_pdf_pages(pdf, dpi):
                ocr = ocr_image(img)
                cells = boxes_to_cells(ocr)
                grid = reconstruct_table(cells)
                if total > 1:
                    if pno > 1:
                        rowptr += 1
                    mark = ws.cell(rowptr, 1, f"—— 第{pno}/{total}页 ——")
                    mark.font = Font(bold=True, color="888888")
                    rowptr += 1
                for line in grid:
                    for ci, val in enumerate(line, 1):
                        if val:
                            cell = ws.cell(rowptr, ci, val)
                            cell.alignment = Alignment(vertical="center", wrap_text=True)
                    max_cols = max(max_cols, len(line))
                    rowptr += 1
        except Exception as exc:  # 单个 PDF 出错不影响整批
            ws.cell(rowptr, 1, f"[转换失败] {type(exc).__name__}: {exc}")
            print(f"      [!] 出错：{exc}")
        # 简单设个列宽
        for c in range(1, max_cols + 1):
            ws.column_dimensions[get_column_letter(c)].width = 22

    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
    wb.save(output_path)
    print(f"[✓] 完成：{output_path}")


def main():
    ap = argparse.ArgumentParser(description="批量把扫描件 PDF OCR 转 Excel（一 PDF 一工作表）")
    ap.add_argument("-i", "--input", required=True, help="PDF 文件夹（或单个 PDF）")
    ap.add_argument("-o", "--output", default="output.xlsx", help="输出 Excel 路径")
    ap.add_argument("--dpi", type=int, default=300, help="渲染分辨率，越高越清但越慢（默认300）")
    args = ap.parse_args()
    convert(args.input, args.output, args.dpi)


if __name__ == "__main__":
    main()
