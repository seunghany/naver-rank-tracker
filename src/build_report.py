#!/usr/bin/env python3
"""센터별 노출 현황 보고서 (HTML → PDF).

data/rankings.csv + config/targets.yml 로 센터마다 A4 보고서를 만든다.
차트는 외부 라이브러리 없이 inline SVG로 그린다 (대시보드와 같은 방식).

사용:
    python src/build_report.py                 # 가장 최근 날짜로
    python src/build_report.py 2026-09-23      # 날짜 지정
    python src/build_report.py --web           # GitHub Pages용 (GitHub Actions가 매일 실행)
출력: reports/<날짜>_<센터>_노출현황.html / .pdf
      --web 이면 docs/reports/<센터>.html (PDF 없음, 플레이스 신호·전환 섹션 제외 —
      저장소와 사이트가 공개라 경영 지표를 올리지 않는다. .gitignore 의 place_signals.csv 와 같은 이유)
"""
from __future__ import annotations

import csv
import html
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "data" / "rankings.csv"
CONFIG = ROOT / "config" / "targets.yml"
OUTDIR = ROOT / "reports"
WEB_OUTDIR = ROOT / "docs" / "reports"
SIGNALS = ROOT / "data" / "place_signals.csv"   # 수기 기록 (src/log_signals.py)

# dataviz 기본 팔레트 (검증 완료: validate_palette.js --mode light 전 항목 PASS)
C = {
    "surface": "#fcfcfb", "text": "#0b0b0b", "sub": "#52514e", "muted": "#8a8981",
    "grid": "#eceae5", "line": "#d9d7d1",
    "s1": "#2a78d6",          # 플레이스
    "s2": "#eb6834",          # 블로그
    "seq": "#9ec5f4",         # 검색량 막대 (sequential 200)
    "off": "#efeeea",         # 미노출
}
PLACE_MAX = 5                 # 지역검색 API가 주는 최대 순위


def esc(s) -> str:
    return html.escape(str(s))


def nkey(text: str) -> str:
    """업체명 비교용 키 — 공백·기호·대소문자 무시 (track.py의 norm과 같은 규칙)."""
    return re.sub(r"[\s\-_.&]", "", text or "").lower()


def load(day: str | None):
    rows = list(csv.DictReader(CSV_PATH.open(encoding="utf-8")))
    days = sorted({r["date"] for r in rows})
    day = day or days[-1]
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    # targets.yml 주석의 검색량(센터 내 상대값)을 읽는다.
    # 센터마다 스케일이 다르고 같은 키워드가 두 센터에 있을 수 있으므로 (센터, 키워드)로 키를 잡는다.
    vol: dict[tuple[str, str], str] = {}
    center = None
    for ln in CONFIG.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s+- name:\s*(\S+)", ln)
        if m:
            center = m.group(1)
            continue
        m = re.match(r"\s+- (\S.*?)\s*#\s*([\d.]+)", ln)
        if m and center:
            vol[(center, m.group(1).strip())] = m.group(2)
    signals = (list(csv.DictReader(SIGNALS.open(encoding="utf-8")))
               if SIGNALS.exists() else [])
    return rows, days, day, cfg, vol, signals


def dot_chart(items, width=660):
    """플레이스 순위 — 1~5위 도트 차트. items: [(keyword, rank, top1)]"""
    if not items:
        return "<p class='empty'>상위 5위 안에 든 키워드가 없습니다.</p>"
    row_h, pad_l, pad_r, pad_t = 30, 150, 30, 26
    h = pad_t + row_h * len(items) + 12
    x = lambda r: pad_l + (r - 1) / (PLACE_MAX - 1) * (width - pad_l - pad_r)  # noqa: E731
    out = [f"<svg viewBox='0 0 {width} {h}' width='{width}' height='{h}' role='img'>"]
    for r in range(1, PLACE_MAX + 1):
        out.append(f"<line x1='{x(r):.0f}' y1='{pad_t - 8}' x2='{x(r):.0f}' y2='{h - 12}' "
                   f"stroke='{C['grid']}' stroke-width='1'/>")
        out.append(f"<text x='{x(r):.0f}' y='{pad_t - 14}' text-anchor='middle' "
                   f"font-size='11' fill='{C['muted']}'>{r}위</text>")
    for i, (kw, rank, _) in enumerate(items):
        y = pad_t + row_h * i + row_h / 2
        out.append(f"<text x='{pad_l - 12}' y='{y + 4}' text-anchor='end' font-size='12.5' "
                   f"fill='{C['text']}'>{esc(kw)}</text>")
        out.append(f"<line x1='{x(PLACE_MAX):.0f}' y1='{y:.0f}' x2='{x(rank):.0f}' y2='{y:.0f}' "
                   f"stroke='{C['grid']}' stroke-width='2' stroke-linecap='round'/>")
        out.append(f"<circle cx='{x(rank):.0f}' cy='{y:.0f}' r='7' fill='{C['s1']}' "
                   f"stroke='{C['surface']}' stroke-width='2'/>")
        out.append(f"<text x='{x(rank) + 13:.0f}' y='{y + 4:.0f}' font-size='11' "
                   f"font-weight='600' fill='{C['sub']}'>{rank}위</text>")
    out.append("</svg>")
    return "".join(out)


