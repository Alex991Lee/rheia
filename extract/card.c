/*
 * card.c — Rheia 安装引导卡片（纯 Win32，零依赖，零版权红线）
 *
 * 架构：card 是纯界面，只传值、绝不实例化任何进程。
 *   --select   模式：显示路径页，收集「目录 + 仅装壳布尔」，
 *              点下一步写 %TEMP%\okoc_card_input.txt（"目录|0或1"）后退出；
 *              点取消写 CANCEL 后退出。setup 的 InitializeSetup 同步等待并读取。
 *   --progress 模式：显示进度页，读 %TEMP%\okoc_inno_progress.txt 显示进度。
 *   拉起静默安装实例由 setup 的 Pascal 代码（InitializeSetup）自己做，card 不碰。
 */

#define UNICODE
#define _UNICODE
#include <windows.h>
#include <shlobj.h>
#include <shobjidl.h>
#include <objbase.h>
#include <shellapi.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#pragma comment(lib, "user32.lib")
#pragma comment(lib, "gdi32.lib")
#pragma comment(lib, "shell32.lib")
#pragma comment(lib, "ole32.lib")

/* ── 卡片尺寸 ── */
#define CARD_WIDTH   500
#define DIR_HEIGHT   210   /* 路径选择页高度 */
#define PROG_HEIGHT  168   /* 进度页高度 */
#define CARD_RADIUS  14

/* ── 色值（COLORREF = 0x00BBGGRR）── */
#define CLR_BG        0x1A1A1A
#define CLR_BG_DEEP   0x0A0A0A
#define CLR_PANEL     0x2A2A2A
#define CLR_BRAND     0xFFFFFF
#define CLR_SUB       0xB0B0B0
#define CLR_BAR_BG    0x333333
#define CLR_BAR_FILL  0x2170F3   /* #f37021 橙 */
#define CLR_ACCENT_HOVER 0x3388FF
#define CLR_PCT       0xE0E0E0
#define CLR_STAGE     0xE0E0E0
#define CLR_DETAIL    0xA0A0A0
#define CLR_COPYRIGHT 0x555555
#define CLR_BTN       0x666666
#define CLR_BTN_TEXT  0x0A0A0A

/* ── IPC ── */
#define SIGNAL_FILENAME  L"signal"
#define INPUT_FILENAME   L"okoc_card_input.txt"
#define CANCEL_FILENAME  L"Rheia_cancel.flag"
#define WATCHDOG_SECONDS 600
#define HOLD_100_SECONDS 2

#define TIMER_POLL    1
#define TIMER_WATCH   2
#define TIMER_EASE    3
#define TIMER_REJECT  4

#define FONT_FACE L"Microsoft YaHei UI"
#define IDI_APPICON 1
#define CLASS_MAIN L"RheiaCard"
#define CLASS_CONFIRM L"RheiaConfirm"
#define IDC_EDIT_PATH 101

/* 路径页按钮 */
#define BTN_BROWSE_X 390
#define BTN_BROWSE_Y 70
#define BTN_BROWSE_W 90
#define BTN_BROWSE_H 30
#define BTN_NEXT_X   20
#define BTN_NEXT_Y   128
#define BTN_NEXT_W   100
#define BTN_NEXT_H   34
#define BTN_CANCEL_X 130
#define BTN_CANCEL_Y 128
#define BTN_CANCEL_W 100
#define BTN_CANCEL_H 34

/* 进度页取消 × */
#define BTN_X_X 452
#define BTN_X_Y 86
#define BTN_X_W 28
#define BTN_X_H 28

/* 按钮 id */
enum { HIT_NONE=0, HIT_BROWSE, HIT_NEXT, HIT_CANCEL, HIT_CLOSE, HIT_SHELLONLY };

static HFONT g_fontBrand  = NULL;
static HFONT g_fontSub    = NULL;
static HFONT g_fontStage  = NULL;
static HFONT g_fontDetail = NULL;
static HFONT g_fontPct    = NULL;
static HFONT g_fontCopy   = NULL;
static HFONT g_fontBtn    = NULL;
static HFONT g_fontEdit   = NULL;
static HFONT g_fontStageSmall = NULL;
static HBRUSH g_brBg      = NULL;
static HBRUSH g_brBgDeep  = NULL;
static HBRUSH g_brPanel   = NULL;
static HBRUSH g_brBarBg   = NULL;
static HBRUSH g_brBarFill = NULL;

static int  g_lastNumeric  = -1;
static int  g_idleSeconds  = 0;
static BOOL g_error        = FALSE;  /* 安装器报错：停在错误文案上，不自动退出 */
static int  g_idleSince100 = 0;
static int  g_display      = 0;
static float g_displayF    = 0.0f;
static int  g_target       = 0;
static BOOL g_stageCleared = FALSE;
static int  g_hover        = -1;

