# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_all

# 本 spec 放在 packaging\ 下，被打包的源码就是它的上一级（仓库根）。
# SPECPATH 是 spec 所在目录（PyInstaller 注入），由此定位仓库根，避免相对路径失效。
# 也容许把 spec 直接放到仓库根跑：那时上一级没有 pyproject.toml，就地当成根。
_SPEC_DIR = globals().get('SPECPATH') or os.getcwd()
_UP = os.path.abspath(os.path.join(_SPEC_DIR, '..'))
REPO = _UP if os.path.isfile(os.path.join(_UP, 'pyproject.toml')) else os.path.abspath(_SPEC_DIR)
print('源码根:', REPO)

datas = [(os.path.join(REPO, 'frontend'), 'frontend')]

# 图标资源：运行时 _icon_path() 与前端 favicon 都要用 assets/ 下的文件。
# icon.png / icon.ico 已随仓库一起提供（由 assets/icon.svg 派生），无需额外生成步骤。
# 缺失时给出提示并继续构建，窗口/exe 退回默认图标。
_icon_ico = os.path.join(REPO, 'assets', 'icon.ico')
if os.path.isdir(os.path.join(REPO, 'assets')):
    datas.append((os.path.join(REPO, 'assets'), 'assets'))
else:
    print('警告：找不到 assets/，窗口与 exe 将使用默认图标')

binaries = []
hiddenimports = []
# tkinter 兜底界面（tempo_converter_gui）在 app 里是函数内动态 import，
# 静态分析抓不到；显式加入，保证冻结版设 TMC_FORCE_TK=1 时也能走 tkinter。
hiddenimports += ['tempo_converter_gui', 'tkinter', 'tkinter.ttk', 'tkinter.messagebox', 'tkinter.scrolledtext', 'tkinter.filedialog']
# numba 的自定义缓存定位器：tempo_converter 通过环境变量
# NUMBA_CACHE_LOCATOR_CLASSES=numba_locator.BeatSyncCacheLocator 注册它，numba 在
# import 期按字符串**动态导入**，静态分析抓不到。漏了它，打包版会在 import librosa
# 阶段抛「cannot cache function '__o_fold': no locator available」而失去转换能力。
hiddenimports += ['numba_locator']
# 试听用的本机回环 HTTP 服务。静态 import 本来就能被分析到，这里显式声明是为了
# 万一将来改成函数内 import 也不会静默丢掉（丢了的表现是点播放按钮毫无反应）。
hiddenimports += ['audio_server']
tmp_ret = collect_all('librosa')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('numba')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('llvmlite')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('webview')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('soundfile')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('soxr')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('clr_loader')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pythonnet')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
# 关闭到托盘要用 pystray（win32 后端）+ Pillow 画图标。
# 注意 excludes 里原有 'PIL'，接托盘后必须去掉，否则打包版拿不到 Pillow、
# 托盘会静默降级成「关窗口=真退出」。PIL 的插件子模块 PyInstaller 收不全，
# 所以整包 collect_all；pystray 的 win32 后端是运行时按平台 import 的，也要显式列。
tmp_ret = collect_all('pystray')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('PIL')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
hiddenimports += ['pystray._win32', 'PIL.Image', 'PIL.ImageDraw', 'six']


a = Analysis(
    [os.path.join(REPO, 'tempo_converter_app.py')],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'pandas', 'IPython', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'shiboken2', 'shiboken6', 'qtpy', 'webview.platforms.qt', 'torch', 'dask', 'distributed', 'pyarrow', 'h5py', 'sphinx', 'docutils', 'pytest', 'py', 'zmq', 'lxml', 'cryptography', 'bcrypt', 'jinja2', 'pygments', 'tornado'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='BeatSync',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # exe 自身图标（资源管理器/任务栏/标题栏共用）；ico 缺失则为 None 用默认
    icon=(_icon_ico if os.path.isfile(_icon_ico) else None),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='BeatSync',
)

