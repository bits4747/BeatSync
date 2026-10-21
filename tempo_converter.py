#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""音乐速度转换器 —— 检测歌曲速度，并以“尽量小”的变速把音乐转换到指定速度。

核心思路：
    节拍器**始终以目标速度发声**（目标 180 就是实打实每秒 3 下）；
    音乐只做尽量小的变速，拉伸到 A′，使得每个音乐拍敲 target ÷ A′ 下——
    每拍敲几下只取两类值，判据是**敲击不能与音乐拍错位**：
        · 整数 1…20        → 「每拍敲 N 下」，每拍内都有落点、第一下踩在拍点上
        · 分子为 1 的分数 1/2…1/20 → 「每 N 拍敲 1 下」，落点全在整数拍位上
    被排除的是 1.5、4/3 这类"非 1 分数"：既不是每拍都有落点，落点也不全在拍位上
    （1.5 → 0, 2/3, 4/3, 2, …，拍序号 1、3、5 的拍点上没有敲击）——用户实测
    "听起来很奇怪"。

    变速还必须落在用户设定的上限内（默认最多放慢 20%、最多加快 50%，可调
    0%~100%）；两个方向都超上限时**跳过该文件**，不硬套一个超限方案。

    例：原速 85 BPM，目标 180 BPM
        → 音乐 85→90（+5.9%），每拍敲 2 下，节拍器仍是 180
    例：原速 60 BPM，目标 180 BPM
        → 音乐完全不动（60→60），每拍敲 3 下（180 ÷ 60 正好 3 倍）
    例：原速 120 BPM，目标 180 BPM
        → 加速档：音乐 120→180（+50%），每拍敲 1 下
          （减速档要 −25%，默认上限 20% 放不下；把上限调到 30% 就会改走减速档
            120→90、每拍 2 下——见 DEFAULT_MAX_SLOWDOWN 那段注释）

命令行示例：
    python tempo_converter.py song.mp3 -t 180                    # 自动测速并转换
    python tempo_converter.py song.mp3 -t 180 --metronome woodblock
    python tempo_converter.py ./music -t 120 --recursive         # 批量转换整个文件夹
    python tempo_converter.py song.flac -t 90 --bpm 88           # 手动指定原速
    python tempo_converter.py song.mp3 --detect-only             # 只测速不转换
    python tempo_converter.py song.mp3 -t 180 --strict           # 精确拉伸到 180（变速可能很大）
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import re
import sys
from dataclasses import dataclass
from typing import NamedTuple
from pathlib import Path

import numpy as np


def _setup_numba_cache() -> str:
    """把 numba 的 JIT 缓存目录指到一个确定可写的位置（返回该目录）。

    为什么必须在导入 librosa **之前**做：numba 的模块级装饰器（librosa 导入时就会
    触发，见 `librosa/core/notation.py`）会调用 `_CacheLocator.ensure_cache_path()`，
    它的实现是「往缓存目录里写一个临时文件来测可写性」：

        path = self.get_cache_path(); os.makedirs(path, exist_ok=True)
        tempfile.TemporaryFile(dir=path).close()

    一旦这个缓存目录落在一个**写入会被挂起**的位置（受限的 TEMP、只读的程序目录、
    被杀软/策略盯上的路径），整个进程就在 **import librosa 阶段永久卡死**——
    表现为"点开始处理卡在第一首歌、毫无日志输出"。指定一个已知可写的
    NUMBA_CACHE_DIR 可以让 numba 跳过那个默认位置的探测。

    这里放在公共模块顶层，保证**任何**入口（CLI / 桌面壳 / tkinter 界面）
    都一定先经过它，不依赖各入口自己记得设置。
    """
    existing = os.environ.get("NUMBA_CACHE_DIR")
    if existing:
        return existing
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).parent / "_ncache")
    candidates.append(Path(__file__).parent / "_ncache")
    import tempfile
    candidates.append(Path(tempfile.gettempdir()) / "tmc_numba_cache")
    for cache in candidates:
        try:
            cache.mkdir(parents=True, exist_ok=True)
            probe = cache / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            os.environ["NUMBA_CACHE_DIR"] = str(cache)
            return str(cache)
        except Exception:  # noqa: BLE001
            continue
    return ""


NUMBA_CACHE_DIR = _setup_numba_cache()

# 支持的输入格式（m4a/aac/wma 需要系统安装 ffmpeg 才能读取）
SUPPORTED_EXTS = {".wav", ".mp3", ".flac", ".ogg", ".oga", ".m4a", ".aac", ".wma", ".aif", ".aiff"}

# 测速结果**不再**归一化到 [70,180) —— 测到什么就报什么。
# 这两个常量仅保留供历史代码/文档引用，不再参与检测结果的换算。
TEMPO_LOW, TEMPO_HIGH = 70.0, 180.0

# 变速上限（用户可无极调节）：把音乐放慢/加快太多听感很怪，所以只接受速度变化
# 落在 [1-max_slowdown, 1+max_speedup] 内的方案；两个方向都不满足就跳过该文件。
#
# 默认 20%/50% —— 这只是**界面初始值**，用户随时可调（0%~100%）。
# 为什么曾经定 30%（保留这段以免以后又"优化"回去）：
#   target=180 时 120 需要 -25% 或 +50%，两个方向正好都在边界附近；30%/50%
#   能让 120 稳定走 -25% 减速档，而 20%/50% 会把它推向 +50% 加速档。
#   2026-10-12 用户明确要求默认改成 20%（并确认知道这个副作用、可自行调高），
#   因此 120→180 在默认值下将走「加速到 180、每拍 1 下」而非「减速到 90、每拍 2 下」。
# 另一条历史教训（仍适用于任何上限组合）：只留 25% 太贴边——检测值比真值高
#   0.2 BPM（项目自带 song_120bpm 实测 120.19）就会让减速变成 25.1% 而被判超限。
DEFAULT_MAX_SLOWDOWN = 0.20
DEFAULT_MAX_SPEEDUP = 0.50
LIMIT_MIN, LIMIT_MAX = 0.0, 1.0        # 两个上限的可调范围：0%~100%

# OGG/Vorbis 单次可安全写出的采样点数上限（保守取值，实测崩溃点在 ~500900）。
# libsndfile 超限时是原生崩溃而非异常，必须提前绕开，见 write_audio。
_OGG_SAFE_FRAMES = 500_000

# 每拍敲几下的候选集（2026-10-11 定稿）。
#
# 组成（两类，上限都是 20）：
#   · 整数 1~CPB_MAX        → 「每拍敲 N 下」
#   · 分子为 1 的分数 1/2~1/CPB_MAX → 「每 N 拍敲 1 下」
#
# 判据：**敲击与音乐拍不能"错位"**。具体分两种可接受情形：
#   (a) 整数 cpb → 每一拍内都有敲击，且每拍的第一下正好落在拍点上
#       cpb=3 → 敲击在拍位 0, 1/3, 2/3, 1, 4/3, … 每拍都有落点 ✔
#   (b) cpb = 1/n → 每 n 拍敲 1 下，**所有**敲击都在整数拍位上
#       cpb=1/3 → 拍位 0, 3, 6, 9, … 全部踩在拍点上 ✔
# 而被排除的非 1 分数（1.5=3/2、4/3、2/3）两头都不占：既不是每拍都有落点，
# 落点也不全在拍位上（1.5 → 0, 2/3, 4/3, 2, … 其中 2/3、4/3 落在拍与拍之间，
# 且拍序号 1、3、5 的拍点上没有敲击）——用户实测"听起来很奇怪"。
# 所以「每拍敲 3 下」(3) 与「每 2 拍敲 3 下」(1.5) 不是一回事，只有后者被禁。
#
# 上限定 20 由用户指定：cpb 越大音乐被压得越慢（每拍敲 20 下 = 音乐只有目标的
# 1/20），实际多会被用户设定的变速上限挡掉；1/n 侧则用于"原速远高于目标"的
# 情形（如目标 60、原速 180 → cpb=1/3、音乐完全不变、每 3 拍敲 1 下）。
CPB_MAX = 20
CPB_CANDIDATES = (tuple(float(i) for i in range(1, CPB_MAX + 1))
                  + tuple(1.0 / i for i in range(2, CPB_MAX + 1)))

TIMBRES = ["beep", "woodblock", "cowbell", "click", "sidestick", "clap"]
TIMBRE_NAMES = {
    "beep": "电子哔声",
    "woodblock": "木鱼",
    "cowbell": "牛铃",
    "click": "短打",
    "sidestick": "边击",
    "clap": "拍手",
}

TIMBRE_NAMES_EN = {
    "beep": "beep",
    "woodblock": "woodblock",
    "cowbell": "cowbell",
    "click": "click",
    "sidestick": "side-stick",
    "clap": "clap",
}


def timbre_name(key: str, lang=None) -> str:
    """音色键 → 当前语言显示名（未知键原样返回）。"""
    if (lang or _LANG) == "en":
        return TIMBRE_NAMES_EN.get(key, key)
    return TIMBRE_NAMES.get(key, key)


_WARMED = {"ok": False, "err": ""}


def warm_up() -> tuple[bool, str]:
    """预热音频引擎：导入 librosa + 跑一次极小的解码与测速。

    为什么要预热：librosa/numba 的**首次**使用含大量一次性开销（导入、numba 缓存
    目录探测、JIT 编译）。把它放在第一首歌里，界面就会长时间停在"[1/3] 处理中"，
    看起来像卡死。放在启动阶段并配可见提示，用户就知道"在初始化"而不是"没反应"。
    返回 (成功?, 错误说明)。失败不致命——真正处理时会再报具体错误。
    """
    if _WARMED["ok"]:
        return True, ""
    try:
        import librosa  # noqa: F401
        import numpy as _np
        sr = 22050
        t = _np.arange(int(sr * 0.5)) / sr
        # 一段 120 BPM 的简单脉冲，足够触发解码/STFT/beat_track 的初始化路径
        y = _np.zeros_like(t)
        for k in range(2):
            i = int(k * 0.5 * sr)
            n = min(400, len(y) - i)
            y[i:i + n] += _np.sin(2 * _np.pi * 220 * t[:n]) * _np.exp(-t[:n] / 0.05)
        onset = librosa.onset.onset_strength(y=y.astype(_np.float32), sr=sr)
        librosa.beat.beat_track(onset_envelope=onset, sr=sr, units="time", trim=False)
        _WARMED["ok"] = True
        return True, ""
    except Exception as e:  # noqa: BLE001
        _WARMED["err"] = f"{type(e).__name__}: {e}"
        return False, _WARMED["err"]


def log(msg: str) -> None:
    print(msg, flush=True)


def _setup_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ---------------------------------------------------------------- 语言层
# 全部用户可见文字（关系/上限说明/日志/报告表头与取值/CLI/弹窗）都经这一层出中英。
# 默认中文：run_checks 与既有中文报告口径不受影响；只有显式切到 en 才变英文。
# 行数据 dict 的**键**始终是中文（内部索引 + run_checks 读 CSV 都靠它），只有写进
# CSV 的**表头/取值**按语言本地化——见 write_report / process_file。

_LANG = (os.environ.get("BEATSYNC_LANG", "zh") or "zh").strip().lower()
if _LANG not in ("zh", "en"):
    _LANG = "zh"


def set_lang(lang) -> str:
    """切换当前输出语言（"zh"/"en"），返回生效值。非法值忽略、维持原状。"""
    global _LANG
    v = str(lang or "").strip().lower()
    if v in ("zh", "en"):
        _LANG = v
    return _LANG


def get_lang() -> str:
    return _LANG