static int  g_stage = 0;  /* 0=路径选择页, 1=进度页, 2=劝退页（日文环境拒载） */
static HWND g_edit  = NULL;
static BOOL g_shellOnly = FALSE;

static WCHAR g_signalPath[MAX_PATH];
static WCHAR g_inputPath[MAX_PATH];
static WCHAR g_installDir[MAX_PATH];
static WCHAR g_cancelPath[MAX_PATH];
static WCHAR g_setupExe[MAX_PATH];
static BOOL  g_selectMode = FALSE;  /* --select：选完写输入文件退出；否则 --progress 只显示进度 */

static WCHAR g_stageText[256];  /* transplay 解析出的实时文案 */

typedef HRESULT (WINAPI *SetAumidFn)(PCWSTR);

static HFONT MakeFont(int px, int weight)
{
    return CreateFontW(-px, 0, 0, 0, weight, FALSE, FALSE, FALSE,
        DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
        CLEARTYPE_QUALITY, DEFAULT_PITCH, FONT_FACE);
}

/* ── 日文环境检测（红线 §3.1 检测源 1）：GetUserDefaultUILanguage 的 primary lang
     id 为 0x11（覆盖 LANGID 0x11 / 0x411 / 0xC11 三种日文语言）。 ── */
static BOOL IsJapaneseSystem(void)
{
    return PRIMARYLANGID(GetUserDefaultUILanguage()) == 0x11;
}

#include "card_i18n.h"

/* ── 文案查表：按系统 UI 语言（GetUserDefaultUILanguage 的 primary id）
     返回 28 语言对应文案。日文 primary 0x11 不在表内 → 回落英文（红线）。
     未知语言 → 回落英文（索引 1）。 ── */
static int _i18n_row(void)
{
    int primary = PRIMARYLANGID(GetUserDefaultUILanguage());
    int n = sizeof(card_langmap) / sizeof(card_langmap[0]);
    for (int i = 0; i < n; i++) {
        if (card_langmap[i].langid == primary) return card_langmap[i].idx;
    }
    return 1;  /* en */
}
static const WCHAR *_t(int key) { return card_i18n[_i18n_row()][key]; }

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

static int HitTestDir(int x, int y)
{
    if (x >= 20 && x <= 460 && y >= 105 && y <= 121) return HIT_SHELLONLY;
    if (x >= BTN_BROWSE_X && x <= BTN_BROWSE_X + BTN_BROWSE_W &&
        y >= BTN_BROWSE_Y && y <= BTN_BROWSE_Y + BTN_BROWSE_H) return HIT_BROWSE;
    if (x >= BTN_NEXT_X && x <= BTN_NEXT_X + BTN_NEXT_W &&
        y >= BTN_NEXT_Y && y <= BTN_NEXT_Y + BTN_NEXT_H) return HIT_NEXT;
    if (x >= BTN_CANCEL_X && x <= BTN_CANCEL_X + BTN_CANCEL_W &&
        y >= BTN_CANCEL_Y && y <= BTN_CANCEL_Y + BTN_CANCEL_H) return HIT_CANCEL;
    return HIT_NONE;
}

static int HitTestProgress(int x, int y)
{
    if (x >= BTN_X_X && x <= BTN_X_X + BTN_X_W &&
        y >= BTN_X_Y && y <= BTN_X_Y + BTN_X_H) return HIT_CLOSE;
    return HIT_NONE;
}

