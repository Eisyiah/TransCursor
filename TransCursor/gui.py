# -*- coding: utf-8 -*-
"""
GUI：翻译浮层 + 设置窗口 + 系统托盘。
- TranslationOverlay：鼠标旁的无边框、点击穿透、置顶浮层，显示译文
- SettingsWindow：策略与参数配置，启停控制
- TransCursorApp：主控，轮询鼠标命中测试，驱动浮层显示与按需翻译
"""
import threading

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject, QPoint, QRect
from PyQt6.QtGui import (
    QColor, QPainter, QFont, QPixmap, QIcon, QCursor, QCloseEvent, QPen,
    QLinearGradient,
)
from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QMainWindow, QVBoxLayout, QHBoxLayout,
    QFormLayout, QComboBox, QDoubleSpinBox, QSpinBox, QPushButton,
    QCheckBox, QSystemTrayIcon, QMenu, QFrame, QGroupBox,
    QDialog, QListWidget, QListWidgetItem, QDialogButtonBox,
)
from core import ScreenTranslator, get_mouse_pos, STRATEGIES
import window_capture


def _screen_origin():
    app = QApplication.instance()
    if app is None:
        return QPoint(0, 0)
    geo = app.primaryScreen().geometry()
    return geo.topLeft()


# ---- 浮层外观预设 ----
# 每个预设定义圆角、背景、边框、阴影、玻璃渐变、文字等全部视觉参数。
OVERLAY_PRESETS = {
    "暗色玻璃": {
        "corner_radius": 10, "bg_color": "#1c1c22", "bg_opacity": 0.80,
        "border_color": "#ffffff", "border_opacity": 0.16, "border_width": 1,
        "shadow": True, "shadow_color": "#000000", "shadow_opacity": 0.50,
        "shadow_blur": 18, "shadow_offset_x": 0, "shadow_offset_y": 4,
        "glass": True, "glass_top": "#3b3b46", "glass_bottom": "#16161c",
        "text_color": "#f5f5f5", "font_size": 14,
        "padding_h": 10, "padding_v": 8,
    },
    "亮色玻璃": {
        "corner_radius": 12, "bg_color": "#f4f4f7", "bg_opacity": 0.82,
        "border_color": "#ffffff", "border_opacity": 0.60, "border_width": 1,
        "shadow": True, "shadow_color": "#000000", "shadow_opacity": 0.22,
        "shadow_blur": 20, "shadow_offset_x": 0, "shadow_offset_y": 6,
        "glass": True, "glass_top": "#ffffff", "glass_bottom": "#e6e6ec",
        "text_color": "#1a1a1f", "font_size": 14,
        "padding_h": 10, "padding_v": 8,
    },
    "纯净暗色": {
        "corner_radius": 8, "bg_color": "#1e1e24", "bg_opacity": 0.90,
        "border_color": "#ffffff", "border_opacity": 0.08, "border_width": 1,
        "shadow": True, "shadow_color": "#000000", "shadow_opacity": 0.45,
        "shadow_blur": 14, "shadow_offset_x": 0, "shadow_offset_y": 3,
        "glass": False, "glass_top": "#000000", "glass_bottom": "#000000",
        "text_color": "#f5f5f5", "font_size": 14,
        "padding_h": 10, "padding_v": 8,
    },
    "极简无边": {
        "corner_radius": 6, "bg_color": "#000000", "bg_opacity": 0.65,
        "border_color": "#000000", "border_opacity": 0.0, "border_width": 0,
        "shadow": False, "shadow_color": "#000000", "shadow_opacity": 0.0,
        "shadow_blur": 0, "shadow_offset_x": 0, "shadow_offset_y": 0,
        "glass": False, "glass_top": "#000000", "glass_bottom": "#000000",
        "text_color": "#ffffff", "font_size": 14,
        "padding_h": 8, "padding_v": 6,
    },
}


def _make_tray_icon():
    pix = QPixmap(32, 32)
    pix.fill(QColor(0, 0, 0, 0))
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor("#3b82f6"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(4, 4, 24, 24, 7, 7)
    p.setPen(QColor("white"))
    p.setFont(QFont("Arial", 13, QFont.Weight.Bold))
    p.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "T")
    p.end()
    return QIcon(pix)


def _force_click_through(widget):
    """Windows 下强制顶级窗口点击穿透：叠加 WS_EX_TRANSPARENT | WS_EX_LAYERED。"""
    try:
        import win32con
        import win32gui
        hwnd = int(widget.winId())
        ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE,
                               ex | win32con.WS_EX_TRANSPARENT |
                               win32con.WS_EX_LAYERED |
                               win32con.WS_EX_TOOLWINDOW)
    except Exception:
        pass


