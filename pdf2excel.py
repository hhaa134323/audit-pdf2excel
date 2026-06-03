# -*- coding: utf-8 -*-
"""
PDF 批量转 Excel（一个 PDF 一个工作表）。

重点：尽量还原 PDF 里的表格显示格式（行列、合并单元格、边框），
同时保证不丢内容：即使表格模型在低质量扫描件上漏识了某些格，
也会把未归入表格的 OCR 文字补在下面，供人工核对。

识别思路：
1. PyMuPDF 把每页渲染成图。
2. RapidOCR 识别文字。
3. 表格结构识别（wired_table_rec / lineless_table_rec）把 OCR 结果
   组成带 rowspan/colspan 的 HTML 表格。
4. table_to_excel 把 HTML 表格写成 Excel，还原合并单元格和边框。
5. 把表格没接住的 OCR 文字补在表下面（保证不丢）。
6. 若某页根本没检测到表格（比如快递面单、无线文本），回退到按坐标启发式拼表。

环境：Windows + VSCode，只能 pip 装包，首次联网下模型，之后可离线。
"""
from __future__ import annotations

import argparse
import importlib
import os
import re
from dataclasses import dataclass
from typing import List, Optional

import numpy as np


# ----------------------------------------------------------------------------
# 渲染 PDF
# ----------------------------------------------------------------------------
def render_pdf_pages(path: str, dpi: int = 300):
    import fitz  # PyMuPDF

    doc = fitz.open(path)
    total = doc.page_count
    scale = dpi / 72.0
    for i in range(total):
        page = doc[i]
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
        img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n == 4:
            img = img[:, :, :3]
        elif pix.n == 1:
            img = np.repeat(img, 3, axis=2)
        yield i, total, img[:, :, ::-1].copy()  # BGR
    doc.close()


# ----------------------------------------------------------------------------
# OCR（RapidOCR）
# ----------------------------------------------------------------------------
_OCR_ENGINE = None


def get_ocr_engine():
    global _OCR_ENGINE
    if _OCR_ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR
        _OCR_ENGINE = RapidOCR()
    return _OCR_ENGINE


def ocr_image(img_bgr) -> list:
    """返回 RapidOCR 格式：[[box(4点), text, score], ...]，没识别到返回 []。"""
    engine = get_ocr_engine()
    result, _ = engine(img_bgr)
    return result or []


# ----------------------------------------------------------------------------
# 表格结构识别 → HTML
# 重要：WiredTableRecognition / LinelessTableRecognition 不是无参构造，
# 需要传 WiredTableInput / LinelessTableInput（配置类常在 .main 里，不一定从包根导出）。
# 这里按多种可能的 API 形状依次尝试，兼容不同版本。
# ----------------------------------------------------------------------------
_WIRED = None
_LINELESS = None


def _build_engine(kind: str):
    """构造表格识别引擎，兼容：
      - WiredTableRecognition(WiredTableInput())   # 新版，需配置对象
      - WiredTableRecognition()                    # 旧版，无参
    配置类优先从包根找，找不到再从 .main 找。
    """
    if kind == "wired":
        pkg_name, rec_name, input_name = "wired_table_rec", "WiredTableRecognition", "WiredTableInput"
    else:
        pkg_name, rec_name, input_name = "lineless_table_rec", "LinelessTableRecognition", "LinelessTableInput"

    pkg = importlib.import_module(pkg_name)
    rec_cls = getattr(pkg, rec_name, None)
    input_cls = getattr(pkg, input_name, None)

    # 配置类 / 识别类 可能只在 .main 里
    if rec_cls is None or input_cls is None:
        try:
            main_mod = importlib.import_module(pkg_name + ".main")
            rec_cls = rec_cls or getattr(main_mod, rec_name, None)
            input_cls = input_cls or getattr(main_mod, input_name, None)
        except Exception:
            pass

    if rec_cls is None:
        raise ImportError(f"找不到 {rec_name}，请确认已 pip install {pkg_name}")

    last_err = None
    # 优先用配置对象构造（新版），再回退到无参（旧版）
    if input_cls is not None:
        try:
            return rec_cls(input_cls())
        except Exception as e:
            last_err = e
    try:
        return rec_cls()
    except Exception as e:
        last_err = e
    raise last_err


def _load_wired():
    global _WIRED
    if _WIRED is None:
        _WIRED = _build_engine("wired")
    return _WIRED


