#!/usr/bin/env python3
"""센터별 주간 노출 리포트.

data/rankings.csv · data/blog_posts.csv · config/targets.yml 로 센터마다 한 페이지를 만든다.
대시보드(docs/index.html)가 '오늘 하루'를 보여 준다면, 이 보고서는 '이번 주 7일 vs 지난주 7일'을 본다.
블로그 순위는 하루에도 크게 출렁이므로(예: 하이록스 1위 → 62위 → 16위) 주간 대표 순위로 판단한다.

- 이번 주 요약 · 지표 카드(최근 4주 추이) · 다음 할 일(규칙 기반)
- 키워드별 주간 순위 · 날짜별 순위 지도 · 우리 글 성과(blog_posts.csv) · 상위 자리 점유
- 블로그 확인 범위가 크게 바뀐 날(2026-10-14 등) 전의 기록은 순위를 비교할 수 없어 쓰지 않는다

차트는 외부 라이브러리 없이 HTML·inline SVG로 그린다 (대시보드와 같은 색 토큰, 다크 모드 포함).

사용:
    python src/build_report.py                 # 가장 최근 날짜로 → reports/<날짜>_<센터>_노출현황.html / .pdf
    python src/build_report.py 2026-10-26      # 날짜 지정
    python src/build_report.py --web           # GitHub Pages용 docs/reports/<센터>.html (GitHub Actions가 매일 실행)
--web 은 플레이스 신호·전환 섹션을 넣지 않는다 — 저장소와 사이트가 공개라 경영 지표를 올리지 않는다
(.gitignore 의 place_signals.csv 와 같은 이유).
"""
from __future__ import annotations

import csv
import html
import math
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import yaml

from build_dashboard import depth_marks, norm, post_key, to_rank

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "data" / "rankings.csv"
POSTS_PATH = ROOT / "data" / "blog_posts.csv"
CONFIG = ROOT / "config" / "targets.yml"
OUTDIR = ROOT / "reports"
WEB_OUTDIR = ROOT / "docs" / "reports"
SIGNALS = ROOT / "data" / "place_signals.csv"   # 수기 기록 (src/log_signals.py)

WEEK = 7          # 주간 비교 단위 (달력 기준 일수)
SPAN = 28         # 추이 차트·순위 지도에 보여 줄 최대 수집일 수 (약 4주)
LIST_MAX = 5      # '다음 할 일' 상자마다 보여 줄 키워드 수
SHARE_MAX = 7     # 상위 자리 점유 표에 보여 줄 다른 업체·블로그 수 (우리는 항상 넣는다)
# 순위 지도 구간 — 한 색상 5단계 (validate_palette.js --ordinal 라이트·다크 통과)
BANDS = ((1, "1위"), (3, "2~3위"), (10, "4~10위"), (30, "11~30위"), (None, "31위~"))
WEEKDAY = "월화수목금토일"


def esc(s) -> str:
    return html.escape("" if s is None else str(s))


def attr(tip_html: str) -> str:
    """data-tip 속성값 — 안쪽 HTML은 이미 esc() 된 것이어야 한다."""
    return html.escape(tip_html, quote=True)


def md(d: str) -> str:
    """2026-10-27 → 10/27"""
    return f"{int(d[5:7])}/{int(d[8:10])}"


def mdw(d: str) -> str:
    """2026-10-27 → 10/27(화)"""
    return f"{md(d)}({WEEKDAY[date.fromisoformat(d).weekday()]})"


def fmt_vol(v: str | None) -> str:
    if v is None:
        return "–"
    f = float(v)
    return f"{f:,.0f}" if f >= 10 or f == int(f) else f"{f:g}"


def band(r: int) -> int:
    for i, (hi, _) in enumerate(BANDS, start=1):
        if hi is None or r <= hi:
            return i
    return len(BANDS)


