#!/usr/bin/env python3
"""data/rankings.csv → docs/index.html (GitHub Pages 대시보드).

외부 CDN을 쓰지 않는다. 차트는 inline SVG로 직접 그리므로
네트워크가 막힌 환경에서도, 파일을 그냥 열어도 동작한다.
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "data" / "rankings.csv"
OUT = ROOT / "docs" / "index.html"
CONFIG = ROOT / "config" / "targets.yml"

TEMPLATE = (Path(__file__).resolve().parent / "dashboard_template.html").read_text(encoding="utf-8")


def build() -> None:
    if not CSV_PATH.exists():
        raise SystemExit(f"{CSV_PATH} 가 없습니다. 먼저 src/track.py 를 실행하세요.")

    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    labels = {c["name"]: c.get("label", c["name"]) for c in cfg["centers"]}
    order = {c["name"]: c["keywords"] for c in cfg["centers"]}

    rows = list(csv.DictReader(CSV_PATH.open(encoding="utf-8")))
    dates = sorted({r["date"] for r in rows})

    lookup: dict = defaultdict(dict)
    depth = 0
    for r in rows:
        value = r["our_rank"].strip()
        lookup[(r["center"], r["surface"], r["keyword"])][r["date"]] = int(value) if value else None
        if r["surface"] == "blog":
            depth = max(depth, int(r["checked_depth"] or 0))
    depth = depth or 300

    surfaces = [
        ("place", "플레이스", "지역검색 상위 5위 안에서의 위치. 5위 밖은 끊어져 보인다."),
        ("blog", "블로그", f"블로그 검색에서 우리 글의 실제 순위. 최대 {depth}위까지 확인."),
    ]

    panels, tiles = [], []
    last = dates[-1] if dates else None
    prev = dates[-2] if len(dates) > 1 else None

    for center, keywords in order.items():
        for surface, s_label, note in surfaces:
            series, observed = [], []
            for kw in keywords:
                by_date = lookup.get((center, surface, kw), {})
                values = [by_date.get(d) for d in dates]
                observed += [v for v in values if v]
                series.append({"name": kw, "values": values})
                now = by_date.get(last)
                before = by_date.get(prev) if prev else None
                tiles.append({
                    "center": labels[center], "label": kw,
                    "surface": surface, "surfaceLabel": s_label,
                    "rank": now,
                    "delta": (before - now) if (now and before) else None,
                })
            # 5위까지만 보이는 플레이스는 축을 5로 고정, 블로그는 관측값에 맞춘다
            ymax = 5 if surface == "place" else max(10, max(observed or [10]))
            panels.append({
                "title": f"{labels[center]} · {s_label}",
                "note": note, "max": ymax, "series": series,
            })

    payload = {"dates": dates, "panels": panels, "today": tiles,
               "hasPrev": len(dates) > 1}
    html = (TEMPLATE
            .replace("__DATA__", json.dumps(payload, ensure_ascii=False))
            .replace("__UPDATED__", last or "-")
            .replace("__DEPTH__", str(depth)))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print(f"대시보드 생성 → {OUT}  (날짜 {len(dates)}개, 패널 {len(panels)}개)")


if __name__ == "__main__":
    build()