class TranslationOverlay(QWidget):
    """鼠标旁的翻译浮层：无边框、置顶、点击穿透，支持自定义圆角/阴影/玻璃质感。"""

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._label = QLabel(self)
        self._label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._label.setWordWrap(True)
        self._label.setMaximumWidth(520)
        self._show_original = False
        self._orig = ""
        self._trans = ""
        self._style = dict(OVERLAY_PRESETS["极简无边"])
        self._margin = 2
        self._apply_label_style()

    def showEvent(self, event):
        super().showEvent(event)
        _force_click_through(self)

    def _apply_label_style(self):
        s = self._style
        self._label.setStyleSheet(
            f"QLabel{{color:{s['text_color']}; font-size:{s['font_size']}px;"
            f"padding:{s['padding_v']}px {s['padding_h']}px;"
            f"background:transparent;}}"
        )

    def _compute_margin(self):
        s = self._style
        if s["shadow"] and s["shadow_opacity"] > 0:
            return (s["shadow_blur"] + max(abs(s["shadow_offset_x"]),
                                           abs(s["shadow_offset_y"]))) + 2
        return 2

    def apply_style(self, style):
        """应用外观样式字典（圆角/阴影/玻璃/文字等）。"""
        self._style = dict(style)
        self._margin = self._compute_margin()
        self._apply_label_style()
        self.adjustSize()
        self._place_label()
        self.update()

    def set_show_original(self, on):
        self._show_original = on

    def set_text(self, original, translation):
        self._orig = original or ""
        self._trans = translation or ""
        s = self._style
        orig_size = max(s["font_size"] - 2, 10)
        if not self._trans:
            text = "翻译中…"
        elif self._show_original and self._orig:
            text = (f"{self._trans}"
                    f"<br><span style='color:#9aa0a6; font-size:{orig_size}px;'>"
                    f"{self._orig}</span>")
        else:
            text = self._trans
        self._label.setText(text)
        self.adjustSize()
        self._place_label()

    def adjustSize(self):
        self._label.adjustSize()
        sh = self._label.sizeHint()
        m = self._margin
        w = sh.width() + 4 + 2 * m
        h = sh.height() + 4 + 2 * m
        self.resize(max(w, 40 + 2 * m), max(h, 20 + 2 * m))

    def _place_label(self):
        m = self._margin
        self._label.setGeometry(m, m, self.width() - 2 * m,
                                self.height() - 2 * m)

    def place_at_mouse(self, mx, my):
        """把浮层放到鼠标右下角（mx,my 为虚拟屏幕坐标）。"""
        gx = mx + 16
        gy = my + 16
        self.adjustSize()
        ow, oh = self.width(), self.height()
        # 用鼠标所在屏幕的可用区做边界避让
        screen = QApplication.screenAt(QPoint(mx, my)) or QApplication.primaryScreen()
        if screen is not None:
            sg = screen.availableGeometry()
            if gx + ow > sg.right() - 4:
                gx = mx - ow - 16
            if gx < sg.left() + 4:
                gx = sg.left() + 4
            if gy + oh > sg.bottom() - 4:
                gy = my - oh - 16
            if gy < sg.top() + 4:
                gy = sg.top() + 4
        self.move(gx, gy)
        self._place_label()
        if not self.isVisible():
            self.show()
        self.raise_()

    def hide_tip(self):
        if self.isVisible():
            self.hide()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = self._style
        m = self._margin
        body = QRect(m, m, self.width() - 2 * m, self.height() - 2 * m)
        r = s["corner_radius"]
        # ---- 阴影：多层渐变圆角矩形模拟高斯模糊 ----
        if s["shadow"] and s["shadow_opacity"] > 0 and s["shadow_blur"] > 0:
            base = QColor(s["shadow_color"])
            br, bg, bb = base.red(), base.green(), base.blue()
            base_a = int(s["shadow_opacity"] * 255)
            ox = s["shadow_offset_x"]
            oy = s["shadow_offset_y"]
            n = min(max(s["shadow_blur"], 6), 28)
            p.setPen(Qt.PenStyle.NoPen)
            for i in range(n, 0, -1):
                t = i / n
                expand = int(s["shadow_blur"] * t)
                fade = 1.0 - t
                alpha = int(base_a * (0.06 + 0.5 * fade))
                if alpha <= 0:
                    continue
                p.setBrush(QColor(br, bg, bb, min(alpha, 255)))
                rr = QRect(body.x() - expand + ox, body.y() - expand + oy,
                           body.width() + 2 * expand,
                           body.height() + 2 * expand)
                p.drawRoundedRect(rr, r + expand, r + expand)
        # ---- 主体：玻璃渐变 或 纯色 ----
        if s["glass"]:
            grad = QLinearGradient(0, body.top(), 0, body.bottom())
            c1 = QColor(s["glass_top"]); c1.setAlphaF(s["bg_opacity"])
            c2 = QColor(s["glass_bottom"]); c2.setAlphaF(s["bg_opacity"])
            grad.setColorAt(0.0, c1)
            grad.setColorAt(1.0, c2)
            p.setBrush(grad)
        else:
            c = QColor(s["bg_color"]); c.setAlphaF(s["bg_opacity"])
            p.setBrush(c)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(body, r, r)
        # ---- 玻璃高光：顶部细线 ----
        if s["glass"]:
            hi = QColor(255, 255, 255, int(70 * s["bg_opacity"]))
            p.setPen(QPen(hi, 1))
            p.drawLine(body.left() + r, body.top() + 1,
                       body.right() - r, body.top() + 1)
        # ---- 边框 ----
        if s["border_width"] > 0 and s["border_opacity"] > 0:
            bc = QColor(s["border_color"]); bc.setAlphaF(s["border_opacity"])
            p.setPen(QPen(bc, s["border_width"]))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(body.adjusted(0, 0, -1, -1), r, r)