def bar_chart(items, width=660, depth=300):
    """블로그 순위 — 가로 막대. 값이 작을수록 좋다. items: [(keyword, rank, title)]
    depth = 블로그를 몇 위까지 확인했는지 (서로 다른 글 수)."""
    if not items:
        return f"<p class='empty'>{depth}위 안에 든 키워드가 없습니다.</p>"
    row_h, pad_l, pad_r, pad_t = 34, 150, 56, 24
    h = pad_t + row_h * len(items) + 10
    top = max(100, max(r for _, r, _ in items))
    plot = width - pad_l - pad_r
    out = [f"<svg viewBox='0 0 {width} {h}' width='{width}' height='{h}' role='img'>"]
    for g in range(0, top + 1, 25 if top <= 100 else 50):
        gx = pad_l + g / top * plot
        out.append(f"<line x1='{gx:.0f}' y1='{pad_t - 6}' x2='{gx:.0f}' y2='{h - 10}' "
                   f"stroke='{C['grid']}' stroke-width='1'/>")
        out.append(f"<text x='{gx:.0f}' y='{pad_t - 12}' text-anchor='middle' font-size='11' "
                   f"fill='{C['muted']}'>{g if g else 1}위</text>")
    for i, (kw, rank, _) in enumerate(items):
        y = pad_t + row_h * i + 6
        w = max(3, rank / top * plot)
        out.append(f"<text x='{pad_l - 12}' y='{y + 14}' text-anchor='end' font-size='12.5' "
                   f"fill='{C['text']}'>{esc(kw)}</text>")
        out.append(f"<rect x='{pad_l}' y='{y}' width='{w:.0f}' height='18' rx='4' fill='{C['s2']}'/>")
        out.append(f"<text x='{pad_l + w + 8:.0f}' y='{y + 14}' font-size='11.5' font-weight='600' "
                   f"fill='{C['sub']}'>{rank}위</text>")
    out.append("</svg>")
    return "".join(out)


def coverage_chart(rows_kw, width=660, depth=300):
    """검색량 대비 노출 — 검색량 막대 + 플레이스/블로그 노출 칸.
    rows_kw: [(keyword, vol(float|None), vol_label, place_rank|None, blog_rank|None,
               place_measured, blog_measured)]"""
    row_h, pad_l, pad_t = 24, 132, 40
    cell_w, gap = 74, 10
    bar_w = width - pad_l - (cell_w * 2 + gap) - 24
    h = pad_t + row_h * len(rows_kw) + 10
    vmax = max([v for _, v, _, _, _, _, _ in rows_kw if v] + [1])
    cx1 = pad_l + bar_w + 24
    cx2 = cx1 + cell_w + gap
    out = [f"<svg viewBox='0 0 {width} {h}' width='{width}' height='{h}' role='img'>"]
    out.append(f"<text x='{pad_l}' y='{pad_t - 20}' font-size='11' fill='{C['muted']}'>검색량(센터 내 상대값)</text>")
    out.append(f"<text x='{cx1 + cell_w / 2:.0f}' y='{pad_t - 20}' text-anchor='middle' font-size='11' "
               f"font-weight='600' fill='{C['s1']}'>플레이스</text>")
    out.append(f"<text x='{cx2 + cell_w / 2:.0f}' y='{pad_t - 20}' text-anchor='middle' font-size='11' "
               f"font-weight='600' fill='{C['s2']}'>블로그</text>")
    for i, (kw, v, vlabel, pr, br, pm, bm) in enumerate(rows_kw):
        y = pad_t + row_h * i
        out.append(f"<text x='{pad_l - 10}' y='{y + 15}' text-anchor='end' font-size='11.5' "
                   f"fill='{C['text']}'>{esc(kw)}</text>")
        w = max(2, (v or 0) / vmax * bar_w)
        out.append(f"<rect x='{pad_l}' y='{y + 4}' width='{w:.0f}' height='13' rx='3' fill='{C['seq']}'/>")
        out.append(f"<text x='{pad_l + w + 6:.0f}' y='{y + 15}' font-size='10' fill='{C['muted']}'>{esc(vlabel)}</text>")
        for cx, rank, color, off_label, meas in ((cx1, pr, C["s1"], "5위밖", pm),
                                                (cx2, br, C["s2"], f"{depth}위밖", bm)):
            on = rank is not None
            label = str(rank) + "위" if on else (off_label if meas else "미측정")
            out.append(f"<rect x='{cx}' y='{y + 2}' width='{cell_w}' height='18' rx='4' "
                       f"fill='{color if on else C['off']}'/>")
            out.append(f"<text x='{cx + cell_w / 2:.0f}' y='{y + 15}' text-anchor='middle' font-size='11' "
                       f"font-weight='600' fill='{'#ffffff' if on else C['muted']}'>{label}</text>")
    out.append("</svg>")
    return "".join(out)