def median_rank(vals: list[int | None]) -> int | None:
    """주간 대표 순위 — 미노출(None)은 확인 범위 밖으로 보고 줄 세운 가운데값.
    짝수 개면 낮은(나쁜) 쪽을 쓴다 (좋게 보이지 않게). 가운데값이 범위 밖이면 None."""
    seq = sorted(v if v else math.inf for v in vals)
    if not seq:
        return None
    m = seq[len(seq) // 2]
    return None if m == math.inf else int(m)


def load(day: str | None):
    rows = list(csv.DictReader(CSV_PATH.open(encoding="utf-8")))
    all_dates = sorted({r["date"] for r in rows})
    day = day or all_dates[-1]
    if day not in all_dates:
        sys.exit(f"{day} 수집 기록이 없습니다 (기록: {all_dates[0]} ~ {all_dates[-1]}).")
    rows = [r for r in rows if r["date"] <= day]
    dates = [d for d in all_dates if d <= day]
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
    posts = ([p for p in csv.DictReader(POSTS_PATH.open(encoding="utf-8")) if p["date"] <= day]
             if POSTS_PATH.exists() else [])
    signals = (list(csv.DictReader(SIGNALS.open(encoding="utf-8")))
               if SIGNALS.exists() else [])
    return rows, dates, day, cfg, vol, posts, signals


def windows(rows: list[dict], dates: list[str], day: str):
    """비교 가능한 기간의 시작일, 이번 주·지난주 수집일, 추이에 쓸 수집일.
    블로그 확인 범위가 크게 바뀐 날(depth_marks) 전후는 순위를 비교할 수 없으므로 그날부터만 쓴다."""
    marks = [m["date"] for m in depth_marks(rows, dates)]
    start = marks[-1] if marks else dates[0]
    base = date.fromisoformat(day)

    def ago(d: str) -> int:
        return (base - date.fromisoformat(d)).days

    this_w = [d for d in dates if d >= start and 0 <= ago(d) < WEEK]
    last_w = [d for d in dates if d >= start and WEEK <= ago(d) < 2 * WEEK]
    span = [d for d in dates if d >= start][-SPAN:]
    return start, this_w, last_w, span


# ── 차트 ─────────────────────────────────────────────────────────────────

def xpos(ds: list[str]) -> list[float]:
    """수집일 → 가로 위치(%) — 달력 기준이라 수집이 빠진 날은 간격이 넓어진다."""
    if len(ds) < 2:
        return [50.0] * len(ds)
    d0, d1 = date.fromisoformat(ds[0]), date.fromisoformat(ds[-1])
    span = max(1, (d1 - d0).days)
    return [(date.fromisoformat(d) - d0).days / span * 100 for d in ds]


def nice_max(m: float) -> int:
    for t in (4, 6, 10, 12, 20, 30, 40, 60, 80, 100, 200):
        if m <= t:
            return t
    return int(math.ceil(m / 100) * 100)


def line_chart(ds: list[str], ys: list[float], ymax: int, week_from: str | None,
               tips: list[str]) -> str:
    """반응형 선 차트 — 선은 비율을 고정하지 않은 SVG, 글자·점은 HTML이라 폭이 바뀌어도 글자 크기가 그대로다.
    세로 축은 0부터 (개수라 0이 기준선), 이번 주 구간은 옅게 칠한다."""
    xs = xpos(ds)
    py = lambda v: 100 - v / ymax * 100                                           # noqa: E731
    pts = " ".join(f"{x:.2f},{py(y):.2f}" for x, y in zip(xs, ys))
    grid = "".join(f"<line x1='0' x2='100' y1='{py(t):.2f}' y2='{py(t):.2f}' class='g{' base' if t == 0 else ''}' "
                   f"vector-effect='non-scaling-stroke'/>" for t in (0, ymax / 2, ymax))
    ylab = "".join(f"<span class='yl' style='top:{py(t):.2f}%'>{t:g}</span>" for t in (0, ymax / 2, ymax))
    shade = ""
    if week_from and week_from in ds and len(ds) > 1:
        i = ds.index(week_from)
        x0 = (xs[i - 1] + xs[i]) / 2 if i else 0
        shade = f"<rect x='{x0:.2f}' y='0' width='{100 - x0:.2f}' height='100' class='wk'/>"
    # 마우스를 올릴 칸 — 이웃 점과의 가운데까지. 올리면 세로선(s)과 그날 점(b)이 보인다
    hits = []
    for i, (x, tip) in enumerate(zip(xs, tips)):
        lo = (xs[i - 1] + x) / 2 if i else 0
        hi = (x + xs[i + 1]) / 2 if i < len(xs) - 1 else 100
        at = (x - lo) / max(hi - lo, 0.01) * 100
        hits.append(f"<i class='hit' style='left:{lo:.2f}%;width:{hi - lo:.2f}%' data-tip='{attr(tip)}'>"
                    f"<s style='left:{at:.1f}%'></s><b style='left:{at:.1f}%;top:{py(ys[i]):.2f}%'></b></i>")
    # 가로 라벨 — 처음 · 이번 주 시작 · 오늘 (이번 주 시작은 양끝과 충분히 떨어져 있을 때만)
    labs = {0: ds[0]}
    if week_from in ds and 18 < xs[ds.index(week_from)] < 82:
        labs[ds.index(week_from)] = week_from
    labs[len(ds) - 1] = ds[-1]
    pos = lambda i: " class='end'" if i == len(ds) - 1 else (" class='start'" if i == 0 else "")  # noqa: E731
    xl = "".join(f"<span style='left:{xs[i]:.2f}%'{pos(i)}>{md(d)}</span>" for i, d in labs.items())
    lx, ly = xs[-1], py(ys[-1])
    return f"""<div class="lc">
  <div class="plot">{ylab}
    <svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">{shade}{grid}
      <polyline points="{pts}" class="ln" vector-effect="non-scaling-stroke"/></svg>
    <span class="dot" style="left:{lx:.2f}%;top:{ly:.2f}%"></span>
    {''.join(hits)}
  </div>
  <div class="xl">{xl}</div>
</div>"""


def range_strip(st: dict, prev: dict | None) -> str:
    """이번 주 순위 범위 — 로그 눈금(1·10·100·1000위). 선 = 최고~최저, 채운 점 = 이번 주 대표 순위,
    빈 점 = 지난주 대표 순위. 미노출인 날은 선에 넣지 않는다."""
    w, h, pad = 128, 22, 6
    x = lambda r: pad + math.log10(max(1, min(r, 1000))) / 3 * (w - 2 * pad)       # noqa: E731
    out = [f"<svg class='strip' width='{w}' height='{h}' viewBox='0 0 {w} {h}' aria-hidden='true'>"]
    for t in (1, 10, 100, 1000):
        out.append(f"<line x1='{x(t):.1f}' x2='{x(t):.1f}' y1='4' y2='{h - 4}' class='g'/>")
    if st["best"]:
        out.append(f"<line x1='{x(st['best']):.1f}' x2='{x(st['worst']):.1f}' y1='{h / 2}' y2='{h / 2}' "
                   f"class='rg'/>")
    if prev and prev["med"]:
        out.append(f"<circle cx='{x(prev['med']):.1f}' cy='{h / 2}' r='4' class='pm'/>")
    if st["med"]:
        out.append(f"<circle cx='{x(st['med']):.1f}' cy='{h / 2}' r='4.5' class='md'/>")
    out.append("</svg>")
    return "".join(out)


STRIP_AXIS = ("<svg class='strip' width='128' height='12' viewBox='0 0 128 12' aria-hidden='true'>"
              + "".join(f"<text x='{6 + math.log10(t) / 3 * 116:.1f}' y='10' text-anchor='middle'>{t}</text>"
                        for t in (1, 10, 100))
              + "</svg>")


# ── 센터 한 곳 ───────────────────────────────────────────────────────────

def build_center(center: dict, ci: int, rows, dates, day, vol, posts, signals, web: bool):
    name, label = center["name"], center.get("label", center["name"])
    local = list(center["keywords"])
    needs = list(center.get("needs_keywords") or [])
    brand = list(center.get("brand_keywords") or [])
    allkw = local + needs + brand
    focus = [k for k in center.get("focus_keywords") or [] if k in local]
    core = [k for k in center.get("core_keywords") or [] if k in allkw]
    notes = {str(k): str(v) for k, v in (center.get("keyword_notes") or {}).items() if str(k) in allkw}
    note_no = {kw: i for i, kw in enumerate([k for k in allkw if k in notes], start=1)}
    our_keys = {norm(a) for a in center.get("place_aliases") or []}
    comp_keys = {c["label"]: [norm(a) for a in (c.get("aliases") or [c["label"]])]
                 for c in center.get("competitors") or []}

    start, this_w, last_w, span = windows(rows, dates, day)

    look = {(r["surface"], r["keyword"], r["date"]): r for r in rows if r["center"] == name}

    def obs(s, kw, d):
        return look.get((s, kw, d))

    def rk(s, kw, d):
        r = obs(s, kw, d)
        return to_rank(r["our_rank"]) if r else None

    depth = max([int(r["checked_depth"] or 0) for (s, _, d), r in look.items()
                 if s == "blog" and d == day] or [0]) or 300
    stamps = sorted(r["collected_at"] for (_, _, d), r in look.items() if d == day and r.get("collected_at"))
    collected = f" · 수집 {stamps[0][11:16]}~{stamps[-1][11:16]}" if stamps else ""

    def vnum(kw):
        v = vol.get((name, kw))
        return float(v) if v is not None else -1.0

    def week(s, kw, ds):
        got = [rk(s, kw, d) for d in ds if obs(s, kw, d)]
        hits = [v for v in got if v]
        return {"n": len(got), "hit": len(hits), "med": median_rank(got),
                "best": min(hits) if hits else None, "worst": max(hits) if hits else None}

    st = {("blog", k): week("blog", k, this_w) for k in allkw}
    st.update({("place", k): week("place", k, this_w) for k in local})
    pv = {("blog", k): week("blog", k, last_w) for k in allkw}
    pv.update({("place", k): week("place", k, last_w) for k in local})
    has_prev = bool(last_w)

    # ── 우리 글 (blog_posts.csv) ─────────────────────────────────────────
    cposts = [p for p in posts if p["center"] == name]
    first_seen: dict[str, str] = {}
    ours_pos: dict[tuple[str, str], set[int]] = defaultdict(set)      # (키워드, 날짜) → 우리 글 순위들
    link_of: dict[tuple[str, int], str] = {}                           # 오늘 (키워드, 순위) → 글 주소
    for p in cposts:
        k = post_key(p["link"])
        if k not in first_seen or p["date"] < first_seen[k]:
            first_seen[k] = p["date"]
        ours_pos[(p["keyword"], p["date"])].add(int(p["rank"]))
        if p["date"] == day:
            link_of[(p["keyword"], int(p["rank"]))] = p["link"]

    # ── 지표 (날짜별) ───────────────────────────────────────────────────
    def kpi(d):
        blog = [rk("blog", k, d) for k in allkw]
        return {
            "coreTop10": sum(1 for k in core if (v := rk("blog", k, d)) and v <= 10),
            "blogTop10": sum(1 for v in blog if v and v <= 10),
            "blogFirst": sum(1 for v in blog if v == 1),
            "blogSeen": sum(1 for v in blog if v),
            "placeTop5": sum(1 for k in local if rk("place", k, d)),
        }

    k_by = {d: kpi(d) for d in set(span) | set(this_w) | set(last_w) | {day}}

    def avg(ds, key):
        return sum(k_by[d][key] for d in ds) / len(ds) if ds else None

    metrics = ([("coreTop10", "◆ 핵심 키워드 · 블로그 10위 안", len(core),
                 "우리 수업과 바로 이어지는 키워드")] if core else []) + [
        ("blogTop10", "블로그 10위 안", len(allkw), "첫 화면 근처에 보이는 키워드"),
        ("blogFirst", "블로그 1위", len(allkw), ""),
        ("blogSeen", "블로그 노출", len(allkw), f"서로 다른 글 약 {depth}개 안"),
        ("placeTop5", "플레이스 5위 안", len(local), "네이버가 5위까지만 알려 줌"),
    ]

    def delta_html(a, b):
        if a is None or b is None:
            return ""
        d = a - b
        if abs(d) < 0.05:
            return "<span class='muted'>같음</span>"
        cls, arrow = ("up", "▲") if d > 0 else ("down", "▼")
        return f"<span class='{cls}'>{arrow}{abs(d):.1f}</span>"

    # 주석 달린 키워드(경쟁 없는 1위 등)가 오늘 집계에 들어가면 숫자는 그대로 두고 카드에 밝힌다
    def noted_today(key):
        ok = {"blogFirst": lambda v: v == 1, "blogTop10": lambda v: v <= 10,
              "coreTop10": lambda v: v <= 10}.get(key)
        pool = core if key == "coreTop10" else allkw
        return [k for k in pool if ok and k in notes and (v := rk("blog", k, day)) and ok(v)]

    cards = []
    for key, title, total, hint in metrics:
        ys = [k_by[d][key] for d in span]
        tips = [f"<b>{esc(mdw(d))}</b>{esc(title.lstrip('◆ '))} {k_by[d][key]}개" for d in span]
        noted = noted_today(key)
        chart = (line_chart(span, ys, nice_max(max(ys + [1])), this_w[0] if this_w else None, tips)
                 if len(span) > 1 else "")
        a, b = avg(this_w, key), avg(last_w, key)
        cards.append(f"""<div class="card{' core' if key == 'coreTop10' else ''}">
  <div class="k">{esc(title)}</div>
  <div class="v">{k_by[day][key]}<small> / {total}</small></div>
  <div class="d"><span>이번 주 평균 <b>{a:.1f}</b></span>{f" · <span>지난주 {b:.1f} " + delta_html(a, b) + "</span>" if b is not None else ""}</div>
  {chart}
  <div class="h">{esc(hint)}{(' · ' if hint else '') + '※ ' + esc(', '.join(noted)) + ' 포함' if noted else ''}</div>
</div>""")

    # ── 변화 판정 (대표 순위 기준) ────────────────────────────────────────
    ups, downs, newly, gone, stable, volatile = [], [], [], [], [], []
    for kw in allkw:
        a, b = pv[("blog", kw)], st[("blog", kw)]
        if b["n"] and b["hit"] == b["n"] and b["worst"] <= 10:
            stable.append(kw)
        if b["hit"] >= 3 and b["best"] <= 10 and b["worst"] >= max(30, b["best"] * 5):
            volatile.append(kw)
        if not (a["n"] and b["n"]):
            continue
        if a["med"] and b["med"]:
            if a["med"] - b["med"] >= 3 and a["med"] / b["med"] >= 1.5:
                ups.append((kw, a["med"], b["med"]))
            elif b["med"] - a["med"] >= 3 and b["med"] / a["med"] >= 1.5:
                downs.append((kw, a["med"], b["med"]))
        elif b["med"] and not a["med"]:
            newly.append((kw, b["med"]))
        elif a["med"] and not b["med"]:
            gone.append((kw, a["med"]))
    ups.sort(key=lambda t: -math.log(t[1] / t[2]))
    downs.sort(key=lambda t: -math.log(t[2] / t[1]))
    volatile.sort(key=lambda k: -st[("blog", k)]["worst"] / st[("blog", k)]["best"])

    week_keys = {post_key(p["link"]) for p in cposts if p["date"] in this_w}
    last_keys = {post_key(p["link"]) for p in cposts if p["date"] in last_w}
    new_keys = {k for k in week_keys if first_seen.get(k, "") in this_w}

    def kws(items, fmt, cap=4):
        head = " · ".join(fmt(i) for i in items[:cap])
        return head + (f" 외 {len(items) - cap}개" if len(items) > cap else "")

    def mark(kw):
        return f"{kw}※" if kw in notes else kw

    bullets = []
    if has_prev:
        if ups:
            bullets.append(f"<b>오른 키워드</b> — {esc(kws(ups, lambda t: f'{mark(t[0])} {t[1]}→{t[2]}위'))}")
        if downs:
            bullets.append(f"<b>내린 키워드</b> — {esc(kws(downs, lambda t: f'{mark(t[0])} {t[1]}→{t[2]}위'))}")
        if newly:
            bullets.append(f"<b>새로 잡힌 키워드</b> — {esc(kws(newly, lambda t: f'{mark(t[0])} {t[1]}위'))}")
        if gone:
            bullets.append(f"<b>빠진 키워드</b> — {esc(kws(gone, lambda t: f'{mark(t[0])} (지난주 {t[1]}위)'))}")
        if not (ups or downs or newly or gone):
            bullets.append("대표 순위가 크게 바뀐 키워드는 없습니다.")
    else:
        bullets.append("비교할 지난주 기록이 없어, 오르내림은 다음 주부터 나옵니다.")
    if stable:
        bullets.append(f"<b>이번 주 매일 10위 안</b> {len(stable)}개 — {esc(kws(stable, mark, 6))}")
    if volatile:
        bullets.append(f"<b>크게 출렁인 키워드</b> {len(volatile)}개 — " + esc(kws(volatile, lambda k: (
            f"{mark(k)} {st[('blog', k)]['best']}~{st[('blog', k)]['worst']}위"))) + ". 하루 숫자보다 대표 순위로 보세요.")
    pt, pl = avg(this_w, "placeTop5"), avg(last_w, "placeTop5")
    bullets.append(f"<b>플레이스</b> — 5위 안 키워드 하루 평균 {pt:.1f}개"
                   + (f" (지난주 {pl:.1f}개)" if pl is not None else "") + f" / 지역 키워드 {len(local)}개")
    bullets.append(f"<b>우리 글</b> — 이번 주 검색에 잡힌 글 {len(week_keys)}편"
                   + (f" (지난주 {len(last_keys)}편)" if has_prev else "")
                   + (f", 그중 이번 주 처음 잡힌 글 {len(new_keys)}편" if new_keys else ""))

    # ── 다음 할 일 (규칙 기반) ───────────────────────────────────────────
    def wk_line(kw):
        b = st[("blog", kw)]
        if b["med"]:
            return f"대표 {b['med']}위 (최고 {b['best']}위)"
        return f"최고 {b['best']}위, 대표 순위는 범위 밖" if b["best"] else "이번 주 미노출"

    def top1_holder(kw):
        c = Counter((obs("place", kw, d) or {}).get("rank1", "") for d in this_w)
        c.pop("", None)
        return c.most_common(1)[0][0] if c else ""

    by_vol = sorted(allkw, key=lambda k: (-vnum(k), k not in core))
    gap = [k for k in by_vol if k not in brand and st[("blog", k)]["n"]
           and (st[("blog", k)]["med"] is None or st[("blog", k)]["med"] > 30)][:LIST_MAX]
    edge = [k for k in by_vol if st[("blog", k)]["med"] and 11 <= st[("blog", k)]["med"] <= 20][:LIST_MAX]
    pgap = [k for k in sorted(local, key=lambda k: -vnum(k))
            if st[("place", k)]["n"] and not st[("place", k)]["hit"]][:LIST_MAX]
    bgap = [k for k in brand if st[("blog", k)]["n"] and st[("blog", k)]["med"] != 1]

    def kwcell(kw):
        m = ("<span class='core-mark'>◆</span>" if kw in core else "") + ("<span class='star'>★</span>" if kw in focus else "")
        fn = f"<sup class='fn'>※{note_no[kw]}</sup>" if kw in notes else ""
        return f"{m}{' ' if m else ''}{esc(kw)}{fn}"

    def act_box(title, lead, items, line):
        if not items:
            return ""
        lis = "".join(f"<li><span class='kw'>{kwcell(k)}</span><span class='meta'>{line(k)}</span></li>" for k in items)
        return f"<div class='box act'><h4>{title}</h4><p>{lead}</p><ul>{lis}</ul></div>"

    vol_s = lambda k: f"검색량 {fmt_vol(vol.get((name, k)))}"                      # noqa: E731
    acts = "".join([
        act_box("글이 더 필요한 키워드",
                "검색량에 비해 블로그에서 우리 글이 30위 밖이거나 안 보입니다. 이 키워드를 주제로 한 새 글 후보입니다.",
                gap, lambda k: f"{vol_s(k)} · {esc(wk_line(k))}"),
        act_box("첫 화면 문턱 (11~20위)",
                "조금만 오르면 10위 안입니다. 이미 잡힌 글을 보강하거나 같은 주제 글을 이어 쓰는 후보입니다.",
                edge, lambda k: f"{vol_s(k)} · {esc(wk_line(k))}"),
        act_box("플레이스 5위 밖",
                "이번 주 하루도 플레이스 5위 안에 들지 못한 지역 키워드입니다 (검색량 순). 그 자리의 1위 업체를 같이 적었습니다.",
                pgap, lambda k: f"{vol_s(k)}" + (f" · 1위 {esc(top1_holder(k))}" if top1_holder(k) else "")),
        act_box("출렁이는 키워드",
                "이번 주 순위 폭이 큽니다. 하루 숫자로 오르내림을 판단하지 말고 대표 순위로 보세요.",
                volatile[:LIST_MAX], lambda k: esc(f"{st[('blog', k)]['best']}~{st[('blog', k)]['worst']}위 · "
                                                  f"대표 {st[('blog', k)]['med'] or '범위 밖'}"
                                                  + ("위" if st[('blog', k)]['med'] else ""))),
        act_box("브랜드 검색",
                "우리 이름으로 찾았는데 우리 글이 1위가 아닙니다. 이미 우리를 아는 사람이 다른 글을 먼저 봅니다.",
                bgap, lambda k: f"{vol_s(k)} · {esc(wk_line(k))}"),
    ]) or "<div class='box'><div class='empty'>이번 주 따로 손볼 곳이 보이지 않습니다.</div></div>"

    # ── 키워드별 주간 순위 표 ────────────────────────────────────────────
    groups = [
        ("core", "<span class='core-mark'>◆</span> 센터 핵심 키워드", [k for k in allkw if k in core]),
        ("focus", "<span class='star'>★</span> 검색량 상위 키워드", [k for k in focus if k not in core]),
        ("local", "그 외 지역 키워드", [k for k in local if k not in core and k not in focus]),
        ("needs", "니즈 키워드 — 지역명이 없어 블로그만 봅니다", [k for k in needs if k not in core]),
        ("brand", "브랜드 키워드 — 블로그만 봅니다", [k for k in brand if k not in core]),
    ]
    groups = [g for g in groups if g[2]]

    def place_cell(kw):
        if kw not in local:
            return "<span class='miss'>—</span>"
        s, r = st[("place", kw)], rk("place", kw, day)
        now = f"<b>{r}위</b>" if r else "<span class='miss'>5위 밖</span>"
        # 이번 주 한 번이라도 5위 안이었으면 며칠이었는지 — 내내 밖이면 적지 않는다
        return f"{now}<span class='sm'>주 {s['hit']}/{s['n']}일</span>" if s["hit"] else now

    def blog_cell(kw):
        r = obs("blog", kw, day)
        if not r:
            return "<span class='miss'>수집 전</span>"
        v = rk("blog", kw, day)
        tot = to_rank(r.get("api_total"))
        t = f" title='검색 결과 {tot:,}건'" if tot is not None else ""
        return f"<span{t}><b>{v}위</b></span>" if v else f"<span class='miss'{t}>미노출</span>"

    def med_cell(kw):
        b, a = st[("blog", kw)], pv[("blog", kw)]
        if not b["n"]:
            return "<span class='miss'>—</span>"
        now = f"<b>{b['med']}위</b>" if b["med"] else "<span class='miss'>범위 밖</span>"
        d = ""
        if has_prev and a["n"]:
            if a["med"] and b["med"] and a["med"] != b["med"]:
                diff = a["med"] - b["med"]
                d = (f"<span class='delta up'>▲{diff}</span>" if diff > 0
                     else f"<span class='delta down'>▼{-diff}</span>")
            elif b["med"] and not a["med"]:
                d = "<span class='delta up'>새로</span>"
            elif a["med"] and not b["med"]:
                d = f"<span class='delta down'>({a['med']}위)</span>"
        return now + d

    def strip_cell(kw):
        b, a = st[("blog", kw)], pv[("blog", kw)]
        if not b["n"]:
            return ""
        miss = b["n"] - b["hit"]
        tip = (f"<b>{esc(kw)} · 블로그 이번 주</b>"
               + (f"{b['best']}~{b['worst']}위" if b["best"] else "순위 없음")
               + f" · 대표 {str(b['med']) + '위' if b['med'] else '범위 밖'}"
               + (f"<br>지난주 대표 {str(a['med']) + '위' if a['med'] else '범위 밖'}" if has_prev and a["n"] else "")
               + (f"<br>미노출 {miss}일 / {b['n']}일" if miss else ""))
        return f"<span data-tip='{attr(tip)}'>{range_strip(b, a if has_prev else None)}</span>"

    def post_cell(kw):
        v = rk("blog", kw, day)
        r = obs("blog", kw, day)
        if not (v and r):
            return ""
        t = r.get("our_title", "")
        link = link_of.get((kw, v))
        return (f"<a href='{esc(link)}' target='_blank' rel='noopener' title='{esc(t)}'>{esc(t)}</a>"
                if link else f"<span title='{esc(t)}'>{esc(t)}</span>")

    trs = []
    for _, gtitle, kws_ in groups:
        trs.append(f"<tr class='grp'><td colspan='7'>{gtitle}</td></tr>")
        for kw in kws_:
            trs.append(f"<tr class='{'em' if kw in core or kw in focus else ''}'>"
                       f"<td class='kw'>{kwcell(kw)}</td>"
                       f"<td class='num vol'>{fmt_vol(vol.get((name, kw)))}</td>"
                       f"<td class='num'>{place_cell(kw)}</td>"
                       f"<td class='num'>{blog_cell(kw)}</td>"
                       f"<td class='num'>{med_cell(kw)}</td>"
                       f"<td class='rng'>{strip_cell(kw)}</td>"
                       f"<td class='post'>{post_cell(kw)}</td></tr>")
    note_foot = "".join(f"<br><span class='fn'>※{note_no[k]}</span> <b>{esc(k)}</b>: {esc(v)}"
                        for k, v in notes.items())
    kw_table = f"""<div class="tw"><table class="kt">
<thead><tr><th>키워드</th><th class="num vol">검색량</th><th class="num">플레이스 오늘</th>
<th class="num">블로그 오늘</th><th class="num">이번 주 대표</th>
<th class="rng">이번 주 범위{STRIP_AXIS}</th><th class="post">오늘 잡힌 우리 글</th></tr></thead>
<tbody>{''.join(trs)}</tbody></table></div>
<p class="foot"><b>이번 주 대표</b> = 이번 주 순위를 줄 세웠을 때 가운데 값 (미노출인 날은 맨 뒤로). 하루 튀는 값에 덜 흔들립니다.
▲▼는 지난주 대표 순위와 비교한 계단 수입니다.
<b>이번 주 범위</b> = 선이 이번 주 최고~최저 순위, <span class="key md"></span> 이번 주 대표,
<span class="key pm"></span> 지난주 대표 (왼쪽일수록 상위, 1·10·100위 간격을 같게 그렸습니다).
<b>플레이스 '주 n/m일'</b> = 이번 주 수집 m번 중 5위 안이었던 날 수.
검색량은 네이버 데이터랩 12개월 상대값이라 이 센터 안에서만 비교합니다.{note_foot}</p>"""

    # ── 날짜별 순위 지도 ─────────────────────────────────────────────────
    def heat(surface, groups_):
        head, prev_m = [], None
        for d in span:
            m = int(d[5:7])
            cls = " class='w0'" if this_w and d == this_w[0] else (" class='w'" if d in this_w else "")
            head.append(f"<th{cls}>{f'<small>{m}월</small>' if m != prev_m else ''}{int(d[8:10])}</th>")
            prev_m = m
        body = []
        for gtitle, kws_ in groups_:
            if gtitle:
                body.append(f"<tr class='grp'><td colspan='{len(span) + 1}'><span>{gtitle}</span></td></tr>")
            for kw in kws_:
                cells = []
                for d in span:
                    o = obs(surface, kw, d)
                    w0 = " w0" if this_w and d == this_w[0] else ""
                    if not o:
                        cells.append(f"<td class='na{w0}'></td>")
                        continue
                    v = rk(surface, kw, d)
                    sname = "플레이스" if surface == "place" else "블로그"
                    tip = f"<b>{esc(mdw(d))} · {esc(kw)}</b>{sname} " + (
                        f"{v}위" if v else ("5위 밖" if surface == "place" else "미노출"))
                    cells.append(f"<td class='{'r' + str(band(v)) if v else 'off'}{w0}' data-tip='{attr(tip)}'>"
                                 f"{v if v else '·'}</td>")
                body.append(f"<tr><th class='kw'>{kwcell(kw)}</th>{''.join(cells)}</tr>")
        return (f"<div class='hw'><table class='heat'><thead><tr><th class='kw'></th>{''.join(head)}</tr></thead>"
                f"<tbody>{''.join(body)}</tbody></table></div>")

    legend = "".join(f"<span><i class='r{i}'></i>{esc(lab)}</span>" for i, (_, lab) in enumerate(BANDS, start=1))
    legend += "<span><i class='off'></i>미노출 · 5위 밖</span><span><i class='na'></i>수집 안 함</span>"
    heat_blog = heat("blog", [(g[1], g[2]) for g in groups])
    heat_place = heat("place", [(None, [k for k in sorted(local, key=lambda k: -vnum(k))])])

    # ── 우리 글 성과 ─────────────────────────────────────────────────────
    today_posts: dict[str, dict] = {}
    for p in cposts:
        if p["date"] != day:
            continue
        e = today_posts.setdefault(post_key(p["link"]), {"title": p["title"], "link": p["link"],
                                                         "postdate": p["postdate"], "hits": []})
        e["hits"].append((int(p["rank"]), p["keyword"]))
    for e in today_posts.values():
        e["hits"].sort()
        e["top10"] = sum(1 for r, _ in e["hits"] if r <= 10)
    best_posts = sorted((e for e in today_posts.values() if e["top10"]),
                        key=lambda e: (-e["top10"], e["hits"][0][0]))[:8]

    def pdate(s):
        return f"{s[2:4]}.{s[4:6]}.{s[6:8]}" if len(s) == 8 else esc(s)

    def post_row(e):
        top = [f"{esc(k)} {r}위" for r, k in e["hits"] if r <= 10][:4]
        more = e["top10"] - len(top)
        return (f"<tr><td class='pt'><a href='{esc(e['link'])}' target='_blank' rel='noopener'>{esc(e['title'])}</a>"
                f"<span class='meta'>{' · '.join(top)}{f' 외 {more}개' if more > 0 else ''}</span></td>"
                f"<td class='num'>{pdate(e['postdate'])}</td>"
                f"<td class='num'><b>{e['top10']}</b></td><td class='num'>{len(e['hits'])}</td></tr>")

    # 이번 주 처음 잡힌 글 — 이번 주 가장 높은 순위 기준
    fresh: dict[str, dict] = {}
    for p in cposts:
        k = post_key(p["link"])
        if p["date"] not in this_w or k not in new_keys:
            continue
        r = int(p["rank"])
        e = fresh.setdefault(k, {"title": p["title"], "link": p["link"], "postdate": p["postdate"],
                                 "first": first_seen[k], "best": r, "kw": p["keyword"], "kws": set()})
        e["kws"].add(p["keyword"])
        if r < e["best"]:
            e["best"], e["kw"] = r, p["keyword"]
    fresh_list = sorted(fresh.values(), key=lambda e: (e["best"], e["first"]))

    def fresh_li(e):
        return (f"<li><a href='{esc(e['link'])}' target='_blank' rel='noopener'>{esc(e['title'])}</a>"
                f"<span class='meta'>{pdate(e['postdate'])} 발행 · {md(e['first'])} 처음 잡힘 · "
                f"최고 {esc(e['kw'])} {e['best']}위 · 키워드 {len(e['kws'])}개</span></li>")

    fresh_html = ("<ul>" + "".join(fresh_li(e) for e in fresh_list[:6]) + "</ul>"
                  + (f"<details><summary>{len(fresh_list) - 6}편 더 보기</summary><ul>"
                     + "".join(fresh_li(e) for e in fresh_list[6:]) + "</ul></details>" if len(fresh_list) > 6 else "")
                  ) if fresh_list else "<div class='empty'>이번 주 처음 잡힌 글이 없습니다.</div>"
    posts_html = f"""<div class="tw"><table class="pt-t">
<thead><tr><th>글 (오늘 10위 안 키워드)</th><th class="num">발행</th><th class="num">10위 안</th><th class="num">잡힌 키워드</th></tr></thead>
<tbody>{''.join(post_row(e) for e in best_posts) or "<tr><td colspan='4' class='empty'>오늘 10위 안에 든 글이 없습니다.</td></tr>"}</tbody></table></div>
<div class="box" style="margin-top:10px"><h4>이번 주 처음 검색에 잡힌 글 <span class="muted">{len(fresh_list)}</span></h4>{fresh_html}</div>"""

    # ── 상위 자리 점유 ───────────────────────────────────────────────────
    def is_ours(title):
        t = norm(title)
        return bool(t) and any(k == t or k in t for k in our_keys)

    def comp_of(title):
        t = norm(title)
        return next((lab for lab, keys in comp_keys.items() if any(k and k in t for k in keys)), None)

    def place_share(ds):
        tot = defaultdict(lambda: [0, 0])
        for d in ds:
            for kw in local:
                r = obs("place", kw, d)
                if not r:
                    continue
                for pos in range(1, 6):
                    t = (r.get(f"rank{pos}") or "").strip()
                    if t:
                        e = tot[label if is_ours(t) else t]
                        e[0] += 1
                        e[1] += pos == 1
        n = len(ds) or 1
        return {k: (v[0] / n, v[1] / n) for k, v in tot.items()}

    def blog_share(ds):
        tot: Counter = Counter()
        for d in ds:
            for kw in allkw:
                r = obs("blog", kw, d)
                if not r:
                    continue
                mine = {p for p in ours_pos.get((kw, d), set()) if p <= 5}
                if not mine and (v := rk("blog", kw, d)) and v <= 5:
                    mine = {v}
                for pos in range(1, 6):
                    if pos in mine:
                        tot[label] += 1
                    elif t := (r.get(f"rank{pos}") or "").strip():
                        tot[t] += 1
        n = len(ds) or 1
        return {k: v / n for k, v in tot.items()}

    def share_rows(now: dict, before: dict, val, fmt):
        """val(값 또는 None) → 막대 길이에 쓸 숫자. 우리는 순위와 상관없이 항상 넣는다."""
        others = sorted((k for k in now if k != label), key=lambda k: -val(now[k]))[:SHARE_MAX]
        shown = sorted(others + [label], key=lambda k: -val(now.get(k)))
        top = max([val(now.get(k)) for k in shown] + [1])
        out = []
        for k in shown:
            v = now.get(k)
            x = val(v)
            d = ""
            if has_prev:
                dv = x - val(before.get(k))
                d = ("<span class='muted'>같음</span>" if abs(dv) < 0.05 else
                     f"<span class='{'up' if dv > 0 else 'down'}'>{'▲' if dv > 0 else '▼'}{abs(dv):.1f}</span>")
            ours = k == label
            comp = None if ours else comp_of(k)
            cls = "ours" if ours else ("comp" if comp else "")
            tag = "<span class='tag'>우리</span>" if ours else ("<span class='tag'>경쟁사</span>" if comp else "")
            out.append(f"<tr class='{cls}'><td class='nm'><div><span title='{esc(k)}'>{esc(k)}</span>{tag}</div></td>"
                       f"<td class='bar'><i style='width:{x / top * 100:.1f}%'></i></td>"
                       f"<td class='num'>{fmt(v)}</td><td class='num'>{d}</td></tr>")
        return "".join(out)

    ps_now, ps_prev = place_share(this_w), place_share(last_w)
    bs_now, bs_prev = blog_share(this_w), blog_share(last_w)
    place_tbl = share_rows(ps_now, ps_prev, lambda v: v[0] if v else 0,
                           lambda v: (f"<b>{v[0]:.1f}</b><span class='sm'>그중 1위 {v[1]:.1f}</span>" if v else "0"))
    blog_slots = 5 * len(allkw)
    blog_tbl = share_rows(bs_now, bs_prev, lambda v: v or 0,
                          lambda v: (f"<b>{v:.1f}</b><span class='sm'>{v / blog_slots * 100:.0f}%</span>" if v else "0"))
    share_html = f"""<div class="two">
<div class="tw"><table class="share"><caption>플레이스 — 지역 키워드 {len(local)}개의 5위 안 자리</caption>
<thead><tr><th>업체</th><th></th><th class="num">5위 안 (하루 평균)</th><th class="num">지난주 대비</th></tr></thead>
<tbody>{place_tbl}</tbody></table></div>
<div class="tw"><table class="share"><caption>블로그 — 키워드 {len(allkw)}개의 상위 5자리 (하루 {blog_slots}자리)</caption>
<thead><tr><th>블로그</th><th></th><th class="num">상위 5자리 (하루 평균)</th><th class="num">지난주 대비</th></tr></thead>
<tbody>{blog_tbl}</tbody></table></div>
</div>
<p class="foot">이번 주 수집일마다 센 자리 수의 평균입니다. 막대 색: <span class="key ours"></span> 우리 ·
<span class="key comp"></span> 경쟁사 (targets.yml에 지정한 곳) · <span class="key oth"></span> 그 외.
플레이스는 지점 이름이 다르면 다른 업체로 셉니다. 블로그 이름은 검색 결과에 보이는 블로그 이름 그대로입니다.</p>"""

    # ── 플레이스 신호 · 전환 (기본 모드만 — 수기 기록) ─────────────────────
    signal_block = ""
    if not web:
        sig = sorted((r for r in signals if r.get("center") == name), key=lambda r: r["date"])
        sig_now, sig_prev = (sig[-1] if sig else None), (sig[-2] if len(sig) > 1 else None)

        def sig_row(key, lab, unit=""):
            now = (sig_now or {}).get(key) or ""
            was = (sig_prev or {}).get(key) or ""
            if not now:
                return f"<tr><td>{lab}</td><td class='num'>기록 없음</td><td></td></tr>"
            delta = ""
            if was:
                try:
                    dd = float(now) - float(was)
                except ValueError:
                    dd = 0.0
                cls = "up" if dd > 0 else ("down" if dd < 0 else "muted")
                txt = f"{dd:.2f}".rstrip("0").rstrip(".") if "." in str(now) else f"{int(dd)}"
                delta = f"<span class='{cls}'>{'+' if dd > 0 else ''}{txt}</span>"
            return f"<tr><td>{lab}</td><td class='num'>{esc(now)}{unit}</td><td class='num'>{delta}</td></tr>"

        sig_note = (f"<p class='foot'>기록 {len(sig)}회 · 최근 {esc(sig_now['date'])}"
                    + (f" · 메모: {esc(sig_now['note'])}" if sig_now.get("note") else "") + "</p>") if sig_now else (
            "<div class='box warn'><b>아직 기록이 없습니다.</b> 스마트플레이스 &gt; 통계에서 리뷰·저장 수를, "
            "자체 집계에서 체험신청 수를 주 1회 넣어 주세요.<br>"
            f"<code>python src/log_signals.py {esc(name)} --reviews 000 --blog-reviews 00 --saves 000 --trials 0</code></div>")
        signal_block = f"""<section id="signals"><h2>플레이스 신호 · 전환</h2>
<p class="lead">순위는 결과입니다. 플레이스 순위를 움직이는 건 리뷰 수·최신성, 저장(찜) 수, 예약·문의 수이고,
순위가 매출이 됐는지는 체험신청 수로만 확인됩니다.</p>
<div class="tw"><table><thead><tr><th>항목</th><th class="num">{esc(sig_now['date']) if sig_now else '최근'}</th>
<th class="num">지난 기록 대비</th></tr></thead><tbody>
{sig_row('visitor_reviews', '방문자 리뷰 (누적)')}{sig_row('blog_reviews', '블로그 리뷰 (누적)')}
{sig_row('rating', '별점')}{sig_row('saves', '저장(찜) 수 — 수기')}{sig_row('trials', '체험신청 (그 주) — 수기', '건')}
</tbody></table></div>{sig_note}</section>"""

    # ── 표로 보기 (지표 날짜별) ───────────────────────────────────────────
    kpi_raw = ("<div class='tw'><table class='raw'><thead><tr><th>날짜</th>"
               + "".join(f"<th class='num'>{esc(t.lstrip('◆ '))}</th>" for _, t, _, _ in metrics)
               + "</tr></thead><tbody>"
               + "".join(f"<tr><td>{esc(mdw(d))}</td>" + "".join(f"<td class='num'>{k_by[d][k]}</td>"
                                                                  for k, _, _, _ in metrics) + "</tr>"
                         for d in reversed(span))
               + "</tbody></table></div>")

    week_txt = (f"{md(this_w[0])}~{md(this_w[-1])} 수집 {len(this_w)}번" if this_w else "없음")
    prev_txt = (f"{md(last_w[0])}~{md(last_w[-1])} 수집 {len(last_w)}번" if last_w else "비교할 기록 없음")
    accent = f"--accent:var(--c{ci % 2});--accent-bg:var(--c{ci % 2}-bg)"
    back = '<p class="back"><a href="../">← 대시보드</a></p>' if web else ""

    page = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(label)} 주간 리포트</title>
