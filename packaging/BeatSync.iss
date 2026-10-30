; BeatSync 安装程序脚本（Inno Setup 6）
; 编译：ISCC.exe BeatSync.iss
; 产物：dist_installer\BeatSyncSetup.exe
;
; 设计要点：
;  1) 默认装到 {localappdata}\Programs\BeatSync（用户目录），不需要管理员权限，
;     而且**可写**——BeatSync.exe 会在自己所在目录写 app_error.log /
;     conversion_report.csv / _ncache，装在 Program Files 下普通用户写不进去。
;     向导仍会给目录选择项，用户可以改。
;  2) PrivilegesRequiredOverridesAllowed=commandline，**不能用 dialog**。Inno 把
;     「为谁安装」对话框硬编码排在语言对话框之前（Setup.MainFunc.pas:3439 早于
;     :3594），一旦放开 dialog，第一屏就永远走「为谁安装」，语言选择排不到第一
;     （用户要求语言选择在第一步）。脚本层也补不回来：PrivilegesRequired 不是
;     PascalScript 可见标识符，写了编译报 `Unknown identifier 'PrivilegesRequired'`。
;     所以这里固定按当前用户安装；批量部署要给所有用户装，用命令行 /ALLUSERS 显式指定。
;  3) 必须把 dist\BeatSync 整个目录打进去，**尤其是 BeatSync.exe.config**——
;     它负责开启 loadFromRemoteSources，缺了它，用户从网上下载安装包后
;     （文件带 Mark of the Web）程序会启动即崩。
;  4) 全程中英双语：安装向导的第一页就是 Inno 内置的语言选择页（这一页固定英文
;     呈现）；选完从欢迎页到完成页、快捷方式名称、卸载程序界面，全部跟随所选语言。
;     所有自定义文案走 [CustomMessages] 的中英成对条目（chinesesimplified.xxx /
;     english.xxx），用 {cm:xxx} 在运行时按当前语言取值——[Messages] 里只能写带
;     语言前缀的覆盖，**不带前缀的是全局覆盖**，会把另一种语言的界面也改掉。

#define MyAppName "BeatSync"
#define MyAppNameCN "BeatSync 音乐速度转换器"
#define MyAppVersion "1.6.0"
#define MyAppExeName "BeatSync.exe"
; AppId 的裸 GUID（不带 [Setup] 里那两个转义花括号），供 [Code] 拼注册表键名用。
#define MyAppId "C09E041D-E804-46F0-BCFA-9C30E400446F"
; 卸载器文件名。Inno 固定生成 unins000.exe + unins000.dat，没有任何指令能改；
; 安装末尾用 [Code] 把这一对文件重命名成下面这个名字，并把注册表卸载入口指过来。
; 注意：必须成对改名——卸载器靠同名 .dat 读卸载日志，只改 exe 会静默什么都不做。
#define MyUninstName "BeatSyncUninstall.exe"

