/* L1-1133：uninstall.c */














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


#define CARD_WIDTH  500
#define LIST_HEIGHT 250   
#define PROG_HEIGHT 168   
#define CARD_RADIUS 14


#define CLR_BG        0x1A1A1A   
#define CLR_BG_DEEP   0x0A0A0A   
#define CLR_BRAND     0xFFFFFF   
#define CLR_SUB       0xB0B0B0   
#define CLR_STAGE     0xE0E0E0   
#define CLR_DETAIL    0xA0A0A0   
#define CLR_COPYRIGHT 0x555555   
#define CLR_SEP       0x333333   
#define CLR_ERROR     0x3C4CE7   
#define CLR_ERROR_HOV 0x2B39C0   
#define CLR_ACCENT    0x2170F3   
#define CLR_ACCENT_HOV 0x3388FF  
#define CLR_BTN_TEXT  0x0A0A0A   
#define CLR_BAR_BG    0x333333   
#define CLR_PCT       0xE0E0E0   

#define FONT_FACE L"Microsoft YaHei UI"


#define SIGNAL_FILENAME L"signal"
#define TIMER_POLL 1
#define TIMER_EASE 2
#define WATCHDOG_SECONDS 600
#define HOLD_100_SECONDS 2


#define IDI_APPICON 1

#define CLASS_MAIN L"RheiaUninstall"
#define CLASS_CONFIRM L"RheiaUninstallConfirm"


#define BTN_CANCEL_X 20
#define BTN_CANCEL_Y 196
#define BTN_CANCEL_W 80
#define BTN_CANCEL_H 32
#define BTN_UNINST_X 360
#define BTN_UNINST_Y 196
#define BTN_UNINST_W 120
#define BTN_UNINST_H 32


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
static int g_stage = 0;            
static int g_display = 0;
static float g_displayF = 0.0f;
static int g_target = 0;
static int g_lastNumeric = -1;
static int g_idleSeconds = 0;
static int g_idleSince100 = 0;
static BOOL g_stageCleared = FALSE;
static BOOL g_keepOpenclaw = FALSE;  
static BOOL g_busy = FALSE;          

static WCHAR g_signalPath[MAX_PATH];
static WCHAR g_stageText[256];
enum { HIT_NONE=0, HIT_CANCEL, HIT_UNINST, HIT_KEEP };  

static WCHAR g_exeDir[MAX_PATH];  

static HFONT MakeFont(int px, int weight)
{
    return CreateFontW(-px, 0, 0, 0, weight, FALSE, FALSE, FALSE,
        DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
        CLEARTYPE_QUALITY, DEFAULT_PITCH, FONT_FACE);
}

#include "uninstall_i18n.h"


static int _i18n_row(void)
{
    int primary = PRIMARYLANGID(GetUserDefaultUILanguage());
    int n = sizeof(uninstall_langmap) / sizeof(uninstall_langmap[0]);
    for (int i = 0; i < n; i++) {
        if (uninstall_langmap[i].langid == primary) return uninstall_langmap[i].idx;
    }
    return 1;  
}
static const WCHAR *_t(int key) { return uninstall_i18n[_i18n_row()][key]; }


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


static int HitTest(int x, int y)
{
    if (x >= 20 && x <= 300 && y >= 176 && y <= 192) return HIT_KEEP;  
    if (x >= BTN_CANCEL_X && x <= BTN_CANCEL_X + BTN_CANCEL_W &&
        y >= BTN_CANCEL_Y && y <= BTN_CANCEL_Y + BTN_CANCEL_H) return HIT_CANCEL;
    if (x >= BTN_UNINST_X && x <= BTN_UNINST_X + BTN_UNINST_W &&
        y >= BTN_UNINST_Y && y <= BTN_UNINST_Y + BTN_UNINST_H) return HIT_UNINST;
    return HIT_NONE;
}


