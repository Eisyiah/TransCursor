---
name: pyinstaller-package
description: Use when packaging a Python app with PyInstaller, especially PyQt6 + torch + transformers + PaddleOCR apps on Windows. Covers .spec writing, common runtime errors after packaging, and how to debug them. Trigger on words like 打包, exe, PyInstaller, 分发, No module named, RuntimeError after build.
---

# PyInstaller 打包技能

打包 Python 应用为 Windows exe 的实战指南，重点记录 PyQt6 + torch + transformers + PaddleOCR 这类重型依赖的坑与解法。

## 基本流程

```powershell
pip install pyinstaller
python -m PyInstaller <name>.spec --noconfirm
```

产物在 `dist/<name>/`：`<name>.exe` + `_internal/`（依赖运行时）。**运行 dist 里的 exe，不要运行 build 里的中间 exe**（build 只有 exe 没有 _internal，会报 `Failed to load Python DLL`）。打包后可删 build/ 避免误用。

## .spec 文件要点

用 .spec 而非 CLI 参数，便于版本管理和精细控制。核心结构：

```python
from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata, collect_data_files

all_datas = []; all_binaries = []; all_hidden = []

# 重型动态依赖（torch/transformers）必须 collect_all：收集全部子模块+数据+二进制
for pkg in ("torch", "transformers"):
    d, b, h = collect_all(pkg)
    all_datas += d; all_binaries += b; all_hidden += h

# 只需子模块/数据文件的包
all_hidden += collect_submodules("paddleocr")
all_datas += collect_data_files("paddleocr")

# 元数据（见下方"依赖元数据缺失"坑）
for pkg in ("paddlex", "paddleocr", "numpy", "pandas", ...):
    try: all_datas += copy_metadata(pkg)
    except Exception: pass

a = Analysis(["main.py"], binaries=all_binaries, datas=all_datas,
             hiddenimports=all_hidden, excludes=[...])
pyz = PYZ(a.pure, a.zipped_data)
exe = EXE(pyz, a.scripts, exclude_binaries=True, name="App",
          console=False, ...)  # GUI 用 False；排错时临时改 True
coll = COLLECT(exe, a.binaries, a.datas, a.zipfiles, [], name="App")
```

### 性能优化

- **不要对 PyQt6 用 `collect_all`**：它会收集 Qml/Quick/WebEngine/3D 等全部子模块，构建耗时翻倍且产物臃肿。只用 QtCore/Gui/Widgets 时，改为：
  ```python
  all_hidden += ["PyQt6", "PyQt6.sip", "PyQt6.QtCore", "PyQt6.QtGui", "PyQt6.QtWidgets"]
  excludes += ["PyQt6.QtQml", "PyQt6.QtQuick", "PyQt6.QtWebEngine", "PyQt6.Qt3D", ...]
  ```
- **轻量依赖靠内置 hook**：cv2/mss/numpy/pywin32 有 PyInstaller 内置 hook，只补 hiddenimports 即可，无需 collect_all。

## 资源路径（不打包模型等大文件）

模型/数据文件不打包进 exe，放 exe 同级目录运行时读取。用统一路径模块管理，**绝不硬编码绝对路径**：

```python
# paths.py
import os, sys
def _app_dir():
    return os.path.dirname(sys.executable) if getattr(sys, "frozen", False) \
           else os.path.dirname(os.path.abspath(__file__))
APP_DIR = _app_dir()
MODELS_DIR = os.environ.get("APP_MODELS") or os.path.join(APP_DIR, "models")
```

- `sys.frozen` 为 True 表示 PyInstaller 打包环境，取 `sys.executable` 目录
- 用环境变量覆盖路径，便于灵活部署
- 启动时检查目录是否存在，缺失则弹窗提示

## 调试打包后的崩溃

1. **临时开控制台**：spec 里 `console=False` 改 `True` 重新打包，stderr 直接可见
2. **写崩溃日志**：main.py 包 try/except，异常写 exe 同级 `crash.log`：
   ```python
   if __name__ == "__main__":
       try: main()
       except Exception:
           import traceback, os, sys
           log = os.path.join(os.path.dirname(sys.executable) if getattr(sys,"frozen",False)
                              else os.path.dirname(os.path.abspath(__file__)), "app_crash.log")
           open(log, "w", encoding="utf-8").write(traceback.format_exc()); raise
   ```
3. **看 warn-<name>.txt**：build 目录里的警告文件，记录所有 missing module，是诊断 ImportError 的第一手资料
4. **被 try/except 吞掉的错误**：应用自身的后台循环常捕获异常只显示状态栏，改进错误捕获显示完整异常链：
   ```python
   except Exception as e:
       cause = e.__cause__ or e.__context__
       msg = f"{e} | {cause}" if cause and str(cause) != str(e) else str(e)
   ```

