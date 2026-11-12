# L1-496：中国国内频道配置面板
r"""中国国内频道配置面板"""













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
    QDialog = QWidget = QTextEdit = QFrame = QApplication = object
    QPushButton = QLabel = QScrollArea = QVBoxLayout = QHBoxLayout = object
    QMessageBox = QSizePolicy = object
    Qt = type("_QtStub", (), {})
    Signal = object

WSL_DISTRO = os.environ.get("RHEIA_WSL_DISTRO", "").strip() or "Ubuntu"




_DOMESTIC_CHANNEL_IDS = ("feishu", "wechat", "wecom", "qqbot", "yuanbao")
try:
    from ui.i18n import tr
except ImportError:
    tr = lambda key, **fmt: key



_ACTION_SIGNALS = (
    ("plugins install", "ui.channel.install_plugin"),
    ("channels add", "ui.channel.add_channel"),
    ("channels login", "ui.channel.scan_login"),
    ("channels", "ui.channel.exec_cmd"),
)

_MANI_PATH = os.path.join(_proj, "kernel", "large", "mani.json")



_CREDENTIAL_PLACEHOLDER = re.compile(r"[A-Za-z]{3,}:[A-Za-z]{3,}")


# L84-90：_needs_input — 该步骤是否要用户先填凭据（数据驱动，不硬编频道/命令）。
def _needs_input(cmd: str) -> bool:
    r"""该步骤是否要用户先填凭据（数据驱动，不硬编频道/命令）。"""




    return bool(_CREDENTIAL_PLACEHOLDER.search(cmd))


# L94-145：_load_channels — 从 mani 的 channels 读频道数据（含 spider 抓的接入命令）。
def _load_channels() -> list[dict]:
    r"""从 mani 的 channels 读频道数据（含 spider 抓的接入命令）。"""





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




# L151-194：_WslWorker — 在 WSL 中执行命令，完成后发 signal 回 UI 线程。
class _WslWorker(QThread):
    r"""在 WSL 中执行命令，完成后发 signal 回 UI 线程。"""
    output = Signal(str)
    finished = Signal(int, str)

    def __init__(self, command: str, parent=None):
        super().__init__(parent)
        self._command = command

    def run(self) -> None:
        full_output: list[str] = []



        popen_kwargs: dict = {}
        if os.name == "nt" and hasattr(subprocess, "STARTUPINFO"):
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0
            popen_kwargs["startupinfo"] = si
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:


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




# L200-267：_TerminalArea — 底部终端：深色终端风格，显示命令执行输出。
class _TerminalArea(QWidget):
    r"""底部终端：深色终端风格，显示命令执行输出。"""

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

        self._queue: list[tuple[str, str]] = []

    def run_command(self, cmd: str, *, post_note: str = "") -> None:
        r"""执行一条 WSL 命令，实时输出到终端区域。同一时间只允许一条命令。"""

        if self._worker and self._worker.isRunning():
            self._queue.append((cmd, post_note))
            self._output.append(tr("ui.channel.busy") + "\n")
            return
        self._start(cmd, post_note)

    def _start(self, cmd: str, post_note: str) -> None:
        r"""真正起线程跑一条命令（队列为空/空闲时由 run_command 或队列泵调用）。"""
        self._output.clear()
        self._output.append(f"<span style='color:#f37021;'>$ {cmd}</span>\n")

        self._worker = _WslWorker(cmd, self)
        self._worker.output.connect(self._append_output)
        self._worker.finished.connect(lambda rc, out: self._on_finished(rc, out, post_note))
        self._worker.start()

    def _append_output(self, text: str) -> None:
        r"""实时追加输出行。"""
        self._output.append(text)

        sb = self._output.verticalScrollBar()
        if sb:
            sb.setValue(sb.maximum())

    def _on_finished(self, rc: int, full_output: str, post_note: str = "") -> None:
        r"""命令执行完毕，打印结果摘要。post_note 用于提示用户下一步操作（如扫码）。"""
        color = STATUS["green"] if rc == 0 else STATUS["red"]
        status_text = tr("ui.channel.success") if rc == 0 else tr("ui.channel.failed", rc=rc)
        self._output.append(f"\n<span style='color:{color};font-weight:bold;'>{status_text}</span>")
        if post_note:
            self._output.append(f"\n<span style='color:{ACCENT};font-weight:bold;font-size:14px;'>{post_note}</span>")

        if self._queue:
            nxt_cmd, nxt_note = self._queue.pop(0)
            self._start(nxt_cmd, nxt_note)




