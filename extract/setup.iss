; Rheia Inno Setup Script
; Encoding: UTF-8 (no BOM; Inno Setup 6.3+ reads UTF-8 without BOM)

[Setup]
AppName=Rheia
AppVersion=1.0.0
DefaultDirName={code:GetAppDir}
DefaultGroupName=Rheia
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
OutputDir=..\Released
OutputBaseFilename=Rheia-Setup
Compression=lzma2/max
SolidCompression=yes
ShowLanguageDialog=no
WizardStyle=modern
DisableWelcomePage=yes
DisableFinishedPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
DisableDirPage=yes
AlwaysShowComponentsList=no
ShowComponentSizes=no
AllowNoIcons=no
SetupIconFile=Rheia.ico
WizardSmallImageFile=setup_small.png
UninstallDisplayIcon={app}\Rheia.ico
UninstallDisplayName=Rheia

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinese"; MessagesFile: "ChineseSimplified.isl"

[Files]
Source: "card.exe"; Flags: dontcopy
Source: "card.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "uninstall.exe"; DestDir: "{app}"; Flags: ignoreversion
; ⚠ Excludes 的分隔符是**逗号**：写成分号等于一条都没写（Inno 按逗号切分，整串会被当成一个文件名）。
; ⚠ 缓存与数据库类一律排掉（2026-09-21）：`*.db` 原来只写在 kernel 那一行，
;   其余行没有 —— 今天这几棵树里恰好没有 db，但将来谁放一个就会被带进客户包。
;   数据库是**客户机器上跑出来的**（baseline_pool.db 在 %PROGRAMDATA%\Rheia），
;   不该由开发机带过去。故五行统一补上 *.db,*.sqlite,*.sqlite3。
Source: "..\lib\*"; DestDir: "{app}\lib"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__,__pycache__\*,*.pyc,*.pyo,*.bak,*.bak-*,*.incomplete,*.cache,*.db,*.sqlite,*.sqlite3"
Source: "..\kernel\*"; DestDir: "{app}\kernel"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__,__pycache__\*,*.pyc,*.pyo,*.bak,*.bak-*,*.incomplete,*.cache,Official text\*,.clawhub\*,*.db,*.sqlite,*.sqlite3,manifest.signal,*.signal,miss.json,ot.json,ambiguity_candidates.json,mechanisms_raw.json"
Source: "..\installer\*"; DestDir: "{app}\installer"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__,__pycache__\*,*.pyc,*.pyo,*.bak,*.bak-*,*.incomplete,*.cache,*.db,*.sqlite,*.sqlite3"
Source: "..\ui\*"; DestDir: "{app}\ui"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__,__pycache__\*,*.pyc,*.pyo,*.bak,*.bak-*,*.incomplete,*.cache,*.db,*.sqlite,*.sqlite3"
Source: "..\main\*"; DestDir: "{app}\main"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__,__pycache__\*,*.pyc,*.pyo,*.bak,*.bak-*,*.incomplete,*.cache,*.db,*.sqlite,*.sqlite3"
Source: "..\persona\*"; DestDir: "{app}\persona"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__,__pycache__\*,*.pyc,*.pyo,*.bak,*.bak-*,*.incomplete,*.cache,*.db,*.sqlite,*.sqlite3"
; 扩展位已并入 persona 树：声明＋源＋构建产物都在 persona\scripts\（随上一行递归进包），
; 独立的 persona-slot-engine\ 目录不再存在。
; 壳侧扩展位 CLI 已并进 persona 树（2026-09-22）：persona\indivi.py —— 它 import 的全是
; persona.*，摆在 tools\ 下本来就不对（旧包因此在 {app}\tools\ 里留下一个只剩一个文件的目录）。
; 现在它随上面那行 `..\persona\*` 递归进包，不需要再有专门的 Source 行。

; No vendor: Python runtime + PySide6 downloaded at install time into {app}\python\

Source: "Rheia.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\kernel\large\typeSpace.json"; DestDir: "{app}\kernel\large"; Flags: onlyifdoesntexist
; 2026-10-29：只随包带**解释器**（executor.py，空工具，无内容）；slot.key / RheiaPersonalSlot 一律不进包
Source: "..\executor.py"; DestDir: "{app}"; Flags: ignoreversion

; Python runtime is shared at C:\Program Files\PythonSide6 (not inside {app}).
; download_python.ps1 writes there; uninstall never touches it.

