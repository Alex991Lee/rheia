#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <wchar.h>
#include <stdio.h>
#include <stdlib.h>

static HANDLE gLog = INVALID_HANDLE_VALUE;

static void logLine(const wchar_t *fmt, ...)
{
    if (gLog == INVALID_HANDLE_VALUE) return;
    SYSTEMTIME st;
    GetLocalTime(&st);
    wchar_t body[2048];
    va_list ap;
    va_start(ap, fmt);
    _vsnwprintf_s(body, _countof(body), _TRUNCATE, fmt, ap);
    va_end(ap);
    wchar_t line[2304];
    _snwprintf_s(line, _countof(line), _TRUNCATE,
                 L"[%04d-%02d-%02d %02d:%02d:%02d] %ls\r\n",
                 st.wYear, st.wMonth, st.wDay, st.wHour, st.wMinute, st.wSecond, body);
    int need = WideCharToMultiByte(CP_UTF8, 0, line, -1, NULL, 0, NULL, NULL);
    if (need <= 1) return;
    char *utf8 = (char *)HeapAlloc(GetProcessHeap(), 0, (SIZE_T)need);
    if (utf8 == NULL) return;
    WideCharToMultiByte(CP_UTF8, 0, line, -1, utf8, need, NULL, NULL);
    DWORD wrote = 0;
    WriteFile(gLog, utf8, (DWORD)(need - 1), &wrote, NULL);
    HeapFree(GetProcessHeap(), 0, utf8);
}

static BOOL openLog(const wchar_t *path)
{
    gLog = CreateFileW(path, FILE_APPEND_DATA,
                       FILE_SHARE_READ | FILE_SHARE_WRITE, NULL,
                       OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    return gLog != INVALID_HANDLE_VALUE;
}

static void closeLog(void)
{
    if (gLog != INVALID_HANDLE_VALUE) {
        CloseHandle(gLog);
        gLog = INVALID_HANDLE_VALUE;
    }
}

static void defaultLogPath(wchar_t *out, int cap)
{
    DWORD n = GetEnvironmentVariableW(L"PROGRAMDATA", out, (DWORD)cap);
    if (n == 0 || n >= (DWORD)cap) {
        wcscpy_s(out, cap, L".");
    } else {
        wcscat_s(out, cap, L"\\Rheia\\tlc_supervisor.log");
    }
}

static BOOL createTarget(const wchar_t *exe, const wchar_t *args,
                         PROCESS_INFORMATION *pi)
{
    wchar_t *cmd = NULL;
    size_t exeLen = wcslen(exe);
    size_t argsLen = args ? wcslen(args) : 0;
    size_t cap = exeLen + argsLen + 4;
    cmd = (wchar_t *)HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, cap * sizeof(wchar_t));
    if (cmd == NULL) return FALSE;
    wcscpy_s(cmd, cap, L"\"");
    wcscat_s(cmd, cap, exe);
    wcscat_s(cmd, cap, L"\"");
    if (args && args[0]) {
        wcscat_s(cmd, cap, L" ");
        wcscat_s(cmd, cap, args);
    }
    STARTUPINFOW si;
    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    ZeroMemory(pi, sizeof(*pi));
    BOOL ok = CreateProcessW(exe, cmd, NULL, NULL, FALSE,
                             CREATE_UNICODE_ENVIRONMENT, NULL, NULL, &si, pi);
    if (!ok) {
        logLine(L"create failed exe=%ls error=%lu", exe, GetLastError());
    }
    HeapFree(GetProcessHeap(), 0, cmd);
    return ok;
}

static int waitOne(HANDLE process, DWORD *exitCode)
{
    DWORD wr = WaitForSingleObject(process, INFINITE);
    if (wr != WAIT_OBJECT_0) {
        logLine(L"wait failed handle=%p result=%lu error=%lu",
                process, wr, GetLastError());
        return 1;
    }
    if (!GetExitCodeProcess(process, exitCode)) {
        logLine(L"exit code read failed error=%lu", GetLastError());
        return 1;
    }
    return 0;
}

int wmain(int argc, wchar_t **argv)
{
    DWORD pid = 0;
    const wchar_t *exe = NULL;
    const wchar_t *args = NULL;
    const wchar_t *logPath = NULL;
    int restart = 0;
    int maxRestarts = 3;
    wchar_t defaultLog[MAX_PATH];

    for (int i = 1; i < argc; i++) {
        if (wcscmp(argv[i], L"--pid") == 0 && i + 1 < argc) {
            pid = (DWORD)_wtoi(argv[++i]);
        } else if (wcscmp(argv[i], L"--exe") == 0 && i + 1 < argc) {
            exe = argv[++i];
        } else if (wcscmp(argv[i], L"--args") == 0 && i + 1 < argc) {
            args = argv[++i];
        } else if (wcscmp(argv[i], L"--log") == 0 && i + 1 < argc) {
            logPath = argv[++i];
        } else if (wcscmp(argv[i], L"--restart") == 0) {
            restart = 1;
        } else if (wcscmp(argv[i], L"--max-restarts") == 0 && i + 1 < argc) {
            maxRestarts = _wtoi(argv[++i]);
        }
    }

    if (logPath == NULL) {
        defaultLogPath(defaultLog, _countof(defaultLog));
        logPath = defaultLog;
    }
    if (!openLog(logPath)) {
        wprintf(L"cannot open log: %ls (error=%lu)\n", logPath, GetLastError());
        return 2;
    }

    logLine(L"supervisor start pid=%lu exe=%ls restart=%d", GetCurrentProcessId(),
            exe ? exe : L"(attach)", restart);

    if (pid != 0) {
        HANDLE h = OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION,
                               FALSE, pid);
        if (h == NULL) {
            logLine(L"open process failed pid=%lu error=%lu", pid, GetLastError());
            closeLog();
            return 2;
        }
        DWORD code = 0;
        int rc = waitOne(h, &code);
        if (rc == 0) {
            logLine(L"exit pid=%lu code=0x%08lX decimal=%lu", pid,
                    (unsigned long)code, (unsigned long)code);
        }
        CloseHandle(h);
        closeLog();
        return rc;
    }

    if (exe == NULL) {
        logLine(L"missing --pid or --exe");
        closeLog();
        return 2;
    }

    int starts = 0;
    for (;;) {
        PROCESS_INFORMATION pi;
        if (!createTarget(exe, args, &pi)) {
            closeLog();
            return 3;
        }
        starts++;
        logLine(L"target start pid=%lu attempt=%d", pi.dwProcessId, starts);
        DWORD code = 0;
        int rc = waitOne(pi.hProcess, &code);
        if (rc == 0) {
            logLine(L"target exit pid=%lu code=0x%08lX decimal=%lu",
                    pi.dwProcessId, (unsigned long)code, (unsigned long)code);
        }
        CloseHandle(pi.hThread);
        CloseHandle(pi.hProcess);
        if (rc != 0) break;
        if (!restart || code == 0) break;
        if (starts >= maxRestarts + 1) {
            logLine(L"restart limit reached count=%d", starts - 1);
            break;
        }
        logLine(L"restart after exit code=0x%08lX attempt=%d",
                (unsigned long)code, starts);
    }

    closeLog();
    return 0;
}
