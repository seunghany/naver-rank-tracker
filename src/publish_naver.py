#!/usr/bin/env python3
"""M4-b — 네이버 블로그 발행 (Playwright).

경고를 코드 맨 앞에 남겨둡니다.
네이버는 2020-05-06 에 블로그 글쓰기 API 를 없앴고, 그 이유를 "광고성 블로그 퇴출"
이라고 직접 밝혔습니다. 즉 이 스크립트가 쓰는 경로는 네이버가 의도적으로 막아둔
길을 우회하는 것이고, 2026년 기준 네이버의 제재 강화 대상에 "매크로 자동화"가
명시돼 있습니다. 적발 시 블로그뿐 아니라 연결된 플레이스 노출까지 영향을 받습니다.

그래서 아래 가드레일은 설정으로 끌 수 없게 코드에 박아두었습니다.
  - 하루 발행 상한 (config: publish.max_per_day, 코드 상한 3)
  - 연속 발행 최소 간격
  - 공감·댓글·서이추·이웃추가 자동화는 구현하지 않음 (추가 요청도 받지 않음)
  - 기본값 dry_run=true — 임시저장까지만 하고 멈춤

사용:
    python src/publish_naver.py content/drafts/2026-09-20-철산하이록스.md
    python src/publish_naver.py <초안.md> --live      # 실제 발행
"""
from __future__ import annotations

import json
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from md_to_html import convert  # noqa: E402
from quality_gate import check, split_frontmatter  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "content.yml"
TARGETS = ROOT / "config" / "targets.yml"
PROFILE = ROOT / ".naver-profile"
STATE = ROOT / "content" / "state" / "publish_log.json"
PUBLISHED = ROOT / "content" / "published"
SHOTS = ROOT / "content" / "state"
KST = timezone(timedelta(hours=9))

HARD_DAILY_CAP = 3          # config 가 뭐라고 하든 이 위로는 안 올라간다

# 스마트에디터 DOM 은 자주 바뀐다. 셀렉터를 여기 모아두고,
# 실패하면 스크린샷을 남겨서 고칠 수 있게 했다.
SEL = {
    "editor_iframe": "#mainFrame",
    "title": ".se-documentTitle .se-text-paragraph, .se-title-text .se-text-paragraph, "
             "[contenteditable='true'].se-text-paragraph",
    "body": ".se-component.se-text .se-text-paragraph, "
            ".se-main-container .se-text-paragraph",
    # 처음 열면 "작성 중인 글이 있습니다" 복구 팝업이 뜬다. 취소를 눌러야 빈 글로 시작한다.
    "restore_cancel": "button.se-popup-button-cancel, .se-popup-button-cancel, "
                      "button:has-text('취소')",
    "help_close": ".se-help-panel-close-button, button.se-popup-close, "
                  "button:has-text('닫기')",
    "save_draft": "button.save_btn__bzc5B, button[class*='save_btn'], "
                  "button:has-text('저장')",
    "publish_open": "button.publish_btn__m9KHH, button[class*='publish_btn'], "
                    "button:has-text('발행')",
    "publish_confirm": "button.confirm_btn__WEaBq, button[class*='confirm_btn'], "
                       "button:has-text('발행')",
}


def try_click(frame, selectors: str, label: str, timeout: int = 2000) -> bool:
    """콤마로 나열된 후보 셀렉터를 차례로 눌러본다. 하나라도 되면 True."""
    for sel in [x.strip() for x in selectors.split(", ") if x.strip()]:
        try:
            loc = frame.locator(sel).first
            if loc.is_visible(timeout=timeout):
                loc.click()
                print(f"    · {label}: '{sel}' 로 처리")
                return True
        except Exception:
            continue
    return False


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"runs": []}


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def guardrails(cfg: dict, state: dict) -> None:
    """상한을 넘으면 여기서 멈춘다."""
    pub = cfg["publish"]
    cap = min(int(pub["max_per_day"]), HARD_DAILY_CAP)
    now = datetime.now(KST)
    today = now.strftime("%Y-%m-%d")

    todays = [r for r in state["runs"] if r["at"].startswith(today) and r.get("live")]
    if len(todays) >= cap:
        sys.exit(f"오늘 이미 {len(todays)}건 발행했습니다 (상한 {cap}). 중단합니다.")

    if todays:
        last = datetime.fromisoformat(todays[-1]["at"])
        gap = (now - last).total_seconds() / 60
        need = int(pub["min_gap_minutes"])
        if gap < need:
            sys.exit(f"직전 발행 후 {gap:.0f}분밖에 안 지났습니다 (최소 {need}분). 중단합니다.")


