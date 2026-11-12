@echo off
cd /d "E:\Coding\Rheia\extract"

set RC_EXE=C:\Program Files (x86)\Windows Kits\10\bin\10.0.26100.0\x64\rc.exe
set CL_EXE=C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Tools\MSVC\14.51.36231\bin\Hostx64\x64\cl.exe
set INC=C:\Program Files (x86)\Windows Kits\10\Include\10.0.26100.0
set LIB=C:\Program Files (x86)\Windows Kits\10\Lib\10.0.26100.0
set MSVC_INC=C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Tools\MSVC\14.51.36231\include
set MSVC_LIB=C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Tools\MSVC\14.51.36231\lib\x64

"%RC_EXE%" /nologo /i "%INC%\um" /i "%INC%\shared" /fo uninstall.res uninstall.rc || exit /b 1
"%CL_EXE%" /nologo /utf-8 /O2 /W3 /D_CRT_SECURE_NO_WARNINGS /I"%INC%\um" /I"%INC%\shared" /I"%INC%\ucrt" /I"%MSVC_INC%" uninstall.c uninstall.res /Fe:uninstall_new.exe /link /LIBPATH:"%LIB%\um\x64" /LIBPATH:"%LIB%\ucrt\x64" /LIBPATH:"%MSVC_LIB%" user32.lib gdi32.lib shell32.lib advapi32.lib || exit /b 1
copy /y uninstall_new.exe uninstall.exe >nul
echo BUILD OK
