# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from table_to_excel import parse_html_table
import pdf2excel as P


def _box(x0, y0, x1, y1, t):
    return [[[x0, y0], [x1, y0], [x1, y1], [x0, y1]], t, 0.99]


def test_parse_html_merges():
    html = (
        '<table>'
        '<tr><td colspan="2">A</td><td>B</td></tr>'
        '<tr><td>c1</td><td>c2</td><td>c3</td></tr>'
        '</table>'
    )
    grid, merges, ncols = parse_html_table(html)
    assert ncols == 3
    assert merges == [(0, 0, 0, 1)]
    assert grid[0][0] == "A"
    assert grid[1] == ["c1", "c2", "c3"]


def test_reconstruct_grid_fallback():
    ocr = [
        _box(10, 10, 60, 30, "序号"), _box(120, 10, 220, 30, "名称"), _box(300, 10, 400, 30, "金额"),
        _box(10, 50, 60, 70, "1"), _box(120, 50, 240, 70, "现金"), _box(300, 50, 400, 70, "100"),
    ]
    grid = P.reconstruct_grid(ocr)
    assert len(grid) == 2
    assert grid[0] == ["序号", "名称", "金额"]
    assert grid[1] == ["1", "现金", "100"]


if __name__ == "__main__":
    test_parse_html_merges()
    test_reconstruct_grid_fallback()
    print("ALL TESTS OK")