<style>{CSS}</style>
</head>
<body style="{accent}">
<div class="wrap">
{back}
<header>
  <h1>{esc(label)} <span>네이버 노출 주간 리포트</span></h1>
  <p class="sub"><b>{esc(mdw(day))}</b> 기준{collected} · 이번 주 = {week_txt} · 지난주 = {prev_txt}</p>
  <p class="sub">블로그 확인 범위가 바뀐 {md(start)}부터의 기록만 씁니다 (그 전 순위는 비교할 수 없음) ·
     <span class="core-mark">◆</span> 센터 핵심 키워드 · <span class="star">★</span> 검색량 상위 키워드</p>
  <nav class="toc"><a href="#summary">요약</a><a href="#todo">할 일</a><a href="#keywords">키워드</a><a href="#map">순위 지도</a><a href="#posts">우리 글</a><a href="#share">자리 점유</a>{'<a href="#signals">신호·전환</a>' if signal_block else ''}<a href="#notes">읽는 법</a></nav>
</header>

<section id="summary"><h2>이번 주 요약</h2>
<div class="box summary"><ul>{''.join(f'<li>{b}</li>' for b in bullets)}</ul>
<p class="foot" style="margin-top:6px">오르내림은 하루 숫자가 아니라 주간 대표 순위(7일 순위의 가운데 값)로 판단했습니다.</p></div>
<div class="cards">{''.join(cards)}</div>
<p class="foot">큰 숫자 = 오늘. 차트는 최근 {len(span)}번 수집, 옅게 칠한 구간이 이번 주입니다. 마우스를 올리면(또는 누르면) 그날 숫자가 보입니다.</p>
<details class="raw"><summary>날짜별 숫자 표로 보기</summary>{kpi_raw}</details>
</section>

