#!/usr/bin/env python3
"""네이버 검색 노출 추적기.

- 지역검색 API : 우리 업체가 상위 5위 안에 있는지 + 1~5위 경쟁사 (API 상한 display=5)
- 니즈/브랜드 키워드: 지역명이 없어 지역검색이 의미 없으므로 블로그만 확인한다
- 블로그검색 API: 우리 블로그 글의 실제 순위 (100개씩 페이지를 넘기며 확인)

결과는 data/rankings.csv 에 한 줄씩 append 된다 (tidy 포맷).
블로그에서 잡힌 우리 글은 키워드마다 전부 data/blog_posts.csv 에 따로 남긴다
(rankings.csv 에는 그중 가장 높은 1편만 들어간다).

블로그 API는 2페이지부터 앞 페이지의 글을 다시 섞어 준다 (2026-10-02 확인: 100개 중
새 글은 30개 안팎). 그래서 순위는 '받은 순서'가 아니라 '서로 다른 글 중 몇 번째'로 세고,
checked_depth 도 서로 다른 글 수로 적는다. API 순위는 네이버 앱 화면 순위와 다를 수 있다.
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
OUT_POSTS = ROOT / "data" / "blog_posts.csv"

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
    "date", "center", "keyword", "surface", "our_rank", "our_title",
    "rank1", "rank2", "rank3", "rank4", "rank5", "checked_depth",
]
POST_FIELDS = ["date", "center", "keyword", "rank", "title", "link", "postdate"]
TOPN = 5                  # 상위 몇 위까지 업체명을 기록할지 (지역검색 API 상한과 동일)


def load_env() -> None:
    """.env 가 있으면 환경변수로 올린다 (외부 패키지 없이). 이미 설정된 값은 건드리지 않는다."""
    path = ROOT / ".env"
    if not path.exists():
        return
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


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
    top = [clean(i.get("title", "")) for i in items[:TOPN]]
    top += [""] * (TOPN - len(top))
    return {
        "surface": "place",
        "our_rank": our_rank,
        "our_title": our_title,
        **{f"rank{i}": top[i - 1] for i in range(1, TOPN + 1)},
        "checked_depth": len(items),
    }


def post_key(link: str) -> str:
    """같은 글인지 가리는 키. 추적용 쿼리(?fromRss=…)와 끝 슬래시를 뗀다."""
    return link.split("?")[0].rstrip("/").lower()


def track_blog(keyword: str, blog_ids: list[str], hdrs: dict, url: str,
               page_size: int, pages: int) -> tuple[dict, list[dict]]:
    """블로그 검색에서 우리 블로그 글의 순위를 찾는다.

    반환: (rankings.csv 한 줄 — 가장 높은 우리 글, 확인 범위 안의 우리 글 전부)
    """
    ids = {b.lower() for b in blog_ids}
    seen: set[str] = set()
    top: list[str] = []
    ours: list[dict] = []
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
        for item in items:
            key = post_key(item.get("link", ""))
            if key in seen:
                continue      # 앞 페이지에서 이미 센 글
            seen.add(key)
            rank = len(seen)
            if len(top) < TOPN:
                top.append(clean(item.get("bloggername", "")) or clean(item.get("title", "")))
            link = (item.get("link", "") + " " + item.get("bloggerlink", "")).lower()
            if any(bid in link for bid in ids):
                ours.append({"rank": rank, "title": clean(item.get("title", "")),
                             "link": item.get("link", "").split("?")[0],
                             "postdate": item.get("postdate", "")})
        if len(items) < page_size:
            break
        time.sleep(0.2)
    top += [""] * (TOPN - len(top))
    best = ours[0] if ours else None
    return {
        "surface": "blog",
        "our_rank": best["rank"] if best else "",
        "our_title": best["title"] if best else "",
        **{f"rank{i}": top[i - 1] for i in range(1, TOPN + 1)},
        "checked_depth": len(seen),
    }, ours


def main() -> None:
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    load_env()
    hdrs, local_url, blog_url = credentials()
    today = datetime.now(KST).strftime("%Y-%m-%d")
    page_size = int(cfg.get("blog_page_size", 100))
    pages = int(cfg.get("blog_pages", 3))

    rows, post_rows = [], []
    for center in cfg["centers"]:
        name = center["name"]

        def blog_for(keyword: str) -> dict:
            blog, ours = track_blog(keyword, center.get("blog_ids", []), hdrs, blog_url,
                                    page_size, pages)
            post_rows.extend({"date": today, "center": name, "keyword": keyword, **p}
                             for p in ours)
            return blog

        for keyword in center["keywords"]:
            print(f"[{name}] {keyword}")
            place = track_place(keyword, center.get("place_aliases", []), hdrs, local_url)
            time.sleep(0.2)
            blog = blog_for(keyword)
            time.sleep(0.2)
            for result in (place, blog):
                rows.append({"date": today, "center": name, "keyword": keyword, **result})
            print(f"   place={place['our_rank'] or '5위밖'}  "
                  f"blog={blog['our_rank'] or '미노출'} (서로 다른 글 {blog['checked_depth']}개 확인)")

        # 니즈·브랜드 키워드 — 지역명이 없어 지역검색은 의미가 없다. 블로그만 본다.
        for group, tag in (("needs_keywords", "니즈"), ("brand_keywords", "브랜드")):
            for keyword in center.get(group) or []:
                print(f"[{name}] {keyword} ({tag})")
                blog = blog_for(keyword)
                time.sleep(0.2)
                rows.append({"date": today, "center": name, "keyword": keyword, **blog})
                print(f"   blog={blog['our_rank'] or '미노출'} "
                      f"(서로 다른 글 {blog['checked_depth']}개 확인)")

    # 검색 결과가 하나도 없으면 인증·API 오류다 → 빈 줄을 '미노출'로 기록하지 않고 실패로 끝낸다
    if rows and all(r["checked_depth"] == 0 for r in rows):
        sys.exit("모든 검색이 결과 0건 — 인증 정보(HTTP 401 등)를 확인한다. 기록하지 않았다.")

    for path, fields, data in ((OUT, FIELDS, rows), (OUT_POSTS, POST_FIELDS, post_rows)):
        path.parent.mkdir(parents=True, exist_ok=True)
        # 같은 날 다시 돌리면(수동 실행 + 예약 실행) 그날 기록을 새 결과로 바꾼다 — 덧붙이면 하루가 두 번 들어간다
        keep = []
        if path.exists():
            with path.open(newline="", encoding="utf-8") as fh:
                keep = [r for r in csv.DictReader(fh) if r["date"] != today]
        # 줄바꿈은 LF로 통일 — git이 저장하는 형식과 같아야 매일 그날 줄만 바뀐다
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(keep + data)
        print(f"{len(data)}행 기록 → {path}")


if __name__ == "__main__":
    main()
