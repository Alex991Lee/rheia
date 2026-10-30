"""
Rheia 备份与恢复模块
============================
侧边栏「初始设置快速恢复」按钮触发的对话框。

功能：
  备份 —— 把 %PROGRAMDATA%\\Rheia\\baseline_pool.db 复制到客户选的目标路径
  初始设置快速恢复 —— 客户选一个 .db 文件，原子替换到 PROGRAMDATA 下的 baseline_pool.db
  导出对话 —— 把当前主会话（agent:main:main）导出为 .md，客户可选目录

baseline_pool.db 是灾难恢复数据库（scout.py 维护，含 user_prefs /
credentials / recovery_state / advanced_providers 表）。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from typing import Any

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QVBoxLayout, QWidget, QSizePolicy, QInputDialog,
)

from ui.theme import fit_button_text, BG, TEXT, ACCENT, ACCENT_HOVER
from ui.i18n import tr

_CARD_BG = BG["deep"]
_TEXT_PRIMARY = TEXT["primary"]
_TEXT_HINT = TEXT["hint"]

# DB 固定位置（与 scout.py 对齐）
_PROGRAMDATA = os.environ.get("PROGRAMDATA", "")
WSL_DISTRO = os.environ.get("RHEIA_WSL_DISTRO", "").strip() or "Ubuntu"   # 发行版名（env 可覆盖）
_DB_DIR = os.path.join(_PROGRAMDATA, "Rheia") if _PROGRAMDATA else ""
_DB_PATH = os.path.join(_DB_DIR, "baseline_pool.db") if _DB_DIR else ""


def _stake(tag: str, msg: str, ok: bool = True) -> None:
    try:
        from main.main import stk
        stk(f"bkrestore.{tag}", msg, ok=ok)
    except Exception:
        pass


def get_db_path() -> str:
    """返回 baseline_pool.db 的完整路径（PROGRAMDATA）。"""
    return _DB_PATH


def export_db(target_path: str) -> tuple[bool, str]:
    """导出基础备份：把 baseline_pool.db 复制到客户选的目标路径。

    ⚠ 2026-10-17 审计〔旧设计·该删〕：原来这里先调 backup_workspace_files() 把宿主
    agent 工作区的关键文件读进 DB —— 那条「往宿主工作区抓手」的路径已删。
    """
    if not _DB_PATH or not os.path.isfile(_DB_PATH):
        return (False, tr("ui.restore.no_db"))
    try:
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        shutil.copy2(_DB_PATH, target_path)
        _stake("export", f"→ {target_path}", ok=True)
        return (True, target_path)
    except Exception as e:
        _stake("export", f"失败: {e}", ok=False)
        return (False, str(e))


def _run_wsl_capture(cmd: str) -> tuple[int, str, str]:
    """在 WSL 里跑一条命令，返回 (rc, stdout, stderr)。

    ⚠ 壳层不留时间闸：不把时间交给子进程（命令返回即信号）。
    本函数没有 timeout 形参，也不要再加回来。
    """
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True,
                           encoding="utf-8", errors="replace",
                           creationflags=subprocess.CREATE_NO_WINDOW, startupinfo=si)
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except Exception:
        return -1, "", "error"


# ⚠ 2026-10-17 审计〔旧设计·该删〕：_WORKSPACE_KEY_FILES（写死要备份的宿主工作区
#   文件名清单）已删除 —— 按文件名点名别人工作区属旧设计；配合下面
#   backup_workspace_files / restore_workspace_files 一并去掉。


def _run_wsl_raw(cmd: str) -> tuple[int, str]:
    """跑 WSL 命令，返回 (rc, stdout)。不 strip 内容，保留原始文本。

    ⚠ 壳层不留时间闸（同 `_run_wsl_capture`：命令返回即信号）。
    """
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True,
                           encoding="utf-8", errors="replace",
                           creationflags=subprocess.CREATE_NO_WINDOW, startupinfo=si)
        return r.returncode, (r.stdout or "")
    except Exception:
        return -1, ""


# ⚠ 2026-10-17 审计〔旧设计·该删〕：backup_workspace_files 的整段实现（宿主工作区
#   关键文件 + memory/*.md 逐个读进 DB、3s 探活 + 5.0s 总闸）已删除。
#   现在备份只做 baseline_pool.db 本身，不再去抓别人的工作区。


# ⚠ 2026-10-17 审计〔旧设计·该删〕：restore_workspace_files 的整段实现（把 DB 里
#   的文件逐个写回宿主 agent 工作区 ~/.openclaw/workspace/<rel>，含 timeout=60 的
#   真时间闸与把路径拼进 shell）已删除。备份库里的 workspace_files 表随
#   baseline_pool.db 一起恢复，不再由壳往别人的工作区落地。


def _rpc_export_conversation(session_key: str) -> tuple[bool, str]:
    """走 Gateway RPC（export_conversation.js）读会话历史，返回 (ok, messages_json)。

    读正文走官方 chat.history RPC（display-normalized），不是读磁盘 jsonl。
    """
    gw_cfg = os.path.join(os.environ.get("PROGRAMDATA", ""), "Rheia", "gateway_config.json")
    token = ""
    if os.path.isfile(gw_cfg):
        try:
            with open(gw_cfg, "r", encoding="utf-8") as f:
                token = json.load(f).get("token", "")
        except (OSError, json.JSONDecodeError):
            token = ""
    if not token:
        return False, tr("ui.restore.read_index_fail")

    script = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "main", "scripts", "export_conversation.js",
    )
    # Windows 路径 -> WSL 路径：E:\x\y -> /mnt/e/x/y
    wsl_script = "/mnt/" + script.replace("\\", "/").replace(":", "").lower()

    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    # stdout 有 64KB 截断风险，JS 端把完整 JSON 写到 WSL 临时文件，stdout 只回路径。
    wsl_out = f"/tmp/okoc_conv_{os.getpid()}.json"
    try:
        # 壳层不留时间闸：不再传 timeout（原来 subprocess.run(timeout=60)
        # 是真闸，超时被吞成「读索引失败」）。
        out = subprocess.run(
            ["wsl", "-d", WSL_DISTRO, "--", "node", wsl_script, token, session_key, "500", wsl_out],
            capture_output=True,
            encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW, startupinfo=si,
        )
    except OSError as e:
        _stake("conv_export", f"RPC 失败: {e}", ok=False)
        return False, tr("ui.restore.read_session_fail")

    result = (out.stdout or "").strip()
    if result.startswith("OK "):
        # 从 WSL 临时文件读完整 JSON（绕过 64KB stdout 截断）
        wsl_path = result[3:].strip()
        rc, content = _run_wsl_raw(f"wsl -d {WSL_DISTRO} -u root -- bash -c \"cat '{wsl_path}' 2>/dev/null; rm -f '{wsl_path}'\"")
        if rc == 0 and content:
            return True, content
    _stake("conv_export", f"RPC 失败: {result[:120]}", ok=False)
    return False, tr("ui.restore.read_session_fail")


def _list_exportable_sessions() -> list[dict]:
    """从 mani 长出来的 `sessions` 命令列会话，返回可导出的对话 session 列表。

    过滤 cron（自动化会话，非对话），保留 main + dashboard 子会话。
    每条返回 {key, label}，label 为空时用 key 的尾段做显示名。
    """
    try:
        from main.main import cli_usage
        sessions_cmd = cli_usage("sessions", "list")
    except Exception:
        sessions_cmd = ""
    if not sessions_cmd:
        sessions_cmd = "openclaw sessions"

    rc, out, _ = _run_wsl_capture(
        f'wsl -d {WSL_DISTRO} -u root -- bash -c '
        f'"export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/sbin:/bin; '
        f'{sessions_cmd} --json 2>/dev/null"'
    )
    if rc != 0 or not out.strip():
        return []
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return []
    sessions = data.get("sessions") if isinstance(data, dict) else data
    if not isinstance(sessions, list):
        return []

    result = []
    for s in sessions:
        if not isinstance(s, dict):
            continue
        key = s.get("key", "")
        if not key or s.get("kind") == "cron":
            continue  # 排除自动化会话
        label = (s.get("label") or "").strip()
        if not label:
            label = "(main)" if key == "agent:main:main" else key.split(":")[-1]
        result.append({"key": key, "label": label})
    return result


def export_conversation_md(target_dir: str, session_key: str = "") -> tuple[bool, str]:
    """把指定 session 导出为 .md，写到客户选的目录。

    session_key：要导出的会话 key。为空时回退主会话 agent:main:main。

    链路：
      1. 读正文走官方 chat.history RPC（display-normalized 历史），
         由 export_conversation.js 承载，不是读磁盘 jsonl。
      2. 拼 markdown（这是 UI 层渲染，属于「必要处允许硬编」的范畴）。
    """
    try:
        # 1) 目标 session key（空则主会话）
        session_key = session_key or "agent:main:main"

        # 2) 读正文走官方 chat.history RPC
        ok, payload = _rpc_export_conversation(session_key)
        if not ok:
            return False, payload

        data = json.loads(payload)
        messages = data.get("messages") or []
        if not messages:
            _stake("conv_export", "会话无消息可导出", ok=False)
            return False, tr("ui.restore.no_conv_content")

        # 3) 解析 user/assistant 消息，拼 md（UI 渲染，必要处硬编）
        # JS 端已抽好 {role, text}，这里只做 md 拼装
        md_lines = [tr("ui.restore.conv_title"), ""]
        for m in messages:
            if not isinstance(m, dict):
                continue
            role = m.get("role", "")
            text = (m.get("text") or "").strip()
            if not text:
                continue
            if role == "user":
                md_lines.append(tr("ui.restore.conv_user", text=text))
            elif role == "assistant":
                md_lines.append(tr("ui.restore.conv_assistant", text=text))
        if len(md_lines) <= 2:
            _stake("conv_export", "会话无消息可导出", ok=False)
            return False, tr("ui.restore.no_conv_content")

        # 4) 写到客户选的目录
        os.makedirs(target_dir, exist_ok=True)
        sid = session_key.split(":")[-1][:8] or "main"
        filename = tr("ui.restore.conv_filename", sid=sid)
        target = os.path.join(target_dir, filename)
        with open(target, "w", encoding="utf-8") as f:
            f.write("\n".join(md_lines))

        _stake("conv_export", f"→ {target} key={session_key}", ok=True)
        return True, target
    except Exception as e:
        _stake("conv_export", f"导出异常: {e}", ok=False)
        return False, str(e)


def import_db(src_path: str) -> tuple[bool, str]:
    """导入：把客户选的 .db 原子替换到 PROGRAMDATA 下的 baseline_pool.db。"""
    if not _DB_DIR:
        return (False, tr("ui.restore.no_progdata"))
    if not os.path.isfile(src_path):
        return (False, tr("ui.restore.file_missing"))

    # 校验：是不是有效的 sqlite db
    try:
        import sqlite3
        conn = sqlite3.connect(src_path)
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {r[0] for r in cur.fetchall()}
        conn.close()
        expected = {"user_prefs", "credentials", "recovery_state"}
        if not expected.issubset(tables):
            return (False, tr("ui.restore.invalid_db", missing=expected - tables))
    except Exception as e:
        return (False, tr("ui.restore.invalid_sqlite", e=str(e)))

    try:
        os.makedirs(_DB_DIR, exist_ok=True)
        tmp_path = None
        # 临时文件 + 原子替换，避免复制到一半被读到
        fd, tmp_path = tempfile.mkstemp(suffix=".db", prefix="okoc-restore-", dir=_DB_DIR)
        os.close(fd)
        shutil.copy2(src_path, tmp_path)

        # WAL 模式下 SQLite 退出后可能残留 -wal/-shm 锁，覆盖主 db 前先清掉
        for suffix in ("-wal", "-shm"):
            _side = _DB_PATH + suffix
            try:
                if os.path.exists(_side):
                    os.remove(_side)
            except OSError as e:
                # 删不掉 -wal/-shm 就可能留下不一致状态 —— 不硬来（原来静默跳过）。
                _stake("import_db", f"无法清理 {_side}: {e}", ok=False)
                return (False, f"{_side}: {e}")

        # db 是安装时以 admin 身份创建的，Users 只有 RX 读权限，os.replace 覆盖会 WinError 5。
        # 替换前先给 db 和目录补 Users 写权限（icacls 提权，失败则尝试直接删旧 db 再 replace）。
        try:
            import subprocess as _sp
            users_sid = "*S-1-5-32-545"  # built-in Users group
            # 不再 /T：原来递归把整个 Rheia 目录树都放宽给 builtin Users，
            # 只给要替换的 db 本身（目录那一层只加不递归）。
            for target, extra in ((_DB_PATH, []), (_DB_DIR, [])):
                _sp.run(
                    ["icacls", target, "/grant", f"{users_sid}:(OI)(CI)M", "/Q"] + extra,
                    capture_output=True,
                    creationflags=0x08000000,  # CREATE_NO_WINDOW
                )
        except Exception:
            pass

        try:
            os.replace(tmp_path, _DB_PATH)
        except PermissionError:
            # 权限补不上时兜底：先把旧库改名留底（不再直接 remove —— 那是不可逆的
            # 删库动作，原来放在失败兜底路径里），再把临时文件顶上去。
            if os.path.exists(_DB_PATH):
                os.replace(_DB_PATH, _DB_PATH + ".bak-before-import")
            os.replace(tmp_path, _DB_PATH)
        _stake("import", f"← {src_path}", ok=True)
        # 注意：这里不再同步调用 _restore_credentials_into_program，
        # 因为它跑 WSL 命令会阻塞 UI。由调用方在后台线程执行。
        return (True, _DB_PATH)
    except Exception as e:
        # 失败时清理临时文件，避免残留
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        _stake("import", f"失败: {e}", ok=False)
        return (False, str(e))


def _restore_credentials_into_program() -> None:
    """把 credentials 表里的 API key / token 写回程序。"""
    try:
        from kernel.scout import getAllCredentials, ensureRecoveryTables
        ensureRecoveryTables()
        all_creds = getAllCredentials()
    except Exception as e:
        _stake("restore_credentials", f"读 DB 失败: {e}", ok=False)
        return

    for key, (value, provider) in all_creds.items():
        if not value:
            continue
        try:
            if key == "gateway_token":
                # 无视 db 里的 token：token 是 gateway 运行时凭据，权威来源是
                # openclaw.json 的 gateway.auth.token，程序运行时实时读取。
                # 备份里的旧 token 一律忽略，不做任何写回，避免覆写导致失联。
                _stake("restore_credentials", "无视 gateway_token（token 由 gateway 管理，不从 db 覆写）")
                continue
            elif key.endswith("_api_key"):
                pid = key[:-len("_api_key")]
                # 自定义 provider（不在 _ONBOARD_PROVIDER_FLAG 表内）交给
                # _restore_advanced_providers 处理（走文件，JSON 不被 shell 吃掉）。
                # 这里只处理官方 provider（deepseek 等，走 paste-api-key）。
                # ⚠⚠ 2026-10-17 逐行审计修：原来 import 的 `_ONBOARD_PROVIDER_FLAG` 在
                #   provider_setup.py 里**根本不存在**（全仓只此三处提到）⇒ ImportError 被
                #   下面 except 吞掉 ⇒ 官方 provider 的 API key 恢复**永远静默失效**。
                #   改成问**声明源**：mani 里声明的 provider 名单（`_load_providers()`），
                #   不在册的才算自定义、交给 advanced 那条路。
                from ui.web.provider_setup import _load_providers, _write_wsl_provider_key
                try:
                    _known_pids = {str(_p.get("id") or "") for _p in (_load_providers() or [])
                                   if isinstance(_p, dict)}
                except Exception:
                    _known_pids = set()
                if pid not in _known_pids:
                    _stake("restore_credentials", f"{pid} 不在 mani 声明的 provider 名单里，交给 advanced 恢复")
                    continue
                ok, msg = _write_wsl_provider_key(pid, value)
                _stake("restore_credentials", f"{pid} 写回: {'OK' if ok else msg}", ok=ok)
            else:
                _stake("restore_credentials", f"跳过未知 key: {key}", ok=False)
        except Exception as e:
            _stake("restore_credentials", f"{key} 写回异常: {e}", ok=False)

    _restore_advanced_providers()
    # ⚠ 2026-10-17 审计〔旧设计·该删〕：_restore_workspace_files 已删除（连带上面
    #   那个「异常只收成一条 stake」的静默包装）—— 不再往宿主 agent 工作区写回。


def _restore_advanced_providers() -> None:
    """把 advanced_providers 表里的高级配置 provider 完整写回（base_url/api/api_key/model_name）。"""
    try:
        from kernel.scout import getAllAdvancedProviders, ensureRecoveryTables
        ensureRecoveryTables()
        all_adv = getAllAdvancedProviders()
    except Exception as e:
        _stake("restore_advanced", f"读 advanced_providers 失败: {e}", ok=False)
        return

    if not all_adv:
        _stake("restore_advanced", "advanced_providers 表为空，跳过")
        return

    from ui.web.advanced_config import write_custom_provider
    for pid, info in all_adv.items():
        try:
            base_url = info.get("base_url", "")
            api = info.get("api", "")
            api_key = info.get("api_key", "")
            model_name = info.get("model_name", "")
            if not base_url and not model_name:
                _stake("restore_advanced", f"{pid} 无 base_url/model_name，跳过", ok=False)
                continue
            fields: dict[str, Any] = {
                "baseUrl": base_url or None,
                "api": api or None,
                "apiKey": api_key or None,
                "models": [{"id": model_name}] if model_name else None,
            }
            ok, msg = write_custom_provider(pid, fields)
            _stake("restore_advanced", f"{pid} 写回: {'OK' if ok else msg}", ok=ok)
        except Exception as e:
            _stake("restore_advanced", f"{pid} 写回异常: {e}", ok=False)


class _RestoreWorker(QThread):
    """后台跑「把 DB 凭据写回程序」，完成后发信号回主线程。

    ⚠ 2026-10-17 审计：原来用裸 threading.Thread + 在非主线程 self.imported.emit()
    （provider_panel 854-856 行记过这个坑：裸线程里发信号会丢投递，界面可能不刷新）。
    改用 QThread：跨线程信号是排队的，槽在主线程执行。
    """
    done = Signal()

    def run(self) -> None:
        try:
            _restore_credentials_into_program()
        except Exception as e:
            _stake("restore_credentials", f"后台恢复异常: {e}", ok=False)
        finally:
            self.done.emit()


class BackupRestoreDialog(QDialog):
    """备份与恢复对话框：导出 / 导入两个按钮。"""

    imported = Signal()  # 导入成功后发出，供上层延迟 reload

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("ui.restore.title"))
        # 固定合理宽度，高度按内容自适应（wordWrap 文案撑开，不钉死高度）
        self.setMinimumWidth(430)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.setStyleSheet(f"""
            QDialog {{ background:{_CARD_BG}; color:{_TEXT_PRIMARY}; }}
            QLabel {{ background:transparent; }}
        """)
        self._build()
        # 先按 sizeHint 定好高度、再居中：原来构造期就 move、构建后又 adjustSize 改一次
        # 高度（尺寸二次变更 → 显示时位置/大小会跳）。
        self.adjustSize()
        self._center_on_screen()

    def _center_on_screen(self) -> None:
        """把窗口移到屏幕正中央（垂直 + 水平）。"""
        from PySide6.QtWidgets import QApplication
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        geo = self.frameGeometry()
        geo.moveCenter(avail.center())
        self.move(geo.topLeft())

    def _build(self) -> None:
        lo = QVBoxLayout(self)
        lo.setContentsMargins(20, 18, 20, 18)
        lo.setSpacing(12)

        title = QLabel(tr("ui.restore.title"))
        title.setStyleSheet(f"color:{_TEXT_PRIMARY};font-size:16px;font-weight:bold;")
        lo.addWidget(title)

        desc = QLabel(
            tr("ui.restore.backup_desc1") + "\n"
            + tr("ui.restore.restore_desc2")
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color:{_TEXT_HINT};font-size:11px;")
        lo.addWidget(desc)

        db_label = QLabel(tr("ui.restore.db_location", path=_DB_PATH or tr("ui.restore.not_init")))
        db_label.setWordWrap(True)
        db_label.setStyleSheet(f"color:{_TEXT_HINT};font-size:10px;")
        lo.addWidget(db_label)

        lo.addStretch()

        # 第一行：备份初始化 / 初始化恢复（同尺寸紧凑按钮）
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch()

        basic_export_btn = QPushButton(tr("ui.restore.export_init"))
        basic_export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        basic_export_btn.setStyleSheet(self._btn_qss())
        basic_export_btn.setFixedSize(96, 28)
        fit_button_text(basic_export_btn, width=96)
        basic_export_btn.clicked.connect(self._on_export)
        btn_row.addWidget(basic_export_btn)

        basic_import_btn = QPushButton(tr("ui.restore.import_init"))
        basic_import_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        basic_import_btn.setStyleSheet(self._btn_qss())
        basic_import_btn.setFixedSize(96, 28)
        fit_button_text(basic_import_btn, width=96)
        basic_import_btn.clicked.connect(self._on_import)
        btn_row.addWidget(basic_import_btn)

        btn_row.addStretch()
        lo.addLayout(btn_row)

        # 第二行：导出对话（居中）
        export_row = QHBoxLayout()
        export_row.addStretch()
        export_conv_btn = QPushButton(tr("ui.restore.export_conv"))
        export_conv_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        export_conv_btn.setStyleSheet(self._btn_qss())
        export_conv_btn.setFixedSize(96, 28)
        fit_button_text(export_conv_btn, width=96)
        export_conv_btn.clicked.connect(self._on_export_conversation)
        export_row.addWidget(export_conv_btn)
        export_row.addStretch()
        lo.addLayout(export_row)

        close_btn = QPushButton(tr("ui.btn.close"))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{_TEXT_HINT};border:1px solid {TEXT['faint']};border-radius:6px;padding:6px 12px;}}"
            f"QPushButton:hover{{color:{_TEXT_PRIMARY};}}")
        close_btn.clicked.connect(self.accept)
        lo.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)

    def _btn_qss(self) -> str:
        return (
            f"QPushButton {{ background:{ACCENT}; color:#0a0a0a; border:none; "
            f"border-radius:5px; padding:5px 12px; font-size:12px; font-weight:600; }}"
            f"QPushButton:hover {{ background:{ACCENT_HOVER}; }}"
        )

    def _on_export(self) -> None:
        if not _DB_PATH or not os.path.isfile(_DB_PATH):
            QMessageBox.warning(self, tr("ui.browser.notice"), tr("ui.restore.no_backup_data"))
            return
        default_name = "Rheia-backup.db"
        target, _ = QFileDialog.getSaveFileName(
            self, tr("ui.restore.backup"), default_name, tr("ui.restore.db_filter"))
        if not target:
            return
        ok, msg = export_db(target)
        if ok:
            QMessageBox.information(self, tr("ui.restore.backup_ok"), tr("ui.restore.backup_ok_msg", msg=msg))
        else:
            QMessageBox.warning(self, tr("ui.restore.backup_failed"), msg)

    def _on_export_conversation(self) -> None:
        """导出对话：列 session 让你选 → 选目录 → 导出选中 session 的 .md。"""
        # 1) 列可导出 session，让用户选
        sessions = _list_exportable_sessions()
        if not sessions:
            QMessageBox.warning(self, tr("ui.restore.export_failed"), tr("ui.restore.read_index_fail"))
            return
        labels = [s["label"] for s in sessions]
        chosen, ok = QInputDialog.getItem(
            self, tr("ui.restore.choose_session"), tr("ui.restore.choose_session_hint"),
            labels, 0, False,
        )
        if not ok or not chosen:
            return
        idx = labels.index(chosen) if chosen in labels else -1
        if idx < 0:
            return
        session_key = sessions[idx]["key"]

        # 2) 选目录
        target_dir = QFileDialog.getExistingDirectory(self, tr("ui.restore.choose_conv_dir"))
        if not target_dir:
            return

        # 3) 导出
        ok2, msg = export_conversation_md(target_dir, session_key=session_key)
        if ok2:
            QMessageBox.information(self, tr("ui.restore.export_ok"), tr("ui.restore.export_ok_msg", msg=msg))
        else:
            QMessageBox.warning(self, tr("ui.restore.export_failed"), msg)

    def _on_import(self) -> None:
        src, _ = QFileDialog.getOpenFileName(
            self, tr("ui.restore.quick_restore"), "", tr("ui.restore.db_filter"))
        if not src:
            return
        # 复制 DB 文件很快，同步做
        ok, msg = import_db(src)
        if ok:
            # 导入成功先给确认反馈；API key/配置的恢复在后台静默进行，结束后发 imported 触发 reload
            QMessageBox.information(self, tr("ui.restore.import_ok"), tr("ui.restore.import_ok_msg"))
            # 后台恢复（QThread，完成后回主线程发 imported → 上层 reload）
            self._restore_worker = _RestoreWorker()
            self._restore_worker.done.connect(self.imported.emit)
            self._restore_worker.start()
        else:
            QMessageBox.warning(self, tr("ui.restore.import_failed"), msg)