def tr(zh: str, en: str, lang=None) -> str:
    """按当前（或指定）语言取中文或英文文案。lang=None 时读全局 _LANG。"""
    return zh if (lang or _LANG) == "zh" else en


# 第三方库（libsndfile / soundfile）抛的是固定英文错误串，直接 str(e) 进备注会让
# 中文界面漏出英文。这里把已知错误短语映射成中文；英文界面原样返回。未命中的一律
# 保留原文（宁可露一句英文，也不误改语义）。
_ERR_ZH = [
    (r"Error opening '([^']*)'[:\s]*", r"无法打开 \1："),
    (r"Error opening [^:]*:\s*", "无法打开文件："),
    (r"Format not recognised\.?", "无法识别的音频格式。"),
    (r"Supported file format but file is malformed\.?", "文件格式受支持，但内容已损坏或不完整。"),
    (r"Supported file format but unsupported encoding\.?", "文件格式受支持，但其编码不被支持。"),
    (r"File does not exist or is not a regular file \(possibly a pipe\?\)\.?", "文件不存在，或不是常规文件。"),
    (r"File exists but no data could be read\.?", "文件存在，但读不到任何数据。"),
    (r"Could not open file\.?", "无法打开文件。"),
    (r"Unspecified internal error\.?", "未指定的内部错误。"),
    (r"System error\.?", "系统错误。"),
    (r"Internal malloc \(\) failed\.?", "内存分配失败。"),
    (r"Channel count is zero\.?", "声道数为 0。"),
    (r"Too many channels specified\.?", "声道数过多。"),
    (r"Bad channel count\.?", "声道数无效。"),
    (r"File contains data in an unimplemented format\.?", "文件使用了尚未实现的格式。"),
    (r"Seek attempted on unseekable file type\.?", "该文件类型不支持定位读取。"),
]


def localize_error(msg: str, lang=None) -> str:
    """把第三方库的英文错误短语按语言本地化。lang=zh 才翻译，en 原样返回。"""
    if (lang or _LANG) != "zh":
        return msg
    out = str(msg)
    for pat, rep in _ERR_ZH:
        out = re.sub(pat, rep, out)
    return out



# 报告字段：固定 (内部中文键, 中文表头, 英文表头)。写 CSV 时按语言选表头。
REPORT_FIELDS = [
    ("输入文件", "输入文件", "Input File"),
    ("检测BPM", "检测BPM", "Detected BPM"),
    ("检测方式", "检测方式", "Detection"),
    ("目标BPM", "目标BPM", "Target BPM"),
    ("拉伸后BPM", "拉伸后BPM", "Stretched BPM"),
    ("拉伸比", "拉伸比", "Stretch Ratio"),
    ("速度变化", "速度变化", "Tempo Change"),
    ("节拍关系", "节拍关系", "Click Pattern"),
    ("节拍器BPM", "节拍器BPM", "Metronome BPM"),
    ("每拍点击数", "每拍点击数", "Clicks Per Beat"),
    ("音乐响度dB", "音乐响度dB", "Music Loudness dB"),
    ("节拍器", "节拍器", "Metronome"),
    ("输出文件", "输出文件", "Output File"),
    ("输出时长秒", "输出时长秒", "Output Length s"),
    ("备注", "备注", "Note"),
]

# 稳定机器状态：不受显示语言影响，替代各处 note.startswith("跳过"/"失败")。
# 兼容中英两套前缀，因此 run_checks（中文）与英文界面都能判对。
def row_status(note) -> str:
    s = str(note or "").strip()
    if s.startswith("跳过") or s.startswith("Skipped") or s.startswith("SKIP"):
        return "skipped"
    if s.startswith("失败") or s.startswith("Failed") or s.startswith("FAIL"):
        return "failed"
    return "done"


# ---------------------------------------------------------------- 速度方案

def normalize_tempo(bpm: float) -> float:
    """**已停用**：历史上把测速结果折叠到 [70, 180)。

    保留函数只为兼容外部调用；当前检测路径不再使用它（见 detect_tempo）。
    直接返回原值，不做任何 2 的整数次幂换算。
    """
    return float(bpm)


def _fold_tempo_legacy(bpm: float) -> float:
    """历史实现（折叠到 [70, 180)），仅供对照/回溯测试使用。"""
    b = float(bpm)
    if b <= 0:
        return b
    while b < TEMPO_LOW:
        b *= 2.0
    while b >= TEMPO_HIGH:
        b /= 2.0
    return b


class StretchPlan(NamedTuple):
    """一套变速+数拍方案。

    节拍器**始终以目标速度发声**（click_bpm == target），音乐按与目标的简单
    倍数关系做最小变速：clicks_per_beat = target / a2，取**整数**或**分子为 1
    的分数**（如 60→180 得 a2=60、每拍敲 3 下；180→60 得 a2=180、每 3 拍敲 1 下）。
    这样听感上永远是「节奏器打在目标速度上」，不会出现 360 BPM 的机关枪。
    """
    a2: float              # 音乐实际被拉伸到的 BPM
    rate: float            # 拉伸比（>1 加速变短，<1 减速变长）
    click_bpm: float       # 节拍器实际发声速率（恒等于目标 BPM）
    clicks_per_beat: float # 每个音乐拍敲几下（可为分数）
    j: int                 # 保留字段：数拍折算，新方案恒为 0


def choose_stretched_tempo(detected: float, target: float, strict: bool = False,
                           max_slowdown: float = DEFAULT_MAX_SLOWDOWN,
                           max_speedup: float = DEFAULT_MAX_SPEEDUP) -> StretchPlan | None:
    """选"拉伸到多少 BPM"，使节拍器以目标速度敲、且变速尽量小。

    核心规则（2026-10-09 起，2026-10-11 定稿为「整数 / 分子为 1 的分数 + 变速上限」）：
    **节拍器恒以目标 BPM 发声**（click_bpm == target），音乐拉伸到
    a2 = target / clicks_per_beat，其中每个音乐拍敲几下 clicks_per_beat 取
        · 整数 1 … CPB_MAX              → 每拍敲 N 下
        · 分子为 1 的分数 1/2 … 1/CPB_MAX → 每 N 拍敲 1 下
    判据是**敲击与音乐拍不错位**：
        · 整数 cpb：每一拍内都有敲击，且每拍的第一下正好落在拍点上
          （cpb=3 → 拍位 0, 1/3, 2/3, 1, 4/3, … 每拍都有落点）
        · cpb = 1/n：每 n 拍敲 1 下，**所有**敲击都踩在整数拍位上（0, n, 2n, …）
    被排除的是 1.5、4/3、2/3 这类"非 1 分数"：既不是每拍都有落点，落点也不全在
    拍位上（1.5 → 0, 2/3, 4/3, 2, …，其中 2/3、4/3 落在拍与拍之间，且拍序号
    1、3、5 的拍点上没有敲击）——用户实测"听起来很奇怪"。
    **「每拍敲 3 下」(3) 与「每 2 拍敲 3 下」(1.5) 不是一回事**，只有后者被禁。

    **变速上限（2026-10-11 新增）**：把音乐放慢太多（"整首歌拖沓了"）听感很怪，
    所以只保留速度变化落在下面区间内的候选：
        1 - max_slowdown  <=  rate  <=  1 + max_speedup
    两个上限都由用户无极调节（GUI 滑块 / CLI `--max-slowdown --max-speedup`）。
    区间内取 |log2(rate)| 最小者（变速幅度最小）——所以只要"音乐完全不变速"
    的候选存在（rate=1、|log2|=0），它一定胜出。

    **若一个候选都不满足 → 返回 None**，表示"这首歌两个上限都不满足"，
    调用方（process_file）应**跳过该文件**而不是硬套一个超限的方案。
    注意：上限是硬边界，会存在"减速差一点、加速也差一点"的空隙（例如
    原速 120、目标 180，上限 20%/41% 时 减速需 25%、加速需 50%，两个都超）。
    默认 20%/50% 下这个特定空隙不存在（120→180 走 +50% 加速档），但仍应知道
    空隙随上限组合可能出现；另外测速有正常误差（实测 song_120bpm 检测为
    120.19），上限贴着真实需求设就会被误差顶出边界。

    strict=True 时忽略上限，直接精确拉伸到目标（那是用户显式要求的"真变速"）。

    例（默认上限 20%/50% 下，均为实测值）：
        60→180   : a2 = 60，每拍敲 3 下（0%，音乐完全不动——180/60 正好 3 倍）
        85→180   : a2 = 90，每拍敲 2 下（+5.9%）
        120→180  : a2 = 180，每拍敲 1 下（+50%，加速档；上限调到 30% 才会变成
                   减速到 90、每拍 2 下）
        180→60   : a2 = 180，每 3 拍敲 1 下（0%，音乐不动——反向同理）
    旧版会把节拍器抬到目标×2（180→360），听感上就是每秒 6 下的机关枪——已废弃。
    """
    target = float(target)
    if strict:
        return StretchPlan(target, target / detected, target, 1.0, 0)
    cpb_candidates = CPB_CANDIDATES
    lo = 1.0 - max(0.0, float(max_slowdown))
    hi = 1.0 + max(0.0, float(max_speedup))
    # 浮点容差：rate 恰好等于边界时应当**保留**（如 120→90 恰好 -25%）
    eps = 1e-9
    best = None
    for cpb in cpb_candidates:
        a2 = target / cpb
        rate = a2 / detected
        if rate < lo - eps or rate > hi + eps:
            continue                      # 超出用户设定的变速上限，不采用
        # 平手时优先「每拍整数下」，其次让音乐更接近原速
        key = (abs(math.log2(rate)), 0 if float(cpb).is_integer() else 1, cpb)
        if best is None or key < best[0]:
            best = (key, a2, cpb)
    if best is None:
        return None                       # 两个方向都不满足 → 调用方跳过该文件
    _, a2, cpb = best
    return StretchPlan(a2, a2 / detected, target, float(cpb), 0)


def stretch_limits_text(detected: float, target: float,
                        max_slowdown: float = DEFAULT_MAX_SLOWDOWN,
                        max_speedup: float = DEFAULT_MAX_SPEEDUP,
                        lang=None) -> str:
    """给"跳过"写清楚原因：两个方向各自最接近的方案差多少、上限是多少。

    取"幅度最小"的那个减速候选与加速候选来报，例如：
        需减速 25.0%（上限 20%）或加速 50.0%（上限 41%）均超出
    """
    cpb_candidates = CPB_CANDIDATES
    slows, fasts = [], []
    for cpb in cpb_candidates:
        rate = (target / cpb) / detected
        delta = rate - 1.0
        if delta < -1e-9:
            slows.append(-delta)          # 减速幅度（正数）
        elif delta > 1e-9:
            fasts.append(delta)           # 加速幅度（正数）
    parts = []
    L = lang or _LANG
    if slows:
        if L == "en":
            parts.append(f"needs {min(slows) * 100:.1f}% slowdown (limit {max_slowdown * 100:.0f}%)")
        else:
            parts.append(f"需减速 {min(slows) * 100:.1f}%（上限 {max_slowdown * 100:.0f}%）")
    if fasts:
        if L == "en":
            parts.append(f"needs {min(fasts) * 100:.1f}% speedup (limit {max_speedup * 100:.0f}%)")
        else:
            parts.append(f"需加速 {min(fasts) * 100:.1f}%（上限 {max_speedup * 100:.0f}%）")
    if not parts:
        return "No feasible option" if L == "en" else "无可用方案"
    if L == "en":
        return " and ".join(parts) + ", both beyond limits"
    return "、".join(parts) + "，均超出"