def _load_lineless():
    global _LINELESS
    if _LINELESS is None:
        _LINELESS = _build_engine("lineless")
    return _LINELESS


def _extract_html(ret) -> Optional[str]:
    """从不同版本的返回值里拿出 HTML 字符串。"""
    if ret is None:
        return None
    # 新版：输出对象带 .pred_html
    html = getattr(ret, "pred_html", None)
    if html:
        return html
    # 旧版：(html, elapse, polygons, logic_points, ocr_res) 元组
    if isinstance(ret, (tuple, list)) and ret:
        first = ret[0]
        if isinstance(first, str) and "<" in first:
            return first
    if isinstance(ret, str) and "<" in ret:
        return ret
    return None


def recognize_table_html(img_bgr, ocr_result: list, prefer: str = "wired") -> Optional[str]:
    """返回表格 HTML；失败或没检测到表格返回 None。"""
    order = ["wired", "lineless"] if prefer == "wired" else ["lineless", "wired"]
    for kind in order:
        try:
            engine = _load_wired() if kind == "wired" else _load_lineless()
        except Exception as e:
            print(f"    [表格识别-{kind}] 加载失败，跳过：{e}")
            continue
        try:
            try:
                ret = engine(img_bgr, ocr_result=ocr_result)
            except TypeError:
                ret = engine(img_bgr)
            html = _extract_html(ret)
            if html and "<td" in html.lower():
                return html
        except Exception as e:
            print(f"    [表格识别-{kind}] 运行出错，跳过：{e}")
    return None


# ----------------------------------------------------------------------------
# 不丢内容：找出表格没接住的 OCR 文字
# ----------------------------------------------------------------------------
def _norm(s) -> str:
    return re.sub(r"\s+", "", str(s))


def find_missing_texts(html: str, ocr_result: list) -> List[str]:
    """返回 OCR 识别到、但没出现在表格 HTML 里的文字（去重后）。"""
    from bs4 import BeautifulSoup
    table_text = _norm(BeautifulSoup(html, "lxml").get_text())
    missing, seen = [], set()
    for item in ocr_result:
        if len(item) < 2:
            continue
        raw = str(item[1]).strip()
        key = _norm(raw)
        if not key or key in seen:
            continue
        if key not in table_text:
            missing.append(raw)
            seen.add(key)
    return missing


# ----------------------------------------------------------------------------
# 回退方案：按坐标启发式拼表（无线 / 模型不可用时）
# ----------------------------------------------------------------------------
@dataclass
class Box:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def cy(self):
        return (self.y0 + self.y1) / 2

    @property
    def h(self):
        return self.y1 - self.y0


def _to_boxes(ocr_result: list) -> List[Box]:
    boxes = []
    for item in ocr_result:
        try:
            quad, text = item[0], item[1]
            xs = [p[0] for p in quad]
            ys = [p[1] for p in quad]
            boxes.append(Box(str(text), min(xs), min(ys), max(xs), max(ys)))
        except Exception:
            continue
    return boxes


def reconstruct_grid(ocr_result: list) -> List[List[str]]:
    """按行聚类 + 列聚类的启发式表格（不丢文字）。"""
    boxes = _to_boxes(ocr_result)
    if not boxes:
        return []
    boxes.sort(key=lambda b: b.cy)
    med_h = float(np.median([b.h for b in boxes if b.h > 0]) or 12)

    rows: List[List[Box]] = []
    cur: List[Box] = [boxes[0]]
    for b in boxes[1:]:
        if abs(b.cy - cur[-1].cy) <= med_h * 0.7:
            cur.append(b)
        else:
            rows.append(cur); cur = [b]
    rows.append(cur)

    lefts = sorted(b.x0 for b in boxes)
    cols: List[float] = [lefts[0]]
    for x in lefts[1:]:
        if x - cols[-1] > med_h * 1.5:
            cols.append(x)
    ncols = len(cols)

    def col_of(b: Box) -> int:
        return min(range(ncols), key=lambda i: abs(b.x0 - cols[i]))

    grid: List[List[str]] = []
    for row in rows:
        line = [""] * ncols
        for b in sorted(row, key=lambda b: b.x0):
            ci = col_of(b)
            line[ci] = (line[ci] + " " + b.text).strip() if line[ci] else b.text
        grid.append(line)
    return grid