<section id="todo"><h2>다음에 할 일</h2>
<p class="lead">이번 주 기록에서 규칙으로 뽑은 후보입니다. 검색량이 큰 키워드부터 적었습니다.</p>
<div class="acts">{acts}</div>
</section>

<section id="keywords"><h2>키워드별 주간 순위</h2>
<p class="lead">오늘 순위와 이번 주 대표 순위를 나란히 봅니다. 하루 숫자가 튀어도 대표 순위가 그대로면 실제 변화가 아닐 가능성이 큽니다.</p>
{kw_table}
</section>

<section id="map"><h2>날짜별 순위 지도</h2>
<p class="lead">칸 하나가 하루입니다. 진할수록 상위이고, 굵은 세로줄 오른쪽이 이번 주입니다. 칸 안 숫자가 그날 순위입니다.</p>
<div class="hl">{legend}</div>
<h3>블로그</h3>{heat_blog}
<h3>플레이스 (지역 키워드, 검색량 순)</h3>{heat_place}
</section>

<section id="posts"><h2>우리 글 성과</h2>
<p class="lead">오늘 10위 안 키워드를 가장 많이 가진 글부터입니다. 글 하나가 여러 키워드에서 동시에 잡힙니다.</p>
{posts_html}
</section>

<section id="share"><h2>상위 자리 점유</h2>
<p class="lead">우리 키워드의 상위 자리를 누가 얼마나 차지하는지 이번 주 평균으로 셉니다.</p>
{share_html}
</section>
{signal_block}
<section id="notes"><h2>읽는 법</h2>
<div class="box notes"><ul>
<li>플레이스 순위는 지역검색 API 기준이고 5위까지만 나옵니다. 실제 지도 앱 순위는 보는 사람 위치에 따라 달라지므로 추세로 읽습니다.</li>
<li>블로그는 서로 다른 글 약 {depth}개까지 확인합니다. '미노출'은 노출 0이 아니라 그 범위 안에 없었다는 뜻입니다.</li>
<li>블로그 순위는 검색 API(정확도순) 기준이라 네이버 앱 화면 순위와 다를 수 있습니다. 절대 순위보다 흐름으로 봅니다.</li>
<li>{md(start)}에 검색 API가 돌려주는 범위가 넓어져(약 380 → 880개) 그 전후 순위는 비교하지 않습니다.</li>
<li>'다음 할 일'은 숫자 규칙으로 뽑은 후보입니다. 글 주제·순서는 블로그 운영 계획에 맞춰 정합니다.</li>
</ul></div>
<footer>naver-rank-tracker · data/rankings.csv · data/blog_posts.csv · 생성 {date.today().isoformat()}</footer>
</section>
</div>
<div id="tip" role="tooltip"></div>
<script>{JS}</script>
</body>
</html>"""
    return label, page


CSS = """
:root {
  color-scheme: light;
  --surface-0:#f6f6f4; --surface-1:#fcfcfb; --border:#e3e2dd;
  --text-primary:#0b0b0b; --text-secondary:#52514e; --text-muted:#8a8981;
  --grid:#eceae5; --axis:#c3c2b7; --bar:#d9d7d1; --bar-comp:#8a8981;
  --star:#eda100; --up:#006300; --down:#c62f2f;
  --c0:#4f46e5; --c0-bg:#eef0fd; --c1:#0f8f7e; --c1-bg:#e8f6f3;
  /* 순위 지도 — 한 색상 5단계 (1위가 가장 진함) */
  --r1:#0d366b; --r2:#1c5cab; --r3:#2a78d6; --r4:#5598e7; --r5:#86b6ef;
  --r1-ink:#fff; --r2-ink:#fff; --r3-ink:#fff; --r4-ink:#0b0b0b; --r5-ink:#0b0b0b;
  --off:#efeeea;
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) {
    color-scheme: dark;
    --surface-0:#111110; --surface-1:#1a1a19; --border:#33332f;
    --text-primary:#ffffff; --text-secondary:#c3c2b7; --text-muted:#8f8e86;
    --grid:#2a2a27; --axis:#383835; --bar:#3a3a37; --bar-comp:#8f8e86;
    --star:#c98500; --up:#2fbf2f; --down:#ee7272;
    --c0:#8b85ff; --c0-bg:#1b1a2e; --c1:#2fc2ad; --c1-bg:#132522;
    --r1:#cde2fb; --r2:#86b6ef; --r3:#5598e7; --r4:#2a78d6; --r5:#184f95;
    --r1-ink:#0b0b0b; --r2-ink:#0b0b0b; --r3-ink:#0b0b0b; --r4-ink:#fff; --r5-ink:#fff;
    --off:#242422;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --surface-0:#111110; --surface-1:#1a1a19; --border:#33332f;
  --text-primary:#ffffff; --text-secondary:#c3c2b7; --text-muted:#8f8e86;
  --grid:#2a2a27; --axis:#383835; --bar:#3a3a37; --bar-comp:#8f8e86;
  --star:#c98500; --up:#2fbf2f; --down:#ee7272;
  --c0:#8b85ff; --c0-bg:#1b1a2e; --c1:#2fc2ad; --c1-bg:#132522;
  --r1:#cde2fb; --r2:#86b6ef; --r3:#5598e7; --r4:#2a78d6; --r5:#184f95;
  --r1-ink:#0b0b0b; --r2-ink:#0b0b0b; --r3-ink:#0b0b0b; --r4-ink:#fff; --r5-ink:#fff;
  --off:#242422;
}
* { box-sizing:border-box; }
body { margin:0; padding:20px 16px 64px; background:var(--surface-0); color:var(--text-primary);
  font-family:-apple-system,BlinkMacSystemFont,"Pretendard","Apple SD Gothic Neo","Malgun Gothic",sans-serif;
  -webkit-font-smoothing:antialiased; }
