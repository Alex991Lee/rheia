#!/usr/bin/env python3
# L1-272：CODE-MAP.md 生成器（字节级）。
r"""CODE-MAP.md 生成器（字节级）。"""

















import ast, hashlib, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP = os.path.join(ROOT, 'CODE-MAP.md')
DIRS = ['extract', 'installer', 'kernel', 'lib', 'main', 'persona', 'platform', 'tools', 'ui']
BT = chr(96)


# L30-52：py_symbols — py_symbols
def py_symbols(data):
    src = data.decode('utf-8', 'replace')
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    lines = src.split(chr(10))
    offs = [0]
    for l in lines:
        offs.append(offs[-1] + len((l + chr(10)).encode('utf-8')))
    out = []

    def visit(node, prefix):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, ast.ClassDef):
                out.append((prefix + ch.name, offs[ch.lineno - 1], offs[ch.end_lineno], ch.lineno))
                visit(ch, prefix + ch.name + '.')
            elif isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.append((prefix + ch.name, offs[ch.lineno - 1], offs[ch.end_lineno], ch.lineno))

    visit(tree, '')
    out.sort(key=lambda x: x[1])
    return out


RE_FN = re.compile(r'^(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(')
RE_VAR = re.compile(r'^(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:function\b|\()')


# L60-86：js_symbols — js_symbols
def js_symbols(data):
    src = data.decode('utf-8', 'replace')
    lines = src.split(chr(10))
    offs = [0]
    for l in lines:
        offs.append(offs[-1] + len((l + chr(10)).encode('utf-8')))
    heads = []
    for i, l in enumerate(lines):
        m = RE_FN.match(l) or RE_VAR.match(l)
        if m:
            heads.append((m.group(1), i, offs[i]))
    out = []
    for name, i, start in heads:
        j, depth, started, end = i, 0, False, None
        while j < len(lines) and end is None:
            for k, c in enumerate(lines[j]):
                if c == '{':
                    depth += 1
                    started = True
                elif c == '}':
                    depth -= 1
                    if started and depth == 0:
                        end = offs[j] + len(lines[j][:k + 1].encode('utf-8'))
                        break
            j += 1
        out.append((name, start, end if end is not None else len(data), i + 1))
    return out


# L90-92：stats — stats
def stats(path):
    data = open(path, 'rb').read()
    return len(data), hashlib.sha256(data).hexdigest()[:16], data.count(b'\n') + 1, data


# L96-102：syms_for — syms_for
def syms_for(path, data):
    if path.endswith('.py'):
        s = py_symbols(data)
        return '(解析失败)' if s is None else s
    if path.endswith('.js'):
        return js_symbols(data)
    return []


# L106-114：cell_table1 — cell_table1
def cell_table1(syms):
    if not syms:
        return ''
    if syms == '(解析失败)':
        return syms
    items = [f'{n}@{a}\u2192{b}' for n, a, b, _ in syms]
    if len(items) > 40:
        return ', '.join(items[:40]) + f' \u2026+{len(items) - 40}'
    return ', '.join(items)


# L118-124：cell_table2 — cell_table2
def cell_table2(syms, is_js):
    if isinstance(syms, str) or not syms:
        return ''
    top = [s for s in syms if '.' not in s[0]][:14]
    if is_js:
        return ', '.join(n for n, a, b, ln in top)
    return ', '.join(f'{n}@{ln}' for n, a, b, ln in top)


RE_T1 = re.compile(r'^\| ([^|]+?) \| (\d+) \| ([0-9a-f]{16}) \| (\d+) \| (.*?)\s*\|$')
RE_T2 = re.compile(r'^\| ([^|]+?) \| (\d+) \| ([^|]*) \| ([^|]*) \| (.*?)\s*\|$')


# L132-143：_role_for — 给表二补新行时用的角色；已有行的手写角色不动。
def _role_for(path: str) -> str:
    r"""给表二补新行时用的角色；已有行的手写角色不动。"""
    ext = os.path.splitext(path)[1].lower()
    if ext == '.py':
        return 'Python'
    if ext == '.js':
        return 'Node/插件'
    if ext == '.json':
        return '配置'
    if ext in ('.md', '.txt'):
        return '文档'
    return '其他'


# L147-168：_scan_files — 地图的源码范围：DIRS 下全部实际文件 + 根 executor.py。
def _scan_files() -> set:
    r"""地图的源码范围：DIRS 下全部实际文件 + 根 executor.py。"""




    found = set()
    for d in DIRS:
        base = os.path.join(ROOT, d)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [x for x in dirnames if x != '__pycache__']
            for fn in filenames:
                if fn.endswith(('.pyc', '.pyo')):
                    continue
                rel = os.path.relpath(os.path.join(dirpath, fn), ROOT)
                found.add(rel.replace(os.sep, '/'))
    executor = os.path.join(ROOT, 'executor.py')
    if os.path.isfile(executor):
        found.add('executor.py')
    return found


