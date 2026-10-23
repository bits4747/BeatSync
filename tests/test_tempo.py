"""BeatSync 单元测试：测速口径、速度方案选择、节拍网格、边界用例、i18n 完整性。

运行：pytest tests/ -v
无 pytest 时：python tests/test_tempo.py （内置极简 runner）

这些测试**不依赖** test_audio/ 里的大文件，全部在内存里合成点击轨，
因此在 CI 上秒级完成。真实音频的端到端回归在仓库外的 _dev/run_checks.py。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

try:
    import pytest
except ImportError:  # 允许无 pytest 直接运行
    pytest = None

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import tempo_converter as tc  # noqa: E402

SR = 44100


def click_track(bpm: float, dur: float = 20.0, sr: int = SR,
                accent_every: int = 4) -> np.ndarray:
    """合成等距点击轨：每 accent_every 拍加重一次，模拟 4/4 的重音。"""
    n = int(sr * dur)
    y = np.zeros(n, dtype=np.float32)
    period = 60.0 / bpm
    t = 0.0
    k = 0
    while t < dur - 0.1:
        i = int(t * sr)
        L = int(0.03 * sr)
        if i + L < n:
            env = np.exp(-np.linspace(0, 1, L) * 8)
            tone = np.sin(2 * np.pi * 1000 * np.linspace(0, L / sr, L))
            amp = 1.0 if (k % accent_every == 0) else 0.45
            y[i:i + L] += (amp * env * tone).astype(np.float32)
        t += period
        k += 1
    return y


# ---------------------------------------------------------------- 测速口径

def test_detects_true_bpm_without_octave_folding():
    """测速必须原样返回，不做 2 的整数次幂折叠。

    2026-10-19 的核心修复：旧版 normalize_tempo 会把 60 折叠成 120，
    导致 60 BPM 慢歌被当 120 处理、变速 +49.9% 硬拉到 180。
    """
    for bpm in (60.0, 85.0, 90.0, 120.0, 150.0, 180.0):
        detected, beats = tc.detect_tempo(click_track(bpm), SR)
        assert abs(detected - bpm) < 1.0, (
            f"检测 {detected:.2f} 与真值 {bpm} 偏离超过 1 BPM；"
            f"若恰是 2 倍关系，说明归一化折叠又回来了"
        )
        assert len(beats) > 4


def test_normalize_tempo_is_identity():
    """normalize_tempo 已停用，任何输入都应原样返回。"""
    for v in (30.0, 55.0, 60.0, 120.0, 200.0, 360.0, 720.0):
        assert abs(tc.normalize_tempo(v) - v) < 1e-9, (
            f"normalize_tempo({v}) 被改回了折叠行为"
        )


def test_legacy_fold_kept_for_comparison():
    """历史折叠实现保留作对照基线，防止有人误删。"""
    assert abs(tc._fold_tempo_legacy(60.0) - 120.0) < 1e-9
    assert abs(tc._fold_tempo_legacy(360.0) - 90.0) < 1e-9


def test_detected_value_is_plausible():
    for bpm in (60.0, 90.0, 120.0):
        detected, _ = tc.detect_tempo(click_track(bpm), SR)
        assert 30.0 <= detected <= 300.0


# ---------------------------------------------------------------- 速度方案

def test_exact_target_returns_rate_one():
    p = tc.choose_stretched_tempo(90.0, 90.0)
    assert p is not None
    assert abs(p.rate - 1.0) < 1e-9
    assert abs(p.clicks_per_beat - 1.0) < 1e-9


def test_60_to_180_matches_readme():
    """修复后 60→180 应当**不变速**、每拍敲 3 下。

    这正是 README 示例表第一行的行为——旧版因为把 60 折叠成 120，
    实际会去走 +49.9% 拉伸，永远到不了这一行。
    """
    p = tc.choose_stretched_tempo(60.0, 180.0)
    assert p is not None, "60→180 不应被跳过"
    assert abs(p.rate - 1.0) < 1e-6, f"应当不变速，实际 rate={p.rate}"
    assert abs(p.clicks_per_beat - 3.0) < 1e-6, (
        f"应当每拍敲 3 下，实际 cpb={p.clicks_per_beat}"
    )
    assert abs(p.click_bpm - 180.0) < 1e-6


def test_click_bpm_always_equals_target():
    """节拍器恒以目标速度发声（项目核心设计）。"""
    for src in (60.0, 85.0, 100.0, 120.0, 150.0):
        p = tc.choose_stretched_tempo(src, 180.0)
        if p is not None:
            assert abs(p.click_bpm - 180.0) < 1e-6, (
                f"{src}→180 的 click_bpm={p.click_bpm} 偏离目标"
            )


def test_120_to_180_uses_speedup_branch():
    """120→180 在 20%/50% 默认下走加速档（减速 25% 超 20% 上限）。"""
    p = tc.choose_stretched_tempo(120.0, 180.0)
    assert p is not None
    assert p.rate > 1.0, f"应走加速档，实际 rate={p.rate}"
    assert abs(p.a2 - 180.0) < 1e-6


def test_rate_always_positive():
    for src in (60.0, 85.0, 100.0, 120.0, 150.0, 200.0):
        p = tc.choose_stretched_tempo(src, 180.0)
        if p is not None:
            assert p.rate > 0 and p.clicks_per_beat > 0


def test_limit_tolerance_absorbs_detection_jitter():
    """120→180 恰好压在 +50% 边界上，测速偏低一点不应被判越限。

    实测同一首 120 BPM 的曲子在不同采样率下会测成 119.92~120.34，若把边界
    当严格不等式，检测值一旦低于 120.0 就会「需加速 50.0%，上限 50%」而整首
    跳过——最常见的使用场景会随机失败。
    """
    for src in (119.7, 119.9, 119.99, 120.0, 120.34):
        p = tc.choose_stretched_tempo(src, 180.0)
        assert p is not None, f"{src}→180 被误判越限"
        assert abs(p.a2 - 180.0) < 1e-6


def test_limit_still_rejects_clearly_out_of_range():
    """容差只吸收抖动，明显超限的歌仍应跳过。"""
    # 180/119.4 = +50.8%，超出 50%+0.5% 的判定上限
    assert tc.choose_stretched_tempo(119.4, 180.0) is None
    # 90/105 = -14.3%，但把放慢上限压到 5% 后两个方向都超
    assert tc.choose_stretched_tempo(105.0, 90.0, max_slowdown=0.05) is None


# ---------------------------------------------------------------- 节拍网格

def test_beat_grid_no_phase_drift():
    """节拍器必须落在音乐自己的拍点上（项目核心宣传点）。"""
    for bpm in (90.0, 120.0):
        y = click_track(bpm, dur=20.0, accent_every=100)  # 去掉重音，纯等距
        _, beats = tc.detect_tempo(y, SR)
        P = 60.0 / bpm
        spread = float(np.std(np.mod(beats, P)))
        assert spread < 0.01, f"{bpm} BPM 相位抖动 std={spread:.4f}s 过大"


def test_beat_interval_is_regular():
    y = click_track(120.0, dur=20.0, accent_every=100)
    _, beats = tc.detect_tempo(y, SR)
    assert float(np.std(np.diff(beats))) < 0.005


# ---------------------------------------------------------------- 边界用例

def test_silence_raises():
    try:
        tc.detect_tempo(np.zeros(SR * 3, dtype=np.float32), SR)
    except RuntimeError:
        return
    raise AssertionError("静音音频应当检测失败并抛 RuntimeError")


def test_tiny_audio_bounded_or_raises():
    """0.3 秒单音：要么报错，要么给出有限正值——不能返回 NaN。"""
    n = int(SR * 0.3)
    y = 0.3 * np.sin(2 * np.pi * 440 * np.linspace(0, 0.3, n)).astype(np.float32)
    try:
        bpm, _ = tc.detect_tempo(y, SR)
    except RuntimeError:
        return
    assert bpm > 0 and np.isfinite(bpm)


# ---------------------------------------------------------------- i18n

def test_i18n_key_parity():
    """ZH / EN 两个词条表的键必须完全对齐——漏一个就会在界面上显示原始键名。"""
    import re
    js = (REPO / "frontend" / "app.js").read_text(encoding="utf-8")
    zh_m = re.search(r"const ZH = \{(.*?)^\};", js, re.S | re.M)
    en_m = re.search(r"const EN = \{(.*?)^\};", js, re.S | re.M)
    assert zh_m and en_m, "未找到 ZH / EN 词条表，结构可能已变更"
    kz = set(re.findall(r"^\s*(\w+):", zh_m.group(1), re.M))
    ke = set(re.findall(r"^\s*(\w+):", en_m.group(1), re.M))
    assert len(kz) > 50, f"ZH 只解析出 {len(kz)} 个键，解析可能失效"
    assert kz == ke, (
        f"词条不对齐：ZH 独有 {sorted(kz - ke)[:5]}，EN 独有 {sorted(ke - kz)[:5]}"
    )


def test_i18n_placeholder_parity():
    """中英词条的 {n} 类占位符必须成对出现。"""
    import re
    js = (REPO / "frontend" / "app.js").read_text(encoding="utf-8")
    zh_m = re.search(r"const ZH = \{(.*?)^\};", js, re.S | re.M)
    en_m = re.search(r"const EN = \{(.*?)^\};", js, re.S | re.M)
    zmap = dict(re.findall(r"^\s*(\w+):\s*\"((?:[^\"\\]|\\.)*)\"", zh_m.group(1), re.M))
    emap = dict(re.findall(r"^\s*(\w+):\s*\"((?:[^\"\\]|\\.)*)\"", en_m.group(1), re.M))
    assert zmap, "未解析到任何 ZH 词条"
    bad = [k for k in set(zmap) & set(emap)
           if len(re.findall(r"\{[^}]*\}", zmap[k])) != len(re.findall(r"\{[^}]*\}", emap[k]))]
    assert not bad, f"占位符中英不一致：{bad[:5]}"


# ---------------------------------------------------------------- 分段变速

def _accent_track(bpm: float, dur: float, sr: int = SR, offset: float = 0.0,
                  acc: int = 4, n: int | None = None) -> np.ndarray:
    """分段用合成点击轨：每 acc 拍一次 1200Hz 强击、其余 500Hz 弱击（与
    _dev 回归同源；重音型保证变点检测能在两段交界处看到档位跳变）。
    n 给定总样本数（两段共用一根时间轴），offset 是该段起点。"""
    n = int(sr * dur) if n is None else n
    y = np.zeros(n, dtype=np.float32)
    period = 60.0 / bpm
    t, k = 0.0, 0
    while t < dur - period * 0.5:
        i = int((t + offset) * sr)
        L = int(0.05 * sr)
        if 0 <= i < n - L:
            env = np.exp(-np.linspace(0, 1, L) * 9)
            strong = (k % acc == 0)
            freq = 1200.0 if strong else 500.0
            amp = 1.0 if strong else 0.5
            y[i:i + L] += (amp * env * np.sin(2 * np.pi * freq * np.linspace(0, L / sr, L))).astype(np.float32)
        t += period
        k += 1
    return y


def _two_section_track(sr: int = SR, half: float = 10.0):
    """前 half 秒 100 BPM、后 half 秒 140 BPM（同一根时间轴上相接），归一到 0.9。"""
    n = int(sr * 2 * half)
    y = (_accent_track(100.0, half, sr, offset=0.0, n=n)
         + _accent_track(140.0, half, sr, offset=half, n=n))
    return 0.9 * y / max(1e-9, float(np.max(np.abs(y)))), half


def test_segments_split_changing_track():
    """曲内 100→140 变拍必须被切开，且边界误差不超过 1 秒。"""
    y, half = _two_section_track()
    segs = tc.detect_tempo_segments(y, SR)
    assert len(segs) >= 2, f"应当切出多段，实际 {len(segs)} 段"
    edges = [s[0] for s in segs[1:]]
    assert min(abs(e - half) for e in edges) <= 1.0, f"边界 {[round(e,2) for e in edges]} 偏离 {half}s 超过 1s"


def test_segments_keep_steady_track_whole():
    """恒速曲不得误切：整曲只有一段（= 不做分段、走默认路径）。"""
    y = _accent_track(120.0, 24.0)
    segs = tc.detect_tempo_segments(y, SR)
    assert len(segs) == 1, f"恒速曲不应分段，实际 {len(segs)} 段: {segs}"


def test_segment_plans_each_within_limits():
    """每段各按最小变速规则出方案：rate 必须落在用户上下限内，倍率档互相独立。"""
    y, _ = _two_section_track()
    plans, infeasible = tc._segment_plans(y, SR, 180.0, False,
                                          tc.DEFAULT_MAX_SLOWDOWN, tc.DEFAULT_MAX_SPEEDUP, lang="zh")
    assert infeasible is None, f"两段曲都应在限内，实际越限: {infeasible}"
    real = [p for p in plans if p[2] is not None]
    assert len(real) >= 2, "两段曲应得到两份方案"
    for (_t0, _t1, sp) in real:
        lo = 1.0 - tc.DEFAULT_MAX_SLOWDOWN
        hi = 1.0 + tc.DEFAULT_MAX_SPEEDUP
        assert lo - 1e-9 <= sp.plan.rate <= hi + 1e-9, f"rate {sp.plan.rate} 越界"
        assert abs(sp.plan.click_bpm - 180.0) < 1e-6, "节拍器恒以目标速度发声"


def test_segment_stitch_seam_is_quiet():
    """拼接关：接缝处的一阶差分不得超过音乐自身最强瞬态（评分 ≤1 = 听不出爆音）。

    评分口径：|diff(out)| 在接缝 ±0.15 s 内的最大值 ÷ 距接缝 0.5 s 以外的全局最大值。
    段自身瞬态（鼓点）天然就是全曲最大跳变，所以分母用「离接缝远处的全局最大值」，
    不能用分位数——合成曲每段起点是重音，硬切时接缝正砸在重音上（对照见 _dev 原型）。
    """
    y, _ = _two_section_track()
    plans, _inf = tc._segment_plans(y, SR, 180.0, False,
                                    tc.DEFAULT_MAX_SLOWDOWN,
                                    tc.DEFAULT_MAX_SPEEDUP, lang="zh")
    plans = [p for p in plans if p[2] is not None]
    assert len(plans) >= 2
    y2 = np.stack([y, y]).astype(np.float32)      # 主干接口是 (声道, 采样)
    out, origins, _ht, _sc = tc._stitch_segments(y2, SR, plans, "phasevocoder")
    d = np.abs(np.diff(out, axis=1)).max(axis=0)   # 沿时间轴；声道轴的 diff 是电平差
    times = np.arange(len(d)) / SR
    for i in range(1, len(plans)):
        c0 = origins[i]
        near, far = np.abs(times - c0) <= 0.15, np.abs(times - c0) > 0.5
        assert far.any()
        score = float(d[near].max()) / max(float(d[far].max()), 1e-9)
        assert score <= 1.0, f"接缝 {c0:.2f}s 评分 {score:.2f} > 1：爆音超过音乐自身瞬态"


def test_segment_metronome_follows_each_section():
    """节拍器关：输出音乐自己的每个拍点都必须有点击落在上面（跨段不错拍）。"""
    y, _ = _two_section_track()
    plans, _inf = tc._segment_plans(y, SR, 180.0, False,
                                    tc.DEFAULT_MAX_SLOWDOWN,
                                    tc.DEFAULT_MAX_SPEEDUP, lang="zh")
    plans = [p for p in plans if p[2] is not None]
    assert len(plans) >= 2
    y2 = np.stack([y, y]).astype(np.float32)
    out, origins, _ht, _sc = tc._stitch_segments(y2, SR, plans, "phasevocoder")
    worst = 0.0
    for i, (_t0, _t1, sp) in enumerate(plans):
        dur_out = float((_t1 - _t0) / sp.plan.rate) + 0.5
        clicks = np.asarray(tc.click_schedule(sp.beats, sp.plan.rate, sp.plan.click_bpm,
                                              dur_out,
                                              clicks_per_beat=sp.plan.clicks_per_beat))
        beats_out = origins[i] + sp.beats / sp.plan.rate
        for b in beats_out:
            if 0 <= b <= out.shape[1] / SR and clicks.size:
                worst = max(worst, float(np.min(np.abs(origins[i] + clicks - b))))
    # 拉伸后拍点会有 ~1 个相位窗的量化偏差；0.1 s 是「明显错拍」和「跟得上」的分界
    assert worst < 0.1, f"最坏拍点-点击偏差 {worst * 1000:.1f}ms：跨段错拍"


def test_segment_infeasible_skips_whole_track():
    """on 模式：任一段超出加减速上限 → 整首不做变速（返回 infeasible，调用方跳过整首）。

    需求 m02195：不允许「只变可行段、跳过越限段」——那会让交接点两侧速度对不上。
    这里显式关掉自动合并（merge_infeasible=False），模拟界面勾选「强制分段」。
    """
    y, _half = _two_section_track()
    plans, infeasible = tc._segment_plans(y, SR, 180.0, False,
                                          max_slowdown=0.01, max_speedup=0.01, lang="zh",
                                          merge_infeasible=False)
    assert infeasible is not None, "1% 的紧上限下必然有段落越限"
    _t0, _t1, _bpm, _why = infeasible
    assert _bpm > 0 and _why, "越限信息应包含该段速度与原因"
    assert all(p[2] is None for p in plans), "越限时不得返回任何可行段方案"


def test_segment_auto_merges_infeasible_section():
    """auto 模式：越限段并入速度相近的邻段，而不是把整首歌丢掉。

    现实案例：一首歌中间有 6~10 秒的过渡/加花段，它自己「加速差一点、减速也
    差一点」，但并进速度相近的大段后整段可行——此时应当照常出文件。
    """
    y, _half = _two_section_track()
    plans, infeasible = tc._segment_plans(y, SR, 180.0, False,
                                          max_slowdown=0.01, max_speedup=0.01, lang="zh",
                                          merge_infeasible=True)
    # 1% 上限下所有段都不可行 → 一路并到整曲 → 交回「没发现变拍」的单段形式，
    # 由整曲单速路径决定跳不跳，而不是在分段这一层把歌判死。
    assert infeasible is None
    assert len(plans) == 1 and plans[0][2] is None


def test_segments_worthwhile_guard():
    """auto 模式：段间速度差不够大时不值得分段。"""
    class _P:
        def __init__(self, bpm):
            self.bpm = bpm

    def mk(*bpms):
        return [(0.0, 1.0, _P(b)) for b in bpms]

    assert tc._segments_worthwhile(mk(100.0, 140.0)) is True
    assert tc._segments_worthwhile(mk(100.0, 106.0)) is False
    assert tc._segments_worthwhile(mk(100.0)) is False


def test_segments_mode_normalisation():
    """opt.segments 接受 bool 与 "auto"/"on"/"off" 三态字符串。"""
    class _O:
        def __init__(self, v):
            self.segments = v

    assert tc._segments_mode(_O("auto")) == "auto"
    assert tc._segments_mode(_O("on")) == "on"
    assert tc._segments_mode(_O("off")) == "off"
    assert tc._segments_mode(_O(True)) == "on"
    assert tc._segments_mode(_O(False)) == "off"
    assert tc._segments_mode(_O(None)) == "auto"


def test_segmented_options_default_auto():
    """默认是 auto：检测到明显变拍就自动分段（变拍歌默认也能对上节拍器）。"""
    assert tc.ConvertOptions().segments == "auto"


def test_segments_merge_equivalent_neighbours():
    """相邻段输出方案完全相同（a2 与每拍点击数一样）时不得留下多余分段。

    这正是用户报告的问题：报告里相邻段 a2/clicks 一模一样，只有 rate 差千分之几，
    听感上是同一段音乐，却白白多一处接缝。判据用 SEG_MERGE_RATIO，别再退回到
    「rate 精确相等」——那样几乎永不命中。
    """
    y, half = _two_section_track()          # 100 → 140，两段输出方案不同
    plans, inf = tc._segment_plans(y, SR, 180.0, False,
                                   tc.DEFAULT_MAX_SLOWDOWN, tc.DEFAULT_MAX_SPEEDUP, lang="zh")
    assert inf is None
    real = [p for p in plans if p[2] is not None]
    for a, b in zip(real[:-1], real[1:]):
        same = (abs(a[2].plan.a2 - b[2].plan.a2) < 1e-9
                and abs(float(a[2].plan.clicks_per_beat)
                        - float(b[2].plan.clicks_per_beat)) < 1e-9)
        assert not same, f"相邻段输出方案相同（{a[2].bpm:.1f}/{b[2].bpm:.1f}）却没被合并"


def test_segment_plans_cover_whole_track():
    """分段必须无缝覆盖全曲：不留空洞、不重叠——否则那段音频会被吞掉或敲两遍。"""
    y, _ = _two_section_track()
    plans, _inf = tc._segment_plans(y, SR, 180.0, False,
                                    tc.DEFAULT_MAX_SLOWDOWN, tc.DEFAULT_MAX_SPEEDUP, lang="zh")
    total = len(y) / SR
    assert abs(plans[0][0]) < 1e-9, f"首段必须从 0 开始，实际 {plans[0][0]}"
    assert abs(plans[-1][1] - total) < 1e-6, f"末段必须到结尾 {total}，实际 {plans[-1][1]}"
    for a, b in zip(plans[:-1], plans[1:]):
        assert abs(a[1] - b[0]) < 1e-9, f"段 {a[:2]} 与 {b[:2]} 之间不连续"


def test_detect_tempo_accepts_precomputed_mel():
    """分段反复测速时复用整曲 mel：结果必须与单独重算一致。

    这是纯提速路径（长曲上单段省 0.3~0.5 秒），一旦结果漂移就会改写分段决策，
    必须锁住「同一段音乐的 bpm 不因换 mel 来源而变」。
    """
    import librosa
    y = _accent_track(120.0, 20.0)
    bpm_a, beats_a = tc.detect_tempo(y, SR)
    mel = np.abs(librosa.feature.melspectrogram(y=y, sr=SR, hop_length=256, fmax=0.5 * SR))
    bpm_b, beats_b = tc.detect_tempo(y, SR, mel=mel)
    assert abs(bpm_a - bpm_b) < 0.1, f"复用 mel 后 bpm 从 {bpm_a:.2f} 变成 {bpm_b:.2f}"
    assert len(beats_a) == len(beats_b)


def test_detect_tempo_mel_slice_matches_whole():
    """按帧切出来的 mel 片段，测速结果要与「对那段音频单独算」一致（误差 ≤0.5 BPM）。

    切片的边界窗与单独算略有不同，但不应影响测出的速度；差太多说明帧号换算错了。
    """
    import librosa
    y = _accent_track(100.0, 30.0)
    mel_all = np.abs(librosa.feature.melspectrogram(y=y, sr=SR, hop_length=256,
                                                    fmax=0.5 * SR))
    a, b = int(4.0 * SR), int(24.0 * SR)
    fa, fb = int(round(a / 256)), int(round(b / 256))
    bpm_seg, _ = tc.detect_tempo(y[a:b], SR)
    bpm_mel, _ = tc.detect_tempo(y[a:b], SR, mel=mel_all[:, fa:fb])
    assert abs(bpm_seg - bpm_mel) < 0.5, f"切片 {bpm_mel:.2f} vs 单独算 {bpm_seg:.2f}"


# ---------------------------------------------------------------- 整数家族

def _mk_merged(*bpms, dur: float = 12.0):
    """构造 _collapse_same_family 的输入：每段 dur 秒、拍点按该段速度铺满。"""
    merged = []
    t0 = 0.0
    for bpm in bpms:
        t1 = t0 + dur
        beats = np.arange(0.0, dur, 60.0 / bpm)
        beats = beats + t0
        plan = tc.choose_stretched_tempo(bpm, 180.0, False,
                                         tc.DEFAULT_MAX_SLOWDOWN,
                                         tc.DEFAULT_MAX_SPEEDUP)
        merged.append((t0, t1, tc.SegmentPlan(t0, t1, float(bpm), beats, plan)))
        t0 = t1
    return merged


def test_int_ratio_accepts_integers_and_rejects_fractions():
    """整数比判据：n 倍 / 1/n 通过，2/3、3/2 这类分数比必须拒绝。

    用户 m07810 的硬要求：测出的速度只能是真实 BPM 的整数倍或整数分之一。
    分数比补不回来（CPB_CANDIDATES 只含整数与 1/整数）→ 节拍器真的错频。
    """
    assert tc._int_ratio(120.0, 60.0) == 2
    assert tc._int_ratio(60.0, 180.0) == 3
    assert tc._int_ratio(90.0, 90.0) == 1
    assert tc._int_ratio(120.0, 180.0) is None      # 3/2：分数比
    assert tc._int_ratio(100.0, 150.0) is None      # 3/2
    assert tc._int_ratio(140.4, 102.8) is None      # YYZ 实测两段：非整数比
    assert tc._int_ratio(0.0, 90.0) is None


def test_snap_family_absorbs_layer_miscount():
    """同一段音乐被数成不同节拍层（90 vs 120，即 4/3）时能吸附到同一家族。

    SNAP_MAX = log2(1.35) 就是为吸收这类层级误差而设；而 YYZ 前奏 102.8 与
    主体 140.4 相差 1.366 倍 > 1.35，必须留在家族外（否则会被并成一段错频）。
    """
    d, layer = tc._snap_family(120.0, 90.0)
    assert d <= tc.SNAP_MAX, f"120 应能吸附到 90 的家族，实际距离 {d:.3f}"
    assert abs(layer - 90.0) < 1e-6, f"应吸附到 90 这一层，实际 {layer}"
    d2, _ = tc._snap_family(140.4, 102.8)
    assert d2 > tc.SNAP_MAX, "YYZ 两段不该被吸附成同一家族"


def test_min_cost_layer_prefers_smallest_stretch():
    """家族内选层只看变速代价：rate 最接近 1 的那层胜出。"""
    mc = tc._min_cost_layer(90.0, 180.0, tc.DEFAULT_MAX_SLOWDOWN,
                            tc.DEFAULT_MAX_SPEEDUP)
    assert mc is not None
    _key, cpb, a2, rate = mc
    assert abs(rate - 1.0) < 1e-9 and cpb == 2 and abs(a2 - 90.0) < 1e-9
    # 150 → 180：cpb=1 的 +20% 比 cpb=2 的 -40% 更接近 1
    mc2 = tc._min_cost_layer(150.0, 180.0, tc.DEFAULT_MAX_SLOWDOWN,
                             tc.DEFAULT_MAX_SPEEDUP)
    assert mc2 is not None and mc2[1] == 1 and abs(mc2[3] - 1.2) < 1e-9


def test_collapse_same_family_merges_layer_jitter():
    """测速在同一段音乐上来回跳档（120|90|120|90）→ 合成一段。

    这是用户 m07727 报的「恒速歌被切成 7 段」：各段互为整数比、本就是同一段
    音乐的不同层，切段纯属多余，只增加接缝。
    """
    merged = _mk_merged(120.0, 90.0, 120.0, 90.0)
    out = tc._collapse_same_family(merged, 180.0, tc.DEFAULT_MAX_SLOWDOWN,
                                   tc.DEFAULT_MAX_SPEEDUP)
    assert len(out) == 1, f"应塌缩成 1 段，实际 {len(out)} 段"
    t0, t1, sp = out[0]
    assert abs(t0 - merged[0][0]) < 1e-9 and abs(t1 - merged[-1][1]) < 1e-9
    # 选层规则：最低的有可行方案的实测段速（最接近音乐真实拍速的那一层）
    assert abs(sp.bpm - 90.0) < 1e-9, f"应选最低实测层 90，实际 {sp.bpm:.1f}"
    assert abs(sp.plan.a2 - 90.0) < 1e-9 and abs(sp.plan.rate - 1.0) < 1e-9
    # 拍点必须接成一条完整时间轴，不得丢段
    beats = np.asarray(sp.beats)
    assert beats[0] < t0 + 1.0 and beats[-1] > t1 - 60.0 / 90.0 - 1.0
    assert np.all(np.diff(beats) > 0), "拼接后的拍点必须严格递增"


def test_collapse_keeps_monotonic_true_change():
    """单调推进的真变拍（90|120|150）不得被塌缩——方向只有一次，不是抖动。"""
    merged = _mk_merged(90.0, 120.0, 150.0)
    out = tc._collapse_same_family(merged, 180.0, tc.DEFAULT_MAX_SLOWDOWN,
                                   tc.DEFAULT_MAX_SPEEDUP)
    assert len(out) == 3, f"真变拍应保留 3 段，实际 {len(out)} 段"


def test_collapse_keeps_non_integer_ratio_sections():
    """非整数比的两段（YYZ 式 102.8|140.4）不得塌缩。

    塔缩会让其中一段被拉到错的速度 → 节拍器错频，宁可保留分段。
    """
    merged = _mk_merged(102.8, 140.4, 102.8)
    out = tc._collapse_same_family(merged, 180.0, tc.DEFAULT_MAX_SLOWDOWN,
                                   tc.DEFAULT_MAX_SPEEDUP)
    assert len(out) == 3, f"非整数比应保留分段，实际 {len(out)} 段"


def test_collapse_touches_nothing_under_three_sections():
    """少于 3 段谈不上「来回横跳」，一律不动（两段变速必须保留）。"""
    merged = _mk_merged(120.0, 90.0)
    out = tc._collapse_same_family(merged, 180.0, tc.DEFAULT_MAX_SLOWDOWN,
                                   tc.DEFAULT_MAX_SPEEDUP)
    assert len(out) == 2


# ---------------------------------------------------------------- mp3 码率

def test_mp3_bitrate_default_is_auto():
    """默认必须是「跟随原文件」，否则又回到库默认的低码率。"""
    assert tc.ConvertOptions().mp3_bitrate == "auto"


def test_snap_mp3_bitrate():
    """任意码率吸附到最接近的可选档位；等距取高的一档。"""
    assert tc._snap_mp3_bitrate(320) == 320
    assert tc._snap_mp3_bitrate(321.6) == 320      # 容器开销让实测值略高
    assert tc._snap_mp3_bitrate(245) == 256        # 典型 VBR 平均码率
    assert tc._snap_mp3_bitrate(128.4) == 128
    assert tc._snap_mp3_bitrate(142) == 128        # 库默认的 142k 也要落到档位上
    assert tc._snap_mp3_bitrate("abc") == tc.DEFAULT_MP3_BITRATE
    assert tc._snap_mp3_bitrate(10) == 96          # 低于最低档时取最低档


def _work_dir():
    """测试写文件用的目录（仓库内 tests/_tmp/，已 gitignore）。

    不用 pytest 的 tmp_path / tempfile.mkdtemp：某些受限环境里那类目录会被
    打上受限 ACL，libsndfile 打不开、pytest 之后也收不进去。os.makedirs 继承
    父目录 ACL，没这个问题。
    """
    d = REPO / "tests" / "_tmp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_resolve_mp3_bitrate_auto_reads_source():
    """auto 要真的去读输入文件码率；读不出来（非 mp3/损坏）时退回 320。"""
    import uuid

    import soundfile as sf

    d = _work_dir()
    src = d / f"a_{uuid.uuid4().hex}.mp3"
    wav = d / f"a_{uuid.uuid4().hex}.wav"
    try:
        sr, n = 44100, 44100 * 3
        y = (0.2 * np.sin(2 * np.pi * 440 * np.arange(n) / sr)).astype(np.float32)
        sf.write(str(src), y, sr, subtype="MPEG_LAYER_III",
                 bitrate_mode="CONSTANT", compression_level=0.65)   # 128 kbps
        got = tc.resolve_mp3_bitrate("auto", src)
        assert got == 128, f"auto 应跟随输入 128k，实际 {got}"

        sf.write(str(wav), y, sr, subtype="PCM_16")
        assert tc.resolve_mp3_bitrate("auto", wav) == tc.DEFAULT_MP3_BITRATE
    finally:
        for p in (src, wav):
            try:
                p.unlink()
            except OSError:
                pass


def test_write_mp3_honours_bitrate():
    """写出的 mp3 实际码率必须贴近请求值（这是本次修复的核心）。"""
    import os
    import uuid

    import soundfile as sf

    d = _work_dir()
    sr, n = 44100, 44100 * 5
    y = np.stack([0.2 * np.sin(2 * np.pi * 440 * np.arange(n) / sr)] * 2).astype(np.float32)
    for want in (320, 128):
        p = d / f"out_{want}_{uuid.uuid4().hex}.mp3"
        try:
            assert tc.write_audio(p, y, sr, mp3_bitrate=want) is None
            info = sf.info(str(p))
            kbps = os.path.getsize(p) * 8 / info.duration / 1000
            assert abs(kbps - want) < 12, f"请求 {want}k，实际 {kbps:.1f}k"
        finally:
            try:
                p.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------- 极简 runner

if __name__ == "__main__":
    if pytest is not None:
        sys.exit(pytest.main([__file__, "-v", "--no-header", "-q"]))
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"[PASS] {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"[FAIL] {name}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} 通过")
    sys.exit(1 if failed else 0)
