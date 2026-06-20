# -*- coding: utf-8 -*-
"""
统一管理所有外部资源路径（模型文件）。

设计原则：不硬编码绝对路径。模型放在应用根目录下的 models/ 子文件夹。
- 开发模式：根目录 = 本文件所在目录（源码目录）
- 打包模式：根目录 = exe 所在目录（PyInstaller frozen 时 sys.executable 的目录）
- 可用环境变量 TRANSCURSOR_MODELS 覆盖模型目录，便于灵活部署。

目录结构（模型不打包，运行时从该目录读取）：
    <root>/
      TransCursor.exe          # 打包后
      models/
        opusmt/                # MarianMT eng->zho
        HY/                    # 腾讯混元 LLM
        OCR/
          OCRdet/              # PP-OCRv6 检测
          OCRrec/              # PP-OCRv6 识别
"""
import os
import sys


def _app_dir():
    """返回应用根目录。打包时取 exe 所在目录，开发时取源码目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


APP_DIR = _app_dir()

_env_models = os.environ.get("TRANSCURSOR_MODELS")
if _env_models:
    MODELS_DIR = os.path.abspath(_env_models)
else:
    MODELS_DIR = os.path.join(APP_DIR, "models")

# 翻译模型
OPUS_DIR = os.path.join(MODELS_DIR, "opusmt")
HY_DIR = os.path.join(MODELS_DIR, "HY")

# OCR 模型
OCR_DET_DIR = os.path.join(MODELS_DIR, "OCR", "OCRdet")
OCR_REC_DIR = os.path.join(MODELS_DIR, "OCR", "OCRrec")


def ensure_models_exist(need=("opusmt", "HY", "OCRdet", "OCRrec"), warn_only=True):
    """检查关键模型目录是否存在，缺失时返回缺失列表。
    warn_only=True 只返回列表不抛异常，便于 GUI 友好提示。"""
    mapping = {
        "opusmt": OPUS_DIR,
        "HY": HY_DIR,
        "OCRdet": OCR_DET_DIR,
        "OCRrec": OCR_REC_DIR,
    }
    missing = [k for k in need if not os.path.isdir(mapping[k])]
    return missing