/* ── 路径选择页 ── */
static void PaintDirPage(HWND hwnd, HDC hdc)
{
    RECT rc; GetClientRect(hwnd, &rc);
    FillRect(hdc, &rc, g_brBg);
    SetBkMode(hdc, TRANSPARENT);

    SetTextColor(hdc, CLR_BRAND);
    SelectObject(hdc, g_fontBrand);
    RECT rB = {20, 12, CARD_WIDTH-20, 38};
    DrawTextW(hdc, L"Rheia", -1, &rB, DT_LEFT|DT_TOP|DT_SINGLELINE|DT_NOCLIP);

    SetTextColor(hdc, CLR_STAGE);
    SelectObject(hdc, g_fontStage);
    RECT rT = {20, 46, CARD_WIDTH-20, 68};
    DrawTextW(hdc, _t(0), -1, &rT, DT_LEFT|DT_TOP|DT_SINGLELINE|DT_NOCLIP);

    DrawButton(hdc, BTN_BROWSE_X, BTN_BROWSE_Y, BTN_BROWSE_W, BTN_BROWSE_H,
               (g_hover==HIT_BROWSE ? CLR_ACCENT_HOVER : CLR_BAR_FILL),
               CLR_BTN_TEXT, _t(1), 12);

    DrawButton(hdc, BTN_NEXT_X, BTN_NEXT_Y, BTN_NEXT_W, BTN_NEXT_H,
               (g_hover==HIT_NEXT ? CLR_ACCENT_HOVER : CLR_BAR_FILL),
               CLR_BTN_TEXT, _t(2), 13);

    DrawButton(hdc, BTN_CANCEL_X, BTN_CANCEL_Y, BTN_CANCEL_W, BTN_CANCEL_H,
               (g_hover==HIT_CANCEL ? CLR_ACCENT_HOVER : CLR_BAR_FILL),
               CLR_BTN_TEXT, _t(3), 13);

    /* 仅装壳复选框 */
    {
        int cx = 20, cy = 105;
        RECT rbox = {cx, cy, cx + 16, cy + 16};
        HPEN pen = CreatePen(PS_SOLID, 1, g_shellOnly ? CLR_BAR_FILL : 0x555555);
        HGDIOBJ oldPen = SelectObject(hdc, pen);
        HGDIOBJ oldBr = SelectObject(hdc, GetStockObject(NULL_BRUSH));
        RoundRect(hdc, cx, cy, cx + 16, cy + 16, 4, 4);
        SelectObject(hdc, oldPen);
        SelectObject(hdc, oldBr);
        DeleteObject(pen);
        if (g_shellOnly) {
            SetTextColor(hdc, CLR_BAR_FILL);
            SelectObject(hdc, g_fontBtn);
            RECT rCheck = {cx, cy - 1, cx + 16, cy + 16};
            DrawTextW(hdc, L"✓", -1, &rCheck, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        }
        SetTextColor(hdc, CLR_SUB);
        SelectObject(hdc, g_fontSub);
        RECT rTxt = {cx + 24, cy, CARD_WIDTH - 20, cy + 18};
        DrawTextW(hdc, _t(4), -1, &rTxt,
                  DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_NOCLIP);
    }

    SetTextColor(hdc, CLR_COPYRIGHT);
    SelectObject(hdc, g_fontCopy);
    RECT rC = {20, DIR_HEIGHT-26, CARD_WIDTH-20, DIR_HEIGHT-10};
    DrawTextW(hdc, L"© Alexander Lee（李亚霖）", -1, &rC, DT_LEFT|DT_TOP|DT_SINGLELINE|DT_NOCLIP);
}

/* ── 进度页 ── */
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

    DrawButton(hdc, BTN_X_X, BTN_X_Y, BTN_X_W, BTN_X_H,
               CLR_BG, (g_hover==HIT_CLOSE ? CLR_BAR_FILL : CLR_BTN), L"×", 14);

    SetTextColor(hdc, CLR_COPYRIGHT);
    SelectObject(hdc, g_fontCopy);
    RECT rC = {20, PROG_HEIGHT-24, CARD_WIDTH-20, PROG_HEIGHT-8};
    DrawTextW(hdc, L"© Alexander Lee（李亚霖）", -1, &rC, DT_LEFT|DT_TOP|DT_SINGLELINE|DT_NOCLIP);
}

/* ── 写单行文本到 %TEMP% 文件（ANSI，供 Inno LoadStringFromFile 读取）── */
static void WriteLineFile(const WCHAR *path, const WCHAR *text);

/* ── 劝退页（日文环境拒载）：覆写整个路径选择界面，只显示劝退文案，无任何按钮 ── */
static void PaintRejectPage(HWND hwnd, HDC hdc)
{
    RECT rc; GetClientRect(hwnd, &rc);
    FillRect(hdc, &rc, g_brBg);
    SetBkMode(hdc, TRANSPARENT);

    SetTextColor(hdc, CLR_BRAND);
    SelectObject(hdc, g_fontBrand);
    RECT rB = {20, 20, CARD_WIDTH-20, 48};
    DrawTextW(hdc, L"Rheia", -1, &rB, DT_LEFT|DT_TOP|DT_SINGLELINE|DT_NOCLIP);

    SetTextColor(hdc, CLR_STAGE);
    SelectObject(hdc, g_fontStage);
    RECT rT = {20, 60, CARD_WIDTH-20, 90};
    DrawTextW(hdc, L"本软件不提供日文支持，安装程序将自毁。", -1, &rT,
              DT_LEFT|DT_TOP|DT_SINGLELINE|DT_NOCLIP);

    SetTextColor(hdc, CLR_SUB);
    SelectObject(hdc, g_fontSub);
    RECT rJ = {20, 96, CARD_WIDTH-20, 120};
    DrawTextW(hdc, L"本ソフトウェアは日本語をサポートしていません。インストーラーは自己破壊します。",
              -1, &rJ, DT_LEFT|DT_TOP|DT_WORDBREAK|DT_NOCLIP);
}

