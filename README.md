# TransCursor

> Real-time offline screen translation with cursor-aware text detection.

TransCursor is a lightweight desktop translation tool that performs OCR and machine translation directly on your local machine. Instead of translating the entire screen, it prioritizes text near the mouse cursor, providing fast contextual translations while reducing unnecessary computation.

## Features

* 🖱️ **Cursor-Aware Translation**

  * Detects text regions on screen.
  * Prioritizes text closest to the mouse cursor.
  * Reduces translation latency and resource usage.

* 🌐 **Fully Offline**

  * No cloud API required.
  * All OCR and translation models run locally.

* ⚡ **Real-Time Processing**

  * Periodic screen capture.
  * Automatic OCR recognition.
  * Instant translation display.

* 📦 **Translation Cache**

  * Built-in LRU cache for repeated text.
  * Avoids redundant model inference.

* 🔧 **Modular Architecture**

  * OCR engine and translation engine are decoupled.
  * Easy to replace OCR models or translation backends.

---

## How It Works

```text
Screen Capture
      │
      ▼
Text Detection (OCR)
      │
      ▼
Text Recognition
      │
      ▼
Translation
      │
      ▼
Distance Ranking
(Cursor Proximity)
      │
      ▼
Display Translation
```

TransCursor continuously captures the screen, extracts text using OCR, translates recognized text, and ranks translation candidates according to their distance from the mouse cursor.

---

## Tech Stack

### OCR

* PaddleOCR (PP-OCRv6)

### Translation

* Hugging Face MarianMT
* OPUS-MT models

### Core Libraries

* Python
* OpenCV
* MSS
* NumPy

### GUI (Planned)

* PyQt6

---

## Project Structure

```text
TransCursor/
│
├── core.py
├── ocr_engine.py
├── translator.py
│
├── test_*.py
│
├── OCR/
│   ├── OCRdet/
│   └── OCRrec/
│
└── opusmt/
```

### Components

| Module        | Description                          |
| ------------- | ------------------------------------ |
| core.py       | Main processing pipeline             |
| ocr_engine.py | OCR wrapper and text extraction      |
| translator.py | Translation model interface          |
| OCR/          | OCR detection and recognition models |
| opusmt/       | Translation models                   |

---

## Installation

### Clone Repository

```bash
git clone https://github.com/Eisyiah/TransCursor.git
cd TransCursor
```

### Create Virtual Environment

```bash
python -m venv venv
```

Windows:

```bash
venv\Scripts\activate
```

Linux/macOS:

```bash
source venv/bin/activate
```

### Install Dependencies

```bash
pip install -r requirements.txt
```

---

## Usage

Run:

```bash
python core.py
```

The application will:

1. Capture the screen periodically.
2. Detect and recognize text.
3. Translate recognized text.
4. Rank text regions by cursor proximity.
5. Display the most relevant translation.

---

## Performance Optimizations

### Lazy Loading

Models are loaded only when first used.

### Translation Cache

Frequently repeated text is cached using an LRU strategy.

### Cursor-Based Prioritization

Instead of translating every detected text region equally, TransCursor focuses on text most likely relevant to the user.

---

## Use Cases

* Visual Novels
* Japanese Games
* RPG Dialogue Translation
* Software Interface Translation
* Educational Reading
* Manga / Comic OCR Experiments

---

## Roadmap

* [ ] Overlay translation window
* [ ] PyQt6 desktop GUI
* [ ] Region-of-interest OCR
* [ ] GPU acceleration
* [ ] ONNX Runtime support
* [ ] Multi-language translation
* [ ] Hotkey support
* [ ] Text change detection
* [ ] Packaging and installer

---

## Disclaimer

This project is intended for educational and research purposes. OCR accuracy and translation quality depend on the selected models and source material.

---

## License

MIT License