.wrap { max-width:1060px; margin:0 auto; }
a { color:inherit; }
.back { margin:0 0 14px; font-size:.82rem; }
.back a { text-decoration:none; color:var(--text-secondary); }
header { border-top:5px solid var(--accent); background:var(--accent-bg); border-radius:16px;
  padding:18px 18px 14px; border-left:1px solid var(--border); border-right:1px solid var(--border);
  border-bottom:1px solid var(--border); }
h1 { font-size:1.4rem; margin:0 0 6px; letter-spacing:-0.015em; color:var(--accent); }
h1 span { color:var(--text-primary); font-weight:600; font-size:1.05rem; margin-left:4px; }
.sub { color:var(--text-secondary); font-size:.8rem; margin:0 0 4px; line-height:1.55; }
.toc { display:flex; flex-wrap:wrap; gap:6px; margin-top:12px; }
.toc a { text-decoration:none; font-size:.76rem; font-weight:600; padding:5px 11px; border-radius:999px;
  color:var(--accent); background:var(--surface-1); border:1px solid var(--border); }
section { margin-top:34px; scroll-margin-top:12px; }
h2 { font-size:1.05rem; margin:0 0 4px; letter-spacing:-0.01em; }
h3 { font-size:.82rem; margin:18px 0 8px; color:var(--text-secondary); font-weight:650; }
.lead { font-size:.8rem; color:var(--text-secondary); margin:0 0 12px; line-height:1.55; }
.foot { font-size:.72rem; color:var(--text-muted); margin:8px 2px 0; line-height:1.6; }
.foot b { color:var(--text-secondary); }
.muted, .miss { color:var(--text-muted); }
.up { color:var(--up); font-weight:600; } .down { color:var(--down); font-weight:600; }
.star { color:var(--star); } .core-mark { color:var(--accent); }
.fn { color:var(--accent); font-size:.66rem; font-weight:650; margin-left:2px; }
.tag { display:inline-block; font-size:.64rem; font-weight:600; padding:0 6px; border-radius:999px;
  border:1px solid var(--border); color:var(--text-secondary); vertical-align:1px; white-space:nowrap; }
