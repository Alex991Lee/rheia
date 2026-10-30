"""随壳本地插件注入（装机 / 自愈链路调用）。

把**随壳分发**的本地插件（persona-slot-engine）登记进 WSL 侧 Gateway 的
`plugins.load.paths` —— **装壳即到位**，不依赖外部源、不需要手工部署，
也根治「开发目录与部署副本各走各的、改名改一半」这类漂移。

用法（在 WSL 侧执行）：
    python3 <app>/installer/inject_local_plugins.py <plugin_dir_wsl_path>

为什么要先从 /mnt/c 拷一份到 Linux 侧再登记：
    官方加载器在发现阶段会拒掉**路径（或其根目录）全局可写**的插件候选
    （见官方文档 `plugins/architecture-internals/load-pipeline.md`：
    "Discovery blocks a candidate when ... its path (or its root directory)
    is world-writable"）。`/mnt/c`（DrvFs）在 WSL 里一律显示成 0777，
    于是随壳插件会被判成 world-writable 而**整个不加载**。
    拷到 Linux 侧（ext4）后按 0755 收紧，才是官方认可的落点；
    顺带它也符合"非捆绑插件属主必须是 root 或当前 uid"这一条。

幂等：每次开机重新拷一份（随壳走、不漂移），配置里已是该路径则不重复写。
"""
import hashlib
import json
import os
import shutil
import stat
import sys

PLUGIN_ID = "persona-slot-engine"          # 内置兜底（声明都读不到时才用）


def _app_root_from(arg: str) -> str:
    """从调用参数推断**安装根**（app root）。

    ⚠ 必须**逐级上溯**找，不能只看上一级（2026-09-21 实测）：
      自愈/开机那条调用传的是 `<app>/persona/scripts`（插件源目录），
      只看上一级会把 app root 认成 `<app>/persona` ⇒ 找不到随壳声明
      （`<app>/installer/slot_consumers.json`）而退回内置兜底 —— 自愈链就被削弱了。

    认根判据：目录里有 `installer/inject_local_plugins.py`（也就是本脚本自己）。
    """
    if not arg:
        return ""
    p = os.path.abspath(arg)
    for _ in range(4):                       # 传进来的可能是插件源目录，往上最多找 4 级
        if os.path.isfile(os.path.join(p, "installer", "inject_local_plugins.py")):
            return p
        parent = os.path.dirname(p)
        if not parent or parent == p:
            break
        p = parent
    return ""


def _load_declarations(app_root: str) -> tuple[list[dict], str]:
    """读**消费端声明**：装什么插件、入口在哪、装法是什么 —— 不再硬编插件 id。

    优先级（谁更权威谁在前）：
      ① mani.json 里的消费端声明（`entries.custom_demands` type=consumer）——
         运行时声明，带与官方机制面的对账结论（`satisfied`/`declaration`）；
      ② 随壳声明 `installer/slot_consumers.json`——装机时就在，兜住"还没跑过采集"；
      ③ 内置兜底（今天这一条），保证任何情况下都装得上。

    返回 (声明列表, 来源说明)。空列表 = 没有任何声明可办（调用方显式报）。
    """
    mani = os.path.join(app_root, "kernel", "large", "mani.json") if app_root else ""
    if mani and os.path.isfile(mani):
        try:
            with open(mani, encoding="utf-8") as f:
                data = json.load(f)
            cons = [d for d in ((data.get("entries") or {}).get("custom_demands") or [])
                    if isinstance(d, dict) and str(d.get("type") or "") == "consumer"]
            if cons:
                return cons, f"mani（运行时声明 {mani}）"
        except Exception as e:                                    # noqa: BLE001
            print("inject_local_plugins: 读 mani 失败（改用随壳声明）->", e)

    shipped = os.path.join(app_root, "installer", "slot_consumers.json") if app_root else ""
    if shipped and os.path.isfile(shipped):
        try:
            with open(shipped, encoding="utf-8") as f:
                data = json.load(f)
            cons = [d for d in (data.get("consumers") or []) if isinstance(d, dict)]
            if cons:
                return cons, f"随壳声明（{shipped}）"
        except Exception as e:                                    # noqa: BLE001
            print("inject_local_plugins: 读随壳声明失败（改用内置兜底）->", e)

    # 没有声明 ⇒ 什么都不做：如实报、退 2，不写任何东西。
    return [], "（没有声明：mani 里没有、随壳声明也没有）"



