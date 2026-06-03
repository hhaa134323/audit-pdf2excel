# -*- coding: utf-8 -*-
"""把表格结构（HTML <table> 或 已带行列定位的单元格列表）写成 Excel，
还原合并单元格 + 边框，尽量跟 PDF 显示一致。
这个模块不依赖 OCR / 模型，可单独单测。
"""
from __future__ import annotations
from typing import List, Tuple, Dict
import re

from bs4 import BeautifulSoup


def parse_html_table(html: str) -> Tuple[List[List[str]], List[Tuple[int, int, int, int]], int]:
    """解析 HTML 表格。

    返回：
      grid:   二维文本（只在合并单元格的左上角放文本）
      merges: [(r0, c0, r1, c1), ...]  0-based，闭区间
      ncols:  总列数
    """
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table") or soup
    rows = table.find_all("tr")

    grid: List[List[str]] = []
    merges: List[Tuple[int, int, int, int]] = []
    occupied: Dict[Tuple[int, int], bool] = {}

    def ensure(r, c):
        while len(grid) <= r:
            grid.append([])
        while len(grid[r]) <= c:
            grid[r].append("")

    for r, tr in enumerate(rows):
        cells = tr.find_all(["td", "th"])
        c = 0
        for cell in cells:
            while occupied.get((r, c)):
                c += 1
            rowspan = int(cell.get("rowspan", 1) or 1)
            colspan = int(cell.get("colspan", 1) or 1)
            text = cell.get_text(separator=" ", strip=True)
            text = re.sub(r"\s+", " ", text)
            ensure(r, c)
            grid[r][c] = text
            if rowspan > 1 or colspan > 1:
                merges.append((r, c, r + rowspan - 1, c + colspan - 1))
            for dr in range(rowspan):
                for dc in range(colspan):
                    occupied[(r + dr, c + dc)] = True
            c += colspan

    ncols = max((len(row) for row in grid), default=0)
    for row in grid:
        while len(row) < ncols:
            row.append("")
    return grid, merges, ncols


def cells_to_grid(
    cells: List[dict], n_rows: int, n_cols: int
) -> Tuple[List[List[str]], List[Tuple[int, int, int, int]], int]:
    """把带定位的单元格列表 [{r0,r1,c0,c1,text}] 转成 (grid, merges, ncols)。
    r0/r1/c0/c1 为 0-based 闭区间。文本放在左上角单元格。
    """
    n_rows = max(n_rows, 1)
    n_cols = max(n_cols, 1)
    grid = [["" for _ in range(n_cols)] for _ in range(n_rows)]
    merges: List[Tuple[int, int, int, int]] = []
    for cell in cells:
        r0, r1 = int(cell["r0"]), int(cell["r1"])
        c0, c1 = int(cell["c0"]), int(cell["c1"])
        r0 = max(0, min(r0, n_rows - 1)); r1 = max(r0, min(r1, n_rows - 1))
        c0 = max(0, min(c0, n_cols - 1)); c1 = max(c0, min(c1, n_cols - 1))
        grid[r0][c0] = cell.get("text", "") or ""
        if r1 > r0 or c1 > c0:
            merges.append((r0, c0, r1, c1))
    return grid, merges, n_cols


def _write_grid_merges(ws, grid, merges, ncols, start_row: int) -> int:
    from openpyxl.styles import Border, Side, Alignment

    thin = Side(style="thin", color="000000")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    align = Alignment(horizontal="center", vertical="center", wrap_text=True)

    if not grid:
        return start_row

    for ri, row in enumerate(grid):
        excel_row = start_row + ri
        for ci in range(ncols):
            val = row[ci] if ci < len(row) else ""
            cell = ws.cell(excel_row, ci + 1, val if val else None)
            cell.border = border
            cell.alignment = align

    for (r0, c0, r1, c1) in merges:
        ws.merge_cells(
            start_row=start_row + r0, start_column=c0 + 1,
            end_row=start_row + r1, end_column=c1 + 1,
        )
    return start_row + len(grid)


def write_table_to_sheet(ws, html: str, start_row: int = 1) -> int:
    """把 HTML 表格写到 worksheet，从 start_row 开始，返回下一个可写行号。"""
    grid, merges, ncols = parse_html_table(html)
    return _write_grid_merges(ws, grid, merges, ncols, start_row)


def write_cells_to_sheet(ws, cells: List[dict], n_rows: int, n_cols: int, start_row: int = 1) -> int:
    """把带定位的单元格列表写到 worksheet，返回下一个可写行号。"""
    grid, merges, ncols = cells_to_grid(cells, n_rows, n_cols)
    return _write_grid_merges(ws, grid, merges, ncols, start_row)
