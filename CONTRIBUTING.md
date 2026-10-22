# 贡献指南

感谢你愿意参与 BeatSync。这份文档说明本地的开发、测试与提交约定。

## 环境准备

需要 **Python 3.11+**。

```bash
git clone <你的 fork 地址>
cd BeatSync
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -e ".[dev,gui]"
```

`gui` 组会装 `pywebview`（WebView 桌面界面需要）。不装也能跑命令行与 tkinter 兼容界面。
只想跑起来看看、不打算改代码的话，`pip install -r requirements.txt` 就够。

## 目录结构

| 路径 | 说明 |
| --- | --- |
| `tempo_converter.py` | 核心：测速、方案选择、变速、节拍器合成、批量、报告、命令行 |
| `tempo_converter_app.py` | WebView 桌面版的 Python 后端（JS ↔ Python 桥） |
| `tempo_converter_gui.py` | tkinter 兼容界面（WebView 起不来时的兜底） |
| `frontend/` | 桌面界面（原生 HTML/CSS/JS，无构建步骤，改完刷新即可） |
| `tests/` | 单元测试 |
| `assets/` | 图标：`icon.svg` 是源文件，`icon.png` / `icon.ico` 由它派生 |

## 跑测试

```bash
pytest tests/ -v
```

单元测试**不依赖**任何音频文件，全部在内存里合成点击轨，几十秒内跑完。
改动测速 / 变速 / 节拍器 / 分段算法时，务必先跑它。

改动界面（`frontend/`）时，除了看浏览器预览，还要用 `python tempo_converter_app.py`
实际跑一遍桌面版——JS 桥的行为在纯浏览器里是模拟不出来的。

## 改动约定

### 提交信息

用中文或英文都行，但**首行要说清「改了什么」**，正文说明**为什么**。
推荐前缀：

- `feat:` 新功能
- `fix:` 缺陷修复
- `docs:` 只改文档
- `test:` 只改测试
- `refactor:` 重构（行为不变）
- `chore:` 构建、依赖、杂项

行为变化（尤其是输出口径、默认值、文件命名）必须在正文里写明
「旧行为 → 新行为 → 哪些用户会受影响」。

### 代码风格

- 保持现有风格：类型注解、中文注释解释「为什么」而不是「做什么」。
- 新增的边界处理要配一条测试；算法类改动要给出可复现的数值依据
  （改前 / 改后的实测数字，而不是「感觉快了」）。
- 不要引入网络请求——本项目承诺全程离线运行。

### 涉及默认值的改动

`DEFAULT_MAX_SLOWDOWN` / `DEFAULT_MAX_SPEEDUP` 这类默认值直接决定
「哪些歌会被跳过」，改动前请在 PR 里贴出改动前后在
`60 / 85 / 100 / 120 / 150 / 180` 六个原速上的方案对比表。

## 版本号与更新日志

当前版本 **1.0.0**，是首个对外发布版本。**每次发布都要改版本号**，
并且下面三处必须一致：

1. `pyproject.toml` 的 `version`
2. `CHANGELOG.md` 顶部新增的那一节标题
3. `_packaging/BeatSync.iss` 的 `MyAppVersion`（安装包版本，脚本在仓库外）

版本号怎么涨（修缺陷 / 加功能 / 不兼容大改）见 `CHANGELOG.md` 开头的规则。
`CHANGELOG.md` 只记录**面向用户的行为**，内部重构不必逐条写。

## 提交 PR

1. 从最新 `main` 切分支：`git checkout -b fix/xxx`
2. 改完跑 `pytest tests/ -v`，全部通过
3. 在 `CHANGELOG.md` 当前版本一节里补一行（或按上面的规则新增一节）
4. 提 PR，描述里写清动机、改法、验证方式

## 报告问题

请附上：

- 操作系统与 Python 版本（`python -V`）
- 复现用的**原速 BPM**、目标 BPM、用了哪些参数
- 实际输出与预期输出的差异

如果涉及「测速不准」，请说明曲目的拍号（4/4、3/4、6/8 等）——
节拍检测对非 4/4 曲目、以及 70 BPM 以下 / 180 BPM 以上的极端速度有已知的
倍频 / 分频歧义，这时用 `--bpm` 手动指定原速即可绕开，这个信息很关键。