.empty { font-size:.78rem; color:var(--text-muted); }
.box { background:var(--surface-1); border:1px solid var(--border); border-radius:12px; padding:14px 16px; }
.box h4 { margin:0 0 6px; font-size:.84rem; }
.box ul { list-style:none; margin:0; padding:0; font-size:.8rem; line-height:1.55; }
.box li { padding:5px 0; border-top:1px solid var(--grid); }
.box li:first-child { border-top:0; }
.box .meta { display:block; font-size:.72rem; color:var(--text-muted); }
.box.warn { border-left:3px solid var(--down); font-size:.8rem; }
.summary li { padding:6px 0; }
.summary li b { color:var(--text-primary); }
.notes li { color:var(--text-secondary); }

/* 지표 카드 */
.cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(185px,1fr)); gap:10px; margin-top:10px; }
.card { background:var(--surface-1); border:1px solid var(--border); border-radius:12px; padding:13px 14px 10px; }
.card.core { border-color:var(--accent); box-shadow:inset 0 0 0 1px var(--accent); }
.card .k { font-size:.76rem; color:var(--text-secondary); font-weight:600; }
.card .v { font-size:1.75rem; font-weight:700; letter-spacing:-0.02em; margin-top:2px; white-space:nowrap; }
.card .v small { font-size:.85rem; font-weight:500; color:var(--text-muted); }
.card .d { font-size:.74rem; color:var(--text-secondary); }
.card .d > span { white-space:nowrap; }
.card .h { font-size:.7rem; color:var(--text-muted); margin-top:6px; }