def needs_chart(rows_kw, width=660, depth=300):
    """니즈 키워드 — 검색량 막대 + 블로그 노출 칸. 지역명이 없어 플레이스는 재지 않는다.
    rows_kw: [(keyword, vol(float|None), vol_label, blog_rank|None, measured)]
    measured=False 는 아직 수집되지 않은 키워드 — '{depth}위밖'과 구분해 표시한다."""
    if not rows_kw:
        return "<p class='empty'>등록된 니즈 키워드가 없습니다.</p>"
    row_h, pad_l, pad_t = 24, 128, 40
    cell_w = 82
    bar_w = width - pad_l - cell_w - 90
    h = pad_t + row_h * len(rows_kw) + 10
    vmax = max([v for _, v, _, _, _ in rows_kw if v] + [1])
    cx = pad_l + bar_w + 66
    out = [f"<svg viewBox='0 0 {width} {h}' width='{width}' height='{h}' role='img'>"]
    out.append(f"<text x='{pad_l}' y='{pad_t - 20}' font-size='11' fill='{C['muted']}'>"
               f"검색량(센터 내 상대값 — 지역 키워드와 같은 스케일)</text>")
    out.append(f"<text x='{cx + cell_w / 2:.0f}' y='{pad_t - 20}' text-anchor='middle' font-size='11' "
               f"font-weight='600' fill='{C['s2']}'>블로그</text>")
    for i, (kw, v, vlabel, br, measured) in enumerate(rows_kw):
        y = pad_t + row_h * i
        out.append(f"<text x='{pad_l - 10}' y='{y + 15}' text-anchor='end' font-size='11.5' "
                   f"fill='{C['text']}'>{esc(kw)}</text>")
        w = max(2, (v or 0) / vmax * bar_w)
        out.append(f"<rect x='{pad_l}' y='{y + 4}' width='{w:.0f}' height='13' rx='3' fill='{C['seq']}'/>")
        out.append(f"<text x='{pad_l + w + 6:.0f}' y='{y + 15}' font-size='10' "
                   f"fill='{C['muted']}'>{esc(vlabel)}</text>")
        on = br is not None
        label = str(br) + "위" if on else (f"{depth}위밖" if measured else "미측정")
        out.append(f"<rect x='{cx}' y='{y + 2}' width='{cell_w}' height='18' rx='4' "
                   f"fill='{C['s2'] if on else C['off']}'/>")
        out.append(f"<text x='{cx + cell_w / 2:.0f}' y='{y + 15}' text-anchor='middle' font-size='11' "
                   f"font-weight='600' fill='{'#ffffff' if on else C['muted']}'>{label}</text>")
    out.append("</svg>")
    return "".join(out)


def versus_chart(rows_kw, comps, width=660):
    """경쟁사 비교 — 키워드별 지역검색 순위. 우리는 채운 칸, 경쟁사는 테두리 칸.
    rows_kw: [(keyword, vol_label, our_rank|None, [comp_rank|None, ...], depth)]"""
    if not rows_kw:
        return "<p class='empty'>비교할 데이터가 없습니다.</p>"
    row_h, pad_l, pad_t = 24, 112, 40
    gap = 8
    h = pad_t + row_h * len(rows_kw) + 10
    x0 = pad_l + 40
    # 경쟁사가 늘어도 660px 안에 맞도록 칸 폭을 자동으로 줄인다 (넘치면 PDF에서 잘린다)
    n = 1 + len(comps)
    cell_w = max(56, min(92, int((width - x0 - gap * (n - 1)) / n)))
    fs_head = 11 if cell_w >= 88 else 9.5
    cx = lambda i: x0 + i * (cell_w + gap)                                     # noqa: E731
    out = [f"<svg viewBox='0 0 {width} {h}' width='{width}' height='{h}' role='img'>"]
    heads = ["우리"] + [c["label"] for c in comps]
    for i, name in enumerate(heads):
        out.append(f"<text x='{cx(i) + cell_w / 2:.0f}' y='{pad_t - 20}' text-anchor='middle' "
                   f"font-size='{fs_head}' font-weight='600' fill='{C['s1'] if i == 0 else C['sub']}'>"
                   f"{esc(name)}</text>")
    for r, (kw, vlabel, our, cranks, depth) in enumerate(rows_kw):
        y = pad_t + row_h * r
        out.append(f"<text x='{pad_l - 8}' y='{y + 15}' text-anchor='end' font-size='11.5' "
                   f"fill='{C['text']}'>{esc(kw)}</text>")
        out.append(f"<text x='{pad_l + 34}' y='{y + 15}' text-anchor='end' font-size='9.5' "
                   f"fill='{C['muted']}'>{esc(vlabel)}</text>")
        # 우리 — 채운 칸 (확인 범위 5위)
        on = our is not None
        out.append(f"<rect x='{cx(0)}' y='{y + 2}' width='{cell_w}' height='18' rx='4' "
                   f"fill='{C['s1'] if on else C['off']}'/>")
        out.append(f"<text x='{cx(0) + cell_w / 2:.0f}' y='{y + 15}' text-anchor='middle' "
                   f"font-size='11' font-weight='600' fill='{'#ffffff' if on else C['muted']}'>"
                   f"{str(our) + '위' if on else '5위밖'}</text>")
        # 경쟁사 — 테두리 칸 (확인 범위 3위)
        for i, cr in enumerate(cranks, start=1):
            hit = cr is not None
            out.append(f"<rect x='{cx(i)}' y='{y + 2}' width='{cell_w}' height='18' rx='4' "
                       f"fill='{C['surface']}' stroke='{C['line'] if not hit else C['sub']}' "
                       f"stroke-width='1'/>")
            out.append(f"<text x='{cx(i) + cell_w / 2:.0f}' y='{y + 15}' text-anchor='middle' "
                       f"font-size='11' font-weight='{'600' if hit else '400'}' "
                       f"fill='{C['text'] if hit else C['muted']}'>"
                       f"{str(cr) + '위' if hit else str(depth) + '위밖'}</text>")
    out.append("</svg>")
    return "".join(out)


def h2h(our, comp, depth):
    """우리 vs 경쟁사 승패. depth = 그 행에서 경쟁사를 몇 위까지 확인했는지.
    양쪽 다 확인 범위 밖이면 누가 앞선지 알 수 없으므로 None."""
    if our and comp:
        return "win" if our < comp else "lose"
    if our and comp is None:
        return "win" if our <= depth else None    # 경쟁사 확인 범위 안에 우리가 있으면 확실한 우위
    if comp and our is None:
        return "lose"
    return None


