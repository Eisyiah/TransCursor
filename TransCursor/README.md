# TransCursor

离线屏幕翻译工具。鼠标悬停在屏幕文字上即显示译文，全程本地推理，无需联网。

- **OCR**：PaddleOCR PP-OCRv6（det + rec，transformers 引擎）
- **翻译**：opusmt (MarianMT eng→zho，快) 或 混元 LLM (慢，质量好)
- **GUI**：PyQt6 托盘 + 鼠标旁浮层（圆角 / 阴影 / 玻璃质感可自定义）
- **架构**：截图线程与翻译线程解耦，互不阻塞

## 目录结构

```
TransCursor/
├── main.py              入口
├── core.py              截图/OCR/预翻译调度（截图线程 + 独立翻译线程）
├── ocr_engine.py        OCR 引擎封装
├── translator.py        翻译引擎（opusmt / hunyuan）
├── window_capture.py    窗口枚举与捕获
├── gui.py               浮层 / 设置窗口 / 托盘
├── paths.py             资源路径统一管理（无硬编码）
├── TransCursor.spec     PyInstaller 打包配置
└── models/              模型文件（不打包，运行时读取）
    ├── opusmt/          MarianMT eng→zho
    ├── HY/              腾讯混元 LLM
    └── OCR/
        ├── OCRdet/      PP-OCRv6 检测
        └── OCRrec/      PP-OCRv6 识别
```

## 资源路径

所有模型路径由 `paths.py` 统一管理，**无硬编码绝对路径**：

- 开发模式：`models/` 取源码目录下
- 打包模式：`models/` 取 exe 所在目录下
- 可用环境变量 `TRANSCURSOR_MODELS` 覆盖模型目录位置，便于灵活部署

启动时会自动检查模型目录是否存在，缺失则弹窗提示。

## 运行

```powershell
python main.py
```

## 打包成 exe

模型不打包进 exe，放在 exe 同级 `models/` 文件夹，便于单独替换/更新模型。

```powershell
pip install pyinstaller
python -m PyInstaller TransCursor.spec --noconfirm
```

产物在 `dist/TransCursor/`：

```
dist/TransCursor/
├── TransCursor.exe      主程序（无控制台，托盘 GUI）
├── _internal/           依赖运行时（PyQt6 / torch / transformers / paddleocr …）
└── models/              把模型文件夹放这里
```

把 `models/` 复制到 `dist/TransCursor/models/` 后即可整体分发，双击 `TransCursor.exe` 运行。

> 如需排错，把 `TransCursor.spec` 里 `console=False` 改成 `True` 重新打包，可在控制台看到错误输出；异常也会写入 exe 同级 `TransCursor_crash.log`。

## 使用

1. 启动后托盘出现 "T" 图标，设置窗口自动打开
2. 选择截图触发策略（周期 / 图像差异 / 鼠标键盘触发）
3. 点"启动"，首次加载模型约 20 秒
4. 鼠标移到屏幕英文文字上，浮层显示中文译文
5. "浮层外观"分组可切换预设（暗色玻璃 / 亮色玻璃 / 纯净暗色 / 极简无边）并微调圆角、阴影、玻璃、字号

## 关键设计

- **截图与翻译解耦**：OCR 线程只负责截图→OCR→把待翻译框入队，立刻进入下一轮；独立翻译线程消费队列，两者互不阻塞。用 generation 号丢弃过期翻译任务，避免给已失效的旧框浪费算力。
- **opusmt 英语过滤**：opusmt 只会英译中，对中文/日文等非 ASCII 或纯数字符号文本直接原样返回，不送模型，避免输出垃圾。
- **推理锁**：`translator.py` 用 `_infer_lock` 串行化模型推理，预翻译线程与 GUI 按需翻译线程不会同时调用 GPU。
