# 更新日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [未发布]

## [1.1.0] — 2026-10-19

### 变更（**行为不兼容**）

- **测速结果不再折叠到 [70, 180)**：`detect_tempo` 现在报告检测器的实测值，
  测到 60 BPM 就报 60 BPM。
  - 旧行为：通过 `normalize_tempo` 对检测值反复乘/除 2，直到落进 [70,180)。
    一首 60 BPM 的慢歌会被报成 120 BPM，随后被推去走 +49.9% 的加速档，
    实际是「把慢歌硬拉到快歌」。
  - 影响：任何按旧报告口径写脚本、或依赖「慢歌自动被归一化」的用户，结果会变。
  - `normalize_tempo()` 保留函数名以兼容外部调用，但现在直接返回原值；
    历史折叠逻辑改名为 `_fold_tempo_legacy()`，仅供对照测试使用。

### 新增

- 单元测试 `tests/test_tempo.py`（15 项，秒级，无需测试音频）：
  测速口径、倍率档选择、节拍网格相位、静音/极短音频边界、中英词条对齐。
- `pyproject.toml`：可用 `pip install -e .` 安装，`beatsync` 命令直达 CLI。
  `gui` 与 `dev` 为可选依赖组。
- `.gitattributes`：统一换行归一化策略。
- `CHANGELOG.md`、`CONTRIBUTING.md`。
- GitHub Actions CI：多版本 Python × 多平台的单元测试。

### 修复

- 测速完成的提示文案不再声称「速度已按 70~180 BPM 的习惯口径归一化」。

## [1.0.0] — 2026-10-17

首个对外版本：命令行、WebView 桌面界面、Tk 兼容界面三套入口，
中英双语，Windows 安装包，MIT 许可。
