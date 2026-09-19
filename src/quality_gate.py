#!/usr/bin/env python3
"""M3 — 품질 게이트.

발행 직전에 초안을 기계적으로 검사한다. 하나라도 걸리면 발행하지 않는다.
여기서 보는 것들은 전부 "네이버가 저품질로 분류하는 패턴"에서 역산했다:
   짧고 얄팍한 글 / 소제목 없는 벽글 / 1차 데이터 없는 일반론 /
   키워드 쑤셔넣기 / 내가 쓴 다른 글과 똑같은 글 / AI 상투어

사용:
    python src/quality_gate.py content/drafts/2026-09-20-철산하이록스.md
    → 통과하면 exit 0, 실패하면 사유 출력 후 exit 1
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "content.yml"
PUBLISHED = ROOT / "content" / "published"

WORD_RE = re.compile(r"[가-힣A-Za-z0-9]+")
FACT_RE = re.compile(r"\[fact:([a-zA-Z0-9_\-]+)\]")


def split_frontmatter(raw: str) -> tuple[dict, str]:
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            return yaml.safe_load(parts[1]) or {}, parts[2].strip()
    return {}, raw.strip()


def jaccard(a: str, b: str) -> float:
    sa, sb = set(WORD_RE.findall(a)), set(WORD_RE.findall(b))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def check(path: Path, cfg: dict) -> list[str]:
    """실패 사유 목록을 돌려준다. 빈 목록이면 통과."""
    q = cfg["quality"]
    meta, body = split_frontmatter(path.read_text(encoding="utf-8"))
    fails: list[str] = []

    # 1. 길이
    n = len(body)
    if n < q["min_chars"]:
        fails.append(f"너무 짧다: {n}자 (최소 {q['min_chars']})")
    if n > q["max_chars"]:
        fails.append(f"너무 길다: {n}자 (최대 {q['max_chars']})")

    # 2. 구조 — 소제목
    sections = re.findall(r"^##\s+\S", body, re.MULTILINE)
    if len(sections) < q["min_sections"]:
        fails.append(f"소제목 부족: {len(sections)}개 (최소 {q['min_sections']})")

    # 3. 구조 — 표 또는 목록 (AI 브리핑이 뽑아가기 쉬운 형태)
    if q.get("require_table_or_list"):
        has_table = "|" in body and re.search(r"^\|.*\|", body, re.MULTILINE)
        has_list = re.search(r"^\s*(?:[-*]|\d+\.)\s+\S", body, re.MULTILINE)
        if not (has_table or has_list):
            fails.append("표도 목록도 없다 — AI 브리핑이 인용하기 어려운 구조")

    # 4. 1차 데이터 인용
    facts = set(FACT_RE.findall(body))
    if len(facts) < q["min_facts"]:
        fails.append(f"사실 바구니 인용 부족: {len(facts)}개 (최소 {q['min_facts']})")

    # 5. 첫 문단 직답 — 인사말로 시작하면 실패
    first = next((ln.strip() for ln in body.splitlines()
                  if ln.strip() and not ln.startswith("#")), "")
    if re.match(r"^(안녕하세요|반갑습니다|오늘은|여러분)", first):
        fails.append(f"첫 문단이 인사말로 시작한다: \"{first[:30]}…\"")

    # 6. AI 상투어
    hit = [p for p in q["banned_phrases"] if p in body]
    if hit:
        fails.append(f"AI 상투어: {', '.join(hit)}")

    # 7. 키워드 과다 반복
    kw = (meta.get("keyword") or "").strip()
    if kw:
        words = WORD_RE.findall(body)
        density = body.count(kw) * len(WORD_RE.findall(kw)) / max(1, len(words))
        if density > q["max_keyword_density"]:
            fails.append(
                f"키워드 '{kw}' 반복 과다: {density:.1%} (상한 {q['max_keyword_density']:.0%})")

    # 8. 내가 쓴 기존 글과의 유사도 — 자기복제 방지
    if PUBLISHED.exists():
        for prev in PUBLISHED.glob("*.md"):
            if prev.name == path.name:
                continue
            _, prev_body = split_frontmatter(prev.read_text(encoding="utf-8"))
            sim = jaccard(body, prev_body)
            if sim > q["max_similarity"]:
                fails.append(f"기존 글과 유사: {prev.name} (자카드 {sim:.2f})")

    return fails


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("사용: python src/quality_gate.py <초안.md>")
    path = Path(sys.argv[1])
    if not path.exists():
        sys.exit(f"파일 없음: {path}")

    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    fails = check(path, cfg)

    if fails:
        print(f"✗ 품질 게이트 실패 — {path.name}")
        for f in fails:
            print(f"   · {f}")
        sys.exit(1)
    print(f"✓ 품질 게이트 통과 — {path.name}")


if __name__ == "__main__":
    main()
