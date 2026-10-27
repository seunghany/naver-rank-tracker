#!/usr/bin/env python3
"""data/rankings.csv (+ data/blog_posts.csv) → docs/index.html (GitHub Pages 대시보드).

외부 CDN을 쓰지 않는다. 차트는 inline SVG로 직접 그리므로
네트워크가 막힌 환경에서도, 파일을 그냥 열어도 동작한다.

센터마다 보여 주는 것
- 오늘 요약: 핵심 키워드(◆)와 전체 키워드의 노출 수 + 최근 2주 흐름 (직전 수집일 대비)
- 변동: 오른 곳 · 내린 곳 · 새로 잡힘 · 빠짐, 새로 검색에 잡힌 글
- 플레이스 경쟁 구도: 업체별 1위·5위 안 키워드 수와 최근 2주 흐름, 1위가 바뀐 묶음
- 키워드별 순위 표: ◆ 센터 핵심 · ★ 검색량 상위 · 그 외 지역 · 니즈 · 브랜드, 키워드마다 최근 2주 흐름
- 추이 차트: 플레이스 · 블로그(지역) · 블로그(니즈·브랜드)
"""
from __future__ import annotations

import csv
import json
import math
import re
from collections import defaultdict
from datetime import date
from html import escape as html_escape
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "data" / "rankings.csv"
POSTS_PATH = ROOT / "data" / "blog_posts.csv"
OUT = ROOT / "docs" / "index.html"
CONFIG = ROOT / "config" / "targets.yml"

TEMPLATE = (Path(__file__).resolve().parent / "dashboard_template.html").read_text(encoding="utf-8")

# 니즈·브랜드 차트에서 색을 받는 키워드 수 — 검색량 상위 니즈 4개 + 브랜드 2개.
# 나머지는 회색. 색은 순위가 아니라 키워드에 고정된다.
EXTRA_NEEDS_COLORED = 4
EXTRA_BRAND_COLORED = 2
# 최근 흐름(미니 차트)에 쓰는 수집일 수
HISTORY = 14
# 경쟁 구도 표에 보여 줄 업체 수 (우리는 순위와 무관하게 항상 넣는다)
COMPETITORS_SHOWN = 6


def post_key(link: str) -> str:
    """같은 글인지 가리는 키 (src/track.py 와 같은 규칙)."""
    return link.split("?")[0].rstrip("/").lower()


def norm(text: str) -> str:
    """업체명 비교용 키 — 공백·대소문자·일부 기호 무시 (src/track.py 와 같은 규칙)."""
    return re.sub(r"[\s\-_.]", "", text or "").lower()


def to_rank(value: str) -> int | None:
    value = (value or "").strip()
    return int(value) if value else None