def relation_text(clicks_per_beat: float, j: int = 0, lang=None) -> str:
    """把节拍关系翻译成人话——只有两种说法（2026-10-11 用户指定）：

        「每拍敲 N 下」   ← cpb 为整数（每拍敲 N 下）
        「每 N 拍敲 1 下」 ← cpb = 1/N

    节拍器恒为目标速度，所以描述的是"音乐拍与敲击的对应关系"：
        每拍敲 3 下      （cpb=3，180÷60=3 → 音乐 60 不变）
        每拍敲 2 下      （cpb=2）
        每拍敲 1 下      （cpb=1，音乐正好等于目标）
        每 2 拍敲 1 下   （cpb=1/2，音乐被加速到目标的 2 倍）
        每 3 拍敲 1 下   （cpb=1/3，音乐被加速到目标的 3 倍）

    其它值（1.5、4/3…）主流程不会产生——它们会让敲击落在拍与拍之间、且有些
    拍点上没有落点（用户实测"很奇怪"）。这里仍给个说明性文字，供手工传入的
    cpb 或历史报告兜底：形如「每 2 拍敲 3 下」。
    """
    from fractions import Fraction
    cpb = float(clicks_per_beat)
    L = lang or _LANG
    # 先看是不是 1/N（含 1/1=1）——用分数精确判断，避免浮点误差
    fr = Fraction(cpb).limit_denominator(64)
    if fr.numerator == 1:
        n = fr.denominator
        if L == "en":
            return "1 click per beat" if n == 1 else f"1 click per {n} beats"
        return f"每拍敲 1 下" if n == 1 else f"每 {n} 拍敲 1 下"
    if fr.denominator == 1:                     # 整数
        return (f"{fr.numerator} clicks per beat" if L == "en"
                else f"每拍敲 {fr.numerator} 下")
    # 兜底：既非整数也非 1/N（正常情况下不会走到这里）
    return (f"{fr.numerator} clicks per {fr.denominator} beats" if L == "en"
            else f"每 {fr.denominator} 拍敲 {fr.numerator} 下")


# ---------------------------------------------------------------- 音频 IO

def load_audio(path: Path):
    """读取音频，返回 (y, sr)。y 形状统一为 (声道, 采样数)。"""
    import librosa

    y, sr = librosa.load(str(path), sr=None, mono=False)
    if y.ndim == 1:
        y = y[np.newaxis, :]
    return y.astype(np.float32), int(sr)


# MP3 输出码率控制。
# libsndfile 没有「码率」参数，而是用 compression_level(0~1) 配合
# bitrate_mode="CONSTANT" 映射到一组标准 CBR 码率。下面这张表是在
# libsndfile 1.2.2 / soundfile 0.14.0 上逐档实测出来的（60 秒素材量体积）：
#   0.00~0.10 -> 320   0.15~0.25 -> 256   0.30~0.35 -> 224   0.40~0.45 -> 192
#   0.50~0.60 -> 160   0.65      -> 128   0.70      -> 112   0.75~0.80 -> 96
# 注意：不指定这两个参数时，libsndfile 默认给的是约 142 kbps 的变码率——
# 那就是「输入 320k、输出掉到 40% 体积、听感发沙」的根源，所以这里必须显式指定。
MP3_BITRATES = (320, 256, 224, 192, 160, 128, 112, 96)
_MP3_COMPRESSION = {320: 0.00, 256: 0.15, 224: 0.30, 192: 0.40,
                    160: 0.50, 128: 0.65, 112: 0.70, 96: 0.75}
DEFAULT_MP3_BITRATE = 320


def source_bitrate_kbps(path: Path):
    """估算输入文件的实际码率（kbps）；拿不到返回 None。

    用「文件字节数 × 8 ÷ 时长」算，对 CBR 和 VBR 都成立（VBR 得到的是平均值）。
    """
    try:
        import soundfile as sf

        info = sf.info(str(path))
        if not info.duration or info.duration <= 0:
            return None
        return int(round(os.path.getsize(path) * 8 / info.duration / 1000.0))
    except Exception:
        return None


def _snap_mp3_bitrate(kbps) -> int:
    """把任意码率吸附到最接近的可选档位（等距时取较高的一档）。"""
    try:
        v = float(kbps)
    except (TypeError, ValueError):
        return DEFAULT_MP3_BITRATE
    return min(MP3_BITRATES, key=lambda b: (abs(b - v), -b))


def resolve_mp3_bitrate(requested, path: Path) -> int:
    """把用户选择解析成实际 CBR 码率（kbps）。

    requested 可以是 "auto"/"source"/None（跟随原文件）、数字或数字字符串。
    跟随原文件时：读不出输入码率（如 wav 输入）就用 320。
    """
    if requested is None or (isinstance(requested, str)
                             and requested.strip().lower() in ("", "auto", "source")):
        src = source_bitrate_kbps(path)
        return DEFAULT_MP3_BITRATE if src is None else _snap_mp3_bitrate(src)
    if isinstance(requested, str):
        try:
            requested = int(float(requested.strip()))
        except ValueError:
            return DEFAULT_MP3_BITRATE
    return _snap_mp3_bitrate(requested)


def write_audio(path: Path, y, sr: int, lang=None, mp3_bitrate=None):
    """写出音频；返回出错时的备注（None 表示成功）。备注按 lang/全局语言出中英。

    mp3_bitrate：仅对 .mp3 生效，单位 kbps（None = DEFAULT_MP3_BITRATE）。
    """
    import soundfile as sf

    L = lang or _LANG
    data = y.T if y.ndim == 2 else y  # soundfile 要求 (帧, 声道)
    suffix = path.suffix.lower()
    # OGG/Vorbis 的已知缺陷：某些 libsndfile 版本（实测 1.2.2 / soundfile 0.14.0）
    # 在写入超过约 50 万采样点时会让进程**原生崩溃**（0xC0000409），不是 Python
    # 异常，下面的 except 拦不住，批处理会连同已完成的报告一起丢失。因此对
    # Vorbis 超出阈值的情况直接改存 wav，绝不把进程交给 libsndfile。
    if suffix in (".ogg", ".oga") and len(data) > _OGG_SAFE_FRAMES:
        alt = path.with_suffix(".wav")
        sf.write(str(alt), np.ascontiguousarray(data), sr, subtype="PCM_16")
        return tr(f"{path.name} 过长，OGG 编码不可靠，已改存 {alt.name}",
                  f"{path.name} is too long for reliable OGG encoding; "
                  f"saved as {alt.name} instead", L)
    try:
        if suffix in (".wav", ".flac"):
            sf.write(str(path), np.ascontiguousarray(data), sr, subtype="PCM_16")
        elif suffix == ".mp3":
            br = int(mp3_bitrate or DEFAULT_MP3_BITRATE)
            try:
                sf.write(str(path), np.ascontiguousarray(data), sr,
                         subtype="MPEG_LAYER_III", bitrate_mode="CONSTANT",
                         compression_level=_MP3_COMPRESSION.get(br, 0.0))
            except Exception:
                # 旧版 libsndfile 不认这两个参数 → 退回库默认写法，总比写不出好
                sf.write(str(path), np.ascontiguousarray(data), sr)
        else:
            sf.write(str(path), np.ascontiguousarray(data), sr)
        return None
    except Exception as e:  # 例如旧版 libsndfile 写不了 mp3
        alt = path.with_suffix(".wav")
        sf.write(str(alt), np.ascontiguousarray(data), sr, subtype="PCM_16")
        em = localize_error(str(e), L)
        return tr(f"写出 {path.name} 失败（{em}），已改存 {alt.name}",
                  f"could not write {path.name} ({em}); saved as {alt.name} instead", L)


# ---------------------------------------------------------------- 测速

def _grid_score(onset, fps: int, P: float, phase: float, t_start: float, t_end: float,
                w: int = 2) -> float:
    """节拍网格与 onset 包络的对齐能量：网格点上 onset 的平均值（±w 帧取最大）。"""
    n_frames = len(onset)
    k0 = int(np.ceil((t_start - phase) / P - 1e-9))
    k1 = int(np.floor((t_end - phase) / P + 1e-9))
    if k1 <= k0:
        return -1.0
    f = np.round((phase + np.arange(k0, k1 + 1) * P) * fps).astype(int)
    f = f[(f >= 0) & (f < n_frames)]
    if len(f) < 4:
        return -1.0
    vals = np.array([onset[max(0, x - w): x + w + 1].max() for x in f])
    return float(np.mean(vals))


def _comb_refine(onset, sr: int, beat_times):
    """梳状网格拟合：在检测节拍 ±4% 范围内搜索拍周期与相位，
    最大化节拍网格与 onset 包络的对齐能量，把速度精修到 ~0.1 BPM 级。

    **层级（拍周期在哪一档）沿用 librosa beat_track 的判定**——它带速度先验，
    比纯 onset 能量最大化可靠：纯能量最大化时更粗的层级（2 倍拍、3 倍拍）总是
    得分更高（粗网格天然只挑最响的点），会把 164 BPM 的歌判成 82 BPM。
    这里只做两件事：① 在 ±4% 内精修周期；② **在整个周期内**搜索相位
    （旧版只围绕首个检测拍搜 ±P/8，相位差半个拍时会整体偏掉）。

    返回 (period_sec, phase_sec)；不可拟合时返回 (None, None)。
    """
    hop = 256
    fps = sr / hop
    n_frames = len(onset)
    if len(beat_times) < 4 or n_frames < 8:
        return None, None
    P0 = float(np.median(np.diff(beat_times)))
    if not (0.25 <= P0 <= 2.5):
        return None, None
    duration = n_frames / fps
    # 分析区间：避开首尾 5%（前奏/尾奏常无稳定节拍）
    t_a = min(0.05 * duration, 5.0)
    t_b = max(duration - min(0.05 * duration, 5.0), t_a + 4 * P0)
    if t_b <= t_a + 4 * P0:
        t_a, t_b = 0.0, duration
    t0 = float(beat_times[0])

    best = [P0, t0, _grid_score(onset, fps, P0, t0, t_a, t_b)]
    # 粗搜：周期 ±4%，相位覆盖整个周期（含旧的 ±P/8 邻域）
    for P in np.linspace(P0 * 0.96, P0 * 1.04, 41):
        for ph in np.linspace(t0 - P, t0 + P, 25, endpoint=False):
            sc = _grid_score(onset, fps, P, ph, t_a, t_b)
            if sc > best[2]:
                best = [P, ph, sc]
    # 细搜：周期 ±0.15%，相位 ±3%
    P1, ph1, _ = best
    for P in np.linspace(P1 * 0.9985, P1 * 1.0015, 31):
        for ph in np.linspace(ph1 - 0.03 * P1, ph1 + 0.03 * P1, 9):
            sc = _grid_score(onset, fps, P, ph, t_a, t_b)
            if sc > best[2]:
                best = [P, ph, sc]
    return float(best[0]), float(best[1])


