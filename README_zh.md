# TransCursor

[English](README.md) | [简体中文](README_zh.md)

> 基于 OCR 和本地机器翻译的实时屏幕翻译工具

## 项目简介

TransCursor 是一个离线运行的实时翻译工具。

程序会定期截取屏幕内容，通过 OCR 识别文字，再调用本地翻译模型完成翻译，并根据鼠标位置优先展示最相关的翻译结果。

与传统屏幕翻译工具不同，TransCursor 不会盲目处理整个屏幕，而是利用鼠标位置作为上下文信息，优先翻译用户正在关注的文本区域，从而降低计算开销并提升响应速度。

### 主要特性

* 完全离线运行
* PaddleOCR 文字识别
* MarianMT 本地翻译
* 鼠标位置感知
* 翻译结果缓存
* 模块化架构设计

## 工作流程

截图

↓

OCR检测

↓

文字识别

↓

机器翻译

↓

鼠标距离排序

↓

显示结果

## 适用场景

* 英文游戏辅助阅读
* 软件界面翻译
* 漫画和图片文字识别
* 外语学习

## 技术栈

### OCR

* PaddleOCR PP-OCRv6

### 翻译模型

* MarianMT
* OPUS-MT

### 开发语言

* Python

### 核心依赖

* OpenCV
* MSS
* NumPy
* PyQt6（计划中）

## 未来计划

* Overlay悬浮窗 ！
* GUI界面 ！
* GPU加速
* ONNX Runtime支持
* 热键控制
* 多语言支持
* Windows安装包 ！

## License

MIT