## 已知坑与解决方案（按踩坑顺序）

### 坑1：误运行 build 目录的 exe

**现象**：`Failed to load Python DLL ...build\..._internal\python3xx.dll`，LoadLibrary 找不到模块。

**原因**：运行了 `build/<name>/<name>.exe`——那是 PyInstaller 中间产物，只有 exe 没有 `_internal` 运行时。真正的成品在 `dist/<name>/`。

**解决**：运行 `dist/<name>/<name>.exe`。打包后删除 build/ 目录避免混淆。

### 坑2：PaddleOCR 的 PDX 重复初始化

**现象**：`RuntimeError: PDX has already been initialized. Reinitialization is not supported.`

**原因**：PaddleOCR 3.7 底层 PaddleX 默认 `EAGER_INITIALIZATION=True`，import paddlex 时自动调 `initialize()` 标记已初始化；之后 paddleocr 创建 TextDetection/TextRecognition 时再调 `initialize()` 就冲突。源码在 `paddlex/__init__.py:41` 和 `paddlex/utils/flags.py:52`。

**解决**：在导入 paddleocr **之前**设环境变量关闭抢先初始化：
```python
import os
os.environ.setdefault("PADDLE_PDX_EAGER_INIT", "0")
import paddleocr  # 必须在 setenv 之后
```
验证：`paddlex.repo_manager.core._GlobalContext.is_initialized()` 应返回 False。

### 坑3：排除 unittest 导致 OCR 崩溃

**现象**：`ModuleNotFoundError: No module named 'unittest'`，OCR 扫描时触发。

**原因**：spec 的 `excludes` 里写了 `"unittest"`，但 `torch._guards`、`numpy.testing._private.utils` 等模块在**顶层** `import unittest.mock`/`unittest.case`。运行时 torch 被实际调用即触发该导入。在 `build/<name>/warn-<name>.txt` 里能看到 `missing module named 'unittest.mock'`。

**解决**：从 `excludes` 移除 `"unittest"`（和 `"test"`）。Python 标准库很小，不值得为省那点空间冒风险。只排除真正无关的第三方大模块（matplotlib/tkinter/IPython/jupyter 等）。

### 坑4：PaddleX 依赖元数据缺失

**现象**：`RuntimeError: A dependency error occurred during predictor creation. Please refer to the installation documentation to ensure all required dependencies are installed.`

**原因**：PaddleX 运行时用 `importlib.metadata.version(dep)` 检查依赖是否可用（`paddlex/utils/deps.py:112`）。PyInstaller 默认**不打包**包的 `.dist-info` 元数据目录，导致 `version()` 返回 None → 判定依赖缺失 → 抛 `DependencyError`，被 `paddleocr/_models/base.py:81` 包装成上面的 RuntimeError，真实原因被吞。

**解决**：用 `copy_metadata()` 收集 paddlex 及其全部依赖的 `.dist-info`：
```python
from PyInstaller.utils.hooks import copy_metadata
for pkg in ("paddlex", "paddleocr", "numpy", "pandas", "pillow", "packaging",
            "pydantic", "pyyaml", "requests", "scipy", "shapely", "pyclipper",
            "imagesize", "opencv-contrib-python", "huggingface-hub", ...):
    try: all_datas += copy_metadata(pkg)
    except Exception: pass  # 未安装的可选包跳过
```
查 paddlex 依赖列表：`python -c "import importlib.metadata as m; [print(r) for r in m.requires('paddlex')]"`，覆盖 base + 你用的 extra（如 ocr-core/ocr）。验证：`dist/<name>/_internal` 下应有 40+ 个 `*.dist-info` 目录。

## 分发

整个 `dist/<name>/` 文件夹原样给对方（exe + _internal + 模型目录，缺一不可）。目标机无需装 Python，Win10/11 64 位即可，缺 VC++ 运行库时装 vc_redist.x64.exe。更新时：换程序只替换 exe+_internal，换模型只替换模型目录。

## 验证清单

打包后逐项确认：
- [ ] `dist/<name>/<name>.exe` 存在，`_internal/python3xx.dll` 存在
- [ ] 删除 build/ 目录
- [ ] 控制台模式（console=True）启动不报错，再切回 console=False 正式构建
- [ ] 把模型/资源放 exe 同级目录，不带环境变量启动验证路径解析
- [ ] 跑足够久（30s+）让模型加载并触发实际推理，确认无运行时错误
- [ ] 检查 crash.log 不存在