def blog_id_for(center: str) -> str:
    data = yaml.safe_load(TARGETS.read_text(encoding="utf-8"))
    for c in data["centers"]:
        if c["name"].lower() == center.lower():
            ids = c.get("blog_ids") or []
            if ids:
                return ids[0]
    sys.exit(f"config/targets.yml 에서 {center} 의 blog_ids 를 찾지 못했습니다.")


def paste_html(page, frame, selector: str, html: str) -> bool:
    """클립보드에 HTML 을 넣고 에디터에 붙여넣는다.

    스마트에디터는 붙여넣은 HTML 을 자기 컴포넌트(표·목록)로 변환해준다.
    직접 타이핑하면 표가 깨지기 때문에 이 방식을 쓴다.
    """
    try:
        page.evaluate(
            """async (html) => {
                const item = new ClipboardItem({
                    'text/html': new Blob([html], {type: 'text/html'}),
                    'text/plain': new Blob([html.replace(/<[^>]+>/g, ' ')], {type: 'text/plain'}),
                });
                await navigator.clipboard.write([item]);
            }""", html)
        if not try_click(frame, selector, "본문 칸", timeout=8000):
            return False
        page.wait_for_timeout(400)
        page.keyboard.press("Control+V")
        page.wait_for_timeout(1200)
        return True
    except Exception as exc:
        print(f"  ! 붙여넣기 실패: {exc}")
        return False