/* 반응형 선 차트 */
.lc { margin-top:10px; }
.lc .plot { position:relative; height:88px; margin:4px 8px 0 26px; }
.lc svg { position:absolute; inset:0; width:100%; height:100%; overflow:visible; }
.lc .g { stroke:var(--grid); stroke-width:1; } .lc .g.base { stroke:var(--axis); }
.lc .wk { fill:var(--accent-bg); }
.lc .ln { fill:none; stroke:var(--accent); stroke-width:2; stroke-linejoin:round; stroke-linecap:round; }
.lc .yl { position:absolute; right:calc(100% + 6px); transform:translateY(-50%); font-size:.64rem;
  color:var(--text-muted); font-variant-numeric:tabular-nums; }
.lc .dot { position:absolute; width:9px; height:9px; margin:-4.5px 0 0 -4.5px; border-radius:50%;
  background:var(--accent); box-shadow:0 0 0 2px var(--surface-1); pointer-events:none; }
.lc .hit { position:absolute; top:0; bottom:0; cursor:crosshair; }
.lc .hit s { position:absolute; top:0; bottom:0; width:1px; background:var(--axis); opacity:0; }
.lc .hit b { position:absolute; width:9px; height:9px; margin:-4.5px 0 0 -4.5px; border-radius:50%;
  background:var(--accent); box-shadow:0 0 0 2px var(--surface-1); opacity:0; }
.lc .hit.on s, .lc .hit.on b { opacity:1; }
.lc .xl { position:relative; height:16px; margin:4px 8px 0 26px; font-size:.66rem; color:var(--text-muted); }
.lc .xl span { position:absolute; transform:translateX(-50%); white-space:nowrap; }
.lc .xl span.start { transform:none; } .lc .xl span.end { transform:translateX(-100%); }

/* 다음 할 일 */
.acts { display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:10px; }
.act p { font-size:.74rem; color:var(--text-secondary); margin:0 0 8px; line-height:1.5; }
.act .kw { font-weight:650; }

/* 표 */
.tw { background:var(--surface-1); border:1px solid var(--border); border-radius:12px; overflow-x:auto; }
table { width:100%; border-collapse:collapse; font-size:.8rem; }
caption { text-align:left; font-size:.8rem; font-weight:650; padding:12px 12px 4px; }
th, td { text-align:left; padding:7px 10px; border-bottom:1px solid var(--grid); vertical-align:middle; }
th { color:var(--text-secondary); font-weight:600; font-size:.71rem; white-space:nowrap; }
td.num, th.num { text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }
tbody tr:last-child td { border-bottom:0; }
tr.grp td { background:var(--surface-0); color:var(--text-secondary); font-size:.72rem; font-weight:650; padding:6px 10px; }
.kt td.kw { white-space:nowrap; } .kt tr.em td.kw { font-weight:650; }
.kt .vol { color:var(--text-muted); }
.kt .sm, .share .sm { display:block; font-size:.66rem; color:var(--text-muted); font-weight:400; }
.kt .delta { font-size:.7rem; margin-left:4px; }
.kt td.rng { width:140px; padding-top:4px; padding-bottom:4px; }
.kt th.rng svg { display:block; margin-top:2px; }
.kt td.post { max-width:280px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:.74rem; color:var(--text-muted); }
.kt td.post a { text-decoration:none; color:var(--text-secondary); }
.kt td.post a:hover { text-decoration:underline; }
.strip { display:block; overflow:visible; }
.strip .g { stroke:var(--grid); stroke-width:1; }
.strip text { font-size:9px; fill:var(--text-muted); }
.strip .rg { stroke:var(--accent); stroke-width:2; stroke-linecap:round; opacity:.45; }
.strip .md { fill:var(--accent); stroke:var(--surface-1); stroke-width:2; }
.strip .pm { fill:var(--surface-1); stroke:var(--text-muted); stroke-width:1.5; }
.key { display:inline-block; width:9px; height:9px; border-radius:50%; vertical-align:-1px; }
.key.md { background:var(--accent); } .key.pm { border:1.5px solid var(--text-muted); }
.key.ours, .key.comp, .key.oth { border-radius:2px; }
.key.ours { background:var(--accent); } .key.comp { background:var(--bar-comp); } .key.oth { background:var(--bar); }

