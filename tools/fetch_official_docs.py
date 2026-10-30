#!/usr/bin/env python3
"""拉取 OpenClaw 官方文档 → OfficialDocument/OpenClaw（按 URL 路径组织）

方法（延续既有副本的约定）：
  - 源：https://docs.openclaw.ai/sitemap.xml（英文主版本，无语言前缀的 URL）
  - 每个页面 → OfficialDocument/OpenClaw/<url 路径>.md
  - 头部带标记：
      ---
      # <title>
      # URL: https://docs.openclaw.ai/<path>
      # Downloaded: <date>
      ---
  - 幂等：已存在且 URL 相同的文件跳过（除非 --force）
"""
import argparse
import datetime
import os
import re
import sys
import time
import urllib.request

BASE = "https://docs.openclaw.ai"
SITEMAP = f"{BASE}/sitemap.xml"
# 项目根（tools/ 的上一级）
_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_ROOT = os.path.join(_PROJ_ROOT, "OfficialDocument", "OpenClaw")

# 排除语言前缀（/ar /zh /de ... 等多语言，只留英文主版本）
_LANG_RE = re.compile(r"^/(ar|zh|de|es|fr|ja|ko|pt|ru|it|nl|pl|tr|vi|th|id|hi|uk|cs|da|el|fi|hu|nb|ro|sv|sk|sl|bg|ca|he|hr|lt|lv|sr|et|fa|ms|ta|bn|ur)\b")


def get(url: str, retries: int = 3) -> str:
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:
            if i == retries - 1:
                raise
            time.sleep(2 * (i + 1))
    return ""


def extract_en_urls(sitemap_xml: str) -> list[str]:
    urls = re.findall(r"<loc>(.*?)</loc>", sitemap_xml)
    out = []
    for u in urls:
        path = u.replace(BASE, "").strip("/")
        if not path:
            continue
        if _LANG_RE.match("/" + path):
            continue
        if "?" in path or "#" in path:
            continue
        out.append(path)
    return sorted(set(out))


def fetch_page(path: str) -> tuple[str, str]:
    """拉取页面 markdown（URL 加 .md 后缀，官方支持），返回 (markdown_body, title)。"""
    # 官方 llms.txt：Fetch individual pages as Markdown with `.md` URLs
    url = f"{BASE}/{path}.md" if not path.endswith(".md") else f"{BASE}/{path}"
    body = get(url)
    if not body:
        return "", ""
    # 去掉可能的 HTML 包裹（若 .md 端点返回了 HTML 则退化为提取正文）
    if body.lstrip().startswith("<"):
        m = re.search(r"<title[^>]*>(.*?)</title>", body, re.DOTALL | re.IGNORECASE)
        title = m.group(1).strip() if m else path.split("/")[-1]
        return "", re.sub(r"\s+", " ", title)
    # 取标题：markdown 第一个 # 行
    title = ""
    for line in body.splitlines():
        if line.startswith("# ") and not line.startswith("# URL"):
            title = line[2:].strip()
            break
    return body, title


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="强制覆盖已存在的文件")
    ap.add_argument("--limit", type=int, default=0, help="只拉前 N 个（调试用）")
    args = ap.parse_args()

    today = datetime.date.today().isoformat()
    print(f"[1/3] 拉取 sitemap: {SITEMAP}")
    sm = get(SITEMAP)
    if not sm:
        print("sitemap 拉取失败"); sys.exit(1)
    paths = extract_en_urls(sm)
    print(f"      英文主版本页面: {len(paths)} 个")
    if args.limit:
        paths = paths[: args.limit]

    os.makedirs(OUT_ROOT, exist_ok=True)

    new, updated, skipped, failed = 0, 0, 0, 0
    print(f"[2/3] 开始拉取 → {OUT_ROOT}")
    for i, path in enumerate(paths, 1):
        rel = f"{path}.md" if not path.endswith(".md") else path
        dest = os.path.join(OUT_ROOT, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        # 幂等：已存在且带 URL 标记则跳过
        if not args.force and os.path.exists(dest):
            try:
                head = open(dest, encoding="utf-8").read(300)
                if f"# URL: {BASE}/{path}" in head:
                    skipped += 1
                    continue
            except Exception:
                pass
        try:
            body, title = fetch_page(path)
            if not body:
                failed += 1
                print(f"  [{i}/{len(paths)}] FAIL {path}")
                continue
            doc = (
                "---\n"
                f"# {title}\n"
                f"# URL: {BASE}/{path}\n"
                f"# Downloaded: {today}\n"
                "---\n\n"
                + body
            )
            with open(dest, "w", encoding="utf-8") as f:
                f.write(doc)
            if os.path.exists(dest):
                updated += 1
            else:
                new += 1
            if i % 25 == 0 or i == len(paths):
                print(f"  [{i}/{len(paths)}] 完成 {new + updated + skipped} / 失败 {failed}")
        except Exception as e:
            failed += 1
            print(f"  [{i}/{len(paths)}] ERR {path}: {e}")
        time.sleep(0.2)

    print(f"[3/3] 完成: 新增 {new} / 更新 {updated} / 跳过 {skipped} / 失败 {failed}")


if __name__ == "__main__":
    main()
