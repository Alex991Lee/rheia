"""
中国国内频道配置面板
====================
侧边栏「中国国内频道配置」→ QDialog 卡片 → 三频道极简配置。

对齐 OpenClaw 官方技术流程：
  - WeChat:  npx install → channels login QR 扫码
  - QQBot:   plugins install → channels add AppID:AppSecret
  - Feishu:  channels login → QR 或手动 AppID+AppSecret

每个频道卡片展示：简介 + 核心 CLI + 执行按钮。
执行结果实时显示在下方终端区域。
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys

logger = logging.getLogger(__name__)

_proj = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _proj not in sys.path:
    sys.path.insert(0, _proj)

# ── 主题 ──
try:
    from ui.theme import BG, TEXT, ACCENT, ACCENT_HOVER, STATUS
except ImportError:
    BG = {"deep": "#1a1a1a", "card": "#2a2a2a", "hover": "#333333"}
    TEXT = {"primary": "#e0e0e0", "secondary": "#b0b0b0", "hint": "#a0a0a0"}
    ACCENT = "#f37021"
    ACCENT_HOVER = "#ff8833"
    STATUS = {"green": "#5a9e6f", "yellow": "#d9a840", "red": "#e85a4a", "gray": "#6a5e52"}

try:
    from PySide6.QtCore import Qt, QThread, Signal
    from PySide6.QtWidgets import (
        QDialog, QHBoxLayout, QLabel, QMessageBox,
        QPushButton, QScrollArea, QVBoxLayout, QWidget, QTextEdit,
        QApplication, QFrame, QSizePolicy,
    )
    _HAS_PYQT = True
except ImportError:
    _HAS_PYQT = False
    QDialog = QWidget = QTextEdit = QFrame = QApplication = object  # type: ignore
    QPushButton = QLabel = QScrollArea = QVBoxLayout = QHBoxLayout = object  # type: ignore
    QMessageBox = QSizePolicy = object  # type: ignore
    Qt = type("_QtStub", (), {})  # type: ignore
    Signal = object  # type: ignore

WSL_DISTRO = os.environ.get("RHEIA_WSL_DISTRO", "").strip() or "Ubuntu"

# ── 国内频道归属（产品侧划分，非官方文档事实）──
# 官方文档没有「国内」这一维，spider 也不产 region；按定稿「零硬编只约束数据
# 驱动侧，UI 必要处允许硬编」，归属清单就摆在这里，明标是 UI 侧硬编。
_DOMESTIC_CHANNEL_IDS = ("feishu", "wechat", "wecom", "qqbot", "yuanbao")
try:
    from ui.i18n import tr
except ImportError:
    tr = lambda key, **fmt: key

# 官方 CLI 的动作信号词 → 按钮文案 key（命令原文仍整条来自 mani）。
# 表外的命令一律退回「执行命令」，不猜、不再散落别的字面量。
_ACTION_SIGNALS = (
    ("plugins install", "ui.channel.install_plugin"),
    ("channels add", "ui.channel.add_channel"),
    ("channels login", "ui.channel.scan_login"),
    ("channels", "ui.channel.exec_cmd"),
)

_MANI_PATH = os.path.join(_proj, "kernel", "large", "mani.json")

# 官方文档里的凭据占位符形态（如 AppID:AppSecret / appKey:appSecret）。
# 命令里出现这种值 = 这条命令要用户先把自己的真凭据填进去，不能直接一键跑。
_CREDENTIAL_PLACEHOLDER = re.compile(r"[A-Za-z]{3,}:[A-Za-z]{3,}")


def _needs_input(cmd: str) -> bool:
    """该步骤是否要用户先填凭据（数据驱动，不硬编频道/命令）。

    mani 的命令条目若自带 needs_input 字段，优先用它（调用处已先看）；没给时
    看这条命令里是否带着文档的凭据占位符 —— 占位符不是真凭据，直接跑必失败。
    """
    return bool(_CREDENTIAL_PLACEHOLDER.search(cmd))


def _load_channels() -> list[dict]:
    """从 mani 的 channels 读频道数据（含 spider 抓的接入命令）。

    频道列表、标题、接入命令来自 spider 从官方文档长出的 mani；归属（哪些算
    国内）是本模块的 UI 侧清单 _DOMESTIC_CHANNEL_IDS —— mani 里没有 region 这
    个字段，也不该指望它长出「国内」这种产品划分。命令一律来自 mani，不硬编。
    """
    import json
    try:
        with open(_MANI_PATH, "r", encoding="utf-8") as f:
            channels = (json.load(f).get("entries") or {}).get("channels") or []
    except (OSError, json.JSONDecodeError):
        return []
    out = []
    for ch in channels:
        if not isinstance(ch, dict):
            continue
        cid = (ch.get("id") or "").strip()
        if cid not in _DOMESTIC_CHANNEL_IDS:
            continue
        commands = [c.get("cmd", "") for c in (ch.get("commands") or []) if c.get("cmd")]
        # provider 优先取 mani 的结构化字段（package / provider / auth.plugin）；
        # 只有数据没给时才回退到从 plugins install 命令里解析包名。
        provider = (ch.get("package") or ch.get("provider")
                    or (ch.get("auth") or {}).get("plugin") or "").strip()
        if not provider:
            for cmd in commands:
                m = re.search(r"plugins install\s+\"?([@\w./-]+)\"?", cmd)
                if m:
                    provider = m.group(1)
                    break
        steps = []
        for c in (ch.get("commands") or []):
            cmd = c.get("cmd", "")
            if not cmd:
                continue
            steps.append({
                "label": tr("ui.channel.exec_cmd"),
                "cmd": cmd,
                # mani 自带 needs_input 优先；数据没给时按命令里的凭据占位符判定
                # （见 _needs_input），免得要填凭据的步骤被一键直接执行。
                "needs_input": bool(c.get("needs_input")) or _needs_input(cmd),
            })
        out.append({
            "id": cid,
            "title": ch.get("section_name") or cid.title(),
            "icon": "",
            "desc": (ch.get("summary") or "").strip(),
            "provider": provider,
            "steps": steps,
        })
    return out


# ── WSL 后台执行线程 ──

class _WslWorker(QThread):
    """在 WSL 中执行命令，完成后发 signal 回 UI 线程。"""
    output = Signal(str)  # 实时输出行
    finished = Signal(int, str)  # returncode, full_output

    def __init__(self, command: str, parent=None):
        super().__init__(parent)
        self._command = command

    def run(self) -> None:
        full_output: list[str] = []
        # 隐藏控制台窗口是 Windows 专有：STARTUPINFO / CREATE_NO_WINDOW 在
        # 非 Windows 上并不存在，这里做跨平台回落，不裸用（原写法的
        # AttributeError 会被 except 吞成一条错误输出）。
        popen_kwargs: dict = {}
        if os.name == "nt" and hasattr(subprocess, "STARTUPINFO"):
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0
            popen_kwargs["startupinfo"] = si
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            # 命令整条作为 bash -c 的单个参数传入（argv 列表、不经 shell 拼串），
            # 命令里带双引号/空格不再破坏结构。
            proc = subprocess.Popen(
                ["wsl", "-d", WSL_DISTRO, "--", "bash", "-c", self._command],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                encoding="utf-8", errors="replace",
                **popen_kwargs,
            )
            assert proc.stdout
            for line in proc.stdout:
                line = line.rstrip("\n")
                if line:
                    full_output.append(line)
                    self.output.emit(line)
            proc.wait()
            self.finished.emit(proc.returncode, "\n".join(full_output))
        except Exception as e:
            msg = tr("ui.channel.exec_error", e=str(e))
            full_output.append(msg)
            self.output.emit(msg)
            self.finished.emit(-1, "\n".join(full_output))


# ── 执行终端区域 ──

class _TerminalArea(QWidget):
    """底部终端：深色终端风格，显示命令执行输出。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._output = QTextEdit()
        self._output.setReadOnly(True)
        self._output.setStyleSheet(f"""
            QTextEdit {{
                background: #0d0d0d;
                color: #33ff33;
                font-family: "Cascadia Code", "Consolas", "Courier New", monospace;
                font-size: 12px;
                border: 1px solid #333;
                border-radius: 4px;
                padding: 8px;
            }}
        """)
        self._output.setMinimumHeight(400)
        self._output.setMaximumHeight(700)
        self._output.setPlaceholderText(tr("ui.channel.output_ph"))
        layout.addWidget(self._output)

        self._worker: _WslWorker | None = None
        # 忙时排队的待执行命令：(cmd, post_note)
        self._queue: list[tuple[str, str]] = []

    def run_command(self, cmd: str, *, post_note: str = "") -> None:
        """执行一条 WSL 命令，实时输出到终端区域。同一时间只允许一条命令。"""
        # 忙时不丢点击：排队，等当前命令跑完自动接着执行。
        if self._worker and self._worker.isRunning():
            self._queue.append((cmd, post_note))
            self._output.append(tr("ui.channel.busy") + "\n")
            return
        self._start(cmd, post_note)

    def _start(self, cmd: str, post_note: str) -> None:
        """真正起线程跑一条命令（队列为空/空闲时由 run_command 或队列泵调用）。"""
        self._output.clear()
        self._output.append(f"<span style='color:#f37021;'>$ {cmd}</span>\n")

        self._worker = _WslWorker(cmd, self)
        self._worker.output.connect(self._append_output)
        self._worker.finished.connect(lambda rc, out: self._on_finished(rc, out, post_note))
        self._worker.start()

    def _append_output(self, text: str) -> None:
        """实时追加输出行。"""
        self._output.append(text)
        # 自动滚底
        sb = self._output.verticalScrollBar()
        if sb:
            sb.setValue(sb.maximum())

    def _on_finished(self, rc: int, full_output: str, post_note: str = "") -> None:
        """命令执行完毕，打印结果摘要。post_note 用于提示用户下一步操作（如扫码）。"""
        color = STATUS["green"] if rc == 0 else STATUS["red"]
        status_text = tr("ui.channel.success") if rc == 0 else tr("ui.channel.failed", rc=rc)
        self._output.append(f"\n<span style='color:{color};font-weight:bold;'>{status_text}</span>")
        if post_note:
            self._output.append(f"\n<span style='color:{ACCENT};font-weight:bold;font-size:14px;'>{post_note}</span>")
        # 队列里还有排队的命令就接着跑（连同 post_note 一起取出）。
        if self._queue:
            nxt_cmd, nxt_note = self._queue.pop(0)
            self._start(nxt_cmd, nxt_note)


