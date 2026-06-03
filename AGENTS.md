# audit-pdf2excel · Agent 工作规则

> 适用于 Cline / Cursor / Claude Code 等本地编程 agent。Cline 会自动读 `.clinerules`，本文为通用副本。

## 运行环境
Windows 11 + VSCode，已装 Python，能跑脚本和终端命令；**不确定能否安装需要管理员权限的外部软件**。
所以：**只能用 pip 包，禁止依赖 Tesseract、poppler 等需单独安装的程序**。

## 项目目标
把一批【扫描件 / 拍照件 PDF】通过 OCR 批量转成一个 Excel：
**一个 PDF 占一个工作表**，并尽量把内容还原成表格（行列对齐），用途是做审计底稿。

## 关键背景（别走错路）
- PDF 是扫描/拍照件，里面【没有可选中的文字】，是纯图片，**必须 OCR**，不能用 pdfplumber/pypdf 直接提文字。
- RapidOCR 首次运行会联网下载模型（几十 MB），下完可离线。
- 禁止改用需要单独装程序的方案（pytesseract+Tesseract、pdf2image+poppler）。

## 技术栈
- `pymupdf`：PDF 渲染成图片（纯 pip，无需 poppler）
- `rapidocr-onnxruntime`：OCR，返回文字+坐标框（纯 pip，自带中英文模型）
- `openpyxl`：写 Excel
- 表格还原：按文字框 y 坐标聚行、x 左边界聚列

## 标准运行步骤
```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
# 把 PDF 放进 pdfs\ 文件夹
python pdf2excel.py -i .\pdfs -o .\out.xlsx --dpi 300
```
先跑逻辑单测确认环境（不依赖 OCR）：`python tests\test_reconstruct.py`（应输出 OK）。

## 参数
- `-i/--input`：PDF 文件夹（递归）或单个 PDF
- `-o/--output`：输出 Excel（默认 output.xlsx）
- `--dpi`：渲染分辨率，默认 300；识别不准调 400，太慢降 200

## 遇问题怎么办
- pip 装不上 / 没网：告诉用户，不要擅自换技术栈。
- OCR 不准：先 `--dpi 400` 重试；仍不行把该页存成图片给用户看。
- 某个 PDF 报错：脚本已对单文件容错（只影响那个 sheet），把报错贴给用户。
- 列错位严重：调 `pdf2excel.py` 里 `detect_columns` 的列聚类 gap 阈值（当前 `1.5 * 行高`），并说明改了什么。

## 验收标准
- pip install 成功，test_reconstruct.py 输出 OK。
- 对 pdfs\ 跑出 out.xlsx，每个 PDF 一个工作表，内容大体是表格形态。
- 反馈运行日志（几个 PDF、有无报错）和最终 out.xlsx 情况。

## 已知局限
- OCR 非 100% 准，水印/条码数字/手指等噪声可能被识别进去；做底稿请抽检。
- 表格还原是启发式的，合并单元格/跨行文字/列距不均可能错位。