def _smooth_beat_grid(beat_times, P: float, phase: float, duration: float):
    """把实测节拍拟合到一条**平滑**的节拍时间轴上，返回规整的节拍序列。

    为什么要平滑：librosa 的逐拍检测有几十毫秒级抖动（真实歌曲上实测残差
    std 可达 130ms）。直接拿实测拍去做节拍器，点击就会忽快忽慢（听感上是
    "打不准"）。这里改为：

      1. 用精修周期 P/相位 phase 给每拍编号 k = round((t-phase)/P)；
      2. 对 (k → t) 做**稳健的低阶多项式拟合**（迭代剔除离群拍），
         阶数按"是否真有系统性变速"自适应：速度稳定的用 1 阶（= 规整网格），
         确实在渐快/渐慢的用 2~3 阶（跟随变速但不跟随抖动）；
      3. 在整数 k 上取值，得到既跟随音乐、又没有逐拍抖动的节拍序列。

    拟合失败（拍太少/离群太多）时返回 None，由调用方退回规整网格。
    """
    bt = np.asarray(beat_times, dtype=np.float64)
    if len(bt) < 8:
        return None
    k = np.round((bt - phase) / P)

    keep = np.ones(len(bt), dtype=bool)
    fit = None
    for deg in (1, 2, 3):
        # 先用当前 deg 做一次稳健拟合（迭代重加权：按绝对残差剔除离群拍）
        kk, tt = k[keep], bt[keep]
        if len(kk) < max(6, deg + 3):
            break
        coef = np.polyfit(kk, tt, deg)
        for _ in range(3):
            resid = bt - np.polyval(coef, k)
            inl = np.abs(resid - np.median(resid[keep])) <= max(0.25 * P,
                                                               5.0 * np.median(np.abs(resid[keep] - np.median(resid[keep]))))
            if inl.sum() < max(6, deg + 3):
                break
            keep = inl
            coef = np.polyfit(k[keep], bt[keep], deg)
        resid_in = (bt - np.polyval(coef, k))[keep]
        # 系统性残差（中位绝对残差）是否已经够小？够小就不必升阶
        sys_err = float(np.median(np.abs(resid_in - np.median(resid_in))))
        if sys_err <= 0.12 * P:
            fit = coef
            break
        fit = coef  # 记录当前最好，继续尝试更高阶
    if fit is None:
        return None

    k0 = int(np.ceil((bt[0] - phase) / P - 1e-9))
    k_end = int(np.floor((duration - phase) / P + 1e-9))
    if k_end <= k0:
        return None
    kg = np.arange(k0, k_end + 1, dtype=np.float64)
    grid = np.polyval(fit, kg)
    # 网格必须严格单调、且与精修周期偏差不能太大（防止多项式跑飞）
    if np.any(np.diff(grid) <= 0.5 * P) or np.any(np.abs(np.diff(grid) - P) > 0.25 * P):
        return None
    return grid


def _regular_grid(beat_times, P: float, phase: float, duration: float):
    """稳定速度的歌返回贴合精修网格的规整节拍序列；速度多变的保留实测节拍。"""
    bt = np.asarray(beat_times, dtype=np.float64)
    k = np.round((bt - phase) / P)
    resid = np.abs(bt - (phase + k * P))
    if float(np.percentile(resid, 90)) > 0.06:  # 90% 分位残差 >60ms → 速度多变
        return bt
    k0 = int(np.ceil((bt[0] - phase) / P - 1e-9))
    k_end = int(np.floor((duration - phase) / P + 1e-9))
    if k_end <= k0:
        return bt
    return phase + np.arange(k0, k_end + 1) * P


class SegmentPlan(NamedTuple):
    """分段变速里一段的完整方案（时间均为**输入音频**秒轴；拼接后再映射到输出轴）。"""
    t0: float
    t1: float
    bpm: float
    beats: np.ndarray        # 段内拍时刻（输入轴，段起点为 0）
    plan: "StretchPlan"


def _frame_logtempo(y_mono, sr: int, hop: int = 512):
    """逐帧 BPM 曲线（log2 域，3 帧中值平滑）。

    librosa.feature.tempo 不开 aggregate，每帧自己估一次；倍频跳变在 log 域是
    台阶，适合做分段常数拟合。中位数 ±2.2 倍裁剪把个别乱跳的帧压回合理带内，
    裁剪后取 log 前先 np.maximum 兜底，避免除零。
    """
    import librosa
    onset = librosa.onset.onset_strength(y=y_mono, sr=sr, hop_length=hop)
    tp = np.asarray(librosa.feature.tempo(onset_envelope=onset, sr=sr,
                                          aggregate=None)).ravel().astype(np.float64)
    med = float(np.median(tp[tp > 0])) if np.any(tp > 0) else 120.0
    tp = np.clip(tp, med / 2.2, med * 2.2)
    logt = np.log2(np.maximum(tp, 1e-6))
    sm = np.array([np.median(logt[max(0, i - 1):i + 2]) for i in range(len(logt))])
    return sm, np.arange(len(tp)) * hop / sr


def _seg_mean_sse(cs, ss, a, b):
    """x[a:b] 的平方残差和（去均值），前缀和 O(1)。

    5 分钟曲有 ~2.6 万帧，分段搜索要评估几百万个子段；直接切片求均值是
    O(长度)，整体会挂到分钟级。SSE(a,b) = S[b]-S[a] - (C[b]-C[a])²/(b-a)。
    """
    n = b - a
    if n <= 0:
        return 0.0
    return float((ss[b] - ss[a]) - (cs[b] - cs[a]) ** 2 / n)


def _binary_segment(x, cs, ss, beta, min_len=8):
    """递归二分的最小二乘分段常数（PELT 式的贪心版）。

    只在「拆分带来的 SSE 下降 > beta」时才拆；beta = 2·s²·ln(n) 是贝叶斯惩罚，
    s² 用相邻差分中位数估计（对倍频跳变的巨大差分稳健）。
    """
    n = len(x)
    found, work = [], [(0, n)]
    while work:
        a, b = work.pop()
        if b - a < 2 * min_len:
            continue
        base = _seg_mean_sse(cs, ss, a, b)
        best, bestj = base, None
        for j in range(a + min_len, b - min_len):
            c = _seg_mean_sse(cs, ss, a, j) + _seg_mean_sse(cs, ss, j, b)
            if c < best:
                best, bestj = c, j
        if bestj is not None and (base - best) > beta:
            found.append(bestj)
            work.append((a, bestj))
            work.append((bestj, b))
    return sorted(found)


def detect_tempo_segments(y_mono, sr: int, dedup_s=2.0, merge_ratio=0.10,
                          min_seg_s=4.0, min_analyze_s=12.0, lang=None):
    """变拍检测：返回 [(t0, t1), ...]（输入秒轴，覆盖全曲）；恒速歌返回单段。

    做法：逐帧 log-BPM 曲线 → 递归二分找变点 → 相邻过近( <dedup_s )的变点合并
    → 短段(<min_seg_s)并入邻段、速度差 <merge_ratio 的邻段并成一段。
    边界实测误差 <0.7 s（合成曲验收）。
    每段至少 min_analyze_s 秒，否则 detect_tempo 拍点太少不可靠。
    """
    total = len(y_mono) / sr
    if total < min_analyze_s * 1.5:
        return [(0.0, total)]
    logt, tf = _frame_logtempo(y_mono, sr)
    n = len(logt)
    cs = np.concatenate([[0.0], np.cumsum(logt)])
    ss = np.concatenate([[0.0], np.cumsum(logt * logt)])
    d = np.diff(logt)
    s2 = 0.5 * float(np.median(d * d)) if len(d) else 1.0
    idxs = _binary_segment(logt, cs, ss, 2.0 * s2 * math.log(max(n, 3)))
    # 时间轴上的变点 → 帧下标
    dedup = []
    for b in idxs:
        t = float(tf[min(b, n - 1)])
        if dedup and abs(t - dedup[-1]) < dedup_s:
            continue
        dedup.append(t)
    edges = [0] + [int(round(t * sr / 512)) for t in dedup] + [n]
    edges = sorted(set(max(0, min(n, e)) for e in edges))
    thr = math.log2(1.0 + merge_ratio)

    def seg_dur(a, b):
        return float(tf[min(b, n - 1)] - tf[a])

    changed = True
    while changed and len(edges) > 2:
        changed = False
        for i in range(len(edges) - 1):
            a, b = edges[i], edges[i + 1]
            nxt = edges[i + 2] if i + 2 < len(edges) else None
            if seg_dur(a, b) < min_seg_s:
                edges.pop(i + 1 if i + 1 < len(edges) - 1 else 1)
                changed = True
                break
            if nxt is not None and abs(float(np.median(logt[a:b]))
                                       - float(np.median(logt[b:nxt]))) < thr:
                edges.pop(i + 1)
                changed = True
                break
    times = [float(tf[e]) if e < n else total for e in edges]
    times[0], times[-1] = 0.0, float(total)
    out = []
    for a, b in zip(times[:-1], times[1:]):
        if b - a >= 1.0:                 # 丢掉退化小尾巴
            out.append((a, b))
    if out:
        out[0] = (0.0, out[0][1])
        out[-1] = (out[-1][0], float(total))
    else:
        out = [(0.0, float(total))]
    return out


def _segment_plans(y_mono, sr: int, target: float, strict: bool = False,
                   max_slowdown: float = DEFAULT_MAX_SLOWDOWN,
                   max_speedup: float = DEFAULT_MAX_SPEEDUP, lang=None):
    """分段测速 + 每段各按原「最小变速」规则出方案。

    返回 (plans, infeasible)。

    - plans：[(t0, t1, SegmentPlan)]；段数 == 1（infeasible 为 None）表示「没发现
      变拍」，调用方应退回整曲单方案路径（保持默认行为字节级不变）。
    - infeasible：None，或 (t0, t1, bpm, reason) —— 表示检测到多段、但**其中某段**
      按上下限算不出可行方案。此时调用方必须把**整首歌**跳过（不生成文件、
      也不退回整曲处理）：只要有一段超出加减速上限，整首就不变（用户要求，
      2026-10 需求 m02195）。不做「只变可行段、跳过越限段」这种事——那会让
      一首歌里有的段变了、有的段没变，交接点两侧速度对不上。

    某段测不到速度（太安静/太短）时 detect_tempo 仍抛 RuntimeError，由调用方
    退回整曲处理（那是「读不出」而非「越限」，与默认行为一致地保守回退）。
    """
    segs = detect_tempo_segments(y_mono, sr, lang=lang)
    if len(segs) < 2:
        return [(segs[0][0], segs[0][1], None)], None
    out = []
    for (t0, t1) in segs:
        a, b = int(t0 * sr), int(t1 * sr)
        bpm, beats = detect_tempo(y_mono[a:b], sr, lang)
        plan = choose_stretched_tempo(bpm, target, strict, max_slowdown, max_speedup)
        if plan is None:
            reason = stretch_limits_text(bpm, target, max_slowdown, max_speedup, lang=lang)
            return [(t0, t1, None)], (t0, t1, float(bpm), reason)
        out.append((t0, t1, SegmentPlan(t0, t1, float(bpm),
                                        np.asarray(beats, dtype=np.float64), plan)))
    # 相邻段方案完全相同（同倍频档同 rate）→ 合并，少一次无谓拼接
    merged = []
    for item in out:
        prev = merged[-1] if merged else None
        if prev is not None and abs(prev[2].plan.rate - item[2].plan.rate) < 1e-9 \
                and abs(float(prev[2].plan.clicks_per_beat)
                        - float(item[2].plan.clicks_per_beat)) < 1e-9:
            t0, t1, sp = prev
            beats = np.concatenate([sp.beats, item[2].beats + (item[0] - t0)])
            merged[-1] = (t0, item[1], SegmentPlan(t0, item[1], sp.bpm, beats, sp.plan))
        else:
            merged.append(item)
    return merged, None


def _xfade(a, b, ov):
    """5 ms 等功率（cos²/sin²）交叉淡化拼接。ov=0 时退化为硬接。"""
    if ov <= 0:
        return np.concatenate([a, b], axis=1)
    t = np.linspace(0.0, math.pi / 2, ov)
    ca, sa = (np.cos(t) ** 2)[np.newaxis, :], (np.sin(t) ** 2)[np.newaxis, :]
    return np.concatenate([a[:, :-ov], a[:, -ov:] * ca + b[:, :ov] * sa, b[:, ov:]], axis=1)


