# -*- mode: python ; coding: utf-8 -*-
"""TransCursor PyInstaller 打包配置。
模型不打包，运行时从 exe 同级 models/ 读取（见 paths.py）。
构建：python -m PyInstaller TransCursor.spec --noconfirm
"""
from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

block_cipher = None

all_datas = []
all_binaries = []
all_hidden = []

# ---- 只对真正需要完整收集的重型依赖用 collect_all ----
# torch / transformers：动态子模块多，必须全收集
for pkg in ("torch", "transformers"):
    try:
        d, b, h = collect_all(pkg)
        all_datas += d; all_binaries += b; all_hidden += h
    except Exception as e:
        print(f"[spec] collect_all({pkg}) 失败: {e}")

# paddleocr（transformers 引擎）：收集子模块 + 数据文件
try:
    all_hidden += collect_submodules("paddleocr")
except Exception as e:
    print(f"[spec] collect_submodules(paddleocr) 失败: {e}")

from PyInstaller.utils.hooks import collect_data_files
all_datas += collect_data_files("paddleocr")

# ---- 收集 PaddleX / PaddleOCR 及其全部依赖的 .dist-info 元数据 ----
# PaddleX 运行时用 importlib.metadata.version(dep) 检查依赖是否可用，
# PyInstaller 默认不打包元数据，导致检查失败抛 DependencyError。
# 下面列表覆盖 paddlex 的 base + ocr-core + ocr 三个 extra 的全部依赖。
_metadata_pkgs = [
    "paddlex", "paddleocr",
    # base (no extra)
    "aistudio-sdk", "chardet", "colorlog", "filelock", "huggingface-hub",
    "modelscope", "numpy", "packaging", "pandas", "pillow", "prettytable",
    "py-cpuinfo", "pydantic", "PyYAML", "requests", "ruamel.yaml",
    "typing-extensions", "ujson",
    # ocr-core
    "imagesize", "opencv-contrib-python", "pyclipper", "pypdfium2",
    "python-bidi", "shapely",
    # ocr
    "beautifulsoup4", "einops", "ftfy", "Jinja2", "latex2mathml", "lxml",
    "openpyxl", "premailer", "regex", "safetensors", "scikit-learn",
    "scipy", "sentencepiece", "tiktoken", "tokenizers",
    # 其它可能被间接检查
    "opencv-python", "PyYAML", "pyyaml", "tqdm", "joblib", "scikit-image",
    "chinese-calendar", "jieba", "pypinyin", "OpenCC", "soundfile",
    "langchain", "langchain-core", "langchain-community",
]
_seen_meta = set()
for _pkg in _metadata_pkgs:
    if _pkg in _seen_meta:
        continue
    _seen_meta.add(_pkg)
    try:
        all_datas += copy_metadata(_pkg)
    except Exception as e:
        print(f"[spec] copy_metadata({_pkg}) 失败: {e}")

# ---- 轻量依赖：靠 PyInstaller 内置 hook，只补隐藏导入 ----
# cv2 / mss / numpy / pywin32 均有内置 hook，无需 collect_all
all_hidden += [
    "cv2", "mss", "numpy",
    "win32gui", "win32ui", "win32con", "win32api", "win32com",
    "pythoncom", "pywintypes",
]

# ---- PyQt6：只用 Core/Gui/Widgets，不要 collect_all（避免 Qml/WebEngine/3D 拖累）----
all_hidden += [
    "PyQt6", "PyQt6.sip",
    "PyQt6.QtCore", "PyQt6.QtGui", "PyQt6.QtWidgets",
]

# ---- 排除：不打包模型与无关大模块 ----
# 注意：不可排除 unittest —— torch / numpy.testing 在顶层导入 unittest.mock，
# 运行 OCR 时会触发 ModuleNotFoundError。
excludes = [
    "paddle", "paddlepaddle", "paddle_serving",
    "matplotlib", "tkinter",
    "IPython", "notebook", "jupyter", "tornado",
    "PyQt5", "PySide2", "PySide6",
    "PyQt6.QtQml", "PyQt6.QtQuick", "PyQt6.QtQuick3D", "PyQt6.QtWebEngine",
    "PyQt6.QtWebEngineCore", "PyQt6.QtWebEngineWidgets", "PyQt6.QtPdf",
    "PyQt6.QtPdfWidgets", "PyQt6.Qt3D", "PyQt6.QtPositioning", "PyQt6.QtCharts",
    "PyQt6.QtMultimedia", "PyQt6.QtBluetooth", "PyQt6.QtSerialPort",
    "PyQt6.QtSensors", "PyQt6.QtTest", "PyQt6.QtXml",
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=all_binaries,
    datas=all_datas,
    hiddenimports=all_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TransCursor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,           # GUI 托盘程序，无控制台
    disable_windowed_traceback=False,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    a.zipfiles,
    [],
    name="TransCursor",
)