def comp_depth(row) -> int:
    """그 행에서 경쟁사를 몇 위까지 확인했는지.
    rank4·rank5 없이 기록된 예전 행(3위까지)을 자동으로 구분한다."""
    cols = [(row.get(f"rank{i}") or "").strip() for i in range(1, 6)]
    got = sum(1 for c in cols if c)
    checked = int(row.get("checked_depth") or 0)
    if checked > 3 and got <= 3:
        return 3
    return min(checked, 5)


def trend_chart(dates, series, width=660, height=210):
    """블로그 순위 추이 — 값이 작을수록 위. series: [(name, [rank|None...], color)]"""
    pad_l, pad_r, pad_t, pad_b = 46, 116, 18, 28
    vals = [v for _, ys, _ in series for v in ys if v]
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    lo, hi = max(1, lo - 8), hi + 8
    px = lambda i: pad_l + i / max(1, len(dates) - 1) * (width - pad_l - pad_r)   # noqa: E731
    py = lambda v: pad_t + (v - lo) / (hi - lo) * (height - pad_t - pad_b)        # noqa: E731
    out = [f"<svg viewBox='0 0 {width} {height}' width='{width}' height='{height}' role='img'>"]
    for v in (lo, (lo + hi) // 2, hi):
        out.append(f"<line x1='{pad_l}' y1='{py(v):.0f}' x2='{width - pad_r}' y2='{py(v):.0f}' "
                   f"stroke='{C['grid']}' stroke-width='1'/>")
        out.append(f"<text x='{pad_l - 8}' y='{py(v) + 4:.0f}' text-anchor='end' font-size='10.5' "
                   f"fill='{C['muted']}'>{int(v)}위</text>")
    for i, d in enumerate(dates):
        out.append(f"<text x='{px(i):.0f}' y='{height - 8}' text-anchor='middle' font-size='10.5' "
                   f"fill='{C['muted']}'>{d[5:]}</text>")
    for name, ys, color in series:
        pts = [(px(i), py(v)) for i, v in enumerate(ys) if v]
        if not pts:
            continue
        d = " ".join(f"{'M' if k == 0 else 'L'}{x:.0f},{y:.0f}" for k, (x, y) in enumerate(pts))
        out.append(f"<path d='{d}' fill='none' stroke='{color}' stroke-width='2' "
                   f"stroke-linecap='round' stroke-linejoin='round'/>")
        for x, y in pts:
            out.append(f"<circle cx='{x:.0f}' cy='{y:.0f}' r='4' fill='{color}' "
                       f"stroke='{C['surface']}' stroke-width='2'/>")
        lx, ly = pts[-1]
        out.append(f"<text x='{lx + 10:.0f}' y='{ly + 4:.0f}' font-size='11' fill='{C['sub']}'>{esc(name)}</text>")
    out.append("</svg>")
    return "".join(out)


def build_center(center: dict, rows, days, day, vol, signals=(),
                 show_signals: bool = True) -> tuple[str, str]:
    name, label = center["name"], center.get("label", center["name"])
    keywords = center["keywords"]
    today = {(r["keyword"], r["surface"]): r for r in rows if r["date"] == day and r["center"] == name}
    # 블로그 확인 범위 — 그날 실제로 본 서로 다른 글 수 (2026-10-01까지는 받은 순서 기준 300)
    bdepth = max([int(r["checked_depth"] or 0) for (_, s), r in today.items() if s == "blog"] or [0]) or 300
    prev_day = days[days.index(day) - 1] if days.index(day) > 0 else None
    prev = {(r["keyword"], r["surface"]): r["our_rank"]
            for r in rows if prev_day and r["date"] == prev_day and r["center"] == name}

    def rank(kw, surface):
        r = today.get((kw, surface))
        return int(r["our_rank"]) if r and r["our_rank"] else None

    place_hit = sorted([(kw, rank(kw, "place"), today[(kw, "place")]["rank1"])
                        for kw in keywords if rank(kw, "place")], key=lambda x: x[1])
    blog_hit = sorted([(kw, rank(kw, "blog"), today[(kw, "blog")]["our_title"])
                       for kw in keywords if rank(kw, "blog")], key=lambda x: x[1])
    fvol = lambda kw: (float(vol[(name, kw)]) if (name, kw) in vol else None)     # noqa: E731
    cover = sorted(
        [(kw, fvol(kw), vol.get((name, kw), "?"), rank(kw, "place"), rank(kw, "blog"),
          (kw, "place") in today, (kw, "blog") in today) for kw in keywords],
        key=lambda x: -(x[1] if x[1] is not None else -1))
    place_miss = [(kw, vol.get((name, kw), "?"), today[(kw, "place")]["rank1"])
                  for kw in keywords if not rank(kw, "place") and (kw, "place") in today]
    blog_miss = [(kw, vol.get((name, kw), "?")) for kw in keywords
                 if not rank(kw, "blog") and (kw, "blog") in today]
    # 추적에 막 추가돼 아직 한 번도 수집되지 않은 키워드
    unmeasured = [kw for kw in keywords + (center.get("needs_keywords") or [])
                  + (center.get("brand_keywords") or [])
                  if (kw, "place") not in today and (kw, "blog") not in today]
    needs = center.get("needs_keywords") or []
    needs_rows = sorted(
        [(kw, fvol(kw), vol.get((name, kw), "?"), rank(kw, "blog"), (kw, "blog") in today)
         for kw in needs],
        key=lambda x: -(x[1] if x[1] is not None else -1))
    needs_hit = [r for r in needs_rows if r[3] is not None]
    needs_new = [r for r in needs_rows if not r[4]]        # 아직 수집 전

    brand = center.get("brand_keywords") or []
    brand_rows = sorted(
        [(kw, fvol(kw), vol.get((name, kw), "?"), rank(kw, "blog"), (kw, "blog") in today)
         for kw in brand],
        key=lambda x: -(x[1] if x[1] is not None else -1))

    # 플레이스 신호·전환 — 수기 기록 (없으면 섹션에서 안내만 띄운다)
    sig = [r for r in signals if r.get("center") == name]
    sig.sort(key=lambda r: r["date"])
    sig_now, sig_prev = (sig[-1] if sig else None), (sig[-2] if len(sig) > 1 else None)

    comps = center.get("competitors") or []

    def comp_rank(kw, aliases):
        """지역검색 상위 5위 안에서 경쟁사를 찾는다."""
        row = today.get((kw, "place"))
        if not row:
            return None
        keys = [nkey(a) for a in aliases if a]
        for i in range(1, 6):
            t = nkey(row.get(f"rank{i}") or "")
            if t and any(k and k in t for k in keys):
                return i
        return None

    def depth_of(kw):
        row = today.get((kw, "place"))
        return comp_depth(row) if row else 0

    versus = sorted(
        [(kw, vol.get((name, kw), "?"), rank(kw, "place"),
          [comp_rank(kw, c.get("aliases") or [c["label"]]) for c in comps], depth_of(kw))
         for kw in keywords],
        key=lambda x: -(fvol(x[0]) if fvol(x[0]) is not None else -1))
    tally = [{"win": 0, "lose": 0, "unknown": 0} for _ in comps]
    behind = []                                  # 경쟁사가 우리보다 앞선 키워드 (검색량 순)
    for kw, vlabel, our, cranks, depth in versus:
        for i, cr in enumerate(cranks):
            verdict = h2h(our, cr, depth)
            tally[i][verdict or "unknown"] += 1
            if verdict == "lose":
                behind.append((kw, vlabel, our, comps[i]["label"], cr))
    focus = center.get("focus_keywords") or []
    focus_hit = sum(1 for kw in focus if rank(kw, "place") or rank(kw, "blog"))

    # 추이 — 블로그에서 이틀 이상 순위가 잡힌 키워드만 (오늘 처음 측정된 키워드는 선이 그려지지 않는다)
    def blog_history(kw):
        ys = []
        for d in days:
            row = next((r for r in rows if r["date"] == d and r["center"] == name
                        and r["keyword"] == kw and r["surface"] == "blog"), None)
            ys.append(int(row["our_rank"]) if row and row["our_rank"] else None)
        return ys

    series, new_today = [], []
    for kw, _, _ in blog_hit:
        ys = blog_history(kw)
        if sum(1 for v in ys if v) >= 2:
            series.append((kw, ys, [C["s2"], C["s1"], "#1baf7a"][len(series) % 3]))
        else:
            new_today.append(kw)
        if len(series) == 3:
            break

    # 어제는 잡혔는데 오늘 빠진 키워드
    dropped = []
    for kw in keywords:
        for surface, sname in (("place", "플레이스"), ("blog", "블로그")):
            was = prev.get((kw, surface), "")
            if was and not rank(kw, surface):
                dropped.append(f"{kw}({sname} {was}위 → 범위 밖)")

    def change(kw, surface, now):
        p = prev.get((kw, surface), "")
        if not p:
            return "<span class='new'>첫 측정</span>"
        if int(p) == now:
            return "<span class='same'>변동 없음</span>"
        diff = int(p) - now
        return (f"<span class='up'>{abs(diff)}계단 상승</span>" if diff > 0
                else f"<span class='down'>{abs(diff)}계단 하락</span>")

    # ── 플레이스 신호 · 전환 (수기 기록) ──────────────────────────────
    def sig_row(key, label, unit=""):
        now = (sig_now or {}).get(key) or ""
        was = (sig_prev or {}).get(key) or ""
        if not now:
            return f"<tr><td>{label}</td><td class='num'>기록 없음</td><td></td></tr>"
        delta = ""
        if was:
            try:
                d = float(now) - float(was)
            except ValueError:
                d = 0.0
            cls = "up" if d > 0 else ("down" if d < 0 else "same")
            sign = "+" if d > 0 else ""
            txt = f"{d:.2f}".rstrip("0").rstrip(".") if "." in str(now) else f"{int(d)}"
            delta = f"<span class='{cls}'>{sign}{txt}</span>"
        return (f"<tr><td>{label}</td><td class='num'>{esc(now)}{unit}</td>"
                f"<td class='num'>{delta}</td></tr>")

    if sig_now:
        sig_date = esc(sig_now["date"])
        sig_note = f'<p class="note">기록 {len(sig)}회 · 최근 {sig_date}'
        if sig_now.get("note"):
            sig_note += f" · 메모: {esc(sig_now['note'])}"
        sig_note += "</p>"
    else:
        sig_date = "최근"
        sig_note = (
            '<div class="box" style="border-left-color:#e34948">'
            "<b>아직 기록이 없습니다.</b><br>"
            "이 값이 없으면 순위가 왜 움직였는지 알 수 없고, 순위가 매출로 이어졌는지도 "
            "확인할 수 없습니다. 스마트플레이스 &gt; 통계에서 리뷰·저장 수를, 자체 집계에서 "
            "체험신청 수를 주 1회 넣어 주세요.<br>"
            f"<code>python src/log_signals.py {name} --reviews 000 "
            "--blog-reviews 00 --saves 000 --trials 0</code></div>")

    signal_block = f"""
<div class="page">
<h2>플레이스 신호 · 전환</h2>
<p class="note">순위는 <b>결과</b>일 뿐입니다. 네이버 플레이스 순위를 움직이는 건 리뷰 수·최신성,
저장(찜) 수, 예약·문의 수이고, 순위가 매출이 됐는지는 체험신청 수로만 확인됩니다.
리뷰·별점은 <code>src/fetch_signals.py</code> 가 플레이스 페이지에서 자동으로 읽고,
저장(찜)·체험신청은 페이지에 없거나 내부 데이터라 <code>src/log_signals.py</code> 로 수기 입력합니다.</p>
<table><tr><th>항목</th><th class="num">{sig_date}</th><th class="num">지난 기록 대비</th></tr>
{sig_row('visitor_reviews', '방문자 리뷰 (누적)')}
{sig_row('blog_reviews', '블로그 리뷰 (누적)')}
{sig_row('rating', '별점')}
{sig_row('saves', '저장(찜) 수 — 수기')}
{sig_row('trials', '체험신청 (그 주) — 수기', '건')}
</table>
{sig_note}
</div>""" if show_signals else ""

    brand_sub = ""
    if brand:
        brand_sub = f"""
<h3>브랜드 키워드</h3>
<p class="note">우리 이름으로 찾는 사람입니다. 여기서 우리 글이 안 나오면 이미 우리를 아는 사람조차
경쟁사 글을 먼저 보게 됩니다.</p>
{needs_chart(brand_rows, depth=bdepth)}"""

    if needs:
        needs_block = f"""
<div class="page">
<h2>니즈 키워드 — 블로그</h2>
<p class="note">지역명 없이 <b>필요·증상·목적</b>으로 검색하는 키워드입니다. 지역검색(플레이스)에는
안 잡히므로 블로그만 봅니다. 검색량은 위 지역 키워드와 같은 스케일이라 바로 비교됩니다.</p>
{needs_chart(needs_rows, depth=bdepth)}
{brand_sub}
<div class="box"><b>노출 {len(needs_hit)} / 측정 {len(needs_rows) - len(needs_new)}개</b>
{f' · 미측정 {len(needs_new)}개 (추적에 막 추가돼 다음 수집부터 채워집니다)' if needs_new else ''}<br>
전국 단위 키워드라 경쟁이 상권이 아니라 전국입니다. 지역 키워드만큼 바로 전환되지는 않지만,
검색량이 지역 키워드보다 수십 배 크기 때문에 블로그 자산으로 쌓을 가치가 있습니다.</div>
</div>"""
    else:
        needs_block = ""

    if comps:
        # 핵심 키워드만 따로 — 매출에 가장 직접적인 구간
        fset = set(focus)
        focus_rows = [v for v in versus if v[0] in fset]
        ftally = [{"win": 0, "lose": 0, "unknown": 0} for _ in comps]
        for kw, vlabel, our, cranks, depth in focus_rows:
            for i, cr in enumerate(cranks):
                ftally[i][h2h(our, cr, depth) or "unknown"] += 1
        fsummary = "<br>".join(
            f"· <b>{esc(c['label'])}</b> — 우리 우위 {t['win']} · 열위 {t['lose']} · 판정 보류 {t['unknown']}"
            for c, t in zip(comps, ftally))
        flost = [f"{kw}({cl} {cr}위 / 우리 {str(our) + '위' if our else '5위밖'})"
                 for kw, v, our, cranks, depth in focus_rows
                 for cl, cr in ((comps[i]["label"], cranks[i]) for i in range(len(comps)))
                 if h2h(our, cr, depth) == "lose"]
        rows_t = "".join(
            f"<tr><td>{esc(c['label'])}</td>"
            f"<td class='num'><b>{t['win']}</b></td><td class='num'>{t['lose']}</td>"
            f"<td class='num'>{t['unknown']}</td></tr>"
            for c, t in zip(comps, tally))
        rows_b = "".join(
            f"<tr><td>{esc(kw)}</td><td class='num'>{esc(v)}</td>"
            f"<td class='num'>{str(our) + '위' if our else '5위밖'}</td>"
            f"<td>{esc(cl)}</td><td class='num'>{cr}위</td></tr>"
            for kw, v, our, cl, cr in behind)
        versus_block = f"""
<div class="page">
<h2>경쟁사 비교 — 플레이스</h2>
<p class="note">같은 키워드에서 우리와 경쟁사가 각각 몇 위인지입니다. 양쪽 모두 <b>5위까지</b> 확인하며,
"5위밖"은 없다는 뜻이 아니라 그 아래여서 모른다는 뜻입니다.</p>

<h3>핵심 키워드 {len(focus_rows)}개</h3>
<p class="note">회원이 실제로 가장 많이 검색하는 키워드입니다. 여기 순위가 매출로 바로 이어집니다.</p>
{versus_chart(focus_rows, comps)}
<div class="box">{fsummary}
{f'<br><br><b>핵심 키워드에서 밀린 곳</b><br>{esc(" · ".join(flost))}' if flost else '<br><br>핵심 키워드에서 경쟁사에 밀린 곳은 없습니다.'}</div>

<h3>전체 키워드 {len(versus)}개</h3>
{versus_chart(versus, comps)}

<h3>승패 요약</h3>
<table><tr><th>경쟁사</th><th class="num">우리 우위</th><th class="num">열위</th><th class="num">판정 보류</th></tr>
{rows_t}</table>
<p class="note">판정 보류 = 양쪽 다 5위 밖이라 누가 앞선지 알 수 없는 키워드입니다.</p>

<h3>경쟁사가 앞선 키워드 {len(behind)}개</h3>
<table class="miss"><tr><th>키워드</th><th class="num">검색량</th><th class="num">우리</th><th>경쟁사</th><th class="num">상대 순위</th></tr>
{rows_b or "<tr><td colspan='5'>없습니다.</td></tr>"}</table>
</div>"""
    else:
        versus_block = ""

    h = [f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<title>{esc(label)} 노출 현황</title><style>
@page {{ size: A4; margin: 16mm 14mm 14mm; }}
* {{ box-sizing: border-box; }}
body {{ font-family: 'Malgun Gothic','맑은 고딕',system-ui,sans-serif; color: {C['text']};
       background: {C['surface']}; margin: 0; font-size: 12px; line-height: 1.6; }}
h1 {{ font-size: 21px; margin: 0 0 4px; letter-spacing: -0.02em; }}
h2 {{ font-size: 14px; margin: 26px 0 4px; padding-bottom: 5px; border-bottom: 2px solid {C['text']}; }}
h3 {{ font-size: 12.5px; margin: 16px 0 6px; color: {C['sub']}; }}
.sub {{ color: {C['sub']}; font-size: 11.5px; margin: 0 0 14px; }}
.cards {{ display: flex; gap: 8px; margin: 14px 0 6px; }}
.card {{ flex: 1; border: 1px solid {C['line']}; border-radius: 8px; padding: 10px 12px; }}
.card .k {{ font-size: 10.5px; color: {C['muted']}; }}
.card .v {{ font-size: 20px; font-weight: 700; letter-spacing: -0.02em; }}
.card .n {{ font-size: 10.5px; color: {C['sub']}; }}
.note {{ font-size: 11px; color: {C['sub']}; margin: 4px 0 12px; }}
table {{ border-collapse: collapse; width: 100%; font-size: 11.5px; margin: 6px 0 4px; }}
th, td {{ border-bottom: 1px solid {C['grid']}; padding: 5px 6px; text-align: left; }}
thead {{ display: table-header-group; }}
tr {{ break-inside: avoid; }}
h3 {{ break-after: avoid; }}
th {{ color: {C['muted']}; font-weight: 600; font-size: 10.5px; }}
td.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
.miss td {{ color: {C['sub']}; }}
.up {{ color: #008300; }} .down {{ color: #e34948; }}
.same, .new {{ color: {C['muted']}; }}
.box {{ border: 1px solid {C['line']}; border-left: 3px solid {C['s1']}; border-radius: 6px;
        padding: 10px 12px; font-size: 11px; color: {C['sub']}; margin-top: 10px; }}
.box b {{ color: {C['text']}; }}
.empty {{ color: {C['muted']}; font-size: 11.5px; }}
.page {{ page-break-after: always; }}
footer {{ margin-top: 14px; font-size: 10px; color: {C['muted']}; }}
</style></head><body>

<div class="page">
<h1>{esc(label)} — 네이버 노출 현황</h1>
<p class="sub">기준일 {esc(day)} · 추적 키워드 {len(keywords)}개 · 자동 수집(네이버 검색 API)</p>

<div class="cards">
  <div class="card"><div class="k">플레이스 노출</div>
    <div class="v">{len(place_hit)}<span style="font-size:13px;color:{C['muted']}"> / {len(keywords)}</span></div>
    <div class="n">상위 5위 안에 든 키워드</div></div>
  <div class="card"><div class="k">블로그 노출</div>
    <div class="v">{len(blog_hit)}<span style="font-size:13px;color:{C['muted']}"> / {len(keywords)}</span></div>
    <div class="n">{bdepth}위 안에 든 키워드</div></div>
  <div class="card"><div class="k">주요 키워드 노출</div>
    <div class="v">{focus_hit}<span style="font-size:13px;color:{C['muted']}"> / {len(focus)}</span></div>
    <div class="n">검색량 상위 {len(focus)}개 중</div></div>
</div>

<h2>플레이스 순위</h2>
<p class="note">네이버 지역검색 순위입니다. 확인 범위는 <b>5위까지</b> — 그 아래는 "5위밖"으로만 나옵니다.</p>
{dot_chart(place_hit)}

<h2>블로그 순위</h2>
<p class="note">블로그 검색에서 우리 글의 순위입니다. 확인 범위는 <b>{bdepth}위까지</b>. 막대가 짧을수록 좋습니다.</p>
{bar_chart(blog_hit, depth=bdepth)}
</div>

<div class="page">
<h2>검색량 대비 노출</h2>
<p class="note">검색량이 많은 키워드부터 세운 표입니다. <b>위쪽 줄이 회색일수록 시급한 문제</b>입니다.
검색량은 이 센터 안에서만 쓰는 상대값이라 다른 센터와 비교하면 안 됩니다.</p>
{coverage_chart(cover, depth=bdepth)}
</div>

{versus_block}
{needs_block}
{signal_block}

<div class="page">
<h2>블로그 순위 추이</h2>
<p class="note">{esc(days[0])}부터 {esc(day)}까지, 이틀 이상 순위가 잡힌 키워드의 변화입니다. 위로 갈수록 순위가 높습니다.</p>
{trend_chart(days, series) or "<p class='empty'>이틀 이상 순위가 잡힌 키워드가 아직 없습니다. 추적 키워드를 늘린 직후라 대부분 오늘이 첫 측정입니다.</p>"}
{f'<p class="note">오늘 처음 순위가 잡혀 추이가 없는 키워드: {esc(", ".join(new_today))}</p>' if new_today else ''}
{f'<div class="box" style="border-left-color:#e34948"><b>어제 대비 순위에서 빠진 키워드</b><br>{esc(" · ".join(dropped))}<br>하루 변동일 수 있어 다음 측정치를 한 번 더 봐야 판단이 됩니다.</div>' if dropped else ''}

<h2>상세</h2>
<h3>플레이스 — 노출 {len(place_hit)}개</h3>
<table><tr><th>키워드</th><th class="num">순위</th><th>어제 대비</th><th>그 키워드 1위 업체</th></tr>
{''.join(f"<tr><td>{esc(kw)}</td><td class='num'>{r}위</td><td>{change(kw,'place',r)}</td><td>{esc(t1)}</td></tr>" for kw, r, t1 in place_hit)}
</table>
<h3>플레이스 — 상위 5위 밖 {len(place_miss)}개</h3>
<table class="miss"><tr><th>키워드</th><th class="num">검색량</th><th>그 키워드 1위 업체</th></tr>
{''.join(f"<tr><td>{esc(kw)}</td><td class='num'>{esc(v)}</td><td>{esc(t1) or '검색 결과 없음'}</td></tr>" for kw, v, t1 in place_miss)}
</table>

<h3>블로그 — 노출 {len(blog_hit)}개</h3>
<table><tr><th>키워드</th><th class="num">순위</th><th>어제 대비</th><th>잡힌 글</th></tr>
{''.join(f"<tr><td>{esc(kw)}</td><td class='num'>{r}위</td><td>{change(kw,'blog',r)}</td><td>{esc(t[:38])}</td></tr>" for kw, r, t in blog_hit)}
</table>
<h3>블로그 — {bdepth}위 안에 없음 {len(blog_miss)}개</h3>
<table class="miss"><tr><th>키워드</th><th class="num">검색량</th></tr>
{''.join(f"<tr><td>{esc(kw)}</td><td class='num'>{esc(v)}</td></tr>" for kw, v in blog_miss)}
</table>

{f'<div class="box" style="border-left-color:#8a8981"><b>아직 측정되지 않은 키워드 {len(unmeasured)}개</b><br>{esc(" · ".join(unmeasured))}<br>추적에 막 추가돼 다음 수집부터 순위가 채워집니다. 표에서는 "미측정"으로 표시됩니다.</div>' if unmeasured else ''}

<div class="box">
<b>읽을 때 주의할 점</b><br>
· 플레이스 순위는 지역검색 API 기준입니다. 실제 지도 앱 순위는 사용자 위치에 따라 달라지므로 절대 순위가 아니라 추세로 읽습니다.<br>
· 순위가 비어 있다는 건 "노출 0"이 아니라 "확인한 범위(플레이스 5위·블로그 {bdepth}위) 안에 없었다"는 뜻입니다.<br>
· 블로그 순위는 검색 API(정확도순) 기준이라 네이버 앱 화면 순위와 다를 수 있습니다 (2026-10-02 확인: API로 100위 밖이던 글이 앱 제목 검색 1위). 절대 순위가 아니라 추세로 읽습니다.<br>
· 검색량은 네이버 데이터랩 12개월 상대값입니다. 센터 안에서만 비교합니다.
</div>
<footer>naver-rank-tracker · data/rankings.csv · 생성 {date.today().isoformat()}</footer>
</div>
</body></html>"""]
    page = "".join(h)
    # 표 머리글이 페이지마다 반복되도록 첫 행을 thead 로 감싼다
    page = re.sub(r"<table([^>]*)>\s*(<tr>.*?</tr>)",
                  lambda m: f"<table{m.group(1)}><thead>{m.group(2)}</thead>",
                  page, flags=re.S)
    return label, page


def to_pdf(html_path: Path, pdf_path: Path) -> bool:
    code = f"""
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page()
    pg.goto({str(html_path.as_uri())!r})
    pg.pdf(path={str(pdf_path)!r}, format="A4", print_background=True)
    b.close()
"""
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    if r.returncode:
        print(r.stderr[-600:])
    return r.returncode == 0


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    web = "--web" in sys.argv
    rows, days, day, cfg, vol, signals = load(args[0] if args else None)
    if web:
        WEB_OUTDIR.mkdir(parents=True, exist_ok=True)
        for center in cfg["centers"]:
            label, page = build_center(center, rows, days, day, vol, show_signals=False)
            hp = WEB_OUTDIR / f"{center['name']}.html"
            page = page.replace(
                "<body>",
                '<body>\n<p style="max-width:760px;margin:16px auto 0;'
                'padding:0 16px;font-size:13px"><a href="../">← 대시보드</a></p>',
                1,
            )
            hp.write_text(page, encoding="utf-8")
            print(f"{label}: {hp.relative_to(ROOT)}")
        return
    OUTDIR.mkdir(exist_ok=True)
    for center in cfg["centers"]:
        label, page = build_center(center, rows, days, day, vol, signals)
        slug = re.sub(r"[^가-힣A-Za-z0-9]+", "", label.split("(")[0])
        hp = OUTDIR / f"{day}_{slug}_노출현황.html"
        pp = hp.with_suffix(".pdf")
        hp.write_text(page, encoding="utf-8")
        ok = to_pdf(hp, pp)
        print(f"{label}: {hp.name} {'→ ' + pp.name if ok else '(PDF 변환 실패)'}")


if __name__ == "__main__":
    main()