static void PaintCard(HWND hwnd, HDC hdc)
{
    RECT rc; GetClientRect(hwnd, &rc);
    FillRect(hdc, &rc, g_brBg);
    SetBkMode(hdc, TRANSPARENT);

    
    SetTextColor(hdc, CLR_BRAND);
    SelectObject(hdc, g_fontBrand);
    RECT rB = {20, 12, CARD_WIDTH-20, 38};
    DrawTextW(hdc, L"Rheia", -1, &rB, DT_LEFT|DT_TOP|DT_SINGLELINE|DT_NOCLIP);

    
    SetTextColor(hdc, CLR_SUB);
    SelectObject(hdc, g_fontSub);
    RECT rS = {20, 40, CARD_WIDTH-20, 58};
    DrawTextW(hdc, _t(0), -1, &rS, DT_LEFT|DT_TOP|DT_SINGLELINE|DT_NOCLIP);

    
    RECT rSep = {20, 64, CARD_WIDTH-20, 65};
    FillRect(hdc, &rSep, g_brSep);

    
    SetTextColor(hdc, CLR_ERROR);
    SelectObject(hdc, g_fontSub);
    RECT rDel = {20, 72, CARD_WIDTH-20, 90};
    DrawTextW(hdc, _t(1), -1, &rDel, DT_LEFT|DT_TOP|DT_SINGLELINE|DT_NOCLIP);

    
    int y = 94;
    static const int item_name_key[4] = {2, 4, 6, 8};
    static const int item_desc_key[4] = {3, 5, 7, 9};
    for (int i = 0; i < 4; i++) {
        
        SetTextColor(hdc, CLR_ERROR);
        SelectObject(hdc, g_fontSub);
        RECT rDot = {20, y, 30, y + 20};
        DrawTextW(hdc, L"•", -1, &rDot, DT_LEFT|DT_TOP|DT_SINGLELINE|DT_NOCLIP);

        
        SetTextColor(hdc, CLR_STAGE);
        SelectObject(hdc, g_fontSub);
        RECT rName = {34, y, 240, y + 20};
        DrawTextW(hdc, _t(item_name_key[i]), -1, &rName, DT_LEFT|DT_TOP|DT_SINGLELINE|DT_NOCLIP);

        
        SetTextColor(hdc, CLR_DETAIL);
        SelectObject(hdc, g_fontCopy);
        RECT rDesc = {250, y + 2, CARD_WIDTH-20, y + 20};
        DrawTextW(hdc, _t(item_desc_key[i]), -1, &rDesc, DT_LEFT|DT_TOP|DT_SINGLELINE|DT_NOCLIP);

        y += 22;
    }

    
    {
        int cx = 20, cy = 176;
        
        RECT rbox = {cx, cy, cx + 16, cy + 16};
        HPEN pen = CreatePen(PS_SOLID, 1, g_keepOpenclaw ? CLR_ACCENT : 0x555555);
        HGDIOBJ oldPen = SelectObject(hdc, pen);
        HGDIOBJ oldBr = SelectObject(hdc, GetStockObject(NULL_BRUSH));
        RoundRect(hdc, cx, cy, cx + 16, cy + 16, 4, 4);
        SelectObject(hdc, oldPen);
        SelectObject(hdc, oldBr);
        DeleteObject(pen);
        if (g_keepOpenclaw) {
            
            SetTextColor(hdc, CLR_ACCENT);
            SelectObject(hdc, g_fontBtn);
            RECT rCheck = {cx, cy - 1, cx + 16, cy + 16};
            DrawTextW(hdc, L"✓", -1, &rCheck, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        }
        
        SetTextColor(hdc, CLR_STAGE);
        SelectObject(hdc, g_fontSub);
        RECT rTxt = {cx + 24, cy, CARD_WIDTH - 20, cy + 18};
        DrawTextW(hdc, _t(10), -1, &rTxt, DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_NOCLIP);
    }

    
    if (g_hover == HIT_CANCEL)
        DrawButton(hdc, BTN_CANCEL_X, BTN_CANCEL_Y, BTN_CANCEL_W, BTN_CANCEL_H,
                   0x2A2A2A, CLR_STAGE, _t(14), 12);
    else {
        
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

    
    DrawButton(hdc, BTN_UNINST_X, BTN_UNINST_Y, BTN_UNINST_W, BTN_UNINST_H,
               (g_hover==HIT_UNINST ? CLR_ERROR_HOV : CLR_ERROR),
               0xFFFFFF, _t(13), 12);

    
    SetTextColor(hdc, CLR_COPYRIGHT);
    SelectObject(hdc, g_fontCopy);
    RECT rC = {20, LIST_HEIGHT-24, CARD_WIDTH-20, LIST_HEIGHT-8};
    DrawTextW(hdc, L"© Alexander Lee（李亚霖）", -1, &rC, DT_CENTER|DT_TOP|DT_SINGLELINE|DT_NOCLIP);
}


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

    
    DrawButton(hdc, 130, 82, 100, 34, CLR_ACCENT, CLR_BTN_TEXT, _t(13), 13);
    
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
    RunHiddenSyncRc(cmd, 30000, FALSE);   
}




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



static void KillShellBrowser(void)
{
    





    RunHiddenSync(
        L"powershell -NoProfile -EncodedCommand "
        L"RwBlAHQALQBDAGkAbQBJAG4AcwB0AGEAbgBjAGUAIABXAGkAbgAzADIAXwBQAHIAbwBjAGUAcwBzACAALQBGAGkAbAB0AGUAcgAgACIATgBhAG0AZQA9ACcAcAB5AHQAaABvAG4AdwAuAGUAeABlACcAIgAgAC0ARQByAHIAbwByAEEAYwB0AGkAbwBuACAAUwBpAGwAZQBuAHQAbAB5AEMAbwBuAHQAaQBuAHUAZQAgAHwAIABXAGgAZQByAGUALQBPAGIAagBlAGMAdAAgAHsAIAAkAF8ALgBFAHgAZQBjAHUAdABhAGIAbABlAFAAYQB0AGgAIAAtAGwAaQBrAGUAIAAnACoAUAB5AHQAaABvAG4AUwBpAGQAZQA2ACoAcAB5AHQAaABvAG4AdwAuAGUAeABlACcAIAAtAGEAbgBkACAAJABfAC4AQwBvAG0AbQBhAG4AZABMAGkAbgBlACAALQBsAGkAawBlACAAJwAqAG0AYQBpAG4AXABtAGEAaQBuAC4AcAB5ACoAJwAgAH0AIAB8ACAARgBvAHIARQBhAGMAaAAtAE8AYgBqAGUAYwB0ACAAewAgAFMAdABvAHAALQBQAHIAbwBjAGUAcwBzACAALQBJAGQAIAAkAF8ALgBQAHIAbwBjAGUAcwBzAEkAZAAgAC0ARgBvAHIAYwBlACAALQBFAHIAcgBvAHIAQQBjAHQAaQBvAG4AIABTAGkAbABlAG4AdABsAHkAQwBvAG4AdABpAG4AdQBlACAAfQA="
    );
}



static void WriteSignalUtf8(const WCHAR *text, int pct, const char *mark);




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
    return (*k == '"' && *(k + 1) != 0 && *(k + 1) != '"');   
}