def depth_marks(rows: list[dict], dates: list[str]) -> list[dict]:
    """블로그 확인 범위(서로 다른 글 수 중앙값)가 크게 바뀐 날 — 차트에 세로선으로 표시한다.
    범위가 넓어지면 전에는 '미노출'이던 글이 순위에 잡히므로, 실제 변화와 구분해야 한다."""
    by_date: dict[str, list[int]] = defaultdict(list)
    for r in rows:
        if r["surface"] == "blog" and r.get("checked_depth"):
            by_date[r["date"]].append(int(r["checked_depth"]))
    marks, before = [], None
    for d in dates:
        vals = sorted(by_date.get(d, []))
        if not vals:
            continue
        median = vals[len(vals) // 2]
        if before and median > before * 1.2:
            marks.append({"date": d, "label": f"확인 범위 확대 (약 {median}개)"})
        before = median
    return marks


def build() -> None:
    if not CSV_PATH.exists():
        raise SystemExit(f"{CSV_PATH} 가 없습니다. 먼저 src/track.py 를 실행하세요.")

    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    rows = list(csv.DictReader(CSV_PATH.open(encoding="utf-8")))
    dates = sorted({r["date"] for r in rows})
    last = dates[-1] if dates else None
    prev = dates[-2] if len(dates) > 1 else None
    day0 = date.fromisoformat(dates[0]) if dates else None
    days = [(date.fromisoformat(d) - day0).days for d in dates]
    # 첫날~마지막날 사이 수집이 없던 날 (차트에서 선은 이어서 그린다)
    have = set(days)
    missing = [date.fromordinal(day0.toordinal() + i).isoformat()
               for i in range(days[-1] + 1) if i not in have] if dates else []

    # (center, surface, keyword) → {date: {rank, title, top1}}
    lookup: dict = defaultdict(dict)
    depth = 0
    for r in rows:
        lookup[(r["center"], r["surface"], r["keyword"])][r["date"]] = {
            "rank": to_rank(r["our_rank"]),
            "title": r.get("our_title", ""),
            "top1": r.get("rank1", ""),
            "top": [r.get(f"rank{i}") or "" for i in range(1, 6)],
        }
        if r["surface"] == "blog" and r["date"] == last:
            depth = max(depth, int(r["checked_depth"] or 0))
    depth = depth or 300

    # 블로그 글 단위 기록 — 오늘 처음 검색에 잡힌 글을 찾는다 (없으면 건너뜀)
    posts = list(csv.DictReader(POSTS_PATH.open(encoding="utf-8"))) if POSTS_PATH.exists() else []
    first_seen: dict[str, str] = {}
    for p in posts:
        k = post_key(p["link"])
        if k not in first_seen or p["date"] < first_seen[k]:
            first_seen[k] = p["date"]

    centers = []
    for c in cfg["centers"]:
        name = c["name"]
        local = list(c["keywords"])
        needs = list(c.get("needs_keywords") or [])
        brand = list(c.get("brand_keywords") or [])
        focus = [k for k in (c.get("focus_keywords") or []) if k in local]
        # ◆ 센터 핵심 — 검색량(★)과 별개로, 우리 수업과 직결돼 집중 공략하는 키워드
        core = [k for k in (c.get("core_keywords") or []) if k in local + needs + brand]
        hist = dates[-HISTORY:]
        our_keys = {norm(a) for a in c.get("place_aliases") or []}

        def obs(surface: str, kw: str, d: str | None) -> dict:
            return lookup.get((name, surface, kw), {}).get(d) or {} if d else {}

        def cell(surface: str, kw: str) -> dict:
            now, before = obs(surface, kw, last), obs(surface, kw, prev)
            r, b = now.get("rank"), before.get("rank")
            return {
                "rank": r, "prev": b,
                "delta": (b - r) if (r and b) else None,
                "measured": bool(now),
                "title": now.get("title", "") if surface == "blog" else "",
                "otherPost": bool(r and b and now.get("title") != before.get("title")),
            }

        # 키워드별 순위 표 — ◆ 핵심을 맨 위에, 그다음 ★ 검색량 상위, 나머지
        table = []
        rest = [k for k in local + needs + brand if k not in core]
        for group, kws in (("core", [k for k in local + needs + brand if k in core]),
                           ("focus", [k for k in focus if k not in core]),
                           ("local", [k for k in rest if k in local and k not in focus]),
                           ("needs", [k for k in rest if k in needs]),
                           ("brand", [k for k in rest if k in brand])):
            for kw in kws:
                table.append({
                    "kw": kw, "group": group, "core": kw in core, "focus": kw in focus,
                    "blogOnly": kw not in local,
                    "place": cell("place", kw) if kw in local else None,
                    "blog": cell("blog", kw),
                })

        # 오늘 요약 — 날짜별로 계산해 최근 2주 흐름도 같이 보낸다
        core_local = [k for k in core if k in local]

        def kpi(d: str | None) -> dict:
            if not d:
                return {}
            place = [obs("place", k, d).get("rank") for k in local]
            blog = [obs("blog", k, d).get("rank") for k in local + needs + brand]
            core_place = [obs("place", k, d).get("rank") for k in core_local]
            core_blog = [obs("blog", k, d).get("rank") for k in core]
            return {
                "corePlaceTop5": sum(1 for v in core_place if v),
                "coreBlogTop10": sum(1 for v in core_blog if v and v <= 10),
                "placeTop5": sum(1 for v in place if v),
                "blogSeen": sum(1 for v in blog if v),
                "blogFirst": sum(1 for v in blog if v == 1),
                "blogTop10": sum(1 for v in blog if v and v <= 10),
            }

        # 변동 — 순위 비율로 정렬해서 2→1위가 45→43위보다 앞에 오게 한다
        up, down, new, lost = [], [], [], []
        for row in table:
            for surface in ("place", "blog"):
                o = row[surface]
                if not o or not o["measured"]:
                    continue
                item = {"kw": row["kw"], "surface": surface, "from": o["prev"], "to": o["rank"],
                        "otherPost": o["otherPost"]}
                if o["rank"] and o["prev"]:
                    if o["rank"] == o["prev"]:
                        continue
                    item["score"] = abs(math.log(o["prev"]) - math.log(o["rank"]))
                    (up if o["rank"] < o["prev"] else down).append(item)
                elif o["rank"] and prev and obs(surface, row["kw"], prev):
                    new.append(item)
                elif o["prev"]:
                    lost.append(item)
        for lst in (up, down):
            lst.sort(key=lambda i: -i["score"])
        new.sort(key=lambda i: i["to"])
        lost.sort(key=lambda i: i["from"])

        # 오늘 처음 검색에 잡힌 우리 글 — 가장 높은 키워드 순위 기준
        fresh: dict[str, dict] = {}
        for p in posts:
            if p["center"] != name or p["date"] != last or first_seen.get(post_key(p["link"])) != last:
                continue
            k, rank = post_key(p["link"]), int(p["rank"])
            e = fresh.setdefault(k, {"title": p["title"], "link": p["link"], "postdate": p["postdate"],
                                     "kw": p["keyword"], "rank": rank, "hits": 0})
            e["hits"] += 1
            if rank < e["rank"]:
                e["kw"], e["rank"] = p["keyword"], rank
        new_posts = sorted(fresh.values(), key=lambda e: e["rank"])

        # 플레이스 1위가 바뀐 곳 — 같은 순서 바뀜이 여러 키워드에 한꺼번에 나타나므로 (이전→지금) 묶음으로
        moved: dict[tuple, list[str]] = defaultdict(list)
        for kw in local:
            a, b = obs("place", kw, prev).get("top1", ""), obs("place", kw, last).get("top1", "")
            if a and b and a != b:
                moved[(a, b)].append(kw)
        top_changes = sorted(({"from": a, "to": b, "kws": kws,
                               "ours": any(norm(b) == k or k in norm(b) for k in our_keys)}
                              for (a, b), kws in moved.items()), key=lambda t: -len(t["kws"]))

        # 플레이스 경쟁 구도 — 업체별로 지역 키워드 몇 개에서 1위·5위 안인지, 최근 2주 흐름
        def is_ours(title: str) -> bool:
            t = norm(title)
            return bool(t) and any(k == t or k in t for k in our_keys)

        board: dict[str, dict] = {}
        for j, d in enumerate(hist):
            for kw in local:
                for pos, title in enumerate(obs("place", kw, d).get("top", []), start=1):
                    if not title:
                        continue
                    key = c.get("label", name) if is_ours(title) else title
                    e = board.setdefault(key, {"name": key, "ours": is_ours(title),
                                               "first": [0] * len(hist), "top5": [0] * len(hist)})
                    e["top5"][j] += 1
                    e["first"][j] += pos == 1
        ranked = sorted(board.values(), key=lambda e: (-e["first"][-1], -e["top5"][-1], e["name"]))
        competitors = [e for e in ranked if not e["ours"]][:COMPETITORS_SHOWN]
        ours_row = next((e for e in ranked if e["ours"]), None)
        competitors.append(ours_row or {"name": c.get("label", name), "ours": True,
                                        "first": [0] * len(hist), "top5": [0] * len(hist)})

        # 검색량 1위 키워드의 플레이스 1위가 최근 2주 동안 어떻게 바뀌었는지 (돌아가며 1위인지 확인용)
        lead_kw = focus[0] if focus else (local[0] if local else None)
        rotation = None
        if lead_kw:
            seq = [obs("place", lead_kw, d).get("top1", "") for d in hist]
            seq = [s for s in seq if s]
            counts: dict[str, int] = defaultdict(int)
            for s in seq:
                counts[s] += 1
            rotation = {"kw": lead_kw, "days": len(seq),
                        "changes": sum(1 for a, b in zip(seq, seq[1:]) if a != b),
                        "holders": sorted(({"name": k, "days": v} for k, v in counts.items()),
                                          key=lambda h: -h["days"])}

        # 추이 차트 — 색은 키워드에 고정 (★ 순서 / 니즈 상위 + 브랜드)
        def series(surface: str, kws: list[str], colored: list[str]) -> list[dict]:
            return [{"name": kw, "slot": colored.index(kw) if kw in colored else None,
                     "focus": kw in focus,
                     "values": [obs(surface, kw, d).get("rank") for d in dates]}
                    for kw in kws]

        extra_colored = needs[:EXTRA_NEEDS_COLORED] + brand[:EXTRA_BRAND_COLORED]
        panels = [
            {"title": "플레이스", "kind": "place",
             "note": "네이버 지역검색 상위 5곳 안에서의 위치. 5위 밖은 선이 끊어져 보입니다.",
             "series": series("place", local, focus)},
            {"title": "블로그 · 지역 키워드", "kind": "blog",
             "note": "블로그 검색에서 우리 글 가운데 가장 높은 순위.",
             "series": series("blog", local, focus)},
        ]
        if needs or brand:
            panels.append({"title": "블로그 · 니즈·브랜드 키워드", "kind": "blog",
                           "note": "지역명이 없는 키워드라 블로그만 봅니다. 색은 검색량 상위 니즈 키워드와 브랜드 키워드.",
                           "series": series("blog", needs + brand, extra_colored)})

        centers.append({
            "name": name, "label": c.get("label", name),
            "counts": {"local": len(local), "needs": len(needs), "brand": len(brand)},
            "coreCounts": {"local": len(core_local), "all": len(core)},
            "kpi": {"now": kpi(last), "prev": kpi(prev), "hist": [kpi(d) for d in hist]},
            "table": table,
            "changes": {"up": up, "down": down, "new": new, "lost": lost},
            "newPosts": new_posts, "topChanges": top_changes,
            "competitors": competitors, "rotation": rotation,
            "panels": panels,
        })

    payload = {"dates": dates, "days": days, "last": last, "prev": prev,
               "missing": missing, "marks": depth_marks(rows, dates), "depth": depth,
               "centers": centers}
    html = (TEMPLATE
            .replace("__DATA__", json.dumps(payload, ensure_ascii=False))
            .replace("__UPDATED__", last or "-")
            # 센터별 보고서 링크 — src/build_report.py --web 이 docs/reports/ 에 만든다
            .replace("__REPORTS__", "".join(
                f'<a href="reports/{c["name"]}.html">{html_escape(c.get("label", c["name"]))} →</a>'
                for c in cfg["centers"])))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print(f"대시보드 생성 → {OUT}  (날짜 {len(dates)}개, 센터 {len(centers)}곳, "
          f"차트 {sum(len(c['panels']) for c in centers)}개)")


if __name__ == "__main__":
    build()