def _pick_join(prev, nxt, ov, win, sr):
    """为拼接点搜索一对下刀位置 (c, cc)，最小化「join 邻域最大一阶差分 ÷
    两段自身强瞬态典型最大值」。评分 ≤1 = join 处没有任何超过音乐自身鼓点
    瞬态的不连续 = 听不到爆音。

    只在前段尾找低能量点**不够**：若后段头上正好是个强音（例如段落的重音起拍），
    它仍会砸在淡化区里响。所以两侧下刀点都要参与搜索，谁响谁进淡化区。
    """
    L0 = int(0.12 * sr)
    # 一阶差分沿**时间轴**（axis=1）求：声道维的差分是左右声道的电平差，
    # 拿它当"瞬态"来评分会彻底错。
    ref = max(float(np.percentile(np.abs(np.diff(prev, axis=1)), 99.95)) or 1e-9,
              float(np.percentile(np.abs(np.diff(nxt, axis=1)), 99.95)) or 1e-9)
    t = np.linspace(0.0, math.pi / 2, ov)
    c2, s2v = np.cos(t) ** 2, np.sin(t) ** 2

    def click_score(c, cc):
        a = prev[:, max(0, c - L0):c - ov]
        mid = prev[:, c - ov:c] * c2 + nxt[:, cc:cc + ov] * s2v
        b = nxt[:, cc + ov:cc + L0]
        j = np.concatenate([a, mid, b], axis=1)
        return float(np.abs(np.diff(j, axis=1)).max()) / ref

    def search(cs, ccs):
        best, bc, bcc = 9e9, cs[0], ccs[0]
        for c in cs:
            for cc in ccs:
                v = click_score(c, cc)
                if v < best:
                    best, bc, bcc = v, c, cc
        return bc, bcc, best

    c_lo = max(ov + L0, prev.shape[1] - win)
    coarse = search(list(range(c_lo, prev.shape[1], 220)), list(range(0, win, 220)))
    bc, bcc = coarse[0], coarse[1]
    cs = [c for c in range(max(c_lo, bc - 260), min(prev.shape[1], bc + 261), 20)] or [bc]
    ccs = list(range(max(0, bcc - 260), min(win, bcc + 261), 20)) or [bcc]
    return search(cs, ccs)


def _stitch_segments(y, sr, plans, engine):
    """逐段拉伸并拼回一轨。返回 (输出音频 (2,n), origins, head_trim, join_scores)。

    origins[i] = 第 i 段第一个样本落在输出轴的位置（秒），节拍器按它平移回全局。
    段头被裁掉的样本数记在 head_trim 里；**内容不丢** —— 搜索下刀点时裁掉的
    只是段首几毫秒的静音/低能量处，不是整拍。
    """
    if y.ndim == 1:
        y = y[np.newaxis, :]
    ov, win = int(0.005 * sr), int(0.04 * sr)
    out, origins, head_trim, scores = None, [], [], []
    for (t0, t1, sp) in plans:
        a, b = int(t0 * sr), int(t1 * sr)
        st = time_stretch(y[:, a:b], sp.plan.rate, engine).astype(np.float64)
        if out is None:
            out, origins, head_trim = st, [0.0], [0]
        else:
            cut, cut_cur, score = _pick_join(out, st, ov, win, sr)
            scores.append(score)
            origins.append((cut - ov - cut_cur) / sr)
            head_trim.append(cut_cur)
            out = _xfade(out[:, :cut], st[:, cut_cur:], ov)
    return out, origins, head_trim, scores


def _no_tempo_msg(lang: str | None = None) -> str:
    """检测失败的提示：随界面语言出中/英（英文界面不应漏中文）。"""
    return tr("无法检测速度（音频太短或太安静？），请用 --bpm 手动指定",
              "Could not detect tempo (audio too short or too quiet?). "
              "Please specify it manually with --bpm.",
              lang)


def detect_tempo(y_mono, sr: int, lang: str | None = None):
    """检测速度，返回 (bpm, beat_times)。

    bpm 为精修后的原值（**不再归一化**：测到 60 BPM 就报 60 BPM），
    beat_times 为拉伸前的时间轴节拍位置（稳定速度歌曲为精修规整网格）。
    lang 只影响检测失败时抛出的 RuntimeError 文案。
    """
    import librosa

    onset = librosa.onset.onset_strength(y=y_mono, sr=sr)
    tempo, beats = librosa.beat.beat_track(onset_envelope=onset, sr=sr,
                                           units="time", trim=False)
    beat_times = np.asarray(beats, dtype=np.float64)
    tracker = float(np.atleast_1d(tempo)[0])
    duration = len(onset) * 512 / sr

    if len(beat_times) < 4:
        bpm = float(tracker) if tracker > 0 else 0.0
        if bpm <= 0:
            raise RuntimeError(_no_tempo_msg(lang))
        return bpm, beat_times

    # 精修：在检测结果 ±4% 内做梳状网格拟合（细帧分辨率）
    onset_fine = librosa.onset.onset_strength(y=y_mono, sr=sr, hop_length=256)
    P, phase = _comb_refine(onset_fine, sr, beat_times)
    if P is None:
        P = float(np.median(np.diff(beat_times)))
        phase = float(beat_times[0])
    bpm = 60.0 / P
    if bpm <= 0:
        raise RuntimeError(_no_tempo_msg(lang))
    # 节拍序列：优先用"稳健平滑网格"（跟随真实变速、滤掉逐拍抖动），
    # 其次退回规整网格/实测拍
    grid = _smooth_beat_grid(beat_times, P, phase, duration)
    if grid is None:
        grid = _regular_grid(beat_times, P, phase, duration)
    return bpm, grid


# ---------------------------------------------------------------- 变速

def time_stretch(y, rate: float, engine: str = "phasevocoder"):
    """把音频变速 rate 倍（>1 加速）。返回 (声道, 采样数)。"""
    import librosa

    if abs(rate - 1.0) < 1e-4:
        return y
    if y.ndim == 1:
        y = y[np.newaxis, :]
    outs = []
    sr_nominal = 10000.0  # 只影响重采样比例，与真实采样率无关
    for ch in y:
        if engine == "resample":
            # 变采样（磁带机式）：速度和音高一起变。
            # 等价于把信号重采样到 sr/rate 再按原采样率回放，soxr 高质量重采样。
            outs.append(librosa.resample(np.asarray(ch, dtype=np.float32),
                                         orig_sr=sr_nominal, target_sr=sr_nominal / rate,
                                         res_type="soxr_hq"))
        else:
            # 相位声码器：变速度不变音高
            # librosa 1.0 每次调用都会发弃用参数警告，这里静音以免刷屏
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                warnings.simplefilter("ignore", FutureWarning)
                outs.append(librosa.effects.time_stretch(np.asarray(ch, dtype=np.float32), rate=rate))
    n = min(o.shape[0] for o in outs)
    outs = [np.asarray(o[:n], dtype=np.float32) for o in outs]
    return np.stack(outs)


# ---------------------------------------------------------------- 节拍器合成

def _sine(freq: float, dur: float, sr: int, tau: float):
    n = int(dur * sr)
    t = np.arange(n) / sr
    return np.sin(2 * np.pi * freq * t) * np.exp(-t / tau)


def _square(freq: float, dur: float, sr: int, tau: float):
    n = int(dur * sr)
    t = np.arange(n) / sr
    return np.sign(np.sin(2 * np.pi * freq * t)) * np.exp(-t / tau)


def _noise(dur: float, sr: int, tau: float, rng):
    n = int(dur * sr)
    return rng.standard_normal(n) * np.exp(-np.arange(n) / sr / tau)


def _mix(*parts):
    """把不同长度的音片零填充后叠加成一条。"""
    n = max(len(p) for p in parts)
    out = np.zeros(n)
    for p in parts:
        out[: len(p)] += p
    return out


def synth_click(timbre: str, sr: int, rng, lang=None) -> np.ndarray:
    """合成单声部节拍器音，峰值归一到 1。"""
    if timbre == "beep":
        x = _sine(1000, 0.06, sr, 0.015)
    elif timbre == "woodblock":
        f0 = 900.0
        x = _mix(_sine(f0, 0.09, sr, 0.012),
                 0.5 * _sine(f0 * 2.7, 0.05, sr, 0.006),
                 0.3 * _noise(0.01, sr, 0.002, rng))
    elif timbre == "cowbell":
        f0 = 540.0
        x = 0.6 * _square(f0, 0.30, sr, 0.06) + 0.6 * _square(f0 * 1.48, 0.30, sr, 0.06)
        x = np.diff(x, prepend=0.0)  # 一阶差分高通，去掉低频嗡嗡声
    elif timbre == "click":
        x = _mix(0.7 * _sine(1000, 0.012, sr, 0.004),
                 0.6 * _noise(0.012, sr, 0.002, rng))
    elif timbre == "sidestick":
        x = _mix(_sine(1700, 0.07, sr, 0.01),
                 0.4 * _noise(0.02, sr, 0.003, rng))
    elif timbre == "clap":
        n = int(0.12 * sr)
        x = np.zeros(n)
        for off, amp in ((0.0, 0.8), (0.010, 1.0), (0.021, 0.9), (0.034, 0.7)):
            ns = _noise(0.09, sr, 0.018, rng) * amp
            s = int(off * sr)
            e = min(n, s + len(ns))
            x[s:e] += ns[: e - s]
        x = np.diff(x, prepend=0.0)
    else:
        raise ValueError(tr(f"未知节拍器音色: {timbre}",
                            f"unknown metronome timbre: {timbre}", lang))
    x = np.asarray(x, dtype=np.float64)
    peak = float(np.max(np.abs(x)))
    if peak > 0:
        x /= peak
    return x.astype(np.float32)


def _rms_db(x) -> float:
    """信号的 RMS 电平（dBFS）。静音返回 -120。"""
    arr = np.asarray(x, dtype=np.float64)
    rms = float(np.sqrt(np.mean(arr ** 2))) if arr.size else 0.0
    return 20.0 * math.log10(rms) if rms > 1e-9 else -120.0


def click_schedule(beat_times, rate: float, click_bpm: float, duration: float,
                   clicks_per_beat: float | None = None):
    """计算节拍器点击时刻列表（秒）。

    有可靠节拍时：点击**定义在拍序号空间**——把每个音乐拍拍序号记为整数，
    点击落在整数与分数拍位上，再用音乐自己的节拍时间轴做插值。落点取决于
    每拍点击数（clicks_per_beat）：
        整数 cpb（如 3）  → 拍位 0, 1/3, 2/3, 1, 4/3, …（每拍都有落点）
        1/n（如 1/3）     → 拍位 0, 3, 6, 9, …（全落在整数拍位上）
        整数 cpb=2        → 拍位 0, 1/2, 1, 3/2, …

    clicks_per_beat 直接取自变速方案（target / a2）。**必须用方案值而不是
    从实测拍距反推**：实测拍距与方案假设的拍距差 0.04% 时，几百拍后点击就
    会漂出半个拍（实测 627 拍漂 83ms），听感是"越敲越不对"。用方案值则
    所有整数拍位上的点击精确压在音乐拍上，分数拍位按音乐拍等比分布，
    只在"音乐速度本身就是那个分数关系"时才成立，不会累积漂移。

    节拍太少不可靠时：按点击速率从头均匀敲。
    """
    period = 60.0 / click_bpm
    if beat_times is not None and len(beat_times) >= 4:
        bt = np.asarray(beat_times, dtype=np.float64) / rate
        ibi = np.diff(bt)
        P = float(np.median(ibi))
        if P > 0:
            # 补漏拍：明显大于中位拍距的间隔按比例拆分；
            # 明显小于中位拍距的（检测抖动重复拍）合并，避免网格被噪声带偏
            beats = [float(bt[0])]
            for i, iv in enumerate(ibi):
                if iv < 0.5 * P:
                    continue  # 重复拍：跳过该拍，网格相位不变
                m = max(1, int(round(iv / P)))
                for q in range(1, m + 1):
                    beats.append(float(bt[i] + iv * q / m))
            beats = np.asarray(beats)
            if len(beats) >= 2:
                idx = np.arange(len(beats), dtype=np.float64)
                cpb = float(clicks_per_beat) if clicks_per_beat else 0.0
                if cpb <= 0:
                    # 未给方案值时，退化为按平均拍距反推
                    span0 = float(beats[-1] - beats[0])
                    if span0 <= 0:
                        cpb = 0.0
                    else:
                        cpb = click_bpm * span0 / (60.0 * (len(beats) - 1))
                if cpb > 0:
                    n_click = int(math.floor((len(beats) - 1) * cpb)) + 1
                    k = np.arange(n_click, dtype=np.float64)
                    frac = k / cpb
                    # 把"应该是整数"的拍位吸附到整数，保证精确落拍
                    near = np.abs(frac - np.round(frac)) < 1e-6
                    frac[near] = np.round(frac[near])
                    times = np.interp(frac, idx, beats)
                    return [float(t) for t in times if 0.0 <= t < duration - 1e-3]
    times = []
    t = 0.0
    while t < duration - 1e-3:
        times.append(t)
        t += period
    return times


