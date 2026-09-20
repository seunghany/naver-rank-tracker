#!/usr/bin/env python3
"""M4-a — 네이버 로그인 세션 1회 저장.

비밀번호는 이 스크립트도, 저장소도, 저도 다루지 않습니다.
브라우저 창을 띄워드릴 뿐이고, 로그인은 사장님이 직접 하십니다.
로그인하고 나면 그 브라우저 프로필에 세션이 남아서 발행 스크립트가 재사용합니다.

이 방식을 택한 이유는 두 가지입니다.
  - 자동 로그인을 시도하면 네이버가 비정상 로그인으로 보고 캡차·기기등록을 띄웁니다.
  - 비밀번호를 파일이나 GitHub Secrets에 두는 것 자체가 더 큰 위험입니다.

사용: python src/naver_login.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / ".naver-profile"          # .gitignore 에 등록됨. 절대 커밋 금지.


def session_status(url: str, body: str) -> str | bool:
    """로그인 상태를 판별한다.

    네이버는 로그인 직후 기기등록/보안 리다이렉트를 일으킬 수 있으므로,
    해당 리다이렉트를 로그인 실패로 보지 않고 보안 흐름으로 인식한다.
    """
    lowered = body.lower()
    if "deviceadd" in url.lower() or "device add" in lowered or "기기등록" in body:
        return "security_redirect"
    if ("로그아웃" in body) or ("logout" in lowered):
        return True
    if ("로그인" in body) and ("네이버" in body):
        return False
    return bool(("로그아웃" in body) or ("logout" in lowered))


def main() -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("playwright 가 없습니다:\n  pip install playwright\n  playwright install chromium")

    PROFILE.mkdir(exist_ok=True)
    print(f"프로필 위치: {PROFILE}")
    print("브라우저를 엽니다. 네이버에 직접 로그인하신 뒤 이 창으로 돌아와 Enter 를 눌러주세요.\n")

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PROFILE), headless=False,
            viewport={"width": 1400, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://nid.naver.com/nidlogin.login", wait_until="domcontentloaded")

        input("로그인 완료 후 Enter > ")

        page.goto("https://www.naver.com", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)
        body = page.content()
        status = session_status(page.url, body)
        if status == "security_redirect":
            print("\n네이버 보안 페이지로 이동했습니다. 기기등록/보안 확인을 마치고 다시 확인해 주세요.")
            print("로그인 완료 후, 브라우저가 네이버 메인으로 돌아오면 다시 실행해 주세요.")
        elif status is True:
            print("\n세션 저장됨 ✓")
        else:
            print("\n로그인 상태를 확인하지 못했습니다. 다시 실행해 보세요.")
        ctx.close()


if __name__ == "__main__":
    main()
