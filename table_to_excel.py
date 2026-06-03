# -*- coding: utf-8 -*-
"""把表格结构识别模型输出的 HTML <table>（带 rowspan/colspan）
写成 Excel，还原合并单元格 + 边框，尽量跟 PDF 显示一致。
这个模块不依赖 OCR / 模型，可单独单测。
"""
from __future__ import annotations
from typing import List, Tuple
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
    occupied = {}  # (r,c) -> True 被已放置的跨行/跨列占用

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


def write_table_to_sheet(ws, html: str, start_row: int = 1):
    """把 HTML 表格写到 worksheet，从 start_row 开始，返回下一个可写行号。"""
    from openpyxl.styles import Border, Side, Alignment

    thin = Side(style="thin", color="000000")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    align = Alignment(horizontal="center", vertical="center", wrap_text=True)

    grid, merges, ncols = parse_html_table(html)
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