class DebugOverlay(QWidget):
    """覆盖虚拟桌面的点击穿透窗口，画所有 OCR 文本框（屏幕坐标）。
    框坐标 = 捕获区原点(win_origin) + OCR 局部坐标，再减去 debug 覆盖原点。"""

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._boxes = []
        self._win_origin = (0, 0, 0, 0)   # 捕获区屏幕原点
        self._debug_origin = (0, 0)       # debug 覆盖左上角的虚拟屏幕坐标

    def showEvent(self, event):
        super().showEvent(event)
        _force_click_through(self)

    def set_virtual_geometry(self):
        """覆盖整个虚拟桌面。"""
        x, y, w, h = window_capture.virtual_screen_rect()
        self._debug_origin = (x, y)
        self.setGeometry(x, y, w, h)

    def update_boxes(self, boxes, win_origin):
        self._boxes = boxes
        self._win_origin = win_origin
        self.update()

    def paintEvent(self, _):
        if not self._boxes:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(Qt.BrushStyle.NoBrush)
        font = QFont("Consolas", 9)
        p.setFont(font)
        ox, oy = self._win_origin[0], self._win_origin[1]
        dx, dy = self._debug_origin
        try:
            mx, my = get_mouse_pos()
        except Exception:
            mx, my = -1, -1
        # 鼠标转捕获区局部坐标用于命中高亮
        lx, ly = mx - ox, my - oy
        for b in self._boxes:
            # 屏幕坐标 -> debug 本地坐标
            sx = ox + b.x - dx
            sy = oy + b.y - dy
            rect = QRect(int(sx), int(sy), b.w, b.h)
            in_box = b.contains(lx, ly)
            if in_box:
                p.setPen(QPen(QColor(255, 60, 60, 230), 3))
            else:
                p.setPen(QPen(QColor(0, 255, 0, 200), 1))
            p.drawRect(rect)
            label = f"{b.text[:24]} ({b.score:.2f})"
            p.setPen(QColor(255, 255, 0, 230))
            p.drawText(int(sx), max(0, int(sy) - 3), label)
        # 鼠标十字线（debug 本地坐标）
        if mx >= 0:
            mxl, myl = mx - dx, my - dy
            p.setPen(QPen(QColor(0, 200, 255, 180), 1))
            p.drawLine(int(mxl), 0, int(mxl), self.height())
            p.drawLine(0, int(myl), self.width(), int(myl))