# ----------------------------------------------------------------------------
# 写 Excel
# ----------------------------------------------------------------------------
def sanitize_sheet_name(name: str) -> str:
    name = re.sub(r"[\\/?*\[\]:]", "_", name)
    name = name.strip() or "Sheet"
    return name[:31]


def _thin_border():
    from openpyxl.styles import Border, Side
    thin = Side(style="thin", color="000000")
    return Border(left=thin, right=thin, top=thin, bottom=thin)


def _write_grid_with_borders(ws, grid: List[List[str]], start_row: int = 1) -> int:
    from openpyxl.styles import Alignment
    border = _thin_border()
    align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ncols = max((len(r) for r in grid), default=0)
    for ri, row in enumerate(grid):
        for ci in range(ncols):
            val = row[ci] if ci < len(row) else ""
            cell = ws.cell(start_row + ri, ci + 1, val if val else None)
            cell.border = border
            cell.alignment = align
    return start_row + len(grid)


def _write_missing_block(ws, missing: List[str], start_row: int) -> int:
    """把未归入表格的文字补在下面，加一个提示抬头。"""
    if not missing:
        return start_row
    from openpyxl.styles import Font
    r = start_row + 1  # 空一行
    c = ws.cell(r, 1, "↓ 以下为未归入表格的识别文字（请人工核对）")
    c.font = Font(bold=True, color="C00000")
    r += 1
    for t in missing:
        ws.cell(r, 1, t)
        r += 1
    return r


def find_pdfs(input_path: str) -> List[str]:
    if os.path.isfile(input_path) and input_path.lower().endswith(".pdf"):
        return [input_path]
    out = []
    for root, _, files in os.walk(input_path):
        for f in files:
            if f.lower().endswith(".pdf"):
                out.append(os.path.join(root, f))
    return sorted(out)


def convert(input_path: str, output_path: str, dpi: int = 300):
    from openpyxl import Workbook
    from table_to_excel import write_table_to_sheet

    pdfs = find_pdfs(input_path)
    if not pdfs:
        print(f"没找到 PDF：{input_path}")
        return

    wb = Workbook()
    wb.remove(wb.active)
    used_names = set()

    for pdf in pdfs:
        base = os.path.splitext(os.path.basename(pdf))[0]
        name = sanitize_sheet_name(base)
        n, k = name, 1
        while n in used_names:
            k += 1
            n = sanitize_sheet_name(f"{base}_{k}")
        used_names.add(n)
        ws = wb.create_sheet(n)
        print(f"\n处理：{pdf} -> 工作表 [{n}]")

        row_cursor = 1
        for pno, total, img in render_pdf_pages(pdf, dpi=dpi):
            print(f"  第 {pno + 1}/{total} 页 OCR...")
            ocr_result = ocr_image(img)
            if not ocr_result:
                print("    （未识别到文字）")
                continue

            html = recognize_table_html(img, ocr_result, prefer="wired")
            if html:
                missing = find_missing_texts(html, ocr_result)
                print(f"    ✓ 表格结构识别成功；未归入文字 {len(missing)} 条")
                row_cursor = write_table_to_sheet(ws, html, start_row=row_cursor)
                row_cursor = _write_missing_block(ws, missing, row_cursor)
                row_cursor += 1
            else:
                print("    · 未检测到表格，回退到坐标拼表")
                grid = reconstruct_grid(ocr_result)
                if grid:
                    row_cursor = _write_grid_with_borders(ws, grid, start_row=row_cursor) + 1

        # 列宽粗调
        for col in ws.columns:
            try:
                letter = col[0].column_letter
            except Exception:
                continue
            maxlen = max((len(str(c.value)) for c in col if c.value), default=0)
            ws.column_dimensions[letter].width = min(40, max(8, maxlen + 2))

    wb.save(output_path)
    print(f"\n完成：{output_path}")


def main():
    ap = argparse.ArgumentParser(description="PDF 批量转 Excel（还原表格格式，不丢内容）")
    ap.add_argument("-i", "--input", required=True, help="PDF 文件或目录")
    ap.add_argument("-o", "--output", default="out.xlsx", help="输出 xlsx")
    ap.add_argument("--dpi", type=int, default=300, help="渲染 DPI（默认 300，扫描件不清晰可调 400）")
    args = ap.parse_args()
    convert(args.input, args.output, dpi=args.dpi)


if __name__ == "__main__":
    main()
