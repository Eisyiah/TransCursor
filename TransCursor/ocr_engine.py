# -*- coding: utf-8 -*-
"""
OCR 引擎：封装 PaddleOCR 的 det + rec。
- 输入一张 BGR 截图，返回 [(rect, text, score), ...]
- rect = (x, y, w, h)，坐标基于该截图的像素（客户区局部坐标）
- 框优化：过滤小框 → 同行合并 → 限制最大框数，显著降低 rec 工作量
- rec 分块调用，减少单次列表过长带来的开销
"""
import os
# 关闭 PaddleX 抢先初始化，避免 import 时 initialize() 一次、创建模型时再 initialize() 报
# "PDX has already been initialized. Reinitialization is not supported."
os.environ.setdefault("PADDLE_PDX_EAGER_INIT", "0")

import numpy as np
import cv2

import paths

# 修正后的 OCR 参数
OCR_CONFIG = {
    "limit_side_len": 960,       # 之前 64 严重错误，限制长边到 960
    "limit_type": "max",         # "max" = 保证长边不超过 limit
    "thresh": 0.30,              # Text Detection Pixel Threshold
    "box_thresh": 0.60,          # Text Detection Box Threshold
    "unclip_ratio": 1.5,         # Expansion Coefficient
    "rec_score_thresh": 0.50,    # 之前 0 不过滤，现 0.5 过滤噪声
}

MAX_BOXES = 80                  # rec 最大处理框数
MIN_BOX_AREA = 24               # w*h 小于此值视为噪声丢弃
MERGE_SAME_LINE = False         # 同行合并默认关闭（过于频繁会误并不相邻元素）
MERGE_Y_TOL_RATIO = 0.3         # 同行判定：y 中心差 < ratio*高度
MERGE_GAP_RATIO = 0.6           # 同行相邻框 x 间隙 > ratio*高度 时不合并
REC_BATCH = 16                  # rec 分块大小

DET_DIR = paths.OCR_DET_DIR
REC_DIR = paths.OCR_REC_DIR
DET_NAME = "PP-OCRv6_small_det"
REC_NAME = "PP-OCRv6_small_rec"


def _filter_small(rects):
    return [r for r in rects if r[2] * r[3] >= MIN_BOX_AREA and r[2] >= 4 and r[3] >= 4]


def _merge_same_line(rects):
    """把同行（y 中心接近）的框合并成整行外接矩形。
    同行内若 x 间隙过大则拆成多组，避免把不相邻的 UI 元素并到一起。
    """
    if not rects:
        return rects
    # 按 y 中心、x 排序
    arr = sorted(rects, key=lambda r: (r[1] + r[3] / 2.0, r[0]))
    lines = []  # 每项: {"cy": float, "h": int, "rects": [...]}
    for r in arr:
        cy = r[1] + r[3] / 2.0
        placed = False
        for ln in lines:
            if abs(cy - ln["cy"]) <= max(r[3], ln["h"]) * MERGE_Y_TOL_RATIO:
                ln["rects"].append(r)
                # 用框宽加权更新行 y 中心
                wsum = ln.get("wsum", 0) + r[2]
                ln["cy"] = (ln["cy"] * ln.get("wsum", 0) + cy * r[2]) / wsum
                ln["wsum"] = wsum
                ln["h"] = max(ln["h"], r[3])
                placed = True
                break
        if not placed:
            lines.append({"cy": cy, "h": r[3], "wsum": r[2], "rects": [r]})

    out = []
    for ln in lines:
        rs = sorted(ln["rects"], key=lambda r: r[0])
        groups = [[rs[0]]]
        for prev, cur in zip(rs, rs[1:]):
            gap = cur[0] - (prev[0] + prev[2])
            if gap > max(prev[3], cur[3]) * MERGE_GAP_RATIO:
                groups.append([cur])
            else:
                groups[-1].append(cur)
        for g in groups:
            x1 = min(r[0] for r in g)
            y1 = min(r[1] for r in g)
            x2 = max(r[0] + r[2] for r in g)
            y2 = max(r[1] + r[3] for r in g)
            out.append((x1, y1, x2 - x1, y2 - y1))
    return out


def _limit_boxes(rects):
    """超过 MAX_BOXES 时，按面积保留最大的若干个（文本框通常比噪声大）。"""
    if len(rects) <= MAX_BOXES:
        return rects
    big = sorted(rects, key=lambda r: r[2] * r[3], reverse=True)[:MAX_BOXES]
    big_set = set(big)
    return [r for r in rects if r in big_set]


class OCREngine:
    """det + rec 的封装。模型在首次调用时加载。"""

    def __init__(self):
        self._det = None
        self._rec = None
        self._lock = __import__("threading").Lock()

    def load(self):
        if self._det is not None:
            return
        with self._lock:
            if self._det is not None:
                return
            from paddleocr import TextDetection, TextRecognition
            self._det = TextDetection(
                model_name=DET_NAME,
                model_dir=DET_DIR,
                engine="transformers",
                limit_side_len=OCR_CONFIG["limit_side_len"],
                limit_type=OCR_CONFIG["limit_type"],
                thresh=OCR_CONFIG["thresh"],
                box_thresh=OCR_CONFIG["box_thresh"],
                unclip_ratio=OCR_CONFIG["unclip_ratio"],
            )
            self._rec = TextRecognition(
                model_name=REC_NAME,
                model_dir=REC_DIR,
                engine="transformers",
            )

    def run(self, img_bgr):
        """对 BGR 图跑 det+rec。返回 [(rect, text, score), ...]，rect=(x,y,w,h)。"""
        self.load()
        det_out = list(self._det.predict(input=img_bgr))[0]
        polygons = det_out["dt_polys"]

        rects = []
        if len(polygons) == 0:
            return []

        for poly in polygons:
            poly = np.array(poly, dtype=np.float32)
            x, y, w, h = cv2.boundingRect(poly.astype(np.int32))
            rects.append((int(x), int(y), int(w), int(h)))

        # ---- 框优化流水线 ----
        rects = _filter_small(rects)
        if MERGE_SAME_LINE:
            rects = _merge_same_line(rects)
        rects = _limit_boxes(rects)
        if not rects:
            return []

        # ---- 裁剪 + rec 分块 ----
        crops = []
        for (x, y, w, h) in rects:
            if w < 3 or h < 3:
                continue
            crops.append(np.ascontiguousarray(img_bgr[y:y + h, x:x + w]))
        valid_rects = rects[:len(crops)]
        if not crops:
            return []

        results = []
        for i in range(0, len(crops), REC_BATCH):
            chunk = crops[i:i + REC_BATCH]
            rec_out = list(self._rec.predict(input=chunk))
            for (x, y, w, h), r in zip(valid_rects[i:i + REC_BATCH], rec_out):
                text = r["rec_text"]
                score = float(r["rec_score"])
                if score < OCR_CONFIG["rec_score_thresh"]:
                    continue
                results.append(((x, y, w, h), text, score))
        return results
