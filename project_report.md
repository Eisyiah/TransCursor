# 项目结构报告 — TransCursor

**生成日期**: 2026-06-19  
**项目路径**: `E:\Anontranslater2`

---

## 1. 项目概述

**TransCursor** 是一个离线屏幕翻译工具原型，通过 **OCR（光学字符识别）+ 机器翻译** 实现实时游戏/应用文本翻译。所有推理均在本地执行，无需网络 API 调用。

### 核心流程

```
截屏 (mss) → 文本检测 (PP-OCRv6_det) → 文本识别 (PP-OCRv6_rec)
    → 按鼠标距离排序 → 批量预翻译 (MarianMT eng→zho) → 鼠标命中测试取结果
```

---

## 2. 目录结构

```
E:\Anontranslater2\
│
├── TransCursor\                    # 主应用源码 (Python 3.14)
│   ├── core.py                     # 核心调度：截图→OCR→预翻译→命中测试
│   ├── ocr_engine.py               # OCR引擎封装 (PaddleOCR 检测+识别)
│   ├── translator.py               # 翻译引擎封装 (MarianMT eng→zho)
│   ├── test_core.py                # 集成测试 (无GUI, 12秒循环)
│   ├── test_ocr.py                 # OCR可行性验证
│   ├── test_ocr_image.py           # 指定图片 OCR 测试
│   ├── test_engines.py             # OCR + 翻译组合冒烟测试
│   ├── test_trans.py               # 翻译模块验证
│   ├── smoke_ocr.py                # 快速 OCR 冒烟 (合成图片)
│   ├── core_test_log.txt           # test_core 运行日志
│   ├── ocr_test_output.png         # OCR 测试可视化输出
│   └── __pycache__\                # Python 3.14 字节码缓存
│
├── opusmt\                         # 翻译模型 (Helsinki-NLP OPUS-MT)
│   ├── config.json                 # Transformer 6层, d_model=512
│   ├── generation_config.json      # beam=4, max_length=512
│   ├── tokenizer_config.json       # source_lang=eng, target_lang=zho
│   ├── vocab.json                  # 65001 tokens
│   ├── source.spm / target.spm     # SentencePiece 分词模型
│   ├── pytorch_model.bin           # PyTorch 权重
│   └── README.md
│
├── OCR\                            # OCR 模型 (PaddleOCR PP-OCRv6)
│   ├── OCRdet\                     # 文本检测 (PP-OCRv6_small_det)
│   │   ├── config.json             # 骨干: LCNetV4, 2.48M 参数
│   │   ├── preprocessor_config.json
│   │   ├── inference.yml           # PaddleOCR 推理流水线
│   │   ├── model.safetensors       # safetensors 权重
│   │   └── README.md
│   └── OCRrec\                     # 文本识别 (PP-OCRv6_small_rec)
│       ├── config.json             # 骨干: LCNetV4, 5.2M 参数
│       ├── preprocessor_config.json # 含 18710 字符表
│       ├── inference.yml
│       ├── model.safetensors
│       └── README.md
│
├── plan.txt                        # 设计文档 (四种截图触发策略)
├── pip_list.txt                    # Python 依赖清单
├── 11.jpg                          # 测试图片
├── E：Anontranslaterpip_list.txt   # 同级项目 pip list 副本
└── project_report.md               # 本报告文件
```

---

## 3. 关键技术栈

| 类别 | 技术 | 版本 |
|------|------|------|
| 语言 | Python | 3.14 |
| 深度学习 | PyTorch | 2.12.0 |
| OCR | PaddleOCR (transformers 后端) | 3.7.0 |
| 翻译 | HuggingFace Transformers (MarianMT) | 5.12.1 |
| 截图 | MSS | 10.2.0 |
| 图像处理 | OpenCV | 4.13.0.92 |
| GUI/鼠标 | PyQt6 | 6.11.0 |
| 分词 | SentencePiece | 0.2.1 |
| 模型格式 | safetensors + PyTorch bin | — |

---

## 4. 核心模块详解

### 4.1 `core.py` — 核心调度器 (`ScreenTranslator`)

- **后台线程**：以 `scan_interval`（默认 1s）循环执行截图 → OCR → 预翻译
- **预翻译策略**：按文本块到鼠标距离排序，优先翻译最近的 N 个（默认 8）
- **命中测试**：主线程调用 `hit_test(mx, my)` 检查鼠标是否在某个文本块内
- **线程安全**：使用 `threading.Lock()` 保护共享数据 `_boxes`
- **数据类 `TextBox`**：`x, y, w, h, text, score, translation`，启用 `__slots__` 优化

### 4.2 `ocr_engine.py` — OCR 引擎 (`OCREngine`)

- 延迟加载（首次调用 `run()` 时才加载模型）
- 检测参数：`thresh=0.30`, `box_thresh=0.60`, `unclip_ratio=1.5`
- 识别分数阈值：`0.00`（不过滤任何结果）

### 4.3 `translator.py` — 翻译引擎 (`Translator`)

- 延迟加载，自动检测 CUDA/CPU
- 批量翻译，支持 `num_beams=4` 波束搜索
- LRU 缓存（容量 2048），避免重复翻译

---

## 5. 设计特点与模式

| 模式 | 说明 |
|------|------|
| **延迟加载** | 模型首次使用时才加载，加快启动速度 |
| **线程安全** | Lock 保护共享状态，后台线程异常被捕获 |
| **读写分离** | 后台线程做推理，主线程仅轻量查询 |
| **LRU 缓存** | 翻译结果缓存，自动淘汰旧条目 |
| **模块解耦** | core/ocr/translator 职责清晰，独立模块 |
| **硬编码路径** | 模型路径指向 `E:\Anontranslater\...` |

---

## 6. 项目状态

- **阶段**：原型/早期开发
- **已有**：完整核心逻辑、模型加载、OCR+翻译流水线
- **待完成**：GUI 界面、截图触发策略（`plan.txt` 中设计了四种方案）
- **测试**：多个独立和集成测试文件已验证流水线可用性
