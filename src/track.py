#!/usr/bin/env python3
"""네이버 검색 노출 추적기.

- 지역검색 API : 우리 업체가 상위 5위 안에 있는지 + 1~3위 경쟁사 (API 상한 display=5)
- 블로그검색 API: 우리 블로그 글의 실제 순위 (100개씩 페이지를 넘기며 확인)

결과는 data/rankings.csv 에 한 줄씩 append 된다 (tidy 포맷).
"""
from __future__ import annotations

import csv
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "targets.yml"
OUT = ROOT / "data" / "rankings.csv"

# 2026-06-25부터 네이버 검색 API는 NAVER CLOUD PLATFORM의 NAVER API HUB로 이관됐다.
# 신규 발급은 HUB 쪽만 가능하고, 기존 developers.naver.com 키는 2027-06-30까지만 동작한다.
HUB_HOST = "https://naverapihub.apigw.ntruss.com"
HUB_LOCAL = HUB_HOST + "/search/v1/local"
HUB_BLOG = HUB_HOST + "/search/v1/blog"

LEGACY_LOCAL = "https://openapi.naver.com/v1/search/local.json"
LEGACY_BLOG = "https://openapi.naver.com/v1/search/blog.json"

KST = timezone(timedelta(hours=9))
TAG_RE = re.compile(r"<[^>]+>")
FIELDS = [
    "date", "center", "keyword", "surface",
    "our_rank", "our_title", "rank1", "rank2", "rank3", "checked_depth",
]


def credentials() -> tuple[dict, str, str]:
    """HUB 키가 있으면 HUB를, 없으면 구 developers.naver.com 키를 쓴다.

    반환: (인증 헤더, 지역검색 URL, 블로그검색 URL)
    """
    ncp_id = os.environ.get("NCP_API_KEY_ID")
    ncp_key = os.environ.get("NCP_API_KEY")
    if ncp_id and ncp_key:
        print("인증: NAVER API HUB (NCP)")
        return ({"X-NCP-APIGW-API-KEY-ID": ncp_id, "X-NCP-APIGW-API-KEY": ncp_key},
                HUB_LOCAL, HUB_BLOG)

    cid = os.environ.get("NAVER_CLIENT_ID")
    csec = os.environ.get("NAVER_CLIENT_SECRET")
    if cid and csec:
        print("인증: developers.naver.com (레거시 — 2027-06-30까지만 동작)")
        return ({"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": csec},
                LEGACY_LOCAL, LEGACY_BLOG)

    sys.exit(
        "API 키가 없습니다.\n"
        "  신규:   NCP_API_KEY_ID / NCP_API_KEY        (console.ncloud.com → NAVER API HUB)\n"
        "  레거시: NAVER_CLIENT_ID / NAVER_CLIENT_SECRET (developers.naver.com, 신규 발급 불가)"
    )


def clean(text: str) -> str:
    return TAG_RE.sub("", text or "").replace("&amp;", "&").strip()


def norm(text: str) -> str:
    """공백/대소문자/일부 기호를 무시한 비교용 키."""
    return re.sub(r"[\s\-_.]", "", clean(text)).lower()


def get(url: str, params: dict, hdrs: dict) -> dict | None:
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, headers=hdrs, timeout=10)
        except requests.RequestException as exc:
            print(f"  ! 요청 실패({attempt + 1}/3): {exc}")
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code == 200:
            return r.json()
        if r.status_code == 429:
            print("  ! 호출 한도 초과(429). 5초 대기 후 재시도")
            time.sleep(5)
            continue
        # start 값이 API 상한을 넘으면 400이 온다 → 더 깊이 보지 않는다는 신호
        print(f"  ! HTTP {r.status_code}: {r.text[:160]}")
        return None
    return None


def track_place(keyword: str, aliases: list[str], hdrs: dict, url: str) -> dict:
    """지역검색 상위 5위 확인. API가 display 5까지만 주므로 그 이상은 알 수 없다."""
    # display 범위는 1~5 (HUB 문서 기준). 그 밖의 순위는 이 API로 알 수 없다.
    data = get(url, {"query": keyword, "display": 5, "sort": "random"}, hdrs)
    items = (data or {}).get("items", [])
    keys = {norm(a) for a in aliases}
    our_rank, our_title = "", ""
    for idx, item in enumerate(items, start=1):
        title = clean(item.get("title", ""))
        if norm(title) in keys or any(k in norm(title) for k in keys):
            our_rank, our_title = idx, title
            break
    top = [clean(i.get("title", "")) for i in items[:3]]
    top += [""] * (3 - len(top))
    return {
        "surface": "place",
        "our_rank": our_rank,
        "our_title": our_title,
        "rank1": top[0], "rank2": top[1], "rank3": top[2],
        "checked_depth": len(items),
    }


def track_blog(keyword: str, blog_ids: list[str], hdrs: dict, url: str,
               page_size: int, pages: int) -> dict:
    """블로그 검색에서 우리 블로그 글의 실제 순위를 찾는다."""
    ids = {b.lower() for b in blog_ids}
    checked = 0
    top: list[str] = []
    for page in range(pages):
        start = page * page_size + 1
        if start > 1000:      # start 상한 1000 (HUB 문서 기준)
            break
        data = get(url, {"query": keyword, "display": page_size,
                         "start": start, "sort": "sim"}, hdrs)
        if not data:
            break  # 400(상한 초과) 또는 오류 → 여기까지만 확인한 것으로 처리
        items = data.get("items", [])
        if not items:
            break
        for offset, item in enumerate(items):
            rank = start + offset
            if len(top) < 3:
                top.append(clean(item.get("bloggername", "")) or clean(item.get("title", "")))
            link = (item.get("link", "") + " " + item.get("bloggerlink", "")).lower()
            if any(bid in link for bid in ids):
                top += [""] * (3 - len(top))
                return {
                    "surface": "blog",
                    "our_rank": rank,
                    "our_title": clean(item.get("title", "")),
                    "rank1": top[0], "rank2": top[1], "rank3": top[2],
                    "checked_depth": rank,
                }
        checked = start + len(items) - 1
        if len(items) < page_size:
            break
        time.sleep(0.2)
    top += [""] * (3 - len(top))
    return {
        "surface": "blog",
        "our_rank": "", "our_title": "",
        "rank1": top[0], "rank2": top[1], "rank3": top[2],
        "checked_depth": checked,
    }


def main() -> None:
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    hdrs, local_url, blog_url = credentials()
    today = datetime.now(KST).strftime("%Y-%m-%d")
    page_size = int(cfg.get("blog_page_size", 100))
    pages = int(cfg.get("blog_pages", 3))

    rows = []
    for center in cfg["centers"]:
        name = center["name"]
        for keyword in center["keywords"]:
            print(f"[{name}] {keyword}")
            place = track_place(keyword, center.get("place_aliases", []), hdrs, local_url)
            time.sleep(0.2)
            blog = track_blog(keyword, center.get("blog_ids", []), hdrs, blog_url,
                              page_size, pages)
            time.sleep(0.2)
            for result in (place, blog):
                rows.append({"date": today, "center": name, "keyword": keyword, **result})
            print(f"   place={place['our_rank'] or '5위밖'}  blog={blog['our_rank'] or '미노출'}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    is_new = not OUT.exists()
    with OUT.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerows(rows)
    print(f"\n{len(rows)}행 기록 → {OUT}")


if __name__ == "__main__":
    main()