def dump_debug(page, frame, tag: str) -> None:
    """실패 지점의 화면과 후보 요소를 남긴다. 이 둘이면 셀렉터를 고칠 수 있다."""
    SHOTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(KST).strftime("%Y%m%d-%H%M%S")

    shot = SHOTS / f"debug-{tag}-{stamp}.png"
    try:
        page.screenshot(path=str(shot), full_page=True)
        print(f"  스크린샷: {shot}")
    except Exception as exc:
        print(f"  스크린샷 실패: {exc}")

    probe = SHOTS / f"debug-{tag}-{stamp}.txt"
    try:
        info = frame.evaluate("""() => {
            const btn = [...document.querySelectorAll('button')]
                .filter(b => b.offsetParent)
                .slice(0, 40)
                .map(b => `BUTTON "${(b.innerText||'').trim().slice(0,20)}" class=${b.className}`);
            const ed = [...document.querySelectorAll('[contenteditable="true"]')]
                .slice(0, 20)
                .map(e => `EDITABLE <${e.tagName.toLowerCase()}> class=${e.className}`);
            return [...btn, ...ed].join('\n');
        }""")
        probe.write_text(info, encoding="utf-8")
        print(f"  후보 요소 목록: {probe}")
        print("  이 두 파일을 Claude 에게 보내면 셀렉터를 고쳐드립니다.")
    except Exception as exc:
        print(f"  요소 수집 실패: {exc}")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    live = "--live" in sys.argv
    if not args:
        sys.exit("사용: python src/publish_naver.py <초안.md> [--live]")

    draft = Path(args[0])
    if not draft.exists():
        sys.exit(f"파일 없음: {draft}")

    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    if live and cfg["publish"].get("dry_run", True):
        sys.exit("config/content.yml 의 publish.dry_run 이 아직 true 입니다.\n"
                 "실제 발행하려면 false 로 바꾸세요. (한 번 더 생각하시라고 넣은 잠금장치입니다)")

    # 품질 게이트를 여기서 한 번 더 — 게이트를 건너뛴 발행은 없다
    fails = check(draft, cfg)
    if fails:
        print(f"✗ 품질 게이트 실패 — 발행하지 않습니다 ({draft.name})")
        for f in fails:
            print(f"   · {f}")
        sys.exit(1)

    state = load_state()
    if live:
        guardrails(cfg, state)

    meta, body_md = split_frontmatter(draft.read_text(encoding="utf-8"))
    title = meta.get("title") or draft.stem
    center = meta.get("center") or "Reboot"
    blog_id = blog_id_for(center)
    html = convert(body_md)

    print(f"제목: {title}")
    print(f"블로그: blog.naver.com/{blog_id}  ({center})")
    print(f"모드: {'실제 발행' if live else '임시저장 (dry run)'}")

    if not PROFILE.exists():
        sys.exit("로그인 세션이 없습니다. 먼저 실행하세요:  python src/naver_login.py")

    # 사람처럼 보이도록 약간의 랜덤 지연
    lo, hi = cfg["publish"]["jitter_minutes"]
    if live and hi > 0:
        wait = random.uniform(lo * 60, hi * 60)
        print(f"랜덤 지연 {wait/60:.1f}분 대기…")
        time.sleep(wait)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("pip install playwright && playwright install chromium")

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PROFILE), headless=False,
            viewport={"width": 1500, "height": 950},
            permissions=["clipboard-read", "clipboard-write"],
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            print("  [1/5] 글쓰기 화면 열기")
            page.goto(f"https://blog.naver.com/{blog_id}/postwrite",
                      wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(4000)

            frame = page.frame(name="mainFrame") or page.main_frame

            print("  [2/5] 팝업 정리")
            try_click(frame, SEL["restore_cancel"], "작성 중인 글 복구 팝업")
            page.wait_for_timeout(500)
            try_click(frame, SEL["help_close"], "도움말 팝업")
            page.wait_for_timeout(500)

            print("  [3/5] 제목 입력")
            if not try_click(frame, SEL["title"], "제목 칸", timeout=8000):
                print("  ! 제목 칸을 못 찾았습니다")
                dump_debug(page, frame, "title")
                sys.exit(1)
            page.keyboard.type(title, delay=random.randint(25, 70))
            page.wait_for_timeout(700)

            print("  [4/5] 본문 붙여넣기")
            if not paste_html(page, frame, SEL["body"], html):
                dump_debug(page, frame, "paste")
                sys.exit(1)

            print("  [5/5] " + ("발행" if live else "임시저장"))
            if live:
                if not try_click(frame, SEL["publish_open"], "발행 버튼", timeout=10000):
                    dump_debug(page, frame, "publish"); sys.exit(1)
                page.wait_for_timeout(1500)
                try_click(frame, SEL["publish_confirm"], "발행 확인", timeout=10000)
                page.wait_for_timeout(4000)
                print("  발행 완료")
                PUBLISHED.mkdir(parents=True, exist_ok=True)
                draft.rename(PUBLISHED / draft.name)
            else:
                if not try_click(frame, SEL["save_draft"], "저장 버튼", timeout=10000):
                    dump_debug(page, frame, "save"); sys.exit(1)
                page.wait_for_timeout(2500)
                print("  임시저장 완료 — 네이버 블로그에서 확인 후 직접 발행하세요.")

            state["runs"].append({
                "at": datetime.now(KST).isoformat(timespec="seconds"),
                "draft": draft.name, "title": title, "center": center, "live": live,
            })
            save_state(state)

        except Exception as exc:
            print(f"\n✗ 실패: {exc}")
            dump_debug(page, frame, "error")
            sys.exit(1)
        finally:
            page.wait_for_timeout(2000)
            ctx.close()


if __name__ == "__main__":
    main()