def _config_path() -> str:
    env = os.environ.get("OPENCLAW_CONFIG")
    if env:
        return env
    return os.path.join(os.path.expanduser("~"), ".openclaw", "openclaw.json")


def _stage_root() -> str:
    env = os.environ.get("OPENCLAW_PLUGIN_STAGE")
    if env:
        return env
    return os.path.join(os.path.expanduser("~"), ".openclaw", "plugins")


def _tighten(root: str) -> None:
    """目录 0755、文件 0644（去掉全局可写位）。"""
    for dirpath, dirnames, filenames in os.walk(root):
        os.chmod(dirpath, 0o755)
        for name in filenames:
            p = os.path.join(dirpath, name)
            if os.path.islink(p):
                continue
            mode = os.stat(p).st_mode
            os.chmod(p, 0o755 if mode & stat.S_IXUSR else 0o644)
    os.chmod(root, 0o755)


def _digest(root: str) -> str:
    """源目录的内容指纹（逐文件 sha256，按相对路径排序）。用来判断"要不要重拷"。

    落点是 Linux 原生盘的持久目录，初装拷一次就在那儿了，不会自己丢；
    所以只在**缺失**或**随壳那份变了**（升级换代）时才重拷，平时开机不白动它 ——
    反复重写插件目录会让官方那边报 "plugin registry 与磁盘发现不一致"。
    """
    h = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            p = os.path.join(dirpath, name)
            if os.path.islink(p) or not os.path.isfile(p):
                continue
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            h.update(rel.encode("utf-8"))
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 16), b""):
                    h.update(chunk)
    return h.hexdigest()


def _stage_plugin(src: str, pid: str = PLUGIN_ID):
    """把随壳插件落到 Linux 侧（缺失或内容变了才拷；每次都顺手收紧权限）。

    `pid` 来自**声明**（spec.plugin），不再硬编 —— 声明里加一条消费端，
    这里就多落一份。返回 (落点, 这次是否真拷了)；失败返回 ("", False)。

    用目录指纹（_digest(src) vs _digest(dst)）判断是否需要重拷，不落标记文件。
    """
    dst = os.path.join(_stage_root(), pid)
    try:
        os.makedirs(_stage_root(), exist_ok=True)
        want = _digest(src)
        if os.path.isdir(dst) and _digest(dst) == want:
            _tighten(dst)                      # 内容没变：只保证权限，不动文件
            return dst, False
        if os.path.isdir(dst):
            shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst, symlinks=False)
        _tighten(dst)
    except Exception as e:
        print("inject_local_plugins: 拷贝失败 ->", e)
        return "", False
    return dst, True


def _world_writable(path: str) -> bool:
    try:
        return bool(os.stat(path).st_mode & stat.S_IWOTH)
    except OSError:
        return False