class WindowPickerDialog(QDialog):
    """选择目标窗口的对话框。返回 hwnd 或 None。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择目标窗口")
        self.resize(440, 420)
        self._hwnd = None
        v = QVBoxLayout(self)
        v.addWidget(QLabel("选择要翻译的窗口（捕获其客户区）："))
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(self._pick)
        v.addWidget(self.list, 1)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._on_ok)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)
        self._refresh()

    def _refresh(self):
        self.list.clear()
        try:
            wins = window_capture.enumerate_windows()
        except Exception as e:
            self.list.addItem(f"枚举失败: {e}")
            return
        for hwnd, title, cls in wins:
            it = QListWidgetItem(f"{title}   [{cls}]")
            it.setData(Qt.ItemDataRole.UserRole, hwnd)
            self.list.addItem(it)
        if self.list.count():
            self.list.setCurrentRow(0)

    def _pick(self, item):
        self._hwnd = item.data(Qt.ItemDataRole.UserRole)
        self.accept()

    def _on_ok(self):
        it = self.list.currentItem()
        if it:
            self._hwnd = it.data(Qt.ItemDataRole.UserRole)
        self.accept()

    def selected_hwnd(self):
        return self._hwnd


class SettingsWindow(QMainWindow):
    """策略与参数配置窗口。"""

    params_changed = pyqtSignal()

    def __init__(self, app):
        super().__init__()
        self._app = app
        self._loading_overlay = False
        self.setWindowTitle("TransCursor 设置")
        self.setWindowIcon(_make_tray_icon())
        self.resize(420, 460)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # ---- 策略 ----
        grp_strategy = QGroupBox("截图触发策略")
        fs = QFormLayout(grp_strategy)
        self.cb_strategy = QComboBox()
        self.cb_strategy.addItems([
            "周期执行 (periodic)",
            "分析图像更新 (image_diff)",
            "鼠标键盘触发+等待稳定 (input_trigger)",
        ])
        self.cb_strategy.currentIndexChanged.connect(self._on_strategy)
        fs.addRow("策略", self.cb_strategy)
        root.addWidget(grp_strategy)

        # ---- 各策略参数 ----
        self.grp_periodic = QGroupBox("周期执行 参数")
        fp = QFormLayout(self.grp_periodic)
        self.sp_scan_interval = QDoubleSpinBox()
        self.sp_scan_interval.setRange(0.1, 60.0)
        self.sp_scan_interval.setSingleStep(0.1)
        self.sp_scan_interval.setSuffix(" s")
        fp.addRow("执行周期", self.sp_scan_interval)
        root.addWidget(self.grp_periodic)

        self.grp_image_diff = QGroupBox("分析图像更新 参数")
        fid = QFormLayout(self.grp_image_diff)
        self.sp_poll_interval = QDoubleSpinBox()
        self.sp_poll_interval.setRange(0.05, 5.0)
        self.sp_poll_interval.setSingleStep(0.05)
        self.sp_poll_interval.setSuffix(" s")
        self.sp_stability = QDoubleSpinBox()
        self.sp_stability.setRange(0.0, 1.0)
        self.sp_stability.setSingleStep(0.005)
        self.sp_stability.setDecimals(3)
        self.sp_consistency = QDoubleSpinBox()
        self.sp_consistency.setRange(0.0, 1.0)
        self.sp_consistency.setSingleStep(0.005)
        self.sp_consistency.setDecimals(3)
        fid.addRow("截图轮询间隔", self.sp_poll_interval)
        fid.addRow("图像稳定性阈值", self.sp_stability)
        fid.addRow("图像一致性阈值", self.sp_consistency)
        root.addWidget(self.grp_image_diff)

        self.grp_input = QGroupBox("鼠标键盘触发 参数")
        fi = QFormLayout(self.grp_input)
        self.sp_trigger_delay = QDoubleSpinBox()
        self.sp_trigger_delay.setRange(0.0, 5.0)
        self.sp_trigger_delay.setSingleStep(0.1)
        self.sp_trigger_delay.setSuffix(" s")
        self.sp_trigger_stability = QDoubleSpinBox()
        self.sp_trigger_stability.setRange(0.0, 1.0)
        self.sp_trigger_stability.setSingleStep(0.005)
        self.sp_trigger_stability.setDecimals(3)
        self.sp_text_sim = QSpinBox()
        self.sp_text_sim.setRange(0, 1000)
        fi.addRow("触发后延迟", self.sp_trigger_delay)
        fi.addRow("图像稳定性阈值", self.sp_trigger_stability)
        fi.addRow("文本相似度阈值(编辑距离)", self.sp_text_sim)
        self.chk_left = QCheckBox("按下鼠标左键")
        self.chk_enter = QCheckBox("按下 Enter")
        self.chk_ctrl = QCheckBox("松开 Ctrl")
        self.chk_shift = QCheckBox("松开 Shift")
        self.chk_alt = QCheckBox("松开 Alt")
        box = QVBoxLayout()
        for c in (self.chk_left, self.chk_enter, self.chk_ctrl,
                  self.chk_shift, self.chk_alt):
            box.addWidget(c)
        fi.addRow("触发事件", None)
        fi.addRow(box)
        root.addWidget(self.grp_input)

        # ---- 捕获目标 ----
        grp_target = QGroupBox("捕获目标")
        ft = QVBoxLayout(grp_target)
        row_t = QHBoxLayout()
        self.btn_pick = QPushButton("选择窗口…")
        self.btn_fullscreen = QPushButton("全屏")
        self.btn_pick.clicked.connect(self._on_pick_window)
        self.btn_fullscreen.clicked.connect(self._on_fullscreen)
        row_t.addWidget(self.btn_pick)
        row_t.addWidget(self.btn_fullscreen)
        row_t.addStretch(1)
        ft.addLayout(row_t)
        self.lbl_target = QLabel("当前：全屏")
        self.lbl_target.setStyleSheet("color:#9aa0a6;")
        ft.addWidget(self.lbl_target)
        root.addWidget(grp_target)

        # ---- 通用 ----
        grp_common = QGroupBox("通用")
        fc = QFormLayout(grp_common)
        self.sp_pretranslate = QSpinBox()
        self.sp_pretranslate.setRange(0, 64)
        self.cb_model = QComboBox()
        self.cb_model.addItems(["opusmt (MarianMT, 快)", "hunyuan (混元 LLM, 慢)"])
        self.cb_model.currentIndexChanged.connect(self._on_model)
        self.chk_merge = QCheckBox("同行合并（默认关，开启可能误并相邻元素）")
        self.chk_merge.toggled.connect(self._on_merge)
        self.chk_show_orig = QCheckBox("浮层同时显示原文")
        self.chk_show_orig.toggled.connect(self._app.overlay.set_show_original)
        self.chk_debug = QCheckBox("Debug：显示所有文本框位置/大小")
        self.chk_debug.toggled.connect(self._app.set_debug)
        fc.addRow("翻译模型", self.cb_model)
        fc.addRow("预翻译框数 N", self.sp_pretranslate)
        fc.addRow("OCR 选项", self.chk_merge)
        fc.addRow("浮层", self.chk_show_orig)
        fc.addRow("调试", self.chk_debug)
        root.addWidget(grp_common)

        # ---- 浮层外观 ----
        grp_overlay = QGroupBox("浮层外观")
        fov = QFormLayout(grp_overlay)
        self.cb_overlay_preset = QComboBox()
        self.cb_overlay_preset.addItems(list(OVERLAY_PRESETS.keys()))
        self.cb_overlay_preset.currentIndexChanged.connect(self._on_overlay_preset)
        fov.addRow("预设", self.cb_overlay_preset)
        self.sp_corner = QSpinBox()
        self.sp_corner.setRange(0, 40)
        fov.addRow("圆角半径", self.sp_corner)
        self.sp_overlay_opacity = QDoubleSpinBox()
        self.sp_overlay_opacity.setRange(0.0, 1.0)
        self.sp_overlay_opacity.setSingleStep(0.05)
        self.sp_overlay_opacity.setDecimals(2)
        fov.addRow("背景不透明度", self.sp_overlay_opacity)
        self.chk_shadow = QCheckBox("启用阴影")
        fov.addRow("阴影", self.chk_shadow)
        self.sp_shadow_blur = QSpinBox()
        self.sp_shadow_blur.setRange(0, 40)
        fov.addRow("阴影模糊", self.sp_shadow_blur)
        self.chk_glass = QCheckBox("启用玻璃渐变")
        fov.addRow("玻璃质感", self.chk_glass)
        self.sp_overlay_font = QSpinBox()
        self.sp_overlay_font.setRange(8, 32)
        fov.addRow("字号", self.sp_overlay_font)
        for w in (self.sp_corner, self.sp_overlay_opacity,
                  self.sp_shadow_blur, self.sp_overlay_font):
            w.valueChanged.connect(self._on_overlay_param)
        for c in (self.chk_shadow, self.chk_glass):
            c.toggled.connect(self._on_overlay_param)
        root.addWidget(grp_overlay)

        # ---- 控制按钮 ----
        row = QHBoxLayout()
        self.btn_start = QPushButton("启动")
        self.btn_stop = QPushButton("停止")
        self.btn_hide = QPushButton("隐藏到托盘")
        self.btn_start.clicked.connect(self._on_start)
        self.btn_stop.clicked.connect(self._on_stop)
        self.btn_hide.clicked.connect(self.hide)
        row.addWidget(self.btn_start)
        row.addWidget(self.btn_stop)
        row.addStretch(1)
        row.addWidget(self.btn_hide)
        root.addLayout(row)

        self.lbl_status = QLabel("状态：未启动")
        root.addWidget(self.lbl_status)
        # 扫描诊断状态栏：实时显示 OCR 是否被调用、耗时、框数、错误
        self.lbl_diag = QLabel("扫描：未运行")
        self.lbl_diag.setStyleSheet(
            "background:#1e1e22; color:#c8c8c8; padding:6px;"
            "border-radius:4px; font-family:Consolas; font-size:12px;")
        self.lbl_diag.setWordWrap(True)
        root.addWidget(self.lbl_diag)
        # 诊断刷新定时器（独立于引擎，即使引擎未启也能显示状态）
        self._diag_timer = QTimer(self)
        self._diag_timer.setInterval(500)
        self._diag_timer.timeout.connect(self._refresh_diag)
        self._diag_timer.start()

        self._load_defaults()
        self._on_strategy(0)

    def _load_defaults(self):
        st = self._app.st
        self.sp_scan_interval.setValue(st.scan_interval)
        self.sp_poll_interval.setValue(st.poll_interval)
        self.sp_stability.setValue(st.stability_threshold)
        self.sp_consistency.setValue(st.consistency_threshold)
        self.sp_trigger_delay.setValue(st.trigger_delay)
        self.sp_trigger_stability.setValue(st.trigger_stability_threshold)
        self.sp_text_sim.setValue(st.text_similarity_threshold)
        self.sp_pretranslate.setValue(st.pretranslate_n)
        self.chk_left.setChecked(True)
        self.chk_enter.setChecked(True)
        self.chk_ctrl.setChecked(True)
        self.chk_shift.setChecked(True)
        self.chk_alt.setChecked(True)
        for w in (self.sp_scan_interval, self.sp_poll_interval,
                  self.sp_stability, self.sp_consistency,
                  self.sp_trigger_delay, self.sp_trigger_stability,
                  self.sp_text_sim, self.sp_pretranslate):
            w.valueChanged.connect(self._on_param)
        for c in (self.chk_left, self.chk_enter, self.chk_ctrl,
                  self.chk_shift, self.chk_alt):
            c.toggled.connect(self._on_param)
        # 浮层外观：从当前样式回填控件
        s = self._app._overlay_style
        self._loading_overlay = True
        # 预设下拉框同步到当前样式对应的预设
        for i in range(self.cb_overlay_preset.count()):
            if self.cb_overlay_preset.itemText(i) == "极简无边":
                self.cb_overlay_preset.setCurrentIndex(i)
                break
        self.sp_corner.setValue(s["corner_radius"])
        self.sp_overlay_opacity.setValue(s["bg_opacity"])
        self.chk_shadow.setChecked(s["shadow"])
        self.sp_shadow_blur.setValue(s["shadow_blur"])
        self.chk_glass.setChecked(s["glass"])
        self.sp_overlay_font.setValue(s["font_size"])
        self._loading_overlay = False

    def _on_strategy(self, idx):
        name = STRATEGIES[idx]
        self.grp_periodic.setVisible(name == "periodic")
        self.grp_image_diff.setVisible(name == "image_diff")
        self.grp_input.setVisible(name == "input_trigger")
        self._app.st.set_strategy(name)
        self.params_changed.emit()

    def _on_param(self, *_):
        st = self._app.st
        st.scan_interval = self.sp_scan_interval.value()
        st.poll_interval = self.sp_poll_interval.value()
        st.stability_threshold = self.sp_stability.value()
        st.consistency_threshold = self.sp_consistency.value()
        st.trigger_delay = self.sp_trigger_delay.value()
        st.trigger_stability_threshold = self.sp_trigger_stability.value()
        st.text_similarity_threshold = self.sp_text_sim.value()
        st.pretranslate_n = self.sp_pretranslate.value()
        st.input_monitor._flags = {
            "left_click": self.chk_left.isChecked(),
            "enter": self.chk_enter.isChecked(),
            "ctrl_release": self.chk_ctrl.isChecked(),
            "shift_release": self.chk_shift.isChecked(),
            "alt_release": self.chk_alt.isChecked(),
        }
        self.params_changed.emit()

    def _on_start(self):
        self._on_param()
        self._app.start_engine()

    def _on_stop(self):
        self._app.stop_engine()

    def _on_model(self, idx):
        # 切换翻译后端（需停止引擎后切换，下次启动生效）
        backend = "opusmt" if idx == 0 else "hunyuan"
        was_running = self._app.st._running
        if was_running:
            self._app.stop_engine()
        self._app.set_translator_backend(backend)
        if was_running:
            self._app.start_engine()

    def _on_merge(self, on):
        import ocr_engine
        ocr_engine.MERGE_SAME_LINE = bool(on)

    def _on_overlay_preset(self, idx):
        name = self.cb_overlay_preset.itemText(idx)
        style = OVERLAY_PRESETS.get(name)
        if not style:
            return
        self._app._overlay_style = dict(style)
        self._app.overlay.apply_style(self._app._overlay_style)
        self._loading_overlay = True
        self.sp_corner.setValue(style["corner_radius"])
        self.sp_overlay_opacity.setValue(style["bg_opacity"])
        self.chk_shadow.setChecked(style["shadow"])
        self.sp_shadow_blur.setValue(style["shadow_blur"])
        self.chk_glass.setChecked(style["glass"])
        self.sp_overlay_font.setValue(style["font_size"])
        self._loading_overlay = False

    def _on_overlay_param(self, *_):
        if self._loading_overlay:
            return
        s = self._app._overlay_style
        s["corner_radius"] = self.sp_corner.value()
        s["bg_opacity"] = self.sp_overlay_opacity.value()
        s["shadow"] = self.chk_shadow.isChecked()
        s["shadow_blur"] = self.sp_shadow_blur.value()
        s["glass"] = self.chk_glass.isChecked()
        s["font_size"] = self.sp_overlay_font.value()
        self._app.overlay.apply_style(s)

    def _refresh_diag(self):
        diag = self._app.st.get_diag()
        if not diag["running"]:
            self.lbl_diag.setText("扫描：未运行")
            return
        if diag["scanning"]:
            head = "● 正在 OCR 扫描…"
        elif not (diag["ocr_loaded"] and diag["translator_loaded"]):
            head = "○ 模型加载中…"
        elif diag["scan_count"] == 0:
            head = "○ 首次扫描准备中…"
        else:
            head = "○ 等待下一轮"
        load = []
        load.append("OCR" + ("✓" if diag["ocr_loaded"] else "…"))
        load.append("翻译" + ("✓" if diag["translator_loaded"] else "…"))
        stats = f"扫描次数={diag['scan_count']}"
        if diag["scan_count"] > 0:
            stats += (f" | 上次OCR={diag['last_duration']:.2f}s"
                      f" 框={diag['last_box_count']}"
                      f" 截图={diag['last_capture_ms']:.0f}ms")
        err = f" | ⚠ {diag['last_error']}" if diag["last_error"] else ""
        self.lbl_diag.setText(f"{head} [{']['.join(load)}] {stats}{err}")

    def _on_pick_window(self):
        dlg = WindowPickerDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            hwnd = dlg.selected_hwnd()
            if hwnd:
                self._app.set_window(hwnd)
                try:
                    import win32gui
                    title = win32gui.GetWindowText(hwnd) or f"hwnd={hwnd}"
                except Exception:
                    title = f"hwnd={hwnd}"
                self.lbl_target.setText(f"当前窗口：{title}")
                self.lbl_target.setStyleSheet("color:#3b82f6;")

    def _on_fullscreen(self):
        self._app.set_window(None)
        self.lbl_target.setText("当前：全屏")
        self.lbl_target.setStyleSheet("color:#9aa0a6;")

    def set_status(self, text, running):
        self.lbl_status.setText(f"状态：{text}")
        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running)

    def closeEvent(self, e: QCloseEvent):
        if self._app.tray.isVisible():
            e.ignore()
            self.hide()
        else:
            super().closeEvent(e)


class TransCursorApp(QObject):
    translation_ready = pyqtSignal(object)

    def __init__(self, argv):
        self.app = QApplication.instance() or QApplication(argv)
        super().__init__()
        self.app.setApplicationName("TransCursor")
        self.app.setQuitOnLastWindowClosed(False)

        self.st = ScreenTranslator(strategy="periodic", pretranslate_n=8)
        self._overlay_style = dict(OVERLAY_PRESETS["极简无边"])
        self.overlay = TranslationOverlay()
        self.overlay.apply_style(self._overlay_style)
        self.debug_overlay = DebugOverlay()
        self.settings_win = SettingsWindow(self)

        self._debug = False
        self._pending_box = None
        self._last_hit_id = None
        self.translation_ready.connect(self._on_translation_ready)

        self._poll = QTimer(self)
        self._poll.setInterval(30)
        self._poll.timeout.connect(self._tick)

        # 托盘
        self.tray = QSystemTrayIcon(_make_tray_icon(), self.app)
        self.tray.setToolTip("TransCursor")
        menu = QMenu()
        act_show = menu.addAction("设置")
        act_start = menu.addAction("启动")
        act_stop = menu.addAction("停止")
        menu.addSeparator()
        act_quit = menu.addAction("退出")
        act_show.triggered.connect(self.show_settings)
        act_start.triggered.connect(self.start_engine)
        act_stop.triggered.connect(self.stop_engine)
        act_quit.triggered.connect(self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self._tray_act_start = act_start
        self._tray_act_stop = act_stop

    # ---- 引擎控制 ----
    def start_engine(self):
        if self.st._running:
            return
        import paths
        missing = paths.ensure_models_exist()
        if missing:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(
                self.settings_win, "模型缺失",
                "缺少模型目录：\n  " + "\n  ".join(missing) +
                f"\n\n请把模型放到：\n{paths.MODELS_DIR}\n"
                "（结构：models/opusmt、models/HY、models/OCR/OCRdet、models/OCR/OCRrec）")
            return
        self.st.start(preload=False)
        self._poll.start()
        self.settings_win.set_status("加载模型中 [OCR…][翻译…]", True)
        self._tray_act_start.setEnabled(False)
        self._tray_act_stop.setEnabled(True)
        self.tray.showMessage("TransCursor", "已启动，正在加载模型，请稍候……",
                              QSystemTrayIcon.MessageIcon.Information, 2500)

    def stop_engine(self):
        self.st.stop()
        self._poll.stop()
        self.overlay.hide_tip()
        if self._debug:
            self.debug_overlay.hide()
        self.settings_win.set_status("已停止", False)
        self._tray_act_start.setEnabled(True)
        self._tray_act_stop.setEnabled(False)

    def set_window(self, hwnd):
        self.st.set_window(hwnd)

    def set_translator_backend(self, backend):
        """切换翻译后端：重建 translator 实例。"""
        import translator
        self.st.translator = translator.Translator(backend=backend)

    def set_debug(self, on):
        self._debug = on
        if on:
            self.debug_overlay.set_virtual_geometry()
            self.debug_overlay.show()
        else:
            self.debug_overlay.hide()

    def quit(self):
        self.stop_engine()
        self.tray.hide()
        self.app.quit()

    def show_settings(self):
        self.settings_win.show()
        self.settings_win.raise_()
        self.settings_win.activateWindow()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_settings()

    # ---- 命中轮询 ----
    def _tick(self):
        if self.st._running:
            diag = self.st.get_diag()
            ocr_ok = diag["ocr_loaded"]
            tr_ok = diag["translator_loaded"]
            # 模型未全部加载完：状态栏显示加载进度
            if not (ocr_ok and tr_ok):
                parts = []
                parts.append("OCR" + ("✓" if ocr_ok else "…"))
                parts.append("翻译" + ("✓" if tr_ok else "…"))
                self.settings_win.set_status("加载模型中 [" + "][".join(parts) + "]", True)
            elif self.settings_win.lbl_status.text() != "状态：运行中":
                self.settings_win.set_status("运行中", True)
        # debug 覆盖：每帧刷新文本框（带捕获区原点用于坐标映射）
        boxes = self.st.get_snapshot()
        if self._debug:
            self.debug_overlay.update_boxes(boxes, self.st._win_origin)
        try:
            mx, my = get_mouse_pos()
        except Exception:
            return
        # 严格命中测试：鼠标必须在框内
        hit = self.st.hit_test(mx, my)
        if hit is None:
            self.overlay.hide_tip()
            self._last_hit_id = None
            return
        hid = (hit.x, hit.y, hit.w, hit.h, hit.text)
        if hit.translation is not None:
            self.overlay.set_text(hit.text, hit.translation)
            self.overlay.place_at_mouse(mx, my)
            self._last_hit_id = hid
        else:
            self.overlay.set_text(hit.text, "")
            self.overlay.place_at_mouse(mx, my)
            if self._last_hit_id != hid:
                self._last_hit_id = hid
                self._ensure_async(hit)

    def _ensure_async(self, box):
        self._pending_box = box

        def work():
            try:
                self.st.ensure_translation(box)
            finally:
                self.translation_ready.emit(box)

        threading.Thread(target=work, daemon=True).start()

    def _on_translation_ready(self, box):
        self._pending_box = None
        try:
            mx, my = get_mouse_pos()
        except Exception:
            return
        hit = self.st.hit_test(mx, my)
        if hit is box and box.translation is not None:
            self.overlay.set_text(box.text, box.translation)
            self.overlay.place_at_mouse(mx, my)

    def run(self):
        self.tray.show()
        self.show_settings()
        return self.app.exec()