def build_click_track(n_samples: int, sr: int, click_times, timbre: str, rng,
                      music_rms_db: float = -18.0, rel_db: float = -6.0, lang=None) -> np.ndarray:
    """在 click_times（秒）位置铺设节拍器音轨（所有敲击等响、无重音）。

    响度自动匹配音乐：节拍器增益 = 音乐RMS(dB) + rel_db - 音色自身RMS(dB)，
    即 rel_db=0 时节拍器与音乐等响；峰值上限 1.0。
    """
    out = np.zeros(n_samples, dtype=np.float32)
    # 预合成一声 click，测量音色自身响度，从而换算出相对音乐的增益；
    # 同一条样本复用到所有点击，保证严格等响
    sample = synth_click(timbre, sr, np.random.default_rng(1), lang)
    amp0 = 10.0 ** ((music_rms_db + rel_db - _rms_db(sample)) / 20.0)
    amp0 = min(max(amp0, 0.0), 1.0)
    for t in click_times:
        i = int(round(t * sr))
        j = min(n_samples, i + len(sample))
        if j > i:
            out[i:j] += sample[: j - i] * amp0
    return out


# ---------------------------------------------------------------- 单文件处理

@dataclass
class ConvertOptions:
    target_bpm: float = 180.0
    bpm: float | None = None            # 手动指定原速（跳过检测）
    detect_only: bool = False           # 只测速
    strict: bool = False                # 精确拉伸到目标 BPM
    engine: str = "phasevocoder"        # phasevocoder | resample
    metronome: str | None = None        # None=不添加，或 TIMBRES 之一
    click_rel_db: float = -6.0            # 节拍器相对音乐响度的 dB（0=等响，负值更轻）
    music_loudness_db: float | None = None  # 手动指定音乐响度（默认自动测量，测试用）
    output_format: str = "source"       # source=与原文件同格式（默认）；或 wav/mp3/flac/ogg
    mp3_bitrate: object = "auto"        # mp3 输出码率 kbps；"auto"=跟随原文件
    out_dir: Path | None = None         # None = 输入文件所在目录/tempo_converted
    mirror_dirs: bool = True            # 指定输出目录时，按输入目录层级镜像子文件夹
    max_slowdown: float = DEFAULT_MAX_SLOWDOWN   # 最多放慢多少（0.25 = 25%），用户可调
    max_speedup: float = DEFAULT_MAX_SPEEDUP     # 最多加快多少（0.50 = 50%），用户可调
    lang: str | None = None             # 输出语言（None=跟随全局 set_lang；"zh"/"en"）
    segments: bool = False              # 分段变速：先找变拍点，每段各按最小变速规则处理


