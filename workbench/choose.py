import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import bootstrap
import tkinter as tk
from tkinter import filedialog

root = tk.Tk()
root.withdraw()
root.attributes('-topmost', True)
kind = sys.argv[1]
try:
    if kind == 'psd':
        paths = list(filedialog.askopenfilenames(title='选择一份或多份 PSD', filetypes=[('Photoshop', '*.psd *.psb')]))
    elif kind == 'images':
        paths=list(filedialog.askopenfilenames(title='选择多张印花',filetypes=[('图片','*.png *.jpg *.jpeg *.tif *.tiff *.webp *.bmp')]))
    elif kind == 'folder':
        value = filedialog.askdirectory(title='选择文件夹')
        paths = [value] if value else []
    else:
        value = filedialog.askopenfilename(title='选择印花图片', filetypes=[('图片', '*.png *.jpg *.jpeg *.tif *.tiff *.webp *.bmp')])
        paths = [value] if value else []
    print(json.dumps(paths, ensure_ascii=True))
finally:
    root.destroy()
