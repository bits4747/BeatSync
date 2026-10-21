#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BeatSync —— 图形界面（tkinter 备选，无需额外安装）。

用法：
    python tempo_converter_gui.py

排查卡死：本模块会把关键步骤写进 `gui_trace.log`（与脚本/exe 同目录），
每行带毫秒时间戳。若界面卡住，看这个日志最后写到哪一步即可定位。
可用环境变量 TMC_NO_TRACE=1 关闭。
"""
from __future__ import annotations

import os
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk


def _setup_numba_cache() -> None:
    """在 import librosa/numba 之前把 numba 缓存目录指到可写位置。

    打包后的 exe 不带 NUMBA_CACHE_DIR，numba 会写默认位置，而在开启了某些
    Windows 安全策略的机器上那个写入会**永久挂起**——表现为点「开始处理」后
    卡在第一首歌、毫无输出（实测：不设 >10 分钟无响应；设了 16.7 秒完成）。
    """
    try:
        if os.environ.get("NUMBA_CACHE_DIR"):
            return
        import sys
        base = (Path(sys.executable).parent if getattr(sys, "frozen", False)
                else Path(__file__).parent)
        cache = base / "_ncache"
        cache.mkdir(parents=True, exist_ok=True)
        probe = cache / ".write_test"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            import tempfile
            cache = Path(tempfile.gettempdir()) / "tmc_numba_cache"
            cache.mkdir(parents=True, exist_ok=True)
        os.environ["NUMBA_CACHE_DIR"] = str(cache)
    except Exception:  # noqa: BLE001
        pass


_setup_numba_cache()

import tempo_converter as tc


def _trace_dir() -> Path:
    import sys
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def _icon_path() -> str | None:
    """窗口图标（.ico）路径；找不到返回 None。打包后资源在 _MEIPASS。"""
    import sys
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).parent
    p = base / "assets" / "icon.ico"
    return str(p) if p.is_file() else None


_TRACE_PATH = _trace_dir() / "gui_trace.log"
_TRACE_ON = not os.environ.get("TMC_NO_TRACE")
_trace_lock = threading.Lock()


def trace(msg: str) -> None:
    """把排查用的时间线写进 gui_trace.log（失败绝不影响主流程）。"""
    if not _TRACE_ON:
        return
    try:
        with _trace_lock:
            with open(_TRACE_PATH, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%H:%M:%S')}.{int(time.time()*1000)%1000:03d} "
                        f"[{threading.current_thread().name}] {msg}\n")
    except Exception:  # noqa: BLE001
        pass


def pick_directory(title: str = "选择文件夹") -> str:
    """独立目录选择器：纯 Tk 列目录，不碰 COM / 系统对话框。

    为什么单独写：本机实测 `tkinter.filedialog` 与 pywebview 的原生文件对话框
    都会卡死或无响应（二者都要初始化 COM 并挂接外壳命名空间）。这里只用 Tk
    控件列目录，任何环境都能弹出来。**可在非主线程调用**（pywebview 的 API
    调用就在工作线程）：每次自建 Tk root、在当前线程跑局部 mainloop，用完后
    销毁，不与其它 Tk 对象共享，因此不会触发 "main thread is not in main loop"。

    目录读取放在后台线程，结果经 queue 回 Picker 自己的 mainloop（poll 模式，
    与本项目 _poll_log 同一套路），绝不在工作线程里直接碰控件。
    """
    import queue as _q

    out = {"path": ""}
    root = tk.Tk()
    root.title(title)
    root.geometry("640x480")
    root.minsize(500, 380)

    var = tk.StringVar(value=str(Path.home()))
    top = ttk.Frame(root)
    top.pack(fill="x", padx=8, pady=(8, 4))
    ttk.Label(top, text="目录:").pack(side="left")
    ent = ttk.Entry(top, textvariable=var)
    ent.pack(side="left", fill="x", expand=True, padx=6)

    body = ttk.Frame(root)
    body.pack(fill="both", expand=True, padx=8, pady=4)
    lb = tk.Listbox(body)
    sb = ttk.Scrollbar(body, orient="vertical", command=lb.yview)
    lb.config(yscrollcommand=sb.set)
    lb.pack(side="left", fill="both", expand=True)
    sb.pack(side="left", fill="y")
    status = tk.StringVar(value="")

    def browse(path: Path):
        status.set("读取中…")
        lb.delete(0, tk.END)
        lb.insert(tk.END, "读取中…")

        def work():
            q.put(("dir", path))

        threading.Thread(target=work, daemon=True, name="ls").start()

    q: _q.Queue = _q.Queue()

    def poll():
        try:
            while True:
                kind, val = q.get_nowait()
                if kind == "dir":
                    p = val
                    try:
                        subs = [x for x in sorted(p.iterdir()) if x.is_dir()]
                    except Exception as e:  # noqa: BLE001
                        lb.delete(0, tk.END)
                        lb.insert(tk.END, f"（无法读取：{e}）")
                        status.set(f"无法读取该目录：{e}")
                        return
                    parent = p.parent if p.parent != p else None
                    lb.delete(0, tk.END)
                    if parent:
                        lb.insert(tk.END, "..")
                    for x in subs:
                        lb.insert(tk.END, x.name)
                    var.set(str(p))
                    status.set(f"{len(subs)} 个子目录")
        except _q.Empty:
            pass
        root.after(90, poll)

    def on_double(_e=None):
        sel = lb.curselection()
        if not sel:
            return
        name = lb.get(sel[0])
        cur = Path(var.get())
        browse(cur.parent if name == ".." else cur / name)

    def choose():
        out["path"] = var.get()
        root.destroy()

    lb.bind("<Double-Button-1>", on_double)
    ent.bind("<Return>", lambda _e: browse(Path(var.get())))

    btns = ttk.Frame(root)
    btns.pack(fill="x", padx=8, pady=(4, 8))
    ttk.Button(btns, text="选择此目录", command=choose).pack(side="left", padx=4)
    ttk.Button(btns, text="上一级", command=lambda: browse(Path(var.get()).parent))\
        .pack(side="left", padx=4)
    ttk.Button(btns, text="取消", command=root.destroy).pack(side="right", padx=4)
    ttk.Label(btns, textvariable=status).pack(side="left", padx=12)

    browse(Path.home())
    root.after(90, poll)
    root.mainloop()
    try:
        root.destroy()
    except Exception:  # noqa: BLE001
        pass
    return out["path"]


TIMBRE_LABELS = {
    "不添加": None,
    "电子哔声 (beep)": "beep",
    "木鱼 (woodblock)": "woodblock",
    "牛铃 (cowbell)": "cowbell",
    "短打 (click)": "click",
    "边击 (sidestick)": "sidestick",
    "拍手 (clap)": "clap",
}
# 英文对照表：键顺序与值（音色 key）与中文表严格一致，仅显示文字不同。
TIMBRE_LABELS_EN = {
    "None": None,
    "Beep": "beep",
    "Woodblock": "woodblock",
    "Cowbell": "cowbell",
    "Click": "click",
    "Side-stick": "sidestick",
    "Clap": "clap",
}
ENGINE_LABELS = {
    "保音高（相位声码器，推荐）": "phasevocoder",
    "磁带机式（音高随速度一起变）": "resample",
}
ENGINE_LABELS_EN = {
    "Keep pitch (phase vocoder, recommended)": "phasevocoder",
    "Tape style (pitch follows tempo)": "resample",
}


def _is_en() -> bool:
    return tc.get_lang() == "en"


def timbre_labels() -> dict:
    """当前语言的「显示标签 -> 音色 key」表。"""
    return TIMBRE_LABELS_EN if _is_en() else TIMBRE_LABELS


def engine_labels() -> dict:
    """当前语言的「显示标签 -> 引擎 key」表。"""
    return ENGINE_LABELS_EN if _is_en() else ENGINE_LABELS


def _label_for_key(table: dict, key) -> str:
    """按音色/引擎 key 反查当前语言的显示标签（用于切换语言时保留选择）。"""
    for lab, val in table.items():
        if val == key:
            return lab
    return next(iter(table))  # 兜底取第一项


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("BeatSync")
        # 两列「标签+控件」布局。注意：高 DPI 下（本机 Tk scaling≈1.33）控件是按
        # 缩放后的字体测量的，winfo_reqwidth() 报的却是逻辑值（约 613），**不能直接
        # 拿来当窗口宽度**——按它给窗口，右列的「原速 BPM / 输出格式 / 变速引擎」
        # 会被裁在窗口外。这里按缩放后的真实需求给足（约 1000 物理像素 × 750）。
        root.minsize(1040, 640)

        self.log_q: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.running = False
        self._scan_cancel = False
        self._stopped = False
        self._heartbeat = time.time()

        # body 是一个可重建容器：切换中/EN 语言时销毁并重建它，
        # 从而让所有按当前语言读取的文案（tc.tr / labels()）即时刷新。
        self.body = ttk.Frame(self.root)
        self.body.pack(fill="both", expand=True)

        trace(f"App: 开始构建界面（trace 文件={_TRACE_PATH}）")
        self._build_ui()
        root.after(100, self._poll_log)
        self._start_watchdog()
        trace("App: 界面构建完成，进入事件循环")

    # ---------- 本地化辅助 ----------
    @staticmethod
    def T(zh: str, en: str) -> str:
        """按当前界面语言取文案（语言由 tc.set_lang 全局控制）。"""
        return tc.tr(zh, en)

    # ---------- 界面 ----------
    def _build_ui(self):
        """在 self.body 上按当前语言构建全部控件（可被 _rebuild 反复调用）。"""
        self._build_lang_bar()
        self._build_file_list()
        self._build_params()
        self._build_buttons()
        self._build_log()

    def _build_lang_bar(self):
        bar = ttk.Frame(self.body)
        bar.pack(fill="x", padx=8, pady=(6, 0))
        ttk.Button(bar, text="中 / EN", command=self._toggle_lang, width=8)\
            .pack(side="right", padx=4)

    def _rebuild(self):
        """销毁并重建整个 body（切换语言用），保留文件列表与参数值。"""
        snap = self._snapshot_state()
        for child in self.body.winfo_children():
            child.destroy()
        self._build_ui()
        self._restore_state(snap)

    def _toggle_lang(self):
        if self.running:
            return  # 转换进行中不允许重建界面
        tc.set_lang("zh" if tc.get_lang() == "en" else "en")
        trace(f"切换语言 -> {tc.get_lang()}")
        self._rebuild()

    def _snapshot_state(self) -> dict:
        """记录当前用户输入，供 _rebuild 后原样恢复。"""
        timbre_key = TIMBRE_LABELS.get(self.var_timbre.get(),
                                       TIMBRE_LABELS_EN.get(self.var_timbre.get()))
        engine_key = ENGINE_LABELS.get(self.var_engine.get(),
                                       ENGINE_LABELS_EN.get(self.var_engine.get()))
        return {
            "files": list(self.file_list.get(0, tk.END)),
            "target": self.var_target.get(),
            "bpm": self.var_bpm.get(),
            "detect_only": self.var_detect_only.get(),
            "metronome": self.var_metronome.get(),
            "timbre_key": timbre_key,
            "gain": self.var_gain.get(),
            "engine_key": engine_key,
            "fmt": self.var_format.get(),
            "out_dir": self.var_out_dir.get(),
            "mirror": bool(self.var_mirror.get()),
            "max_slow": self.var_max_slow.get(),
            "max_fast": self.var_max_fast.get(),
            "segments": "auto" if self.var_segments.get() else "off",
            "status": self.var_status.get(),
        }

    def _restore_state(self, s: dict):
        for p in s["files"]:
            self.file_list.insert(tk.END, p)
        self.var_target.set(s["target"])
        self.var_bpm.set(s["bpm"])
        self.var_detect_only.set(s["detect_only"])
        self.var_metronome.set(s["metronome"])
        self.var_timbre.set(_label_for_key(timbre_labels(), s["timbre_key"]))
        self.var_gain.set(s["gain"])
        self.var_engine.set(_label_for_key(engine_labels(), s["engine_key"]))
        self.var_format.set(s["fmt"])
        self.var_out_dir.set(s["out_dir"])
        self.var_mirror.set(s.get("mirror", True))
        self.var_max_slow.set(s.get("max_slow", self._fmt_pct(tc.DEFAULT_MAX_SLOWDOWN)))
        self.var_max_fast.set(s.get("max_fast", self._fmt_pct(tc.DEFAULT_MAX_SPEEDUP)))
        # 旧版本存的是布尔 False（那时默认就是关），不能当成「用户主动关掉」
        self.var_segments.set(s.get("segments", "auto") not in ("off", "0", 0))
        self._sync_mirror_state()
        self._limit_hint_update()
        self._set_status_text(s["status"])

    def _set_status_text(self, text: str):
        """把状态文字按当前语言重新呈现。中文是基准态，逐字保留；
        其它已知状态串按英文重建，未匹配的（如英文完成行）原样显示。"""
        known = {
            "就绪": self.T("就绪", "Ready"),
            "正在停止…": self.T("正在停止…", "Stopping…"),
            "正在扫描文件夹…": self.T("正在扫描文件夹…", "Scanning folder…"),
        }
        if text in known:
            self.var_status.set(known[text])
        else:
            self.var_status.set(text)

    def _build_file_list(self):
        box = ttk.LabelFrame(
            self.body,
            text=self.T(" 1. 待处理文件（可混合添加文件和文件夹） ",
                        " 1. Files to process (mix of files and folders) "))
        box.pack(fill="both", expand=False, padx=8, pady=(8, 4))
        frame = ttk.Frame(box)
        frame.pack(fill="both", expand=True, padx=6, pady=6)

        self.file_list = tk.Listbox(frame, height=7, selectmode=tk.EXTENDED)
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.file_list.yview)
        self.file_list.config(yscrollcommand=sb.set)
        self.file_list.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")

        btns = ttk.Frame(frame)
        btns.pack(side="left", fill="y", padx=(6, 0))
        for zh, en, cmd, attr in (
            ("添加文件…", "Add files…", self.add_files, "btn_add_files"),
            ("添加文件夹…", "Add folder…", self.add_folder, "btn_add_folder"),
            ("移除选中", "Remove selected", self.remove_selected, "btn_remove"),
            ("清空", "Clear", self.clear_files, "btn_clear"),
        ):
            btn = ttk.Button(btns, text=self.T(zh, en), command=cmd, width=14)
            btn.pack(pady=3)
            setattr(self, attr, btn)

    def _build_params(self):
        box = ttk.LabelFrame(self.body, text=self.T(" 2. 转换设置 ", " 2. Conversion settings "))
        box.pack(fill="x", padx=8, pady=4)
        g = ttk.Frame(box)
        g.pack(fill="x", padx=6, pady=6)
        # 两列「标签 + 控件」成对排布：每行最多两对。原先的 4 列网格里，
        # 「原速 BPM（留空=自动检测）」「输出目录（留空=…）」这类长标签会把整行
        # 撑到 1100px 以上，窗口按常规宽度开就看不全右侧（用户反馈的「保持层级
        # 选项看不到」正是被裁在视野外）。两列布局让自然宽度收窄到 ~800px。
        g.columnconfigure(1, weight=1)
        g.columnconfigure(3, weight=1)

        # 第 0 行：目标速度 | 原速 BPM
        ttk.Label(g, text=self.T("目标速度 (BPM):", "Target tempo (BPM):"))\
            .grid(row=0, column=0, sticky="e", padx=4, pady=2)
        self.var_target = tk.StringVar(value="180")
        ttk.Spinbox(g, from_=20, to=300, increment=1, textvariable=self.var_target,
                    width=8).grid(row=0, column=1, sticky="w", padx=4)
        ttk.Label(g, text=self.T("原速 BPM:", "Original tempo (BPM):"))\
            .grid(row=0, column=2, sticky="e", padx=4)
        self.var_bpm = tk.StringVar(value="")
        bpm_cell = ttk.Frame(g)
        bpm_cell.grid(row=0, column=3, sticky="w", padx=4)
        ttk.Entry(bpm_cell, textvariable=self.var_bpm, width=8).pack(side="left")
        ttk.Label(bpm_cell, text=self.T("（留空=自动检测）", "(leave blank to auto-detect)"),
                  foreground="#888888").pack(side="left", padx=(4, 0))

        # 第 1 行：只测速 | 输出格式
        self.var_detect_only = tk.BooleanVar(value=False)
        ttk.Checkbutton(g, text=self.T("只测速（不生成文件）", "Detect tempo only (no output file)"),
                        variable=self.var_detect_only)\
            .grid(row=1, column=0, columnspan=2, sticky="w", padx=4, pady=2)
        ttk.Label(g, text=self.T("输出格式:", "Output format:"))\
            .grid(row=1, column=2, sticky="e", padx=4)
        self.var_format = tk.StringVar(value="wav")
        ttk.Combobox(g, textvariable=self.var_format, state="readonly",
                     values=["wav", "mp3", "flac"], width=8).grid(row=1, column=3, sticky="w", padx=4)

        # 第 2 行：节拍器 | 音色
        self.var_metronome = tk.BooleanVar(value=False)
        ttk.Checkbutton(g, text=self.T("添加节拍器", "Add metronome"), variable=self.var_metronome)\
            .grid(row=2, column=0, sticky="e", padx=4, pady=2)
        tl = timbre_labels()
        default_timbre = _label_for_key(tl, "woodblock")
        self.var_timbre = tk.StringVar(value=default_timbre)
        ttk.Combobox(g, textvariable=self.var_timbre, state="readonly",
                     values=list(tl), width=22).grid(row=2, column=1, sticky="w", padx=4)
        ttk.Label(g, text=self.T("变速引擎:", "Time-stretch engine:")).grid(row=2, column=2, sticky="e", padx=4)
        el = engine_labels()
        self.var_engine = tk.StringVar(value=list(el)[0])
        # width 按英文最长标签给足（"Keep pitch (phase vocoder, recommended)"），
        # 给窄了英文界面会被截成 "…recomm"。
        ttk.Combobox(g, textvariable=self.var_engine, state="readonly",
                     values=list(el), width=36).grid(row=2, column=3, sticky="w", padx=4)

        # 第 3 行：节拍器音量（滑块 + 数值框 | 单位说明）
        ttk.Label(g, text=self.T("节拍器相对音量:", "Metronome level:"))\
            .grid(row=3, column=0, sticky="e", padx=4, pady=2)
        self.var_gain = tk.DoubleVar(value=-6.0)
        # 滑块 + 数值框：滑块拖动会写回 DoubleVar，数值框反过来也能精确输入，
        # 两边共用同一个变量，再用 trace 把值规格化成整数文本（与网页端一致）。
        gain_cell = ttk.Frame(g)
        gain_cell.grid(row=3, column=1, columnspan=3, sticky="ew", padx=4)
        self.scale_gain = ttk.Scale(gain_cell, from_=-20.0, to=12.0,
                                    variable=self.var_gain, orient="horizontal")
        self.scale_gain.pack(side="left", fill="x", expand=True)
        self.var_gain_text = tk.StringVar(value=self._fmt_gain(self.var_gain.get()))
        ent_gain = ttk.Entry(gain_cell, textvariable=self.var_gain_text, width=5, justify="right")
        ent_gain.pack(side="left", padx=(6, 0))
        ttk.Label(gain_cell, text="dB").pack(side="left", padx=(2, 0))
        ttk.Label(gain_cell, text=self.T("（0 = 与音乐等响）", " (0 = as loud as the music)"),
                  foreground="#888888").pack(side="left", padx=(8, 0))
        self.var_gain.trace_add("write", self._on_gain_change)
        ent_gain.bind("<Return>", self._commit_gain_text)
        ent_gain.bind("<FocusOut>", self._commit_gain_text)

        # 第 4 行：输出目录（整行）+ 浏览按钮
        ttk.Label(g, text=self.T("输出目录:", "Output dir:"))\
            .grid(row=4, column=0, sticky="e", padx=4, pady=(6, 2))
        out_frame = ttk.Frame(g)
        out_frame.grid(row=4, column=1, columnspan=3, sticky="ew", padx=4, pady=(6, 2))
        self.var_out_dir = tk.StringVar(value="")
        ttk.Entry(out_frame, textvariable=self.var_out_dir).pack(side="left", fill="x", expand=True)
        ttk.Button(out_frame, text=self.T("浏览…", "Browse…"), command=self.pick_out_dir, width=10)\
            .pack(side="left", padx=(6, 0))
        ttk.Label(g, text=self.T("（留空=每个文件各自目录下的 tempo_converted）",
                                 "(blank = tempo_converted beside each file)"),
                  foreground="#888888")\
            .grid(row=5, column=1, columnspan=3, sticky="w", padx=8)

        # 第 6 行：「保持原来的文件夹层级」——只在填了输出目录时才有意义（与网页端同一规则）
        self.var_mirror = tk.BooleanVar(value=True)
        self.chk_mirror = ttk.Checkbutton(
            g, text=self.T("保持原来的文件夹层级", "Keep the original folder structure"),
            variable=self.var_mirror)
        self.chk_mirror.grid(row=6, column=1, columnspan=3, sticky="w", padx=8, pady=(2, 0))
        self.var_out_dir.trace_add("write", self._sync_mirror_state)
        self._sync_mirror_state()

        # 第 7 行：变速上限（最多放慢 / 最多加快，百分数，0–100）
        # 与网页端 #maxSlowdown/#maxSpeedup 同一语义：超出上限的音乐宁可跳过也不硬拉。
        self.var_max_slow = tk.StringVar(value=self._fmt_pct(tc.DEFAULT_MAX_SLOWDOWN))
        self.var_max_fast = tk.StringVar(value=self._fmt_pct(tc.DEFAULT_MAX_SPEEDUP))
        ttk.Label(g, text=self.T("最多放慢 (%):", "Max slowdown (%):"))\
            .grid(row=7, column=0, sticky="e", padx=4, pady=(6, 0))
        ent_slow = ttk.Entry(g, textvariable=self.var_max_slow, width=6, justify="right")
        ent_slow.grid(row=7, column=1, sticky="w", padx=4, pady=(6, 0))
        ttk.Label(g, text=self.T("最多加快 (%):", "Max speedup (%):"))\
            .grid(row=7, column=2, sticky="e", padx=4, pady=(6, 0))
        ent_fast = ttk.Entry(g, textvariable=self.var_max_fast, width=6, justify="right")
        ent_fast.grid(row=7, column=3, sticky="w", padx=4, pady=(6, 0))
        for _ent, _var in ((ent_slow, self.var_max_slow), (ent_fast, self.var_max_fast)):
            _ent.bind("<Return>", self._commit_limit_text)
            _ent.bind("<FocusOut>", self._commit_limit_text)
        # 实时回显上限说明（与网页端 limitHint 同款文案）
        self.var_limit_hint = tk.StringVar(value=self._limit_hint_text())
        ttk.Label(g, textvariable=self.var_limit_hint, foreground="#888888")\
            .grid(row=8, column=1, columnspan=3, sticky="w", padx=8)
        self.var_max_slow.trace_add("write", self._on_limit_change)
        self.var_max_fast.trace_add("write", self._on_limit_change)

        # 第 9 行：自动分段变速（默认开；检测到明显变拍时每段各按最小变速处理）
        self.var_segments = tk.BooleanVar(value=True)
        self.chk_segments = ttk.Checkbutton(
            g, text=self.T("自动分段变速（检测到明显变拍时每段各按最小变速处理；手动指定原速时不生效）",
                           "Auto per-section (splits on clear tempo changes; "
                           "ignored when source BPM is set)"),
            variable=self.var_segments)
        self.chk_segments.grid(row=9, column=0, columnspan=4, sticky="w", padx=4, pady=(6, 0))

    def _build_buttons(self):
        frame = ttk.Frame(self.body)
        frame.pack(fill="x", padx=8, pady=4)
        self.btn_start = ttk.Button(frame, text=self.T("开始处理", "Start"), command=self.start)
        self.btn_start.pack(side="left", padx=4)
        self.btn_stop = ttk.Button(frame, text=self.T("停止", "Stop"), command=self.stop, state="disabled")
        self.btn_stop.pack(side="left", padx=4)
        self.var_status = tk.StringVar(value=self.T("就绪", "Ready"))
        ttk.Label(frame, textvariable=self.var_status).pack(side="left", padx=12)

    def _build_log(self):
        box = ttk.LabelFrame(self.body, text=self.T(" 3. 处理日志 ", " 3. Log "))
        box.pack(fill="both", expand=True, padx=8, pady=(4, 8))
        self.log_text = scrolledtext.ScrolledText(box, height=12, state="disabled", wrap="word")
        self.log_text.pack(fill="both", expand=True, padx=6, pady=6)

    # ---------- 文件列表 ----------
    # ---------- 目录/文件选择（自带实现，不用系统对话框）----------
    def _ask_directory(self, title: str = "") -> str:
        """自带的目录选择器（纯 Tk，不依赖 COM/资源管理器外壳）。

        为什么不用 filedialog.askdirectory()：本机实测它**直接卡死不返回**
        （gui_trace.log 停在"打开目录对话框…"，之后无任何输出）。系统的
        打开/保存对话框要初始化 COM 并挂接外壳命名空间，在本机被安全策略
        拦住时会永久阻塞主线程。这个实现只列目录、不需要任何 COM。
        """
        trace("_ask_directory: 打开自带目录选择器")
        result: list[str] = []

        dlg = tk.Toplevel(self.root)
        dlg.title(title or self.T("选择文件夹", "Select folder"))
        dlg.transient(self.root)
        dlg.geometry("620x460")
        dlg.minsize(480, 360)

        # 起始目录：用户主目录；再退一步用当前盘根
        cur = Path.home()
        try:
            if not cur.exists():
                cur = Path(cur.anchor)
        except Exception:  # noqa: BLE001
            cur = Path(".")

        var = tk.StringVar(value=str(cur))
        top = ttk.Frame(dlg)
        top.pack(fill="x", padx=8, pady=(8, 4))
        ttk.Label(top, text=tc.tr("目录:", "Directory:")).pack(side="left")
        ent = ttk.Entry(top, textvariable=var)
        ent.pack(side="left", fill="x", expand=True, padx=6)

        body = ttk.Frame(dlg)
        body.pack(fill="both", expand=True, padx=8, pady=4)
        lb = tk.Listbox(body, height=16)
        sb = ttk.Scrollbar(body, orient="vertical", command=lb.yview)
        lb.config(yscrollcommand=sb.set)
        lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")

        status = tk.StringVar(value="")

        def refresh(path: Path, _err: str | None = None):
            """列出子目录（后台线程执行，主线程只负责填充）。"""
            try:
                if _err:
                    raise RuntimeError(_err)
                sub = [p for p in sorted(path.iterdir()) if p.is_dir()]
            except Exception as e:  # noqa: BLE001
                def bad():
                    lb.delete(0, tk.END)
                    lb.insert(tk.END, self.T(f"（无法读取：{e}）", f"(cannot read: {e})"))
                    status.set(self.T(f"无法读取该目录：{e}", f"Cannot read folder: {e}"))
                dlg.after(0, bad)
                return
            parent = path.parent if path.parent != path else None

            def fill():
                lb.delete(0, tk.END)
                if parent is not None:
                    lb.insert(tk.END, "..")
                for p in sub:
                    lb.insert(tk.END, p.name)
                var.set(str(path))
                status.set(self.T(f"{len(sub)} 个子目录", f"{len(sub)} subfolder(s)"))
            dlg.after(0, fill)

        def browse(path: Path):
            """切目录：先在后台列目录，界面立刻显示"读取中"，不阻塞。"""
            status.set(self.T("读取中…", "Reading…"))
            lb.delete(0, tk.END)
            lb.insert(tk.END, self.T("读取中…", "Reading…"))
            threading.Thread(target=refresh, args=(path,),
                             daemon=True, name="ls").start()

        def on_double(_e=None):
            sel = lb.curselection()
            if not sel:
                return
            name = lb.get(sel[0])
            cur_p = Path(var.get())
            if name == "..":
                browse(cur_p.parent)
            else:
                browse(cur_p / name)

        def on_choose():
            result.append(var.get())
            dlg.destroy()

        def on_cancel():
            dlg.destroy()

        lb.bind("<Double-Button-1>", on_double)
        ent.bind("<Return>", lambda _e: browse(Path(var.get())))

        btns = ttk.Frame(dlg)
        btns.pack(fill="x", padx=8, pady=(4, 8))
        ttk.Button(btns, text=self.T("选择此目录", "Choose"), command=on_choose).pack(side="left", padx=4)
        ttk.Button(btns, text=self.T("上一级", "Up"), command=lambda: browse(Path(var.get()).parent))\
            .pack(side="left", padx=4)
        ttk.Button(btns, text=self.T("取消", "Cancel"), command=on_cancel).pack(side="right", padx=4)
        ttk.Label(btns, textvariable=status).pack(side="left", padx=12)

        browse(cur)
        dlg.grab_set()
        self.root.wait_window(dlg)
        picked = result[0] if result else ""
        trace(f"_ask_directory: 返回 {picked!r}")
        return picked

    def add_files(self):
        """添加音频文件：优先用自带目录选择器（系统对话框在本机会卡死）。"""
        trace("add_files: 打开目录选择器（选目录即加入其中音频）")
        d = self._ask_directory(self.T("选择含音频文件的文件夹", "Select the folder containing audio files"))
        if not d:
            return
        self._scan_and_add(Path(d), recursive=False)

    def add_folder(self):
        trace("add_folder: 打开自带目录选择器")
        d = self._ask_directory(self.T("选择文件夹（将递归搜索其中所有音频）",
                                       "Select a folder (audio will be found recursively)"))
        trace(f"add_folder: 选择器返回 {d!r}")
        if not d:
            return
        self._scan_and_add(Path(d), recursive=True)

    def _scan_and_add(self, folder: Path, recursive: bool = True):
        """扫描目录并把音频加进列表。

        扫描在后台线程跑（rglob 整棵树可能很久），结果经 log_q 回主线程插入，
        **绝不在子线程直接碰 Tk**（会抛 main thread is not in main loop）。
        """
        self._set_busy(True, self.T("正在扫描文件夹…", "Scanning folder…"))
        self._scan_cancel = False

        def worker():
            found: list[Path] = []
            err: str | None = None
            trace(f"扫描线程: 开始 rglob {folder} recursive={recursive}")
            try:
                it = folder.rglob("*") if recursive else folder.iterdir()
                for f in it:
                    if self._scan_cancel:
                        trace("扫描线程: 被取消")
                        return
                    try:
                        if f.is_file() and f.suffix.lower() in tc.SUPPORTED_EXTS:
                            found.append(f)
                            if len(found) % 500 == 0:
                                self.log_q.put(("__scan__", len(found)))
                                trace(f"扫描线程: 已找到 {len(found)}")
                    except OSError:
                        continue  # 权限不足/路径过长的条目直接跳过
            except Exception as e:  # noqa: BLE001
                err = f"{type(e).__name__}: {e}"
                trace(f"扫描线程: 异常 {err}")
            trace(f"扫描线程: 结束，共 {len(found)} 个")
            self.log_q.put(("__scanned__", (found, err, folder)))

        threading.Thread(target=worker, daemon=True, name="scan").start()

    def _set_busy(self, busy: bool, status: str) -> None:
        """扫描/转换期间的忙碌态：禁用会重入的按钮 + 更新状态文字。"""
        try:
            self.var_status.set(status if busy or status else self.T("就绪", "Ready"))
        except Exception:  # noqa: BLE001
            pass
        state = "disabled" if busy else "normal"
        for name in ("btn_add_folder", "btn_add_files", "btn_clear", "btn_remove"):
            btn = getattr(self, name, None)
            if btn is not None:
                try:
                    btn.configure(state=state)
                except Exception:  # noqa: BLE001
                    pass

    def _append_file(self, p: Path):
        self._append_files([p])

    def _append_files(self, paths) -> None:
        """批量插入文件（一次读出已有项，避免每个文件都 get(0, tk.END)）。

        还要过滤掉已有的路径，大目录下用 set 判断，别拿 list 反复 in。
        """
        existing = set(self.file_list.get(0, tk.END))
        if not existing and not paths:
            return
        added = 0
        for p in paths:
            s = str(p)
            if s not in existing:
                self.file_list.insert(tk.END, s)
                existing.add(s)
                added += 1
        if added:
            n = self.file_list.size()
            self.var_status.set(self.T(f"已添加 {added} 个文件，共 {n} 个",
                                       f"Added {added} file(s), {n} total"))

    def remove_selected(self):
        for i in reversed(self.file_list.curselection()):
            self.file_list.delete(i)

    def clear_files(self):
        self.file_list.delete(0, tk.END)

    def pick_out_dir(self):
        """输出目录：同样用自带选择器（系统对话框在本机会卡死）。"""
        d = self._ask_directory(self.T("选择输出目录", "Select output directory"))
        if d:
            self.var_out_dir.set(d)

    # ---------- 参数 ----------
    # ---------- 节拍器音量：滑块 ↔ 数值框 ----------
    GAIN_MIN, GAIN_MAX = -20.0, 12.0

    @staticmethod
    def _fmt_gain(v) -> str:
        """把 dB 值格式化成数值框里的文本：整数就省掉小数点（-6 而不是 -6.0）。"""
        try:
            f = float(v)
        except (TypeError, ValueError):
            return "-6"
        return f"{f:g}"

    def _on_gain_change(self, *_a):
        """滑块或数值框改动 → 统一把当前值写进数值框（拖滑块时实时显示）。"""
        if getattr(self, "_gain_sync", False):
            return
        self._gain_sync = True
        try:
            self.var_gain_text.set(self._fmt_gain(self.var_gain.get()))
        finally:
            self._gain_sync = False

    def _commit_gain_text(self, _e=None):
        """数值框回车/失焦：解析文本并夹到 [GAIN_MIN, GAIN_MAX]，非法则回退原值。"""
        raw = self.var_gain_text.get().strip()
        try:
            v = float(raw)
        except ValueError:
            self._on_gain_change()          # 输入不是数字 → 还原显示
            return
        v = max(self.GAIN_MIN, min(self.GAIN_MAX, v))
        self.var_gain.set(v)
        self._on_gain_change()

    def _sync_mirror_state(self, *_a):
        """没填输出目录时，「保持层级」无从谈起 → 置灰（值保留，与网页端一致）。"""
        try:
            self.chk_mirror.config(state="normal" if self.var_out_dir.get().strip() else "disabled")
        except Exception:  # noqa: BLE001
            pass

    # ---------- 变速上限：文本 ↔ 百分数 ----------
    @staticmethod
    def _fmt_pct(ratio) -> str:
        """0.2 → '20'（整数就省掉小数点），与网页端输入框显示一致。"""
        try:
            return f"{float(ratio) * 100:g}"
        except (TypeError, ValueError):
            return "20"

    def _parse_pct(self, var: tk.StringVar, default_ratio: float) -> float:
        """把输入框文本解析成 0–1 的比率；非法/超范围则回退默认值并回写。"""
        raw = var.get().strip().rstrip("%").strip()
        try:
            pct = float(raw)
        except ValueError:
            var.set(self._fmt_pct(default_ratio))
            return default_ratio
        pct = max(0.0, min(100.0, pct))
        var.set(f"{pct:g}")
        return pct / 100.0

    def _commit_limit_text(self, _e=None):
        """上限输入框回车/失焦：夹到 0–100 并规格化显示。"""
        self._parse_pct(self.var_max_slow, tc.DEFAULT_MAX_SLOWDOWN)
        self._parse_pct(self.var_max_fast, tc.DEFAULT_MAX_SPEEDUP)
        self._limit_hint_update()

    def _limit_hint_text(self) -> str:
        """上限说明文案（与网页端 limitHint 同款）。"""
        return self.T(
            f"最多放慢 {self.var_max_slow.get()}% · 最多加快 {self.var_max_fast.get()}%",
            f"Up to {self.var_max_slow.get()}% slower · up to {self.var_max_fast.get()}% faster")

    def _limit_hint_update(self):
        try:
            self.var_limit_hint.set(self._limit_hint_text())
        except Exception:  # noqa: BLE001
            pass

    def _on_limit_change(self, *_a):
        """输入框改动 → 实时刷新说明文字。"""
        if getattr(self, "_limit_sync", False):
            return
        self._limit_sync = True
        try:
            self._limit_hint_update()
        finally:
            self._limit_sync = False

    def _build_options(self) -> tc.ConvertOptions | None:
        try:
            target = float(self.var_target.get())
            if target <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror(
                self.T("错误", "Error"),
                self.T("目标速度必须是正数，例如 180",
                       "Target tempo must be a positive number, e.g. 180"))
            return None
        bpm = None
        if self.var_bpm.get().strip():
            try:
                bpm = float(self.var_bpm.get().strip())
                if bpm <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror(
                    self.T("错误", "Error"),
                    self.T("原速 BPM 必须是正数，或留空自动检测",
                           "Source BPM must be a positive number, or leave it blank to auto-detect"))
                return None
        out_dir = Path(self.var_out_dir.get()) if self.var_out_dir.get().strip() else None
        # 语言一致性兜底：下拉框当前文字在两张表里都查一遍（正常切换时已对齐）。
        tlabel = self.var_timbre.get()
        tkey = timbre_labels().get(tlabel, TIMBRE_LABELS.get(tlabel, TIMBRE_LABELS_EN.get(tlabel)))
        elabel = self.var_engine.get()
        ekey = engine_labels().get(elabel, ENGINE_LABELS.get(elabel, ENGINE_LABELS_EN.get(elabel)))
        return tc.ConvertOptions(
            target_bpm=target,
            bpm=bpm,
            detect_only=self.var_detect_only.get(),
            engine=ekey,
            metronome=tkey if self.var_metronome.get() else None,
            click_rel_db=float(self.var_gain.get()),
            output_format=self.var_format.get(),
            out_dir=out_dir,
            mirror_dirs=bool(self.var_mirror.get()),
            max_slowdown=self._parse_pct(self.var_max_slow, tc.DEFAULT_MAX_SLOWDOWN),
            max_speedup=self._parse_pct(self.var_max_fast, tc.DEFAULT_MAX_SPEEDUP),
            segments=("auto" if self.var_segments.get() else "off"),
            lang=tc.get_lang(),
        )

    # ---------- 处理 ----------
    def start(self):
        if self.running:
            return
        files = [Path(p) for p in self.file_list.get(0, tk.END)]
        trace(f"start: 点击开始，文件数={len(files)}")
        if not files:
            messagebox.showwarning(
                self.T("提示", "Notice"),
                self.T("请先添加要处理的音频文件或文件夹。",
                       "Add audio files or folders first."))
            return
        opt = self._build_options()
        if opt is None:
            return
        if not opt.detect_only and not opt.target_bpm:
            messagebox.showerror(self.T("错误", "Error"),
                                 self.T("请填写目标速度。", "Enter a target tempo."))
            return

        self.running = True
        self._stopped = False
        self.stop_event.clear()
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self._log_clear()
        trace("start: 已清空日志，启动处理线程")
        threading.Thread(target=self._worker, args=(files, opt),
                         daemon=True, name="worker").start()

    def stop(self):
        self.stop_event.set()
        self._scan_cancel = True  # 若正在扫描文件夹，也一并取消
        self.var_status.set(self.T("正在停止…", "Stopping…"))

    def _worker(self, files, opt):
        # 语言快照：整个任务期间日志/状态/报告跟随点击「开始」时界面上的语言，
        # 中途切换语言不会把已生成的文案混成两种。
        L = getattr(opt, "lang", None) or tc.get_lang()

        def tr(zh: str, en: str) -> str:
            return tc.tr(zh, en, L)

        total = len(files)
        trace(f"处理线程: 开始，共 {total} 个文件")
        self.log_q.put(tr(f"开始处理，共 {total} 个文件。", f"Processing {total} file(s)."))
        rows = []
        ok = fail = skip = 0
        for i, p in enumerate(files, 1):
            if self.stop_event.is_set():
                self._stopped = True
                msg = tr("已停止。", "Stopped.")
                self.log_q.put(msg)
                self.log_q.put(("__status__", msg))
                trace("处理线程: 已停止")
                break
            self.log_q.put(f"[{i}/{total}] {p.name}")
            self.log_q.put(("__status__", f"{i}/{total} " + tr("处理中…", "processing…")))
            t0 = time.time()
            trace(f"处理线程: [{i}/{total}] 开始 {p.name}")
            row = tc.process_file(p, opt, rel_base=self._rel_base_for(p), log_fn=self.log_q.put)
            trace(f"处理线程: [{i}/{total}] 结束 {p.name} 耗时 {time.time()-t0:.1f}s "
                  f"备注={row.get('备注','')!r}")
            rows.append(row)
            # 状态判定走 tc.row_status：兼容中/英文备注前缀，不再依赖中文 startswith。
            # 跳过（超出变速上限、只测速等）既不是成功也不是失败，必须单独计一档，
            # 否则界面上会把「没产出文件」的条目报成成功（与桌面版/CLI 口径不一致）。
            _st = tc.row_status(str(row.get("备注") or ""))
            if _st == "failed":
                fail += 1
            elif _st == "skipped":
                skip += 1
            else:
                ok += 1
        if rows:
            report = Path("conversion_report.csv").resolve()
            try:
                tc.write_report(rows, report, lang=L)
                self.log_q.put(tr(f"报告已保存: {report}", f"Report saved: {report}"))
                trace(f"处理线程: 报告已写 {report}")
            except Exception as e:
                self.log_q.put(tr(f"报告写出失败: {e}", f"Failed to write report: {e}"))
                trace(f"处理线程: 报告写出失败 {e}")
        summary = tr(f"完成：成功 {ok}，失败 {fail}，跳过 {skip}。",
                     f"Done: {ok} succeeded, {fail} failed, {skip} skipped.")
        self.log_q.put(summary)
        self.log_q.put(("__status__", summary))
        trace(f"处理线程: 全部完成 ok={ok} fail={fail} skip={skip}")
        self.log_q.put(("__done__", bool(self._stopped)))

    def _poll_log(self):
        # 心跳：主线程每轮都更新一次时间戳；看门狗线程据此判断主线程是否被卡住
        self._heartbeat = time.time()
        try:
            while True:
                msg = self.log_q.get_nowait()
                # ---- 文件夹扫描（后台线程 → 主线程）----
                if isinstance(msg, tuple) and msg[0] == "__scan__":
                    self.var_status.set(
                        self.T(f"正在扫描… 已找到 {msg[1]} 个",
                               f"Scanning… {msg[1]} found so far"))
                    continue
                if isinstance(msg, tuple) and msg[0] == "__status__":
                    self.var_status.set(str(msg[1]))
                    continue
                if isinstance(msg, tuple) and msg[0] == "__scanned__":
                    trace("主线程: 收到 __scanned__，开始插入列表")
                    self._on_scanned(*msg[1])
                    trace("主线程: 列表插入完成")
                    continue
                if isinstance(msg, tuple) and msg[0] == "__done__":
                    self.running = False
                    self.btn_start.config(state="normal")
                    self.btn_stop.config(state="disabled")
                    # 完成/停止的状态文字由 __status__ 消息带过；为空才兜底回「就绪」。
                    if not str(self.var_status.get()).strip():
                        self.var_status.set(self.T("就绪", "Ready"))
                else:
                    self._log_append(str(msg))
        except queue.Empty:
            pass
        except Exception as e:  # noqa: BLE001
            trace(f"主线程: _poll_log 异常 {type(e).__name__}: {e}")
        self.root.after(100, self._poll_log)

    def _start_watchdog(self):
        """看门狗：主线程超过 15 秒没心跳就写日志。

        用途：界面真卡住时，gui_trace.log 会明确写下"主线程疑似卡住"，
        配合前面的步骤记录就能定位是哪一步阻塞（而不是靠猜）。
        """
        def beat():
            while True:
                time.sleep(5)
                gap = time.time() - getattr(self, "_heartbeat", time.time())
                if gap > 15:
                    trace(f"看门狗: 主线程疑似卡住，已 {gap:.0f}s 无心跳")

        threading.Thread(target=beat, daemon=True, name="watchdog").start()

    def _on_scanned(self, found, err, folder=None):
        """扫描结束（在主线程执行）：恢复按钮、报错/提示、批量插入列表。"""
        self._set_busy(False, self.T("就绪", "Ready"))
        if err:
            messagebox.showerror(self.T("扫描失败", "Scan failed"),
                                 self.T(f"读取文件夹时出错：\n{err}", f"Error reading folder:\n{err}"))
            return
        if not found:
            messagebox.showinfo(self.T("提示", "Notice"),
                                self.T("该文件夹（含子目录）中没有找到支持的音频文件。",
                                       "No supported audio files were found in this folder (including subfolders)."))
            return
        if folder is not None:
            # 记下「用户添加的根」，处理时用它算相对层级（指定输出目录时镜像原文件夹结构）
            roots = getattr(self, "_roots", None)
            if roots is None:
                roots = self._roots = []
            fp = str(Path(folder).resolve()).lower()
            if fp not in {str(Path(r).resolve()).lower() for r in roots}:
                roots.append(Path(folder))
        self._append_files(sorted(found))  # 批量插入，避免逐个 get(0,END) 的 O(n²)

    def _rel_base_for(self, p: Path):
        """这个文件属于哪个「用户添加的根」——取能包含它的最深的那个根。"""
        best = None
        for r in getattr(self, "_roots", []) or []:
            try:
                p.resolve().relative_to(Path(r).resolve())
            except (ValueError, OSError):
                continue
            if best is None or len(Path(r).parts) > len(Path(best).parts):
                best = r
        return Path(best) if best is not None else None

    def _log_append(self, msg):
        try:
            self.log_text.config(state="normal")
            self.log_text.insert(tk.END, msg + "\n")
            self.log_text.see(tk.END)
            self.log_text.config(state="disabled")
        except Exception as e:  # noqa: BLE001
            trace(f"日志写入失败（忽略）: {type(e).__name__}: {e}")

    def _log_clear(self):
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.config(state="disabled")


def main():
    root = tk.Tk()
    # 主题：优先选观感好的原生主题。注意 ttk 在「主题不存在」时才会抛异常，
    # 而在打包/无 DPI 感知的环境下 vista 可能静默不可用 → 逐个试，最后兜底 clam。
    style = ttk.Style()
    available = set(style.theme_names())
    for name in ("vista", "winnative", "xpnative", "clam"):
        if name in available:
            try:
                style.theme_use(name)
                break
            except Exception:  # noqa: BLE001
                continue
    # 高 DPI：让窗口在缩放显示器上不发虚（失败无所谓，不影响功能）
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001
        pass
    root.title("BeatSync")
    # 窗口/任务栏图标（.ico 随 assets/ 一起提供；缺失则保持系统默认）
    ico = _icon_path()
    if ico:
        try:
            root.iconbitmap(ico)
        except Exception:  # noqa: BLE001
            pass
    root.geometry("1060x780")
    App(root)
    root.mainloop()


def smoke_test():
    """无界面冒烟测试：创建窗口、实例化控件、立即销毁。"""
    root = tk.Tk()
    root.withdraw()
    style = ttk.Style()
    available = set(style.theme_names())
    for name in ("vista", "winnative", "xpnative", "clam"):
        if name in available:
            try:
                style.theme_use(name)
                break
            except Exception:  # noqa: BLE001
                continue
    app = App(root)
    root.update_idletasks()
    root.update()
    app._log_append("冒烟测试")
    root.destroy()
    print("GUI smoke test OK")


if __name__ == "__main__":
    main()