[Icons]
Name: "{group}\Rheia"; Filename: "{code:GetPythonExe}"; Parameters: """{app}\main\main.py"""; WorkingDir: "{app}"; IconFilename: "{app}\Rheia.ico"; Tasks:
Name: "{group}\Uninstall Rheia"; Filename: "{app}\uninstall.exe"; WorkingDir: "{app}"; IconFilename: "{app}\Rheia.ico"; Tasks:

[Registry]
; 覆盖 Inno 默认的 UninstallString，让「控制面板→卸载」也走我们的深色卡片 uninstall.exe，
; 而不是 unins000.exe 的原生大框子。
Root: HKLM; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Rheia_is1"; ValueType: string; ValueName: "UninstallString"; ValueData: """{app}\uninstall.exe"""; Flags: uninsdeletevalue

; ── 2026-10-15：禁用「全屏优化」——阻止窗口被切进 direct-flip 直扫路 ──
;   依据：QtWebEngine 6.11.2 + NVIDIA 在这条路上会周期性撕裂/锯齿闪（同机同卡 Chrome 不闪）；
;   实测：同一个裸窗，小窗不闪、最大化/全屏就闪 ⇒ 病在"那条呈现路"的同步，不在壳。
;   目标 = 壳的宿主 pythonw.exe（与制卡机共用）；卸载时删掉这个值。
;   ↑ 2026-10-28：这条已移出 [Registry] —— 提权安装时 HKCU 是提权账户的 hive，会写到别人名下；
;   改由 [Run] / [UninstallRun] 以 runasoriginaluser 调 reg.exe 写/删（见下面两节）。
Root: HKLM; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Rheia_is1"; ValueType: string; ValueName: "QuietUninstallString"; ValueData: """{app}\uninstall.exe"" /SILENT"; Flags: uninsdeletevalue