# L172-268：main — main
def main():
    check = '--check' in sys.argv
    lines = open(MAP, encoding='utf-8').read().split(chr(10))
    t2 = next(i for i, l in enumerate(lines) if l.startswith('## 二、'))
    old_sha = {}
    t1_paths = set()
    t2_paths = set()
    for i, l in enumerate(lines):
        m = RE_T1.match(l)
        if m and i < t2 and m.group(1).strip() != '文件':
            p = m.group(1).strip()
            t1_paths.add(p)
            old_sha[p] = m.group(3)
        m2 = RE_T2.match(l)
        if m2 and i > t2 and m2.group(1).strip() != '文件':
            t2_paths.add(m2.group(1).strip())

    canonical = _scan_files()
    missing_t1 = sorted(canonical - t1_paths)
    missing_t2 = sorted(canonical - t2_paths)
    extra = sorted((t1_paths | t2_paths) - canonical)
    cache = {}
    for p in canonical:
        cache[p] = stats(os.path.join(ROOT, p))

    out, diffs = [], []
    for i, ln in enumerate(lines):
        m = RE_T1.match(ln)
        if m and i < t2 and m.group(1).strip() != '文件':
            p = m.group(1).strip()
            if p not in canonical:
                diffs.append((p, '表一删除'))
                continue
            b, h, l, data = cache[p]
            new = f'| {p} | {b} | {h} | {l} | {cell_table1(syms_for(p, data))} |'
            if new != ln:
                diffs.append((p, '表一'))
            out.append(new)
            continue
        m2 = RE_T2.match(ln)
        if m2 and i > t2 and m2.group(1).strip() != '文件':
            p = m2.group(1).strip()
            if p not in canonical:
                diffs.append((p, '表二删除'))
                continue
            b, h, l, data = cache[p]
            cell = m2.group(4).strip()
            if old_sha.get(p) != h:
                cell = cell_table2(syms_for(p, data), p.endswith('.js'))
            new = f'| {p} | {b} | {m2.group(3).strip()} | {cell} | {m2.group(5).strip()} |'
            if new != ln:
                diffs.append((p, '表二'))
            out.append(new)
            continue
        out.append(ln)


    for p in missing_t1:
        b, h, l, data = cache[p]
        out.insert(next(i for i, l in enumerate(out) if l.startswith('## 二、')),
                   f'| {p} | {b} | {h} | {l} | {cell_table1(syms_for(p, data))} |')
        diffs.append((p, '表一新增'))
    for p in missing_t2:
        b, h, l, data = cache[p]
        start = next(i for i, l in enumerate(out) if l.startswith('## 二、'))
        stop = next((i for i, l in enumerate(out[start + 1:], start + 1)
                     if l.startswith('## ')), len(out))
        out.insert(stop,
                   f'| {p} | {b} | {_role_for(p)} | {cell_table2(syms_for(p, data), p.endswith(".js"))} |  |')
        diffs.append((p, '表二新增'))

    text = chr(10).join(out)
    files = sorted(canonical)
    total = sum(cache[p][0] for p in files)
    text = re.sub(r'^- 文件 \d+ 个，合计 \d+ 字节$',
                  f'- 文件 {len(files)} 个，合计 {total} 字节', text, count=1, flags=re.M)
    for d in DIRS:
        ps = [p for p in files if p.startswith(d + '/')]
        text = re.sub(r'^- ' + BT + d + r'/` \d+ 个文件 / \d+ 字节$',
                      f'- {BT}{d}/` {len(ps)} 个文件 / {sum(cache[p][0] for p in ps)} 字节',
                      text, count=1, flags=re.M)

    print(f'文件 {len(files)} 个 / {total} 字节；改动行 {len(diffs)}')
    print(f'  表一 {len(t1_paths)} -> {len(files)}；表二 {len(t2_paths)} -> {len(files)}；'
          f'缺失 {len(missing_t1) + len(missing_t2)}；多余 {len(extra)}')
    for p, t in diffs:
        print(f'  {t} {p}')
    if extra:
        print('  ⚠ 地图中有已不存在的文件，已从结果中移除：')
        for p in extra:
            print(f'    {p}')
    if check:
        print('--check：未写盘')
        return 1 if diffs else 0
    open(MAP, 'w', encoding='utf-8', newline=chr(10)).write(text)
    print('已写盘', MAP)
    return 0


if __name__ == '__main__':
    main()