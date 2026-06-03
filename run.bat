@echo off
chcp 65001 >nul
REM 把 pdfs\ 文件夹里的所有 PDF 转成 out.xlsx（一 PDF 一工作表）
REM 第一次运行会自动下载 OCR 模型（需联网一次），之后可离线。
python pdf2excel.py -i .\pdfs -o .\out.xlsx --dpi 300
pause