# 构建后：把 BeatSync.exe.config 放到与 BeatSync.exe 同一层。
# PyInstaller 6 会把 datas 统一塞进 _internal/，而这个配置文件必须紧挨着 exe
# 才会被 .NET 读作宿主配置（它负责开启 loadFromRemoteSources，见文件内注释）。
# 缺失时程序在"带 Mark of the Web 的副本 / 中文路径"下会启动即崩。
_cfg_src = os.path.join(_SPEC_DIR, 'BeatSync.exe.config')
import shutil
if os.path.isfile(_cfg_src):
    shutil.copyfile(_cfg_src, os.path.join(coll.name, 'BeatSync.exe.config'))
    print('已复制 BeatSync.exe.config 到', os.path.join(coll.name, 'BeatSync.exe.config'))
else:
    print('警告：缺少 BeatSync.exe.config —— 打包版在带网络标记或中文路径下会启动失败！')

# 构建后：把中英文 README 一并放到安装目录根部（与 exe 同层），
# 这样离线安装完也能直接在程序目录里看到使用说明；datas 走不进 exe 同层
# （PyInstaller 6 把 datas 全塞 _internal/），所以这里同样显式复制。
# README 开头有 ![icon](assets/icon.svg)，所以要连 assets/ 一起复制到同层，
# 否则装完看到的是一张裂图。
for _doc in ('README.md', 'README.en.md'):
    _doc_src = os.path.join(REPO, _doc)
    if os.path.isfile(_doc_src):
        shutil.copyfile(_doc_src, os.path.join(coll.name, _doc))
        print('已复制', _doc, '到安装目录根部')
    else:
        print('警告：找不到', _doc_src, '—— 安装目录将缺少该说明文档')

# README 里的相对路径 assets/icon.svg 必须能在安装目录根部解析到，
# 否则文档开头的图标是裂图。与 README 同层放一份 assets/。
_assets_src = os.path.join(REPO, 'assets')
if os.path.isdir(_assets_src):
    _assets_dst = os.path.join(coll.name, 'assets')
    os.makedirs(_assets_dst, exist_ok=True)
    for _f in os.listdir(_assets_src):
        _src_f = os.path.join(_assets_src, _f)
        if os.path.isfile(_src_f):
            shutil.copyfile(_src_f, os.path.join(_assets_dst, _f))
    print('已复制 assets/ 到安装目录根部（供 README 引用图标）')
else:
    print('警告：找不到', _assets_src, '—— README 里的图标将显示为裂图')

# README 的「许可证」一节链到 [LICENSE](LICENSE)，所以许可证原文也要与 README 同层，
# 否则装完点那个链接是死链。
_lic_src = os.path.join(REPO, 'LICENSE')
if os.path.isfile(_lic_src):
    shutil.copyfile(_lic_src, os.path.join(coll.name, 'LICENSE'))
    print('已复制 LICENSE 到安装目录根部')
else:
    print('警告：找不到', _lic_src, '—— README 的许可证链接将无法解析')

# 构建后：在安装目录根部放一个双击即可启动兼容界面（tkinter）的 bat。
# 起因：兼容界面与桌面版共用同一个 exe，只在启动时多带 --tk 参数，
# 用户在安装目录里翻不到第二个可执行文件；开始菜单那条快捷方式又不是每台机器都显眼。
# 内容刻意只用 ASCII（cmd 按 ANSI 读 bat，写中文会乱码）。
# start "" 让 bat 立刻返回，不留黑框。
# 文件名刻意用纯 ASCII：中英两份 README 都要引用同一个文件名，
# 用中文名会让英文版文档指向一个不存在的文件。
_bat_name = 'BeatSync-Compatibility.bat'
_bat_body = (
    '@echo off\r\n'
    'cd /d "%~dp0"\r\n'
    'start "" "%~dp0BeatSync.exe" --tk\r\n'
)
try:
    with open(os.path.join(coll.name, _bat_name), 'w', encoding='gbk', newline='') as _f:
        _f.write(_bat_body)
    print('已生成', _bat_name, '到安装目录根部')
except Exception as _e:
    print('警告：生成', _bat_name, '失败：', _e)

