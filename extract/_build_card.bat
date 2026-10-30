@echo off
cd /d "%~dp0"

set CL_EXE="C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Tools\MSVC\14.51.36231\bin\Hostx64\x64\cl.exe"
set SDK=C:\Program Files (x86)\Windows Kits\10
set INC=%SDK%\Include\10.0.26100.0
set LIB=%SDK%\Lib\10.0.26100.0
set MSVC_INC=C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Tools\MSVC\14.51.36231\include
set MSVC_LIB=C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Tools\MSVC\14.51.36231\lib\x64
set RC_EXE="%SDK%\bin\10.0.26100.0\x64\rc.exe"

%RC_EXE% /nologo /i "%INC%\um" /i "%INC%\shared" /fo card.res card.rc || exit /b 1
%CL_EXE% /nologo /utf-8 /O2 /W3 /D_CRT_SECURE_NO_WARNINGS ^
  /I"%INC%\um" /I"%INC%\shared" /I"%INC%\ucrt" /I"%MSVC_INC%" ^
  card.c card.res /Fe:card_new.exe ^
  /link /LIBPATH:"%LIB%\um\x64" /LIBPATH:"%LIB%\ucrt\x64" /LIBPATH:"%MSVC_LIB%" ^
  user32.lib gdi32.lib shell32.lib ole32.lib comctl32.lib

echo EXIT CODE: %ERRORLEVEL%
