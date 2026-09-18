#!/usr/bin/env python3
"""네이버 검색광고 키워드도구(/keywordstool) — 절대 월간 검색수 조회.

DataLab은 상대 비율만 주지만 이 API는 실제 월간 검색수를 준다.
키 발급: 네이버 검색광고 → 도구 → API 관리 (액세스 라이선스 / 비밀키 / CUSTOMER_ID)

사용: python src/keywordtool.py 하이록스 크로스핏 철산 광교
출력: data/keywords_YYYY-MM-DD.csv
"""
from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://api.naver.com"
URI = "/keywordstool"
KST = timezone(timedelta(hours=9))


def signature(secret: str, timestamp: str, method: str, uri: str) -> str:
    message = f"{timestamp}.{method}.{uri}"
    digest = hmac.new(secret.encode(), message.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def headers(method: str, uri: str) -> dict:
    api_key = os.environ.get("NAVER_AD_API_KEY")
    secret = os.environ.get("NAVER_AD_SECRET_KEY")
    customer = os.environ.get("NAVER_AD_CUSTOMER_ID")
    if not all([api_key, secret, customer]):
        sys.exit("NAVER_AD_API_KEY / NAVER_AD_SECRET_KEY / NAVER_AD_CUSTOMER_ID 가 필요합니다.")
    timestamp = str(round(time.time() * 1000))
    return {
        "X-Timestamp": timestamp,
        "X-API-KEY": api_key,
        "X-Customer": str(customer),
        "X-Signature": signature(secret, timestamp, method, uri),
    }


def to_int(value) -> int:
    """'< 10' 같은 값이 섞여 오므로 정수로 정규화."""
    try:
        return int(str(value).replace("<", "").replace(",", "").strip())
    except (ValueError, AttributeError):
        return 0


def fetch(seeds: list[str]) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    for seed in seeds:
        params = {"hintKeywords": seed, "showDetail": "1"}
        r = requests.get(BASE + URI, params=params, headers=headers("GET", URI), timeout=15)
        if r.status_code != 200:
            print(f"  ! {seed}: HTTP {r.status_code} {r.text[:200]}")
            continue
        for item in r.json().get("keywordList", []):
            kw = item.get("relKeyword", "")
            if not kw or kw in seen:
                continue
            seen.add(kw)
            pc = to_int(item.get("monthlyPcQcCnt"))
            mo = to_int(item.get("monthlyMobileQcCnt"))
            rows.append({
                "keyword": kw,
                "seed": seed,
                "pc": pc,
                "mobile": mo,
                "total": pc + mo,
                "comp": item.get("compIdx", ""),
                "ad_depth": item.get("plAvgDepth", ""),
            })
        print(f"  {seed}: 누적 {len(rows)}개")
        time.sleep(0.3)
    return rows


def main() -> None:
    seeds = sys.argv[1:] or ["하이록스", "크로스핏", "철산", "광교"]
    rows = fetch(seeds)
    rows.sort(key=lambda r: r["total"], reverse=True)

    out = ROOT / "data" / f"keywords_{datetime.now(KST):%Y-%m-%d}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=["keyword", "seed", "pc", "mobile", "total", "comp", "ad_depth"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"\n{len(rows)}개 키워드 → {out}")
    print("\n상위 15개")
    for row in rows[:15]:
        print(f"  {row['total']:>8,}  {row['comp']:<4} {row['keyword']}")


if __name__ == "__main__":
    main()
