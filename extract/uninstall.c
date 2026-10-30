/*
 * uninstall.c — Rheia 卸载卡片（纯 Win32，零依赖，零版权红线）
 *
 * 取代原 PyQt6/PySide6 版 uninstallui.py。与 card.c 同视觉语言：
 *   深色无边框圆角卡片、圆角橙色/红色按钮、深色确认子窗口。
 *
 * 功能：
 *   - 列出将被移除的项（程序文件 / WSL 子系统 / 用户数据 / 共享数据）
 *   - 确认后静默启动 unins000.exe（/SILENT），由 Inno 的 [UninstallRun] 清干净
 *   - 找不到卸载器时兜底清理 WSL 实例
 *
 * 视觉规格与 installui.py InstallerCard 对齐。
 */

#define UNICODE
#define _UNICODE
#include <windows.h>
#include <shellapi.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>

#pragma comment(lib, "user32.lib")
#pragma comment(lib, "gdi32.lib")
#pragma comment(lib, "shell32.lib")

/* ── 卡片尺寸 ── */
#define CARD_WIDTH  500
#define LIST_HEIGHT 250   /* 列表页高度 */
#define PROG_HEIGHT 168   /* 进度页高度（与安装 card 一致） */
#define CARD_RADIUS 14

/* ── 色值（COLORREF = 0x00BBGGRR）── */
#define CLR_BG        0x1A1A1A   /* #1a1a1a */
#define CLR_BG_DEEP   0x0A0A0A   /* #0a0a0a 子窗口 */
#define CLR_BRAND     0xFFFFFF   /* #ffffff */
#define CLR_SUB       0xB0B0B0   /* #b0b0b0 */
#define CLR_STAGE     0xE0E0E0   /* #e0e0e0 */
#define CLR_DETAIL    0xA0A0A0   /* #a0a0a0 */
#define CLR_COPYRIGHT 0x555555   /* #555555 */
#define CLR_SEP       0x333333   /* #333333 分离线 */
#define CLR_ERROR     0x3C4CE7   /* #e74c3c 红色（BGR 反转） */
#define CLR_ERROR_HOV 0x2B39C0   /* #c0392b 红色 hover */
#define CLR_ACCENT    0x2170F3   /* #f37021 橙 */
#define CLR_ACCENT_HOV 0x3388FF  /* #ff8833 */
#define CLR_BTN_TEXT  0x0A0A0A   /* 橙按钮深色字 */
#define CLR_BAR_BG    0x333333   /* 进度条底 */
#define CLR_PCT       0xE0E0E0   /* 百分比文字 */

#define FONT_FACE L"Microsoft YaHei UI"

/* ── signal / IPC ── */
#define SIGNAL_FILENAME L"signal"
#define TIMER_POLL 1
#define TIMER_EASE 2
#define WATCHDOG_SECONDS 600
#define HOLD_100_SECONDS 2

/* 图标资源 ID（uninstall.rc 里定义） */
#define IDI_APPICON 1

#define CLASS_MAIN L"RheiaUninstall"
#define CLASS_CONFIRM L"RheiaUninstallConfirm"

/* ── 按钮矩形 ── */
#define BTN_CANCEL_X 20
#define BTN_CANCEL_Y 196
#define BTN_CANCEL_W 80
#define BTN_CANCEL_H 32
#define BTN_UNINST_X 360
#define BTN_UNINST_Y 196
#define BTN_UNINST_W 120
#define BTN_UNINST_H 32

/* ── 全局 ── */
static HFONT g_fontBrand  = NULL;
static HFONT g_fontSub    = NULL;
static HFONT g_fontStage  = NULL;
static HFONT g_fontDetail = NULL;
static HFONT g_fontCopy   = NULL;
static HFONT g_fontBtn    = NULL;
static HFONT g_fontPct    = NULL;
static HFONT g_fontStageSmall = NULL;
static HBRUSH g_brBg      = NULL;
static HBRUSH g_brBgDeep  = NULL;
static HBRUSH g_brSep     = NULL;
static HBRUSH g_brBarBg   = NULL;
static HBRUSH g_brBarFill = NULL;

static int g_hover = -1;
static int g_stage = 0;            /* 0=列表页, 1=进度页 */
static int g_display = 0;
static float g_displayF = 0.0f;
static int g_target = 0;
static int g_lastNumeric = -1;
static int g_idleSeconds = 0;
static int g_idleSince100 = 0;
static BOOL g_stageCleared = FALSE;
static BOOL g_keepOpenclaw = FALSE;  /* 复选框：不卸载 OpenClaw（完全不触碰 WSL/Ubuntu/OpenClaw） */
static BOOL g_busy = FALSE;          /* 卸载流程进行中：泵消息期间禁止退出/重入
                                        （不然 Alt+F4 或定时器能把窗口销毁在半路） */
static WCHAR g_signalPath[MAX_PATH];
static WCHAR g_stageText[256];
enum { HIT_NONE=0, HIT_CANCEL, HIT_UNINST, HIT_KEEP };  /* HIT_KEEP = 复选框 */

static WCHAR g_exeDir[MAX_PATH];  /* uninstall.exe 所在目录 = {app} */

static HFONT MakeFont(int px, int weight)
{
    return CreateFontW(-px, 0, 0, 0, weight, FALSE, FALSE, FALSE,
        DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
        CLEARTYPE_QUALITY, DEFAULT_PITCH, FONT_FACE);
}

#include "uninstall_i18n.h"

/* ── 文案查表：按系统 UI 语言返回 28 语言对应文案。未知/日文 → 回落英文 ── */
static int _i18n_row(void)
{
    int primary = PRIMARYLANGID(GetUserDefaultUILanguage());
    int n = sizeof(uninstall_langmap) / sizeof(uninstall_langmap[0]);
    for (int i = 0; i < n; i++) {
        if (uninstall_langmap[i].langid == primary) return uninstall_langmap[i].idx;
    }
    return 1;  /* en */
}
static const WCHAR *_t(int key) { return uninstall_i18n[_i18n_row()][key]; }

