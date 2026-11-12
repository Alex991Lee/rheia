# L1-453：随壳本地插件注入（装机 / 自愈链路调用）。
r"""随壳本地插件注入（装机 / 自愈链路调用）。"""



















import hashlib
import json
import os
import shutil
import stat
import sys

PLUGIN_ID = "persona-slot-engine"


# L33-53：_app_root_from — 从调用参数推断安装根（app root）。
def _app_root_from(arg: str) -> str:
    r"""从调用参数推断安装根（app root）。"""








    if not arg:
        return ""
    p = os.path.abspath(arg)
    for _ in range(4):
        if os.path.isfile(os.path.join(p, "installer", "inject_local_plugins.py")):
            return p
        parent = os.path.dirname(p)
        if not parent or parent == p:
            break
        p = parent
    return ""


# L57-92：_load_declarations — 读消费端声明：装什么插件、入口在哪、装法是什么 —— 不再硬编插件 id。
def _load_declarations(app_root: str) -> tuple[list[dict], str]:
    r"""读消费端声明：装什么插件、入口在哪、装法是什么 —— 不再硬编插件 id。"""









    mani = os.path.join(app_root, "kernel", "large", "mani.json") if app_root else ""
    if mani and os.path.isfile(mani):
        try:
            with open(mani, encoding="utf-8") as f:
                data = json.load(f)
            cons = [d for d in ((data.get("entries") or {}).get("custom_demands") or [])
                    if isinstance(d, dict) and str(d.get("type") or "") == "consumer"]
            if cons:
                return cons, f"mani（运行时声明 {mani}）"
        except Exception as e:
            print("inject_local_plugins: 读 mani 失败（改用随壳声明）->", e)

    shipped = os.path.join(app_root, "installer", "slot_consumers.json") if app_root else ""
    if shipped and os.path.isfile(shipped):
        try:
            with open(shipped, encoding="utf-8") as f:
                data = json.load(f)
            cons = [d for d in (data.get("consumers") or []) if isinstance(d, dict)]
            if cons:
                return cons, f"随壳声明（{shipped}）"
        except Exception as e:
            print("inject_local_plugins: 读随壳声明失败（改用内置兜底）->", e)


    return [], "（没有声明：mani 里没有、随壳声明也没有）"



# L97-101：_config_path — _config_path
def _config_path() -> str:
    env = os.environ.get("OPENCLAW_CONFIG")
    if env:
        return env
    return os.path.join(os.path.expanduser("~"), ".openclaw", "openclaw.json")


# L105-109：_stage_root — _stage_root
def _stage_root() -> str:
    env = os.environ.get("OPENCLAW_PLUGIN_STAGE")
    if env:
        return env
    return os.path.join(os.path.expanduser("~"), ".openclaw", "plugins")


# L113-123：_tighten — 目录 0755、文件 0644（去掉全局可写位）。
def _tighten(root: str) -> None:
    r"""目录 0755、文件 0644（去掉全局可写位）。"""
    for dirpath, dirnames, filenames in os.walk(root):
        os.chmod(dirpath, 0o755)
        for name in filenames:
            p = os.path.join(dirpath, name)
            if os.path.islink(p):
                continue
            mode = os.stat(p).st_mode
            os.chmod(p, 0o755 if mode & stat.S_IXUSR else 0o644)
    os.chmod(root, 0o755)


# L127-146：_digest — 源目录的内容指纹（逐文件 sha256，按相对路径排序）。用来判断"要不要重拷"。
def _digest(root: str) -> str:
    r"""源目录的内容指纹（逐文件 sha256，按相对路径排序）。用来判断"要不要重拷"。"""





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


# L150-172：_stage_plugin — 把随壳插件落到 Linux 侧（缺失或内容变了才拷；每次都顺手收紧权限）。
def _stage_plugin(src: str, pid: str = PLUGIN_ID):
    r"""把随壳插件落到 Linux 侧（缺失或内容变了才拷；每次都顺手收紧权限）。"""






    dst = os.path.join(_stage_root(), pid)
    try:
        os.makedirs(_stage_root(), exist_ok=True)
        want = _digest(src)
        if os.path.isdir(dst) and _digest(dst) == want:
            _tighten(dst)
            return dst, False
        if os.path.isdir(dst):
            shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst, symlinks=False)
        _tighten(dst)
    except Exception as e:
        print("inject_local_plugins: 拷贝失败 ->", e)
        return "", False
    return dst, True


# L176-180：_world_writable — _world_writable
def _world_writable(path: str) -> bool:
    try:
        return bool(os.stat(path).st_mode & stat.S_IWOTH)
    except OSError:
        return False


# L184-217：_plugin_src — 找到这个消费端的随壳源目录（并按清单里的 id 验明正身）。
def _plugin_src(app_root: str, pid: str, decl: dict, legacy_dir: str) -> str:
    r"""找到这个消费端的随壳源目录（并按清单里的 id 验明正身）。"""










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
        except Exception:
            mid = ""
        if mid == pid:
            return c
    return ""


# L221-247：_runtime_spec_for — 读壳递出来的运行规格（~/.openclaw/slot_runtime.json），取这个插件那一份。
def _runtime_spec_for(pid: str) -> dict | None:
    r"""读壳递出来的运行规格（~/.openclaw/slot_runtime.json），取这个插件那一份。"""




    p = os.path.join(os.path.expanduser("~"), ".openclaw", "slot_runtime.json")
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    plugins = data.get("plugins") if isinstance(data, dict) else None



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


# L251-384：_register_one — 把某个消费端的落点登记进配置，并把运行规格写进插件配置。
def _register_one(pid: str, target: str, copied: bool, spec: dict | None = None) -> int:
    r"""把某个消费端的落点登记进配置，并把运行规格写进插件配置。"""














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
    changed = bool(copied)


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


    entries = plugins.setdefault("entries", {})
    entry = entries.setdefault(pid, {})
    if entry.get("enabled") is not True:
        entry["enabled"] = True
        changed = True


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

        try:
            with open(cfg, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print("inject_local_plugins: 写配置失败 ->", e)
            return 2
        print(f"inject_local_plugins: {pid} 仅规格变更（热重载即可，无需重启）")
        return 20


    try:
        with open(cfg, "r", encoding="utf-8") as f:
            _orig = f.read()
    except Exception:
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
            except Exception as e2:
                print("inject_local_plugins: 回写原文也失败 ->", e2)
        return 2

    print(f"inject_local_plugins: 已登记 {pid} -> {target}")
    return 10


# L388-449：main — 按声明办事：声明里有几条消费端，就把几条装到位。
def main() -> int:
    r"""按声明办事：声明里有几条消费端，就把几条装到位。"""








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


        if one == 2:
            rc = 2
        elif one == 10 and rc != 2:
            rc = 10
        elif one == 20 and rc not in (2, 10):
            rc = 20
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
