# Rheia

**Server-grade OpenClaw on Windows — turned into one double-click.**

Download, double-click, wait for the progress bar — and you get the same OpenClaw you would have on a Linux server: official package, official gateway, starts at boot, with a UI.

**English** | **[简体中文](自述文件.md)**

**[⬇ Download Rheia-Setup.exe](./Rheia-Setup.exe)**

📖 **[Why Rheia exists](https://alex991lee.github.io/rheia/blog/en/install-openclaw-on-windows.html)** · 3.55 MB · SHA256 `7fe98d80e418b7074577f6a3effbe106e537c68ba832a5e926213f944811f4e3`

![GitHub stars](https://img.shields.io/github/stars/Alex991Lee/rheia?style=social)
![GitHub forks](https://img.shields.io/github/forks/Alex991Lee/rheia?style=social)
![GitHub release](https://img.shields.io/github/v/release/Alex991Lee/rheia)
![GitHub last commit](https://img.shields.io/github/last-commit/Alex991Lee/rheia)
![License](https://img.shields.io/github/license/Alex991Lee/rheia)
![Platform](https://img.shields.io/badge/platform-Windows%20x64-0078D6)

<p align="center"><img src="./docs/screenshot.png" alt="Rheia main window" width="880"></p>

---

## How this differs from other Windows options

There are three ways to run OpenClaw on Windows today. Both official paths work — but Rheia will be your best option:

| | Official PowerShell install | Official Windows Hub | **Rheia** |
|---|---|---|---|
| Form | Command-line install | WinUI desktop app | **Desktop shell** |
| Runtime | Native Windows process | Provisions its own app-owned WSL distro (`OpenClawGateway`) | **Your existing WSL2 + Ubuntu** |
| Native OpenClaw | Yes | Yes | **Yes (official npm package, unmodified)** |
| Admin required | Manual steps | No | **Yes — the price of systemd-grade reliability** |
| Existing Ubuntu | Not involved | Lives alongside it — a second distro on disk | **Reused, zero waste** |
| Start at boot | You set it up yourself | Tray app | **systemd service, starts with the machine** |
| Downloads in mainland China | Direct to upstream | Direct to upstream | **Automatic domestic / international mirror selection** |
| UI | None (browser) | Official Companion | **Embedded browser + management panels, 29 languages** |

Still annoyed at yourself for mistyping one character while installing native OpenClaw? Double-click `Rheia-Setup.exe`, go pour yourself a coffee — you may find OpenClaw already usable before the cup is empty.

**The official Windows builds have had a few public failures** (links verifiable, all closed):

- 2026-06 · preflight abort with `WSL version output did not include a parseable WSL version`. Root cause: **on non-English systems `wsl --version` localises its field names** (this case was a Swedish locale). Fixed the next day ([#694](https://github.com/openclaw/openclaw-windows-node/issues/694))
- 2026-06 · a fresh install did not register the `OpenClawGateway` distro; fixed within four days ([#775](https://github.com/openclaw/openclaw-windows-node/issues/775))
- 2026-02 · `gateway connect` failing inside WSL with `gateway closed (1008): pairing required` ([openclaw#22445](https://github.com/openclaw/openclaw/issues/22445)) — marked **closed as not planned** upstream

Rheia and the official Windows Hub diverge — purely because the design philosophies differ. Hub creates a dedicated `OpenClawGateway` distro for OpenClaw; note that this is how the official docs define it: not a defect, a choice. Rheia reuses the Ubuntu you already have. Both are valid — the only difference is this: if you already run an Ubuntu, Rheia will not make you feed a second one.

Rheia holds to one principle: **stay faithful to the official distribution, and turn Ubuntu into the best productivity base for one-person companies and small founding teams.**

---

## 29 languages, not one of them bolted on

The sidebar, every panel, every dialog and the installer wizard ship in **29 languages**:

`ar bg ca cs da de el en es fa fi fr he hr hu it ka ko lv nl nn pl pt_BR ru sk sv tr uk zh`

The language is read from the Windows display language at startup; `RHEIA_LOCALE` overrides it, and anything unsupported falls back to English rather than to a half-translated screen. Buttons size themselves to their label first, shrink the font second, wrap third, and move to an extra row last — **a long Russian label never ends up underneath its neighbour**.

The wizard, the install log and the panels share one string table, so a language fixed once is fixed everywhere.

---

## Five things worth knowing

### 1. It picks the right network path during install

The installer probes your network automatically: users in mainland China go through mirrors, users abroad connect to upstream — no manual configuration at all; during install, Rheia picks the fastest path itself, and a source that tests slow is skipped instead of timing out.

### 2. What the shell preinstalls: work-ready out of the box

You do not get an empty OpenClaw. Once the gateway is up, the shell installs a set in the background — everything here is something you use on a normal working day:

| Preinstalled | Type | Purpose |
|---|---|---|
| `@openclaw/tavily-plugin` | plugin | Web search, the `web_search` provider |
| `@mem9/mem9` | plugin | Persistent memory across sessions |
| `@weiwei2027/office-toolkit` | skill | Office documents: Word / Excel / PPT / PDF |
| `@cindypapa/agent-browser` | skill | Browser automation |
| `@paudyyin/summarize` | skill | Long-text summarisation |
| `@pskoett/self-improving-agent` | skill | Review and self-improvement |
| `@guipi888/find-skills` | skill | Skill discovery |
| `@spclaudehome/skill-vetter` | skill | Skill vetting |

**Companion dependencies are installed too, so nothing is left half-working:**

- **Conversion engine**: `apt install libreoffice` — `soffice`, which all document format conversion relies on
- **Python document libraries** (pip): `python-docx` (Word), `python-pptx` (PPT), `openpyxl` (Excel), `pymupdf` (PDF read/write), `reportlab` (PDF generation) — installed from the Aliyun mirror, falling back to upstream on failure
- **Browser**: `npm i -g agent-browser` + `agent-browser install` (Chrome kernel)

**Three details that show this is not a hand-written checklist:**

- **Names, not categories** — whether an entry is a skill or a plugin is decided by the CLI at install time. If upstream promotes a skill to a plugin, the list does not change
- **Idempotent** — marker files prevent duplicate work on reinstall or resume
- **Failures are recorded, not swallowed** — failed entries go to `failed_skills.json`, visible in the sidebar and retryable

Check your own machine:

```bash
openclaw plugins list
openclaw skills list
```

### 3. Nearly four months, unchanged core

Rheia is only a shell — OpenClaw is the superhero, Rheia is the suit. A suit does not remake the hero; it does one thing: take the barrier to using official, native OpenClaw down to zero, so that people who do not write Linux commands can still drive this agent platform.

Rheia started at 2026.6.33 (21 July 2026); today it is 2026.10.30 — three months and change, four mainlines, with upstream iterating fast. Rheia's answer is one sentence: change nothing, adapt to everything — separate "installing OpenClaw" from "presenting OpenClaw", so whatever upstream changes, the shell adapts rather than being torn down. What keeps moving is the periphery — panels, mirrors, wording, edge cases, ground finer each time. The installer itself went from gigabytes down to 3.55 MB.

### 4. We stay native. No forks, no patches.

Rheia does not fork OpenClaw, does not patch its source, does not ship its own build: what gets installed is the official `npm install -g openclaw@latest`, using the official gateway, the official plugin system and the official Control UI. The shell does exactly three things — prepare the environment, start the gateway, open the UI.

This is an unquestionable advantage: every upstream iteration is followed immediately, staying as close to latest as possible — guaranteeing you are on the newest build, not on some distribution that is half a year out of date.

The OpenClaw on your machine is the official one. Official docs, community and issue tracker all match; when something breaks, trace it against the official documentation directly, without waiting for us to translate it.

### 5. What you receive is close to a full-strength install

A customer ends up with a complete runtime:

```
WSL2 + Ubuntu (reusing yours)
  └─ Node.js
      └─ OpenClaw (latest official)
          ├─ 8 shell preinstalls (2 plugins + 6 skills)
          ├─ companions: LibreOffice / Chrome kernel
          ├─ Gateway (systemd service, starts at boot)
          └─ Control UI (opens in the embedded browser)
```

Not "it runs" — it is **ready to work**.

---

## What it does to your machine

**The higher the privilege, the more transparency is owed.** The installer runs elevated, so here is every step:

**System**
- Enables WSL2 and the Virtual Machine Platform via `dism` (admin required; one reboot may be needed, the install resumes afterwards)
- Imports an Ubuntu cloud image as a WSL distro (default name `Ubuntu`, override with `RHEIA_WSL_DISTRO`)
- Installs apt / pip dependencies and Node.js inside the distro, then installs OpenClaw via npm
- Registers a systemd service so the gateway starts at boot
- The Python runtime goes to `C:\Program Files\PythonSide6` (shared; not removed by uninstall)

**Files written**
- `{app}` — the program
- `C:\ProgramData\Rheia\` — `install.log`, `download.log`, progress file
- `%TEMP%\okoc_signal.txt` — progress signal between the wizard and the installer (temporary)

**What it does not do**
- Does not upload your conversations
- Sends no telemetry, builds no fingerprint
- Writes no data files outside the install directory
- **Never shows you a console window** — every child process on the install and boot chain is created with `CREATE_NO_WINDOW` (see *Engineering details* below)

---

## Install flow

```
step0  self-check
step2  enable WSL2          step3  enable VM Platform
       └─ reboot when needed; RunOnce resumes
step4  request reboot (only if the kernel needs it)
step5  import Ubuntu        step55 apt / pip / venv
step6  install Node.js      step7  npm install OpenClaw
step8  systemd + gateway    step85 warm up core data (scout / large)
step9  extract gateway info → launch the UI
```

Progress: unpack 0–5 ｜ download 5–25 ｜ dism 26–40 ｜ Ubuntu 41–60 ｜ Node 61–65 ｜ npm 66–85 ｜ systemd 81–90 ｜ UI 96–100

Cancelling writes `%TEMP%\Rheia_cancel.flag`; the installer rolls back what was already installed.

---

## Panels

| Panel | Does |
|---|---|
| **Embedded browser** | Hosts the Control UI; single instance, self-heals on demand, three-state status light (connected / retrying / down) |
| **Provider console** | First-run quick setup plus a full console — per-provider status light, API key entry, enable/disable; writes only `models.providers.<id>.apiKey` and `enabled` |
| **Advanced config** | Sub-page of the provider console for custom providers — one parser, per-provider declarative field map, so a new provider is data, not code |
| **China channels** | Minimal setup for WeChat / QQBot / Feishu, aligned with the official CLI flow, with live output |
| **Gateway diagnostics** | WSL probe, gateway process, token summary, log tail — live, uncached, three-colour coded; restart uses a precise `pgrep` + `kill` instead of a bare `pkill -f` |
| **Memory** | Five tabs over `mani.json`: dreaming, background actions, memory search, card content (read-only), backup |
| **Backup & restore** | Back up / restore `baseline_pool.db` (preferences, credentials, recovery state, advanced providers), and export conversations to .md |

---

## Layout

```
extract/          installer wizard (Inno Setup script + card.c native UI + uninstaller)
installer/        step0–step9 orchestration, mirrors.json, component declarations, install engine
main/             daily entry: wake VM → probe gateway → embedded browser
                  boot_worker (kernel revival) / install_worker (headless install chain)
ui/web/           Qt panels: browser, provider, advanced config, diagnostics,
                  memory, backup, channels, support
ui/translations/  29 languages, one shared key table
kernel/           data-driven engine — scout (fetch → filter → probe) → large
                  (manifest → am → steak → forge) → render
lib/              process-level guards (no_window) and shared helpers
```

Roughly 60k lines of source — 47k of them Python — plus 19k lines of string tables across 29 languages, and one native `card.exe` UI for the wizard. No third-party runtime the customer has to install by hand.

---

## Engineering details

**1. Silent by construction.** `lib/no_window.py` wraps `subprocess.Popen` at every entry point — install, boot, card load, panels. A call that does not set `creationflags` gets `CREATE_NO_WINDOW`; a call that deliberately sets it is left alone. Installing and starting Rheia produces **no flashing console windows**, and this is a property of the process, not a rule someone has to remember.

**2. Atomic progress, cheap UI.** Worker processes write progress JSON to `.tmp` and `os.replace` it; the UI reads once a second. The heavy work never runs on the UI thread, and a UI crash cannot corrupt the install chain.

**3. Data-driven panels.** Panels are shells; their switches, defaults and commands come from `mani.json`, produced by `kernel/scout` from the official documentation corpus. When upstream adds a knob, the panel grows it without a code change.

**4. Precise, not blunt.** Restarting the gateway first resolves the PID, then kills that PID. No `pkill -f` sweeping up an unrelated process.

**5. Nothing leaves the machine.** No telemetry, no fingerprint, no upload of conversations. The only outbound traffic is npm / apt / upstream docs.

---

## Building from source

Requirements: Windows 10/11 x64, [Inno Setup 6.3+](https://jrsoftware.org/isinfo.php) with ISPP, MSVC

```bat
cd extract
_build_card.bat        :: build card.exe
_build_uninstall.bat   :: build uninstall.exe
ISCC setup.iss         :: package -> ..\Released\Rheia-Setup.exe
```

---

## About the UI — full disclosure

**On some machines the UI flickers, caused by PySide6 (QtWebEngine).**

The cause sits in Chromium's compositor, not in application code: the same Control UI renders correctly in Chrome and repaints repeatedly inside QtWebEngine. We tried GPU flags, the D3D11 backend and reduced motion — those tame part of it, but we cannot guarantee every GPU.

Three things stated plainly:

1. **This is a trade-off known at selection time, not an incident** — PySide6 was chosen to avoid Qt's commercial licence; we knew the cost and accepted it
2. **We do not paper over it** — GPU flags, the D3D11 backend and reduced motion were all tried; they tame part of it, not all of it. The real fix is on the roadmap (switch languages, buy a commercial licence)
3. **Transparency is part of the product** — saying what you will run into, and what we intend to do about it, beats explaining it after the fact

---

## Donations

This project is free and open source. Donations are **entirely voluntary** and change nothing functionally — no donation, full features, nothing locked. The sidebar has a support entry; it is the only place it appears, and it closes when you close it.

Where the money goes:

- **Part** covers the developer's living costs, so this can keep being built
- **Most of it** goes into engineering, one concrete direction being: **switch languages and buy a commercial licence** to eliminate the PySide6 rendering defect at the root and ship a better interface

So a donation is not a tip — it is an **investment**: it buys the next, better build of the UI, and keeps the project alive long enough to deliver it.

---

## License

This repository is released under the **MIT License** — see `LICENSE`.

- **OpenClaw** — MIT, © 2026 OpenClaw Foundation. Rheia installs and manages it; it does not modify it.
- **PySide6 / Qt for Python** — LGPLv3 for the core, © The Qt Company and contributors.

---

## Known limits

- **Windows x64 only, deliberately deep rather than half-done everywhere**; admin rights required (WSL2 and service registration)
- **The first install downloads runtimes** — a one-time investment; every update afterwards is incremental; mainland China uses mirrors automatically
- **Uninstall leaves `C:\Program Files\PythonSide6` in place** — deliberate restraint: remove only what we installed, no overreach, no collateral damage (shared across products)
