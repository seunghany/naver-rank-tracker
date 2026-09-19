#!/usr/bin/env python3
"""M1 — 글감 발굴.

상위 노출 중인 블로그 글들의 '제목과 요약'을 API로 모아서,
어떤 각도가 이미 포화됐고 어떤 각도가 비어 있는지 센다.

본문은 수집하지 않는다. 이건 성능 한계가 아니라 의도다 —
남의 본문을 긁어와 리라이팅하면 유사문서로 묶여서 저품질로 간다.
우리가 노리는 건 "아무도 제대로 안 다룬 질문"이지 "1등 글의 재탕"이 아니다.

사용: python src/find_topics.py 철산하이록스 [키워드2 ...]
출력: content/briefs/<날짜>-<키워드>.json
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from naver_api import search_blog  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "content.yml"
OUTDIR = ROOT / "content" / "briefs"
KST = timezone(timedelta(hours=9))

WORD_RE = re.compile(r"[가-힣A-Za-z0-9]+")
STOP = {
    "그리고", "하지만", "그런데", "있는", "있습니다", "합니다", "입니다", "해서",
    "하는", "위해", "대한", "에서", "으로", "하고", "에게", "까지", "부터",
    "the", "and", "for", "with", "you", "your",
}


def tokens(text: str) -> list[str]:
    return [w for w in WORD_RE.findall(text.lower()) if len(w) > 1 and w not in STOP]


def angle_coverage(items: list[dict], angles: list[dict]) -> list[dict]:
    """각 각도가 상위글 중 몇 개에서 다뤄지는지 센다."""
    out = []
    for angle in angles:
        cues = [c.lower() for c in angle["cues"]]
        hits, examples = 0, []
        for it in items:
            blob = (it["title"] + " " + it["description"]).lower()
            if any(c in blob for c in cues):
                hits += 1
                if len(examples) < 2:
                    examples.append(it["title"][:60])
        out.append({
            "id": angle["id"],
            "label": angle["label"],
            "covered": hits,
            "coverage_pct": round(hits / max(1, len(items)) * 100, 1),
            "examples": examples,
        })
    return sorted(out, key=lambda a: a["covered"])


def recency(items: list[dict]) -> dict:
    """상위글이 얼마나 최신인지 — 오래된 글이 버티고 있으면 비집고 들어갈 틈이 있다."""
    today = datetime.now(KST).date()
    ages = []
    for it in items:
        d = it.get("postdate", "")
        if len(d) == 8 and d.isdigit():
            try:
                ages.append((today - datetime.strptime(d, "%Y%m%d").date()).days)
            except ValueError:
                pass
    if not ages:
        return {"n": 0}
    ages.sort()
    return {
        "n": len(ages),
        "median_days": ages[len(ages) // 2],
        "within_90d": sum(1 for a in ages if a <= 90),
        "over_1y": sum(1 for a in ages if a > 365),
    }


def build_brief(keyword: str, cfg: dict) -> dict:
    top_n = int(cfg["research"]["top_n"])
    items = search_blog(keyword, display=min(100, top_n))[:top_n]
    if not items:
        return {"keyword": keyword, "error": "검색 결과 없음"}

    title_words = Counter()
    for it in items:
        title_words.update(set(tokens(it["title"])))

    bloggers = Counter(it["blogger"] for it in items if it["blogger"])

    return {
        "keyword": keyword,
        "collected_at": datetime.now(KST).isoformat(timespec="seconds"),
        "n_analyzed": len(items),
        "recency": recency(items),
        # 이미 포화된 각도 vs 비어 있는 각도
        "angle_coverage": angle_coverage(items, cfg["research"]["angles"]),
        # 제목에서 반복되는 단어 = 이 키워드의 기본 문법
        "common_title_words": title_words.most_common(20),
        # 한 블로거가 상위를 독식하는지
        "top_bloggers": bloggers.most_common(5),
        # 상위 10개는 그대로 보여준다 (제목/요약만)
        "top10": [
            {k: it[k] for k in ("rank", "title", "description", "blogger", "postdate", "link")}
            for it in items[:10]
        ],
    }


def main() -> None:
    keywords = sys.argv[1:]
    if not keywords:
        sys.exit("사용: python src/find_topics.py <키워드> [키워드2 ...]")

    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    OUTDIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(KST).strftime("%Y-%m-%d")

    for kw in keywords:
        print(f"[{kw}] 상위글 분석 중…")
        brief = build_brief(kw, cfg)
        path = OUTDIR / f"{today}-{kw}.json"
        path.write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")

        if brief.get("error"):
            print(f"  ! {brief['error']}")
            continue
        gaps = [a for a in brief["angle_coverage"] if a["coverage_pct"] < 20]
        print(f"  분석 {brief['n_analyzed']}건 · 90일 내 글 {brief['recency'].get('within_90d', 0)}건")
        if gaps:
            print("  빈틈:", ", ".join(f"{g['label']}({g['coverage_pct']}%)" for g in gaps))
        else:
            print("  뚜렷한 빈틈 없음 — 경쟁이 빡빡한 키워드")
        print(f"  → {path}")


if __name__ == "__main__":
    main()