# ── 单个频道卡片 ──

class _ChannelCard(QFrame):
    """一个频道的配置卡片：标题 + 描述 + 步骤列表 + 逐步骤执行按钮。"""

    def __init__(self, channel: dict, terminal: _TerminalArea, parent=None):
        super().__init__(parent)
        self._channel = channel
        self._terminal = terminal

        self.setStyleSheet(f"""
            _ChannelCard {{
                background: {BG['card']};
                border: 1px solid #444;
                border-radius: 8px;
                padding: 16px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)

        # 标题行（只放标题，允许换行）
        header = QHBoxLayout()
        header.setSpacing(0)
        icon_label = QLabel(f"{channel['icon']}  {channel['title']}")
        icon_label.setWordWrap(True)
        icon_label.setStyleSheet(f"color: {ACCENT}; font-size: 15px; font-weight: bold; background: transparent;")
        header.addWidget(icon_label, stretch=1)
        header.addStretch()
        layout.addLayout(header)

        # 插件包名（独立一行小字，可换行，不与标题挤同一行；空则跳过）
        if channel.get("provider"):
            provider_label = QLabel(channel["provider"])
            provider_label.setWordWrap(True)
            provider_label.setStyleSheet(f"color: {TEXT['hint']}; font-size: 10px; background: transparent;")
            layout.addWidget(provider_label)

        # 描述（空则跳过，不留空行）
        if channel.get("desc"):
            desc_label = QLabel(channel["desc"])
            desc_label.setWordWrap(True)
            desc_label.setStyleSheet(f"color: {TEXT['secondary']}; font-size: 12px; background: transparent;")
            layout.addWidget(desc_label)

        # 分隔线
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #333; background: transparent;")
        sep.setFixedHeight(1)
        layout.addWidget(sep)

        # 步骤列表
        for i, step in enumerate(channel["steps"]):
            step_layout = QVBoxLayout()
            step_layout.setSpacing(4)

            # 步骤序号（小标题，无按钮占位）
            step_num = QLabel(tr("ui.channel.step", n=i+1))
            step_num.setStyleSheet(f"color: {TEXT['hint']}; font-size: 11px; font-weight: bold; background: transparent;")
            step_layout.addWidget(step_num)

            # ── 命令行：命令用换行文本展示，按钮只给动作名、不显示命令 ──
            cmd = step["cmd"]
            # 动作名从命令信号词推导（数据驱动，不硬编命令字面量）
            action = self._derive_action(cmd)

            cmd_label = QLabel(cmd)
            cmd_label.setWordWrap(True)
            cmd_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            cmd_label.setStyleSheet(
                f"color: {TEXT['secondary']}; font-size: 11px; background: #141414; "
                f"border: 1px solid #333; border-radius: 4px; padding: 4px 8px; font-family: Consolas, monospace;"
            )
            step_layout.addWidget(cmd_label)

            cmd_btn = QPushButton(f"▶ {action}")
            cmd_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            cmd_btn.setToolTip(tr("ui.channel.click_exec", cmd=cmd))
            cmd_btn.setStyleSheet(f"""
                QPushButton {{
                    color: {ACCENT};
                    font-size: 12px;
                    font-weight: bold;
                    background: #1a1a1a;
                    border: 1px solid #444;
                    border-radius: 4px;
                    padding: 5px 12px;
                }}
                QPushButton:hover {{
                    background: #222;
                    border-color: {ACCENT};
                    color: {ACCENT_HOVER};
                }}
                QPushButton:pressed {{
                    background: #2a2a2a;
                    border-color: {ACCENT_HOVER};
                }}
            """)
            cmd_btn.clicked.connect(
                lambda checked, c=step["cmd"], n=step.get("needs_input", False), pn=step.get("post_note", ""):
                    self._execute_step(c, n, pn))
            step_layout.addWidget(cmd_btn, alignment=Qt.AlignmentFlag.AlignLeft)

            note = step.get("note", "")
            if note:
                note_label = QLabel(note)
                note_label.setWordWrap(True)
                note_label.setStyleSheet(f"color: {TEXT['hint']}; font-size: 11px; background: transparent;")
                step_layout.addWidget(note_label)

            layout.addLayout(step_layout)

            if i < len(channel["steps"]) - 1:
                layout.addSpacing(5)

        layout.addStretch()

    def _derive_action(self, cmd: str) -> str:
        """按官方 CLI 的动作信号词表 _ACTION_SIGNALS 推导按钮动作名。

        表外的命令一律退回「执行命令」，不猜（原注释自称「不硬编命令字面量」
        与实现相反，现按实说明：信号词集中在模块级表里，命令本身仍来自 mani）。
        """
        for signal, key in _ACTION_SIGNALS:
            if signal in cmd:
                return tr(key)
        return tr("ui.channel.exec_cmd")

    def _execute_step(self, cmd: str, needs_input: bool, post_note: str = "") -> None:
        """执行某个步骤的命令。需要用户输入参数时先弹出提示。执行后显示 post_note 提示。"""
        if needs_input:
            QMessageBox.information(
                self,
                tr("ui.channel.need_credential"),
                tr("ui.channel.need_credential_msg") + f"\n\n{tr('ui.channel.cmd')}:\n{cmd}",
            )
            return
        self._terminal.run_command(cmd, post_note=post_note)


# ── 主面板 ──

class CnChannelPanel(QDialog):
    """中国国内频道配置面板 —— 三个频道卡片 + 底部执行终端。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        try:
            from main.main import stk as _stk
            _stk("cn_channel_panel.open", "打开国内频道配置面板", ok=True)
        except Exception:
            pass
        self.setWindowTitle(tr("ui.channel.title"))
        from ui.theme import fitToScreen
        fitToScreen(self, 0.30, 0.85)
        self.setStyleSheet(f"""
            QDialog {{
                background: {BG['deep']};
            }}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # 标题
        title = QLabel(tr("ui.channel.title"))
        title.setStyleSheet(f"color: {TEXT['primary']}; font-size: 18px; font-weight: bold; background: transparent;")
        root.addWidget(title)

        hint = QLabel(tr("ui.channel.hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {TEXT['hint']}; font-size: 12px; background: transparent; margin-bottom: 4px;")
        root.addWidget(hint)

        # ── 终端区域（放在最上面，执行时用户能立刻看到）──
        self._terminal = _TerminalArea()
        root.addWidget(self._terminal, stretch=0)

        # ── 三频道卡片（可滚动）──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"""
            QScrollArea {{
                background: transparent;
                border: none;
            }}
            QScrollBar:vertical {{
                background: {BG['deep']};
                width: 8px;
            }}
            QScrollBar::handle:vertical {{
                background: #555;
                border-radius: 4px;
                min-height: 30px;
            }}
            QScrollBar::handle:vertical:hover {{ background: #777; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)

        cards_widget = QWidget()
        cards_layout = QVBoxLayout(cards_widget)
        cards_layout.setContentsMargins(0, 0, 0, 0)
        cards_layout.setSpacing(12)

        for ch in _load_channels():
            card = _ChannelCard(ch, self._terminal)
            cards_layout.addWidget(card)

        cards_layout.addStretch()
        scroll.setWidget(cards_widget)
        root.addWidget(scroll, stretch=1)


# ── 独立运行入口 ──

if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication as QA
    app = QA(sys.argv)
    panel = CnChannelPanel()
    panel.show()
    app.exec()
