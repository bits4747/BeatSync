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

# 支持的输入格式。前五种（wav/mp3/flac/ogg/aiff）由 libsndfile 直接解码；
# m4a/aac/wma 只作为**可添加**的扩展名保留：解码库读不了它们，添加后会给出一条
# 明确提示（见 load_audio），而不是让文件在扫描阶段无声消失。
SUPPORTED_EXTS = {".wav", ".mp3", ".flac", ".ogg", ".oga", ".m4a", ".aac", ".wma", ".aif", ".aiff"}
# 解码库明确不支持的容器（librosa 1.0 起无 audioread 回退，ffmpeg 也救不了）
_UNREADABLE_EXTS = {".m4a", ".aac", ".wma"}

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

# 变速上限的判定余量（rate 单位，0.005 = 0.5 个百分点）。
# 上限是硬边界，但测速本身有抖动：同一首 120 BPM 的曲子，44.1k / 48k / 22.05k
# 三种采样率下实测检测值 119.92 / 119.95 / 120.34（±0.3 BPM）。默认上限下
# 120→180 恰好压在 +50% 边界上，检测只要偏低 0.01 BPM 就会被判「需加速 50.0%，
# 上限 50%」而整首跳过——最常见的使用场景会随机失败。所以边界外留 0.5% 余量：
# 宁可多转 0.5%，也不要把刚好卡在边界的歌随机丢掉。
RATE_LIMIT_TOL = 0.005

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
    判定时边界外留 `RATE_LIMIT_TOL`（0.5%）余量：上限是硬边界，但测速有正常
    误差（同一首 120 BPM 曲在 44.1k/48k/22.05k 下实测 119.92~120.34），
    默认上限下 120→180 恰好压在 +50% 边界上，不放余量会随机被判越限跳过。

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
    # 边界容差：既覆盖「rate 恰好等于边界」（如 120→90 恰好 -25%），
    # 也吸收测速误差（见 RATE_LIMIT_TOL 的说明）。
    eps = RATE_LIMIT_TOL
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

    try:
        y, sr = librosa.load(str(path), sr=None, mono=False)
    except Exception as exc:  # noqa: BLE001
        # librosa 1.0 已彻底移除 audioread 回退，而 libsndfile 不支持
        # M4A / AAC / WMA 这几种容器——**装 ffmpeg 也读不出来**。
        # 这三种后缀仍留在 SUPPORTED_EXTS 里，是为了让用户添加后能看到一条
        # 可操作的提示，而不是被静默跳过或只看到底层的英文解码噪声。
        if Path(path).suffix.lower() in _UNREADABLE_EXTS:
            raise RuntimeError(tr(
                "无法读取该文件：本程序的音频解码库不支持 M4A / AAC / WMA 格式"
                "（安装 ffmpeg 也无效）。请先用其它工具转成 wav / mp3 / flac 再处理。",
                "Cannot read this file: the bundled audio decoder does not support "
                "M4A / AAC / WMA (installing ffmpeg does not help). "
                "Convert it to wav / mp3 / flac first.",
                get_lang())) from exc
        raise
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

def _dilate_max(onset, w: int = 2):
    """onset 包络的 ±w 帧滑窗最大值（越界按截断处理）。

    与 ``np.array([onset[max(0, x-w): x+w+1].max() for x in ...])`` 逐点等价，
    但用一次滑窗滤波算好整条包络，供 _grid_score 反复取值——梳状网格搜索要
    打分上万次，逐点切片是这里的主要开销。
    """
    env = np.asarray(onset, dtype=np.float64)
    if w <= 0 or len(env) == 0:
        return env
    pad = np.full(w, -np.inf, dtype=np.float64)
    padded = np.concatenate([pad, env, pad])
    try:
        from scipy.ndimage import maximum_filter1d
        out = maximum_filter1d(padded, size=2 * w + 1, mode="constant", cval=-np.inf)
        return out[w:w + len(env)]
    except Exception:                                    # scipy 缺失时退回逐点
        return np.array([env[max(0, i - w): i + w + 1].max() for i in range(len(env))])


def _grid_score(onset, fps: int, P: float, phase: float, t_start: float, t_end: float,
                w: int = 2, onset_max=None) -> float:
    """节拍网格与 onset 包络的对齐能量：网格点上 onset 的平均值（±w 帧取最大）。

    onset_max 可传入 ``_dilate_max(onset, w)`` 的预计算结果，批量打分时省掉
    逐点切片（数值与现算完全一致）。
    """
    n_frames = len(onset)
    k0 = int(np.ceil((t_start - phase) / P - 1e-9))
    k1 = int(np.floor((t_end - phase) / P + 1e-9))
    if k1 <= k0:
        return -1.0
    f = np.round((phase + np.arange(k0, k1 + 1) * P) * fps).astype(int)
    f = f[(f >= 0) & (f < n_frames)]
    if len(f) < 4:
        return -1.0
    if onset_max is None:
        onset_max = _dilate_max(onset, w)
    return float(np.mean(np.asarray(onset_max, dtype=np.float64)[f]))