/* ── 劝退自毁：写 CANCEL（让 setup 引导实例退出）+ 删除 setup.exe 安装包 + 退出 ── */
static void RejectSelfDestruct(HWND hwnd)
{
    if (g_selectMode)
        WriteLineFile(g_inputPath, L"CANCEL");

    if (g_setupExe[0]) {
        /* 删安装包 exe：纯 C 实现（客户机装前可能无 Python）。安装包正被 setup
           引导实例锁定，写 CANCEL 后引导实例退出释放锁。用独立 cmd for /l 循环
           busy-del 直到文件消失（无固定 delay，删掉就走），del 失败空转重试。 */
        WCHAR cmd[MAX_PATH * 2 + 64];
        wsprintfW(cmd,
                  L"cmd.exe /c for /l %%i in (1,1,100000) do @(del /f /q \"%s\" 2>nul & if not exist \"%s\" exit /b)",
                  g_setupExe, g_setupExe);
        STARTUPINFOW si = { sizeof(si) };
        si.dwFlags = STARTF_USESHOWWINDOW;
        si.wShowWindow = SW_HIDE;
        PROCESS_INFORMATION pi = { 0 };
        CreateProcessW(NULL, cmd, NULL, NULL, FALSE,
                       CREATE_NO_WINDOW, NULL, NULL, &si, &pi);
        if (pi.hProcess) CloseHandle(pi.hProcess);
        if (pi.hThread) CloseHandle(pi.hThread);
    }

    DestroyWindow(hwnd);
}