/* 순위 지도 */
.hl { display:flex; flex-wrap:wrap; gap:6px 14px; font-size:.72rem; color:var(--text-secondary); margin-bottom:4px; }
.hl span { display:inline-flex; align-items:center; gap:6px; }
.hl i { width:14px; height:12px; border-radius:3px; display:inline-block; }
.hw { background:var(--surface-1); border:1px solid var(--border); border-radius:12px; overflow-x:auto; padding:6px; }
table.heat { border-collapse:separate; border-spacing:2px; width:auto; font-size:.66rem; }
.heat th, .heat td { border:0; padding:0; text-align:center; }
.heat thead th { font-weight:500; color:var(--text-muted); font-size:.62rem; padding:0 0 3px; vertical-align:bottom; min-width:26px; }
.heat thead th small { display:block; font-size:.58rem; }
.heat thead th.w, .heat thead th.w0 { color:var(--text-primary); font-weight:650; }
.heat th.kw { position:sticky; left:0; z-index:1; background:var(--surface-1); text-align:left; white-space:nowrap;
  font-size:.74rem; font-weight:500; color:var(--text-primary); padding:0 10px 0 4px; }
.heat td { width:26px; min-width:26px; height:22px; border-radius:4px; font-variant-numeric:tabular-nums; cursor:default; }
.heat tr.grp td { background:none; text-align:left; height:auto; padding:8px 4px 3px; font-size:.68rem; }
.heat tr.grp td span { position:sticky; left:4px; }
.heat td.w0, .heat th.w0 { box-shadow:-2px 0 0 0 var(--text-secondary); }
.r1 { background:var(--r1); color:var(--r1-ink); font-weight:700; }
.r2 { background:var(--r2); color:var(--r2-ink); } .r3 { background:var(--r3); color:var(--r3-ink); }
.r4 { background:var(--r4); color:var(--r4-ink); } .r5 { background:var(--r5); color:var(--r5-ink); }
td.off, i.off { background:var(--off); color:var(--text-muted); }
td.na, i.na { background:transparent; box-shadow:inset 0 0 0 1px var(--grid); }
.heat td.na.w0 { box-shadow:inset 0 0 0 1px var(--grid), -2px 0 0 0 var(--text-secondary); }
.heat td.on { outline:2px solid var(--text-primary); outline-offset:-1px; }

/* 우리 글 */
.pt-t td.pt a { font-weight:600; text-decoration:none; }
.pt-t td.pt a:hover { text-decoration:underline; }
.pt-t td.pt .meta { display:block; font-size:.7rem; color:var(--text-muted); margin-top:2px; }
.box a { text-decoration:none; font-weight:600; } .box a:hover { text-decoration:underline; }
details summary { cursor:pointer; font-size:.76rem; color:var(--text-secondary); padding-top:6px; }
details.raw { margin-top:10px; } details.raw .tw { margin-top:8px; max-height:420px; overflow:auto; }

/* 자리 점유 */
.two { display:grid; grid-template-columns:repeat(auto-fit,minmax(380px,1fr)); gap:10px; }
.share td.nm { max-width:200px; }
.share td.nm div { display:flex; align-items:center; gap:5px; min-width:0; }
.share td.nm span:first-child { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; min-width:0; }
.share td.nm .tag { flex:none; }
.share td.bar { width:34%; min-width:70px; }
.share td.bar i { display:block; height:12px; border-radius:0 4px 4px 0; background:var(--bar); min-width:2px; }
.share tr.comp td.bar i { background:var(--bar-comp); }
.share tr.ours td { background:var(--accent-bg); font-weight:650; }
.share tr.ours td.bar i { background:var(--accent); }

footer { margin-top:14px; font-size:.7rem; color:var(--text-muted); }

#tip { position:fixed; pointer-events:none; opacity:0; transition:opacity .1s; z-index:9;
  background:var(--surface-0); border:1px solid var(--border); border-radius:8px; padding:7px 10px;
  font-size:.75rem; color:var(--text-secondary); box-shadow:0 4px 16px rgba(0,0,0,.14); max-width:260px; line-height:1.5; }
#tip b { display:block; color:var(--text-primary); margin-bottom:2px; }

@media (max-width:640px) {
  body { padding:14px 12px 48px; }
  header { padding:14px 14px 12px; }
  h1 span { display:block; margin:2px 0 0; }
  .kt .rng, .kt .post, .kt .vol { display:none; }
  th, td { padding:7px 7px; }
  .two { grid-template-columns:1fr; }
  .share td.nm { max-width:118px; }
  .share td.bar { width:22%; min-width:40px; }
  .share th { white-space:normal; }
  .acts { grid-template-columns:1fr; }
}
@media print {
  @page { size:A4; margin:12mm 10mm; }
  body { background:#fff; padding:0; font-size:11px; }
  .toc, .back, #tip, details.raw { display:none; }
  section { break-inside:auto; margin-top:22px; }
  .card, .box, tr { break-inside:avoid; }
  h2, h3 { break-after:avoid; }
  .hw, .tw { overflow:visible; }
  /* 순위 지도는 A4 폭(약 700px)에 맞게 칸을 줄인다 — 4주(28칸)까지 들어간다 */
  .heat td, .heat thead th { width:19px; min-width:19px; height:17px; font-size:7.5px; }
  .heat th.kw { font-size:8.5px; padding-right:6px; }
  .kt .post { display:none; }   /* 글 제목은 '우리 글 성과'에 따로 나온다 */
  * { -webkit-print-color-adjust:exact; print-color-adjust:exact; }
}
"""

JS = """
const tip = document.getElementById('tip');
let on = null;
function place(x, y) {
  const w = tip.offsetWidth, h = tip.offsetHeight;
  tip.style.left = Math.max(8, Math.min(x + 14, innerWidth - w - 8)) + 'px';
  tip.style.top = (y - h - 12 < 8 ? y + 18 : y - h - 12) + 'px';
}
function show(el, x, y) {
  if (on !== el) {
    if (on) on.classList.remove('on');
    on = el; el.classList.add('on');
    tip.innerHTML = el.dataset.tip;
  }
  tip.style.opacity = '1';
  place(x, y);
}
function hide() { tip.style.opacity = '0'; if (on) on.classList.remove('on'); on = null; }
function track(e) {
  const el = e.target.closest && e.target.closest('[data-tip]');
  el ? show(el, e.clientX, e.clientY) : hide();
}
document.addEventListener('pointermove', track);
document.addEventListener('pointerdown', track);
addEventListener('scroll', hide, { passive: true });
"""


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
    rows, dates, day, cfg, vol, posts, signals = load(args[0] if args else None)
    if web:
        WEB_OUTDIR.mkdir(parents=True, exist_ok=True)
        for ci, center in enumerate(cfg["centers"]):
            label, page = build_center(center, ci, rows, dates, day, vol, posts, signals, web=True)
            hp = WEB_OUTDIR / f"{center['name']}.html"
            hp.write_text(page, encoding="utf-8")
            print(f"{label}: {hp.relative_to(ROOT).as_posix()}")
        return
    OUTDIR.mkdir(exist_ok=True)
    for ci, center in enumerate(cfg["centers"]):
        label, page = build_center(center, ci, rows, dates, day, vol, posts, signals, web=False)
        slug = re.sub(r"[^가-힣A-Za-z0-9]+", "", label.split("(")[0])
        hp = OUTDIR / f"{day}_{slug}_노출현황.html"
        pp = hp.with_suffix(".pdf")
        hp.write_text(page, encoding="utf-8")
        ok = to_pdf(hp, pp)
        print(f"{label}: {hp.name} {'→ ' + pp.name if ok else '(PDF 변환 실패)'}")


if __name__ == "__main__":
    main()
