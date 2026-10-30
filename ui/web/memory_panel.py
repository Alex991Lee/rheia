"""
Memory 面板 —— 记忆功能（数据驱动，从 mani 长出来）
====================================================
侧边栏「记忆」入口 → QDialog → 6 个子页（QTabWidget）：
  1. 记忆整理（Dreaming 开关，me0002）
  2. 后台动作（memoryFlush + rememberAcrossConversations 开关，me0003）
  3. 记忆搜索（memory.search 配置，me0004）
  4. 记忆内容（卡上区只读查看，me0005）
  5. 备份恢复（me0006）

数据来源：mani.json 的 entries.memories（spider 提取的 switches/knobs）。
UI 外壳（容器/区划/颜色/布局）硬编；数据（开关名、默认值、命令、provider 清单）
全部从 mani 长出来，不硬编。
"""

from __future__ import annotations

import json
import logging
import os
import sys

logger = logging.getLogger(__name__)

_proj = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _proj not in sys.path:
    sys.path.insert(0, _proj)

try:
    from ui.theme import fit_button_text, BG, TEXT, ACCENT, ACCENT_HOVER, STATUS
except ImportError:
    BG = {"deep": "#1a1a1a", "card": "#2a2a2a", "hover": "#333333"}
    TEXT = {"primary": "#e0e0e0", "secondary": "#b0b0b0", "hint": "#a0a0a0"}
    ACCENT = "#f37021"
    ACCENT_HOVER = "#ff8833"
    STATUS = {"green": "#5a9e6f", "yellow": "#d9a840", "red": "#e85a4a", "gray": "#6a5e52"}

try:
    from ui.i18n import tr
except ImportError:
    tr = lambda key, **fmt: key

try:
    from PySide6.QtCore import Qt, QThread, Signal
    from PySide6.QtWidgets import (
        QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton,
        QVBoxLayout, QWidget, QTextEdit, QTabWidget, QSizePolicy,
        QCheckBox, QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox,
    )
    _HAS_PYQT = True
except ImportError:
    _HAS_PYQT = False

WSL_DISTRO = os.environ.get("RHEIA_WSL_DISTRO", "").strip() or "Ubuntu"
_MANI_PATH = os.path.join(_proj, "kernel", "large", "mani.json")

_CARD_BG = BG["deep"]
_TEXT_PRIMARY = TEXT["primary"]
_TEXT_HINT = TEXT["hint"]
_GREEN = STATUS["green"]
_RED = STATUS["red"]

# 控件通用样式（下拉/计数器/输入框）
_COMBO_QSS = f"background:#222; color:{_TEXT_PRIMARY}; border:1px solid #444; border-radius:4px; padding:4px;"
_SPIN_QSS = f"background:#222; color:{_TEXT_PRIMARY}; border:1px solid #444; border-radius:4px; padding:2px 6px;"
_LINE_QSS = f"background:#222; color:{_TEXT_PRIMARY}; border:1px solid #444; border-radius:4px; padding:4px 8px;"
_BTN_ORANGE = f"background:{ACCENT}; color:#0a0a0a; border:none; border-radius:6px; padding:8px 16px; font-weight:600;"
_BTN_ORANGE_DISABLED = f"background:#555; color:#999; border:none; border-radius:6px; padding:8px 16px; font-weight:600;"


def _stake(tag: str, msg: str = "", ok: bool = True) -> None:
    """检测桩：写 memory 面板的关键节点到 stake.log，定位数据读取/子页构建哪一步炸。"""
    try:
        from main.main import stk
        stk(f"memory.{tag}", msg, ok=ok)
    except Exception:
        pass


def _available_models() -> list[dict]:
    """拉取客户已配置且 available 的模型清单（供「做梦模型」下拉）。"""
    from main.main import cli_usage
    models_list = cli_usage("models", "list")
    if not models_list:
        return []
    rc, out, _ = _run_wsl(
        f'wsl -d {WSL_DISTRO} -u root -- bash -c '
        f'"export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/sbin:/bin; '
        f'{models_list} --json 2>/dev/null"'
    )
    if rc != 0 or not out.strip():
        return []
    try:
        import json as _json
        data = _json.loads(out)
    except _json.JSONDecodeError:
        return []
    models = data.get("models", []) if isinstance(data, dict) else data
    out_list = []
    for m in models:
        if not isinstance(m, dict):
            continue
        if not m.get("available"):
            continue
        key = m.get("key", "")
        name = m.get("name", "") or key
        out_list.append({"key": key, "name": name})
    return out_list


# ═══════════════════════════════════════════════════════════════
#  数据读取层 —— 从 mani 长出来
# ═══════════════════════════════════════════════════════════════

