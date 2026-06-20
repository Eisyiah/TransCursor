# -*- coding: utf-8 -*-
"""
TransCursor 入口：离线屏幕翻译工具。
OCR (PP-OCRv6) + 机器翻译 (MarianMT eng->zho)，全部本地推理。
"""
import ctypes


def _set_dpi_aware():
    # 必须在创建 QApplication 之前调用，使 win32 / Qt / mss 坐标系一致（物理像素）
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE_V2
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


_set_dpi_aware()

import sys
from gui import TransCursorApp


def main():
    app = TransCursorApp(sys.argv)
    sys.exit(app.run())


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        import os
        log = os.path.join(os.path.dirname(sys.executable)
                           if getattr(sys, "frozen", False)
                           else os.path.dirname(os.path.abspath(__file__)),
                           "TransCursor_crash.log")
        with open(log, "w", encoding="utf-8") as f:
            traceback.print_exc(file=f)
        raise
