#!/usr/bin/env python3
"""M2 — 초안 생성.

M1이 찾은 '빈틈 각도' + 사실 바구니를 넣고 LLM에게 초안을 쓰게 한다.

설계상 지켜지는 두 가지:
  1. 숫자·사례는 사실 바구니에 있는 것만 쓴다. 프롬프트에서 금지하고,
     품질 게이트가 [fact:id] 인용 개수를 세서 이중으로 막는다.
  2. 상위글 본문을 넣지 않는다. 제목·요약만 '이미 포화된 각도'를 피하는
     용도로 쓴다. 남의 본문을 넣는 순간 유사문서가 된다.

사용:
    export ANTHROPIC_API_KEY=sk-ant-...
    python src/write_draft.py content/briefs/2026-09-20-철산하이록스.json Reboot
출력: content/drafts/<날짜>-<키워드>.md
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "content.yml"
FACTS = ROOT / "content" / "facts"
OUTDIR = ROOT / "content" / "drafts"
KST = timezone(timedelta(hours=9))

SYSTEM = """당신은 네이버 블로그에 실을 글을 쓴다. 광고 카피가 아니라 정보 글이다.

절대 규칙
1. 숫자, 기간, 사례, 가격, 인원은 <사실> 목록에 있는 것만 쓴다.
   목록에 없는 수치는 절대 지어내지 않는다. 필요하면 그 문장을 빼라.
2. <사실>을 쓸 때는 문장 끝에 [fact:해당id] 를 붙인다. 최소 2개 이상 쓴다.
3. 남의 글을 요약하거나 바꿔 쓰지 않는다. <이미 포화된 각도>는 피하고
   <빈 각도>를 정면으로 다룬다.

형식
- 첫 문단: 제목의 질문에 2~3문장으로 바로 답한다. 인사말·서론 금지.
- 본문은 "## 소제목" 단위. 소제목 하나가 하위질문 하나에 대응한다. 3개 이상.
- 비교는 표로, 절차·목록은 번호나 불릿으로. 최소 하나는 반드시 넣는다.
- 마지막은 "## 요약" 으로 3줄 불릿.
- 분량 1,500~2,500자.

문체
- 담백한 평서문. 과장·감탄사·이모지 없음.
- 다음 표현은 쓰지 않는다: "알아보겠습니다", "여러분", "오늘은",
  "마무리하며", "살펴보겠습니다", "도움이 되셨길".
- 주 키워드를 억지로 반복하지 않는다. 자연스러우면 3~5회면 충분하다.

출력은 마크다운 본문만. 설명이나 머리말을 덧붙이지 않는다."""


def load_facts(center: str) -> list[dict]:
    path = FACTS / f"{center.lower()}.yml"
    if not path.exists():
        sys.exit(f"사실 바구니가 없습니다: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    facts = data.get("facts") or []
    if len(facts) < 2:
        sys.exit(f"사실 바구니에 항목이 {len(facts)}개뿐입니다. 최소 2개는 채우고 실행하세요: {path}")
    return facts


def build_prompt(brief: dict, facts: list[dict]) -> str:
    gaps = [a for a in brief["angle_coverage"] if a["coverage_pct"] < 25][:3]
    saturated = [a for a in brief["angle_coverage"] if a["coverage_pct"] >= 50][:4]

    fact_lines = "\n".join(
        f"- [{f['id']}] {f['fact']}" + (f"  (출처: {f['source']})" if f.get("source") else "")
        for f in facts
    )
    gap_lines = "\n".join(f"- {g['label']} — 상위 {brief['n_analyzed']}개 중 {g['covered']}개만 다룸"
                          for g in gaps) or "- (뚜렷한 빈틈 없음 — 깊이로 승부해야 함)"
    sat_lines = "\n".join(f"- {s['label']} ({s['coverage_pct']}%)" for s in saturated) or "- 없음"
    titles = "\n".join(f"- {t['title']}" for t in brief["top10"][:8])

    rec = brief.get("recency", {})
    age_note = ""
    if rec.get("n"):
        age_note = (f"\n상위글 신선도: 중앙값 {rec['median_days']}일 전, "
                    f"90일 내 {rec['within_90d']}건, 1년 초과 {rec['over_1y']}건")

    return f"""주 키워드: {brief['keyword']}

<빈 각도> — 이 중 하나를 골라 글의 중심으로 삼는다
{gap_lines}

<이미 포화된 각도> — 중심 주제로 삼지 않는다
{sat_lines}

<상위 노출 중인 제목들> — 비슷하게 쓰지 말라는 참고용이다. 내용은 보지 못했고 볼 필요도 없다.
{titles}{age_note}

<사실> — 숫자와 사례는 여기 있는 것만 쓴다
{fact_lines}

위 조건으로 블로그 글 하나를 써라. 제목은 첫 줄에 "# "로 시작한다."""


def call_anthropic(system: str, prompt: str, cfg: dict) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        sys.exit("ANTHROPIC_API_KEY 가 없습니다. console.anthropic.com 에서 발급하세요.")
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": cfg["llm"]["model"], "max_tokens": cfg["llm"]["max_tokens"],
              "system": system, "messages": [{"role": "user", "content": prompt}]},
        timeout=180,
    )
    if r.status_code != 200:
        sys.exit(f"LLM 호출 실패 HTTP {r.status_code}: {r.text[:300]}")
    return "".join(b.get("text", "") for b in r.json().get("content", []))


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit("사용: python src/write_draft.py <brief.json> <Reboot|TeamPoise>")
    brief_path, center = Path(sys.argv[1]), sys.argv[2]

    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    if brief.get("error"):
        sys.exit(f"브리프에 오류가 있습니다: {brief['error']}")
    facts = load_facts(center)

    print(f"[{brief['keyword']}] 초안 생성 중… (사실 {len(facts)}개 투입)")
    md = call_anthropic(SYSTEM, build_prompt(brief, facts), cfg).strip()

    # 첫 줄 "# 제목" 을 프론트매터로 올린다
    lines = md.splitlines()
    title = lines[0].lstrip("# ").strip() if lines and lines[0].startswith("#") else brief["keyword"]
    body = "\n".join(lines[1:]).strip() if lines and lines[0].startswith("#") else md

    used = sorted(set(re.findall(r"\[fact:([a-zA-Z0-9_\-]+)\]", body)))
    today = datetime.now(KST).strftime("%Y-%m-%d")
    out = OUTDIR / f"{today}-{brief['keyword']}.md"
    OUTDIR.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "---\n"
        + yaml.safe_dump({"title": title, "keyword": brief["keyword"], "center": center,
                          "created": today, "facts_used": used, "status": "draft"},
                         allow_unicode=True, sort_keys=False)
        + "---\n\n" + body + "\n",
        encoding="utf-8")

    print(f"  제목: {title}")
    print(f"  인용한 사실: {', '.join(used) if used else '없음 (게이트에서 걸릴 것)'}")
    print(f"  → {out}")
    print(f"\n다음: python src/quality_gate.py {out}")


if __name__ == "__main__":
    main()