def _unique_output_path(out_dir: Path, stem: str, target_bpm: float, ext: str) -> Path:
    """生成 形如 歌名_180bpm.wav 的唯一输出路径。ext 形如 '.wav'。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = f"{target_bpm:g}bpm"
    p = out_dir / f"{stem}_{tag}{ext}"
    i = 1
    while p.exists():
        p = out_dir / f"{stem}_{tag}_{i}{ext}"
        i += 1
    return p


def _resolve_out_dir(opt: ConvertOptions, path: Path, rel_base: Path | None) -> Path:
    """算出这个文件的输出目录。

    - 没指定输出目录：各文件所在目录下的 tempo_converted/（保持原行为）。
    - 指定了输出目录且 mirror_dirs：在该目录下**按输入层级镜像子文件夹**——
      用户添加的根文件夹 D:\\Music 下有 Pop/Jazz，则输出到 out/Pop/、out/Jazz/。
      rel_base 是「用户添加的那个根」（文件夹本身，或直接添加文件时的父目录）。
    """
    if opt.out_dir is None:
        return path.parent / "tempo_converted"
    base = Path(opt.out_dir)
    if not opt.mirror_dirs:
        return base
    root = Path(rel_base) if rel_base is not None else path.parent
    try:
        rel = path.parent.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        rel = Path()                       # 不在根之下（或跨盘）→ 直接放输出目录根部
    if str(rel) in (".", ""):
        return base
    # 逐段过滤：只保留正常目录名，杜绝 ".." 之类逃出输出目录
    safe = [seg for seg in rel.parts if seg not in (".", "..") and not Path(seg).is_absolute()]
    return base.joinpath(*safe) if safe else base


def _process_segmented(path, opt, y, sr, y_mono, seg_plans, row, notes,
                       log_fn, rel_base):
    """分段变速的落地路径：逐段拉伸 → 无爆音拼接 → 每段沿自己的拍网格敲节拍器。

    只在 --segments（且检测到多段）时进入；报告仍是一行，逐段明细写进「备注」，
    CSV 列结构不变。拼接评分 >1 时也在备注里提醒（说明该处接缝可能听得出来）。
    """
    L = opt.lang or _LANG
    y2, origins, head_trim, scores = _stitch_segments(y, sr, seg_plans, opt.engine)

    def join(vals, fmt="{:.1f}"):
        return " / ".join(fmt.format(v) for v in vals)

    bpms = [sp.bpm for (_t0, _t1, sp) in seg_plans]
    a2s = [sp.plan.a2 for (_t0, _t1, sp) in seg_plans]
    rates = [sp.plan.rate for (_t0, _t1, sp) in seg_plans]
    cpbs = [float(sp.plan.clicks_per_beat) for (_t0, _t1, sp) in seg_plans]
    row["检测BPM"] = join(bpms)
    row["检测方式"] = tr("自动检测·分段", "Auto · segmented", L)
    row["拉伸后BPM"] = join(a2s)
    row["拉伸比"] = join(rates, "{:.4f}")
    row["速度变化"] = " / ".join(f"{(r - 1.0) * 100:+.1f}%" for r in rates)
    row["节拍关系"] = " | ".join(relation_text(c, lang=L) for c in cpbs)
    row["节拍器BPM"] = f"{seg_plans[0][2].plan.click_bpm:g}"
    row["每拍点击数"] = join(cpbs, "{:g}")

    # 内部结构化键（不进 CSV：write_report 只写 REPORT_FIELDS 的 15 个键）——
    # 供桌面/pywebview 桥接透出给前端逐段渲染「120｜154｜130」样式。
    row["_分段"] = [
        {"t0": round(float(t0), 3), "t1": round(float(t1), 3),
         "detected": f"{sp.bpm:.1f}", "a2": f"{sp.plan.a2:g}",
         "rate": f"{sp.plan.rate:.4f}",
         "relation": relation_text(sp.plan.clicks_per_beat, lang=L)}
        for (t0, t1, sp) in seg_plans
    ]

    music_rms_db = (opt.music_loudness_db if opt.music_loudness_db is not None
                    else _rms_db(y2))
    row["音乐响度dB"] = f"{music_rms_db:.1f}"

    times = []
    for i, (t0, t1, sp) in enumerate(seg_plans):
        dur_out = float((t1 - t0) / sp.plan.rate) + 0.5
        ct = click_schedule(sp.beats, sp.plan.rate, sp.plan.click_bpm, dur_out,
                           clicks_per_beat=sp.plan.clicks_per_beat)
        times.extend(origins[i] + float(c) for c in ct)
        if sp.beats is None or len(sp.beats) < 4:
            notes.append(tr(f"第{i + 1}段未取到可靠节拍位置，该段节拍器按速率均匀敲",
                            f"segment {i + 1} has no reliable beat positions; "
                            "its metronome clicks are spaced evenly", L))
    times = sorted(t for t in times if 0.0 <= t < y2.shape[1] / sr)
    if opt.metronome:
        n = y2.shape[1]
        rng = np.random.default_rng(20260606)
        click = build_click_track(n, sr, times, opt.metronome, rng,
                                  music_rms_db=music_rms_db, rel_db=opt.click_rel_db, lang=L)
        y2 = np.array(y2, copy=True)
        y2[:, :n] += click[np.newaxis, :n]
        row["节拍器"] = timbre_name(opt.metronome, L)

    peak = float(np.max(np.abs(y2)))
    if peak > 0.999:
        y2 *= 0.999 / peak
        notes.append(tr("已做峰值限制（防止叠加节拍器后削波）",
                        "peak-limited (to avoid clipping after adding the metronome)", L))

    detail = tr(
        f"分段 {len(seg_plans)} 段：", f"{len(seg_plans)} segments: ", L) + " | ".join(
        f"{t0:g}-{t1:g}s {sp.bpm:.1f}→{sp.plan.a2:g}BPM {(sp.plan.rate - 1) * 100:+.1f}%"
        for i, (t0, t1, sp) in enumerate(seg_plans))
    notes.append(detail)
    if scores:
        worst = max(scores)
        if worst > 1.0:
            notes.append(tr(f"拼接点最大接缝评分 {worst:.2f}（>1 说明接缝可能听得出来，建议听检）",
                            f"worst join score {worst:.2f} (>1 = the seam may be audible; "
                            "please listen-check)", L))

    out_dir = _resolve_out_dir(opt, path, rel_base)
    fmt = str(opt.output_format or "wav").lower()
    if fmt in ("source", "same", "auto"):
        fmt = path.suffix.lstrip(".").lower() or "wav"
    if fmt not in ("wav", "mp3", "flac", "ogg", "aiff", "aif"):
        fmt = "wav"
    out_path = _unique_output_path(Path(out_dir), path.stem, opt.target_bpm, f".{fmt}")
    br = resolve_mp3_bitrate(opt.mp3_bitrate, path) if fmt == "mp3" else None
    err = write_audio(out_path, y2, sr, lang=L, mp3_bitrate=br)
    if err:
        notes.append(err)
        out_path = out_path.with_name(out_path.stem + ".wav")

    dur = y2.shape[1] / sr
    row["输出文件"] = str(out_path)
    row["输出时长秒"] = f"{dur:.2f}"
    log_fn("  " + tr("分段变速 : ", "Segmented  : ", L)
           + tr(f"共 {len(seg_plans)} 段，每段各按最小变速",
                f"{len(seg_plans)} segments, each stretched by the minimum rule", L))
    for (t0, t1, sp) in seg_plans:
        log_fn("    " + tr(f"[{t0:.1f}–{t1:.1f}s] 测速 {sp.bpm:.1f} → ",
                           f"[{t0:.1f}-{t1:.1f}s] {sp.bpm:.1f} → ", L)
               + f"{sp.plan.a2:g} BPM {(sp.plan.rate - 1.0) * 100:+.1f}%"
               + tr("，节拍器 ", ", metronome ", L) + f"{sp.plan.click_bpm:g} BPM "
               + tr("——", " — ", L) + relation_text(sp.plan.clicks_per_beat, lang=L))
    if opt.metronome and times:
        log_fn("  " + tr("节拍器   : ", "Metronome  : ", L)
               + f"{len(times)} " + tr("下，音量", "clicks, volume", L)
               + f" {opt.click_rel_db:+g} dB")
    log_fn("  " + tr("输出     : ", "Output     : ", L) + str(out_path)
           + tr("（", " (", L) + f"{dur:.2f}" + tr(" 秒）", " s)", L))
    if notes:
        row["备注"] = tr("；", "; ", L).join(notes)
    return row


def process_file(path: Path, opt: ConvertOptions, log_fn=log,
                 rel_base: Path | None = None) -> dict:
    """处理单个文件，返回一行报告（dict）。任何失败都记在“备注”里而不中断批量。

    rel_base：这个文件所属的「用户添加的根」（文件夹本身，或单文件的父目录），
    用于在指定输出目录时镜像原目录层级；不传则退化为文件自身所在目录。
    """
    L = opt.lang or _LANG
    path = Path(path)                     # 容错：调用方传 str 时不至于在 path.parent 上炸
    row = {"输入文件": str(path), "检测BPM": "", "检测方式": "", "目标BPM": opt.target_bpm,
           "拉伸后BPM": "", "拉伸比": "", "速度变化": "", "节拍关系": "",
           "节拍器BPM": "", "每拍点击数": "", "音乐响度dB": "",
           "节拍器": tr("无", "None", L),
           "输出文件": "", "输出时长秒": "", "备注": ""}
    notes = []
    try:
        y, sr = load_audio(path)
        y_mono = y.mean(axis=0) if y.shape[0] > 1 else y[0]

        # 1) 测速
        if opt.bpm and opt.bpm > 0:
            detected = float(opt.bpm)
            beat_times = None
            how = tr("手动指定", "Manual", L)
        else:
            detected, beat_times = detect_tempo(y_mono, sr, L)
            how = tr("自动检测", "Auto", L)
        row["检测BPM"] = f"{detected:.2f}"
        row["检测方式"] = how

        if opt.detect_only:
            log_fn(f"  {path.name}: {detected:.1f} BPM")
            row["备注"] = tr("仅检测速度", "Tempo only", L)
            return row

        # 1.5) 分段变速（opt-in）：找变拍点，每段各按「最小变速」规则出方案。
        #      手动指定原速时无从分段；只发现一段时退回整曲路径。
        #      关键语义（需求 m02195）：检测到多段但**任一段**超出加减速上限时，
        #      整首跳过、不生成文件、也不退回整曲处理——避免出现「一首歌里有的段
        #      变了、有的段没变」导致交接点两侧速度对不上。
        seg_plans = None
        seg_infeasible = None
        if opt.segments and not (opt.bpm and opt.bpm > 0):
            try:
                cand, seg_infeasible = _segment_plans(
                    y_mono, sr, opt.target_bpm, opt.strict,
                    opt.max_slowdown, opt.max_speedup, lang=L)
                if seg_infeasible is None:
                    if len(cand) > 1:
                        seg_plans = cand
                    else:
                        notes.append(tr("未发现变拍，按整曲处理",
                                        "no tempo change detected; processed as one piece", L))
            except RuntimeError as re:
                notes.append(tr("分段变速不可用（",
                                "segmented mode unavailable (", L) + str(re)
                             + tr("），已改按整曲处理", "); processed as one piece", L))
        if seg_plans:
            return _process_segmented(path, opt, y, sr, y_mono, seg_plans,
                                      row, notes, log_fn, rel_base)
        if seg_infeasible is not None:
            _t0, _t1, _bpm, _why = seg_infeasible
            row["检测方式"] = tr("自动检测·分段", "Auto · segmented", L)
            row["备注"] = (tr("跳过：", "Skipped: ", L)
                           + tr("分段变速中有段落超出加减速上限",
                               "a section exceeds the stretch limits in segmented mode", L)
                           + tr("：", ": ", L) + _why
                           + tr("，整首不做变速", "; the whole track is left unchanged", L))
            log_fn("  " + row["备注"])
            return row

        # 2) 选最小变速方案（受用户设定的变速上限约束；都不满足 → 跳过该文件）
        plan = choose_stretched_tempo(detected, opt.target_bpm, opt.strict,
                                      opt.max_slowdown, opt.max_speedup)
        if plan is None:
            why = stretch_limits_text(detected, opt.target_bpm,
                                      opt.max_slowdown, opt.max_speedup, lang=L)
            row["备注"] = tr("跳过：", "Skipped: ", L) + why
            log_fn("  " + tr("跳过：", "Skipped: ", L) + why + tr(
                "（这首歌按当前上限无可用方案，未生成文件）",
                " — no feasible option within the current limits; no file produced", L))
            return row
        row["拉伸后BPM"] = f"{plan.a2:.1f}"
        row["拉伸比"] = f"{plan.rate:.4f}"
        row["速度变化"] = f"{(plan.rate - 1.0) * 100:+.1f}%"
        row["节拍关系"] = relation_text(plan.clicks_per_beat, plan.j, lang=L)
        row["节拍器BPM"] = f"{plan.click_bpm:g}"
        row["每拍点击数"] = f"{plan.clicks_per_beat:g}"

        # 3) 变速
        y2 = time_stretch(y, plan.rate, opt.engine)

        # 4) 音乐响度测量 + 节拍器
        music_rms_db = opt.music_loudness_db if opt.music_loudness_db is not None else _rms_db(y2)
        row["音乐响度dB"] = f"{music_rms_db:.1f}"
        if opt.metronome:
            n = y2.shape[1]
            times = click_schedule(beat_times, plan.rate, plan.click_bpm, n / sr,
                                   clicks_per_beat=plan.clicks_per_beat)
            if beat_times is None or len(beat_times) < 4:
                notes.append(tr("未取到可靠节拍位置，节拍器按速率均匀敲",
                                "no reliable beat positions; metronome clicks spaced evenly", L))
            rng = np.random.default_rng(20260606)
            click = build_click_track(n, sr, times, opt.metronome, rng,
                                      music_rms_db=music_rms_db, rel_db=opt.click_rel_db, lang=L)
            y2 = np.array(y2, copy=True)
            y2[:, :n] += click[np.newaxis, :n]
            row["节拍器"] = timbre_name(opt.metronome, L)

        # 5) 峰值限制 + 写出
        peak = float(np.max(np.abs(y2)))
        if peak > 0.999:
            y2 *= 0.999 / peak
            notes.append(tr("已做峰值限制（防止叠加节拍器后削波）",
                            "peak-limited (to avoid clipping after adding the metronome)", L))

        out_dir = _resolve_out_dir(opt, path, rel_base)
        # 输出格式：output_format == "source" 表示"与原文件同格式"（界面默认项）
        fmt = str(opt.output_format or "wav").lower()
        if fmt in ("source", "same", "auto"):
            fmt = path.suffix.lstrip(".").lower() or "wav"
        if fmt not in ("wav", "mp3", "flac", "ogg", "aiff", "aif"):
            fmt = "wav"                      # 不支持的格式（如 m4a/aac 写不出）退回 wav
        out_path = _unique_output_path(Path(out_dir), path.stem, opt.target_bpm, f".{fmt}")
        br = resolve_mp3_bitrate(opt.mp3_bitrate, path) if fmt == "mp3" else None
        err = write_audio(out_path, y2, sr, lang=L, mp3_bitrate=br)
        if err:
            notes.append(err)
            out_path = out_path.with_name(out_path.stem + ".wav")

        dur = y2.shape[1] / sr
        row["输出文件"] = str(out_path)
        row["输出时长秒"] = f"{dur:.2f}"
        log_fn("  " + tr("检测速度 : ", "Detected   : ", L)
               + f"{detected:.1f} BPM" + tr("（", " (", L) + how + tr("）", ")", L))
        log_fn("  " + tr("方案     : ", "Plan       : ", L)
               + tr("音乐拉伸到 ", "stretch music to ", L) + f"{plan.a2:g} BPM"
               + tr("（变速 ", " (", L) + f"{(plan.rate - 1.0) * 100:+.1f}%" + tr("），", "), ", L)
               + tr("节拍器 ", "metronome ", L) + f"{plan.click_bpm:g} BPM"
               + tr("——", " — ", L) + relation_text(plan.clicks_per_beat, plan.j, lang=L))
        if opt.metronome:
            # 音色名只在两种语言下与 key 不同时才追加括号，避免 "woodblock (woodblock)"
            _tn = timbre_name(opt.metronome, L)
            _tpart = "" if _tn == opt.metronome else tr("（", " (", L) + _tn + tr("）", ")", L)
            log_fn("  " + tr("节拍器   : ", "Metronome  : ", L) + opt.metronome + _tpart
                   + f" @ {plan.click_bpm:g} BPM"
                   + tr(f"（音乐每拍敲 {plan.clicks_per_beat:g} 下），",
                        f", {plan.clicks_per_beat:g} click(s) per music beat, ", L)
                   + tr("音量为音乐响度 ", "volume ", L) + f"{opt.click_rel_db:+g} dB")
        _brnote = tr(f"，mp3 {br} kbps", f", mp3 {br} kbps", L) if br else ""
        log_fn("  " + tr("输出     : ", "Output     : ", L) + str(out_path)
               + tr("（", " (", L) + f"{dur:.2f}" + tr(" 秒", " s", L)
               + _brnote + tr("）", ")", L))
    except Exception as e:
        em = localize_error(str(e), L)
        row["备注"] = tr("失败: ", "Failed: ", L) + em
        log_fn("  " + tr("失败: ", "Failed: ", L) + em)
        return row
    if notes:
        row["备注"] = tr("；", "; ", L).join(notes)
    return row


# ---------------------------------------------------------------- 批量

def collect_inputs_with_base(inputs, recursive: bool = False):
    """展开输入 → 逐个 yield (文件, 所属根目录)。

    「所属根目录」= 用户添加的那个根：添加文件夹时是**文件夹本身**（输出镜像它下面的层级），
    添加单个文件时是**文件所在目录**（此时相对层级为空，直接铺在输出目录下）。
    同一个文件被多次添加时以第一次为准。
    """
    seen = set()
    for s in inputs:
        p = Path(s)
        if p.is_dir():
            base = p
            it = p.rglob("*") if recursive else p.iterdir()
            found = [f for f in sorted(it) if f.is_file() and f.suffix.lower() in SUPPORTED_EXTS]
            if not found:
                log(f"警告：文件夹 {p} 下没有找到支持的音频文件")
        elif p.is_file():
            base = p.parent
            if p.suffix.lower() not in SUPPORTED_EXTS:
                log(f"警告：不支持的文件类型，已跳过 {p}")
                continue
            found = [p]
        else:
            log(f"警告：路径不存在，已跳过 {s}")
            continue
        for f in found:
            key = str(f.resolve()).lower()
            if key not in seen:
                seen.add(key)
                yield f, base


def collect_inputs(inputs, recursive: bool = False):
    """只要文件列表（保持旧接口）。需要镜像层级时用 collect_inputs_with_base。"""
    for f, _base in collect_inputs_with_base(inputs, recursive=recursive):
        yield f


def report_header(lang=None):
    """按语言返回报告表头（内部键恒为中文，这里只做展示层翻译）。"""
    L = lang or _LANG
    return [h if L == "zh" else e for (_k, h, e) in REPORT_FIELDS]


def write_report(rows, report_path: Path, lang=None) -> None:
    """写 CSV 报告。表头按语言本地化；每行仍由内部中文键取（不受语言影响）。"""
    L = lang or _LANG
    keys = [k for (k, _h, _e) in REPORT_FIELDS]
    headers = [h if L == "zh" else e for (_k, h, e) in REPORT_FIELDS]
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig 方便 Excel 打开
        w = csv.writer(f)
        w.writerow(headers)
        for row in rows:
            w.writerow([row.get(k, "") for k in keys])


# ---------------------------------------------------------------- CLI

def build_cli(lang=None) -> argparse.ArgumentParser:
    L = lang or _LANG
    if L == "en":
        description = ("BeatSync tempo converter: detect BPM, the metronome always ticks at the "
                       "target tempo while the music is only stretched by the minimum amount, "
                       "batch supported.")
        epilog = """Examples:
  %(prog)s song.mp3 -t 180                       auto-detect 85 -> stretch music to 90, metronome stays 180 (2 clicks per beat)
  %(prog)s song.mp3 -t 180 --metronome woodblock add a woodblock metronome @180BPM
  %(prog)s ./music -t 120 --recursive            convert a whole folder (incl. subfolders)
  %(prog)s song.flac -t 90 --bpm 88              specify the original 88 BPM manually, skip detection
  %(prog)s song.mp3 --detect-only                detect tempo only
  %(prog)s song.mp3 -t 180 --strict              stretch exactly to 180 (no fractional relation, may be a large change)

