"""초안 생성 비용 기록 — "정말 편당 50원인가" 를 실측으로 확인하기 위한 장부.

content/state/cost_log.csv 에 한 줄씩 쌓는다.

billed_usd        실제로 청구되는 금액 (API 경로에서만 발생)
subscription_usd  구독 사용량에서 차감된 환산액 (claude-code 경로. 청구 아님)

두 칸을 나눈 이유는, auto 모드가 "이번 달 실제 청구액" 만 보고 전환을 결정하기
때문이다. 구독으로 쓴 건 지갑에서 나가지 않으므로 예산 계산에 넣지 않는다.
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "content" / "state" / "cost_log.csv"
KST = timezone(timedelta(hours=9))

FIELDS = ["at", "provider", "model", "keyword", "center",
          "input_tokens", "output_tokens", "billed_usd", "subscription_usd", "seconds"]


def price_of(cfg: dict, model: str, input_tokens: int, output_tokens: int) -> float:
    """config 의 단가표로 USD 를 계산한다. 단가표에 없는 모델이면 0 을 돌려준다."""
    table = (cfg.get("llm", {}).get("pricing") or {}).get(model)
    if not table:
        return 0.0
    return (input_tokens / 1_000_000) * float(table["input"]) \
         + (output_tokens / 1_000_000) * float(table["output"])


def record(**row) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    is_new = not LOG.exists()
    row.setdefault("at", datetime.now(KST).isoformat(timespec="seconds"))
    with LOG.open("a", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if is_new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in FIELDS})


def rows() -> list[dict]:
    if not LOG.exists():
        return []
    with LOG.open(encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def _f(value) -> float:
    try:
        return float(value or 0)
    except ValueError:
        return 0.0


def month_billed_usd(month: str | None = None) -> float:
    """해당 월(YYYY-MM)에 실제로 청구된 금액 합계. 기본값은 이번 달."""
    month = month or datetime.now(KST).strftime("%Y-%m")
    return sum(_f(r["billed_usd"]) for r in rows() if r["at"].startswith(month))