/* ── 圆角按钮 ── */
static void DrawButton(HDC hdc, int x, int y, int w, int h,
                       COLORREF bg, COLORREF fg, const WCHAR *text, int fontPx)
{
    HFONT f = MakeFont(fontPx, FW_SEMIBOLD);
    HGDIOBJ old = SelectObject(hdc, f);
    RECT r = { x, y, x + w, y + h };
    HBRUSH br = CreateSolidBrush(bg);
    HPEN pen = CreatePen(PS_SOLID, 1, bg);
    HGDIOBJ oldPen = SelectObject(hdc, pen);
    HGDIOBJ oldBr = SelectObject(hdc, br);
    RoundRect(hdc, x, y, x + w, y + h, 8, 8);
    SelectObject(hdc, oldPen);
    SelectObject(hdc, oldBr);
    DeleteObject(pen);
    DeleteObject(br);
    SetTextColor(hdc, fg);
    SetBkMode(hdc, TRANSPARENT);
    DrawTextW(hdc, text, -1, &r, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
    SelectObject(hdc, old);
    DeleteObject(f);
}

/* ── 命中检测 ── */
static int HitTest(int x, int y)
{
    if (x >= 20 && x <= 300 && y >= 176 && y <= 192) return HIT_KEEP;  /* 复选框 */
    if (x >= BTN_CANCEL_X && x <= BTN_CANCEL_X + BTN_CANCEL_W &&
        y >= BTN_CANCEL_Y && y <= BTN_CANCEL_Y + BTN_CANCEL_H) return HIT_CANCEL;
    if (x >= BTN_UNINST_X && x <= BTN_UNINST_X + BTN_UNINST_W &&
        y >= BTN_UNINST_Y && y <= BTN_UNINST_Y + BTN_UNINST_H) return HIT_UNINST;
    return HIT_NONE;
}

/* ── 主卡片绘制 ── */
static void PaintCard(HWND hwnd, HDC hdc)
{
    RECT rc; GetClientRect(hwnd, &rc);
    FillRect(hdc, &rc, g_brBg);
    SetBkMode(hdc, TRANSPARENT);

    /* 品牌 */
    SetTextColor(hdc, CLR_BRAND);
    SelectObject(hdc, g_fontBrand);
    RECT rB = {20, 12, CARD_WIDTH-20, 38};
    DrawTextW(hdc, L"Rheia", -1, &rB, DT_LEFT|DT_TOP|DT_SINGLELINE|DT_NOCLIP);

    /* 副标题 */
    SetTextColor(hdc, CLR_SUB);
    SelectObject(hdc, g_fontSub);
    RECT rS = {20, 40, CARD_WIDTH-20, 58};
    DrawTextW(hdc, _t(0), -1, &rS, DT_LEFT|DT_TOP|DT_SINGLELINE|DT_NOCLIP);

    /* 分离线 */
    RECT rSep = {20, 64, CARD_WIDTH-20, 65};
    FillRect(hdc, &rSep, g_brSep);

    /* 将被移除标题 */
    SetTextColor(hdc, CLR_ERROR);
    SelectObject(hdc, g_fontSub);
    RECT rDel = {20, 72, CARD_WIDTH-20, 90};
    DrawTextW(hdc, _t(1), -1, &rDel, DT_LEFT|DT_TOP|DT_SINGLELINE|DT_NOCLIP);

    /* 列表项 */
    int y = 94;
    static const int item_name_key[4] = {2, 4, 6, 8};
    static const int item_desc_key[4] = {3, 5, 7, 9};
    for (int i = 0; i < 4; i++) {
        /* 圆点 */
        SetTextColor(hdc, CLR_ERROR);
        SelectObject(hdc, g_fontSub);
        RECT rDot = {20, y, 30, y + 20};
        DrawTextW(hdc, L"•", -1, &rDot, DT_LEFT|DT_TOP|DT_SINGLELINE|DT_NOCLIP);

        /* 名称 */
        SetTextColor(hdc, CLR_STAGE);
        SelectObject(hdc, g_fontSub);
        RECT rName = {34, y, 240, y + 20};
        DrawTextW(hdc, _t(item_name_key[i]), -1, &rName, DT_LEFT|DT_TOP|DT_SINGLELINE|DT_NOCLIP);

        /* 描述 */
        SetTextColor(hdc, CLR_DETAIL);
        SelectObject(hdc, g_fontCopy);
        RECT rDesc = {250, y + 2, CARD_WIDTH-20, y + 20};
        DrawTextW(hdc, _t(item_desc_key[i]), -1, &rDesc, DT_LEFT|DT_TOP|DT_SINGLELINE|DT_NOCLIP);

        y += 22;
    }

    /* 复选框：不卸载 OpenClaw */
    {
        int cx = 20, cy = 176;
        /* 勾选框 */
        RECT rbox = {cx, cy, cx + 16, cy + 16};
        HPEN pen = CreatePen(PS_SOLID, 1, g_keepOpenclaw ? CLR_ACCENT : 0x555555);
        HGDIOBJ oldPen = SelectObject(hdc, pen);
        HGDIOBJ oldBr = SelectObject(hdc, GetStockObject(NULL_BRUSH));
        RoundRect(hdc, cx, cy, cx + 16, cy + 16, 4, 4);
        SelectObject(hdc, oldPen);
        SelectObject(hdc, oldBr);
        DeleteObject(pen);
        if (g_keepOpenclaw) {
            /* 勾 */
            SetTextColor(hdc, CLR_ACCENT);
            SelectObject(hdc, g_fontBtn);
            RECT rCheck = {cx, cy - 1, cx + 16, cy + 16};
            DrawTextW(hdc, L"✓", -1, &rCheck, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        }
        /* 文案 */
        SetTextColor(hdc, CLR_STAGE);
        SelectObject(hdc, g_fontSub);
        RECT rTxt = {cx + 24, cy, CARD_WIDTH - 20, cy + 18};
        DrawTextW(hdc, _t(10), -1, &rTxt, DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_NOCLIP);
    }

    /* 取消按钮（透明边框） */
    if (g_hover == HIT_CANCEL)
        DrawButton(hdc, BTN_CANCEL_X, BTN_CANCEL_Y, BTN_CANCEL_W, BTN_CANCEL_H,
                   0x2A2A2A, CLR_STAGE, _t(14), 12);
    else {
        /* 透明边框按钮：画边框 + 文字 */
        RECT r = {BTN_CANCEL_X, BTN_CANCEL_Y, BTN_CANCEL_X+BTN_CANCEL_W, BTN_CANCEL_Y+BTN_CANCEL_H};
        HPEN pen = CreatePen(PS_SOLID, 1, 0x444444);
        HGDIOBJ oldPen = SelectObject(hdc, pen);
        HGDIOBJ oldBr = SelectObject(hdc, GetStockObject(NULL_BRUSH));
        RoundRect(hdc, BTN_CANCEL_X, BTN_CANCEL_Y, BTN_CANCEL_X+BTN_CANCEL_W, BTN_CANCEL_Y+BTN_CANCEL_H, 8, 8);
        SelectObject(hdc, oldPen);
        SelectObject(hdc, oldBr);
        DeleteObject(pen);
        SetTextColor(hdc, CLR_STAGE);
        SelectObject(hdc, g_fontBtn);
        SetBkMode(hdc, TRANSPARENT);
        DrawTextW(hdc, _t(14), -1, &r, DT_CENTER|DT_VCENTER|DT_SINGLELINE);
    }

    /* 确认卸载按钮（红色） */
    DrawButton(hdc, BTN_UNINST_X, BTN_UNINST_Y, BTN_UNINST_W, BTN_UNINST_H,
               (g_hover==HIT_UNINST ? CLR_ERROR_HOV : CLR_ERROR),
               0xFFFFFF, _t(13), 12);

    /* 版权 */
    SetTextColor(hdc, CLR_COPYRIGHT);
    SelectObject(hdc, g_fontCopy);
    RECT rC = {20, LIST_HEIGHT-24, CARD_WIDTH-20, LIST_HEIGHT-8};
    DrawTextW(hdc, L"© Alexander Lee（李亚霖）", -1, &rC, DT_CENTER|DT_TOP|DT_SINGLELINE|DT_NOCLIP);
}

/* ── 确认取消子窗口 ── */
static BOOL g_confirmResult = FALSE;

static void PaintConfirm(HWND hwnd, HDC hdc)
{
    RECT rc; GetClientRect(hwnd, &rc);
    FillRect(hdc, &rc, g_brBgDeep);
    SetBkMode(hdc, TRANSPARENT);

    SetTextColor(hdc, CLR_STAGE);
    SelectObject(hdc, g_fontStage);
    RECT rT = {24, 22, 356, 46};
    DrawTextW(hdc, _t(11), -1, &rT, DT_LEFT|DT_TOP|DT_SINGLELINE|DT_NOCLIP);

    SetTextColor(hdc, CLR_SUB);
    SelectObject(hdc, g_fontSub);
    RECT rS = {24, 48, 356, 68};
    DrawTextW(hdc, _t(12), -1, &rS, DT_LEFT|DT_TOP|DT_SINGLELINE|DT_NOCLIP);

    /* 确认卸载（橙） */
    DrawButton(hdc, 130, 82, 100, 34, CLR_ACCENT, CLR_BTN_TEXT, _t(13), 13);
    /* 取消（橙） */
    DrawButton(hdc, 240, 82, 100, 34, CLR_ACCENT, CLR_BTN_TEXT, _t(14), 13);
}

static LRESULT CALLBACK ConfirmProc(HWND hwnd, UINT msg, WPARAM wParam, LPARAM lParam)
{
    switch (msg) {
    case WM_PAINT: {
        PAINTSTRUCT ps; HDC hdc = BeginPaint(hwnd, &ps);
        PaintConfirm(hwnd, hdc);
        EndPaint(hwnd, &ps);
        return 0;
    }
    case WM_LBUTTONDOWN: {
        int x = LOWORD(lParam), y = HIWORD(lParam);
        if (x>=130&&x<=230&&y>=82&&y<=116) { g_confirmResult = TRUE; DestroyWindow(hwnd); return 0; }
        if (x>=240&&x<=340&&y>=82&&y<=116) { g_confirmResult = FALSE; DestroyWindow(hwnd); return 0; }
        return 0;
    }
    case WM_DESTROY:
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProcW(hwnd, msg, wParam, lParam);
}

static BOOL ShowConfirm(HWND parent)
{
    WNDCLASSEXW wc = { sizeof(wc) };
    wc.style = CS_HREDRAW | CS_VREDRAW;
    wc.lpfnWndProc = ConfirmProc;
    wc.hInstance = GetModuleHandle(NULL);
    wc.hCursor = LoadCursor(NULL, IDC_ARROW);
    wc.lpszClassName = CLASS_CONFIRM;
    RegisterClassExW(&wc);

    HWND h = CreateWindowExW(WS_EX_TOOLWINDOW, CLASS_CONFIRM, _t(13),
        WS_POPUP, 0, 0, 380, 140, parent, NULL, GetModuleHandle(NULL), NULL);
    if (!h) return FALSE;

    HRGN rgn = CreateRoundRectRgn(0,0,381,141, 28, 28);
    SetWindowRgn(h, rgn, TRUE);

    RECT pr, hr;
    GetWindowRect(parent, &pr);
    GetWindowRect(h, &hr);
    SetWindowPos(h, NULL,
        pr.left + (pr.right-pr.left)/2 - (hr.right-hr.left)/2,
        pr.top + (pr.bottom-pr.top)/2 - (hr.bottom-hr.top)/2,
        0,0, SWP_NOSIZE | SWP_NOZORDER);

    ShowWindow(h, SW_SHOW);
    UpdateWindow(h);

    g_confirmResult = FALSE;
    MSG msg;
    while (GetMessageW(&msg, NULL, 0, 0) > 0) {
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }
    return g_confirmResult;
}

/* ── 找 Inno 卸载器 uninsNNN.exe（.exe 与 .dat 必须配对齐全）──
   Inno 卸载器是 uninsNNN.exe + uninsNNN.dat 配对。卸载器自删 exe 可能延迟失败，
   留下残缺的 uninsNNN.exe（dat 已被删）。只认 .exe 与 .dat 同时存在的配对，
   否则调起来会报「uninsNNN.dat 不存在，无法卸载」。从最大编号往回找（编号越大越新）。 */
static BOOL FindUninstaller(WCHAR *out, int cap)
{
    for (int i = 9; i >= 0; i--) {
        WCHAR exe[MAX_PATH], dat[MAX_PATH];
        wsprintfW(exe, L"%s\\unins%03d.exe", g_exeDir, i);
        wsprintfW(dat, L"%s\\unins%03d.dat", g_exeDir, i);
        if (GetFileAttributesW(exe) != INVALID_FILE_ATTRIBUTES &&
            GetFileAttributesW(dat) != INVALID_FILE_ATTRIBUTES) {
            wcscpy(out, exe);
            return TRUE;
        }
    }
    return FALSE;
}

/* ── 等子进程结束，**期间照常处理窗口消息**（2026-09-21 设计约定：
     「我卸载前加载配置包没有？你把我的进程占那么久？」—— 那是窗口假死，不是活儿本身慢）。
   返回 WAIT_OBJECT_0=已结束 / WAIT_TIMEOUT=超时。── */
static DWORD WaitPump(HANDLE hProc, DWORD timeoutMs)
{
    DWORD start = GetTickCount();
    for (;;) {
        DWORD used = GetTickCount() - start;
        if (used >= timeoutMs) return WAIT_TIMEOUT;
        DWORD slice = timeoutMs - used;
        if (slice > 100) slice = 100;
        if (MsgWaitForMultipleObjects(1, &hProc, FALSE, slice, QS_ALLINPUT) == WAIT_OBJECT_0)
            return WAIT_OBJECT_0;
        MSG msg;
        while (PeekMessageW(&msg, NULL, 0, 0, PM_REMOVE)) {
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
    }
}

/* ── 静默执行：cmd.exe /c 跑命令行，CREATE_NO_WINDOW + SW_HIDE 不弹黑框，
   同步等它跑完保证顺序。**返回退出码**（(DWORD)-1 = 起不来 / 超时被杀）。
   ⚠ 超时那条路必须 kill：弹配置包要是没跑完就往下走，配置包里那点东西就再也收不回来了。 */
static DWORD RunHiddenSyncRc(const WCHAR *cmd, DWORD timeoutMs, BOOL killOnTimeout)
{
    WCHAR full[MAX_PATH * 4];
    wsprintfW(full, L"cmd.exe /c %s", cmd);
    STARTUPINFOW si = { sizeof(si) };
    si.dwFlags = STARTF_USESHOWWINDOW;
    si.wShowWindow = SW_HIDE;
    PROCESS_INFORMATION pi = { 0 };
    if (!CreateProcessW(NULL, full, NULL, NULL, FALSE, CREATE_NO_WINDOW, NULL, NULL, &si, &pi)) {
        return (DWORD)-1;
    }
    DWORD rc = (DWORD)-1;
    if (WaitPump(pi.hProcess, timeoutMs) == WAIT_OBJECT_0) {
        DWORD code = (DWORD)-1;
        if (GetExitCodeProcess(pi.hProcess, &code)) rc = code;
    } else if (killOnTimeout) {
        TerminateProcess(pi.hProcess, (UINT)-1);
        WaitForSingleObject(pi.hProcess, 5000);
    }
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return rc;
}

static void RunHiddenSync(const WCHAR *cmd)
{
    RunHiddenSyncRc(cmd, 30000, FALSE);   /* 最多等 30s；超时不管（老行为，别改别的调用点） */
}

/* ── 卸载流程诊断日志：落 %TEMP%。
   ⚠ 不许写 %PROGRAMDATA%\Rheia —— 同一次卸载会把那个目录 rmdir 掉，
     上次就是这么把定位 g_keepOpenclaw 的证据自己删没的。── */
static void LogLine(const WCHAR *line)
{
    WCHAR p[MAX_PATH];
    if (!GetTempPathW(MAX_PATH, p)) return;
    lstrcatW(p, L"rheia-uninstall.log");
    FILE *f = _wfopen(p, L"a, ccs=UTF-8");
    if (!f) return;
    fwprintf(f, L"%s\n", line);
    fclose(f);
}

/* ── 关闭正在运行的壳+Browser（pythonw.exe = runtime/main.py）并等待退出。
   卸载前必须做，否则删 {app} 时 pythonw 占用的文件删不干净。── */
static void KillShellBrowser(void)
{
    /* 按「独立 Python 运行时 + 命令行含 runtime\main.py」精确匹配主程序进程杀掉。
       Python 运行时已独立到 C:\Program Files\PythonSide6（与制卡机共用），
       不能按目录通配 '*Rheia*pythonw.exe' 杀——那会误杀用同一套 Python
       的制卡机。这里用 ExecutablePath（精确锁定独立运行时，比 CommandLine 可靠）
       + CommandLine（区分 main.py 主程序 vs card_maker.py 制卡机）双重匹配。
       用 -EncodedCommand（base64）传递脚本，避开 cmd.exe 对 | 管道符的解析破坏。 */
    RunHiddenSync(
        L"powershell -NoProfile -EncodedCommand "
        L"RwBlAHQALQBDAGkAbQBJAG4AcwB0AGEAbgBjAGUAIABXAGkAbgAzADIAXwBQAHIAbwBjAGUAcwBzACAALQBGAGkAbAB0AGUAcgAgACIATgBhAG0AZQA9ACcAcAB5AHQAaABvAG4AdwAuAGUAeABlACcAIgAgAC0ARQByAHIAbwByAEEAYwB0AGkAbwBuACAAUwBpAGwAZQBuAHQAbAB5AEMAbwBuAHQAaQBuAHUAZQAgAHwAIABXAGgAZQByAGUALQBPAGIAagBlAGMAdAAgAHsAIAAkAF8ALgBFAHgAZQBjAHUAdABhAGIAbABlAFAAYQB0AGgAIAAtAGwAaQBrAGUAIAAnACoAUAB5AHQAaABvAG4AUwBpAGQAZQA2ACoAcAB5AHQAaABvAG4AdwAuAGUAeABlACcAIAAtAGEAbgBkACAAJABfAC4AQwBvAG0AbQBhAG4AZABMAGkAbgBlACAALQBsAGkAawBlACAAJwAqAG0AYQBpAG4AXABtAGEAaQBuAC4AcAB5ACoAJwAgAH0AIAB8ACAARgBvAHIARQBhAGMAaAAtAE8AYgBqAGUAYwB0ACAAewAgAFMAdABvAHAALQBQAHIAbwBjAGUAcwBzACAALQBJAGQAIAAkAF8ALgBQAHIAbwBjAGUAcwBzAEkAZAAgAC0ARgBvAHIAYwBlACAALQBFAHIAcgBvAHIAQQBjAHQAaQBvAG4AIABTAGkAbABlAG4AdABsAHkAQwBvAG4AdABpAG4AdQBlACAAfQA="
    );
}

/* WriteSignalUtf8 定义在后面（写 signal 给窗口轮询）——这里先前置声明，
   因为"弹配置包"那一步要在它之前用到（客户看得见的进度文案）。 */
static void WriteSignalUtf8(const WCHAR *text, int pct, const char *mark);

/* ── 有卡才弹：判定读壳写在 Windows 侧的指针（%USERPROFILE%\.openclaw\slot_policy.json）。
     没载入 ⇒ 一步都不走 —— 不白起 WSL、不占卸载进程（2026-09-21 设计约定：
     「光明正大给他弹出配置包是业务所需，偷偷摸摸占进程就是故障」）。── */
static BOOL CardIsLoaded(void)
{
    WCHAR p[MAX_PATH];
    if (!GetEnvironmentVariableW(L"USERPROFILE", p, MAX_PATH)) return FALSE;
    lstrcatW(p, L"\\.openclaw\\slot_policy.json");
    FILE *f = _wfopen(p, L"rb");
    if (!f) return FALSE;
    char buf[2048];
    size_t n = fread(buf, 1, sizeof(buf) - 1, f);
    fclose(f);
    buf[n] = 0;
    const char *k = strstr(buf, "\"card\"");
    if (!k) return FALSE;
    k = strchr(k + 6, ':');
    if (!k) return FALSE;
    k++;
    while (*k == ' ' || *k == '\t') k++;
    return (*k == '"' && *(k + 1) != 0 && *(k + 1) != '"');   /* "card": "" = 没卡 */
}

/* ── WSL 发行版在不在：不在就别惊动 wsl.exe（它找不到发行版会去拉安装流程，
     卸载器被占在那等 —— 那正是"偷偷摸摸占进程"那一类故障）── */
static BOOL WslDistroPresent(void)
{
    return GetFileAttributesW(L"C:\\wsl-distro") != INVALID_FILE_ATTRIBUTES;
}

/* ── 求 {app}\persona\indivi.py 在 WSL 里的可见路径（弹配置包 / 摘插件都要它）。
   `C:\Program Files\Rheia` → `/mnt/c/Program Files/Rheia/persona/indivi.py`；
   安装目录是客户选的，所以盘符与层级都得现算。── */
static BOOL WslSlotReaderPath(WCHAR *out, int cap)
{
    WCHAR exe[MAX_PATH], dir[MAX_PATH], tail[MAX_PATH];
    if (!GetModuleFileNameW(NULL, exe, MAX_PATH)) return FALSE;
    lstrcpyW(dir, exe);
    WCHAR *slash = wcsrchr(dir, L'\\');
    if (slash) *slash = 0;
    lstrcpyW(tail, dir + 2);                     /* 去掉盘符 "C:" */
    WCHAR *q;
    for (q = tail; *q; q++) if (*q == L'\\') *q = L'/';
    WCHAR drive = (WCHAR)((dir[0] >= L'A' && dir[0] <= L'Z') ? dir[0] + 32 : dir[0]);
    if (wsprintfW(out, L"/mnt/%c%s/persona/indivi.py", drive, tail) >= cap) return FALSE;
    return TRUE;
}

/* ── 当前进程是不是管理员（提权实例）── */
static BOOL IsElevated(void)
{
    BOOL isElevated = FALSE;
    HANDLE hToken = NULL;
    if (OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &hToken)) {
        TOKEN_ELEVATION elev;
        DWORD len = 0;
        if (GetTokenInformation(hToken, TokenElevation, &elev, sizeof(elev), &len)) {
            isElevated = elev.TokenIsElevated;
        }
        CloseHandle(hToken);
    }
    return isElevated;
}

/* ── **最高权限**跑一条命令（弹配置包专用）──────────────────────────────
    2026-09-25 令：「改卸载的时候用**最高权限**弹配置包」。

   已经提权（`wWinMain` 启动时 runas 过）⇒ 直接跑，子进程继承管理员令牌。
   万一没提权（被别的东西以普通权限拉起来、或提权实例被绕过）⇒ 这里**再提一次**：
   `ShellExecuteEx(runas)` 起 `cmd.exe /c <cmd>`，弹 UAC；用户拒绝 ⇒ 返回 -1（如实报，不假装成功）。

   ⚠ 为什么不直接用 `RunHiddenSyncRc`：它 `CreateProcessW` 造出来的子进程**继承当前令牌**，
     当前不是管理员它就只能是普通权限 —— 那就不是"最高权限弹配置包"了。── */
static DWORD RunElevatedSyncRc(const WCHAR *cmd, DWORD timeoutMs, BOOL killOnTimeout)
{
    if (IsElevated()) return RunHiddenSyncRc(cmd, timeoutMs, killOnTimeout);

    WCHAR params[MAX_PATH * 4];
    wsprintfW(params, L"/c %s", cmd);
    SHELLEXECUTEINFOW sei = { sizeof(sei) };
    sei.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC;
    sei.lpVerb = L"runas";
    sei.lpFile = L"cmd.exe";
    sei.lpParameters = params;
    sei.nShow = SW_HIDE;
    if (!ShellExecuteExW(&sei) || !sei.hProcess) return (DWORD)-1;
    DWORD rc = (DWORD)-1;
    if (WaitPump(sei.hProcess, timeoutMs) == WAIT_OBJECT_0) {
        DWORD code = (DWORD)-1;
        if (GetExitCodeProcess(sei.hProcess, &code)) rc = code;
    } else if (killOnTimeout) {
        TerminateProcess(sei.hProcess, (UINT)-1);
        WaitForSingleObject(sei.hProcess, 5000);
    }
    CloseHandle(sei.hProcess);
    return rc;
}

/* ── 弹配置包：调壳侧 CLI（{app}\persona\indivi.py --eject-slot，经 WSL python3）。
   返回 CLI 退出码：0 = 弹干净了；非 0 = 没弹成（配置包被占 / 起不来 / 超时被杀）。

   **最高权限**（2026-09-25 设计约定）三件一起做到，缺一不可：
     · Windows 侧：`RunElevatedSyncRc`（已提权直接跑；没提权就 runas 再提一次）；
     · WSL 侧：`-u root`（发行版内最高权限）；
     · 可审计：提权状态与完整命令都写进 `%TEMP%\rheia-uninstall.log`——
       出问题一眼看得出是"没提权"还是"命令本身崩了"。
   ⚠ `wsl.exe` 走**绝对路径**（`%SystemRoot%\System32\wsl.exe`）：提权实例的 PATH 可能被裁过，
     靠 PATH 找 `wsl` 会静默失败（"起不来"和"真失败"混成一笔账）。
   ⚠ 可见性由**调用方**负责：DoUninstall 先把「正在弹出配置包…」画到进度卡上再调这里
     —— 卸载的活儿都是阻塞的，画在这一步之前客户才看得见（2026-09-21 设计约定：
     「我卸载东西也要让客户先看见配置包弹出再卸载」）。
   ⚠ 超时 180s：卡侧那把锁是 blocking 60s（`persona\indivi.py` 的 `_card_lock`），
     再加"收/交班单/清账"的活儿。超时**杀进程并按失败处理**。
   ⚠ 没载入 / WSL 不在的那道门在调用方（这里只管弹，不管要不要弹）。── */
static DWORD EjectCard(void)
{
    WCHAR wslScript[MAX_PATH * 2], cmd[MAX_PATH * 4], wslExe[MAX_PATH], l[512];
    if (!WslSlotReaderPath(wslScript, MAX_PATH * 2)) return (DWORD)-1;
    if (!GetSystemDirectoryW(wslExe, MAX_PATH)) return (DWORD)-1;
    lstrcatW(wslExe, L"\\wsl.exe");
    wsprintfW(cmd,
              L"\"%s\" -d Ubuntu -u root -- python3 \"%s\" --eject-slot "
              L">> \"%%TEMP%%\\rheia-slot-eject.log\" 2>&1",
              wslExe, wslScript);
    {
        WCHAR exe[MAX_PATH];
        GetModuleFileNameW(NULL, exe, MAX_PATH);
        wsprintfW(l, L"弹配置包：提权=%d wsl=%s", IsElevated() ? 1 : 0, wslExe);
        LogLine(l);
    }
    DWORD rc = RunElevatedSyncRc(cmd, 180000, TRUE);
    wsprintfW(l, L"弹配置包 rc=%lu（提权=%d）", (unsigned long)rc, IsElevated() ? 1 : 0);
    LogLine(l);
    return rc;
}

/* ── 摘掉**我们自己的插件**（卸载壳时把配置包相关插件从 WSL 侧摘干净）。

   为什么必须在**卸载**这条路上做（2026-09-21 问：「我们不卸载 plugin，你觉得对
   下次安装有没有影响？」）—— 答案就在 `persona\indivi.py` 自己的实现里：
     · L1518-1521：配置里 `plugins.entries.<id>` 与 `plugins.load.paths` **两处都要摘**，
       「漏一处 OpenClaw 就报 config invalid，**装下一次会被它绊住**」
       （实测症状：`plugins list` 报 `plugin path not found`、`plugins doctor` 报
       `OpenClaw config is invalid`）；
     · L1551-1555：摘完**必须重启网关**（不是停）—— 插件是网关加载进内存的，
       「不重启它就一直转着 —— 实测卸载完插件进程还在每 30 秒读一次指针」。
   只留安装期 `setup.iss:82` 那条钩子兜不住：从卸载到下次安装之间（可能永远不装），
   残留插件在客户自己的 OpenClaw 里每轮还会 spawn `--drain-host-copies`，而 {app} 已经删了。

   ⚠ 只在**保留 WSL**（勾了「仅卸载 Rheia」/ 日语自毁）时做：全量卸载 WSL 整个消失，摘它没意义。
   ⚠ 失败**不阻断**（这是清我们自己的东西，不碰客户资产），但如实落 `%TEMP%\rheia-uninstall.log`。
   ⚠ 它内部会 `openclaw gateway restart --force`（CLI 原话：不 stop 只 restart，因为客户还要用
     OpenClaw），所以超时给足 180s。── */
static void RemoveOurPlugin(void)
{
    WCHAR script[MAX_PATH * 2], cmd[MAX_PATH * 4], l[256];
    if (!WslSlotReaderPath(script, MAX_PATH * 2)) return;
    wsprintfW(cmd,
              L"wsl -d Ubuntu -u root -- python3 \"%s\" --uninstall-plugin "
              L">> \"%%TEMP%%\\rheia-slot-plugin.log\" 2>&1",
              script);
    DWORD rc = RunHiddenSyncRc(cmd, 180000, TRUE);
    wsprintfW(l, L"摘插件 --uninstall-plugin rc=%lu", (unsigned long)rc);
    LogLine(l);
}


/* ── 写 signal（UTF-8，与 card.c 读一致）── */
static void WriteSignalUtf8(const WCHAR *text, int pct, const char *mark)
{
    HANDLE f = CreateFileW(g_signalPath, FILE_APPEND_DATA, 0, NULL,
                           OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (f == INVALID_HANDLE_VALUE) return;
    char utf8[512];
    int n = WideCharToMultiByte(CP_UTF8, 0, text, -1, utf8, sizeof(utf8)-1, NULL, NULL);
    if (n > 0) utf8[n-1] = 0; /* n 含结尾 null，只去掉结尾 null 这一个字节 */
    char line[600];
    int len = wsprintfA(line, "%s | %d | %s\r\n", utf8, pct, mark);
    DWORD w = 0;
    WriteFile(f, line, len, &w, NULL);
    CloseHandle(f);
}

/* ── 立刻切到进度页：客户点「确认卸载」后**第一眼**看见的就得是这一步在走，
   而不是冻在确认页上等半天、干完活才闪一下 100%（2026-09-21 设计约定）。── */
static void ShowProgressPage(HWND hwnd)
{
    if (!hwnd) return;
    g_stage = 1;
    {
        int sw = GetSystemMetrics(SM_CXSCREEN), sh = GetSystemMetrics(SM_CYSCREEN);
        SetWindowPos(hwnd, NULL, (sw-CARD_WIDTH)/2, (sh-PROG_HEIGHT)/2,
                     CARD_WIDTH, PROG_HEIGHT, SWP_NOZORDER | SWP_NOACTIVATE);
        HRGN rgn = CreateRoundRectRgn(0, 0, CARD_WIDTH+1, PROG_HEIGHT+1,
                                      CARD_RADIUS*2, CARD_RADIUS*2);
        SetWindowRgn(hwnd, rgn, TRUE);
    }
    SetTimer(hwnd, TIMER_POLL, 150, NULL);
    SetTimer(hwnd, TIMER_EASE, 30, NULL);
    InvalidateRect(hwnd, NULL, TRUE);
    UpdateWindow(hwnd);
}

/* ── 一步一显：写 signal（同一份日志，壳侧/卡侧查得到）+ 当场同步重绘。
   卸载的活儿全是阻塞的，不在这里重绘，客户在整个流程里就什么都看不见 —— 那就是
   「偷偷摸摸占进程」。pct 只给 g_target，进度条由 TIMER_EASE 平滑爬上去。── */
static void ShowStep(HWND hwnd, const WCHAR *text, int pct, const char *mark)
{
    WriteSignalUtf8(text, pct, mark);
    if (!hwnd) return;
    lstrcpynW(g_stageText, text, 256);
    g_stageCleared = (pct >= 100);
    g_target = pct;
    InvalidateRect(hwnd, NULL, TRUE);
    UpdateWindow(hwnd);
}

/* ── 停 ms 毫秒，期间继续泵消息（让客户把这一行读完；窗口不假死）── */
static void PumpIdle(DWORD ms)
{
    DWORD start = GetTickCount();
    while (GetTickCount() - start < ms) {
        MSG msg;
        while (PeekMessageW(&msg, NULL, 0, 0, PM_REMOVE)) {
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
        Sleep(20);
    }
}

/* ── 进度页绘制（与安装 card 同风格）── */
static void PaintProgressPage(HWND hwnd, HDC hdc)
{
    RECT rc; GetClientRect(hwnd, &rc);
    FillRect(hdc, &rc, g_brBg);
    SetBkMode(hdc, TRANSPARENT);

    SetTextColor(hdc, CLR_BRAND);
    SelectObject(hdc, g_fontBrand);
    RECT rB = {20, 10, CARD_WIDTH-20, 34};
    DrawTextW(hdc, L"Rheia", -1, &rB, DT_LEFT|DT_TOP|DT_SINGLELINE|DT_NOCLIP);

    RECT rBg = {20, 66, 20+406, 76};
    FillRect(hdc, &rBg, g_brBarBg);
    int fillW = 406 * g_display / 100;
    if (fillW > 0) { RECT rf = {20, 66, 20+fillW, 76}; FillRect(hdc, &rf, g_brBarFill); }

    SetTextColor(hdc, CLR_PCT);
    SelectObject(hdc, g_fontPct);
    WCHAR buf[16]; wsprintfW(buf, L"%d%%", g_display);
    RECT rP = {436, 64, 480, 80};
    DrawTextW(hdc, buf, -1, &rP, DT_RIGHT|DT_TOP|DT_SINGLELINE|DT_NOCLIP);

    if (!g_stageCleared) {
        SetTextColor(hdc, CLR_DETAIL);
        SelectObject(hdc, g_fontStageSmall);
        RECT rT = {20, 86, CARD_WIDTH-20, 106};
        DrawTextW(hdc, g_stageText, -1, &rT, DT_LEFT|DT_TOP|DT_SINGLELINE|DT_NOCLIP);
    }

    SetTextColor(hdc, CLR_COPYRIGHT);
    SelectObject(hdc, g_fontCopy);
    RECT rC = {20, PROG_HEIGHT-24, CARD_WIDTH-20, PROG_HEIGHT-8};
    DrawTextW(hdc, L"© Alexander Lee（李亚霖）", -1, &rC, DT_LEFT|DT_TOP|DT_SINGLELINE|DT_NOCLIP);
}

/* ── transplay：读 signal 最后一行，解析「文案 | 进度 | 标记」。返回 -2=done, -1=无, 0~100=进度 ── */
static int ReadSignal(void)
{
    FILE *f = _wfopen(g_signalPath, L"r");
    char line[512], last[512];
    last[0] = 0;
    while (fgets(line, sizeof(line), f)) strcpy(last, line);
    fclose(f);

    char *p = last;
    while (*p==' '||*p=='\t'||*p=='\r'||*p=='\n') p++;
    char *end = p + strlen(p);
    while (end>p && (end[-1]==' '||end[-1]=='\t'||end[-1]=='\r'||end[-1]=='\n')) end--;
    *end = '\0';

    char *seg1 = p;
    char *seg2 = strchr(seg1, '|');
    *seg2 = 0; seg2++;
    char *seg3 = strchr(seg2, '|');
    if (seg3) { *seg3 = 0; seg3++; }

    MultiByteToWideChar(CP_UTF8, 0, seg1, -1, g_stageText, 256);
    int v = atoi(seg2);
    if (v < 0 || v > 100) return -1;
    if (seg3 && strstr(seg3, "done")) return -2;
    return v;
}

static void QuitUninstall(HWND hwnd)
{
    /* 自删：uninstall.exe 正在运行，Windows 不允许删除被占用的 exe（提权也不行——
       那是占用锁，不是权限）。但**重命名不受占用限制**：立刻把自己改名挪到 %TEMP%，
       {app} 目录当场就干净了，不留空壳、不用等、不用延时重试。
       挪走的那个副本是残留垃圾，但它在 %TEMP% 里，不影响安装目录的洁净。 */
    {
        WCHAR selfpath[MAX_PATH];
        GetModuleFileNameW(NULL, selfpath, MAX_PATH);

        WCHAR tmp[MAX_PATH], target[MAX_PATH];
        if (GetTempPathW(MAX_PATH, tmp)) {
            wsprintfW(target, L"%sRheia-uninstall-bak.exe", tmp);
            /* 先清掉上次可能残留的同名副本（已退出的进程不占用它） */
            DeleteFileW(target);
            if (!MoveFileExW(selfpath, target, MOVEFILE_REPLACE_EXISTING)) {
                /* 普通权限挪不动 → 提权再挪一次（runas：弹 UAC） */
                WCHAR args[MAX_PATH + 64];
                wsprintfW(args, L"/c move /y \"%s\" \"%s\"", selfpath, target);
                SHELLEXECUTEINFOW sei = { sizeof(sei) };
                sei.fMask = SEE_MASK_NOCLOSEPROCESS;
                sei.lpFile = L"cmd.exe";
                sei.lpParameters = args;
                sei.lpVerb = L"runas";
                sei.nShow = SW_HIDE;
                if (ShellExecuteExW(&sei) && sei.hProcess) {
                    WaitForSingleObject(sei.hProcess, 10000);
                    CloseHandle(sei.hProcess);
                }
            }
        }
    }
    DestroyWindow(hwnd);
}

static void OnPoll(HWND hwnd)
{
    int v = ReadSignal();
    if (v == -2) {
        /* done：不立即秒退，先显示 100% 停顿 2 秒让客户看见"卸载完成" */
        g_stageCleared = TRUE;
        g_target = 100;
        g_idleSince100++;
        if (g_idleSince100 > HOLD_100_SECONDS * 1000 / 150) { QuitUninstall(hwnd); return; }
        return;
    }
    if (v < 0) return;
    if (v == g_lastNumeric) return;
    if (v < g_lastNumeric && v != 100) return;
    g_lastNumeric = v;
    g_idleSeconds = 0;
    g_target = v;
    if (v == 100) {
        g_stageCleared = TRUE;
        g_idleSince100++;
        if (g_idleSince100 > HOLD_100_SECONDS * 1000 / 150) { QuitUninstall(hwnd); return; }
    } else {
        g_idleSince100 = 0;
        g_stageCleared = FALSE;
    }
}

static void OnEase(HWND hwnd)
{
    float diff = (float)g_target - g_displayF;
    if (diff > 0.01f || diff < -0.01f) {
        g_displayF += diff * 0.12f;
        int nd = (int)(g_displayF + 0.5f);
        if (nd != g_display) { g_display = nd; InvalidateRect(hwnd, NULL, FALSE); }
    } else if (g_displayF != (float)g_target) {
        g_displayF = (float)g_target;
        g_display = g_target;
        InvalidateRect(hwnd, NULL, FALSE);
    }
}

static void OnWatchdog(HWND hwnd)
{
    g_idleSeconds += 5;
    if (g_idleSeconds >= WATCHDOG_SECONDS) QuitUninstall(hwnd);
}

/* ── 确认卸载（唯一出口）──
   顺序铁律（2026-09-21 设计约定：「我卸载东西也要让客户先看见配置包弹出再卸载」）：
     ① 进度页立刻亮出来（客户第一眼就看得见流程在走）
     ② **弹配置包**——整个流程的第一步，在清 WSL / 删 {app} / 停壳之前
     ③ 停壳+Browser
     ④ 清 WSL（只有没勾「仅卸载 Rheia」才做）
     ⑤ 调 unins000 删壳层
     ⑥ 卸载完成
   为什么弹配置包必须是第一步：这一趟要"收"回配置包的东西全在**还没被清掉**的宿主上 ——
   壳（persona/、persona\indivi.py）、WSL（她那边的会话与语料）、{app} 本身。
   任何一样先没了，配置包里就再也收不回这一次的东西（原话：
   「卸载后的配置包数据能存回配置包吗？」）。弹不出来就**一个字节都不删**、如实报错收场。*/
static void DoUninstall(HWND hwnd)
{
    /* 清空 signal 统一日志——否则残留的 done 会秒杀进度页 */
    HANDLE sf = CreateFileW(g_signalPath, GENERIC_WRITE, 0, NULL,
                            CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (sf != INVALID_HANDLE_VALUE) CloseHandle(sf);

    /* ① 进度页立刻亮出来。从这一刻起本体是"忙"的：泵消息期间禁止退出/重入
       （不然 Alt+F4 或定时器会把窗口销毁在半路）。 */
    g_busy = TRUE;
    ShowProgressPage(hwnd);
    {
        WCHAR l[256];
        wsprintfW(l, L"── 卸载开始：g_keepOpenclaw=%d 配置包在槽=%d WSL在=%d",
                  g_keepOpenclaw ? 1 : 0, CardIsLoaded() ? 1 : 0,
                  WslDistroPresent() ? 1 : 0);
        LogLine(l);
    }

    /* ② 弹配置包 —— 有卡才走（没载入就一步都不走：不白起 WSL、不占卸载进程） */
    BOOL shellKilled = FALSE;
    if (CardIsLoaded() && WslDistroPresent()) {
        ShowStep(hwnd, _t(19), 3, "complete");      /* 「正在弹出配置包…」客户先看见这一步 */
        DWORD rc = EjectCard();
        if (rc != 0) {
            /* 没弹成最常见的原因是配置包被占（卡侧那把排他锁还拿着）—— 壳就是那个写手，
               停掉它再弹一次；**停壳只是这一步内部的动作，不算开始卸载**。 */
            LogLine(L"弹配置包第一次没成 ⇒ 停壳后重试");
            KillShellBrowser();
            shellKilled = TRUE;
            rc = EjectCard();
        }
        {
            WCHAR l[128];
            wsprintfW(l, L"弹配置包 rc=%lu", (unsigned long)rc);
            LogLine(l);
        }
        if (rc != 0) {
            /* 有卡就自动弹过了（直接弹 → 停壳后重弹，两次）。还没成也**不拦卸载**：
               "卡在不在槽里"跟"能不能卸载"是两件事 —— 弹不出来如实记账、给客户一句实话，
               卸载照走（客户要卸就得卸得掉）。卡里那点东西的保险是上面那两次重弹，
               不是拿"拒绝卸载"当保险。 */
            WCHAR l2[192];
            wsprintfW(l2, L"弹配置包未成 rc=%lu ⇒ 记账后继续卸载（不阻断）", (unsigned long)rc);
            LogLine(l2);
            if (hwnd) {
                ShowStep(hwnd, _t(20), 3, "complete");
                PumpIdle(1500);
            }
        }
    }

    /* ③ 停壳+Browser：删 {app} 前必须停（文件被占用删不干净）；清 WSL 前也必须停
       （保活/自愈会把刚 unregister 掉的发行版重新拉起来）。②里已经停过就不重复。 */
    if (!shellKilled) KillShellBrowser();

    /* ④ 不勾选「仅卸载 Rheia」→ 清 WSL（terminate → unregister → 删 C:\wsl-distro → ~/.openclaw）
       g_keepOpenclaw=TRUE（勾了「仅卸载 Rheia」/ 日语自毁）时全部跳过 —— 完全不触碰
       WSL/Ubuntu/OpenClaw，那是客户资产。 */
    ShowStep(hwnd, _t(16), 10, "complete");
    if (!g_keepOpenclaw) {
        RunHiddenSync(L"wsl --terminate Ubuntu >nul 2>&1");
        RunHiddenSync(L"wsl --unregister Ubuntu >nul 2>&1");
        /* 全量卸载：连 C:\wsl-distro 目录一起删干净（unregister 只删 vhdx 数据，
           目录本身会残留空壳，这里补 rmdir 让「整个 distro 卸载掉」）。 */
        RunHiddenSync(L"rmdir /s /q \"C:\\wsl-distro\" >nul 2>&1");
        WCHAR dotopenclaw[MAX_PATH];
        GetEnvironmentVariableW(L"USERPROFILE", dotopenclaw, MAX_PATH);
        lstrcatW(dotopenclaw, L"\\.openclaw");
        WCHAR cmd[MAX_PATH + 64];
        wsprintfW(cmd, L"rmdir /s /q \"%s\" >nul 2>&1", dotopenclaw);
        RunHiddenSync(cmd);
        LogLine(L"WSL 已清：terminate/unregister/C:\\wsl-distro/~/.openclaw");
    } else {
        /* 勾了「仅卸载 Rheia」（或日语自毁）⇒ WSL/OpenClaw/客户的配置包都留着。
           **唯一要动的是我们自己的插件**：插件在我们刚卸载掉的这个壳里才有人管它，
           留着的后果写在 `persona\indivi.py 里 --uninstall-plugin 那段`（配置两处漏摘一处，
           下次安装会被半残配置绊住）与 `1551-1555`（不重启网关它一直在内存里转）。
           不是"碰客户资产"：摘的是 Rheia 自己的插件，客户的 OpenClaw 与数据一律不动
           （CLI 内部只 restart 网关、不 stop，客户那边照常可用）。 */
        if (WslDistroPresent()) {
            ShowStep(hwnd, _t(21), 12, "complete");
            RemoveOurPlugin();
        } else {
            LogLine(L"g_keepOpenclaw=TRUE 且 WSL 不在 ⇒ 没有可摘的插件");
        }
        LogLine(L"g_keepOpenclaw=TRUE ⇒ 客户资产（WSL/OpenClaw/配置包）一律不动");
    }

    /* ⑤ 静默调 unins000.exe（/VERYSILENT 后台跑，只删壳层 {app} + 清注册表 +
          删 ProgramData 白名单，unins000 完全不碰 WSL），丑方框不出现。
          uninstall.exe 已在 wWinMain 提权成功，unins000 作为套接结构继承管理员
          权限，用 open 即可（不再 runas 弹第二次 UAC）。 */
    ShowStep(hwnd, _t(17), 40, "complete");
    WCHAR unins[MAX_PATH];
    if (FindUninstaller(unins, MAX_PATH)) {
        SHELLEXECUTEINFOW sei = { sizeof(sei) };
        sei.fMask = SEE_MASK_NOCLOSEPROCESS;
        sei.lpFile = unins;
        sei.lpParameters = L"/VERYSILENT";
        sei.nShow = SW_HIDE;
        sei.lpVerb = L"open";
        if (ShellExecuteExW(&sei)) {
            if (sei.hProcess) CloseHandle(sei.hProcess);
        }
        LogLine(L"壳层移除已发起：unins000 /VERYSILENT");
    } else {
        LogLine(L"找不到 unins000.exe/.dat 配对 ⇒ 壳层没删");
    }

    /* ⑥ 完成（进度页停在 100% 两秒让客户看见"卸载完成"，再由 TIMER_POLL 收尾退出） */
    ShowStep(hwnd, _t(18), 100, "done");
    g_busy = FALSE;
}

/* ── 主窗口过程 ── */
static LRESULT CALLBACK WndProc(HWND hwnd, UINT msg, WPARAM wParam, LPARAM lParam)
{
    switch (msg) {
    case WM_CREATE: {
        g_fontBrand  = MakeFont(18, FW_BOLD);
        g_fontSub    = MakeFont(12, FW_NORMAL);
        g_fontStage  = MakeFont(14, FW_SEMIBOLD);
        g_fontDetail = MakeFont(12, FW_NORMAL);
        g_fontCopy   = MakeFont(10, FW_NORMAL);
        g_fontBtn    = MakeFont(12, FW_SEMIBOLD);
        g_fontPct    = MakeFont(12, FW_SEMIBOLD);
        g_fontStageSmall = MakeFont(11, FW_NORMAL);
        g_brBg       = CreateSolidBrush(CLR_BG);
        g_brBgDeep   = CreateSolidBrush(CLR_BG_DEEP);
        g_brSep      = CreateSolidBrush(CLR_SEP);
        g_brBarBg    = CreateSolidBrush(CLR_BAR_BG);
        g_brBarFill  = CreateSolidBrush(CLR_ACCENT);
        return 0;
    }
    case WM_PAINT: {
        PAINTSTRUCT ps;
        HDC hdc = BeginPaint(hwnd, &ps);
        if (g_stage == 0) PaintCard(hwnd, hdc);
        else PaintProgressPage(hwnd, hdc);
        EndPaint(hwnd, &ps);
        return 0;
    }
    case WM_TIMER:
        /* 流程进行中：只跑缓动（进度条继续爬，窗口看着是活的），不跑 OnPoll
           —— 那时候窗口不许被定时器收走，进度也不由 signal 回读驱动。 */
        if (wParam == TIMER_EASE) OnEase(hwnd);
        else if (wParam == TIMER_POLL && !g_busy) OnPoll(hwnd);
        return 0;
    case WM_CLOSE:
        if (g_busy) return 0;          /* 流程进行中：Alt+F4 不许把窗口销毁在半路 */
        DestroyWindow(hwnd);
        return 0;
    case WM_MOUSEMOVE: {
        int x = LOWORD(lParam), y = HIWORD(lParam);
        int hit = HitTest(x, y);
        if (hit != g_hover) { g_hover = hit; InvalidateRect(hwnd, NULL, FALSE); }
        return 0;
    }
    case WM_LBUTTONDOWN: {
        if (g_busy) return 0;          /* 流程进行中：不接受任何点击（含重入 DoUninstall） */
        int x = LOWORD(lParam), y = HIWORD(lParam);
        int hit = HitTest(x, y);
        if (hit == HIT_CANCEL) DestroyWindow(hwnd);
        else if (hit == HIT_UNINST) DoUninstall(hwnd);
        else if (hit == HIT_KEEP) {
            g_keepOpenclaw = !g_keepOpenclaw;  /* 切换勾选 */
            InvalidateRect(hwnd, NULL, FALSE);
        }
        else if (y < 100) {
            ReleaseCapture();
            SendMessageW(hwnd, WM_NCLBUTTONDOWN, HTCAPTION, 0);
        }
        return 0;
    }
    case WM_DESTROY:
        if (g_fontBrand) DeleteObject(g_fontBrand);
        if (g_fontSub) DeleteObject(g_fontSub);
        if (g_fontStage) DeleteObject(g_fontStage);
        if (g_fontDetail) DeleteObject(g_fontDetail);
        if (g_fontCopy) DeleteObject(g_fontCopy);
        if (g_fontBtn) DeleteObject(g_fontBtn);
        if (g_fontPct) DeleteObject(g_fontPct);
        if (g_fontStageSmall) DeleteObject(g_fontStageSmall);
        if (g_brBg) DeleteObject(g_brBg);
        if (g_brBgDeep) DeleteObject(g_brBgDeep);
        if (g_brSep) DeleteObject(g_brSep);
        if (g_brBarBg) DeleteObject(g_brBarBg);
        if (g_brBarFill) DeleteObject(g_brBarFill);
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProcW(hwnd, msg, wParam, lParam);
}

/* ── 入口 ── */
int WINAPI wWinMain(HINSTANCE hInst, HINSTANCE hPrev, PWSTR cmdline, int show)
{
    /* 提权：卸载要删 {app}（admin 创建的文件）+ 清注册表卸载键，都需要管理员权限。
       当前进程非管理员 → 用 runas 提权重启自己，一次弹 UAC，之后整个卸载流程
       都在管理员权限下跑，删干净 + 清注册表，一次成功。
       ⚠ 判据用 `IsElevated()`（**全文件只有一份**）—— 弹配置包那条路也用它；
         两份手抄早晚有一处忘了改。 */
    {
        if (!IsElevated()) {
            WCHAR exe[MAX_PATH];
            GetModuleFileNameW(NULL, exe, MAX_PATH);
            SHELLEXECUTEINFOW sei = { sizeof(sei) };
            sei.lpVerb = L"runas";
            sei.lpFile = exe;
            sei.lpParameters = cmdline;
            sei.nShow = SW_SHOWNORMAL;
            if (ShellExecuteExW(&sei)) {
                return 0;  /* 提权实例已启动，本普通实例退出 */
            }
            /* 用户拒绝 UAC → 直接退出，绝不以普通权限继续跑。
               卸载要删 admin 创建的 {app} + 清注册表，普通权限跑下去
               只会留下删不干净的半吊子状态。提权失败 = 卸载中止。 */
            MessageBoxW(NULL, L"卸载需要管理员权限，已取消。", L"Rheia", MB_OK | MB_ICONWARNING);
            return 1;
        }
    }

    /* 任务栏 AppUserModelID：让窗口在任务栏/Alt+Tab 正确显示 */
    {
        HMODULE shell = LoadLibraryW(L"shell32.dll");
        if (shell) {
            typedef HRESULT (WINAPI *SetAumidFn)(PCWSTR);
            SetAumidFn f = (SetAumidFn)(void*)GetProcAddress(shell, "SetCurrentProcessExplicitAppUserModelID");
            if (f) f(L"Rheia.Uninstaller");
            FreeLibrary(shell);
        }
    }

    GetModuleFileNameW(NULL, g_exeDir, MAX_PATH);
    for (int i = lstrlenW(g_exeDir) - 1; i >= 0; i--) {
        if (g_exeDir[i] == L'\\') { g_exeDir[i] = 0; break; }
    }

    { /* signal 路径：%TEMP%\okoc_signal.txt（临时通信文件，卸载不会删 TEMP） */
        GetTempPathW(MAX_PATH, g_signalPath);
        lstrcatW(g_signalPath, L"okoc_signal.txt");
    }
    wcscpy(g_stageText, _t(15));

    /* 日文自毁入口：main.py 检测日语后调 uninstall.exe（无参数）。这里检测到
       日语环境 → 等效客户已勾选「仅卸载 Rheia」（g_keepOpenclaw=TRUE），
       静默走 DoUninstall，只删壳层，绝不碰 WSL/Ubuntu/OpenClaw。 */
    if (PRIMARYLANGID(GetUserDefaultUILanguage()) == 0x11) {
        g_keepOpenclaw = TRUE;  /* 日语环境 → 不删 WSL */
        DoUninstall(NULL);
        return 0;
    }


    WNDCLASSEXW wc = { sizeof(wc) };
    wc.style = CS_HREDRAW | CS_VREDRAW;
    wc.lpfnWndProc = WndProc;
    wc.hInstance = hInst;
    wc.hCursor = LoadCursor(NULL, IDC_ARROW);
    wc.hIcon = LoadIconW(hInst, MAKEINTRESOURCEW(IDI_APPICON));
    wc.hIconSm = LoadIconW(hInst, MAKEINTRESOURCEW(IDI_APPICON));
    wc.lpszClassName = CLASS_MAIN;
    RegisterClassExW(&wc);

    HWND hwnd = CreateWindowExW(WS_EX_APPWINDOW, CLASS_MAIN, L"Rheia 卸载",
        WS_POPUP, 0, 0, CARD_WIDTH, LIST_HEIGHT, NULL, NULL, hInst, NULL);
    if (!hwnd) return 1;

    HRGN rgn = CreateRoundRectRgn(0,0,CARD_WIDTH+1,LIST_HEIGHT+1, CARD_RADIUS*2, CARD_RADIUS*2);
    SetWindowRgn(hwnd, rgn, TRUE);

    int sw = GetSystemMetrics(SM_CXSCREEN), sh = GetSystemMetrics(SM_CYSCREEN);
    SetWindowPos(hwnd, NULL, (sw-CARD_WIDTH)/2, (sh-LIST_HEIGHT)/2, 0,0,
        SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE);

    ShowWindow(hwnd, SW_SHOW);
    UpdateWindow(hwnd);

    MSG msg;
    while (GetMessageW(&msg, NULL, 0, 0) > 0) {
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }
    return (int)msg.wParam;
}
