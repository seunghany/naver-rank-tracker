"""NAVER API HUB 검색 API 얇은 래퍼.

track.py 는 자체 구현을 그대로 쓴다(이미 운영 중이라 건드리지 않음).
콘텐츠 파이프라인 쪽에서만 이 모듈을 쓴다.
"""
from __future__ import annotations

import os
import re
import sys
import time

import requests

HUB = "https://naverapihub.apigw.ntruss.com"
LEGACY = "https://openapi.naver.com"

PATHS = {
    "hub": {"blog": "/search/v1/blog", "local": "/search/v1/local"},
    "legacy": {"blog": "/v1/search/blog.json", "local": "/v1/search/local.json"},
}

TAG_RE = re.compile(r"<[^>]+>")


def clean(text: str) -> str:
    """검색 결과의 <b> 강조 태그와 HTML 엔티티를 벗긴다."""
    text = TAG_RE.sub("", text or "")
    for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                 ("&quot;", '"'), ("&#39;", "'")):
        text = text.replace(a, b)
    return text.strip()


def credentials() -> tuple[dict, str, dict]:
    """(헤더, 베이스 URL, 경로맵) 반환. HUB 키가 있으면 HUB 우선."""
    ncp_id = os.environ.get("NCP_API_KEY_ID")
    ncp_key = os.environ.get("NCP_API_KEY")
    if ncp_id and ncp_key:
        return ({"X-NCP-APIGW-API-KEY-ID": ncp_id, "X-NCP-APIGW-API-KEY": ncp_key},
                HUB, PATHS["hub"])

    cid, csec = os.environ.get("NAVER_CLIENT_ID"), os.environ.get("NAVER_CLIENT_SECRET")
    if cid and csec:
        return ({"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": csec},
                LEGACY, PATHS["legacy"])

    sys.exit("NCP_API_KEY_ID / NCP_API_KEY 가 필요합니다 (console.ncloud.com → NAVER API HUB).")


def search_blog(query: str, display: int = 100, start: int = 1,
                sort: str = "sim") -> list[dict]:
    """블로그 검색. 제목·요약·블로거명·작성일·링크만 돌려준다.

    본문은 가져오지 않는다 — API가 주지 않고, 본문을 긁어다 쓰면
    유사문서 필터에 걸리기 때문에 설계상 의도적으로 뺐다.
    """
    hdrs, base, paths = credentials()
    display = max(1, min(100, display))          # HUB 문서 기준 상한 100
    if start > 1000:                             # start 상한 1000
        return []
    r = requests.get(base + paths["blog"], headers=hdrs, timeout=15,
                     params={"query": query, "display": display,
                             "start": start, "sort": sort})
    if r.status_code != 200:
        print(f"  ! 블로그 검색 실패 HTTP {r.status_code}: {r.text[:200]}")
        return []
    out = []
    for i, item in enumerate(r.json().get("items", [])):
        out.append({
            "rank": start + i,
            "title": clean(item.get("title", "")),
            "description": clean(item.get("description", "")),
            "blogger": clean(item.get("bloggername", "")),
            "postdate": item.get("postdate", ""),
            "link": item.get("link", ""),
        })
    time.sleep(0.2)
    return out