def _comb_refine(onset, sr: int, beat_times):
    """梳状网格拟合：在检测节拍 ±4% 范围内搜索拍周期与相位，
    最大化节拍网格与 onset 包络的对齐能量，把速度精修到 ~0.1 BPM 级。

    与 `_comb_fit_at` 是同一套两段搜索的两份实现，**刻意不合并**：两者粗搜的
    相位采样方式不同（本函数以首个检测拍为中心 ±P 且随 P 放宽；fit_at 固定
    覆盖 [0, P)），合并会移动采样点、精修结果跟着漂，相位敏感的断言会回归。

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
    omax = _dilate_max(onset, 2)          # 一次算好，下面上万次打分复用

    best = [P0, t0, _grid_score(onset, fps, P0, t0, t_a, t_b, onset_max=omax)]
    # 粗搜：周期 ±4%，相位覆盖整个周期（含旧的 ±P/8 邻域）
    for P in np.linspace(P0 * 0.96, P0 * 1.04, 41):
        for ph in np.linspace(t0 - P, t0 + P, 25, endpoint=False):
            sc = _grid_score(onset, fps, P, ph, t_a, t_b, onset_max=omax)
            if sc > best[2]:
                best = [P, ph, sc]
    # 细搜：周期 ±0.15%，相位 ±3%
    P1, ph1, _ = best
    for P in np.linspace(P1 * 0.9985, P1 * 1.0015, 31):
        for ph in np.linspace(ph1 - 0.03 * P1, ph1 + 0.03 * P1, 9):
            sc = _grid_score(onset, fps, P, ph, t_a, t_b, onset_max=omax)
            if sc > best[2]:
                best = [P, ph, sc]
    return float(best[0]), float(best[1])


def _comb_fit_at(onset, sr, P0):
    """在指定拍周期档（±4%）做与 _comb_refine 同粒度的两段搜索。

    与 _comb_refine 的区别：相位不依赖 beat_track 的拍，直接搜整周期——
    供附点档修正重扫邻档用（邻档的拍位与原档的拍没有整数倍关系）。
    返回 (period_sec, phase_sec)；不可拟合时返回 (None, None)。
    """
    fps = sr / 256
    n_frames = len(onset)
    if not (0.2 <= P0 <= 2.5) or n_frames < 8:
        return None, None
    duration = n_frames / fps
    t_a = min(0.05 * duration, 5.0)
    t_b = max(duration - min(0.05 * duration, 5.0), t_a + 4 * P0)
    if t_b <= t_a + 4 * P0:
        t_a, t_b = 0.0, duration
    omax = _dilate_max(onset, 2)
    best = [P0, 0.0, _grid_score(onset, fps, P0, 0.0, t_a, t_b, onset_max=omax)]
    for P in np.linspace(P0 * 0.96, P0 * 1.04, 41):
        for ph in np.linspace(0, P, 25, endpoint=False):
            sc = _grid_score(onset, fps, P, ph, t_a, t_b, onset_max=omax)
            if sc > best[2]:
                best = [P, ph, sc]
    P1, ph1, _ = best
    for P in np.linspace(P1 * 0.9985, P1 * 1.0015, 31):
        for ph in np.linspace(ph1 - 0.03 * P1, ph1 + 0.03 * P1, 9):
            sc = _grid_score(onset, fps, P, ph, t_a, t_b, onset_max=omax)
            if sc > best[2]:
                best = [P, ph, sc]
    return float(best[0]), float(best[1])


# 附点档修正参数。
# 真拍与检测档成 3/4 或 3/2 倍（附点关系）时，真拍恰好落在检测网格的 1/3、
# 2/3 位上——此时节拍器有一半点击落在拍间，听感明显错拍。这类错误的根源是
# beat_track 的 120 BPM 先验：真档自相关峰只比 120 附近的假档强一点点时，
# 先验就把它翻掉（实测：180 BPM 的「失败的晚餐」判 120、96 BPM 的
# 「乏善可陈」判 128）。判据用「三度位显著度」：三度位的 onset 均值 ÷ 拍位
# 均值。真实曲上分离干净——正常档（三度位只有弱细分）实测 ≤0.77，附点错误
# 档实测 ≥0.99；阈值取中间并留余量。
DOTTED_FIRE = 0.85
# 修正方向：×3/4 与 ×3/2 两个邻档都可能。自相关/网格分在这两档之间的排序
# 不可靠（八分音符网格会骗过能量判据：实测「乏善可陈」真值 96 与八分档 192
# 的分数几乎并列甚至倒挂），拿不准时回到 120 BPM 感知锚点——与 beat_track
# 的 start_bpm 同源：人打拍子的生理舒适区。
DOTTED_ANCHOR_BPM = 120.0
DOTTED_BPM_LO = 50.0
DOTTED_BPM_HI = 240.0


def _thirds_ratio(onset, fps, P, phase, omax=None):
    """检测网格的「三度位显著度」：1/3、2/3 拍位处 onset 均值 ÷ 拍位均值。

    分析窗口与 _comb_refine 相同（避开首尾 5%，前奏/尾奏常无稳定节拍）。
    """
    n_frames = len(onset)
    fps = float(fps)
    duration = n_frames / fps
    t_a = min(0.05 * duration, 5.0)
    t_b = max(duration - min(0.05 * duration, 5.0), t_a + 4 * P)
    if t_b <= t_a + 4 * P:
        t_a, t_b = 0.0, duration
    if omax is None:
        omax = _dilate_max(onset, 2)

    def mean_at(offset):
        k0 = int(np.ceil((t_a - phase - offset) / P - 1e-9))
        k1 = int(np.floor((t_b - phase - offset) / P + 1e-9))
        if k1 <= k0:
            return -1.0
        f = np.round((phase + offset + np.arange(k0, k1 + 1) * P) * fps).astype(int)
        f = f[(f >= 0) & (f < n_frames)]
        if len(f) < 4:
            return -1.0
        return float(np.mean(np.asarray(omax, dtype=np.float64)[f]))

    beat = mean_at(0.0)
    if beat <= 0:
        return 0.0
    third_a, third_b = mean_at(P / 3), mean_at(2 * P / 3)
    if third_a < 0 or third_b < 0:
        return 0.0
    return max(third_a, third_b) / beat


def _dotted_level_fix(onset, sr, P, phase):
    """附点档修正：检测档的三度位上有拍级能量时，改判到 3/4 或 3/2 倍邻档。

    返回修正后的 (P, phase)；不修正时原样返回。邻档重新做梳状拟合，并用
    三度位显著度复核——若邻档仍显著（连环附点）再走一步；两步后仍显著就
    按当前档返回（不猜第三步，连环三层以上的附点关系没有实例）。
    """
    n_frames = len(onset)
    if n_frames < 8:
        return P, phase
    fps = sr / 256
    omax = _dilate_max(onset, 2)
    if _thirds_ratio(onset, fps, P, phase, omax) < DOTTED_FIRE:
        return P, phase
    for _ in range(2):
        bpm0 = 60.0 / P
        cands = [bpm0 * m for m in (0.75, 1.5)
                 if DOTTED_BPM_LO <= bpm0 * m <= DOTTED_BPM_HI]
        if not cands:
            break
        bc = min(cands, key=lambda b: abs(np.log2(b / DOTTED_ANCHOR_BPM)))
        P2, phase2 = _comb_fit_at(onset, sr, 60.0 / bc)
        if P2 is None:
            break
        P, phase = P2, phase2
        if _thirds_ratio(onset, fps, P, phase, omax) < DOTTED_FIRE:
            break
    return P, phase


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

    fit = _robust_beat_fit(k, bt, P)
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


def _robust_beat_fit(k, t, P: float):
    """(拍号 → 拍时刻) 的稳健低阶多项式拟合，返回系数或 None。

    阶数自适应：速度稳定 1 阶（= 规整网格）；确有系统性变速升到 2~3 阶
    （跟随变速但不跟随逐拍抖动）。迭代剔除离群拍（残差 >0.25P 或 5×MAD）。
    """
    k = np.asarray(k, dtype=np.float64)
    t = np.asarray(t, dtype=np.float64)
    keep = np.ones(len(t), dtype=bool)
    fit = None
    for deg in (1, 2, 3):
        kk, tt = k[keep], t[keep]
        if len(kk) < max(6, deg + 3):
            break
        coef = np.polyfit(kk, tt, deg)
        for _ in range(3):
            resid = t - np.polyval(coef, k)
            med = np.median(resid[keep])
            inl = np.abs(resid - med) <= max(0.25 * P,
                                             5.0 * np.median(np.abs(resid[keep] - med)))
            if inl.sum() < max(6, deg + 3):
                break
            keep = inl
            coef = np.polyfit(k[keep], t[keep], deg)
        resid_in = (t - np.polyval(coef, k))[keep]
        # 系统性残差（中位绝对残差）是否已经够小？够小就不必升阶
        sys_err = float(np.median(np.abs(resid_in - np.median(resid_in))))
        if sys_err <= 0.12 * P:
            return coef
        fit = coef  # 记录当前最好，继续尝试更高阶
    return fit


def _relock_beats(onset, sr: int, P: float, phase: float, t0: float, t1: float,
                  hop: int = 256):
    """把恒速骨架（phase + k·P）的拍位重新锁回音乐自己的瞬态上。

    **逐拍跟踪**（锁相环式）：下一拍的预测位置 = 上一锁定拍 + (拍号差)·P，
    在预测位 ±0.25P 窗内取**过阈且离预测最近**的帧。恒速骨架只用于初始
    锚定（前部骨架漂移可忽略）——骨架与真实拍的偏差是累积性的（层值误差
    ×几百拍，2026-10-25 实测 0.4% 失配 4 分钟漂出约 1 秒），固定窗重锁会在
    漂出 ±1/4 拍后锁不到真拍；逐拍跟踪则每拍吸收偏差、不累积。

    ±0.25P 窗保证不会跳层：半拍位（八分低音）在窗外，永远选不中；窗内
    零散噪声峰由 _refit_grid 的稳健拟合剔除。窗内无过阈帧的拍（安静段落）
    被丢弃，交给拟合外推。

    窗内取**过阈帧中 onset 最强**的（不是离预测最近的）：「最近」有惰性——
    锁定点被骨架拽着走，速度修正打折（实测真实 95.3 只修到 95.0）；「最强」
    直接锁在拍瞬态中心。跳层风险不变：半拍位仍在 ±0.25P 窗外。

    返回 (拍号数组, 拍时刻数组，绝对秒)。
    """
    onset = np.asarray(onset, dtype=np.float64)
    n = len(onset)
    fps = sr / hop
    pos = onset[onset > 0]
    if pos.size < 8:
        return np.array([]), np.array([])
    thr = float(np.percentile(pos, 90)) * 0.35
    half = max(1, int(round(0.25 * P * fps)))
    k0 = int(np.ceil((t0 - phase) / P))
    k1 = int(np.floor((t1 - phase) / P))
    if k1 - k0 < 3:
        return np.array([]), np.array([])
    ks, ts = [], []
    last_t, last_k = None, None
    for k in range(k0, k1 + 1):
        if last_t is None:
            c = int(round((phase + k * P) * fps))
        else:
            c = int(round((last_t + (k - last_k) * P) * fps))
        a, b = max(0, c - half), min(n, c + half + 1)
        if b - a < 2:
            continue
        w = onset[a:b]
        j = a + int(np.argmax(w))
        if w[j - a] < thr:
            continue
        t = j / fps
        ks.append(k)
        ts.append(t)
        last_t, last_k = t, k
    return np.asarray(ks, dtype=np.float64), np.asarray(ts, dtype=np.float64)


def _refit_grid(onset, sr: int, P: float, phase: float, t0: float, t1: float,
                hop: int = 256):
    """恒速骨架的「贴合化」：重锁实测拍 → 稳健多项式拟合 → 整数拍号网格。

    拍号范围与恒速网格完全一致（拍数不变，节拍器密度不受影响），但每个
    拍位贴回音乐真实瞬态，点击跟随音乐拍、不再累积漂移。拟合失败或形状
    护栏（严格单调、间距 0.75~1.25P）不过时返回 None，调用方退回恒速网格。
    返回绝对秒时刻数组。
    """
    ks, ts = _relock_beats(onset, sr, P, phase, t0, t1, hop)
    if len(ks) < 8:
        return None
    fit = _robust_beat_fit(ks, ts, P)
    if fit is None:
        return None
    k0 = int(np.ceil((t0 - phase) / P))
    k_end = int(np.floor((t1 - phase) / P + 1e-9))
    if k_end <= k0:
        return None
    kg = np.arange(k0, k_end + 1, dtype=np.float64)
    grid = np.polyval(fit, kg)
    if np.any(np.diff(grid) <= 0.5 * P) or np.any(np.abs(np.diff(grid) - P) > 0.25 * P):
        return None
    return grid


class SegmentPlan(NamedTuple):
    """分段变速里一段的完整方案（时间均为**输入音频**秒轴；拼接后再映射到输出轴）。"""
    t0: float
    t1: float
    bpm: float
    beats: np.ndarray        # 段内拍时刻（输入轴，段起点为 0）
    plan: "StretchPlan"


def _frame_logtempo(y_mono, sr: int, hop: int = 512, mel=None):
    """逐帧 BPM 曲线（log2 域，3 帧中值平滑）。

    librosa.feature.tempo 不开 aggregate，每帧自己估一次；倍频跳变在 log 域是
    台阶，适合做分段常数拟合。中位数 ±2.2 倍裁剪把个别乱跳的帧压回合理带内，
    裁剪后取 log 前先 np.maximum 兜底，避免除零。

    mel：可选的整曲 mel 谱（hop=256）。hop=512 的帧正好是它的偶数帧，所以
    S=mel[:, ::2] 与「按 hop=512 重算一遍」等价，能省掉一次重复的 STFT。
    """
    import librosa
    from scipy.ndimage import median_filter
    if mel is None:
        onset = librosa.onset.onset_strength(y=y_mono, sr=sr, hop_length=hop)
    else:
        step = max(1, hop // 256)
        onset = librosa.onset.onset_strength(S=librosa.power_to_db(mel[:, ::step]),
                                             sr=sr, hop_length=hop)
    tp = np.asarray(librosa.feature.tempo(onset_envelope=onset, sr=sr,
                                          aggregate=None)).ravel().astype(np.float64)
    med = float(np.median(tp[tp > 0])) if np.any(tp > 0) else 120.0
    tp = np.clip(tp, med / 2.2, med * 2.2)
    logt = np.log2(np.maximum(tp, 1e-6))
    # 3 帧滑动中值用 scipy 实现：原来逐帧取 np.median 的 Python 循环在 5 分钟曲上
    # 是 5 万次切片（实测 0.69 s / 次），而 median_filter 在同一组数据上逐位相同
    # （max|diff|=0），耗时 ~0。
    sm = median_filter(logt, size=3, mode="nearest")
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
                          min_seg_s=4.0, min_analyze_s=12.0, lang=None,
                          mel=None):
    """变拍检测：返回 [(t0, t1), ...]（输入秒轴，覆盖全曲）；恒速歌返回单段。

    做法：逐帧 log-BPM 曲线 → 递归二分找变点 → 相邻过近( <dedup_s )的变点合并
    → 短段(<min_seg_s)并入邻段、速度差 <merge_ratio 的邻段并成一段。
    边界实测误差 <0.7 s（合成曲验收）。
    每段至少 min_analyze_s 秒，否则 detect_tempo 拍点太少不可靠。

    mel：可选的整曲 mel 谱（hop=256、fmax=0.5*sr），用于省掉重算 STFT。
    """
    total = len(y_mono) / sr
    if total < min_analyze_s * 1.5:
        return [(0.0, total)]
    logt, tf = _frame_logtempo(y_mono, sr, mel=mel)
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

    def _med(a, b):
        return float(np.median(logt[a:max(b, a + 1)]))

    # 合并循环。必须「只 pop 两段之间那条边界」，并且要挑最该并的那一对再动手：
    # 旧版遇到最后一个短段时会 pop edges[1]（把开头两段并掉），于是不断从头重来，
    # 短段越多级联越猛，实测同一首曲子 min_seg_s=4 给出 1 段、min_seg_s=8 给出
    # 10 段（对 min_seg_s 非单调），完全是这个 bug 造成的。
    changed = True
    guard = 0
    while changed and len(edges) > 2 and guard < 4 * len(edges) + 8:
        guard += 1
        changed = False
        nseg = len(edges) - 1
        meds = [_med(edges[i], edges[i + 1]) for i in range(nseg)]
        # 1) 先处理太短的段：并入 log 速度更接近的那一侧（不硬塞给任意邻居）
        short = [i for i in range(nseg) if seg_dur(edges[i], edges[i + 1]) < min_seg_s]
        if short:
            i = min(short, key=lambda k: seg_dur(edges[k], edges[k + 1]))
            opts = []
            if i > 0:
                opts.append((abs(meds[i - 1] - meds[i]), i - 1))
            if i + 1 < nseg:
                opts.append((abs(meds[i + 1] - meds[i]), i))
            if opts:
                edges.pop(min(opts)[1] + 1)      # 只删 lo 与 hi 的公共边界
                changed = True
                continue
        # 2) 再合并「速度差小于 merge_ratio」的相邻段，每次只并最接近的一对
        best = None
        for i in range(nseg - 1):
            d = abs(meds[i] - meds[i + 1])
            if d < thr and (best is None or d < best[0]):
                best = (d, i)
        if best is not None:
            edges.pop(best[1] + 1)
            changed = True
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


SEG_GRID_MIN_SCORE = 1.10   # 段内拍网格「命中比」低于此值 → 认为该段拍子没跟上
SEG_MIN_SPREAD = 1.15       # auto 模式：最快/最慢段速度比低于此值不值得分段
SEG_MIN_SEG_S = 8.0         # 分段最短时长（太短的段测速不可靠，并入邻段）
# 相邻段测得速度只差这么一点（相对）、且每拍点击数相同时，认为两段输出等价 → 合并。
# 为什么需要：段间测速本身有 ±1~3% 抖动（同一段音乐换切点就会差这么多），不合并就会
# 出现「相邻段 a2 与 cpb 完全一样、只有 rate 差千分之几」的多余分段——用户听不出区别，
# 却白白多一次拼接、多一处接缝。旧判据是 rate 精确到 1e-9 相等，几乎永不命中。
SEG_MERGE_RATIO = 0.06

# ---- 整数家族：测速结果的倍频/分数比收敛 --------------------------------
# 测速器对同一段音乐可能给出真实速度的整数倍、整数分之一，也可能落到 4/3、
# 2/3 这类**分数比**上（把三连音数成四分音符）。分数比是最坏情况：节拍器
# 敲 target 时，音乐实际速度 = 真值×rate，点击要踩拍就必须
#     target / (真值×rate) 是整数或 1/整数，
# 而 CPB_CANDIDATES 只含整数与 1/整数，分数比补不回来 → 真的错频、用户听到
# 「乱七八糟」。所以这里把各段速度收敛到同一个「整数家族」：
#   家族 = {B×n, B/n | n = 1..INT_FAMILY_MAX}
# 家族内选哪一层不影响节拍器（cpb 能补），唯一判据就是变速代价最小。
# 各段若同属一个家族，说明它们本来就是同一段音乐的不同层 → 合并成一段。
INT_FAMILY_MAX = 16          # 家族最大整数倍数
INT_RATIO_TOL = 0.04         # 判「互为整数比」的相对容差（4%）
SNAP_MAX = 0.43              # 吸附到家族层的最大 log2 距离（≈1.35 倍）
SEG_HIT_MIN = 0.75           # 单层塌缩前要求的最小网格命中率（真变拍实测 0.33~0.40）
COLLAPSE_FAMILY_COVER = 0.98  # 塌缩要求：一个 base 吸附 ≥98% 的长段时长才算「同一首音乐」
KICK_FLIP_RATIO = 1.02        # 底鼓带相位裁决：反拍 onset 超过正拍 2% 即判网格半拍反相
OUTBEATS_FIX_EVERY = 8        # 输出轴节拍器：每隔几拍用音乐校一次相位
OUTBEATS_MAX_STEP = 0.060     # 单块相位修正量上限（秒）——比例控制 0.5×偏差的限幅
OUTBEATS_DEADBAND = 0.02      # 块质心偏差小于 2%P 视为常值偏置，不积分（见 _output_beats）
SEG_SHAPE_MIN_S = 10.0       # 判「来回横跳」时只用不短于它的段（短段测速不可靠）
SEG_SHAPE_FRAC = 0.06        # 同上，同时要求段长至少占全曲这个比例
LAYER_REFINE_TOL = 0.02      # 家族选层后精修层速度的搜索半径（±2%，见 _refine_layer_speed）
HALVE_MIN_PARITY = 0.93      # 把实测速度减半前，要求实测网格上奇偶拍能量比低于它
COLLAPSE_DOM_COVER = 0.60    # 塌缩前，长段里最大一簇（±5%）的时长占比下限（见 _dominant_cover）
LAYER_RATIO_MAX = 6          # 「只差一个层」允许的最大分子/分母（见 _is_layer_ratio）
LAYER_RATIO_TOL = 0.02       # 同上，有理比容差


def _min_cost_layer(bpm: float, target: float, max_slowdown: float,
                    max_speedup: float):
    """把 bpm 变到 target 的最小代价方案（rate 尽量接近 1）。

    返回 (key, cpb, a2, rate)，key 越小越优（并列时 cpb 小=点击更密）。
    都不在 [1-max_slowdown, 1+max_speedup] 内时返回 None。
    """
    eps = RATE_LIMIT_TOL
    best = None
    for cpb in (1, 2, 3, 4, 6, 8):
        a2 = target / cpb
        rate = a2 / bpm
        if (1.0 - max_slowdown - eps) <= rate <= (1.0 + max_speedup + eps):
            key = (round(abs(math.log2(rate)), 4), cpb)
            if best is None or key < best[0]:
                best = (key, cpb, a2, rate)
    return best


def _snap_family(bpm: float, base: float, nmax: int = INT_FAMILY_MAX):
    """把 bpm 吸附到 base 的整数家族里最近的一层，返回 (log2距离, 层值)。"""
    best = None
    for n in range(1, nmax + 1):
        for cand in (base * n, base / n):
            if not (30.0 <= cand <= 480.0):
                continue
            d = abs(math.log2(bpm / cand))
            if best is None or d < best[0]:
                best = (d, cand)
    return best if best is not None else (0.0, bpm)


def _int_ratio(a: float, b: float, tol: float = INT_RATIO_TOL,
               nmax: int = 8):
    """a、b 互为整数比时返回那个整数，否则 None。"""
    if a <= 0 or b <= 0:
        return None
    x = max(a, b) / min(a, b)
    n = int(round(x))
    if 1 <= n <= nmax and abs(x - n) <= tol * n:
        return n
    return None


def _common_family(pairs, target: float, max_slowdown: float,
                   max_speedup: float, nmax: int = INT_FAMILY_MAX,
                   min_frac: float = 0.80):
    """找公共基础脉冲 B，使**足够时长**的段速度都落在 B 的整数家族里。

    参数 pairs 是 [(bpm, 时长)] 列表。
    返回 (B, layer, cpb, a2, rate)，找不到返回五个 None。

    判据用 `_snap_family` 的 log2 距离（≤ SNAP_MAX）而不是「两两整数比容差」：
    YYZ 的 102.8 与 140.4 相差 1.366 倍，按「整数比 ±4%」会被 4/3=1.333 蒙混
    过关（差 2.4%），整首歌的前奏变拍就被抹掉了。SNAP_MAX=log2(1.35) 恰好把
    1.366 挡在外面，同时仍能吸收「同一段音乐被数成 4/3 或 3/4」这类层级误差。

    为什么 base 只取**实测段速**、不扫稠密网格：稠密扫描配 4% 容差几乎能让任意
    一组速度都凑出某个 base（130.5 与 132.7 差 1.7%，乘个大 n 就对齐了），等于
    把真变拍也吞掉。

    为什么允许 min_frac=0.80 的时长落在家族外：短段的测速噪声会给出与家族无关
    的孤立值（奢香夫人有一处 8.3 秒的 112.35，只占全曲 3.2%），把它当成「真变拍」
    会让整首歌拒绝合并、退化成多段乱变速。

    层值取**家族内最低的实测段速**：低层是这一族里最接近音乐真实拍速的那一层，
    用它算 rate 最小、clicks_per_beat 的语义也对（测 86、目标 180 → a2=90、
    每拍敲 2 下）；若取 174 那层会得到 a2=180、每拍 1 下，而音乐实际只有 90，
    点击密度就和报告对不上了。
    """
    ps = [(float(v), float(w)) for v, w in pairs if v and v > 0 and w and w > 0]
    if not ps:
        return None, None, None, None, None
    total = sum(w for _v, w in ps) or 1.0
    best = None
    for base in sorted({round(v, 3) for v, _w in ps}):
        good = 0.0
        members = []
        for v, w in ps:
            d, _layer = _snap_family(v, base, nmax)
            if d > SNAP_MAX:
                continue
            good += w
            members.append(v)
        if not members or good / total < min_frac:
            continue
        low = min(members)
        _d, layer = _snap_family(low, base, nmax)
        mc = _min_cost_layer(layer, target, max_slowdown, max_speedup)
        if mc is None:
            continue
        key = (round(mc[0][0], 4), mc[0][1])
        if best is None or key < best[0]:
            best = (key, base, layer, mc[1], mc[2], mc[3])
    if best is None:
        return None, None, None, None, None
    _key, base, layer, cpb, a2, rate = best
    return base, layer, cpb, a2, rate


def _phase_grid(onset, sr: int, bpm: float, t0: float, t1: float,
                nphase: int = 64, tol_frac: float = 0.045,
                hop: int = 256):
    """在 [t0, t1) 上按 bpm 生成**相位最优**的规整网格，返回 (grid_rel, hit_frac)。

    网格与 bpm 严格自洽（间距恒为 60/bpm），这是硬要求：段方案里的 `bpm` 与
    实际拍点间距必须一致，否则 `click_schedule` 会按错的拍距去敲——表现为
    「节拍器变成机关枪」（实测拍距 0.46s 却记成 93 BPM，点击密度 261/min
    而目标只有 180/min）。

    hit_frac 是这条网格真正踩到起音的比例（同 `_seg_hit_frac` 的口径），
    用来确认这一层确实能解释这段音乐：层选错了（例如把 134.9 的歌当成 93），
    网格只能命中六成左右的点，会被拒掉。

    grid_rel 是相对 t0 的拍时刻（SegmentPlan.beats 的约定）。
    """
    if bpm <= 0 or t1 - t0 < 1.0:
        return None, 0.0
    fa = max(0, int(t0 * sr / hop))
    fb = max(fa + 1, int(t1 * sr / hop))
    env = np.asarray(onset[fa:fb], dtype=np.float64)
    pos = env[env > 0]
    if pos.size < 8:
        return None, 0.0
    thr = float(np.percentile(pos, 90)) * 0.30
    T = 60.0 / bpm
    half = max(1, int(tol_frac * T * sr / hop))
    omax = _dilate_max(onset, half)
    n = len(onset)
    best = (-1.0, 0.0)
    for k in range(nphase):
        ph = k / nphase * T
        cnt = int((t1 - t0 - ph) / T) + 1
        if cnt < 6:
            continue
        g = t0 + ph + np.arange(cnt) * T
        g = g[g < t1]
        if len(g) < 6:
            continue
        idx = np.round(g * sr / hop).astype(np.int64)
        idx = idx[(idx >= 0) & (idx < n)]
        if len(idx) < 6:
            continue
        frac = float(np.mean(omax[idx] >= thr))
        if frac > best[0]:
            best = (frac, ph)
    if best[0] < 0:
        return None, 0.0
    ph = best[1]
    cnt = int((t1 - t0 - ph) / T) + 1
    g = t0 + ph + np.arange(cnt) * T
    g = g[(g >= t0) & (g < t1)]
    if len(g) < 6:
        return None, 0.0
    return g - t0, best[0]


def _seg_align_score(onset, rms, sr: int, beats, t0: float, t1: float,
                     hop: int = 256) -> float:
    """段内拍网格的「命中比」：拍点处 onset 均值 ÷ 该段有效帧 onset 均值。

    >1 表示拍点确实落在起音上；≈1 表示网格和音乐基本无关（拍子没跟上）。
    只统计非静音帧，避免淡出尾巴把基准拉低。
    """
    n = min(len(onset), len(rms))
    f0 = max(0, min(int(t0 * sr / hop), n))
    f1 = max(0, min(int(t1 * sr / hop), n))
    if f1 - f0 < 8 or not len(rms):
        return 0.0
    env, r = onset[f0:f1], rms[f0:f1]
    v = r > float(np.max(rms)) * 0.03
    if not bool(np.any(v)):
        return 0.0
    base = float(np.mean(env[v]))
    if base <= 0:
        return 0.0
    idx = [int(round(float(t) * sr / hop)) - f0 for t in beats if t0 <= float(t) < t1]
    idx = [i for i in idx if 0 <= i < len(env) and bool(v[i])]
    if len(idx) < 4:
        return 0.0
    return float(np.mean(env[idx])) / base


def _seg_hit_frac(onset, sr: int, bpm: float, t0: float, t1: float,
                  nphase: int = 32, tol_frac: float = 0.045,
                  hop: int = 256) -> float:
    """某段音乐在速度 bpm 的网格下，「网格点真正踩到起音」的比例（0~1）。

    与 _seg_align_score 的区别：那个是「能量比」，慢网格天然占便宜（点少、均值高）；
    这个是**命中率**，慢网格套在快音乐上只能命中一小部分点（例如 90 的网格套在
    150 的音乐上只有 3/5 的点落得到起音上），所以它能区分「同一段音乐的不同层」
    与「真的换了速度」。

    阈值取段内 onset 正值 90 分位的 30%：既躲开底噪，又不要求每一下都是重音。
    """
    if bpm <= 0 or t1 - t0 < 1.0:
        return 0.0
    fa = max(0, int(t0 * sr / hop))
    fb = max(fa + 1, int(t1 * sr / hop))
    env = np.asarray(onset[fa:fb], dtype=np.float64)
    pos = env[env > 0]
    if pos.size < 8:
        return 0.0
    thr = float(np.percentile(pos, 90)) * 0.30
    T = 60.0 / bpm
    half = max(1, int(tol_frac * T * sr / hop))
    n_all = len(onset)
    best = 0.0
    for k in range(nphase):
        ph = k / nphase * T
        g = t0 + ph + np.arange(int((t1 - t0 - ph) / T) + 1) * T
        g = g[(g >= t0) & (g < t1)]
        if len(g) < 6:
            continue
        idx = np.round(g * sr / hop).astype(int)
        hits = 0
        for i in idx:
            lo, hi = max(0, i - half), min(n_all, i + half + 1)
            if hi > lo and float(np.max(onset[lo:hi])) >= thr:
                hits += 1
        f = hits / len(g)
        if f > best:
            best = f
    return best


def _segments_mode(opt) -> str:
    """把 opt.segments（bool 或 "auto"/"on"/"off"）规整成三态字符串。

    auto = 只在检测到**明显**变拍时自动分段（默认）；on = 强制分段（严格：
    任一段越限则整首跳过）；off = 一律整曲单速。
    """
    v = getattr(opt, "segments", "auto")
    if isinstance(v, str):
        v = v.strip().lower()
        if v in ("auto", "on", "off"):
            return v
        if v in ("1", "true", "yes", "force"):
            return "on"
        if v in ("0", "false", "no", "none", ""):
            return "off"
        return "auto"
    if v is None:
        return "auto"
    return "on" if v else "off"


def _segments_worthwhile(plans) -> bool:
    """auto 模式：段间速度差是否大到值得分段（最快/最慢 ≥ SEG_MIN_SPREAD）。

    分段会对每段各做一次变速与拼接，本身有代价（接缝、音质）；只差百分之几的
    「速度漂移」不值得切，宁可整曲单速。
    """
    bpms = [sp.bpm for (_t0, _t1, sp) in plans if sp is not None]
    if len(bpms) < 2:
        return False
    lo, hi = min(bpms), max(bpms)
    return lo > 0 and (hi / lo) >= SEG_MIN_SPREAD


def _segment_plans(y_mono, sr: int, target: float, strict: bool = False,
                   max_slowdown: float = DEFAULT_MAX_SLOWDOWN,
                   max_speedup: float = DEFAULT_MAX_SPEEDUP, lang=None,
                   min_seg_s: float = SEG_MIN_SEG_S,
                   merge_infeasible: bool = True, mel=None):
    """分段测速 + 每段各按原「最小变速」规则出方案。

    返回 (plans, infeasible)。

    - plans：[(t0, t1, SegmentPlan)]；段数 == 1（infeasible 为 None）表示「没发现
      明显变拍」，调用方应退回整曲单方案路径。
    - infeasible：None，或 (t0, t1, bpm, reason) —— 表示多段且**某段**按上下限
      算不出可行方案。auto 模式由调用方退回整曲处理（不丢歌）；on 模式按
      m02195 的规则把整首跳过。

    mel：可选的整曲 mel 谱（hop=256、fmax=0.5*sr、对 y_mono 同一切片算的）。
    调用方（process_file）手里有就传进来，省一次全曲 STFT（4 分钟的歌约 3.6 秒，
    占分段路径总耗时的四分之一）；没有就现场算，行为不变。

    两道「合并」闸门（都不改变「每段各按最小变速」这条主规则）：
      1. 拍网格跟不上（命中比 < SEG_GRID_MIN_SCORE）的段 → 并入拍网格更可信
         的邻段。典型是淡出尾奏、噪声段：单独给它一个网格反而更糟。
      2. 算不出可行方案的段 → 并入速度更接近（log2 域距离最小）的邻段。
         典型是两三秒的过渡/加花段，它自己「加速差一点、减速也差一点」，
         但并进速度相近的大段后整段是可行的。
    每轮只处理最该处理的一段，然后对合并后的段重新测速，直到没有需要处理的段。
    """
    import librosa
    # 整首的 mel 谱算一次，逐帧速度曲线、各段测速、onset 包络全都复用它：
    # 分段时同一首曲子的十几段被反复测速，每段各做一次 STFT/mel 是纯重复劳动
    # （长曲上单段就 0.5 秒）。mel 是逐帧的线性变换，按帧切片与「对切片重算」
    # 只差边界窗，实测测速结果一致（bpm 差 ≤0.05）。恒速歌走下面的早退分支，
    # mel 只在 _frame_logtempo 里用这一次，也不算浪费。
    if mel is None:
        mel = np.abs(librosa.feature.melspectrogram(y=y_mono, sr=sr,
                                                    hop_length=256,
                                                    fmax=0.5 * sr))

    segs = detect_tempo_segments(y_mono, sr, min_seg_s=min_seg_s, lang=lang,
                                 mel=mel)
    total = len(y_mono) / sr
    if len(segs) < 2:
        return [(segs[0][0], segs[0][1], None)], None

    onset = librosa.onset.onset_strength(S=librosa.power_to_db(mel),
                                         sr=sr, hop_length=256)
    rms = librosa.feature.rms(y=y_mono, frame_length=2048, hop_length=256)[0]

    infos = []
    detected_cache = {}

    def _detect_seg(a: int, b: int):
        """同一段音频（同一 a/b 切片）只测一次速。

        合并重测时只有被合并的那一段变了，其余段的切片与上一轮完全相同；
        测速是这里最贵的一步（4 分钟的歌每段 2~6 秒），重复算没有意义。
        """
        key = (a, b)
        got = detected_cache.get(key)
        if got is None:
            fa = int(round(a / 256))            # 采样点 → 帧号（hop=256）
            fb = max(fa + 1, int(round(b / 256)))
            try:
                got = detect_tempo(y_mono[a:b], sr, lang, mel=mel[:, fa:fb])
            except RuntimeError:
                got = (0.0, np.asarray([], dtype=np.float64))
            got = (got[0], np.asarray(got[1], dtype=np.float64).copy())
            detected_cache[key] = got
        return got

    for _round in range(8):
        infos = []
        for (t0, t1) in segs:
            a, b = int(t0 * sr), int(t1 * sr)
            bpm, beats = _detect_seg(a, b)
            rel = np.asarray(beats, dtype=np.float64)
            plan = (choose_stretched_tempo(bpm, target, strict, max_slowdown, max_speedup)
                    if bpm > 0 else None)
            infos.append({"t0": t0, "t1": t1, "bpm": float(bpm), "beats": rel,
                          "plan": plan,
                          "score": _seg_align_score(onset, rms, sr, rel + t0, t0, t1)})
        if len(infos) < 2:
            break
        # 快速通道：首轮各段都有可行方案时，先做一次「同家族来回抖动」判定。
        # 这类曲子的最终结论就是「塌缩成一层」（整首本来就是一条拍子，只是测速
        # 在八度/层级间跳），而下面的逐对合并每轮要给所有相邻对重测一次才能判定，
        # 实测 7 段的抖动曲要跑 40+ 次测速（22 秒）。先便宜地试一次，能塌缩就直接
        # 返回，不能塌缩再进逐对循环。
        if _round == 0 and len(infos) >= 3:
            guess = [(s["t0"], s["t1"],
                      SegmentPlan(s["t0"], s["t1"], s["bpm"], s["beats"], s["plan"]))
                     for s in infos]
            fast = _collapse_same_family(guess, target, max_slowdown, max_speedup,
                                         onset=onset, sr=sr, mel=mel)
            if len(fast) < len(guess):
                return fast, None
        bad_plan = [i for i, s in enumerate(infos) if s["plan"] is None]
        if bad_plan and not merge_infeasible:
            break                     # on 模式：越限即整首跳过，不做合并
        weak = [i for i, s in enumerate(infos) if s["score"] < SEG_GRID_MIN_SCORE]
        if bad_plan:
            i = bad_plan[0]
        elif weak:
            i = min(weak, key=lambda k: infos[k]["score"])
        else:
            break
        # 选并入方向：越限段看速度是否更接近；弱网格段看邻段网格是否更可信
        cand = []
        if i > 0:
            if infos[i]["plan"] is None:
                d = abs(math.log2(infos[i]["bpm"] / infos[i - 1]["bpm"])) \
                    if infos[i]["bpm"] > 0 and infos[i - 1]["bpm"] > 0 else 0.0
            else:
                d = -infos[i - 1]["score"]
            cand.append((d, i - 1))
        if i < len(infos) - 1:
            if infos[i]["plan"] is None:
                d = abs(math.log2(infos[i]["bpm"] / infos[i + 1]["bpm"])) \
                    if infos[i]["bpm"] > 0 and infos[i + 1]["bpm"] > 0 else 0.0
            else:
                d = -infos[i + 1]["score"]
            cand.append((d, i + 1))
        if not cand:
            break
        j = min(cand)[1]
        lo, hi = min(i, j), max(i, j)
        segs = sorted([(s["t0"], s["t1"]) for k, s in enumerate(infos) if k not in (lo, hi)]
                      + [(infos[lo]["t0"], infos[hi]["t1"])])

    if len(infos) < 2:
        return [(0.0, total, None)], None
    for s in infos:
        if s["plan"] is None:
            reason = stretch_limits_text(s["bpm"], target, max_slowdown, max_speedup, lang=lang)
            return [(s["t0"], s["t1"], None)], (s["t0"], s["t1"], float(s["bpm"]), reason)
    out = [(s["t0"], s["t1"], SegmentPlan(s["t0"], s["t1"], s["bpm"], s["beats"], s["plan"]))
           for s in infos]
    # 相邻段方案输出等价（拉伸比几乎一样、每拍点击数相同）→ 合并，少一次无谓拼接。
    # 判据为什么用「bpm 相对差」而不是「a2 或 rate」：
    #   * a2 恒等于 target/cpb，cpb=1 时每段的 a2 都是 180，拿它判会把速度真的差 20%
    #     的两段也并到一起（并完是同一 rate，输出速度就会在段内漂移）。
    #   * rate = a2/bpm，所以 |Δrate|/rate 与 |Δbpm|/bpm 是同一个量，取 bpm 更直观。
    # 阈值取 SEG_MERGE_RATIO：比这更小的速度差听不出「换了一段」，并掉它省一次拼接、
    # 少一处接缝。旧判据要求 rate 精确到 1e-9 相等，几乎永不命中，于是留下大量
    # 「a2/cpb 完全相同」的多余分段。
    # 合并后按拼接后的拍点重算 bpm 与方案（不直接沿用前一段的 rate），
    # 保证合并段的平均速度仍然朝向目标，不会累积偏移。
    merged = []
    for item in out:
        prev = merged[-1] if merged else None
        if prev is not None and prev[2].bpm > 0 and item[2].bpm > 0 \
                and abs(math.log2(prev[2].bpm / item[2].bpm)) <= math.log2(1.0 + SEG_MERGE_RATIO) \
                and abs(float(prev[2].plan.clicks_per_beat)
                        - float(item[2].plan.clicks_per_beat)) < 1e-9:
            t0, _t1, sp = prev
            beats = np.concatenate([sp.beats, item[2].beats + (item[0] - t0)])
            t1 = item[1]                     # 区间终点必须取后一段的结尾
            bpm = 60.0 * len(beats) / max(1e-9, (t1 - t0))
            plan = choose_stretched_tempo(bpm, target, strict, max_slowdown, max_speedup)
            if plan is not None:
                merged[-1] = (t0, t1, SegmentPlan(t0, t1, bpm, beats, plan))
                continue
        merged.append(item)
    # 第三道闸门：相邻段「其实是一段」时合并。
    # 两种常见冗余：
    #   1) 输出等价——a2 与每拍点击数都相同（输出音乐速度、点击密度完全一样），
    #      只有段内测速差了几个百分点（146 vs 160）。听感上就是一段，切两段只是
    #      白白多一处接缝。
    #   2) 倍频层——同一段音乐测速落在 1.5 / 2 / 3 家族的隔壁层（88 报成 176、
    #      106.7 报成 160），相邻段输出速度来回跳（180 → 90 → 180），用户听到的
    #      是「一直在拼接」。
    # 判据都不看速度本身，而是合并后重新测速、看拍网格对位变不变差：合并段的命中
    # 比不低于原来两段里的较大者（留 12% 余量）才合并；明显变差说明是真的变拍，
    # 保留原分段。每合并一次就重扫，直到没有可合并的。
    rejected = set()
    changed = True
    while changed and len(merged) > 1:
        changed = False
        for i in range(len(merged) - 1):
            a, b = merged[i], merged[i + 1]
            if a[2] is None or b[2] is None or a[2].bpm <= 0 or b[2].bpm <= 0:
                continue
            key = (a[0], a[1], b[0], b[1])
            if key in rejected:
                continue
            r = b[2].bpm / a[2].bpm
            cpb_same = abs(float(a[2].plan.clicks_per_beat)
                           - float(b[2].plan.clicks_per_beat)) < 1e-9
            a2_same = abs(a[2].plan.a2 - b[2].plan.a2) < 0.5
            same_out = a2_same and cpb_same
            jitter = same_out and abs(math.log2(r)) <= math.log2(1.0 + SEG_MERGE_RATIO)
            layer = False
            for k in (1.5, 2.0, 3.0):
                if abs(math.log2(r / k)) < 0.045 or abs(math.log2(r * k)) < 0.045:
                    layer = True                  # 约 3% 以内落在节拍层上
                    break
            if not (jitter or same_out or layer):
                continue
            # 三类候选（都不重测合并段，见下）：
            #   jitter  段间测速抖动（146 vs 152，同一个输出方案），并掉只会少一处接缝。
            #   same_out 输出方案相同但速度差得更多（146 vs 160）。**不能只看方案相同**：
            #           121 BPM 用 rate 1.49、146 BPM 用 rate 1.23 都能到 180，并成一段
            #           后 121 那段只会变成 149，那是真的变拍。所以要用「合并后的方案
            #           必须仍然是前一段那个方案」来判——真变拍并起来方案会变。
            #   layer   落在 1.5 / 2 / 3 家族的隔壁节拍层（88 报成 176、107 报成 160），
            #           合并会改变输出速度，同样要求方案不变。
            t0, t1 = a[0], b[1]
            if jitter:
                # 速度差不到 6%、输出方案又完全一样：合并只是少一处接缝，直接串
                # 拍点即可，不必再花一次整段测速（一段 4 分钟的重测要 1~2 秒）。
                beats = np.concatenate([np.asarray(a[2].beats),
                                        np.asarray(b[2].beats) + (b[0] - t0)])
                bpm = 60.0 * len(beats) / max(1e-9, (t1 - t0))
                plan = (choose_stretched_tempo(bpm, target, strict,
                                               max_slowdown, max_speedup)
                        if bpm > 0 else None)
                if plan is None or not (abs(plan.a2 - a[2].plan.a2) < 0.5
                                        and abs(float(plan.clicks_per_beat)
                                                - float(a[2].plan.clicks_per_beat)) < 1e-9):
                    rejected.add(key)
                    continue
            else:
                # 合并前对合并后的整段重新测速：拼接两段各自的拍点等于假设「两段
                # 之间也没有换速度」，而重测才是对「它们本来就是一段」的直接检验。
                bpm, beats = _detect_seg(int(t0 * sr), int(t1 * sr))
                plan = (choose_stretched_tempo(bpm, target, strict,
                                               max_slowdown, max_speedup)
                        if bpm > 0 else None)
                if plan is None:
                    rejected.add(key)
                    continue
                # 合并后的方案必须仍是**前一段**的方案；拿两段之间的 cpb 比是错的
                # （107/cpb2 与 160/cpb1 本来就不同，合并后又回到 cpb2）。
                if not (abs(plan.a2 - a[2].plan.a2) < 0.5
                        and abs(float(plan.clicks_per_beat)
                                - float(a[2].plan.clicks_per_beat)) < 1e-9):
                    rejected.add(key)
                    continue
                # 方案相同还不够：真变拍的两段也可能都到 180（121 用 rate 1.49、
                # 146 用 rate 1.23），并成一段后 121 那段只会变成 149。用「合并后的
                # 拍网格对位不差于原来较好的那段」兜底——真变拍时混合网格的命中比
                # 会明显掉下来。
                sa = _seg_align_score(onset, rms, sr, np.asarray(a[2].beats) + a[0], a[0], a[1])
                sb = _seg_align_score(onset, rms, sr, np.asarray(b[2].beats) + b[0], b[0], b[1])
                sm = _seg_align_score(onset, rms, sr, np.asarray(beats) + t0, t0, t1)
                need = (0.88 if layer else 1.0) * max(sa, sb)
                if sm < need:
                    rejected.add(key)                 # 合并后对位变差 → 真的变拍
                    continue
            merged[i:i + 2] = [(t0, t1,
                                SegmentPlan(t0, t1, bpm, np.asarray(beats), plan))]
            changed = True
            break
    merged = _collapse_same_family(merged, target, max_slowdown, max_speedup,
                                   onset=onset, sr=sr)
    return merged, None


def _family_layer_candidates(measured_bpms, target, max_slowdown, max_speedup,
                             weights=None):
    """家族层候选：长段实测速度的**倍频**层（×2/÷2），按用户口径排序。

    只走倍频格、不走 ×3/÷3 这类整数倍：倍频层与真拍格保持整数关系（点集
    互为子集），每拍点击数能把它们补回来；÷3 层与真拍成 3/2 这类分数关系，
    点击必然错频——正是家族机制要排除的东西（实测「奢香夫人」真值 83，从
    166.23 ÷3 出来的 55.41 层就会让节拍器一半点击落在拍间）。

    排序 = （变速代价小 → 代价并列 ±0.02 八度取层值低）。
      * **代价优先**：整数倍/分数无所谓（cpb 能补回来），音乐不能乱变速——
        「满月」（真值 61）全曲测在 123.98=2×真值，它的整数分之一 61.99 从没被
        任何段测出来过，但代价 0.047 远小于实测层（95.3→0.082、123.98→0.538），
        按代价选层才能让音乐几乎不动。实测值都是真值的 2 倍时，低层就是真值层。
      * **代价并列取低层**：倍频层之间 rate 恒相同（85 与 170 的方案代价一模
        一样），且测量噪声会让"恒相同"变成差千分之一——并列必须给容差，容差
        内取低层（v1.1.0 定则：低层最接近音乐真实拍速），「每拍敲 N 下」的
        文案才与真实拍数一致。
      * **主导实测层保护（2026-10-25）**：代价判据只在层都为真时才有意义——
        「夜间独白」六段全部实测 110.x（真实层），半层 55 从未被测出，代价却
        更小（|log2 1.09|=0.13 < |log2 0.82|=0.29）；选假层的代价是谎言（名义
        +9%、实际把 110 的音乐拉到 120，与点击 180 成 3/2，全程错频）。当
        **时长加权主导的实测层**与最优候选的代价差落在 (0.02, 0.35] 八度
        （超过并列带、又不到「值得为代价抛弃实测层」的程度）时，实测层提到
        最前。满月不回归：其主导实测 124 与 61.99 代价差 0.49 > 0.35，仍按
        代价选 61.99。
    候选是否真能解释音乐，由调用方的命中率闸门逐个验证，这里不做判断。
    """
    out = []
    for v in measured_bpms:
        for k in range(0, 4):
            for L in (v * (2 ** k), v / (2 ** k)):
                if not (DOTTED_BPM_LO <= L <= DOTTED_BPM_HI):
                    continue
                mc = _min_cost_layer(L, target, max_slowdown, max_speedup)
                if mc is None:
                    continue
                out.append((mc[0][0], 0 if k == 0 else 1, L, mc))
    out.sort(key=lambda t: (t[0], t[1], t[2]))
    seen, uniq = set(), []
    for c in out:
        k = round(c[2], 3)
        if k not in seen:
            seen.add(k)
            uniq.append(c)
    # 代价并列（相差 ≤0.02 个八度 ≈ 1.4% 拉伸差）时取**最低层**——家族内低层
    # 最接近音乐真实拍速（v1.1.0 定则）。浮点代价并列必须给容差：「奢香夫人」
    # 实测 83.02 与 166.23 并存，两个层的 rate 只差千分之一（166.23≠2×83.02 的
    # 测量噪声），按裸代价排会把 83 的歌报成 166。
    if uniq:
        cmin = uniq[0][0]
        band = sorted((c for c in uniq if c[0] <= cmin + 0.02),
                      key=lambda t: t[2])
        rest = [c for c in uniq if c[0] > cmin + 0.02]
        # 主导实测层保护：见 docstring。weights 与 measured_bpms 对齐（段时长）。
        guard = _dominant_measured_guard(out, measured_bpms, weights, cmin)
        if guard is not None:
            return [guard] + [c for c in band if c is not guard] + rest
        return band + rest
    return uniq


def _parity_ratio(onset, sr: int, bpm: float, t0: float, t1: float,
                  nphase: int = 64, hop: int = 256):
    """bpm 网格上「偶数拍 vs 奇数拍」的能量均值比（min/max，相位取总能量最大者）。

    用途：判断 bpm 是不是**真实拍速的 2 倍**。若每两拍一个重音（强弱交替），
    偶奇比明显小于 1，说明真拍只有一半、把 bpm 减半是有依据的；若每拍一样强
    （比值 ≈1），网格上不存在 2 拍分组，减半等于凭空造出半拍。

    实测（2026-10-27）：真拍速是 bpm 一半的曲子 0.54~0.90；「地图」实测 122.05
    而每拍等强，比值 0.9978——它就是被漏掉、减半成 61 的。
    """
    if onset is None or len(onset) < 64 or bpm <= 0:
        return None
    fps = sr / hop
    P = 60.0 / bpm
    omax = _dilate_max(onset, 2)
    dur = t1 - t0
    if dur <= 4.0 * P:
        return None
    best = (-1.0, None)
    for k in range(nphase):
        ph = t0 + k / nphase * P
        k0 = int(np.ceil((t0 - ph) / P - 1e-9))
        k1 = int(np.floor((t1 - ph) / P + 1e-9))
        ks = np.arange(k0, k1 + 1)
        f = np.round((ph + ks * P) * fps).astype(int)
        ok = (f >= 0) & (f < len(omax))
        if int(ok.sum()) < 24:
            continue
        vals, kk = omax[f[ok]], ks[ok]
        even, odd = vals[kk % 2 == 0], vals[kk % 2 == 1]
        if len(even) < 8 or len(odd) < 8:
            continue
        tot = float(vals.mean())
        if tot > best[0]:
            hi = max(float(even.mean()), float(odd.mean()))
            if hi <= 0:
                continue
            best = (tot, min(float(even.mean()), float(odd.mean())) / hi)
    return best[1]


def _octave_err(layer: float, dom: float) -> float:
    """layer 与 dom 相差几个八度（取最近的整数八度，只算 -2..+2）的误差。

    0 = 同层，1 = 高八度，-1 = 低八度。1/3、2/3、3/2 这类关系会给出很大的
    误差——它们正是「点击与真拍错频」的来源（每真实拍敲 2/3 或 3/2 下）。
    """
    if not dom or dom <= 0 or layer <= 0:
        return 0.0
    v = math.log2(layer / dom)
    return min(abs(v - k) for k in (-2, -1, 0, 1, 2))


def _may_halve(onset, sr: int, L: float, dom: float, t0: float, t1: float):
    """层 L 是不是「把实测速度 dom 减半」得来的；是的话要求有 2 拍分组的证据。

    返回 True = 允许用这个层；False = 减半缺乏依据，应换下一个候选。
    非减半层（L 与 dom 同量级或更高）一律允许。
    """
    if not dom or dom <= 0 or L <= 0:
        return True
    if abs(L / dom - 0.5) > 0.06:
        return True                     # 不是减半，与本判据无关
    par = _parity_ratio(onset, sr, dom, t0, t1)
    if par is None:
        return True                     # 测不出来就别拦
    return par < HALVE_MIN_PARITY


def _dominant_cluster(measured_bpms, weights):
    """时长加权占时最多的实测速度簇的代表速度（±5% 合并）。无权重返回 None。"""
    if weights is None or len(weights) != len(measured_bpms):
        return None
    pairs = sorted(zip(measured_bpms, weights))
    clusters = []      # [ [总时长, 代表速度] ]
    for v, w in pairs:
        if clusters and abs(v / clusters[-1][1] - 1.0) <= 0.05:
            clusters[-1][0] += w
        else:
            clusters.append([w, v])
    if not clusters:
        return None
    return max(clusters)[1]


def _dominant_measured_guard(cands, measured_bpms, weights, cmin, band=0.35):
    """主导实测层保护：返回应提到最前的同族候选，不适用时返回 None。

    主导实测层 = 时长加权后占时最多的实测速度簇（±5% 合并；无 weights 时
    不启用——平权会把「满月」少数段的 95.3 当主导而错误触发）。该层的
    候选代价与全局最优差在 (0.02, band] 八度时才保护：≤0.02 属于代价并列
    （已有并列带规则），> band 说明代价收益大到值得偏离实测层（满月 0.49）。

    **同族含倍频层（2026-10-27 修）**：主导实测层自己常常**没有可行候选**——
    「艳红」12 段全测 130.0，而 target=180、max_speedup 限制下 130 的
    cpb=1/2/3/6 方案 rate 全部超界，于是 `c[1] == 0` 一个都匹配不上，保护
    静默失效。此时最优代价落到末段离群值 123.65 ÷2 = 61.82（代价 0.0432，
    远小于真半速层 65.0 的 0.1154），音乐被送到 62.9 BPM 而节拍器按 60
    敲——整曲 4.6% 恒差，δ* 在 ±160ms 间乱跳，听感「全程没踩上拍」。
    现在把主导实测值的**任一倍频层**都视为同族，并优先取倍频误差最小、
    其次代价最小、再次层值最小的那个，保护才真正兜住。
    """
    dom_v = _dominant_cluster(measured_bpms, weights)
    if dom_v is None:
        return None
    best = None
    for c in cands:
        r = c[2] / dom_v
        oct_err = None
        for j in range(0, 4):
            for ratio in (2.0 ** j, 1.0 / (2.0 ** j)):
                e = abs(r / ratio - 1.0)
                if e <= 0.05 and (oct_err is None or e < oct_err):
                    oct_err = e
        if oct_err is None:
            continue
        key = (oct_err, c[0], c[2])
        if best is None or key < best[0]:
            best = (key, c)
    if best is None:
        return None
    c = best[1]
    if 0.02 < c[0] - cmin <= band:
        return c
    return None


def _long_segments(merged, short_s: float):
    """长段 [(t0, t1, sp)]：时长 ≥ short_s 且测速有效（bpm > 0）。

    短段的测速不可信（「我不用问」中段 126.2、「满月」前奏 141.95 都来自几秒
    的段），家族判定与选层都不参与，只由命中率闸门间接验证它们能否跟上
    长段的层。
    """
    return [(t0, t1, sp) for t0, t1, sp in merged
            if (t1 - t0) >= short_s and sp is not None and sp.bpm > 0]


def _layer_explains_all(onset, sr: int, L: float, spans):
    """命中率闸门：层 L 的规整网格在**每一个**长段上都命中 ≥ SEG_HIT_MIN。

    逐段而不是全曲平均：90|120 的 60 层全曲均值能凑到 0.75，但 90 段自身
    只有 0.5——塌缩过去节拍器就和那段音乐错频。真变拍必有至少一段不达标，
    层抖动与全曲翻倍则每段都能过。

    （2026-10-25 曾试过在此加「逐拍跟踪锁定率」第二道判据，实测会把前奏
    自由节奏的恒速歌误判为变拍段——「夏夜晚风」（55 BPM 单速、前奏散板）
    被误切两段，用户确认其为单速后已回滚。勿重试。）
    """
    for s0, s1 in spans:
        grid, hit = _phase_grid(onset, sr, L, s0, s1)
        if grid is None or hit < SEG_HIT_MIN:
            return False
    return True


def _dominant_cover(bpms, wts, tol: float = 0.05) -> float:
    """长段里最大一簇（相对 ±tol）的时长占比。

    塌缩的前提是「整首就一个速度」：只有当一个速度能解释**大部分时长**时，
    各段之间测速的差异才可能是层级抖动/测速误差。真有多个速度时（万有引力
    128/138/110/102），最大簇只占 30%~40%。

    实测（2026-10-28，全库 44 首，±5%）：单速曲 0.60~1.00（多数 ≥0.94）；
    真变拍曲「万有引力」0.37、「在这里停一下」0.35、「天堂与泥土」0.53、
    「卧室」0.52。阈值 0.60 只让这几首保留分段，其余 40 首行为不变。
    """
    tot = float(sum(wts)) or 1.0
    best = 0.0
    for b in bpms:
        if b <= 0:
            continue
        s = sum(w for v, w in zip(bpms, wts) if v > 0 and abs(v / b - 1.0) <= tol)
        if s / tot > best:
            best = s / tot
    return best


def _is_layer_ratio(a: float, b: float, nmax: int = LAYER_RATIO_MAX,
                    tol: float = LAYER_RATIO_TOL) -> bool:
    """两个实测速度是否只差一个**层**：比值接近 n/m（n、m ≤ nmax）。

    音乐里同一段被数成不同层，只可能是八度（2、1/2）或附点层（3/2、2/3、
    4/3、3/4）这类**简单有理比**。用「小分子小分母」而不是任意整数比，
    才能把「层抖动」与「真变拍」分开：
      · 120 / 90 = 4/3 —— 层抖动，该合并；
      · 128 / 110.5 = 1.159、138 / 128 = 1.078、110.5 / 102 = 1.083 ——
        都不是小分子小分母（最近的是 7/6、14/13、13/12），是四个真速度。
    """
    if a <= 0 or b <= 0:
        return False
    x = max(a, b) / min(a, b)
    for n in range(1, nmax + 1):
        for m in range(1, nmax + 1):
            r = n / m
            if abs(x - r) <= tol * r:
                return True
    return False


def _all_layer_ratios(bpms) -> bool:
    """所有长段速度是否两两只差一个层（见 `_is_layer_ratio`）。"""
    vals = [float(v) for v in bpms if v and v > 0]
    for i in range(len(vals)):
        for j in range(i + 1, len(vals)):
            if not _is_layer_ratio(vals[i], vals[j]):
                return False
    return True


def _refine_layer_speed(onset, sr: int, L: float, t0: float, t1: float,
                        tol: float = LAYER_REFINE_TOL):
    """把家族选出的层速度在 onset 包络上精修（相位自由，只定周期）。

    家族候选层是从若干**带噪**实测段速里按「变速代价并列取最低」选出来的，
    等价于取一组实测值的最小值——系统性偏低。实测「乏善可陈」：真值 95.996，
    选出的层 95.9236（偏低 0.075%）。这一偏差会顺着 rate = a2/L 进入变速：
    音乐实际落到 90.068 而非 90.000，而节拍器周期恒为 60/a2 = 666.667ms，
    两者每拍差 0.5ms、465 拍累计 240ms——听感就是「前半段还凑合、后半段
    越来越错拍」（用户 2026-10-26 报的就是这个）。

    所以选定层后必须回到 onset 上把周期精修回来。

    半径 ±2%（2026-10-28 由 ±0.5% 放宽）：家族层是「一组实测值里最低那个」再吸附
    到整数家族，最坏情况下与真值差多少并不由 ±0.5% 决定。实测「满月」：真值
    61.99875，候选层里 `low` 取到了 95.2692（一个 16 秒短段的实测值），
    `_snap_family(95.2692, base=129.115)` 落到 61.56535（偏低 0.70%），
    ±0.5% 半径根本够不到真值，只修到 61.837（仍偏低 0.26%）→ rate=0.9703 把音乐
    送到 60.159 BPM，而节拍器按名义 60.000 敲，每拍差 2.65ms、270 拍累计 715ms，
    听感是「整首没踩准」（用户 2026-10-28 报的就是这个，尤其没人声的伴奏段落）。

    为什么 ±2% 不会跳层：家族内相邻层的最小倍距是 4/3（log2≈0.415），±2% 连
    它的二十分之一都不到；实测八首（满月/万有引力/乏善可陈/艳红/地图/夏夜晚风/
    奢香夫人/红尘客栈）放宽后只有满月变化（61.837→62.004，误差 0.26%→0.009%），
    其余七首误差不变或更小。

    拟合不可靠时原值返回。
    """
    P0 = 60.0 / L
    if onset is None or len(onset) < 8 or not (0.2 <= P0 <= 2.5):
        return float(L)
    fps = sr / 256.0
    omax = _dilate_max(onset, 2)
    best = [P0, 0.0, _grid_score(onset, fps, P0, 0.0, t0, t1, onset_max=omax)]
    for P in np.linspace(P0 * (1.0 - tol), P0 * (1.0 + tol), 121):
        for ph in np.linspace(0.0, P, 49, endpoint=False):
            sc = _grid_score(onset, fps, P, ph, t0, t1, onset_max=omax)
            if sc > best[2]:
                best = [P, ph, sc]
    P1, ph1, _ = best
    for P in np.linspace(P1 * 0.9996, P1 * 1.0004, 41):
        for ph in np.linspace(ph1 - 0.02 * P1, ph1 + 0.02 * P1, 11):
            sc = _grid_score(onset, fps, P, ph, t0, t1, onset_max=omax)
            if sc > best[2]:
                best = [P, ph, sc]
    if best[2] <= 0:
        return float(L)
    return float(60.0 / best[0])


def _collapse_same_family(merged, target: float, max_slowdown: float,
                          max_speedup: float, onset=None, sr: int = 0,
                          mel=None):
    """各段速度同属一个家族（互为整数倍/整数分之一）时，塌缩成一段、全曲
    统一拉伸比，并按变速代价最小选层。

    为什么必须做：测速器会把同一段音乐的不同部分数到不同节拍层上——层抖动
    （120/90/120/90 来回跳）、短段离谱值（「我不用问」中段 9.7 秒测成
    95×4/3=126.2）、甚至全曲翻倍（「满月」真值 61 全曲测成 124）。各段各按
    自己的层选每拍点击数，拉伸比就会段间跳变（实测前奏 −5.5%、主体 +46.6%），
    恒速曲听感「中途加速」。塌缩成一层后全曲一个拉伸比，跳速度消失；倍频差
    由每拍点击数补回（测 90、目标 180、cpb=2，落点仍准），所以家族内选层只看
    **变速代价最小**。

    流程与三道闸门（对应验收：②单速曲不切多段 ③多速曲必分段）：
      1. 家族覆盖（COLLAPSE_FAMILY_COVER）：一个 base 必须吸附 ≥98% 的长段
         时长。「YYZ」前奏 102.8 与主体 140.65 相差 1.366 倍、超 SNAP_MAX，
         最好的 base 覆盖只有 86% → 保留分段。
      2. 候选层只走**倍频格**（长段实测层 ×2/÷2，见 _family_layer_candidates）。
      3. 命中率闸门逐长段验证（_layer_explains_all）：真变拍 90|120|90 的任何
         倍频层都有段不达标 → 保留分段。
    塌缩后拍网格按选定层重建（间距恒 60/L）——bpm 与拍距不一致时
    click_schedule 会按错的拍距敲（v1.1.1 实测点击密度 261/min）。
    """
    if len(merged) < 3:
        return merged
    # plan 为 None 只是「这一段自己算不出可行方案」，它的拍点和速度仍然可用：
    # 塌缩成一层后方案由所选层重算，所以这里只要求 bpm 与拍点有效。
    if any(sp is None or sp.bpm <= 0 or len(np.asarray(sp.beats)) < 4
           for _t0, _t1, sp in merged):
        return merged
    dur_all = merged[-1][1] - merged[0][0]
    short_s = min(SEG_SHAPE_MIN_S, max(2.0, SEG_SHAPE_FRAC * dur_all))
    longs = _long_segments(merged, short_s)
    if len(longs) < 2:
        return merged                      # 长段不足 2 个，没有可信速度可对照
    t0, t1 = merged[0][0], merged[-1][1]

    # ① 家族覆盖：没有 base 能吸附几乎全部长段 → 存在真变拍，保留分段。
    long_pairs = [(sp.bpm, s1 - s0) for s0, s1, sp in longs]
    base, _layer, _cpb, _a2, _rate = _common_family(
        long_pairs, target, max_slowdown, max_speedup,
        min_frac=COLLAPSE_FAMILY_COVER)
    if base is None:
        return merged

    # ①b 层抖动还是真变拍？两条判据任一成立才允许塌缩：
    #   · 主簇覆盖 ≥ COLLAPSE_DOM_COVER：一个速度能解释大部分时长，其余段是
    #     少数派的层级抖动（「满月」16 秒的 95.27 对 246 秒的 124）。
    #   · 层比两两都是小分子小分母：同一段被数成不同层只会是八度（2、1/2）
    #     或附点层（3/2、2/3、4/3、3/4）；120|90|120|90 这种各占一半的层抖动
    #     主簇覆盖只有 0.50，但比值恰好 4/3，仍该合并。
    #
    # 为什么不能只用家族覆盖：SNAP_MAX=±35% 太宽，四个相差 1.36 倍的速度也
    # 能被算成「同一整数家族」（万有引力 128/138/110/102 全吸进 base=110.487，
    # 覆盖 100%）。为什么不能只用其中一条：见上面两条各自的失效场景。
    _long_bpms = [sp.bpm for _s0, _s1, sp in longs]
    _long_wts = [s1 - s0 for s0, s1, _sp in longs]
    if (_dominant_cover(_long_bpms, _long_wts) < COLLAPSE_DOM_COVER
            and not _all_layer_ratios(_long_bpms)):
        return merged

    if onset is None or sr <= 0:
        # 无包络（单元测试直调）：命中率无从验证，取家族内最低实测层、
        # 规整网格兜底。
        L = min(sp.bpm for _s0, _s1, sp in longs)
        mc = _min_cost_layer(L, target, max_slowdown, max_speedup)
        if mc is None:
            return merged
        grid = np.arange(0.0, t1 - t0, 60.0 / L)
        if len(grid) < 4:
            return merged
        plan = StretchPlan(float(mc[2]), float(mc[3]), float(target),
                           float(mc[1]), 0)
        return [(t0, t1, SegmentPlan(t0, t1, float(L), grid, plan))]

    # ② + ③ 候选层逐个过命中率闸门，第一个能解释所有长段的获胜。
    spans = [(s0, s1) for s0, s1, _sp in longs]
    long_bpms = [sp.bpm for _s0, _s1, sp in longs]
    long_wts = [s1 - s0 for s0, s1, _sp in longs]
    # 减半闸门用的参照速度取**最长长段**的实测值：_dominant_cluster 取的是簇内
    # 最小值（簇按 ±5% 归并、代表值取簇首），实测「夏夜晚风」给 108.899，而真正
    # 占满全曲的那段是 110.041——用前者算奇偶比会把 0.795 算成 0.970，误判成
    # 「没有两拍分组」。最长段的实测速度没有这个偏差。
    i_long = int(np.argmax(long_wts))
    ref_v = float(long_bpms[i_long])
    dom_v = _dominant_cluster(long_bpms, long_wts)
    cands = _family_layer_candidates(long_bpms, target, max_slowdown, max_speedup,
                                     weights=long_wts)
    # 减半闸门：半速网格是实测网格的**子集**，命中率闸门对它毫无分辨力（两者
    # 都能「解释」音乐），所以「把实测速度减半」必须另有证据——音乐里真有每两
    # 拍一个重音的强弱交替。实测「地图」：全曲 9 段都测在 122.0，而 122 的网格
    # 上每拍等强（奇偶比 0.96），减半到 61 之后音乐被送到 120、节拍器按 60 敲
    # 3 下/拍，正好落在拍与拍之间（用户 2026-10-27 报的「一拍打 3 下」）。
    # 只在**减半被拒**时收窄到八度同族：八度同族能保证「点击与真拍不错频」，
    # 但会把本该减半的曲子抬回实测值（艳红 65→130 等），那些曲子已按奇偶比
    # 判定「减半有依据」，不该被这条改掉。
    if ref_v > 0:
        kept = []
        rejected_halve = False
        for c in cands:
            if abs(c[2] / ref_v - 0.5) <= 0.06 and not _may_halve(
                    onset, sr, c[2], ref_v, t0, t1):
                rejected_halve = True
                continue
            kept.append(c)
        if rejected_halve:
            same_oct = [c for c in kept if _octave_err(c[2], ref_v) <= 0.05]
            if same_oct:
                kept = same_oct
        cands = kept
    for _cost, _mflag, L, mc in cands:
        if not _layer_explains_all(onset, sr, L, spans):
            continue
        # 候选层来自「代价并列取最低」，等价于取实测值的最小值——系统性偏低，
        # 会以 rate=a2/L 的形式把音乐送到偏离目标的 BPM 上（节拍器周期恒为
        # 60/a2，于是每拍累积固定偏差，后半段越来越错拍）。先精修层速度，
        # 再按精修后的层重做闸门与网格；半径 ±2% 远小于层间距（4/3），翻不了层。
        L_ref = _refine_layer_speed(onset, sr, L, t0, t1)
        if abs(L_ref - L) > 1e-9:
            if not _layer_explains_all(onset, sr, L_ref, spans):
                L_ref = L                       # 精修后解释不了 → 退回原层
            else:
                mc_ref = _min_cost_layer(L_ref, target, max_slowdown, max_speedup)
                if mc_ref is not None:
                    L, mc = L_ref, mc_ref
        grid, _hit = _phase_grid(onset, sr, L, t0, t1)
        if grid is None or len(grid) < 4:
            continue
        P_L = 60.0 / L
        ph_grid = t0 + float(grid[0]) % P_L   # 骨架相位（绝对轴）
        if mel is not None:
            # 底鼓带相位裁决（同 detect_tempo）：八分低音盖过底鼓时网格会
            # 半拍反相；翻转后骨架相位平移半拍（段相对轴）。
            ph_new, flipped = _kick_phase_align(mel, sr, P_L, ph_grid, t0, t1)
            if flipped:
                ph_grid = ph_new
                k0 = int(np.ceil((t0 - ph_new) / P_L))
                k1 = int(np.floor((t1 - ph_new) / P_L))
                grid = ph_new - t0 + np.arange(k0, k1 + 1) * P_L
                if len(grid) < 4:
                    continue
        # 贴合化：恒速网格只在平均速度上与音乐一致，速度起伏会让点击累积
        # 漂移；用 onset 重锁 + 稳健拟合把拍位贴回音乐（拍数不变、密度不变）。
        grid2 = _refit_grid(onset, sr, P_L, ph_grid, t0, t1)
        if grid2 is not None and len(grid2) >= 4:
            grid = grid2 - t0
        plan = StretchPlan(float(mc[2]), float(mc[3]), float(target),
                           float(mc[1]), 0)
        # 检测速度记成所选层的速度：报告与拍点间距严格自洽（60/L）。
        return [(t0, t1, SegmentPlan(t0, t1, float(L), grid, plan))]
    return merged                          # 家族里没有任何层能解释 → 保留分段


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


def _kick_phase_align(mel, sr: int, P: float, phase: float, t0: float, t1: float):
    """底鼓带（<120Hz）相位裁决：网格若锁在低音八分位上，平移半拍回到真拍。

    为什么需要：全带 onset 通量会被**低音吉他的八分音符**盖过底鼓——「乏善可陈」
    的八分低音让梳状拟合把网格锁在八分位上（正拍位只剩 7.5% 的弱势），节拍器
    整体半拍反相、听感全程错拍。纯底鼓频段（<120Hz，吉他够不着）在真拍位上
    能量必然更高，用它裁决：反拍位（phase+P/2）的底鼓 onset 超过当前相位
    KICK_FLIP_RATIO 倍时，把相位平移半拍。

    **注意**：本函数在 `detect_tempo` 里也用来在 bpm 与 bpm/2 之间定倍频
    （「夏夜晚风」靠它才报 55 而不是 110），那里的半拍平移是**改变测速结论**，
    不能用「全带能量」去否决（全带在八分位上的能量本来就更高，正是要修的病）。
    输出端（`_output_beats`）的同类裁决另有能量否决，见该函数。

    phase 为**绝对时间**轴（整曲从 0 起）；验证窗口 [t0, t1]（整曲路径传
    0/duration，塌缩路径传段的绝对起止）。

    返回 (相位, 是否翻转)。翻转后调用方须用规整网格重建拍列（原检测拍在旧
    相位上，对半拍新相位是半整数，多项式拟合会失效）。
    """
    import librosa
    freqs = librosa.mel_frequencies(n_mels=mel.shape[0], fmin=0, fmax=0.5 * sr)
    sub = mel[freqs <= 120]
    if sub.shape[0] == 0:
        return phase, False
    e = np.asarray(librosa.onset.onset_strength(S=librosa.power_to_db(sub),
                                                sr=sr, hop_length=256),
                   dtype=np.float64)
    mx = _dilate_max(e, 2)
    fps = sr / 256

    def mean_at(off):
        k0 = int(np.ceil((t0 - off) / P))
        k1 = int(np.floor((t1 - off) / P))
        if k1 <= k0:
            return 0.0
        f = np.round((off + np.arange(k0, k1 + 1) * P) * fps).astype(int)
        f = f[(f >= 0) & (f < len(mx))]
        if len(f) < 4:
            return 0.0
        return float(np.mean(mx[f]))

    a, b = mean_at(phase), mean_at((phase + P / 2) % P)
    if a > 0 and b > a * KICK_FLIP_RATIO:
        return phase + P / 2, True
    return phase, False


def _no_tempo_msg(lang: str | None = None) -> str:
    """检测失败的提示：随界面语言出中/英（英文界面不应漏中文）。"""
    return tr("无法检测速度（音频太短或太安静？），请用 --bpm 手动指定",
              "Could not detect tempo (audio too short or too quiet?). "
              "Please specify it manually with --bpm.",
              lang)


def detect_tempo(y_mono, sr: int, lang: str | None = None, mel=None):
    """检测速度，返回 (bpm, beat_times)。

    bpm 为精修后的原值（**不再归一化**：测到 60 BPM 就报 60 BPM），
    beat_times 为拉伸前的时间轴节拍位置（稳定速度歌曲为精修规整网格）。
    lang 只影响检测失败时抛出的 RuntimeError 文案。

    mel：可选的预计算 mel 谱（hop=256、fmax=0.5*sr、对 y_mono 的**同一个切片**算的）。
    分段时要对同一首曲子的十几段反复测速，每段各算一次 mel 是纯重复劳动；
    调用方按帧切好传进来即可跳过这一步（最长的一段能省 0.5 秒）。
    """
    import librosa

    # 一次 mel 谱派生两档 hop 的 onset：hop=512 的 STFT 帧正好是 hop=256 的偶数帧，
    # 所以 S[:, ::2] 与「直接按 hop=512 再算一遍 mel」逐位相同（实测 max|diff|=0），
    # 省掉一次重复的 STFT/mel（长曲上这一步占测速的三分之一左右）。
    if mel is None:
        mel = np.abs(librosa.feature.melspectrogram(y=y_mono, sr=sr, hop_length=256,
                                                    fmax=0.5 * sr))
    onset = librosa.onset.onset_strength(S=librosa.power_to_db(mel[:, ::2]),
                                         sr=sr, hop_length=512)
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
    onset_fine = librosa.onset.onset_strength(S=librosa.power_to_db(mel),
                                              sr=sr, hop_length=256)
    P, phase = _comb_refine(onset_fine, sr, beat_times)
    if P is None:
        P = float(np.median(np.diff(beat_times)))
        phase = float(beat_times[0])
    else:
        # 附点档修正：检测档的 1/3、2/3 位上有拍级能量（真拍在 3/4 或 3/2 倍
        # 档上，120 BPM 先验把档位拽错的典型形态）时改判档位。修正后的拍列
        # 与 beat_track 的拍不再对齐，按精修网格重建规整拍列——能触发修正的
        # 歌速度稳定，规整网格即真实拍。
        P2, phase2 = _dotted_level_fix(onset_fine, sr, P, phase)
        if (P2, phase2) != (P, phase):
            P, phase = P2, phase2
            # 改档后旧拍编号失效。优先 onset 重锁 + 拟合重建贴合网格
            # （恒速网格不跟真实拍，速度起伏会让点击累积漂移）；失败退恒速。
            grid2 = _refit_grid(onset_fine, sr, P, phase, 0.0, duration)
            if grid2 is not None:
                beat_times = grid2
            else:
                k_end = int(np.floor((duration - phase) / P + 1e-9))
                beat_times = phase + np.arange(0, k_end + 1) * P
        # 底鼓带相位裁决：八分低音盖过底鼓时网格会锁到八分位（半拍反相），
        # 按纯底鼓频段裁决并平移。翻转后原检测拍对新相位是半整数、平滑拟合
        # 失效，同样用 onset 重锁重建贴合网格。
        phase, flipped = _kick_phase_align(mel, sr, P, phase, 0.0, duration)
        if flipped:
            grid2 = _refit_grid(onset_fine, sr, P, phase, 0.0, duration)
            if grid2 is not None:
                beat_times = grid2
            else:
                k_end = int(np.floor((duration - phase) / P + 1e-9))
                beat_times = phase + np.arange(0, k_end + 1) * P
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


def _best_output_phase(onset, sr: int, P: float, duration: float,
                       clicks_per_beat: float | None = None,
                       nphase: int = 144, hop: int = 256):
    """输出节拍器格的相位：直接最大化**点击格**与 onset 包络的对齐能量。

    这里不能用 `_phase_grid` 选相位。`_phase_grid` 的目标函数是**命中率**
    （格点在阈值以上的比例），在真实混音上几乎每个相位都能到 0.98+（实测
    乏善可陈整曲 64 个相位里最大命中率 0.9892、只差 0.02 的就有 64 个），
    于是它等于在 0.98 的平台上随便挑一个——相位实际上由浮点顺序决定。
    实测 v1.2.9 成品：点击列车整体比音乐起音**提前 150.7ms**（≈半格），
    听感就是「整首歌都没踩上拍」。

    改用能量目标（格点处 onset 幅值的均值）后，同一段音乐的最优相位稳定
    落在起音上：v1.2.9 上 `_phase_grid` 给 531.25ms（能量 0.6951），能量最
    优给 10.42ms（能量 1.1034，高出 59%），且 0/10.4/20.8ms 三个相邻点在
    物理上就是同一个起音的抖动范围内，说明地形是单峰的、可复现的。

    打分周期用**点击周期** P/cpb 而不是拍周期 P：cpb=2 时点击落在拍与反拍
    上，若按拍周期择优，「掐在拍上」和「掐在反拍上」得分相同、二义不决；
    按点击周期打分则两者的点击集合完全相同，天然没有二义。
    """
    env = np.asarray(onset, dtype=np.float64)
    if env.size < 8 or P <= 0 or duration <= 0:
        return None
    fps = sr / hop
    cpb = float(clicks_per_beat) if clicks_per_beat else 1.0
    if cpb <= 0:
        cpb = 1.0
    Pc = P / cpb
    if Pc < 0.05:
        return None
    omax = _dilate_max(env, 2)
    best = (-1.0, 0.0)
    for k in range(nphase):
        ph = k / nphase * Pc
        e = _grid_score(env, fps, Pc, ph, 0.0, duration, onset_max=omax)
        if e > best[0]:
            best = (e, ph)
    if best[0] < 0:
        return None
    # 细搜：在粗搜最优点 ±1 个粗步长内再走两轮，分辨率到 Pc/2300
    step = Pc / nphase
    ph0 = best[1]
    for _ in range(2):
        cand = (-1.0, ph0)
        for j in range(-12, 13):
            ph = ph0 + j / 12.0 * step
            e = _grid_score(env, fps, Pc, ph, 0.0, duration, onset_max=omax)
            if e > cand[0]:
                cand = (e, ph)
        step /= 12.0
        ph0 = cand[1]
    # 槽位归一：点击格周期 Pc = P/cpb 上，ph0 与 ph0 + j*Pc (j=1..cpb-1) 给出
    # **完全相同的点击集合**，能量也完全相同——对 cpb 个槽位彼此等价。但
    # `_output_beats` 把返回值当作**拍**相位用（`phase0 + k*P` 铺格，再在
    # ±0.15P 窗内量 onset 质心），槽位选错就会让量质心的窗口落在拍与拍之间。
    # 合成脉冲曲上实测：cpb=3 时抽到 0.3293 而真拍在 0.008，窗内没有 onset
    # → n_valid=0 → 整首退回网格插值路径（拍速对、相位丢）。所以还要在 cpb
    # 个等价槽里按**拍周期 P** 的能量挑出「拍槽」。
    if cpb >= 2.0 and abs(cpb - round(cpb)) < 1e-9:
        nslot = int(round(cpb))
        best_slot = (-1.0, ph0)
        for j in range(nslot):
            ph = ph0 + j * Pc
            e = _grid_score(env, fps, P, ph, 0.0, duration, onset_max=omax)
            if e > best_slot[0]:
                best_slot = (e, ph)
        ph0 = best_slot[1]
    return float(ph0 % P)


def _output_beats(y2, sr: int, a2: float, duration: float,
                  clicks_per_beat: float | None = None):
    """**恒速**节拍器格在变速后音频上的相位对齐（用户 2026-10-25 口径：
    节拍器必须是目标 BPM、间隔恒定，只是用变好速的音乐对齐）。

    返回输出轴拍时刻列表（供 click_schedule 以 rate=1 铺点击）；对齐不可
    靠时返回 None，调用方退回网格插值路径。

    做法（恒速格 + 慢速相位跟随）：
      1. 拍格恒定间隔 P=60/a2（点击密度精确等于目标 BPM，间隔恒定，
         绝不出现附点/切分/多敲）；
      2. 粗相位由**点击格能量择优**给出（`_best_output_phase`；只有
         「每拍 1 敲」才再过底鼓带半拍裁决）；
      3. 每隔 OUTBEATS_FIX_EVERY 拍，用该块拍位 ±0.15P 窗内的 onset
         加权质心校一次相位，修正量限幅 OUTBEATS_MAX_STEP、并在块间
         线性摊开——节拍器微观上仍是恒速（间隔偏差 <2ms），但格子的
         相位缓慢贴住音乐，层值残余误差（~0.1%）不会累积成漂移。
         **死区（OUTBEATS_DEADBAND）**：|偏差| < 2%P 时**不积分**。块质心
         相对网格有一个系统性常值偏置（起音重心比拍点略早，实测 −3~−8ms），
         累积式积分器会把它一路积成无界漂移——「艳红」实测 dev 均值
         −7.56ms、50 块积分到 **−189ms**，整曲偏半个点击格（用户听感
         「全程没踩上拍」，20s 窗 δ* 在 ±160ms 间乱跳）。真实段落漂移
         （间奏后失锁）幅度在 0.1P 以上，远大于死区，仍能正常拉回。
    （此前试过「逐拍跟踪音乐瞬态」的版本——节拍器跟着音乐的微观速度
    波动走，间隔不均、丢拍处补漏拆分会多敲，听感成附点/切分，已废弃。）
    """
    import librosa
    ym = np.asarray(y2, dtype=np.float32)
    if ym.ndim > 1:
        ym = ym.mean(axis=0)
    onset = np.asarray(librosa.onset.onset_strength(y=ym, sr=sr, hop_length=256),
                       dtype=np.float64)
    fps = sr / 256
    n_beat = int(np.floor(duration / (60.0 / a2))) + 2
    if n_beat < 8:
        return None
    P = 60.0 / a2
    phase0 = _best_output_phase(onset, sr, P, duration,
                                clicks_per_beat=clicks_per_beat)
    if phase0 is None:
        return None
    # 底鼓带半拍裁决只对「每拍 1 敲」有意义。cpb>=2 时点击集合恒为
    # {phase + m*P/cpb}，真正的拍位已由上面的点击格能量择优定好；此时再
    # 平移 P/2 等于把整串点击挪 cpb/2 个格：cpb 偶数时是整数格（等于没动），
    # cpb 奇数/分数时是**半格**，会把每一敲都推到两个格点中间——正是
    # 「夏夜晚风」（55→60、cpb=3）整首错半格的原因（实测 δ* = −Pc/2）。
    if clicks_per_beat is None or abs(float(clicks_per_beat) - 1.0) < 1e-9:
        mel2 = np.abs(librosa.feature.melspectrogram(y=ym, sr=sr, hop_length=256,
                                                     fmax=0.5 * sr))
        ph_flip, _fl = _kick_phase_align(mel2, sr, P, phase0, 0.0, duration)
        if _fl:
            # **全带能量否决（2026-10-27 修）**：底鼓带并不总是可信（双踩、
            # 切分、超低音合成器都会让 <120Hz 在反拍更响）。这里的用途是让
            # 点击贴住音乐，所以「翻转后全带能量反而更低」就是明确反证——
            # 实测 YYZ 段2 翻转后全带掉 29.9%（1.4844→1.0480），整段偏半个
            # 点击格（δ* = −164.81ms）。注意 `detect_tempo` 里不能用这条：
            # 那里的半拍平移是在定 bpm 倍频（夏夜晚风靠它报 55 而非 110），
            # 而全带在八分位能量更高正是要修的病。
            omax2 = _dilate_max(onset, 2)
            e_now = _grid_score(onset, fps, P, phase0, 0.0, duration,
                                onset_max=omax2)
            e_flip = _grid_score(onset, fps, P, ph_flip % P, 0.0, duration,
                                 onset_max=omax2)
            if e_flip < e_now:
                ph_flip = phase0
        phase0 = ph_flip
    # 块质心校准：块 b 覆盖拍号 [b*E, (b+1)*E)，质心 = 拍位（含当前曲线
    # 修正）±0.15P 窗内 onset 幅值加权位置的平均偏移。**比例控制**：每块
    # 只修残余偏差的一半、上限 OUTBEATS_MAX_STEP——固定小限速在「间奏段
    # 无锚（格子按恒速走、音乐按真速走，偏差在间奏里累积到 ~1/4 拍）」
    # 之后要几十秒才爬得回来（实测乏善可陈间奏后 46-77s 偏 109→142ms）；
    # 比例控制让恢复时间与偏差成正比（140ms 约 2-3 块 ≈15s 内拉回），
    # 而稳态时质心噪声被 0.5 增益减半、块内线性摊开后间隔偏差 <2ms。
    # 质心离散度过大（间奏里独奏音符冒充拍）的块整块弃用。
    E = OUTBEATS_FIX_EVERY
    half = max(1, int(round(0.15 * P * fps)))
    thr = float(np.percentile(onset[onset > 0], 90)) * 0.30 if np.any(onset > 0) else 0.0
    ks_ctl, offs_ctl = [0.0], [0.0]
    n_blk = n_beat // E
    n_valid = 0
    cur = 0.0
    for b in range(n_blk):
        k0b = b * E
        offsets, weights = [], []
        for k in range(k0b, k0b + E):
            c = int(round((phase0 + k * P + cur) * fps))
            a, bb = c - half, c + half + 1
            if a < 0 or bb > len(onset):
                continue
            w = onset[a:bb].copy()
            w[w < thr] = 0.0
            if not np.any(w > 0):
                continue
            pos = np.arange(a, bb) / fps
            offsets.append(float(np.sum(pos * w) / np.sum(w)) - (phase0 + k * P + cur))
            weights.append(float(np.sum(w)))
        if len(offsets) >= max(2, E // 2):
            dev = float(np.average(offsets, weights=weights))
            var = float(np.average((np.asarray(offsets) - dev) ** 2, weights=weights))
            if abs(dev) <= 0.25 * P and math.sqrt(max(var, 0.0)) <= 0.08 * P:
                n_valid += 1        # 块本身可信（与是否越过死区无关）
                if abs(dev) >= OUTBEATS_DEADBAND * P:
                    cur += max(-OUTBEATS_MAX_STEP,
                               min(OUTBEATS_MAX_STEP, 0.5 * dev))
        ks_ctl.append(float(k0b))
        offs_ctl.append(cur)
    if n_valid < max(2, int(0.4 * n_blk)):
        return None
    ks_ctl.append(float(n_beat))
    offs_ctl.append(offs_ctl[-1])
    ks_g = np.arange(n_beat, dtype=np.float64)
    curve = np.interp(ks_g, ks_ctl, offs_ctl)
    beats = phase0 + ks_g * P + curve
    beats = beats[(beats >= 0.0) & (beats < duration)]
    if len(beats) < 8:
        return None
    return beats


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
    # 分段变速："auto"（默认，检测到明显变拍才分段，分段不适用则退回整曲）/
    # "on"（强制分段，任一段越限则整首跳过）/ "off"（一律整曲单速）。
    # 也接受 True/False（True→on，False→off），兼容旧调用方。
    segments: object = "auto"


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
        o0 = int(round(origins[i] * sr))
        o1 = min(y2.shape[1], o0 + int(round(dur_out * sr)))
        bt_out = (_output_beats(y2[:, o0:o1], sr, float(sp.plan.a2),
                                (o1 - o0) / sr,
                                clicks_per_beat=float(sp.plan.clicks_per_beat))
                  if o1 - o0 > 8 * sr else None)
        if bt_out is not None:
            ct = click_schedule(bt_out, 1.0, sp.plan.click_bpm, dur_out,
                                clicks_per_beat=sp.plan.clicks_per_beat)
        else:
            ct = click_schedule(sp.beats, sp.plan.rate, sp.plan.click_bpm, dur_out,
                                clicks_per_beat=sp.plan.clicks_per_beat)
            if sp.beats is None or len(sp.beats) < 4:
                notes.append(tr(f"第{i + 1}段未取到可靠节拍位置，该段节拍器按速率均匀敲",
                                f"segment {i + 1} has no reliable beat positions; "
                                "its metronome clicks are spaced evenly", L))
        times.extend(origins[i] + float(c) for c in ct)
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
        #    自动检测推迟到「分段变速」判定之后再跑：分段一旦接手，整曲测速的结果
        #    就用不上，先跑等于白算一遍全曲（4 分钟的歌约 3 秒，占单曲总耗时 15%）。
        manual = bool(opt.bpm and opt.bpm > 0)
        detected = float(opt.bpm) if manual else None
        beat_times = None
        how = tr("手动指定", "Manual", L) if manual else tr("自动检测", "Auto", L)

        if opt.detect_only:
            if detected is None:
                detected, beat_times = detect_tempo(y_mono, sr, L)
            row["检测BPM"] = f"{detected:.2f}"
            row["检测方式"] = how
            log_fn(f"  {path.name}: {detected:.1f} BPM")
            row["备注"] = tr("仅检测速度", "Tempo only", L)
            return row

        # 1.5) 分段变速（opt-in）：找变拍点，每段各按「最小变速」规则出方案。
        #      手动指定原速时无从分段；只发现一段时退回整曲路径。
        #      关键语义（需求 m02195）：检测到多段但**任一段**超出加减速上限时，
        #      整首跳过、不生成文件、也不退回整曲处理——避免出现「一首歌里有的段
        #      变了、有的段没变」导致交接点两侧速度对不上。
        seg_mode = _segments_mode(opt)
        seg_plans = None
        seg_infeasible = None
        single_layer = None
        mel_all = None
        if seg_mode != "off" and not manual:
            try:
                # 全曲 mel 算一次：分段路径与回退整曲测速共用（约 3.6 秒/首，
                # 占分段路径总耗时四分之一，重复算就是纯浪费）。
                import librosa
                mel_all = np.abs(librosa.feature.melspectrogram(
                    y=y_mono, sr=sr, hop_length=256, fmax=0.5 * sr))
                cand, seg_infeasible = _segment_plans(
                    y_mono, sr, opt.target_bpm, opt.strict,
                    opt.max_slowdown, opt.max_speedup, lang=L,
                    merge_infeasible=(seg_mode == "auto"), mel=mel_all)
                if seg_infeasible is None:
                    if len(cand) > 1 and (seg_mode == "on" or _segments_worthwhile(cand)):
                        seg_plans = cand
                    elif len(cand) == 1 and cand[0][2] is not None:
                        # 分段塌缩成一层：这不是「没发现变拍」，而是「整首是同一条
                        # 拍子，只是测速在八度/层级之间跳」。这一层的速度和拍点比
                        # beat_track 的整曲结果可靠（实测 86 BPM 的曲子 beat_track
                        # 报 152，塌缩层是 87.0），所以整曲路径要复用它的结果，
                        # 不能丢掉重测。
                        single_layer = cand[0][2]
                    else:
                        notes.append(tr("未发现明显变拍，按整曲处理",
                                        "no significant tempo change; processed as one piece", L))
            except RuntimeError as re:
                notes.append(tr("分段变速不可用（",
                                "segmented mode unavailable (", L) + str(re)
                             + tr("），已改按整曲处理", "); processed as one piece", L))
        if seg_plans:
            return _process_segmented(path, opt, y, sr, y_mono, seg_plans,
                                      row, notes, log_fn, rel_base)
        if seg_infeasible is not None:
            _t0, _t1, _bpm, _why = seg_infeasible
            if seg_mode == "auto":
                # 自动模式：分段不可行就退回整曲单速，绝不因为「分段失败」把歌丢掉。
                notes.append(tr("分段变速不适用（",
                                "segmented mode not applicable (", L) + _why
                             + tr("），已按整曲处理", "); processed as one piece", L))
            else:
                if detected is None:
                    detected, beat_times = detect_tempo(y_mono, sr, L)
                row["检测BPM"] = f"{detected:.2f}"
                row["检测方式"] = tr("自动检测·分段", "Auto · segmented", L)
                row["备注"] = (tr("跳过：", "Skipped: ", L)
                               + tr("分段变速中有段落超出加减速上限",
                                   "a section exceeds the stretch limits in segmented mode", L)
                               + tr("：", ": ", L) + _why
                               + tr("，整首不做变速", "; the whole track is left unchanged", L))
                log_fn("  " + row["备注"])
                return row

        # 1.9) 整曲测速（分段没接手时才做；手动指定时 detected 已有值）
        if detected is None:
            if single_layer is not None:
                detected = float(single_layer.bpm)
                beat_times = np.asarray(single_layer.beats) + float(single_layer.t0)
            else:
                detected, beat_times = detect_tempo(y_mono, sr, L, mel=mel_all)
        row["检测BPM"] = f"{detected:.2f}"
        row["检测方式"] = how

        # 2) 选最小变速方案（受用户设定的变速上限约束；都不满足 → 跳过该文件）
        plan = (single_layer.plan if single_layer is not None
                else choose_stretched_tempo(detected, opt.target_bpm, opt.strict,
                                            opt.max_slowdown, opt.max_speedup))
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
            # 按变速后的音乐直接找拍（输出轴逐拍跟踪）；不可靠再退回
            # 「输入轴网格 ÷ 拉伸比」的理论路径。
            bt_out = _output_beats(y2, sr, float(plan.a2), n / sr,
                                   clicks_per_beat=float(plan.clicks_per_beat))
            if bt_out is not None:
                times = click_schedule(bt_out, 1.0, plan.click_bpm, n / sr,
                                       clicks_per_beat=plan.clicks_per_beat)
            else:
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
            segments=("tempo-change handling: auto = detect clear tempo changes inside each track and "
                      "convert every section with its own minimum-stretch plan (seams cross-faded; "
                      "default), on = always split (the whole track is left unchanged if any section "
                      "exceeds the limits), off = one speed for the whole track; ignored with --bpm"),
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
            segments=("变拍处理：auto=检测到明显变拍时自动分段，每段各按最小变速规则处理"
                      "（拼接点做交叉淡化；默认）；on=总是分段（任一段超出加减速上限时整首不变速）；"
                      "off=整曲单速；手动指定 --bpm 时不分段"),
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
    ap.add_argument("--segments", nargs="?", const="on", default="auto",
                    choices=["auto", "on", "off"], help=H["segments"])
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
        segments=getattr(args, "segments", "auto"),
    )

    pairs = list(collect_inputs_with_base(args.inputs, args.recursive))
    if not pairs:
        print(e("没有可处理的音频文件。支持格式：", "No audio files to process. Supported formats: ")
              + " ".join(sorted(SUPPORTED_EXTS - _UNREADABLE_EXTS)))
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
