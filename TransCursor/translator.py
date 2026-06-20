# -*- coding: utf-8 -*-
"""
翻译引擎：支持两种后端，统一接口。
- opusmt (MarianMT eng->zho)：seq2seq，真批量，速度快
- hunyuan (腾讯混元 LLM)：CausalLM，chat 模板翻译，质量好但慢

两者共享 LRU 缓存。Backend 由 TRANSLATOR_BACKEND 全局或 Translator(backend=) 指定。
"""
import threading
from collections import OrderedDict

import paths

OPUS_DIR = paths.OPUS_DIR
HY_DIR = paths.HY_DIR

TRANSLATOR_BACKEND = "opusmt"   # "opusmt" | "hunyuan"


def is_english_text(text):
    """判断文本是否适合送给 eng->zho 的 opusmt 翻译。

    opusmt 只会英译中，对非英语输入（中文、日文等）会输出垃圾。
    判定规则：只允许 ASCII 字符（26 字母 + 数字 + 符号），且至少含一个
    拉丁字母（纯数字/符号没有翻译意义）。
    """
    if not text:
        return False
    has_letter = False
    for ch in text:
        o = ord(ch)
        if o > 127:
            return False
        if (65 <= o <= 90) or (97 <= o <= 122):
            has_letter = True
    return has_letter


class _BaseTranslator:
    def __init__(self, cache_size=1000):
        self._cache = OrderedDict()   # key->译文，按"最近使用"排序（末尾为最新）
        self._cache_size = cache_size
        self._lock = threading.Lock()
        self._load_lock = threading.Lock()
        self._infer_lock = threading.Lock()
        self._loaded = False

    def load(self):
        raise NotImplementedError

    def _infer_batch(self, texts):
        """子类实现：输入待翻译文本列表，返回译文列表。"""
        raise NotImplementedError

    def translate(self, text):
        if not text or not text.strip():
            return ""
        with self._lock:
            if text in self._cache:
                self._cache.move_to_end(text)  # 命中即视作最新，保证留在缓存
                return self._cache[text]
        self.load()
        return self.translate_batch([text])[0]

    def translate_batch(self, texts):
        if not texts:
            return []
        results = [None] * len(texts)
        todo_idx, todo_texts = [], []
        with self._lock:
            for i, t in enumerate(texts):
                if not t or not t.strip():
                    results[i] = ""
                    continue
                if t in self._cache:
                    results[i] = self._cache[t]
                    self._cache.move_to_end(t)  # 命中即视作最新
                else:
                    todo_idx.append(i)
                    todo_texts.append(t)
        if todo_texts:
            self.load()
            with self._infer_lock:
                out = self._infer_batch(todo_texts)
            with self._lock:
                for i, t, o in zip(todo_idx, todo_texts, out):
                    o = (o or "").strip()
                    self._cache[t] = o          # 新条目自动在末尾（最新）
                    if len(self._cache) > self._cache_size:
                        self._cache.popitem(last=False)  # 淘汰最久未用
                    results[i] = o
        return results


class MarianTranslator(_BaseTranslator):
    def __init__(self, cache_size=1000):
        super().__init__(cache_size)
        self._tok = None
        self._model = None
        self._device = None

    def load(self):
        if self._loaded:
            return
        with self._load_lock:
            if self._loaded:
                return
            import torch
            from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
            self._tok = AutoTokenizer.from_pretrained(OPUS_DIR)
            self._model = AutoModelForSeq2SeqLM.from_pretrained(OPUS_DIR)
            self._model.eval()
            self._device = "cuda" if torch.cuda.is_available() else "cpu"
            try:
                self._model.to(self._device)
            except NotImplementedError:
                self._model.to_empty(device=self._device)
            self._loaded = True

    def _infer_batch(self, texts):
        import torch
        # opusmt 是 eng->zho，只翻译英语文本（仅含 ASCII 且有拉丁字母）。
        # 非英语（含中文/日文等非 ASCII，或纯数字符号）直接原样返回，避免输出垃圾。
        en_mask = [is_english_text(t) for t in texts]
        out = [texts[i] if not en_mask[i] else None for i in range(len(texts))]
        en_texts = [t for t, m in zip(texts, en_mask) if m]
        en_pos = [i for i, m in enumerate(en_mask) if m]
        if not en_texts:
            return out
        enc = self._tok(en_texts, return_tensors="pt", padding=True,
                        truncation=True, max_length=512)
        enc = {k: v.to(self._device) for k, v in enc.items()}
        with torch.no_grad():
            gen = self._model.generate(**enc, max_length=512, num_beams=4)
        decoded = self._tok.batch_decode(gen, skip_special_tokens=True)
        for pos, tr in zip(en_pos, decoded):
            out[pos] = tr
        return out


class HunYuanTranslator(_BaseTranslator):
    """腾讯混元 LLM 翻译后端。用 chat 模板构造翻译指令，逐句生成。"""

    SYSTEM_PROMPT = "你是一个专业翻译引擎，将用户给的英文翻译成简体中文，只输出译文，不要解释。"

    def __init__(self, cache_size=1000):
        super().__init__(cache_size)
        self._tok = None
        self._model = None
        self._device = None

    def load(self):
        if self._loaded:
            return
        with self._load_lock:
            if self._loaded:
                return
            import torch
            from transformers import AutoTokenizer, AutoModelForCausalLM
            self._tok = AutoTokenizer.from_pretrained(HY_DIR)
            # CPU 上 bf16 不一定支持，强制 float32
            dtype = torch.float16 if torch.cuda.is_available() else torch.float32
            self._model = AutoModelForCausalLM.from_pretrained(
                HY_DIR, torch_dtype=dtype, trust_remote_code=True)
            self._model.eval()
            self._device = "cuda" if torch.cuda.is_available() else "cpu"
            try:
                self._model.to(self._device)
            except NotImplementedError:
                self._model.to_empty(device=self._device)
            self._loaded = True

    def _translate_one(self, text):
        import torch
        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ]
        prompt = self._tok.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True)
        inputs = self._tok(prompt, return_tensors="pt", truncation=True,
                           max_length=1024)
        inputs = {k: v.to(self._device) for k, v in inputs.items()}
        with torch.no_grad():
            out = self._model.generate(
                **inputs, max_new_tokens=256, do_sample=False,
                temperature=1.0, top_p=1.0, repetition_penalty=1.0)
        new_tokens = out[0][inputs["input_ids"].shape[1]:]
        result = self._tok.decode(new_tokens, skip_special_tokens=True)
        return result.strip()

    def _infer_batch(self, texts):
        # LLM 逐句翻译（CausalLM 无法真批量，每句有独立 chat 上下文）
        return [self._translate_one(t) for t in texts]


def Translator(backend=None, **kwargs):
    """工厂：按 backend 返回对应翻译器实例。"""
    bk = backend or TRANSLATOR_BACKEND
    if bk == "hunyuan":
        return HunYuanTranslator(**kwargs)
    return MarianTranslator(**kwargs)
