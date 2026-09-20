#!/usr/bin/env python3
"""M1 → M2 → M3 을 한 번에. 발행(M4)은 일부러 분리해 두었다.

사용:
    python src/pipeline.py 철산하이록스 Reboot
    → 글감 분석 → 초안 생성 → 품질 게이트까지. 통과하면 발행 명령을 안내한다.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KST = timezone(timedelta(hours=9))


def run(step: str, cmd: list[str]) -> None:
    print(f"\n── {step} " + "─" * (60 - len(step)))
    if subprocess.run(cmd, cwd=ROOT).returncode != 0:
        sys.exit(f"\n{step} 에서 중단되었습니다.")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    passthru = [a for a in sys.argv[1:] if a.startswith("--provider=")]
    if len(args) < 2:
        sys.exit("사용: python src/pipeline.py <키워드> <Reboot|TeamPoise> "
                 "[--provider=anthropic|claude-code]")
    keyword, center = args[0], args[1]
    today = datetime.now(KST).strftime("%Y-%m-%d")

    brief = ROOT / "content" / "briefs" / f"{today}-{keyword}.json"
    draft = ROOT / "content" / "drafts" / f"{today}-{keyword}.md"

    run("M1 글감 발굴", [sys.executable, "src/find_topics.py", keyword])
    run("M2 초안 생성", [sys.executable, "src/write_draft.py", str(brief), center] + passthru)
    run("M3 품질 게이트", [sys.executable, "src/quality_gate.py", str(draft)])

    print(f"""
통과했습니다. 발행하려면:

  임시저장만   python src/publish_naver.py {draft.relative_to(ROOT)}
  실제 발행    python src/publish_naver.py {draft.relative_to(ROOT)} --live

처음 몇 편은 임시저장으로 돌려서 결과를 눈으로 확인하시길 권합니다.""")


if __name__ == "__main__":
    main()