def _plugin_src(app_root: str, pid: str, decl: dict, legacy_dir: str) -> str:
    """找到这个消费端的**随壳源目录**（并按清单里的 id 验明正身）。

    候选顺序（谁先命中用谁）：
      ① 声明里的 `source`（数据驱动，推荐：随壳布局改了只改声明）
      ② `<app>/<插件 id>`（老布局）
      ③ 调用方直接传进来的那个目录（生产就是 `<app>/persona/scripts`）

    ⚠ 每个候选都必须**目录里有 openclaw.plugin.json，且清单里的 id 就是 pid** ——
      2026-09-21 血泪：布局从 `<app>/persona-slot-engine` 挪到 `<app>/persona/scripts` 后，
      按老布局找会"找不到 ⇒ 跳过"，插件就永远不进网关（看着像环境问题，其实是路径假设过期）。
    """
    cands: list[str] = []
    src = str(decl.get("source") or "").strip()
    if src and app_root:
        cands.append(os.path.join(app_root, src))
    if app_root:
        cands.append(os.path.join(app_root, pid))
    if legacy_dir:
        cands.append(legacy_dir)
    for c in cands:
        if not c or not os.path.isdir(c):
            continue
        man = os.path.join(c, "openclaw.plugin.json")
        if not os.path.isfile(man):
            continue
        try:
            with open(man, encoding="utf-8") as f:
                mid = str((json.load(f) or {}).get("id") or "")
        except Exception:                                        # noqa: BLE001
            mid = ""
        if mid == pid:
            return c
    return ""


def _runtime_spec_for(pid: str) -> dict | None:
    """读壳递出来的**运行规格**（`~/.openclaw/slot_runtime.json`），取这个插件那一份。

    壳（Rheia）把"声明 → 规格"编译好放这儿；本脚本负责把它写进插件配置
    （官方热重载通道）。文件不存在/没有这个插件 → None（调用方照常走登记）。
    """
    p = os.path.join(os.path.expanduser("~"), ".openclaw", "slot_runtime.json")
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    plugins = data.get("plugins") if isinstance(data, dict) else None
    # **接线事实（paths）是文件顶层的**，不属于某个插件的规格切片 —— 取切片时要把它带上，
    # 否则 `_register_one` 里那段"paths 落进插件配置"永远看不到 paths（判据永远为假），
    # 表现为"规格未变，跳过"，插件拿不到 policy/指针落点 ⇒ 每轮 fail-closed 空转。
    _paths = data.get("paths") if isinstance(data, dict) else None
    if isinstance(plugins, dict) and isinstance(plugins.get(pid), dict):
        one = dict(plugins[pid])
        if isinstance(_paths, dict) and _paths:
            one["paths"] = _paths
        return one
    if isinstance(data, dict) and data.get("hooks"):
        return data
    if isinstance(_paths, dict) and _paths:
        return {"paths": _paths}
    return None


