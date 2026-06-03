# audit-pdf2excel · Agent 工作规则

> 适用于 Cline / Cursor / Claude Code 等本地编程 agent。Cline 会自动读 `.clinerules`，本文为通用副本。

## 运行环境
Windows 11 + VSCode，能跑脚本和终端命令；**不确定能否安装需要管理员权限的外部软件**。
所以：**只能用 pip 包，禁止依赖 Tesseract、poppler 等需单独安装的程序**。

## 装代码 / 装依赖（容易踩坑，严格按这个来）
- **获取代码别假设有 git**：用户机器不一定装了 git。先试 `git clone`，若 `git` 不可用，就改为从 https://github.com/hhaa134323/audit-pdf2excel 下载 ZIP 解压，别卡在 git clone 上反复问。
- **Python 版本用 3.10-3.12（推荐 3.12）**：OCR 依赖（onnxruntime / rapidocr-onnxruntime / wired-table-rec / lineless-table-rec）目前没有 Python 3.13 的 wheel，3.13 会装不上。建虚拟环境时显式指定：`py -3.12 -m venv .venv`。
- **务必在虚拟环境里装**：激活后再 `pip install`，否则会落到系统 Python（症状是 pip 提示 “Defaulting to user installation”）。
- **国内网络加清华镜像**：`pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt`，否则默认源经常超时（pymupdf、onnxruntime 都是几十 MB）。
- RapidOCR 首次运行会联网下载模型（几十 MB），下完可离线。

## 项目目标
把一批【扫描件 / 拍照件 PDF】通过 OCR 批量转成一个 Excel：
**一个 PDF 占一个工作表**，并尽量把内容还原成表格（行列对齐、合并单元格、边框），用途是做审计底稿。

## 关键背景（别走错路）
- PDF 是扫描/拍照件，里面【没有可选中的文字】，是纯图片，**必须 OCR**，不能用 pdfplumber/pypdf 直接提文字。
- 禁止改用需要单独装程序的方案（pytesseract+Tesseract、pdf2image+poppler）。
- 这里用到的「模型」都是 OCR、表格识别这类**识别型小模型**，不是生成式 LLM；不要引入任何会“编内容”的大模型。

## 技术栈
- `pymupdf`：PDF 渲染成图片（纯 pip，无需 poppler）
- `rapidocr-onnxruntime`：OCR，返回文字+坐标框（纯 pip，自带中英文模型）
- `wired-table-rec` / `lineless-table-rec`：表格结构识别（仅作回退方案）
- `openpyxl`：写 Excel；`beautifulsoup4`/`lxml`：解析 HTML 表格；`numpy`：坐标计算

## 识别主路（重要，别按旧的“聚行聚列”理解）
- **主路是 `reconstruct.py` 的 `reconstruct_from_ocr()`：纯几何，直接用 OCR 文本框坐标重建表格**——用“框最多”的那一行定表头和列边界，用最左列（序号）定逻辑行，每个文本框按中心点归到单元格；并处理表头换行、OCR 把跨多列文字识别成一个超宽框（按字符切列，连续数字串如账号整段保留）。
- 主路找不到明确表头时才回退到表格结构识别模型（wired/lineless），再不行回退到纯坐标拼表。对扫描件开户清单一般主路就够。
- `table_to_excel.py` 负责把单元格写进 Excel（合并单元格、边框、居中换行）。
- 没接住的文字补到表下（红字「请人工核对」）；低置信度文字（水印）不进表格，只进兜底区。

## 标准运行步骤
```powershell
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
# 把 PDF 放进 pdfs\ 文件夹
python pdf2excel.py -i .\pdfs -o .\out.xlsx --dpi 300
```
先跑逻辑单测确认环境（不依赖 OCR）：`python tests\test_reconstruct.py`（应输出 OK）。

## 参数
- `-i/--input`：PDF 文件夹（递归）或单个 PDF
- `-o/--output`：输出 Excel（默认 out.xlsx）
- `--dpi`：渲染分辨率，默认 300；识别不准调 400，太慢降 200
- `--min-score`：OCR 置信度阈值，默认 0.5；低于此值的文字（多半是水印）不进表格，只进表下兜底区
- `--debug`：导出 `<输出>.debug.json`（每页 OCR 文字+坐标+重建结果），排查列错位用

## 遇问题怎么办
- git 不可用：改下载 ZIP，别卡在 git clone。
- 装不上 / 下载超时：确认用了清华镜像、且 Python 是 3.10-3.12；3.13 装不上 OCR 依赖。
- 没激活 venv（pip 提示 Defaulting to user installation）：先 activate 再装。
- OCR 不准：先 `--dpi 400` 重试；仍不行把该页存成图片给用户看。
- 某个 PDF 报错：脚本已对单文件容错（只影响那个 sheet），把报错贴给用户。
- 列错位严重：用 `--debug` 导出真实坐标，照坐标调 `reconstruct.py` 里的列边界/超宽框拆分逻辑（`reconstruct_from_ocr` 及其辅助函数），并说明改了什么。**不要瞎猜，照 debug.json 的坐标改。**

## 验收标准
- pip install 成功，`tests\test_reconstruct.py` 输出 OK。
- 对 pdfs\ 跑出 out.xlsx，每个 PDF 一个工作表，内容大体是表格形态、账号等关键列没串列。
- 反馈运行日志（几个 PDF、有无报错）和最终 out.xlsx 情况。

## 已知局限
- OCR 非 100% 准，水印/条码数字/手指等噪声可能被识别进去；做底稿请抽检。
- 表格还原是几何启发式的，合并单元格/跨行文字/列距不均可能差一两个字，需要人工核对。
