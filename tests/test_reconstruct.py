# -*- coding: utf-8 -*-
"""纯逻辑单测：不依赖 OCR / PDF 库，验证文本框 -> 表格 的重建。
运行： python -m pytest tests/  或  python tests/test_reconstruct.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pdf2excel import boxes_to_cells, reconstruct_table


def _box(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def test_three_by_three():
    ocr = [
        [_box(60, 100, 140, 130), "序号", 0.99],
        [_box(220, 100, 360, 130), "账号", 0.99],
        [_box(520, 100, 680, 130), "账户名称", 0.99],
        [_box(60, 160, 120, 190), "1", 0.99],
        [_box(220, 160, 470, 190), "95508802498624", 0.98],
        [_box(520, 160, 760, 190), "柳州华埦康医疗科技", 0.97],
        [_box(60, 220, 120, 250), "2", 0.99],
        [_box(220, 220, 470, 250), "12345678901234", 0.98],
        [_box(520, 220, 760, 250), "某某公司", 0.97],
    ]
    grid = reconstruct_table(boxes_to_cells(ocr))
    assert len(grid) == 3
    assert grid[0] == ["序号", "账号", "账户名称"]
    assert grid[1] == ["1", "95508802498624", "柳州华埦康医疗科技"]


if __name__ == "__main__":
    test_three_by_three()
    print("OK")
