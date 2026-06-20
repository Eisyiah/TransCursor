# -*- coding: utf-8 -*-
"""
窗口捕获：枚举可见窗口、按 hwnd 捕获客户区图像。
坐标系统：所有返回坐标均为虚拟屏幕坐标（物理像素，DPI 感知后与 Qt 一致）。

捕获策略：PrintWindow(PW_RENDERFULLCONTENT) 优先（兼容 DirectX/浏览器），
失败回退 BitBlt(SRCCOPY)。捕获整窗后裁剪到客户区，去除标题栏。
"""
import ctypes
import numpy as np
import cv2
import win32gui
import win32ui
import win32con
import win32api


def set_dpi_aware():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _is_pickable(hwnd):
    if not win32gui.IsWindowVisible(hwnd):
        return False
    if not win32gui.GetWindowText(hwnd):
        return False
    style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
    if style & win32con.WS_ICONIC:  # 最小化
        return False
    if style & win32con.WS_CHILD:  # 子控件，不是顶层窗口
        return False
    return True


def enumerate_windows():
    """返回 [(hwnd, title, class), ...]，按标题排序。"""
    out = []

    def cb(hwnd, _):
        if _is_pickable(hwnd):
            out.append((hwnd, win32gui.GetWindowText(hwnd),
                        win32gui.GetClassName(hwnd)))

    win32gui.EnumWindows(cb, None)
    out.sort(key=lambda x: x[1].lower())
    return out


def capture_window(hwnd):
    """捕获 hwnd 的客户区。返回 (img_bgr, (screen_left, screen_top, w, h))。
    screen_left/top 为客户区左上角在虚拟屏幕的坐标（用于 OCR 框→屏幕坐标映射）。
    """
    wl, wt, wr, wb = win32gui.GetWindowRect(hwnd)
    ww, wh = wr - wl, wb - wt
    if ww <= 0 or wh <= 0:
        raise ValueError("窗口尺寸无效")

    cl_x, cl_y = win32gui.ClientToScreen(hwnd, (0, 0))
    client_left = cl_x - wl   # 客户区在整窗内的 x 偏移
    client_top = cl_y - wt    # 客户区在整窗内的 y 偏移
    rl, rt, rr, rb = win32gui.GetClientRect(hwnd)
    cw, ch = rr - rl, rb - rt
    if cw <= 0 or ch <= 0:
        raise ValueError("客户区尺寸无效")

    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mfc_dc, ww, wh)
    save_dc.SelectObject(bmp)

    # PW_RENDERFULLCONTENT=0x2，对 DirectX/现代应用更可靠
    ok = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 0x2)
    if not ok:
        save_dc.BitBlt((0, 0), (ww, wh), mfc_dc, (0, 0), win32con.SRCCOPY)

    bmpstr = bmp.GetBitmapBits(True)
    img = np.frombuffer(bmpstr, dtype=np.uint8).reshape(wh, ww, 4)
    img_bgr = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

    save_dc.DeleteDC()
    mfc_dc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwnd_dc)
    win32gui.DeleteObject(bmp.GetHandle())

    # 裁剪到客户区
    img_client = img_bgr[client_top:client_top + ch,
                         client_left:client_left + cw]
    return img_client, (cl_x, cl_y, cw, ch)


def virtual_screen_rect():
    """虚拟桌面（多屏合并）矩形 (x, y, w, h)。"""
    x = win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN)
    y = win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN)
    w = win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN)
    h = win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN)
    return (x, y, w, h)