Metronome timbres: beep  woodblock  cowbell  click  sidestick  clap
"""
    else:
        description = "音乐速度转换器：检测速度，节拍器恒定以目标速度发声、音乐只做最小变速，支持批量。"
        epilog = """示例:
  %(prog)s song.mp3 -t 180                       自动测速 85 -> 音乐拉伸到 90，节拍器仍是 180（每拍 2 下）
  %(prog)s song.mp3 -t 180 --metronome woodblock 加木鱼节拍器 @180BPM
  %(prog)s ./music -t 120 --recursive            批量转换整个文件夹（含子目录）
  %(prog)s song.flac -t 90 --bpm 88              手动指定原速 88，跳过检测
  %(prog)s song.mp3 --detect-only                只测速
  %(prog)s song.mp3 -t 180 --strict              精确拉伸到 180（不做分数关系，变速可能很大）

节拍器音色: beep(电子哔声) woodblock(木鱼) cowbell(牛铃) click(短打) sidestick(边击) clap(拍手)
"""
    H = {}
    if L == "en":
        H = dict(
            inputs="audio files or folders (one or more)",
            target="target BPM (e.g. 180; optional with --detect-only)",
            bpm="specify the original BPM manually (skip auto-detection)",
            detect_only="detect tempo only, no conversion",
            strict="stretch exactly to the target BPM (no fractional relation, change may be large)",
            metronome="add a metronome and pick a timbre (none by default)",
            click_rel_db="metronome relative volume (dB): matched to the music's own loudness, 0 = equal, negative = quieter (default -6)",
            engine="stretch engine: phasevocoder=pitch preserved (default); resample=tape-style (pitch follows speed)",
            output_format="output format (default source = same as input; unsupported formats like m4a fall back to wav)",
            mp3_bitrate=("mp3 output bitrate in kbps: auto = match the input file's bitrate (default); "
                         "or one of 320/256/224/192/160/128/112/96"),
            out_dir="output directory (default tempo_converted next to each input; when set, subfolders mirror the input layout by default, see --no-mirror-dirs)",
            no_mirror_dirs="do not mirror the folder layout (all files flat in the output dir)",
            recursive="search subfolders recursively",
            report="CSV report path (default conversion_report.csv in the current dir)",
            max_slowdown="max slowdown percentage for the music before switching to a speedup option (0~100, default %g)",
            max_speedup="max speedup percentage; files beyond both limits are skipped (0~100, default %g)",
            lang="output language: zh / en (default zh; affects notes, logs and the report)",
            segments=("detect tempo changes inside each track and convert every section with its "
                      "own minimum-stretch plan (seams cross-faded); if any section is beyond the "
                      "limits the whole track is left unchanged; ignored with --bpm"),
        )
    else:
        H = dict(
            inputs="音频文件或文件夹（可给多个）",
            target="目标 BPM（如 180；--detect-only 时可省略）",
            bpm="手动指定原速 BPM（跳过自动检测）",
            detect_only="只检测速度，不转换",
            strict="精确拉伸到目标 BPM（不做分数关系，变速可能很大）",
            metronome="添加节拍器并选择音色（默认不添加）",
            click_rel_db="节拍器相对音量（dB）：以音乐自身响度为基准自动匹配，0=等响，负值更轻（默认 -6）",
            engine="变速引擎：phasevocoder=保音高（默认）；resample=磁带机式（音高随速度变化）",
            output_format="输出格式（默认 source=与原文件同格式；写不出的格式如 m4a 自动退回 wav）",
            mp3_bitrate=("mp3 输出码率 kbps：auto=跟随输入文件的码率（默认）；"
                         "或 320/256/224/192/160/128/112/96 之一"),
            out_dir=("输出目录（默认各输入文件所在目录下的 tempo_converted；"
                     "指定后默认按输入层级镜像子文件夹，见 --no-mirror-dirs）"),
            no_mirror_dirs="指定输出目录时不做层级镜像（所有文件平铺在输出目录下）",
            recursive="递归搜索子文件夹",
            report="CSV 报告路径（默认当前目录 conversion_report.csv）",
            max_slowdown="最多把音乐放慢多少百分比，超出就改用加速方案（0~100，默认 %g)",
            max_speedup="最多把音乐加快多少百分比，两个上限都不满足的文件将被跳过（0~100，默认 %g)",
            lang="输出语言：zh / en（默认 zh；影响备注、日志与报告）",
            segments=("检测曲内变拍，每段各按最小变速规则处理（拼接点做交叉淡化）；"
                      "任一段超出加减速上限时整首不变速；手动指定 --bpm 时不分段"),
        )
    ap = argparse.ArgumentParser(
        description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilog)
    ap.add_argument("inputs", nargs="+", help=H["inputs"])
    ap.add_argument("-t", "--target", type=float, default=None, help=H["target"])
    ap.add_argument("--bpm", type=float, default=None, help=H["bpm"])
    ap.add_argument("--detect-only", action="store_true", help=H["detect_only"])
    ap.add_argument("--strict", action="store_true", help=H["strict"])
    ap.add_argument("--metronome", choices=TIMBRES, default=None, help=H["metronome"])
    ap.add_argument("--click-rel-db", type=float, default=-6.0, help=H["click_rel_db"])
    ap.add_argument("--engine", choices=["phasevocoder", "resample"], default="phasevocoder",
                    help=H["engine"])
    ap.add_argument("--format", dest="output_format",
                    choices=["source", "wav", "mp3", "flac", "ogg"], default="source",
                    help=H["output_format"])
    ap.add_argument("--mp3-bitrate", dest="mp3_bitrate", default="auto",
                    choices=["auto"] + [str(b) for b in MP3_BITRATES],
                    help=H["mp3_bitrate"])
    ap.add_argument("-o", "--out-dir", default=None, help=H["out_dir"])
    ap.add_argument("--no-mirror-dirs", dest="mirror_dirs", action="store_false", default=True,
                    help=H["no_mirror_dirs"])
    ap.add_argument("--recursive", action="store_true", help=H["recursive"])
    ap.add_argument("--report", default=None, help=H["report"])
    ap.add_argument("--max-slowdown", type=float, default=DEFAULT_MAX_SLOWDOWN * 100,
                    metavar="PCT", help=H["max_slowdown"] % (DEFAULT_MAX_SLOWDOWN * 100))
    ap.add_argument("--max-speedup", type=float, default=DEFAULT_MAX_SPEEDUP * 100,
                    metavar="PCT", help=H["max_speedup"] % (DEFAULT_MAX_SPEEDUP * 100))
    ap.add_argument("--lang", choices=["zh", "en"], default=None, help=H["lang"])
    ap.add_argument("--segments", action="store_true", help=H["segments"])
    return ap


def main(argv=None) -> int:
    _setup_stdout()
    argv = list(sys.argv[1:] if argv is None else argv)
    # 先扫一次 --lang：argparse 自身的报错文案是固定中文/英文，需要语言已生效才好构建解析器。
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--lang", choices=["zh", "en"], default=None)
    pre_args, _ = pre.parse_known_args(argv)
    lang = pre_args.lang or _LANG
    set_lang(lang)
    args = build_cli(lang).parse_args(argv)
    L = lang
    if L == "en":
        def e(zh, en):  # 局部小工具：按 L 选串（main 内多处一次性提示）
            return en
    else:
        def e(zh, en):
            return zh

    if not args.detect_only and args.target is None:
        print(e("错误：请用 -t/--target 指定目标 BPM（只测速可用 --detect-only）",
               "Error: use -t/--target to set the target BPM (or --detect-only to just measure)"))
        return 2
    if args.metronome and args.detect_only:
        print(e("提示：--detect-only 模式不生成音频，已忽略节拍器选项",
               "Note: --detect-only produces no audio; the metronome option is ignored"))

    opt = ConvertOptions(
        target_bpm=float(args.target or 0.0),
        bpm=args.bpm,
        detect_only=args.detect_only,
        strict=args.strict,
        engine=args.engine,
        metronome=args.metronome,
        click_rel_db=args.click_rel_db,
        output_format=args.output_format,
        mp3_bitrate=args.mp3_bitrate,
        out_dir=Path(args.out_dir) if args.out_dir else None,
        mirror_dirs=bool(args.mirror_dirs),
        max_slowdown=max(0.0, float(args.max_slowdown)) / 100.0,
        max_speedup=max(0.0, float(args.max_speedup)) / 100.0,
        lang=L,
        segments=bool(getattr(args, "segments", False)),
    )

    pairs = list(collect_inputs_with_base(args.inputs, args.recursive))
    if not pairs:
        print(e("没有可处理的音频文件。支持格式：", "No audio files to process. Supported formats: ")
              + " ".join(sorted(SUPPORTED_EXTS)))
        return 1
    files = [f for f, _b in pairs]

    rows, ok, fail, skipped = [], 0, 0, 0
    for i, (p, base) in enumerate(pairs, 1):
        print(flush=True)
        log(f"[{i}/{len(files)}] {p}")
        row = process_file(p, opt, rel_base=base)
        rows.append(row)
        st = row_status(row.get("备注"))
        if st == "skipped":
            skipped += 1
        elif st == "failed":
            fail += 1
        else:
            ok += 1

    print(flush=True)
    if opt.detect_only:
        log(e(f"测速完成：共 {len(rows)} 个文件（报告的是检测器实测值，未做倍频折叠）。",
              f"Detection complete: {len(rows)} file(s) (reported tempos are raw detector values, no octave folding)."))
    else:
        if L == "en":
            tail = f", skipped {skipped} (beyond stretch limits)" if skipped else ""
            log(f"Conversion complete: {ok} ok, {fail} failed{tail}.")
        else:
            tail = f"，跳过 {skipped} 个（变速超出上限）" if skipped else ""
            log(f"转换完成：成功 {ok} 个，失败 {fail} 个{tail}。")
    report = Path(args.report) if args.report else Path("conversion_report.csv")
    write_report(rows, report, lang=L)
    log(e("报告已保存: ", "Report saved: ") + str(report.resolve()))
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