# L273-411：_ChannelCard — 一个频道的配置卡片：标题 + 描述 + 步骤列表 + 逐步骤执行按钮。
class _ChannelCard(QFrame):
    r"""一个频道的配置卡片：标题 + 描述 + 步骤列表 + 逐步骤执行按钮。"""

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


        header = QHBoxLayout()
        header.setSpacing(0)
        icon_label = QLabel(f"{channel['icon']}  {channel['title']}")
        icon_label.setWordWrap(True)
        icon_label.setStyleSheet(f"color: {ACCENT}; font-size: 15px; font-weight: bold; background: transparent;")
        header.addWidget(icon_label, stretch=1)
        header.addStretch()
        layout.addLayout(header)


        if channel.get("provider"):
            provider_label = QLabel(channel["provider"])
            provider_label.setWordWrap(True)
            provider_label.setStyleSheet(f"color: {TEXT['hint']}; font-size: 10px; background: transparent;")
            layout.addWidget(provider_label)


        if channel.get("desc"):
            desc_label = QLabel(channel["desc"])
            desc_label.setWordWrap(True)
            desc_label.setStyleSheet(f"color: {TEXT['secondary']}; font-size: 12px; background: transparent;")
            layout.addWidget(desc_label)


        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #333; background: transparent;")
        sep.setFixedHeight(1)
        layout.addWidget(sep)


        for i, step in enumerate(channel["steps"]):
            step_layout = QVBoxLayout()
            step_layout.setSpacing(4)


            step_num = QLabel(tr("ui.channel.step", n=i+1))
            step_num.setStyleSheet(f"color: {TEXT['hint']}; font-size: 11px; font-weight: bold; background: transparent;")
            step_layout.addWidget(step_num)


            cmd = step["cmd"]

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
        r"""按官方 CLI 的动作信号词表 _ACTION_SIGNALS 推导按钮动作名。"""




        for signal, key in _ACTION_SIGNALS:
            if signal in cmd:
                return tr(key)
        return tr("ui.channel.exec_cmd")

    def _execute_step(self, cmd: str, needs_input: bool, post_note: str = "") -> None:
        r"""执行某个步骤的命令。需要用户输入参数时先弹出提示。执行后显示 post_note 提示。"""
        if needs_input:
            QMessageBox.information(
                self,
                tr("ui.channel.need_credential"),
                tr("ui.channel.need_credential_msg") + f"\n\n{tr('ui.channel.cmd')}:\n{cmd}",
            )
            return
        self._terminal.run_command(cmd, post_note=post_note)




# L417-486：CnChannelPanel — 中国国内频道配置面板 —— 三个频道卡片 + 底部执行终端。
class CnChannelPanel(QDialog):
    r"""中国国内频道配置面板 —— 三个频道卡片 + 底部执行终端。"""

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


        title = QLabel(tr("ui.channel.title"))
        title.setStyleSheet(f"color: {TEXT['primary']}; font-size: 18px; font-weight: bold; background: transparent;")
        root.addWidget(title)

        hint = QLabel(tr("ui.channel.hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {TEXT['hint']}; font-size: 12px; background: transparent; margin-bottom: 4px;")
        root.addWidget(hint)


        self._terminal = _TerminalArea()
        root.addWidget(self._terminal, stretch=0)


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




if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication as QA
    app = QA(sys.argv)
    panel = CnChannelPanel()
    panel.show()
    app.exec()