static BOOL WslDistroPresent(void)
{
    return GetFileAttributesW(L"C:\\wsl-distro") != INVALID_FILE_ATTRIBUTES;
}




static BOOL WslSlotReaderPath(WCHAR *out, int cap)
{
    WCHAR exe[MAX_PATH], dir[MAX_PATH], tail[MAX_PATH];
    if (!GetModuleFileNameW(NULL, exe, MAX_PATH)) return FALSE;
    lstrcpyW(dir, exe);
    WCHAR *slash = wcsrchr(dir, L'\\');
    if (slash) *slash = 0;
    lstrcpyW(tail, dir + 2);                     
    WCHAR *q;
    for (q = tail; *q; q++) if (*q == L'\\') *q = L'/';
    WCHAR drive = (WCHAR)((dir[0] >= L'A' && dir[0] <= L'Z') ? dir[0] + 32 : dir[0]);
    if (wsprintfW(out, L"/mnt/%c%s/personal-slot/persona/indivi.py", drive, tail) >= cap) return FALSE;
    return TRUE;
}


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
        wsprintfW(l, L"弹卡带：提权=%d wsl=%s", IsElevated() ? 1 : 0, wslExe);
        LogLine(l);
    }
    DWORD rc = RunElevatedSyncRc(cmd, INFINITE, FALSE);
    wsprintfW(l, L"弹卡带 rc=%lu（提权=%d）", (unsigned long)rc, IsElevated() ? 1 : 0);
    LogLine(l);
    return rc;
}


















static void RemoveOurPlugin(void)
{
    WCHAR script[MAX_PATH * 2], cmd[MAX_PATH * 4], l[256];
    if (!WslSlotReaderPath(script, MAX_PATH * 2)) return;
    wsprintfW(cmd,
              L"wsl -d Ubuntu -u root -- python3 \"%s\" --uninstall-plugin "
              L">> \"%%TEMP%%\\rheia-slot-plugin.log\" 2>&1",
              script);
    DWORD rc = RunHiddenSyncRc(cmd, INFINITE, FALSE);
    wsprintfW(l, L"摘插件 --uninstall-plugin rc=%lu", (unsigned long)rc);
    LogLine(l);
}