[Run]
; 2026-10-28：禁用「全屏优化」的兼容性旗标 —— 必须由「登录用户」写 HKCU；
;   提权安装时进程自己的 HKCU 不是桌面那位用户，所以不能走 [Registry]。
Filename: "{sys}\reg.exe"; Parameters: "add ""HKCU\Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers"" /v ""{code:GetPythonExe}"" /t REG_SZ /d DISABLEDXMAXIMIZEDWINDOWEDMODE /f"; Flags: runasoriginaluser runhidden waituntilterminated; Check: PythonExeExists
; ⚠ **第 0 件事：先把已有的配置包相关插件摘掉**（2026-09-20 设计约定：
;   「新包封装一条命令 —— 卸载已有配置包相关插件」）。
;   旧包（或上一次装剩）的插件会在 WSL 里继续挂着跑：卸载 Rheia 只删 Windows 侧，
;   插件目录 + openclaw.json 里那条 enabled 全留着，实测卸载完还在每 30 秒读一次指针。
;   所以装新包时先摘干净，再由 install.py 重新注入新插件（一句命令：--uninstall-plugin）。
;   「配置包相关插件」归壳侧管，卡侧不掺和；WSL 里没 Ubuntu（新机）⇒ 这条跳过。
;   输出落 %TEMP%\rheia-slot-plugin.log（install.py 的动静在 install.log，这一条单独留痕，
;   好不好使、摘没摘掉都查得到 —— 别让它成了"看不见的一步"）。
Filename: "{sys}\cmd.exe"; Parameters: "/c {sys}\wsl.exe -d Ubuntu -u root -- python3 ""{code:WslSlotReader}"" --uninstall-plugin >> ""%TEMP%\rheia-slot-plugin.log"" 2>&1"; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; Check: ShouldUninstallOldPlugin
; Step 1: download Python runtime into C:\Program Files\PythonSide6 (shared, not in {app})
; Step 2: python pythonw runs install.py headless (card.exe covers the whole progress)
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\installer\download_python.ps1"" -AppDir ""{app}"""; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; Check: ShouldRunInstall
Filename: "{code:GetPythonExe}"; Parameters: """{app}\installer\install.py"""; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; Check: ShouldRunInstall

[UninstallRun]
; 2026-10-28：对称删除上面 [Run] 写的那条旗标。
;   （[UninstallRun] 不支持 runasoriginaluser，这里退化为「谁提权谁删」，与旧行为一致。）
Filename: "{sys}\reg.exe"; Parameters: "delete ""HKCU\Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers"" /v ""{code:GetPythonExe}"" /f"; Flags: runhidden waituntilterminated; RunOnceId: "ClearAppCompatLayer"
; ⚠ 卸载流程里**一个 WSL 动作都不在这里**（2026-09-21 设计约定）：
;   · **弹配置包**是业务所需，但必须**光明正大**：它现在在 `extract\uninstall.c` 自己的窗口里跑
;     —— 客户看得见「正在弹出配置包…」、且**前置**在清任何东西之前；
;     没载入 / WSL 不在 ⇒ 一步都不走（不白起 WSL、不占卸载进程）。
;   · 原来这里挂着两条 `wsl.exe`（--eject-slot / --uninstall-plugin）：`runhidden` 客户看不见、
;     **绕过了 uninstall.c 的 `g_keepOpenclaw` 那道门**（勾了「仅卸载 Rheia」也照样动 WSL），
;     而且位置上**排在本 exe 之后** —— Inno 文档说 [UninstallRun] 是"卸载的第一步"没错，
;     但那说的是 **unins000 自己那趟**；本包的 WSL 清理是 `uninstall.exe` 在调 unins000 **之前**
;     就做掉的，于是挂在这里的"弹配置包"落在 WSL/宿主已经被清掉之后：配置包侧那一趟要"收"回来
;     的东西（她那边的会话与语料、{app}\persona、工作区那份）全没了 ⇒ **数据收不回配置包**
;     （2026-09-21 原话：「卸载后的配置包数据能存回配置包吗？」）。还会把卸载器占住很久
;     （实测「卸载结束不了」）。
;   · WSL 侧的账只由 `uninstall.c` 按 `g_keepOpenclaw` 判断（不经过信号文件、也不经过本文件）。
; 本文件只做 **Windows 侧**：`{commonappdata}\Rheia` + `{app}` + 清 RunOnce。
; vhdx 已迁到 C:\wsl-distro（不在 ProgramData 下），此目录无客户资产，
; 直接整目录 rmdir 彻底清空（含 active_persona.json、各类 .done 标记、残留 wsl-distro），
; 安装时会重新生成。全量卸载与「仅卸载 Rheia」都走这里，两种模式都该清干净。
Filename: "{sys}\cmd.exe"; Parameters: "/c rmdir /s /q ""{commonappdata}\Rheia"""; Flags: runhidden; RunOnceId: "CleanProgramData"
Filename: "{sys}\reg.exe"; Parameters: "delete HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce /v Rheia-Resume /f"; Flags: runhidden; RunOnceId: "CleanRunOnce"
; Fallback: delete entire {app} (incl. any runtime residue); baseline_pool.db already in PROGRAMDATA
Filename: "{sys}\cmd.exe"; Parameters: "/c rmdir /s /q ""{app}"""; Flags: runhidden; RunOnceId: "CleanAppDir"
; ⚠ **WSL 的账不在这里算**（ 2026-09-20 令，两个行为要分开）：
;   · **不勾**「仅卸载 Rheia」⇒ 连 WSL 一起清（terminate → unregister → 删 vhdx）**是对的**；
;   · **勾了**「仅卸载 Rheia」⇒ **完全不碰 WSL/Ubuntu/OpenClaw**（那是客户资产）。
;   这两条由 `extract\uninstall.c` 自己按 `g_keepOpenclaw` 判断，**不经过信号文件、
;   也不经过本文件**（那里的注释原话：「不经过信号文件/unins000 间接层」）。
;   所以本文件里**一个字节都不许碰 WSL** —— 一碰，勾了「仅卸载 Rheia」的客户也被动了。
;   壳留下的 wsl.exe 孤儿由**壳侧**根治：保活子进程绑"父死子亡"的作业对象
;   （`main\main.py:_bind_child_to_parent`）+ `_kill_process_tree`。

[InstallDelete]
; ⚠ 旧布局残留（2026-09-22）：壳侧扩展位 CLI 原来摆在 {app}\tools\（旧名 slot_reader.py，
;   现为 persona\indivi.py）。升级/重装时把这个目录清掉 —— 否则新包装完，装机目录里会一直留着
;   一个只剩一个文件的 tools\（客户看到的就是"多出来一个目录"）。
; 只删这一个目录；tools\ 下从来没有别的东西随包发布过。
Type: filesandordirs; Name: "{app}\tools"
; 旧包残留：付费模块曾随旧壳包铺到 {app}\personal-slot；新壳包不含它，
;   升级/重装时清掉。
Type: filesandordirs; Name: "{app}\personal-slot"
; 2026-10-30：第二道红线哨兵已整体移除（未接电的死代码）；升级时清掉旧包留下的这一份。
Type: files; Name: "{app}\main\license_anchor.py"

