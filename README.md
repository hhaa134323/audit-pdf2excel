# audit-pdf2excel

把一批**扫描件 PDF**（拍照 / 扫描的图片型 PDF）通过 **OCR** 批量转成一个 Excel，
**一个 PDF 占一个工作表**，并尽量把内容还原成**表格（行列对齐）**，方便做底稿。

> 适用场景：银行结算账户清单、函证、回函、EMS 面单等**扫描 / 拍照件**归集入底稿。

## 为什么需要 OCR？
你手上的 PDF 是**扫描 / 拍照**得来的，里面没有可选中的文字（纯图片），
所以不能直接提文字，必须先用 OCR 把图片识别成文字再排成表格。

## 依赖（全部 pip 装，不需管理员权限 / 不需装外部软件）
这个工具特意选了**只需 `pip install`** 的组件，不用装 Tesseract、不用装 poppler：

| 用途 | 包 | 说明 |
|---|---|---|
| PDF 转图片 | `pymupdf` | 纯 pip，自带渲染引擎，**无需** poppler |
| OCR 识别 | `rapidocr-onnxruntime` | 纯 pip，自带中英文模型，**首次运行会下载模型（联网一次）**，之后可离线 |
| 写 Excel | `openpyxl` | |
| 数组 | `numpy` | |

## 安装 & 运行（Windows + VSCode）

1. 把仓库下载 / clone 到本地，用 VSCode 打开这个文件夹。
2. 在 VSCode 终端（`下方面板 → TERMINAL`）依次执行：
   ```powershell
   REM （可选但推荐）建个虚拟环境，不需管理员权限
   python -m venv .venv
   .venv\Scripts\activate

   REM 装依赖
   pip install -r requirements.txt
   ```
3. 把要转的 PDF 都丢进 `pdfs\` 文件夹（没有就新建一个）。
4. 运行：
   ```powershell
   python pdf2excel.py -i .\pdfs -o .\out.xlsx --dpi 300
   ```
   或者直接双击 / 运行 `run.bat`。
5. 完成后打开 `out.xlsx`，每个 PDF 一个工作表（表名 = 文件名）。

> 首次运行 RapidOCR 会下载几十 MB 模型，需要联网一次；之后完全离线跑。

## 参数
| 参数 | 默认 | 说明 |
|---|---|---|
| `-i, --input` | 必填 | PDF 文件夹（递归找子文件夹），也可以是单个 PDF |
| `-o, --output` | `output.xlsx` | 输出 Excel 路径 |
| `--dpi` | `300` | 渲染分辨率。识别不准可调高到 `400`；太慢可降到 `200` |

## 它是怎么还原表格的
1. `pymupdf` 把每页 PDF 渲染成高清图片。
2. `RapidOCR` 识别出每块文字及其**坐标框**。
3. 按文字框的 **y 坐标**聚成“行”，按 **x 左边界**聚成“列”，填成二维表格。
4. 每个 PDF 写入一个工作表；多页 PDF 会在同一表里用“—— 第N页 ——”分隔。

## 已知局限（请必读）
- OCR 不是 100% 准。扫描件模糊 / 歪斜 / 有水印时，文字可能识别错。**做底稿请务必抽检核对**。
- 表格还原是**启发式**的（靠坐标推断行列），遇到合并单元格 / 跨行文字 / 列距不均可能错位。
- 扫描件的手指、水印、条形码上的文字也可能被识别进去。

## 目录结构
```
audit-pdf2excel/
├─ pdf2excel.py        # 主脚本
├─ requirements.txt    # 依赖
├─ run.bat             # Windows 一键运行
├─ tests/
│  └─ test_reconstruct.py  # 表格重建逻辑单测（不需 OCR）
└─ README.md
```
