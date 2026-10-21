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
    """任一段超出加减速上限 → 整首不做变速（返回 infeasible，调用方跳过整首）。

    需求 m02195：不允许「只变可行段、跳过越限段」——那会让交接点两侧速度对不上。
    """
    y, _half = _two_section_track()
    plans, infeasible = tc._segment_plans(y, SR, 180.0, False,
                                          max_slowdown=0.01, max_speedup=0.01, lang="zh")
    assert infeasible is not None, "1% 的紧上限下必然有段落越限"
    _t0, _t1, _bpm, _why = infeasible
    assert _bpm > 0 and _why, "越限信息应包含该段速度与原因"
    assert all(p[2] is None for p in plans), "越限时不得返回任何可行段方案"


def test_segmented_options_default_off():
    """硬约束：分段是 opt-in，默认值必须是 False（默认路径行为完全不变）。"""
    assert tc.ConvertOptions().segments is False


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