[UninstallDelete]
; baseline_pool.db 已迁移到 %PROGRAMDATA%（由 [UninstallRun] 的 rmdir 一并清除），{app} 无数据
; 独立 Python 目录 C:\Program Files\PythonSide6 不在此列 —— 卸载绝不碰它。
Type: files; Name: "{app}\Rheia.ico"
Type: files; Name: "{app}\uninstall.exe"
Type: files; Name: "{app}\card.exe"
Type: filesandordirs; Name: "{app}\installer"
Type: filesandordirs; Name: "{app}\kernel"
Type: filesandordirs; Name: "{app}\lib"
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\main"
Type: filesandordirs; Name: "{app}\ui"

[Code]
const
  { 深色主题色值，与 card 视觉规格逐一对齐（TColor 为 BGR 顺序） }
  CLR_BG = $1A1A1A;        { 背景 #1a1a1a }
  CLR_PANEL = $2A2A2A;     { 面板/输入框 #2a2a2a }
  CLR_BORDER = $444444;    { 边框 #444444 }
  CLR_TEXT = $E0E0E0;      { 主文字 #e0e0e0 }
  CLR_TEXT_MUTED = $A0A0A0;{ 次要文字 #a0a0a0 }
  CLR_ACCENT = $2170F3;    { 橙色 #f37021（BGR 反转） }

var
  gInstallDone: Boolean;
  gLastPct: Integer;
  gBootstrapper: Boolean;  { True = 引导实例，退出时不写 EXIT（否则秒杀刚启动的 card） }
  gAppDir: String;         { card 选定的安装目录 }
  gShellOnly: Boolean;     { card 勾选「仅装壳」 }

function GetInputPath: String;
begin
  Result := GetEnv('TEMP') + '\okoc_card_input.txt';
end;

function GetAppDir(Param: String): String;
{ DefaultDirName 的 code:GetAppDir 回调。返回 card 选定的目录（尚未确定时用默认值）。 }
begin
  if gAppDir = '' then
    Result := ExpandConstant('{autopf}\Rheia')
  else
    Result := gAppDir;
end;

function PythonExeExists(): Boolean;
{ [Registry] 用：只有在共享 Python 宿主（壳的 pythonw.exe）存在时，才写那条「禁用全屏优化」的兼容性旗标。 }
begin
  Result := FileExists(ExpandConstant('{autopf}\PythonSide6\pythonw.exe'));
end;

function GetPythonExe(Param: String): String;{ 独立 Python 运行时目录（与制卡机共用），返回 pythonw.exe 完整路径。
  卸载绝不碰这个目录，重装时 download_python.ps1 检测版本决定是否重下。 }
begin
  Result := ExpandConstant('{autopf}\PythonSide6\pythonw.exe');
end;

function WslSlotReader(Param: String): String;
// 返回安装目录里 persona\indivi.py 在 WSL 里的可见路径（卸载前弹配置包要用它）。
//
// 为什么要在 Pascal 里换算：wsl.exe 里跑的是 Linux 的 python3，认的是
// /mnt/c/... 这种路径；而安装目录是客户选的（DefaultDirName 走 card），
// 不能把 C:\Program Files\Rheia 写死。
// ⚠ 注释里不许出现花括号常量（Inno 的 Pascal 注释就是花括号，会提前闭合）。
var
  d: String;
begin
  d := ExpandConstant('{app}');
  if (Length(d) >= 2) and (d[2] = ':') then
    d := '/mnt/' + LowerCase(d[1]) + Copy(d, 3, Length(d) - 1);
  StringChangeEx(d, '\', '/', True);
  Result := d + '/persona/indivi.py';
end;

function ShouldRunInstall: Boolean;
{ 取消标记存在时跳过 [Run]（避免 Abort 回滚删了文件后 [Run] 还执行导致找不到文件） }
begin
  Result := not FileExists(GetEnv('TEMP') + '\Rheia_cancel.flag');
end;

function WslDistroReady: Boolean;
{ WSL 里 Ubuntu 在不在（新机第一步才是装 WSL，摘旧插件那条该跳过）。
  直接问 wsl.exe 一句，不认它内部的实现差异。 }
var
  rc: Integer;
begin
  Result := False;
  if not FileExists(ExpandConstant('{sys}\wsl.exe')) then
    Exit;
  if Exec(ExpandConstant('{sys}\wsl.exe'), '-d Ubuntu -u root -- true', '',
          SW_HIDE, ewWaitUntilTerminated, rc) then
    Result := (rc = 0);
end;

function ShouldUninstallOldPlugin: Boolean;
{ 装之前摘旧插件这一条：既没取消安装，WSL 里也确实有 Ubuntu。 }
begin
  Result := ShouldRunInstall and WslDistroReady;
end;

procedure ApplyDarkTheme;
begin
  { 安装向导窗体深色化：背景 + 文字 + 主面板 }
  WizardForm.Color := CLR_BG;
  WizardForm.Font.Color := CLR_TEXT;
  WizardForm.MainPanel.Color := CLR_BG;
  WizardForm.InnerPage.Color := CLR_BG;

  { 页面标题 / 描述文字 }
  WizardForm.PageNameLabel.Font.Color := CLR_TEXT;
  WizardForm.PageDescriptionLabel.Font.Color := CLR_TEXT_MUTED;

  { 路径选择页：输入框 + 标签 }
  WizardForm.DirEdit.Color := CLR_PANEL;
  WizardForm.DirEdit.Font.Color := CLR_TEXT;
  WizardForm.SelectDirLabel.Font.Color := CLR_TEXT_MUTED;
  WizardForm.SelectDirBrowseLabel.Font.Color := CLR_TEXT_MUTED;

  { 各页面提示标签统一深色（Welcome/SelectDir/Ready 等） }
  WizardForm.WelcomeLabel1.Font.Color := CLR_TEXT;
  WizardForm.WelcomeLabel2.Font.Color := CLR_TEXT_MUTED;
end;

procedure ApplyDarkThemeUninstall;
begin
  { 控制面板卸载进度窗体深色化 }
  UninstallProgressForm.Color := CLR_BG;
  UninstallProgressForm.Font.Color := CLR_TEXT;
end;

procedure InitializeUninstallProgressForm;
begin
  ApplyDarkThemeUninstall;
end;

function GetSignalPath: String;
begin
  Result := GetEnv('TEMP') + '\okoc_signal.txt';
end;

function GetProgressPath: String;
begin
  Result := GetEnv('TEMP') + '\okoc_inno_progress.txt';
end;

procedure WriteSignal(const Text: String; const Pct: Integer; const Mark: String);
{ 向统一 signal 日志追加一行「文案 | 进度值 | complete/done」，UTF-8 编码。
  用 SaveStringsToUTF8File 保证与 install.py/ps1 的 UTF-8 一致，card 用 CP_UTF8 读。 }
var
  line: String;
  arr: TArrayOfString;
begin
  line := Text + ' | ' + IntToStr(Pct) + ' | ' + Mark;
  SetArrayLength(arr, 1);
  arr[0] := line;
  SaveStringsToUTF8File(GetSignalPath, arr, True);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
{ 卸载进度由 uninstall.c 自己写 signal（C 代码 UTF-8 编码可靠），
  Inno 的 Pascal 字符串是 UTF-16，写 signal 会乱码，故此处不再写。 }
begin
end;

function InitializeSetup: Boolean;
var
  rc: Integer;
  input: String;
  content: AnsiString;
  s: String;
  pipePos: Integer;
  dirPart: String;
  shellPart: String;
begin
  Result := True;
  gInstallDone := False;
  gLastPct := -1;
  gBootstrapper := False;
  gAppDir := '';
  gShellOnly := False;


  if not WizardSilent() then
  begin
    { 引导实例（非静默）：只收集 card 的值，然后拉起 /VERYSILENT 静默实例，
      自己 Result:=False 退出。InitializeSetup 返回 False 时向导窗体尚未创建 → 无框子。
      静默实例全程无 Inno UI。card 只显示、不实例化。 }
    if FileExists(GetProgressPath) then
      DeleteFile(GetProgressPath);
    if FileExists(GetEnv('TEMP') + '\Rheia_cancel.flag') then
      DeleteFile(GetEnv('TEMP') + '\Rheia_cancel.flag');
    if FileExists(GetInputPath) then
      DeleteFile(GetInputPath);
    { 清空 signal 统一日志——直接删文件，杜绝上次残留的 done 秒杀刚启动的 card }
    if FileExists(GetSignalPath) then
      DeleteFile(GetSignalPath);

    ExtractTemporaryFile('card.exe');

    { card --select：收集目录+仅装壳布尔，并把 srcexe 路径传给它，
      card 启动静默 setup 后写文件退出，引导实例读文件后 Result:=False 退出。 }
    Exec(ExpandConstant('{tmp}\card.exe'),
         ExpandConstant('--select "{srcexe}"'), '', SW_SHOW,
         ewWaitUntilTerminated, rc);

    input := GetInputPath;
    if FileExists(input) and LoadStringFromFile(input, content) then
    begin
      s := content;
      if Pos('CANCEL', s) = 1 then
        Result := False
      else
      begin
        pipePos := Pos('|', s);
        if pipePos > 0 then
        begin
          dirPart := Copy(s, 1, pipePos - 1);
          shellPart := Copy(s, pipePos + 1, Length(s));
          if dirPart <> '' then
            gAppDir := dirPart;
          gShellOnly := (shellPart = '1');
        end;
      end;
    end
    else
    begin
      Result := False;
    end;

    if Result then
    begin
      if gShellOnly then
        SaveStringToFile(GetEnv('TEMP') + '\Rheia_shellonly.flag', '1', False)
      else
        DeleteFile(GetEnv('TEMP') + '\Rheia_shellonly.flag');

      { 引导实例只负责启动 card 选路径（已传 srcexe 路径），选完 card 自己启动静默实例。
        引导实例 Result:=False 退出，向导窗体未创建，无框子。 }
      gBootstrapper := True;
      Result := False;
    end;
  end
  else
  begin
    { 静默实例（/VERYSILENT）：无 Inno UI。启动 card --progress，继续解压+下载+安装。 }
    ExtractTemporaryFile('card.exe');
    Exec(ExpandConstant('{tmp}\card.exe'), '--progress', '', SW_SHOW, ewNoWait, rc);
  end;
end;

procedure InitializeWizard;
begin
  WizardForm.Visible := False;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  { Inno 在进入 wpInstalling 时会重新 Show 窗体，每个页面切换点都强制 invisible }
  WizardForm.Visible := False;
end;

procedure OverwriteUninstallString;
{ Inno 在成功安装的最后阶段会自动写 UninstallString=unins000.exe（原生大框子），
  覆盖掉 [Registry] 段的值。必须在 ssDone 里最后再覆写一次，指向 uninstall.exe。 }
var
  key: String;
begin
  key := 'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Rheia_is1';
  RegWriteStringValue(HKEY_LOCAL_MACHINE, key, 'UninstallString',
                      ExpandConstant('"{app}\uninstall.exe"'));
  RegWriteStringValue(HKEY_LOCAL_MACHINE, key, 'QuietUninstallString',
                      ExpandConstant('"{app}\uninstall.exe" /SILENT'));
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  WizardForm.Visible := False;
  { 解压完成，交接给下载阶段（ps1 从 6 继续） }
  if CurStep = ssPostInstall then
    SaveStringToFile(GetProgressPath, '5', False);
  if CurStep = ssDone then
  begin
    OverwriteUninstallString;
    gInstallDone := True;
  end;
end;

procedure CurInstallProgressChanged(CurProgress, MaxProgress: Integer);
var
  pct: Integer;
begin
  if MaxProgress <= 0 then Exit;
  WizardForm.Visible := False;
  { 解压阶段占整体 0-5% }
  pct := CurProgress * 5 / MaxProgress;
  if pct <> gLastPct then
  begin
    SaveStringToFile(GetProgressPath, IntToStr(pct), False);
    WriteSignal('__unpack__', pct, 'complete');
    gLastPct := pct;
  end;
end;

procedure DeinitializeSetup;
begin
  { 引导实例主动退出：不写 EXIT（写了会秒杀刚启动的 card）；真失败/中止才写 EXIT }
  if (not gInstallDone) and (not gBootstrapper) then
    SaveStringToFile(GetProgressPath, 'EXIT', False);
end;

procedure CancelButtonClick(CurPageID: Integer; var Cancel, Confirm: Boolean);
begin
  { 无 Inno UI：引导实例已退出、静默实例无窗口，此事件不会被触发 }
  Confirm := False;
end;