static void WriteSignalUtf8(const WCHAR *text, int pct, const char *mark)
{
    HANDLE f = CreateFileW(g_signalPath, FILE_APPEND_DATA, 0, NULL,
                           OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (f == INVALID_HANDLE_VALUE) return;
    char utf8[512];
    int n = WideCharToMultiByte(CP_UTF8, 0, text, -1, utf8, sizeof(utf8)-1, NULL, NULL);
    if (n > 0) utf8[n-1] = 0; 
    char line[600];
    int len = wsprintfA(line, "%s | %d | %s\r\n", utf8, pct, mark);
    DWORD w = 0;
    WriteFile(f, line, len, &w, NULL);
    CloseHandle(f);
}



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
    



    {
        WCHAR selfpath[MAX_PATH];
        GetModuleFileNameW(NULL, selfpath, MAX_PATH);

        WCHAR tmp[MAX_PATH], target[MAX_PATH];
        if (GetTempPathW(MAX_PATH, tmp)) {
            wsprintfW(target, L"%sRheia-uninstall-bak.exe", tmp);
            
            DeleteFileW(target);
            if (!MoveFileExW(selfpath, target, MOVEFILE_REPLACE_EXISTING)) {
                
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













static void DoUninstall(HWND hwnd)
{
    
    HANDLE sf = CreateFileW(g_signalPath, GENERIC_WRITE, 0, NULL,
                            CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (sf != INVALID_HANDLE_VALUE) CloseHandle(sf);

    

    g_busy = TRUE;
    ShowProgressPage(hwnd);
    {
        WCHAR l[256];
        wsprintfW(l, L"── 卸载开始：g_keepOpenclaw=%d 卡带在槽=%d WSL在=%d",
                  g_keepOpenclaw ? 1 : 0, CardIsLoaded() ? 1 : 0,
                  WslDistroPresent() ? 1 : 0);
        LogLine(l);
    }

    
    BOOL shellKilled = FALSE;
    if (CardIsLoaded() && WslDistroPresent()) {
        ShowStep(hwnd, _t(19), 3, "complete");      
        

        KillShellBrowser();
        shellKilled = TRUE;
        DWORD rc = EjectCard();
        {
            WCHAR l[128];
            wsprintfW(l, L"弹卡带 rc=%lu", (unsigned long)rc);
            LogLine(l);
        }
        if (rc != 0) {
            



            WCHAR l2[192];
            wsprintfW(l2, L"弹卡带未成 rc=%lu ⇒ 记账后继续卸载（不阻断）", (unsigned long)rc);
            LogLine(l2);
        }
    }

    

    if (!shellKilled) KillShellBrowser();

    


    ShowStep(hwnd, _t(16), 10, "complete");
    if (!g_keepOpenclaw) {
        RunHiddenSync(L"wsl --terminate Ubuntu >nul 2>&1");
        RunHiddenSync(L"wsl --unregister Ubuntu >nul 2>&1");
        

        RunHiddenSync(L"rmdir /s /q \"C:\\wsl-distro\" >nul 2>&1");
        WCHAR dotopenclaw[MAX_PATH];
        GetEnvironmentVariableW(L"USERPROFILE", dotopenclaw, MAX_PATH);
        lstrcatW(dotopenclaw, L"\\.openclaw");
        WCHAR cmd[MAX_PATH + 64];
        wsprintfW(cmd, L"rmdir /s /q \"%s\" >nul 2>&1", dotopenclaw);
        RunHiddenSync(cmd);
        LogLine(L"WSL 已清：terminate/unregister/C:\\wsl-distro/~/.openclaw");
    } else {
        





        if (WslDistroPresent()) {
            ShowStep(hwnd, _t(20), 12, "complete");
            RemoveOurPlugin();
        } else {
            LogLine(L"g_keepOpenclaw=TRUE 且 WSL 不在 ⇒ 没有可摘的插件");
        }
        LogLine(L"g_keepOpenclaw=TRUE ⇒ 客户资产（WSL/OpenClaw/卡带）一律不动");
    }

    



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

    
    ShowStep(hwnd, _t(18), 100, "done");
    g_busy = FALSE;
}


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
        

        if (wParam == TIMER_EASE) OnEase(hwnd);
        else if (wParam == TIMER_POLL && !g_busy) OnPoll(hwnd);
        return 0;
    case WM_CLOSE:
        if (g_busy) return 0;          
        DestroyWindow(hwnd);
        return 0;
    case WM_MOUSEMOVE: {
        int x = LOWORD(lParam), y = HIWORD(lParam);
        int hit = HitTest(x, y);
        if (hit != g_hover) { g_hover = hit; InvalidateRect(hwnd, NULL, FALSE); }
        return 0;
    }
    case WM_LBUTTONDOWN: {
        if (g_busy) return 0;          
        int x = LOWORD(lParam), y = HIWORD(lParam);
        int hit = HitTest(x, y);
        if (hit == HIT_CANCEL) DestroyWindow(hwnd);
        else if (hit == HIT_UNINST) DoUninstall(hwnd);
        else if (hit == HIT_KEEP) {
            g_keepOpenclaw = !g_keepOpenclaw;  
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


int WINAPI wWinMain(HINSTANCE hInst, HINSTANCE hPrev, PWSTR cmdline, int show)
{
    




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
                return 0;  
            }
            


            MessageBoxW(NULL, L"卸载需要管理员权限，已取消。", L"Rheia", MB_OK | MB_ICONWARNING);
            return 1;
        }
    }

    
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

    { 
        GetTempPathW(MAX_PATH, g_signalPath);
        lstrcatW(g_signalPath, L"okoc_signal.txt");
    }
    wcscpy(g_stageText, _t(15));

    


    if (PRIMARYLANGID(GetUserDefaultUILanguage()) == 0x11) {
        g_keepOpenclaw = TRUE;  
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
