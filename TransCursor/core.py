# -*- coding: utf-8 -*-
"""
整合核心：屏幕翻译器。
- 后台线程按所选触发策略截屏 -> OCR，得到全屏文本框列表（rect + text + score）
- 预翻译：每轮按鼠标距离排序，提前翻译鼠标附近 N 个文本框
- 命中测试：鼠标真正落在某个文本框内时，返回该框的原文 + 译文（供 GUI 在鼠标旁显示）

三种触发策略（见 plan.txt）：
  periodic      周期执行：每隔 scan_interval 秒跑一次 OCR
  image_diff    分析图像更新：高频截图，先判稳定再判一致性，文本变化才 OCR
  input_trigger 鼠标键盘触发+等待稳定：检测到输入事件后等待稳定再 OCR

线程安全：共享数据用 _lock 保护。所有耗时操作（截屏、OCR、翻译）都在后台线程，
主线程（GUI）只做轻量的命中测试和读取。
"""
import time
import threading
import queue
import ctypes
import numpy as np
import cv2
from mss import mss

from ocr_engine import OCREngine
from translator import Translator
import window_capture


STRATEGIES = ("periodic", "image_diff", "input_trigger")


class TextBox:
    __slots__ = ("x", "y", "w", "h", "text", "score", "translation")

    def __init__(self, x, y, w, h, text, score):
        self.x, self.y, self.w, self.h = x, y, w, h
        self.text = text
        self.score = score
        self.translation = None

    def contains(self, mx, my):
        return (self.x <= mx <= self.x + self.w and
                self.y <= my <= self.y + self.h)

    def center(self):
        return (self.x + self.w / 2.0, self.y + self.h / 2.0)

    def distance_to(self, mx, my):
        dx = max(self.x - mx, 0, mx - (self.x + self.w))
        dy = max(self.y - my, 0, my - (self.y + self.h))
        return (dx * dx + dy * dy) ** 0.5


def capture_screen():
    """截取主屏幕，返回 BGR ndarray 和屏幕几何 (left,top,width,height)。"""
    with mss() as sct:
        mon = sct.monitors[1]
        shot = sct.grab(mon)
        img = np.frombuffer(shot.rgb, dtype=np.uint8).reshape(
            shot.height, shot.width, 3)
        img_bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    return img_bgr, (mon["left"], mon["top"], mon["width"], mon["height"])


def get_mouse_pos():
    """返回鼠标在虚拟屏幕的坐标 (x, y)（与 win32 GetWindowRect / ClientToScreen 一致）。"""
    from PyQt6.QtGui import QCursor
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    pos = QCursor.pos()
    return (pos.x(), pos.y())


def image_similarity(img1, img2):
    """两张截图的相似度，0~1。用降采样灰度 MSE 归一化，越大越像。"""
    if img1 is None or img2 is None:
        return 0.0
    h = min(img1.shape[0], img2.shape[0])
    w = min(img1.shape[1], img2.shape[1])
    g1 = cv2.resize(cv2.cvtColor(img1, cv2.COLOR_BGR2GRAY), (64, 64),
                    interpolation=cv2.INTER_AREA).astype(np.float32)
    g2 = cv2.resize(cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY), (64, 64),
                    interpolation=cv2.INTER_AREA).astype(np.float32)
    mse = float(np.mean((g1 - g2) ** 2))
    return 1.0 - min(mse / (255.0 * 255.0), 1.0)


def text_edit_distance(a, b):
    """两段文本的 Levenshtein 编辑距离。"""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        ca = a[i - 1]
        for j in range(1, lb + 1):
            cost = 0 if ca == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[lb]


def _ocr_text_signature(boxes):
    """把文本框列表压成一段用于比较的签名文本。"""
    return "\n".join(b.text for b in boxes)


# Windows 虚拟键码
_VK_LBUTTON = 0x01
_VK_RETURN = 0x0D
_VK_SHIFT = 0x10
_VK_CONTROL = 0x11
_VK_MENU = 0x12