def _load_mani() -> dict:
    try:
        with open(_MANI_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _load_memory_entries() -> list[dict]:
    return (_load_mani().get("entries") or {}).get("memories") or []


def _memory_switches() -> dict:
    """合并所有 memory 条目的 switches（如 dreaming）。返回 {name: {on,off,status,enabled_default}}"""
    out: dict = {}
    for e in _load_memory_entries():
        for name, sw in (e.get("switches") or {}).items():
            if isinstance(sw, dict):
                out[name] = sw
    return out


def _memory_knobs() -> list[dict]:
    """合并所有 memory 条目的 knobs（如 memory.search.enabled）。返回 [{key,type,default,path}]"""
    out: list = []
    for e in _load_memory_entries():
        out.extend(e.get("knobs") or [])
    return out


def _knobs_by_path(path: str) -> list[dict]:
    return [k for k in _memory_knobs() if k.get("path") == path]


# ⚠ 2026-10-17 逐行审计（该删删）：`_memory_tiers()` 已删 —— 记忆内容页已改成**列卡上的区**
#   （mem/user/expertise/relat/corpus），不再列宿主工作区的 tier 文件；该取数口全仓零调用方。


def _memory_providers() -> list[dict]:
    """合并所有 memory 条目的 providers（embedding provider 清单）。"""
    out: list = []
    for e in _load_memory_entries():
        out.extend(e.get("providers") or [])
    return out

# ═══════════════════════════════════════════════════════════════
#  卡侧读取 —— 记忆内容由**卡**持有（不在壳里直读宿主工作区）
# ═══════════════════════════════════════════════════════════════

#: 「记忆内容」页上列的五项＝**卡上的区**（卡的结构，不是客户数据 ⇒ 不随 mani 变）：
#:   mem＝记忆、user＝他的档案、expertise＝知识库、relat＝共同经历、corpus＝语料。
_CARD_ZONES = ("mem", "user", "expertise", "relat", "corpus")


def _wsl_to_win(p: str) -> str:
    """/mnt/e/x → E:\\x（槽账里存的是 WSL 形态；壳在本机原地读写同一张卡）。"""
    s = str(p or "").strip()
    if not s:
        return ""
    if s.startswith("/mnt/") and len(s) > 6 and s[6] == "/":
        return s[5].upper() + ":" + s[6:].replace("/", "\\")
    return s


def _resolve_card_path() -> str:
    """认当前卡路径 —— 只读壳自己落的账，**不写死绝对路径**。读不到 ⇒ 空串（不猜）。

    ① 壳的活跃卡账：`baseline_pool.db`（scout）→ fallback `active_persona.json`；
    ② 槽账 `~/.openclaw/slot_window.json` 的 `card`（顶层＝最后插的那张；WSL 形态 ⇒ 转回来）。
    """
    def _ok(_p) -> str:
        """路径**能落地**才算认到（本机 / WSL 两种写法各认一次）。"""
        _p = str(_p or "").strip()
        for _cand in (_p, _win_to_wsl(_p), _wsl_to_win(_p)):
            if _cand and os.path.isfile(_cand):
                return _cand
        return ""

    # ① 活跃卡账（壳自己写的那两份：先库、后 JSON）
    try:
        from kernel.scout import getPref, ensureRecoveryTables
        ensureRecoveryTables()
        _raw = getPref("active_persona")
        if _raw:
            _hit = _ok((json.loads(_raw) or {}).get("filepath"))
            if _hit:
                return _hit
    except Exception:                                             # noqa: BLE001
        pass
    try:
        _pd = os.environ.get("PROGRAMDATA") or "/mnt/c/ProgramData"
        _ap = os.path.join(_pd, "Rheia", "active_persona.json")
        if os.path.isfile(_ap):
            with open(_ap, encoding="utf-8", errors="replace") as f:
                _hit = _ok((json.load(f) or {}).get("filepath"))
            if _hit:
                return _hit
    except Exception:                                             # noqa: BLE001
        pass
    # ② 槽账（壳的边界账；卡位置由壳载入时记下）
    try:
        from persona.indivi import read_slot_window
        _hit = _ok((read_slot_window() or {}).get("card"))
        if _hit:
            return _hit
    except Exception:                                             # noqa: BLE001
        pass
    return ""


def _read_card_zone(card_path: str, zone: str) -> tuple[bool, str]:
    """从**卡**上读一个区（**只读，不写卡**）。返回 (读到没, 正文或如实说明)。

    读法走实测那套：`persona.carddoor.deep_read_zone`（按区名取区、解压、去 INDPRV
    前缀）；它不可用时退回壳自己的桥 `persona.cardBridge.read_indm*`。
    读不到不拿空串糊弄：卡不在 ⇒ 如实说；在但区里没内容 ⇒ 如实说。
    """
    if not card_path or not os.path.isfile(card_path):
        return False, tr("ui.persona.m13")

    def _door(_c: str, _z: str) -> bytes:
        from persona.carddoor import deep_read_zone
        return deep_read_zone(_c, _z)

    def _bridge(_c: str, _z: str) -> bytes:
        from persona.cardBridge import (read_indm, read_indm_corpus, read_indm_expertise,
                                        read_indm_relat)
        _pick = {"mem": lambda: read_indm(_c)[2],
                 "user": lambda: read_indm(_c)[3],
                 "relat": lambda: read_indm_relat(_c),
                 "expertise": lambda: read_indm_expertise(_c),
                 "corpus": lambda: read_indm_corpus(_c)}
        _fn = _pick.get(_z)
        return _fn() if _fn else b""

    for _reader in (_door, _bridge):
        try:
            _body = _reader(card_path, zone) or b""
        except Exception:                                         # noqa: BLE001
            _body = b""
        if _body:
            return True, _body.decode("utf-8", "replace")
    return False, tr("ui.persona.m14")


# ═══════════════════════════════════════════════════════════════
#  WSL 配置读写（复用 wsl_config）
# ═══════════════════════════════════════════════════════════════

def _read_config() -> dict:
    try:
        from main.main import read as _r
        return _r()
    except Exception:
        return {}


def _write_config(cfg: dict) -> bool:
    try:
        from main.main import write as _w
        return _w(cfg)
    except Exception:
        return False


def _set_config_path(cfg: dict, path: str, value) -> dict:
    """按点号路径写 dict（plugins.entries.memory-core.config.dreaming.enabled）。"""
    parts = path.split(".")
    node = cfg
    for p in parts[:-1]:
        node = node.setdefault(p, {})
    node[parts[-1]] = value
    return cfg


def _get_config_path(cfg: dict, path: str, default=None):
    parts = path.split(".")
    node = cfg
    for p in parts:
        if not isinstance(node, dict) or p not in node:
            return default
        node = node[p]
    return node


def _run_wsl(cmd: str) -> tuple[int, str, str]:
    """跑一条 WSL 命令，返回 (rc, stdout, stderr)。

    ⚠ 壳层不留时间闸：不传 timeout（命令返回即信号），
    原先那个「签名留着、值不生效」的死 timeout 参数已去掉。
    """
    import subprocess
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True,
                           encoding="utf-8", errors="replace",
                           creationflags=subprocess.CREATE_NO_WINDOW, startupinfo=si)
        return r.returncode, (r.stdout or ""), (r.stderr or "")
    except Exception:
        return -1, "", "error"


class _AsyncCmd(QThread):
    """后台执行一条 WSL 命令，避免长命令（装插件/重建索引）阻塞 UI 假死。"""
    done = Signal(int, str, str)  # (rc, stdout, stderr)

    def __init__(self, cmd: str, parent=None):
        super().__init__(parent)
        self._cmd = cmd

    def run(self):
        # 不再存/传 timeout（那一路都是安慰剂，读代码的人会以为有闸）。
        rc, out, err = _run_wsl(self._cmd)
        self.done.emit(rc, out, err)


class _ModelsWorker(QThread):
    """后台拉取已配置模型清单，避免 WSL 冷启动 + models list 阻塞 UI 假死。"""
    done = Signal(list)

    def run(self):
        self.done.emit(_available_models())


# ═══════════════════════════════════════════════════════════════
#  Memory 主面板（me0007 骨架）
# ═══════════════════════════════════════════════════════════════