def _register_one(pid: str, target: str, copied: bool, spec: dict | None = None) -> int:
    """把某个消费端的落点登记进配置，并把**运行规格**写进插件配置。

    返回码（调用方据此决定"要不要重启"——重启是最后手段，不是常规）：
      0  = 没动配置
      10 = **代码/落点/登记变了** ⇒ 需要 `plugins reload`（不行才冷启动）
      20 = **只有运行规格（数据）变了** ⇒ 官方 hybrid reload 自动热生效，**不用重启**
      2  = 失败

    为什么要把规格写进 `plugins.entries.<id>.config`（2026-09-21）：
    官方默认 hybrid 热重载对**插件配置变更**是自动生效的（`plugins/hooks.md:154`、
    `plugins/manage-plugins.md:284`、`plugins/codex-harness-reference/supervision.md:88`
    明写 "no Gateway restart is needed"），而且改 config 会**替换插件实例并重跑
    register()** ⇒ 钩子按新规格重挂。于是"改声明 = 改插件行为"**零重启**，
    不必把可用性押在"重启命令能不能用"上。
    """
    cfg = _config_path()
    if not os.path.exists(cfg):
        print("inject_local_plugins: 无配置 ->", cfg)
        return 2
    if _world_writable(target):
        print("inject_local_plugins: 警告 —— 该路径全局可写，官方加载器会拒载 ->", target)
    if not os.path.exists(os.path.join(target, "openclaw.plugin.json")):
        print("inject_local_plugins: 不是插件目录（缺 openclaw.plugin.json）->", target)
        return 2
    try:
        with open(cfg, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print("inject_local_plugins: 读配置失败 ->", e)
        return 2

    plugins = data.setdefault("plugins", {})
    load = plugins.setdefault("load", {})
    paths = list(load.get("paths") or [])
    changed = bool(copied)      # **重拷过也算变**：插件字节换了，屋里跑的必须重启才吃到

    # 同一份插件的旧路径（含 /mnt/c 那份）先摘掉，防两份并存 / 漂移再来
    _target = os.path.normpath(target)
    stale = [p for p in paths
             if os.path.basename(os.path.normpath(str(p))) == pid
             and os.path.normpath(str(p)) != _target]
    if stale:
        paths = [p for p in paths if p not in stale]
        load["paths"] = paths
        changed = True

    if target not in paths:
        paths.append(target)
        load["paths"] = paths
        changed = True

    # 插件条目：确保 enabled（不覆盖其它字段）
    entries = plugins.setdefault("entries", {})
    entry = entries.setdefault(pid, {})
    if entry.get("enabled") is not True:
        entry["enabled"] = True
        changed = True

    # ── 运行规格 → 插件配置（官方热重载通道；只有它变 = 不用重启）──
    spec_changed = False
    if spec:
        ent_cfg = entry.setdefault("config", {})
        if not isinstance(ent_cfg, dict):
            ent_cfg = {}
            entry["config"] = ent_cfg
        if ent_cfg.get("runtimeSpec") != spec:
            ent_cfg["runtimeSpec"] = spec
            spec_changed = True
            print(f"inject_local_plugins: {pid} 运行规格已写进插件配置（官方 hybrid reload 自动生效）")
    # 接线事实（paths）走同一条通道：壳解析好的路径原样落进插件配置，插件只读配置。
    if isinstance(spec, dict) and isinstance(spec.get("paths"), dict) and spec["paths"]:
        ent_cfg2 = entry.setdefault("config", {})
        if not isinstance(ent_cfg2, dict):
            ent_cfg2 = {}
            entry["config"] = ent_cfg2
        if ent_cfg2.get("paths") != spec["paths"]:
            ent_cfg2["paths"] = spec["paths"]
            spec_changed = True
            print(f"inject_local_plugins: {pid} 接线路径已写进插件配置"
                  f"（{', '.join(sorted(spec['paths']))}）")

    # ── 会话钩子的访问旗（2026-09-22）──────────────────────────────
    #   `agent_end`（每轮结束那条 Observe 钩子）要看这一轮的最终消息 —— 属于官方说的
    #   **conversation access**。非随包插件不开这面旗会被直接拦掉（实测：mem9 的
    #   `agent_end` 就是这样被 blocked 的，日志原话 "non-bundled plugins must set
    #   plugins.entries.<id>.hooks.allowConversationAccess=true"）。卡侧的捕获/蒸馏
    #   全挂在 `agent_end` 上 ⇒ 这面旗不开，客户插了卡也永远是"只进语料、不蒸馏"。
    _ha = entry.setdefault("hooks", {})
    if not isinstance(_ha, dict):
        _ha = {}
        entry["hooks"] = _ha
    if _ha.get("allowConversationAccess") is not True:
        _ha["allowConversationAccess"] = True
        changed = True
        print(f"inject_local_plugins: {pid} 已开会话钩子访问旗（agent_end 才收得到内容）")

    if not changed and not spec_changed:
        print(f"inject_local_plugins: {pid} 已登记、规格未变，跳过 -> {target}")
        return 0
    if not changed and spec_changed:
        # 只写配置：官方保证热生效 ⇒ 不重启
        try:
            with open(cfg, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print("inject_local_plugins: 写配置失败 ->", e)
            return 2
        print(f"inject_local_plugins: {pid} 仅规格变更（热重载即可，无需重启）")
        return 20

    # 原文留在内存（_orig），写坏当场写回，不落 .bak 文件。
    try:
        with open(cfg, "r", encoding="utf-8") as f:
            _orig = f.read()
    except Exception:                                                 # noqa: BLE001
        _orig = ""

    try:
        with open(cfg, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("inject_local_plugins: 写配置失败 ->", e)
        if _orig:
            try:
                with open(cfg, "w", encoding="utf-8") as f:
                    f.write(_orig)
                print("inject_local_plugins: 已把原文写回（没落备份文件）")
            except Exception as e2:                                   # noqa: BLE001
                print("inject_local_plugins: 回写原文也失败 ->", e2)
        return 2

    print(f"inject_local_plugins: 已登记 {pid} -> {target}")
    return 10           # 10 = 登记结果变了 ⇒ 调用方应重启一次 Gateway 让它加载


def main() -> int:
    """**按声明办事**：声明里有几条消费端，就把几条装到位。

    声明来源见 `_load_declarations`（mani 运行时声明 → 随壳声明 → 内置兜底）。
    每条的装法也来自声明：
      · `satisfied.match == "external"` / 本地插件 → 从**随壳目录**（app_root/<插件 id>）
        拷到 Linux 侧并登记（老路子，只是 id 不再硬编）；
      · 声明里带**官方路由**（官方插件，install_route/package）→ 本机不拷，
        显式打印"交给 `openclaw plugins install`"，绝不假装装过。
    """
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    app_root = _app_root_from(arg)
    legacy_dir = arg if (arg and os.path.isdir(arg)
                         and os.path.isfile(os.path.join(arg, "openclaw.plugin.json"))) else ""
    if not app_root and not legacy_dir:
        print("inject_local_plugins: 既没有安装根、也没有插件目录 ->", arg)
        return 2

    decls, src_note = _load_declarations(app_root)
    print(f"inject_local_plugins: 声明来源 = {src_note}（{len(decls)} 条消费端）")
    if not decls:
        print("inject_local_plugins: 声明为空 ⇒ 没有要装的消费端（显式，不猜）")
        return 2

    rc = 0
    for d in decls:
        spec = d.get("spec") if isinstance(d.get("spec"), dict) else {}
        pid = str(spec.get("plugin") or d.get("id") or "").strip()
        if not pid:
            print("  · 声明缺 spec.plugin/id ⇒ 跳过（显式）")
            rc = max(rc, 2)
            continue
        sat = d.get("satisfied") if isinstance(d.get("satisfied"), dict) else {}
        route = str(sat.get("install_route") or "")
        official = str(sat.get("match") or "") != "external" and route and \
            not route.startswith("installer/")
        if official:
            pkg = str(sat.get("package") or "") or pid
            print(f"  · {pid}: 官方路由（{route} / {pkg}）⇒ 交给 openclaw plugins install，"
                  f"本机不拷（按声明办，不假装装过）")
            continue
        src_dir = _plugin_src(app_root, pid, d, legacy_dir)
        if not src_dir:
            print(f"  · {pid}: 找不到随壳源目录（找过 声明.source / {app_root}/{pid} / "
                  f"{legacy_dir or '(无参)'}，且要求清单 id 相符）⇒ 跳过（显式）")
            rc = max(rc, 2)
            continue
        staged, copied = _stage_plugin(src_dir, pid)
        target = staged or src_dir
        if staged:
            print(f"inject_local_plugins: {pid} "
                  + ("已拷到 Linux 侧 -> " if copied else "Linux 侧已有、内容未变 -> ") + staged)
        one = _register_one(pid, target, copied, _runtime_spec_for(pid))
        # 返回码有优先级：2（失败）> 10（要 reload/冷启动）> 20（仅规格变，热重载即可）
        # ⚠ 不能用 max()：max(10, 20) = 20 会把"代码换了要 reload"降级成"热重载就行"。
        if one == 2:
            rc = 2
        elif one == 10 and rc != 2:
            rc = 10
        elif one == 20 and rc not in (2, 10):
            rc = 20
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
