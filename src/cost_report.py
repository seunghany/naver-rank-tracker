#!/usr/bin/env python3
"""초안 생성 비용 리포트 — "정말 편당 50원인가" 확인용.

사용: python src/cost_report.py
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cost_log  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
KST = timezone(timedelta(hours=9))


def main() -> None:
    cfg = yaml.safe_load((ROOT / "config" / "content.yml").read_text(encoding="utf-8"))
    rate = float(cfg["llm"].get("krw_per_usd", 1386))
    budget = float(cfg["llm"].get("monthly_budget_usd", 3.0))

    rows = cost_log.rows()
    if not rows:
        print("아직 기록이 없습니다. 초안을 한 번 생성하면 여기 쌓입니다.")
        return

    by_month = defaultdict(list)
    for r in rows:
        by_month[r["at"][:7]].append(r)

    print(f"{'월':<9} {'건수':>4} {'청구(USD)':>11} {'청구(원)':>10} "
          f"{'편당(원)':>9} {'구독차감(원)':>12}")
    print("─" * 62)
    for month in sorted(by_month):
        items = by_month[month]
        billed = sum(cost_log._f(r["billed_usd"]) for r in items)
        subs = sum(cost_log._f(r["subscription_usd"]) for r in items)
        paid = [r for r in items if cost_log._f(r["billed_usd"]) > 0]
        per = (billed / len(paid) * rate) if paid else 0
        print(f"{month:<9} {len(items):>4} {billed:>11.4f} {billed * rate:>9,.0f}원 "
              f"{per:>8,.0f}원 {subs * rate:>11,.0f}원")

    now = datetime.now(KST).strftime("%Y-%m")
    spent = cost_log.month_billed_usd(now)
    pct = spent / budget * 100 if budget else 0
    print()
    print(f"이번 달({now}) 청구 {spent * rate:,.0f}원 / 예산 {budget * rate:,.0f}원  ({pct:.0f}%)")
    if spent >= budget:
        print("→ 예산 초과. auto 모드면 지금부터 구독 경로로 나갑니다.")

    print("\n최근 5건")
    for r in rows[-5:]:
        won = cost_log._f(r["billed_usd"]) * rate
        tag = f"{won:,.0f}원" if won else "구독"
        print(f"  {r['at'][5:16]}  {r['provider']:<12} {r['keyword']:<14} "
              f"{tag:>8}  {r['seconds']}초")


if __name__ == "__main__":
    main()