class InputMonitor:
    """轮询鼠标/键盘状态，检测触发事件。
    触发事件：按下鼠标左键、按下 Enter、松开 Ctrl/Shift/Alt。"""

    def __init__(self, trigger_left_click=True, trigger_enter=True,
                 trigger_ctrl_release=True, trigger_shift_release=True,
                 trigger_alt_release=True):
        self._prev = {}
        self._flags = {
            "left_click": trigger_left_click,
            "enter": trigger_enter,
            "ctrl_release": trigger_ctrl_release,
            "shift_release": trigger_shift_release,
            "alt_release": trigger_alt_release,
        }
        try:
            self._user32 = ctypes.windll.user32
        except Exception:
            self._user32 = None

    def _down(self, vk):
        if self._user32 is None:
            return False
        return self._user32.GetAsyncKeyState(vk) & 0x8000 != 0

    def poll(self):
        """返回 True 表示本次轮询检测到触发事件。"""
        if self._user32 is None:
            return False
        cur = {
            "lb": self._down(_VK_LBUTTON),
            "ret": self._down(_VK_RETURN),
            "ctrl": self._down(_VK_CONTROL),
            "shift": self._down(_VK_SHIFT),
            "alt": self._down(_VK_MENU),
        }
        prev = self._prev
        self._prev = cur
        triggered = False
        if self._flags["left_click"] and cur["lb"] and not prev.get("lb", False):
            triggered = True
        if self._flags["enter"] and cur["ret"] and not prev.get("ret", False):
            triggered = True
        if self._flags["ctrl_release"] and prev.get("ctrl", False) and not cur["ctrl"]:
            triggered = True
        if self._flags["shift_release"] and prev.get("shift", False) and not cur["shift"]:
            triggered = True
        if self._flags["alt_release"] and prev.get("alt", False) and not cur["alt"]:
            triggered = True
        return triggered