/* ── 目录选择（IFileOpenDialog，跟随系统深色模式）── */
static void BrowseFolder(HWND hwnd)
{
    IFileOpenDialog *dlg = NULL;
    if (FAILED(CoCreateInstance(&CLSID_FileOpenDialog, NULL, CLSCTX_INPROC_SERVER,
                                 &IID_IFileOpenDialog, (void**)&dlg)))
        return;
    DWORD flags;
    dlg->lpVtbl->GetOptions(dlg, &flags);
    dlg->lpVtbl->SetOptions(dlg, flags | FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM);
    dlg->lpVtbl->SetTitle(dlg, _t(0));
    if (SUCCEEDED(dlg->lpVtbl->Show(dlg, hwnd))) {
        IShellItem *item = NULL;
        if (SUCCEEDED(dlg->lpVtbl->GetResult(dlg, &item))) {
            PWSTR path = NULL;
            if (SUCCEEDED(item->lpVtbl->GetDisplayName(item, SIGDN_FILESYSPATH, &path))) {
                SetWindowTextW(g_edit, path);
                CoTaskMemFree(path);
            }
            item->lpVtbl->Release(item);
        }
    }
    dlg->lpVtbl->Release(dlg);
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
    DrawTextW(hdc, _t(7), -1, &rT, DT_LEFT|DT_TOP|DT_SINGLELINE|DT_NOCLIP);

    SetTextColor(hdc, CLR_SUB);
    SelectObject(hdc, g_fontSub);
    RECT rS = {24, 48, 356, 68};
    DrawTextW(hdc, _t(8), -1, &rS, DT_LEFT|DT_TOP|DT_SINGLELINE|DT_NOCLIP);

    DrawButton(hdc, 130, 82, 100, 34, CLR_BAR_FILL, CLR_BTN_TEXT, _t(9), 13);
    DrawButton(hdc, 240, 82, 100, 34, CLR_BAR_FILL, CLR_BTN_TEXT, _t(10), 13);
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

    HWND h = CreateWindowExW(WS_EX_TOOLWINDOW, CLASS_CONFIRM, _t(9),
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

/* ── 写单行文本到 %TEMP% 文件（ANSI，供 Inno LoadStringFromFile 读取）── */
static void WriteLineFile(const WCHAR *path, const WCHAR *text)
{
    HANDLE f = CreateFileW(path, GENERIC_WRITE, 0, NULL,
                           CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (f != INVALID_HANDLE_VALUE) {
        char ansi[1024];
        WideCharToMultiByte(CP_ACP, 0, text, -1, ansi, (int)sizeof(ansi), NULL, NULL);
        DWORD w = 0;
        WriteFile(f, ansi, (DWORD)strlen(ansi), &w, NULL);
        CloseHandle(f);
    }
}

/* ── 取消：写取消标记（select 模式写 CANCEL）后退出 ── */
static void OnCancel(HWND hwnd)
{
    if (g_stage == 0 && g_selectMode) {
        /* 路径选择页取消：直接通知 setup 取消，不弹确认框 */
        WriteLineFile(g_inputPath, L"CANCEL");
        DestroyWindow(hwnd);
        return;
    }
    if (!ShowConfirm(hwnd)) return;

    HANDLE cf = CreateFileW(g_cancelPath, GENERIC_WRITE, 0, NULL,
                            CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (cf != INVALID_HANDLE_VALUE) {
        DWORD w;
        WriteFile(cf, "1", 1, &w, NULL);
        CloseHandle(cf);
    }

    DestroyWindow(hwnd);
}

/* ── 下一步：select 模式启动静默 setup 后切进度页；progress 模式切进度页。
     card 是外层壳，负责启动静默 setup（CreateProcessW 不锁 exe、正常触发一次 UAC）。 ── */
static void OnNext(HWND hwnd)
{
    GetWindowTextW(g_edit, g_installDir, MAX_PATH);
    if (wcslen(g_installDir) == 0) {
        wcscpy(g_installDir, L"C:\\Program Files\\Rheia");
    }

    if (g_selectMode) {
        /* 启动静默 setup：/VERYSILENT 无 Inno UI，/DIR 传路径，/SHELLONLY 传布尔 */
        if (wcslen(g_setupExe) > 0) {
            WCHAR params[MAX_PATH * 2];
            if (g_shellOnly)
                wsprintfW(params, L"/VERYSILENT /SHELLONLY /DIR=\"%s\"", g_installDir);
            else
                wsprintfW(params, L"/VERYSILENT /DIR=\"%s\"", g_installDir);
            WCHAR fullCmd[MAX_PATH * 3];
            wsprintfW(fullCmd, L"\"%s\" %s", g_setupExe, params);
            STARTUPINFOW si = { sizeof(si) };
            si.dwFlags = STARTF_USESHOWWINDOW;
            si.wShowWindow = SW_HIDE;
            PROCESS_INFORMATION pi = { 0 };
            if (CreateProcessW(NULL, fullCmd, NULL, NULL, FALSE,
                               CREATE_NO_WINDOW, NULL, NULL, &si, &pi)) {
                CloseHandle(pi.hThread);
                CloseHandle(pi.hProcess);
            }
        }
        /* 写回输入文件，告知引导实例"已选好路径"，然后 card 退出，由静默实例拉起 --progress */
        WCHAR buf[MAX_PATH * 2];
        wsprintfW(buf, L"%s|%d", g_installDir, g_shellOnly ? 1 : 0);
        WriteLineFile(g_inputPath, buf);
        DestroyWindow(hwnd);
        return;
    }

    ShowWindow(g_edit, SW_HIDE);
    g_stage = 1;
    {
        int sw = GetSystemMetrics(SM_CXSCREEN), sh = GetSystemMetrics(SM_CYSCREEN);
        SetWindowPos(hwnd, NULL, (sw-CARD_WIDTH)/2, (sh-PROG_HEIGHT)/2,
                     CARD_WIDTH, PROG_HEIGHT, SWP_NOZORDER | SWP_NOACTIVATE);
        HRGN rgn = CreateRoundRectRgn(0, 0, CARD_WIDTH+1, PROG_HEIGHT+1, CARD_RADIUS*2, CARD_RADIUS*2);
        SetWindowRgn(hwnd, rgn, TRUE);
    }
    SetTimer(hwnd, TIMER_POLL, 150, NULL);
    SetTimer(hwnd, TIMER_WATCH, 5000, NULL);
    SetTimer(hwnd, TIMER_EASE, 30, NULL);
    InvalidateRect(hwnd, NULL, TRUE);
}

/* ── transplay（translate + display）：读 signal 文件最后一行，解析出进度值 + 文案 + 终止标记 ──
    返回：
       -2 = done（初始化结束，card 自毁）
       -1 = 无有效内容
       0~100 = 进度值（同时 g_stageText 被更新为对应文案）
    signal 每行格式："文案 | 进度值 | complete/done"，card 只读最后一行。 */
static int ReadSignal(void)
{
    FILE *f = _wfopen(g_signalPath, L"r");
    if (!f) return -1;
    char line[512];
    char last[512];
    last[0] = 0;
    while (fgets(line, sizeof(line), f)) {
        strcpy(last, line);
    }
    fclose(f);

    if (last[0] == 0) return -1;

    /* 去尾换行 */
    char *p = last;
    while (*p==' '||*p=='\t'||*p=='\r'||*p=='\n') p++;
    char *end = p + strlen(p);
    while (end>p && (end[-1]==' '||end[-1]=='\t'||end[-1]=='\r'||end[-1]=='\n')) end--;
    *end = '\0';

    /* 去 UTF-8 BOM（EF BB BF）：Inno SaveStringsToUTF8File append 会在行首写 BOM，
       导致 seg1 变成 "\xEF\xBB\xBF__unpack__"，strcmp 匹配失败、内部 token 泄漏到界面。 */
    if ((unsigned char)p[0] == 0xEF && (unsigned char)p[1] == 0xBB && (unsigned char)p[2] == 0xBF) {
        p += 3;
    }

    /* 按 '|' 分割三段：文案 | 进度 | 标记 */
    char *seg1 = p;
    char *seg2 = strchr(seg1, '|');
    if (!seg2) return -1;
    *seg2 = 0; seg2++;
    char *seg3 = strchr(seg2, '|');
    if (seg3) { *seg3 = 0; seg3++; }

    /* 去 seg1 首尾空格：信号格式「文案 | 进度」里 | 前有一个空格，
       seg1 截断后末尾残留空格，strcmp 匹配「__unpack__」会失败、内部 token 泄漏。 */
    char *s1 = seg1;
    while (*s1 == ' ' || *s1 == '\t') s1++;
    char *s1end = s1 + strlen(s1);
    while (s1end > s1 && (s1end[-1] == ' ' || s1end[-1] == '\t')) s1end--;
    *s1end = 0;

    /* 文案 → 宽字符 */
    if (strcmp(s1, "__unpack__") == 0) {
        /* Inno 层写稳定 token，card 侧按系统语言翻译 */
        wcscpy(g_stageText, _t(11));
    } else {
        MultiByteToWideChar(CP_UTF8, 0, s1, -1, g_stageText, 256);
    }

    /* 进度值 */
    int v = atoi(seg2);
    if (v < 0 || v > 100) return -1;

    /* 标记：done 则自毁；error 则停住显示原因（不许静默消失） */
    if (seg3 && strstr(seg3, "done")) return -2;
    if (seg3 && strstr(seg3, "error")) return -3;

    return v;
}

static void QuitCard(HWND hwnd) { DestroyWindow(hwnd); }

static void OnPoll(HWND hwnd)
{
    /* 同一进度值内换了文案（DISM 启 WSL/VMP、导入 Ubuntu 这些阶段）也要重绘，
       否则卡面停在上一句，客户以为卡死。 */
    WCHAR prevStage[256];
    wcscpy(prevStage, g_stageText);
    int v = ReadSignal();
    if (v == -2) { QuitCard(hwnd); return; }
    if (v == -3) {           /* 安装器报错：留窗口、显示原因、不再看门狗自杀 */
        g_error = TRUE;
        g_idleSeconds = 0;
        g_stageCleared = FALSE;
        InvalidateRect(hwnd, NULL, TRUE);
        return;
    }
    if (v < 0) return;
    if (v == g_lastNumeric) {
        if (wcscmp(prevStage, g_stageText) != 0) InvalidateRect(hwnd, NULL, TRUE);
        return;
    }
    if (v < g_lastNumeric && v != 100) return;
    g_lastNumeric = v;
    g_idleSeconds = 0;
    g_target = v;

    if (v == 100) {
        g_stageCleared = TRUE;
        g_idleSince100++;
        if (g_idleSince100 > HOLD_100_SECONDS * 1000 / 150) {
            QuitCard(hwnd);
            return;
        }
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
        if (nd != g_display) {
            g_display = nd;
            InvalidateRect(hwnd, NULL, FALSE);
        }
    } else if (g_displayF != (float)g_target) {
        g_displayF = (float)g_target;
        g_display = g_target;
        InvalidateRect(hwnd, NULL, FALSE);
    }
}

static void OnWatchdog(HWND hwnd)
{
    if (g_error) { g_idleSeconds = 0; return; }  /* 报错态不自杀，等人看见 */
    g_idleSeconds += 5;
    if (g_idleSeconds >= WATCHDOG_SECONDS) QuitCard(hwnd);
}

static LRESULT CALLBACK WndProc(HWND hwnd, UINT msg, WPARAM wParam, LPARAM lParam)
{
    switch (msg) {
    case WM_CREATE: {
        g_fontBrand  = MakeFont(18, FW_BOLD);
        g_fontSub    = MakeFont(12, FW_NORMAL);
        g_fontStage  = MakeFont(14, FW_SEMIBOLD);
        g_fontDetail = MakeFont(12, FW_NORMAL);
        g_fontPct    = MakeFont(12, FW_SEMIBOLD);
        g_fontCopy   = MakeFont(10, FW_NORMAL);
        g_fontBtn    = MakeFont(13, FW_SEMIBOLD);
        g_fontEdit   = MakeFont(12, FW_NORMAL);
        g_fontStageSmall = MakeFont(11, FW_NORMAL);
        g_brBg       = CreateSolidBrush(CLR_BG);
        g_brBgDeep   = CreateSolidBrush(CLR_BG_DEEP);
        g_brPanel    = CreateSolidBrush(CLR_PANEL);
        g_brBarBg    = CreateSolidBrush(CLR_BAR_BG);
        g_brBarFill  = CreateSolidBrush(CLR_BAR_FILL);

        WCHAR pf[MAX_PATH];
        GetEnvironmentVariableW(L"ProgramFiles", pf, MAX_PATH);
        wsprintfW(g_installDir, L"%s\\Rheia", pf);

        g_edit = CreateWindowExW(WS_EX_CLIENTEDGE, L"EDIT", g_installDir,
            WS_CHILD | WS_VISIBLE | ES_AUTOHSCROLL,
            20, 70, 360, 30, hwnd, (HMENU)IDC_EDIT_PATH,
            GetModuleHandle(NULL), NULL);
        SendMessageW(g_edit, WM_SETFONT, (WPARAM)g_fontEdit, TRUE);
        return 0;
    }
    case WM_CTLCOLOREDIT: {
        HDC hdc = (HDC)wParam;
        SetTextColor(hdc, CLR_PCT);
        SetBkColor(hdc, CLR_PANEL);
        return (LRESULT)g_brPanel;
    }
    case WM_PAINT: {
        PAINTSTRUCT ps;
        HDC hdc = BeginPaint(hwnd, &ps);
        if (g_stage == 0) PaintDirPage(hwnd, hdc);
        else if (g_stage == 1) PaintProgressPage(hwnd, hdc);
        else PaintRejectPage(hwnd, hdc);
        EndPaint(hwnd, &ps);
        return 0;
    }
    case WM_MOUSEMOVE: {
        int x = LOWORD(lParam), y = HIWORD(lParam);
        int hit = (g_stage == 0) ? HitTestDir(x, y) : HitTestProgress(x, y);
        if (g_stage == 2) hit = HIT_NONE;
        if (hit != g_hover) { g_hover = hit; InvalidateRect(hwnd, NULL, FALSE); }
        return 0;
    }
    case WM_LBUTTONDOWN: {
        int x = LOWORD(lParam), y = HIWORD(lParam);
        int hit = (g_stage == 0) ? HitTestDir(x, y) : HitTestProgress(x, y);
        if (g_stage == 2) return 0;  /* 劝退页不响应任何点击，也不可拖动 */
        if (hit == HIT_BROWSE) BrowseFolder(hwnd);
        else if (hit == HIT_NEXT) OnNext(hwnd);
        else if (hit == HIT_CANCEL) OnCancel(hwnd);
        else if (hit == HIT_CLOSE) OnCancel(hwnd);
        else if (hit == HIT_SHELLONLY) { g_shellOnly = !g_shellOnly; InvalidateRect(hwnd, NULL, FALSE); }
        else if (y < 100) {
            ReleaseCapture();
            SendMessageW(hwnd, WM_NCLBUTTONDOWN, HTCAPTION, 0);
        }
        return 0;
    }
    case WM_TIMER:
        if (wParam == TIMER_POLL) OnPoll(hwnd);
        else if (wParam == TIMER_WATCH) OnWatchdog(hwnd);
        else if (wParam == TIMER_EASE) OnEase(hwnd);
        else if (wParam == TIMER_REJECT) RejectSelfDestruct(hwnd);
        return 0;
    case WM_DESTROY:
        if (g_fontBrand) DeleteObject(g_fontBrand);
        if (g_fontSub) DeleteObject(g_fontSub);
        if (g_fontStage) DeleteObject(g_fontStage);
        if (g_fontDetail) DeleteObject(g_fontDetail);
        if (g_fontPct) DeleteObject(g_fontPct);
        if (g_fontCopy) DeleteObject(g_fontCopy);
        if (g_fontBtn) DeleteObject(g_fontBtn);
        if (g_fontEdit) DeleteObject(g_fontEdit);
        if (g_fontStageSmall) DeleteObject(g_fontStageSmall);
        if (g_brBg) DeleteObject(g_brBg);
        if (g_brBgDeep) DeleteObject(g_brBgDeep);
        if (g_brPanel) DeleteObject(g_brPanel);
        if (g_brBarBg) DeleteObject(g_brBarBg);
        if (g_brBarFill) DeleteObject(g_brBarFill);
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProcW(hwnd, msg, wParam, lParam);
}

int WINAPI wWinMain(HINSTANCE hInst, HINSTANCE hPrev, PWSTR cmdline, int show)
{
    CoInitialize(NULL);

    HMODULE shell = LoadLibraryW(L"shell32.dll");
    if (shell) {
        SetAumidFn f = (SetAumidFn)(void*)GetProcAddress(shell, "SetCurrentProcessExplicitAppUserModelID");
        if (f) f(L"Rheia.Installer");
    }

    /* signal 统一日志在 %TEMP%\okoc_signal.txt（临时通信文件，卸载不会删 TEMP） */
    GetTempPathW(MAX_PATH, g_signalPath);
    lstrcatW(g_signalPath, L"okoc_signal.txt");
    { /* card 生成 signal：清空旧内容，杜绝残留 done 秒杀 */
        HANDLE sf = CreateFileW(g_signalPath, GENERIC_WRITE, 0, NULL,
                                CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
        if (sf != INVALID_HANDLE_VALUE) CloseHandle(sf);
    }
    GetTempPathW(MAX_PATH, g_inputPath);
    lstrcatW(g_inputPath, INPUT_FILENAME);
    GetTempPathW(MAX_PATH, g_cancelPath);
    lstrcatW(g_cancelPath, CANCEL_FILENAME);

    g_stageText[0] = 0;
    wcscpy(g_stageText, _t(5));

    /* 模式：--select（传值） vs --progress（显示进度）。默认 --select。 */
    g_selectMode = (cmdline == NULL || wcsstr(cmdline, L"--progress") == NULL);

    /* 命令行参数：setup.exe 路径（Inno 引导模式传入 {srcexe}），card 用于启动静默实例 */
    g_setupExe[0] = 0;
    if (cmdline && *cmdline) {
        /* cmdline 形如：--select "C:\...\Rheia-Setup.exe" */
        WCHAR *p = wcsstr(cmdline, L"\"");
        if (p) {
            p++;
            int n = 0;
            while (p[n] && p[n] != L'"' && n < MAX_PATH - 1) {
                g_setupExe[n] = p[n];
                n++;
            }
            g_setupExe[n] = 0;
        }
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

    HWND hwnd = CreateWindowExW(WS_EX_APPWINDOW, CLASS_MAIN, L"okoc_card",
        WS_POPUP, 0, 0, CARD_WIDTH, DIR_HEIGHT, NULL, NULL, hInst, NULL);
    if (!hwnd) return 1;

    HRGN rgn = CreateRoundRectRgn(0, 0, CARD_WIDTH+1, DIR_HEIGHT+1, CARD_RADIUS*2, CARD_RADIUS*2);
    SetWindowRgn(hwnd, rgn, TRUE);

    int sw = GetSystemMetrics(SM_CXSCREEN), sh = GetSystemMetrics(SM_CYSCREEN);
    SetWindowPos(hwnd, NULL, (sw-CARD_WIDTH)/2, (sh-DIR_HEIGHT)/2, 0,0,
        SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE);

    /* 日文环境红线：检测命中 → 覆写路径选择页为劝退页，6 秒后自毁。
       只判 primary lang id（0x11），不做佐证（这是安装程序，用系统 UI 语言单条即可）。 */
    if (IsJapaneseSystem()) {
        if (g_edit) { DestroyWindow(g_edit); g_edit = NULL; }
        g_stage = 2;
        SetTimer(hwnd, TIMER_REJECT, 6000, NULL);
    }

    /* --progress 模式：不显示路径页，直接进进度页（销毁 EDIT 控件）。
       但日文环境已进劝退页（g_stage==2）时不覆盖，守住红线。 */
    if (!g_selectMode && g_stage != 2) {
        if (g_edit) { DestroyWindow(g_edit); g_edit = NULL; }
        g_stage = 1;
        SetWindowPos(hwnd, NULL, (sw-CARD_WIDTH)/2, (sh-PROG_HEIGHT)/2,
                     CARD_WIDTH, PROG_HEIGHT, SWP_NOZORDER | SWP_NOACTIVATE);
        HRGN rgn2 = CreateRoundRectRgn(0, 0, CARD_WIDTH+1, PROG_HEIGHT+1, CARD_RADIUS*2, CARD_RADIUS*2);
        SetWindowRgn(hwnd, rgn2, TRUE);
        SetTimer(hwnd, TIMER_POLL, 150, NULL);
        SetTimer(hwnd, TIMER_WATCH, 5000, NULL);
        SetTimer(hwnd, TIMER_EASE, 30, NULL);
    }

    ShowWindow(hwnd, SW_SHOW);
    UpdateWindow(hwnd);

    MSG msg;
    while (GetMessageW(&msg, NULL, 0, 0) > 0) {
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }
    if (shell) FreeLibrary(shell);
    CoUninitialize();
    return (int)msg.wParam;
}