[Setup]
AppId={{C09E041D-E804-46F0-BCFA-9C30E400446F}
; AppName 在两种语言下都显示同一个商品名，避免「开始菜单叫 BeatSync、设置里叫中文长名」的割裂
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=BeatSync
DefaultDirName={localappdata}\Programs\BeatSync
DefaultGroupName=BeatSync
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
; nonadmin = 「为所有用户」这项选择不再询问，永远按最低权限安装到当前用户目录
; （不需要管理员、不弹 UAC）。原因：Inno 把「选择安装模式」对话框硬编码排在语言对话框之前
; （Setup.MainFunc.pas:3439 早于 :3594），只要放开 dialog 选项，第一屏就永远是「为谁安装」，
; 语言选择根本排不到第一；而脚本层改不了 PrivilegesRequired（它不是脚本可见标识符），
; 也没法在选完语言后自己再问一次。两者不可兼得，这里选「语言选择在第一」。
; 需要给所有用户安装的部署方，仍可用命令行 /ALLUSERS 显式指定。
PrivilegesRequiredOverridesAllowed=commandline
OutputDir=dist_installer
OutputBaseFilename=BeatSyncSetup
; 图标在仓库里，本 iss 在 packaging\ 下，相对路径要往上一级走
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
; 覆盖/修复安装时，Inno 会因「版本相同」跳过大量已有文件，被跳过的文件不会写进
; 本次卸载日志 → 卸载后 _internal 残留几百个空目录。Append 模式让日志跨次累积，
; 保证 uninstaller 始终持有完整文件清单，反复覆盖安装后仍能一次卸载干净。
UninstallLogMode=append
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; 语言选择放第一步、且这一项以英语呈现：
;   * ShowLanguageDialog=yes —— 用 Inno 内置语言框，它固定最先弹出；
;   * LanguageDetectionMethod=none —— 初始语言固定取 [Languages] 第一项（english），
;     中文 Windows 上也不会先以中文进入，保证这一页是英语界面；
;   * 内置框的标题/提示取自「当前候选语言」的消息，中文版会显示「选择安装语言」，
;     用下面 [Messages] 里**带语言前缀**的条目把它钉成英文（只覆盖中文那一套，
;     不影响后续页面）。下拉框里的语言名是各 isl 自带的 LanguageName，无法改；
;     中文项显示为「简体中文」，本身可辨识。
;
; 注意：不能用 ShowLanguageDialog=no + 自建页来做——Inno 的脚本层只暴露只读的
; ActiveLanguage: String，切换语言的 SetActiveLanguage 是内部私有过程
; （Setup.MainFunc.pas:2369），自建页选完语言也无法真正切换，整个安装程序会停在初始语言上。
ShowLanguageDialog=yes
LanguageDetectionMethod=none

[Languages]
; 顺序即内置语言框里的顺序，第一个是默认选中项。
; 英文放第一：这一项要按英文呈现，首项与页面文案都是英语才一致；选中中文后全程中文。
; ChineseSimplified.isl 是 Inno 官方仓库 Files\Languages\ChineseSimplified.isl 的副本
; （官方安装包不自带中文，必须随脚本一起放），与本文件同目录，用相对路径引用。
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

; 把内置语言框的文案钉成英文：不带语言前缀的 [Messages] 是全局覆盖，会把选完中文后的
; 欢迎页、进度页也一起改成英文，所以**只能**用 chinesesimplified. 前缀这种逐语言条目。
[Messages]
chinesesimplified.SelectLanguageTitle=Select Setup Language
chinesesimplified.SelectLanguageLabel=Select the language to use during the installation.

[Tasks]
Name: "desktopicon"; Description: "{cm:TaskDesktopIcon}"; GroupDescription: "{cm:TaskGroup}"; Flags: checkedonce

[Files]
Source: "dist\BeatSync\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; 开始菜单 / 桌面上的文字要跟着语言走，所以名称也用 {cm:...}
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autoprograms}\{#MyAppName} ({cm:ShortcutCompatSuffix})"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--tk"; WorkingDir: "{app}"; Comment: "{cm:ShortcutCompatComment}"
Name: "{autoprograms}\{cm:ShortcutUninstall}"; Filename: "{app}\{#MyUninstName}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:RunAfterInstall}"; Flags: nowait postinstall skipifsilent

; 卸载时清掉整个安装目录树。Inno 自带的卸载只删「本次日志里记着的文件」，
; 覆盖/修复安装时上一次的目录骨架会被漏掉，卸载后留下几百个空文件夹。
; uninsneveruninstall 与用户数据无关：程序自己产生的输出都在用户选的输出目录，
; 安装目录里只有程序文件，整树删除是安全的。
[UninstallDelete]
Type: filesandordirs; Name: "{app}"

; ---------------------------------------------------------------------------
; 自定义文案：每种语言一套，{cm:Key} 在运行时按当前语言取。
; 约定：未声明语言前缀的是默认值（英文），带 <语言名>. 前缀的覆盖该语言。
; 这样即使以后再加语言，缺条目也只是回落英文，不会报错。
; ---------------------------------------------------------------------------
[CustomMessages]
; —— 英文（默认值）——
TaskDesktopIcon=Create a &desktop shortcut
TaskGroup=Additional tasks:
ShortcutCompatSuffix=Compatibility
ShortcutCompatComment=Same features, tkinter interface that needs neither .NET nor WebView2
ShortcutUninstall=Uninstall {#MyAppName}
RunAfterInstall=Launch {#MyAppName} now
; 桌面/开始菜单里安装条目本身也带一句说明
AppComment=Batch-convert music to the tempo you need

; —— 简体中文 ——
chinesesimplified.TaskDesktopIcon=创建桌面快捷方式(&D)
chinesesimplified.TaskGroup=附加任务：
chinesesimplified.ShortcutCompatSuffix=兼容界面
chinesesimplified.ShortcutCompatComment=功能一致，且不需 .NET / WebView2 的 tkinter 界面
chinesesimplified.ShortcutUninstall=卸载 {#MyAppName}
chinesesimplified.RunAfterInstall=立即运行 {#MyAppName}
chinesesimplified.AppComment=把音乐批量转成你需要的速度

[Code]
{ Inno 生成的卸载器固定叫 unins000.exe（+ 同名 unins000.dat），没有指令能改。
  这里在文件复制完成后把这一对文件重命名成 BeatSyncUninstall.*，并把注册表里的
  卸载入口（UninstallString / QuietUninstallString）一并指过去，这样：
    * 安装目录里看到的是 BeatSyncUninstall.exe，不再是含义不明的 unins000.exe；
    * 设置 → 应用 里的「卸载」按钮仍然有效（它读的就是 UninstallString）。
  两个文件必须成对改名：卸载器按自身文件名去找同名 .dat 读卸载日志，
  只改 exe 会导致它静默什么都不做（实测：退出码 0 但一个文件都不删）。 }

const
  UNINST_OLD = 'unins000';

procedure RenameUninstaller();
var
  oldExe, oldDat, newExe, newDat, subKey, s: string;
  root: Integer;
begin
  oldExe := ExpandConstant('{app}\' + UNINST_OLD + '.exe');
  oldDat := ExpandConstant('{app}\' + UNINST_OLD + '.dat');
  newExe := ExpandConstant('{app}\{#MyUninstName}');
  newDat := ExpandConstant('{app}\BeatSyncUninstall.dat');

  if not FileExists(oldExe) then
  begin
    Log('RenameUninstaller: 找不到 ' + oldExe + '，跳过');
    Exit;
  end;

  { 覆盖/修复安装时，上次改好名的卸载器还在原地，RenameFile 会因目标已存在而失败，
    结果卸载入口退回 unins000.exe（目录里同时留两套卸载器）。改名先把目标删掉。 }
  if FileExists(newExe) then
  begin
    if DeleteFile(newExe) then
      Log('RenameUninstaller: 已删除旧的 ' + newExe)
    else
      Log('RenameUninstaller: 无法删除旧的 ' + newExe + '（可能被占用）');
  end;

  if not RenameFile(oldExe, newExe) then
  begin
    Log('RenameUninstaller: 卸载器 exe 改名失败，保持 ' + UNINST_OLD + '.exe');
    Exit;
  end;
  Log('RenameUninstaller: exe -> ' + newExe);

  if FileExists(oldDat) then
  begin
    if FileExists(newDat) then
      DeleteFile(newDat);
    if RenameFile(oldDat, newDat) then
      Log('RenameUninstaller: dat -> ' + newDat)
    else
      Log('RenameUninstaller: dat 改名失败（卸载仍可用，但请勿手工删除该文件）');
  end;

  { 注册表入口：最低权限安装写 HKCU，提权安装写 HKLM（64 位视图）。
    键名是 AppId 的裸 GUID 前后各加一对花括号，再加 _is1 后缀。
    用字符串拼接而不是直接写花括号，避免与 Pascal 注释定界符冲突。 }
  subKey := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' +
            '{' + '{#MyAppId}' + '}_is1';
  if IsAdminInstallMode then
    root := HKEY_LOCAL_MACHINE
  else
    root := HKEY_CURRENT_USER;

  if RegQueryStringValue(root, subKey, 'UninstallString', s) then
  begin
    RegWriteStringValue(root, subKey, 'UninstallString', '"' + newExe + '"');
    Log('RenameUninstaller: UninstallString -> ' + newExe);
  end
  else
    Log('RenameUninstaller: 未找到 UninstallString（键 ' + subKey + '）');

  if RegQueryStringValue(root, subKey, 'QuietUninstallString', s) then
  begin
    RegWriteStringValue(root, subKey, 'QuietUninstallString',
                        '"' + newExe + '" /SILENT');
    Log('RenameUninstaller: QuietUninstallString -> ' + newExe);
  end;
end;

procedure CleanupStaleUninstDat();
var
  staleDat: string;
begin
  staleDat := ExpandConstant('{app}\unins000.dat');
  if FileExists(staleDat) then
  begin
    if DeleteFile(staleDat) then
      Log('CleanupStaleUninstDat: removed ' + staleDat)
    else
      Log('CleanupStaleUninstDat: could not remove ' + staleDat);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    RenameUninstaller();
    CleanupStaleUninstDat();
  end;
end;
