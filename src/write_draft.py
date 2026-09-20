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
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cost_log  # noqa: E402

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
   "※ 사용 조건" 이 달린 항목은 그 조건을 반드시 지킨다. 조건을 지킬 수 없으면 그 항목을 쓰지 않는다.
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

출력은 마크다운 본문만. 설명이나 머리말을 덧붙이지 않는다.
파일을 읽거나 명령을 실행하지 말고, 주어진 정보만으로 글을 써서 바로 출력한다."""


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

    def fact_line(f: dict) -> str:
        line = f"- [{f['id']}] {f['fact']}"
        if f.get("source"):
            line += f"  (출처: {f['source']})"
        if f.get("caution"):
            line += f"\n    ※ 사용 조건: {' '.join(f['caution'].split())}"
        return line

    fact_lines = "\n".join(fact_line(f) for f in facts)
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


def call_anthropic(system: str, prompt: str, cfg: dict) -> dict:
    """API 직접 호출. 실제 청구가 발생하는 경로."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        sys.exit("ANTHROPIC_API_KEY 가 없습니다. console.anthropic.com 에서 발급하세요.\n"
                 "  (키 없이 쓰시려면 config/content.yml 의 provider 를 claude-code 로)")
    model = cfg["llm"]["model"]
    t0 = time.time()
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": model, "max_tokens": cfg["llm"]["max_tokens"],
              "system": system, "messages": [{"role": "user", "content": prompt}]},
        timeout=180,
    )
    if r.status_code != 200:
        sys.exit(f"LLM 호출 실패 HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    usage = data.get("usage", {})
    tin = int(usage.get("input_tokens", 0))
    tout = int(usage.get("output_tokens", 0))
    return {
        "text": "".join(b.get("text", "") for b in data.get("content", [])),
        "provider": "anthropic", "model": model,
        "input_tokens": tin, "output_tokens": tout,
        "billed_usd": cost_log.price_of(cfg, model, tin, tout),
        "subscription_usd": 0.0,
        "seconds": round(time.time() - t0, 1),
    }


def call_claude_code(system: str, prompt: str, cfg: dict) -> dict:
    """구독제 경로 — API 키 대신 Claude Code CLI 를 거쳐 호출한다.

    핵심은 ANTHROPIC_API_KEY 를 자식 프로세스 환경에서 제거하는 것이다.
    이 변수가 있으면 Claude Code 가 구독이 아니라 API 로 붙어서 별도 과금된다
    (Anthropic 지원 문서에 명시된 동작).

    --bare 는 쓰지 않는다. bare 모드는 OAuth 를 건너뛰어서
    구독 로그인이 아니라 API 키를 요구하기 때문이다.
    """
    import shutil
    import subprocess

    exe = shutil.which("claude")
    if not exe:
        sys.exit("claude CLI 를 찾지 못했습니다.\n"
                 "  설치: npm install -g @anthropic-ai/claude-code\n"
                 "  로그인: claude  (한 번 실행해서 구독 계정으로 로그인)")

    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)      # ← 이게 있으면 구독이 아니라 API 과금
    env.pop("ANTHROPIC_AUTH_TOKEN", None)

    cmd = [exe, "-p", prompt,
           "--append-system-prompt", system,
           "--output-format", "json"]

    print("  (Claude Code 구독 경로 — 30초~2분 걸릴 수 있습니다)")
    t0 = time.time()
    try:
        r = subprocess.run(cmd, env=env, capture_output=True, text=True,
                           timeout=900, encoding="utf-8")
    except subprocess.TimeoutExpired:
        sys.exit("claude CLI 응답이 15분을 넘겨 중단했습니다.")
    if r.returncode != 0:
        sys.exit(f"claude CLI 실패 (exit {r.returncode}):\n{(r.stderr or r.stdout)[:500]}")

    try:
        payload = json.loads(r.stdout)
    except json.JSONDecodeError:
        payload = {"result": r.stdout.strip()}

    usage = payload.get("usage") or {}
    return {
        "text": (payload.get("result") or "").strip(),
        "provider": "claude-code", "model": cfg["llm"]["model"],
        "input_tokens": int(usage.get("input_tokens", 0) or 0),
        "output_tokens": int(usage.get("output_tokens", 0) or 0),
        "billed_usd": 0.0,                                   # 구독이라 청구 없음
        "subscription_usd": float(payload.get("total_cost_usd") or 0.0),
        "seconds": round(time.time() - t0, 1),
    }


def resolve_provider(cfg: dict, override: str | None) -> tuple[str, str]:
    """실제로 쓸 경로를 정한다. (provider, 이유) 를 돌려준다."""
    if override:
        return override, "명령줄 지정"

    setting = cfg["llm"].get("provider", "anthropic")
    if setting != "auto":
        return setting, "설정값"

    # auto: 이번 달 실제 청구액이 예산을 넘으면 구독으로 넘어간다
    budget = float(cfg["llm"].get("monthly_budget_usd", 3.0))
    spent = cost_log.month_billed_usd()
    rate = float(cfg["llm"].get("krw_per_usd", 1390))
    if spent >= budget:
        return ("claude-code",
                f"이번 달 API 청구 ${spent:.2f} ≥ 예산 ${budget:.2f} "
                f"({spent * rate:,.0f}원 / {budget * rate:,.0f}원) → 구독으로 전환")
    return ("anthropic",
            f"이번 달 API 청구 ${spent:.2f} / 예산 ${budget:.2f} "
            f"({spent * rate:,.0f}원 / {budget * rate:,.0f}원)")


def generate(system: str, prompt: str, cfg: dict, override: str | None,
             keyword: str, center: str) -> str:
    provider, why = resolve_provider(cfg, override)
    print(f"  경로: {provider}  ({why})")

    if provider == "claude-code":
        out = call_claude_code(system, prompt, cfg)
    elif provider == "anthropic":
        out = call_anthropic(system, prompt, cfg)
    else:
        sys.exit(f"알 수 없는 provider: {provider}  (auto | anthropic | claude-code)")

    cost_log.record(provider=out["provider"], model=out["model"],
                    keyword=keyword, center=center,
                    input_tokens=out["input_tokens"], output_tokens=out["output_tokens"],
                    billed_usd=f"{out['billed_usd']:.6f}",
                    subscription_usd=f"{out['subscription_usd']:.6f}",
                    seconds=out["seconds"])

    rate = float(cfg["llm"].get("krw_per_usd", 1390))
    if out["billed_usd"]:
        print(f"  비용: ${out['billed_usd']:.4f} (약 {out['billed_usd'] * rate:,.0f}원) "
              f"· 입력 {out['input_tokens']:,} / 출력 {out['output_tokens']:,} 토큰 "
              f"· {out['seconds']}초")
    else:
        won = out["subscription_usd"] * rate
        print(f"  비용: 청구 없음 (구독 차감분 환산 약 {won:,.0f}원) · {out['seconds']}초")
    return out["text"]


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    override = None
    for a in sys.argv[1:]:
        if a.startswith("--provider="):
            override = a.split("=", 1)[1]
    if len(args) < 2:
        sys.exit("사용: python src/write_draft.py <brief.json> <Reboot|TeamPoise> "
                 "[--provider=anthropic|claude-code]")
    brief_path, center = Path(args[0]), args[1]

    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    if brief.get("error"):
        sys.exit(f"브리프에 오류가 있습니다: {brief['error']}")
    facts = load_facts(center)

    print(f"[{brief['keyword']}] 초안 생성 중… (사실 {len(facts)}개 투입)")
    md = generate(SYSTEM, build_prompt(brief, facts), cfg,
                  override, brief["keyword"], center).strip()

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