class ScreenTranslator:
    def __init__(self, ocr=None, translator=None,
                 strategy="periodic", pretranslate_n=8, **params):
        self.ocr = ocr or OCREngine()
        self.translator = translator or Translator()
        self.strategy = strategy if strategy in STRATEGIES else "periodic"
        self.pretranslate_n = pretranslate_n

        # periodic
        self.scan_interval = float(params.get("scan_interval", 1.0))
        # image_diff
        self.poll_interval = float(params.get("poll_interval", 0.2))
        self.stability_threshold = float(params.get("stability_threshold", 0.98))
        self.consistency_threshold = float(params.get("consistency_threshold", 0.96))
        # input_trigger
        self.trigger_delay = float(params.get("trigger_delay", 0.3))
        self.trigger_stability_threshold = float(
            params.get("trigger_stability_threshold", 0.98))
        self.text_similarity_threshold = int(
            params.get("text_similarity_threshold", 2))
        self.input_monitor = InputMonitor()

        self._lock = threading.Lock()
        self._boxes = []
        self._last_scan = 0.0
        self._running = False
        self._thread = None
        self._trans_queue = queue.Queue()
        self._trans_thread = None
        self._trans_gen = 0
        self._last_ocr_img = None      # 上一次跑 OCR 的截图（一致性比对）
        self._last_capture = None      # 上一次截图（稳定性比对）
        self._last_signature = ""      # 上一次 OCR 文本签名

        # 诊断字段（供 GUI 状态栏读取，无需加锁，近似值即可）
        self._diag_scan_count = 0      # 已完成扫描次数
        self._diag_last_duration = 0.0 # 上次 OCR 耗时(秒)
        self._diag_last_box_count = 0  # 上次框数
        self._diag_last_error = ""     # 上次错误信息
        self._diag_scanning = False    # 是否正在扫描
        self._diag_last_capture_ms = 0.0  # 上次截图耗时

        # 窗口捕获：hwnd=None 时全屏，否则捕获该窗口客户区
        self._hwnd = None
        self._win_origin = (0, 0, 0, 0)  # (left, top, w, h) 当前捕获区的屏幕原点

    def set_window(self, hwnd):
        """设置目标窗口（None=全屏）。"""
        self._hwnd = hwnd

    def clear_window(self):
        self._hwnd = None

    @property
    def hwnd(self):
        return self._hwnd

    def _capture(self):
        """按当前目标捕获图像。返回 (img_bgr, (left, top, w, h))。"""
        t0 = time.time()
        if self._hwnd:
            try:
                img, origin = window_capture.capture_window(self._hwnd)
                self._win_origin = origin
                self._diag_last_capture_ms = (time.time() - t0) * 1000
                return img, origin
            except Exception as e:
                self._diag_last_error = f"窗口捕获失败: {e}"
                print(f"[ScreenTranslator] {self._diag_last_error}，回退全屏")
                self._hwnd = None
        img, origin = capture_screen()
        self._win_origin = origin
        self._diag_last_capture_ms = (time.time() - t0) * 1000
        return img, origin

    def screen_rect_of(self, box):
        """把 OCR 局部坐标的文本框转成虚拟屏幕坐标 (sx, sy, sw, sh)。"""
        ox, oy = self._win_origin[0], self._win_origin[1]
        return (ox + box.x, oy + box.y, box.w, box.h)

    # ---- 生命周期 ----
    def start(self, preload=False):
        if self._running:
            return
        if preload:
            self.ocr.load()
            self.translator.load()
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self._trans_thread = threading.Thread(target=self._trans_loop, daemon=True)
        self._trans_thread.start()

    def stop(self):
        self._running = False
        self._trans_queue.put(None)
        if self._trans_thread:
            self._trans_thread.join(timeout=5)
            self._trans_thread = None
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def set_strategy(self, name):
        if name in STRATEGIES:
            self.strategy = name

    # ---- 后台循环（策略分发）----
    def _loop(self):
        while self._running:
            try:
                if self.strategy == "periodic":
                    self._step_periodic()
                elif self.strategy == "image_diff":
                    self._step_image_diff()
                elif self.strategy == "input_trigger":
                    self._step_input_trigger()
            except Exception as e:
                # 捕获完整异常链（PaddleOCR 会把 DependencyError 包装成 RuntimeError）
                cause = e.__cause__ or e.__context__
                if cause is not None and str(cause) and str(cause) != str(e):
                    self._diag_last_error = f"{type(e).__name__}: {e} | {type(cause).__name__}: {cause}"
                else:
                    self._diag_last_error = f"{type(e).__name__}: {e}"
                print(f"[ScreenTranslator] 扫描出错: {self._diag_last_error}")
                time.sleep(0.5)

    def _wait(self, seconds):
        """可被 stop() 打断的睡眠。"""
        end = time.time() + seconds
        while self._running and time.time() < end:
            time.sleep(min(0.05, end - time.time()))

    # ---- 周期执行 ----
    def _step_periodic(self):
        t0 = time.time()
        img, _ = self._capture()
        self._run_ocr_and_publish(img)
        self._wait(max(0.0, self.scan_interval - (time.time() - t0)))

    # ---- 分析图像更新 ----
    def _step_image_diff(self):
        img, _ = self._capture()
        # 稳定性：与上一次截图比对
        if self._last_capture is not None:
            if image_similarity(img, self._last_capture) < self.stability_threshold:
                self._last_capture = img
                self._wait(self.poll_interval)
                return
        self._last_capture = img
        # 一致性：与上一次 OCR 截图比对，相似度高说明没变化，跳过
        if self._last_ocr_img is not None:
            if image_similarity(img, self._last_ocr_img) >= self.consistency_threshold:
                self._wait(self.poll_interval)
                return
        self._run_ocr_and_publish(img)
        self._wait(self.poll_interval)

    # ---- 鼠标键盘触发+等待稳定 ----
    def _step_input_trigger(self):
        if not self.input_monitor.poll():
            time.sleep(0.03)
            return
        # 固有延迟 0.1s + 用户配置延迟
        self._wait(0.1 + self.trigger_delay)
        # 等待图像稳定
        stable_count = 0
        deadline = time.time() + 5.0
        while self._running and time.time() < deadline:
            img, _ = self._capture()
            if self._last_capture is not None and \
                    image_similarity(img, self._last_capture) >= \
                    self.trigger_stability_threshold:
                stable_count += 1
                if stable_count >= 2:
                    break
            else:
                stable_count = 0
            self._last_capture = img
            self._wait(self.poll_interval)
        if not self._running:
            return
        # 稳定后必定 OCR
        img, _ = self._capture()
        t0 = time.time()
        self._diag_scanning = True
        new_boxes = self._run_ocr(img)
        self._diag_scanning = False
        # 文本相似度门槛：与上次 OCR 结果编辑距离过小则不刷新
        new_sig = _ocr_text_signature(new_boxes)
        if (self._last_signature and
                text_edit_distance(self._last_signature, new_sig) <=
                self.text_similarity_threshold):
            return
        self._publish(new_boxes, img)
        self._diag_last_duration = time.time() - t0
        self._diag_last_box_count = len(new_boxes)
        self._diag_scan_count += 1
        self._diag_last_error = ""
        self._last_signature = new_sig

    # ---- OCR 执行 ----
    def _run_ocr(self, img):
        raw = self.ocr.run(img)
        return [TextBox(x, y, w, h, txt, s) for (x, y, w, h), txt, s in raw]

    def _run_ocr_and_publish(self, img):
        t0 = time.time()
        self._diag_scanning = True
        boxes = self._run_ocr(img)
        dt_ocr = time.time() - t0
        self._publish(boxes, img)
        self._diag_scanning = False
        dt_pub = time.time() - t0 - dt_ocr
        self._diag_last_duration = time.time() - t0
        self._diag_last_box_count = len(boxes)
        self._diag_scan_count += 1
        self._diag_last_error = ""
        self._last_signature = _ocr_text_signature(boxes)
        print(f"[ScreenTranslator] 第{self._diag_scan_count}轮完成 "
              f"OCR={dt_ocr:.2f}s publish={dt_pub:.2f}s 框={len(boxes)}",
              flush=True)

    def _publish(self, boxes, img):
        with self._lock:
            self._boxes = boxes
            self._last_scan = time.time()
        self._last_ocr_img = img
        self._pretranslate(boxes)

    def _pretranslate(self, boxes):
        if not boxes:
            return
        # 鼠标转成捕获区局部坐标，再算距离
        try:
            mx, my = get_mouse_pos()
        except Exception:
            mx, my = -1, -1
        if mx >= 0:
            ox, oy = self._win_origin[0], self._win_origin[1]
            lx, ly = mx - ox, my - oy
            ranked = sorted(boxes, key=lambda b: b.distance_to(lx, ly))
        else:
            ranked = boxes
        todo = []
        for b in ranked:
            if b.translation is None:
                todo.append(b)
            if len(todo) >= self.pretranslate_n:
                break
        if not todo:
            return
        self._trans_gen += 1
        self._trans_queue.put((self._trans_gen, todo))

    def _trans_loop(self):
        """后台翻译线程：消费翻译队列，与截图/OCR 线程互不干扰。
        每个任务带 generation 号；若已有更新的 OCR 结果，旧任务直接丢弃。"""
        while self._running:
            try:
                job = self._trans_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if job is None:
                break
            gen, todo = job
            if gen != self._trans_gen:
                continue
            texts = [b.text for b in todo]
            try:
                translations = self.translator.translate_batch(texts)
            except Exception as e:
                self._diag_last_error = f"翻译失败: {e}"
                translations = [""] * len(todo)
            for b, tr in zip(todo, translations):
                b.translation = tr

    # ---- 主线程查询接口 ----
    def get_snapshot(self):
        with self._lock:
            return list(self._boxes)

    def get_diag(self):
        """返回诊断信息字典（供 GUI 状态栏显示）。"""
        return {
            "running": self._running,
            "scanning": self._diag_scanning,
            "scan_count": self._diag_scan_count,
            "last_duration": self._diag_last_duration,
            "last_box_count": self._diag_last_box_count,
            "last_capture_ms": self._diag_last_capture_ms,
            "last_error": self._diag_last_error,
            "last_scan": self._last_scan,
            "hwnd": self._hwnd,
            "win_origin": self._win_origin,
            "ocr_loaded": self.ocr._det is not None,
            "translator_loaded": self.translator._loaded,
        }

    def hit_test(self, mx, my):
        """命中测试。mx,my 为虚拟屏幕坐标，内部转成捕获区局部坐标后比对。"""
        ox, oy = self._win_origin[0], self._win_origin[1]
        lx, ly = mx - ox, my - oy
        with self._lock:
            boxes = self._boxes
        hit = None
        for b in boxes:
            if b.contains(lx, ly):
                if hit is None or (b.w * b.h) < (hit.w * hit.h):
                    hit = b
        return hit

    def ensure_translation(self, box):
        if box.translation is not None:
            return box.translation
        tr = self.translator.translate(box.text)
        box.translation = tr
        return tr