class MemoryPanel(QDialog):
    """记忆面板：6 子页。"""

    def __init__(self, parent=None):
        if not _HAS_PYQT:
            raise RuntimeError("PySide6 not available")
        super().__init__(parent)
        self.setWindowTitle(tr("ui.memory.title"))
        from ui.theme import fitToScreen
        fitToScreen(self, 0.2718, 0.46)
        self.setStyleSheet(f"QDialog {{ background: {_CARD_BG}; color: {_TEXT_PRIMARY}; }}")

        # 一次读 config，5 个子页复用，避免构造时多次同步起 WSL 假死
        self._cfg = _read_config()

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        _stake("panel.open", "打开记忆面板")

        tabs = QTabWidget()
        tabs.setStyleSheet(f"""
            QTabWidget::pane {{ border: 1px solid #333; }}
            QTabBar::tab {{ color:{_TEXT_PRIMARY}; padding:8px 8px; background:#222; }}
            QTabBar::tab:selected {{ background:{ACCENT}; color:#0a0a0a; }}
        """)
        # 5 个标签等比均分铺满整行，不出现左右滚动箭头
        from PySide6.QtWidgets import QTabBar
        tabs.tabBar().setExpanding(True)

        # ① 记忆整理（Dreaming）—— me0002
        _stake("tab.dreaming", "构建记忆整理子页")
        tabs.addTab(self._build_dreaming_page(), tr("ui.memory.tab.dreaming"))

        # ② 后台动作 —— me0003
        _stake("tab.background", "构建后台动作子页")
        tabs.addTab(self._build_background_page(), tr("ui.memory.tab.background"))

        # ③ 记忆搜索 —— me0004
        _stake("tab.search", "构建记忆搜索子页")
        tabs.addTab(self._build_search_page(), tr("ui.memory.tab.search"))

        # ④ 记忆内容 —— me0005
        _stake("tab.content", "构建记忆内容子页")
        tabs.addTab(self._build_content_page(), tr("ui.memory.tab.content"))

        # ⑤ 备份恢复 —— me0006
        _stake("tab.backup", "构建备份恢复子页")
        tabs.addTab(self._build_backup_page(), tr("ui.memory.tab.backup"))

        root.addWidget(tabs)
        _stake("panel.built", "记忆面板构建完成")

        # 模型清单后台拉取（models list 慢），回来灌进两个下拉，不在构造时同步起 WSL
        self._models_worker = _ModelsWorker()
        self._models_worker.done.connect(self._on_models_ready)
        self._models_worker.start()

    # ── 子页构建 ──

    def _on_models_ready(self, models: list[dict]):
        """后台拉回的模型清单，灌进「做梦模型」「搜索模型」两个下拉。

        面板此时可能已经显示：下拉逐条 addItem 会让已显示的控件反复重排/重绘
        （用户正开着该页签时内容会跳一下）。整批灌完再放行重绘，少刷一次是一次。
        """
        for w in (self._dream_model, self._search_model):
            w.setUpdatesEnabled(False)
        try:
            for m in models:
                self._dream_model.addItem(m["name"], m["key"])
                self._search_model.addItem(m["name"], m["key"])
        finally:
            for w in (self._dream_model, self._search_model):
                w.setUpdatesEnabled(True)

    def _build_dreaming_page(self) -> QWidget:
        """me0002：dreaming 开关 + 频率 + 模型。"""
        page = QWidget()
        lo = QVBoxLayout(page)
        lo.setContentsMargins(16, 16, 16, 16)
        lo.setSpacing(10)

        title = QLabel(tr("ui.memory.dreaming.title"))
        title.setStyleSheet(f"color:{_TEXT_PRIMARY}; font-size:16px; font-weight:bold; background:transparent;")
        lo.addWidget(title)

        desc = QLabel(tr("ui.memory.dreaming.desc"))
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color:{_TEXT_HINT}; font-size:12px;")
        lo.addWidget(desc)

        # 从 mani 的 dreaming switches/knobs 长出来
        sw = _memory_switches().get("dreaming", {})
        knobs = _knobs_by_path("plugins.entries.memory-core.config.dreaming")
        _stake("dreaming.data", f"switch={bool(sw)} knobs={len(knobs)}", ok=bool(sw) and len(knobs) > 0)

        # enabled 开关（switch 的 enabled_default 优先）
        cb = QCheckBox(tr("ui.memory.enable"))
        cb.setStyleSheet(f"color:{_TEXT_PRIMARY}; spacing:8px; font-size:13px;")
        cfg = self._cfg
        cur = _get_config_path(cfg, "plugins.entries.memory-core.config.dreaming.enabled", None)
        default_val = bool(sw.get("enabled_default", True))
        cb.setChecked(cur if cur is not None else default_val)
        cb.toggled.connect(lambda checked: self._set_dreaming_enabled(checked, cb))
        lo.addWidget(cb)

        # ── 运行频率（小节标题，独立一行，不再当行内标签放在下拉前）──
        freq_title = QLabel(tr("ui.memory.freq_title"))
        freq_title.setStyleSheet(f"color:{ACCENT}; font-size:13px; font-weight:bold; background:transparent; margin-top:4px;")
        lo.addWidget(freq_title)

        # 模式下拉（与下面「时间」行的输入框左对齐：统一 40 宽 label 列）
        mode_row = QHBoxLayout()
        mode_lbl = QLabel(tr("ui.memory.mode"))
        mode_lbl.setFixedWidth(36)
        mode_lbl.setStyleSheet(f"color:{_TEXT_PRIMARY}; background:transparent;")
        self._freq_mode = QComboBox()
        self._freq_mode.setStyleSheet(_COMBO_QSS)
        self._freq_mode.setFixedWidth(82)
        self._freq_mode.addItem(tr("ui.memory.freq_daily"), "daily")
        self._freq_mode.addItem(tr("ui.memory.freq_weekly"), "weekly")
        self._freq_mode.addItem(tr("ui.memory.freq_monthly"), "monthly")
        self._freq_mode.currentIndexChanged.connect(self._on_freq_mode_changed)
        mode_row.addWidget(mode_lbl)
        mode_row.addWidget(self._freq_mode)
        mode_row.addStretch()
        lo.addLayout(mode_row)

        # 频率细节区（按模式动态渲染：每天→每隔N天 / 每周→勾周几 / 每月→每月几号）
        self._freq_detail = QVBoxLayout()
        lo.addLayout(self._freq_detail)

        # 时间：小时计数器 + 分钟文本框
        time_row = QHBoxLayout()
        time_lbl = QLabel(tr("ui.memory.time"))
        time_lbl.setFixedWidth(36)
        time_lbl.setStyleSheet(f"color:{_TEXT_PRIMARY}; background:transparent;")
        self._freq_hour = QSpinBox()
        self._freq_hour.setRange(0, 23)
        self._freq_hour.setValue(3)
        self._freq_hour.setSuffix(tr("ui.memory.hour_suffix"))
        self._freq_hour.setStyleSheet(_SPIN_QSS)
        self._freq_min = QLineEdit()
        self._freq_min.setText("0")
        self._freq_min.setMaxLength(3)
        self._freq_min.setFixedWidth(54)
        self._freq_min.setStyleSheet(_LINE_QSS)
        min_lbl = QLabel(tr("ui.memory.minute"))
        min_lbl.setStyleSheet(f"color:{_TEXT_PRIMARY}; background:transparent;")
        time_row.addWidget(time_lbl)
        time_row.addWidget(self._freq_hour)
        time_row.addWidget(min_lbl)
        time_row.addWidget(self._freq_min)
        time_row.addStretch()
        lo.addLayout(time_row)

        # ── 模型（下拉，从已配模型清单长出）──
        model_row = QHBoxLayout()
        model_lbl = QLabel(tr("ui.memory.model"))
        model_lbl.setFixedWidth(36)
        model_lbl.setStyleSheet(f"color:{_TEXT_PRIMARY}; background:transparent;")
        self._dream_model = QComboBox()
        self._dream_model.setStyleSheet(_COMBO_QSS)
        self._dream_model.setMaximumWidth(254)  # 大屏最多 40 西文字符；小屏可缩，不顶大窗体
        self._dream_model.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._dream_model.addItem(tr("ui.memory.follow_default_model"), "")
        self._dream_model.setCurrentIndex(0)
        # 模型清单由 __init__ 的 _ModelsWorker 后台拉取灌入，构造时不同步起 WSL
        model_row.addWidget(model_lbl)
        model_row.addWidget(self._dream_model)
        model_row.addStretch()
        lo.addLayout(model_row)

        # 保存按钮
        self._save_btn = QPushButton(tr("ui.btn.save"))
        self._save_btn.setStyleSheet(
            f"QPushButton {{ background:{ACCENT}; color:#0a0a0a; border:none; "
            f"border-radius:6px; padding:6px 16px; font-weight:600; }}"
            f"QPushButton:pressed {{ background:{ACCENT_HOVER}; }}"
        )
        self._save_btn.clicked.connect(self._save_dreaming_config)
        lo.addWidget(self._save_btn, alignment=Qt.AlignmentFlag.AlignLeft)

        # 初始渲染频率细节
        self._on_freq_mode_changed()

        # 回显已保存的 frequency（核心：重开页面显示上次配置，而非硬编码 3:00）
        saved_cron = _get_config_path(cfg, "plugins.entries.memory-core.config.dreaming.frequency", None)
        if saved_cron:
            self._apply_freq_cron(saved_cron)

        lo.addStretch()
        return page

    def _clear_layout_items(self, layout):
        """递归清空一个布局里的所有 widget（含嵌套子布局），避免 addLayout 项残留。

        根因：_freq_detail 里加的是子布局（addLayout(row)），item.widget() 对
        布局项返回 None，旧控件 deleteLater 不到，残留叠在窗体左上角。
        """
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
            elif item.layout() is not None:
                self._clear_layout_items(item.layout())

    def _on_freq_mode_changed(self, mode=None):
        """按频率模式动态渲染细节区。mode 缺省或为索引 int 时取下拉当前 data。

        注：currentIndexChanged 信号传的是 int（索引），不是字符串，
        这里归一化成 currentData() 的 "daily"/"weekly"/"monthly"。
        """
        # 切模式要重建这块（功能如此），但「清空 → 重建」整段压在同一个重绘里做完：
        # 否则容器可见区先塌成空、再长回来，叠在对话框重绘上就是一次闪。
        holder = self._freq_detail.parentWidget()
        if holder is not None:
            holder.setUpdatesEnabled(False)
        # 清空旧细节（递归清子布局，杜绝 addLayout 残留）
        self._clear_layout_items(self._freq_detail)

        if not isinstance(mode, str) or not mode:
            mode = self._freq_mode.currentData() or "daily"

        if mode == "daily":
            row = QHBoxLayout()
            lbl = QLabel(tr("ui.memory.interval"))
            lbl.setFixedWidth(36)
            lbl.setStyleSheet(f"color:{_TEXT_PRIMARY}; background:transparent;")
            self._freq_every_n = QSpinBox()
            self._freq_every_n.setRange(1, 365)
            self._freq_every_n.setValue(1)
            self._freq_every_n.setStyleSheet(_SPIN_QSS)
            unit = QLabel(tr("ui.memory.day"))
            unit.setStyleSheet(f"color:{_TEXT_PRIMARY}; background:transparent;")
            row.addWidget(lbl)
            row.addWidget(self._freq_every_n)
            row.addWidget(unit)
            row.addStretch()
            self._freq_detail.addLayout(row)
        elif mode == "weekly":
            row = QHBoxLayout()
            lbl = QLabel(tr("ui.memory.weekday"))
            lbl.setFixedWidth(36)
            lbl.setStyleSheet(f"color:{_TEXT_PRIMARY}; background:transparent;")
            self._freq_weekdays = []
            days = [tr("ui.memory.wd_mon"), tr("ui.memory.wd_tue"), tr("ui.memory.wd_wed"),
                    tr("ui.memory.wd_thu"), tr("ui.memory.wd_fri"), tr("ui.memory.wd_sat"),
                    tr("ui.memory.wd_sun")]
            for i, d in enumerate(days):
                cbw = QCheckBox(d)
                cbw.setStyleSheet(f"color:{_TEXT_PRIMARY}; spacing:2px; font-size:12px;")
                if i == 0:
                    cbw.setChecked(True)
                self._freq_weekdays.append(cbw)
                row.addWidget(cbw)
            row.addStretch()
            self._freq_detail.addLayout(row)
        elif mode == "monthly":
            row = QHBoxLayout()
            lbl = QLabel(tr("ui.memory.date"))
            lbl.setFixedWidth(36)
            lbl.setStyleSheet(f"color:{_TEXT_PRIMARY}; background:transparent;")
            self._freq_day = QComboBox()
            self._freq_day.setStyleSheet(_COMBO_QSS)
            for d in range(1, 29):
                self._freq_day.addItem(tr("ui.memory.day_n", n=d), str(d))
            self._freq_day.addItem(tr("ui.memory.last_day"), "L")
            row.addWidget(lbl)
            row.addWidget(self._freq_day)
            row.addStretch()
            self._freq_detail.addLayout(row)

        if holder is not None:
            holder.setUpdatesEnabled(True)   # 整块建好再放行重绘（一次到位）

    def _build_freq_cron(self) -> str:
        """把 UI 控件组合拼回 cron 表达式。分钟超 60 归零、向小时进一。"""
        minute = 0
        try:
            minute = int(self._freq_min.text().strip() or "0")
        except ValueError:
            minute = 0
        hour = self._freq_hour.value()
        # 分钟超 60 → 归零 + 小时进一
        if minute >= 60:
            hour = (hour + 1) % 24
            minute = minute % 60

        mode = self._freq_mode.currentData() or "daily"
        if mode == "daily":
            every = getattr(self, "_freq_every_n", None)
            n = every.value() if every else 1
            dom = "*/%d" % n if n > 1 else "*"
            return f"{minute} {hour} {dom} * *"
        elif mode == "weekly":
            wds = []
            if hasattr(self, "_freq_weekdays"):
                for i, cbw in enumerate(self._freq_weekdays):
                    if cbw.isChecked():
                        wds.append(str(i + 1))  # cron 周日=0/7，周一=1
            dow = ",".join(wds) if wds else "1"
            return f"{minute} {hour} * * {dow}"
        else:  # monthly
            day = getattr(self, "_freq_day", None)
            dom = day.currentData() if day else "1"
            return f"{minute} {hour} {dom} * *"

    def _apply_freq_cron(self, cron: str):
        """把已保存的 cron 字符串反向解析，回显到频率控件（重开页面不再硬编码 3:00）。

        cron 格式（_build_freq_cron 的正向格式，反向照此解析）：
          daily   {min} {hour} {dom} * *      dom = * 或 */n（n>1 每隔 n 天）
          weekly  {min} {hour} * * {dow}      dow 逗号分隔（1=周一…7=周日）
          monthly {min} {hour} {dom} * *      dom = 纯数字（每月几号）或 L（月末）
        daily 与 monthly 的 cron 外形相同，靠 dom 区分：* / */n = daily，纯数字/L = monthly。
        """
        parts = cron.split()
        if len(parts) != 5:
            # 读不懂就静默 return 的话，界面会一直显示默认值，用户以为配置没生效 —— 落账。
            _stake("dreaming.freq", f"无法解析已保存的 frequency={cron!r}，保留当前显示", ok=False)
            return
        minute, hour, dom, _month, dow = parts

        # 时间字段回显
        try:
            self._freq_hour.setValue(int(hour) % 24)
        except (ValueError, AttributeError):
            pass
        try:
            self._freq_min.setText(str(int(minute) % 60))
        except (ValueError, AttributeError):
            pass

        # 模式判定：dom 为 * 或 */n → daily；dow 非 * → weekly；否则 monthly
        if dow != "*":
            self._freq_mode.setCurrentIndex(self._freq_mode.findData("weekly"))
            self._on_freq_mode_changed("weekly")
            # 勾选对应星期（cron 周一=1 … 周日=7）
            selected = set()
            for w in dow.split(","):
                try:
                    selected.add(int(w))
                except ValueError:
                    pass
            if hasattr(self, "_freq_weekdays"):
                for i, cbw in enumerate(self._freq_weekdays):
                    cbw.setChecked((i + 1) in selected)
        elif dom == "*" or dom.startswith("*/"):
            self._freq_mode.setCurrentIndex(self._freq_mode.findData("daily"))
            self._on_freq_mode_changed("daily")
            if dom.startswith("*/"):
                try:
                    n = int(dom[2:])
                except ValueError:
                    n = 1
                every = getattr(self, "_freq_every_n", None)
                if every:
                    every.setValue(max(1, n))
        else:
            self._freq_mode.setCurrentIndex(self._freq_mode.findData("monthly"))
            self._on_freq_mode_changed("monthly")
            day = getattr(self, "_freq_day", None)
            if day:
                idx = day.findData(dom)
                if idx >= 0:
                    day.setCurrentIndex(idx)

    def _save_dreaming_config(self):
        cfg = _read_config()
        cron = self._build_freq_cron()
        _set_config_path(cfg, "plugins.entries.memory-core.config.dreaming.frequency", cron)
        model_key = self._dream_model.currentData()
        if model_key:
            _set_config_path(cfg, "plugins.entries.memory-core.config.dreaming.model", model_key)
        ok = _write_config(cfg)
        # 点击反馈：保存快，直接改按钮文字提示（对齐第三标签页搜索页）
        self._save_btn.setText(tr("ui.memory.saved") if ok else tr("ui.memory.save_failed"))
        if not ok:
            # 原来按钮文字和弹框是同一句「保存失败」，说了两遍还是没说失败在哪。
            QMessageBox.warning(self, tr("ui.memory.save_failed"),
                                f"{tr('ui.memory.save_failed')}\n frequency={cron} model={model_key or 'default'}")
        _stake("dreaming.save", f"frequency={cron} model={model_key or 'default'} ok={ok}", ok=ok)

    def _humanize_key(self, key: str) -> str:
        m = {"enabled": tr("ui.memory.knob_enabled"),
             "frequency": tr("ui.memory.knob_frequency"),
             "model": tr("ui.memory.knob_model")}
        return m.get(key, key)

    def _set_dreaming_enabled(self, checked: bool, cb: QCheckBox):
        cfg = _read_config()
        _set_config_path(cfg, "plugins.entries.memory-core.config.dreaming.enabled", checked)
        if not _write_config(cfg):
            # 静默回滚最坑：用户只看到开关自己弹回来。落账 + 提示是哪个开关。
            _stake("dreaming.enabled", "写入失败，开关已回滚", ok=False)
            cb.blockSignals(True)
            cb.setChecked(not checked)
            cb.blockSignals(False)
            QMessageBox.warning(self, tr("ui.memory.save_failed"), cb.text())

    def _build_background_page(self) -> QWidget:
        """me0003：memoryFlush + rememberAcrossConversations。"""
        page = QWidget()
        lo = QVBoxLayout(page)
        lo.setContentsMargins(16, 16, 16, 16)
        lo.setSpacing(10)

        title = QLabel(tr("ui.memory.background.title"))
        title.setStyleSheet(f"color:{_TEXT_PRIMARY}; font-size:16px; font-weight:bold; background:transparent;")
        lo.addWidget(title)

        # memoryFlush
        cb1 = QCheckBox(tr("ui.memory.memory_flush"))
        cb1.setStyleSheet(f"color:{_TEXT_PRIMARY}; spacing:8px; font-size:13px;")
        cfg = self._cfg
        cur1 = _get_config_path(cfg, "agents.defaults.compaction.memoryFlush.enabled", None)
        cb1.setChecked(cur1 if cur1 is not None else True)
        cb1.toggled.connect(lambda checked: self._set_path_bool("agents.defaults.compaction.memoryFlush.enabled", checked, cb1))
        lo.addWidget(cb1)

        # rememberAcrossConversations（条件默认：个人开 / 有 DM 隔离关）
        cb2 = QCheckBox(tr("ui.memory.remember_across"))
        cb2.setStyleSheet(f"color:{_TEXT_PRIMARY}; spacing:8px; font-size:13px;")
        cur2 = _get_config_path(cfg, "memory.search.rememberAcrossConversations", None)
        if cur2 is None:
            # 官方默认：有 DM 隔离则关，否则开
            dm_scope = _get_config_path(cfg, "session.dmScope", None)
            default_val = not (dm_scope and dm_scope != "main")
            cur2 = default_val
        cb2.setChecked(cur2)
        cb2.toggled.connect(lambda checked: self._set_path_bool("memory.search.rememberAcrossConversations", checked, cb2))
        lo.addWidget(cb2)

        lo.addStretch()
        return page

    def _set_path_bool(self, path: str, checked: bool, cb: QCheckBox):
        cfg = _read_config()
        _set_config_path(cfg, path, checked)
        if not _write_config(cfg):
            # 同 _set_dreaming_enabled：写失败不能无声无息地把开关翻回去。
            _stake("config.bool", f"{path} 写入失败，开关已回滚", ok=False)
            cb.blockSignals(True)
            cb.setChecked(not checked)
            cb.blockSignals(False)
            QMessageBox.warning(self, tr("ui.memory.save_failed"), cb.text())

    def _build_search_page(self) -> QWidget:
        """me0004：memory.search 配置（provider 下拉 + 依赖区动态渲染 + 重建引导）。"""
        page = QWidget()
        lo = QVBoxLayout(page)
        lo.setContentsMargins(16, 16, 16, 16)
        lo.setSpacing(10)

        title = QLabel(tr("ui.memory.search.title"))
        title.setStyleSheet(f"color:{_TEXT_PRIMARY}; font-size:16px; font-weight:bold; background:transparent;")
        lo.addWidget(title)

        # provider 下拉（从 mani 长出来，限宽）
        row = QHBoxLayout()
        lbl = QLabel(tr("ui.memory.embedding_provider"))
        lbl.setFixedWidth(91)
        lbl.setStyleSheet(f"color:{_TEXT_PRIMARY}; background:transparent;")
        self._search_combo = QComboBox()
        self._search_combo.setStyleSheet(_COMBO_QSS)
        self._search_combo.setFixedWidth(163)  # 收窄后约 21 个 byte 宽
        providers = self._extract_providers()
        self._search_combo.addItems(providers)
        cfg = self._cfg
        cur = _get_config_path(cfg, "memory.search.provider", None)
        if cur in providers:
            self._search_combo.setCurrentText(cur)
        self._search_combo.currentTextChanged.connect(self._on_provider_changed)
        row.addWidget(lbl)
        row.addWidget(self._search_combo)
        row.addStretch()
        lo.addLayout(row)

        # 依赖区（按 provider 动态渲染）
        self._dep_area = QVBoxLayout()
        lo.addLayout(self._dep_area)

        # model 下拉（从已配模型长出，不是手输）
        row_m = QHBoxLayout()
        lm = QLabel(tr("ui.memory.model"))
        lm.setFixedWidth(91)
        lm.setStyleSheet(f"color:{_TEXT_PRIMARY}; background:transparent;")
        self._search_model = QComboBox()
        self._search_model.setStyleSheet(_COMBO_QSS)
        self._search_model.setMaximumWidth(254)  # 大屏最多 40 西文字符；小屏可缩
        self._search_model.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._search_model.addItem(tr("ui.memory.default"), "")
        self._search_model.setCurrentIndex(0)
        # 模型清单由 __init__ 的 _ModelsWorker 后台拉取灌入
        row_m.addWidget(lm)
        row_m.addWidget(self._search_model)
        row_m.addStretch()
        lo.addLayout(row_m)

        # 命中数 + 相关度（并排一行，左对齐，与上两行 label 同宽）
        row_q = QHBoxLayout()
        # 最近相关的 N 条
        lbl_mr = QLabel(tr("ui.memory.recent_related"))
        lbl_mr.setFixedWidth(91)
        lbl_mr.setStyleSheet(f"color:{_TEXT_PRIMARY}; background:transparent;")
        self._search_maxresults = QSpinBox()
        self._search_maxresults.setRange(1, 100)
        self._search_maxresults.setValue(6)
        self._search_maxresults.setStyleSheet(_SPIN_QSS)
        lbl_mr_tail = QLabel(tr("ui.memory.item_unit"))
        lbl_mr_tail.setStyleSheet(f"color:{_TEXT_PRIMARY}; background:transparent;")
        # 相关度
        lbl_sc = QLabel(tr("ui.memory.relevance"))
        lbl_sc.setStyleSheet(f"color:{_TEXT_PRIMARY}; background:transparent; margin-left:16px;")
        self._search_minscore = QDoubleSpinBox()
        self._search_minscore.setRange(0.0, 1.0)
        self._search_minscore.setSingleStep(0.05)
        self._search_minscore.setValue(0.35)
        self._search_minscore.setStyleSheet(_SPIN_QSS)
        row_q.addWidget(lbl_mr)
        row_q.addWidget(self._search_maxresults)
        row_q.addWidget(lbl_mr_tail)
        row_q.addWidget(lbl_sc)
        row_q.addWidget(self._search_minscore)
        row_q.addStretch()
        lo.addLayout(row_q)

        # 保存 + 重建 并排按钮（橙色，异步 + 点击反馈）
        btn_row = QHBoxLayout()
        self._save_btn = QPushButton(tr("ui.memory.save_config"))
        self._save_btn.setStyleSheet(_BTN_ORANGE)
        self._save_btn.clicked.connect(self._save_search_config)
        self._rebuild_btn = QPushButton(tr("ui.memory.rebuild_index"))
        self._rebuild_btn.setStyleSheet(_BTN_ORANGE)
        self._rebuild_btn.clicked.connect(self._rebuild_index)
        btn_row.addWidget(self._save_btn)
        btn_row.addWidget(self._rebuild_btn)
        btn_row.addStretch()
        lo.addLayout(btn_row)

        hint = QLabel(tr("ui.memory.rebuild_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{_TEXT_HINT}; font-size:11px;")
        lo.addWidget(hint)

        # 初始渲染依赖区
        self._render_dep_area(self._search_combo.currentText())

        lo.addStretch()
        return page

    def _render_dep_area(self, provider: str):
        """按 provider 动态渲染依赖区（key 输入/自托管/credential/插件引导）。"""
        # 「清空 → 重建」压在同一个重绘里做完：否则可见区先塌成空再长回来 = 一次闪。
        holder = self._dep_area.parentWidget()
        if holder is not None:
            holder.setUpdatesEnabled(False)
        # 清空旧依赖区
        while self._dep_area.count():
            item = self._dep_area.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        info = self._provider_info(provider)
        dep_type = info.get("dep_type", "none")

        if dep_type == "key":
            row = QHBoxLayout()
            lk = QLabel(tr("ui.memory.api_key"))
            lk.setFixedWidth(91)
            lk.setStyleSheet(f"color:{_TEXT_PRIMARY}; background:transparent;")
            self._dep_key = QLineEdit()
            self._dep_key.setEchoMode(QLineEdit.EchoMode.Password)
            self._dep_key.setStyleSheet(f"background:#222; color:{_TEXT_PRIMARY}; border:1px solid #444; border-radius:4px; padding:4px 8px;")
            row.addWidget(lk)
            row.addWidget(self._dep_key, stretch=1)
            self._dep_area.addLayout(row)
        elif dep_type == "selfhosted":
            row = QHBoxLayout()
            lb = QLabel(tr("ui.memory.service_url"))
            lb.setFixedWidth(91)
            lb.setStyleSheet(f"color:{_TEXT_PRIMARY}; background:transparent;")
            self._dep_baseurl = QLineEdit()
            self._dep_baseurl.setStyleSheet(f"background:#222; color:{_TEXT_PRIMARY}; border:1px solid #444; border-radius:4px; padding:4px 8px;")
            row.addWidget(lb)
            row.addWidget(self._dep_baseurl, stretch=1)
            self._dep_area.addLayout(row)
        elif dep_type == "plugin":
            tip = QLabel(tr("ui.memory.llama_install_hint"))
            tip.setWordWrap(True)
            tip.setStyleSheet(f"color:{STATUS['yellow']}; font-size:11px;")
            self._dep_area.addWidget(tip)
            self._install_btn = QPushButton(tr("ui.memory.install_llama"))
            self._install_btn.setStyleSheet(_BTN_ORANGE)
            self._install_btn.setFixedWidth(136)  # 只够完整显示「安装 llama-cpp 插件」
            self._install_btn.clicked.connect(self._install_llama_cpp)
            self._dep_area.addWidget(self._install_btn)
        elif dep_type == "credential":
            tip = QLabel(tr("ui.memory.credential_hint"))
            tip.setWordWrap(True)
            tip.setStyleSheet(f"color:{_TEXT_HINT}; font-size:11px;")
            self._dep_area.addWidget(tip)

        if holder is not None:
            holder.setUpdatesEnabled(True)   # 依赖区整块建好再放行重绘

    def _provider_info(self, provider: str) -> dict:
        """从 mani 的 provider 清单查 provider 的依赖类型。

        依赖类型由官方 Notes 列的信号词判定（数据驱动，不硬编 provider 名单）：
          - auto-download        → plugin（需装插件下载模型）
          - self-hosted          → selfhosted（自托管 baseUrl）
          - credential chain / subscription → credential（系统凭据/订阅，无 key）
          - needs_key yes/usually → key（需 API Key）
        """
        for p in _memory_providers():
            if p.get("id") != provider:
                continue
            notes = (p.get("notes") or "").lower()
            needs = (p.get("needs_key") or "").lower()
            if "auto-download" in notes:
                return {"dep_type": "plugin"}
            if "self-hosted" in notes:
                return {"dep_type": "selfhosted"}
            if "credential chain" in notes or "subscription" in notes:
                return {"dep_type": "credential"}
            if needs in ("yes", "usually"):
                return {"dep_type": "key"}
            return {"dep_type": "none"}
        return {"dep_type": "none"}

    def _on_provider_changed(self, provider: str):
        self._render_dep_area(provider)

    def _save_search_config(self):
        cfg = _read_config()
        _set_config_path(cfg, "memory.search.provider", self._search_combo.currentText())
        model_key = self._search_model.currentData()
        if model_key:
            _set_config_path(cfg, "memory.search.model", model_key)
        if self._search_maxresults is not None:
            _set_config_path(cfg, "memory.search.query.maxResults", self._search_maxresults.value())
        if self._search_minscore is not None:
            _set_config_path(cfg, "memory.search.query.minScore", self._search_minscore.value())
        # 依赖区：key / baseurl（若当前 provider 需要，且填了）
        if getattr(self, "_dep_key", None) and self._dep_key.text().strip():
            _set_config_path(cfg, "memory.search.apiKey", self._dep_key.text().strip())
        if getattr(self, "_dep_baseurl", None) and self._dep_baseurl.text().strip():
            _set_config_path(cfg, "memory.search.remote.baseUrl", self._dep_baseurl.text().strip())
        ok = _write_config(cfg)
        # 点击反馈：保存快，直接改按钮文字提示
        self._save_btn.setText(tr("ui.memory.saved") if ok else tr("ui.memory.save_failed"))
        _stake("search.save", f"provider={self._search_combo.currentText()} ok={ok}", ok=ok)

    def _rebuild_index(self):
        # 点击反馈：立刻禁用按钮 + 改文字，避免「点没点上」的困惑
        self._rebuild_btn.setEnabled(False)
        self._rebuild_btn.setText(tr("ui.memory.rebuilding"))
        from main.main import cli_usage
        memory_index = cli_usage("memory", "index") or "openclaw memory index"
        cmd = ('wsl -d {d} -u root -- bash -c "export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/sbin:/bin; '
               '{cmd} --force 2>&1"').format(d=WSL_DISTRO, cmd=memory_index)
        self._async_worker = _AsyncCmd(cmd, parent=self)
        self._async_worker.done.connect(self._on_rebuild_done)
        self._async_worker.start()
        _stake("search.rebuild", "触发重建索引（异步）", ok=True)

    def _on_rebuild_done(self, rc: int, out: str, err: str):
        self._rebuild_btn.setEnabled(True)
        ok = rc == 0
        self._rebuild_btn.setText(tr("ui.memory.rebuild_done") if ok else tr("ui.memory.rebuild_failed"))
        _stake("search.rebuild", f"rc={rc} out={out[:120]!r} err={err[:120]!r}", ok=ok)
        if not ok:
            # 原来失败只在按钮上换个词、stdout/stderr 全不展示，用户看不到原因。
            QMessageBox.warning(self, tr("ui.memory.rebuild_failed"), ((out + err).strip()[-400:] or f"rc={rc}"))

    def _install_llama_cpp(self):
        # 点击反馈：立刻禁用按钮 + 改文字，后台装，不假死
        self._install_btn.setEnabled(False)
        self._install_btn.setText(tr("ui.memory.installing"))
        from main.main import cli_usage
        plugins_install = cli_usage("plugins", "install") or "openclaw plugins install"
        cmd = ('wsl -d {d} -u root -- bash -c "export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/sbin:/bin; '
               '{cmd} @openclaw/llama-cpp-provider --accept-capabilities 2>&1"').format(d=WSL_DISTRO, cmd=plugins_install)
        self._async_worker = _AsyncCmd(cmd, parent=self)
        self._async_worker.done.connect(self._on_install_done)
        self._async_worker.start()
        _stake("search.install_plugin", "安装 llama-cpp（异步）", ok=True)

    def _on_install_done(self, rc: int, out: str, err: str):
        self._install_btn.setEnabled(True)
        ok = rc == 0 or "already" in (out + err).lower()
        self._install_btn.setText(tr("ui.memory.install_done") if ok else tr("ui.memory.install_failed"))
        _stake("search.install_plugin", f"rc={rc} {out[:80]}", ok=ok)

    def _extract_providers(self) -> list[str]:
        """从 mani 的 memory providers 长出来（不硬编 provider 清单）。"""
        providers = _memory_providers()
        ids = [p.get("id", "") for p in providers if p.get("id")]
        # 去重保序
        seen = set()
        out = []
        for pid in ids:
            if pid not in seen:
                seen.add(pid)
                out.append(pid)
        return out or []

    def _build_content_page(self) -> QWidget:
        """me0005：记忆内容（卡上的区）查看 —— 只读，从卡侧读。"""
        page = QWidget()
        lo = QVBoxLayout(page)
        lo.setContentsMargins(16, 16, 16, 16)
        lo.setSpacing(10)

        title = QLabel(tr("ui.memory.content.title"))
        title.setStyleSheet(f"color:{_TEXT_PRIMARY}; font-size:16px; font-weight:bold; background:transparent;")
        lo.addWidget(title)

        # 记忆内容由**卡**持有：这里列**卡上的区**（mem＝记忆 / user＝他的档案 /
        #   expertise＝知识库 / relat＝共同经历 / corpus＝语料），逐区从卡侧读（只读）。
        # ⚠ 2026-10-17 审计〔旧设计·该删〕：原来这里按 mani 的 tier 清单列**宿主工作区
        #   的文件**，还按写死的层名集合 {"Review"} 决定只读/可编辑 —— 可编辑那一路会写进
        #   宿主 agent 工作区。那条路径已删：记忆内容不在壳里直读/写别人的工作区。
        # 区名是**卡的结构**，不是客户数据 ⇒ 不随 mani 变。
        card_path = _resolve_card_path()
        hint = QLabel(card_path or tr("ui.persona.m13"))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{_TEXT_HINT}; font-size:11px;")
        lo.addWidget(hint)

        for zone in _CARD_ZONES:
            # 分组之间的分隔线
            if lo.count() > 2:
                sep = QLabel()
                sep.setFixedHeight(1)
                sep.setStyleSheet(f"background:#333; margin-top:6px;")
                lo.addWidget(sep)
            row = QHBoxLayout()
            row.setSpacing(0)
            zl = QLabel(zone)
            zl.setStyleSheet(f"color:{_TEXT_PRIMARY}; background:transparent;")
            row.addWidget(zl)
            # 虚线引导：区名与操作按钮之间的点线，一眼看清对应关系
            dots = QLabel()
            dots.setStyleSheet(
                f"color:{TEXT['faint']}; background:transparent; "
                f"border-bottom:1px dotted #555;"
            )
            dots.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            row.addWidget(dots, stretch=1)
            btn = QPushButton(tr("ui.btn.view"))
            btn.setFixedWidth(64)
            fit_button_text(btn, width=64)
            btn.setFixedSize(60, 26)
            btn.setStyleSheet(f"background:{ACCENT}; color:#0a0a0a; border:none; border-radius:4px;")
            btn.clicked.connect(lambda checked, z=zone: self._open_zone(z))
            row.addWidget(btn)
            lo.addLayout(row)

        lo.addStretch()
        return page

    def _open_zone(self, zone: str):
        """查看**卡上**的一个区（只读）。

        ⚠ 2026-10-17 审计〔旧设计·该删〕：原来这里直接 cat 宿主 agent 工作区的 tier
        文件（~/.openclaw/workspace/<rel>）—— 绕过卡/落盘账直读别人的工作区，顺手还
        能写回。那条路径已删：记忆内容由卡持有，这里只从**卡侧**读，不写卡。
        """
        card_path = _resolve_card_path()
        _body = _read_card_zone(card_path, zone)[1]
        dlg = QDialog(self)
        dlg.setWindowTitle(f"{tr('ui.memory.content.title')} · {zone}")
        dlg.resize(600, 400)
        lay = QVBoxLayout(dlg)
        te = QTextEdit()
        te.setReadOnly(True)
        te.setPlainText(f"{zone} · {card_path or tr('ui.persona.none')}\n\n{_body}")
        te.setStyleSheet(f"background:#222; color:{_TEXT_PRIMARY};")
        lay.addWidget(te)
        dlg.exec()

    # ⚠ 2026-10-17 审计〔旧设计·该删〕：_save_file（mkdir -p + cp 把内容写进宿主
    #   agent 工作区 ~/.openclaw/workspace/<rel>）已删除 —— 那是「往宿主工作区落地」
    #   的遗产路径。记忆内容由卡持有，不在壳里直写别人的工作区。

    def _build_backup_page(self) -> QWidget:
        """me0006：记忆备份恢复（针对 workspace 记忆文件，不是 db）。

        覆盖备份 = 走官方 openclaw backup create（全量 tar.gz + manifest）
        增量备份 = 自己 tar 打包 memory/ 每日记忆
        恢复 = 客户选 tar.gz，交 OpenClaw 校验/恢复
        """
        page = QWidget()
        lo = QVBoxLayout(page)
        lo.setContentsMargins(16, 16, 16, 16)
        lo.setSpacing(10)

        title = QLabel(tr("ui.memory.backup.title"))
        title.setStyleSheet(f"color:{_TEXT_PRIMARY}; font-size:16px; font-weight:bold; background:transparent;")
        lo.addWidget(title)

        desc = QLabel(tr("ui.memory.backup.desc"))
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color:{_TEXT_HINT}; font-size:12px;")
        lo.addWidget(desc)

        # 备份区
        sec1 = QLabel(tr("ui.memory.backup.section"))
        sec1.setStyleSheet(f"color:{ACCENT}; font-size:13px; font-weight:bold; background:transparent;")
        lo.addWidget(sec1)

        # 覆盖备份 + 增量备份 并排
        backup_row = QHBoxLayout()
        self._full_backup_btn = QPushButton(tr("ui.memory.backup.full"))
        self._full_backup_btn.setStyleSheet(_BTN_ORANGE)
        self._full_backup_btn.clicked.connect(self._full_backup)
        # ⚠ 2026-10-17 审计〔旧设计·该删〕：增量备份按钮已删 —— 那是壳自己 tar 打
        #   宿主工作区 memory/ 的私有口径（官方没有这套），路径也直指别人的工作区。
        backup_row.addWidget(self._full_backup_btn)
        backup_row.addStretch()
        lo.addLayout(backup_row)

        # 恢复区
        sec2 = QLabel(tr("ui.memory.restore"))
        sec2.setStyleSheet(f"color:{ACCENT}; font-size:13px; font-weight:bold; background:transparent; margin-top:8px;")
        lo.addWidget(sec2)

        self._restore_btn = QPushButton(tr("ui.memory.restore_choose"))
        self._restore_btn.setStyleSheet(_BTN_ORANGE)
        self._restore_btn.clicked.connect(self._restore_backup)
        lo.addWidget(self._restore_btn, alignment=Qt.AlignmentFlag.AlignLeft)

        hint = QLabel(tr("ui.memory.backup.hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{_TEXT_HINT}; font-size:11px;")
        lo.addWidget(hint)

        lo.addStretch()
        return page

    def _full_backup(self):
        """覆盖备份：走官方 openclaw backup create，输出到客户选目录。"""
        from PySide6.QtWidgets import QFileDialog as _QFD
        out_dir = _QFD.getExistingDirectory(self, tr("ui.memory.backup.choose_dir"))
        if not out_dir:
            return
        self._full_backup_btn.setEnabled(False)
        self._full_backup_btn.setText(tr("ui.memory.backing_up"))
        wsl_dir = _win_to_wsl(out_dir)
        from main.main import cli_usage
        backup_create = cli_usage("backup", "create") or "openclaw backup create"
        # 输出目录包成单引号字面量：目录含空格/中文时不再把命令拆坏。
        out_q = "'" + wsl_dir.replace("'", "'\\''") + "'"
        cmd = ('wsl -d {d} -u root -- bash -c "export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/sbin:/bin; '
               '{cmd} --output {out} 2>&1"').format(d=WSL_DISTRO, cmd=backup_create, out=out_q)
        self._async_worker = _AsyncCmd(cmd, parent=self)
        self._async_worker.done.connect(self._on_full_backup_done)
        self._async_worker.start()
        _stake("backup.full", f"覆盖备份 → {out_dir}", ok=True)

    def _on_full_backup_done(self, rc: int, out: str, err: str):
        self._full_backup_btn.setEnabled(True)
        ok = rc == 0
        self._full_backup_btn.setText(tr("ui.memory.backup_done") if ok else tr("ui.memory.backup_failed"))
        _stake("backup.full", f"rc={rc} out={out[:120]!r} err={err[:120]!r}", ok=ok)
        if not ok:
            # 失败不能只换按钮文字：把原因摆出来。
            QMessageBox.warning(self, tr("ui.memory.backup_failed"), ((out + err).strip()[-400:] or f"rc={rc}"))

    # ⚠ 2026-10-17 审计〔旧设计·该删〕：_incr_backup / _on_incr_backup_done 已删除 ——
    #   「自己 tar 打包 ~/.openclaw/workspace/memory」是官方没有的私有备份口径，
    #   而且路径直指宿主 agent 工作区。备份统一走官方 backup create（上面的覆盖备份）。

    def _restore_backup(self):
        """恢复：客户选 tar.gz，交 OpenClaw 校验并引导恢复。"""
        from PySide6.QtWidgets import QFileDialog as _QFD
        src, _ = _QFD.getOpenFileName(self, tr("ui.memory.backup.choose_file"), "",
                                      tr("ui.memory.backup.file_filter"))
        if not src:
            return
        self._restore_btn.setEnabled(False)
        self._restore_btn.setText(tr("ui.memory.restoring"))
        wsl_src = _win_to_wsl(src)
        # 先 verify 校验归档，再提示客户按官方流程恢复（官方无 restore 命令）
        from main.main import cli_usage
        backup_verify = cli_usage("backup", "verify") or "openclaw backup verify"
        # 归档路径包成单引号字面量（含空格/中文不再拆坏命令）。
        src_q = "'" + wsl_src.replace("'", "'\\''") + "'"
        cmd = ('wsl -d {d} -u root -- bash -c "export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/sbin:/bin; '
               '{cmd} {src} 2>&1"').format(d=WSL_DISTRO, cmd=backup_verify, src=src_q)
        self._async_worker = _AsyncCmd(cmd, parent=self)
        self._async_worker.done.connect(self._on_restore_done)
        self._async_worker.start()
        _stake("backup.restore", f"校验恢复归档 → {src}", ok=True)

    def _on_restore_done(self, rc: int, out: str, err: str):
        self._restore_btn.setEnabled(True)
        ok = rc == 0
        self._restore_btn.setText(tr("ui.memory.verify_done") if ok else tr("ui.memory.verify_failed"))
        if ok:
            QMessageBox.information(self, tr("ui.memory.verify_ok"), tr("ui.memory.verify_ok_msg"))
        else:
            QMessageBox.warning(self, tr("ui.memory.verify_failed"), (out + err)[:300])
        _stake("backup.restore", f"rc={rc} {out[:100]}", ok=ok)


def _win_to_wsl(win_path: str) -> str:
    """Windows 路径 → WSL /mnt/ 路径。"""
    if len(win_path) < 2:
        return win_path
    if win_path[1] == ":":
        return "/mnt/" + win_path[0].lower() + win_path[2:].replace("\\", "/")
    return win_path.replace("\\", "/")
