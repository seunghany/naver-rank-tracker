"""초안 마크다운 → 스마트에디터에 붙여넣을 HTML.

우리 출력 규격(## 소제목 / 불릿 / 번호 / 표 / **굵게**)만 처리한다.
범용 마크다운 변환기가 아니라, 우리가 만드는 형태만 확실히 변환하는 쪽을 택했다.
"""
from __future__ import annotations

import html
import re

FACT_RE = re.compile(r"\s*\[fact:[a-zA-Z0-9_\-]+\]")
# <!-- 📷 ... --> 형태의 사진 배치 지시. 발행본에는 나가지 않는다.
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\)]+)\)")


def inline(text: str) -> str:
    text = FACT_RE.sub("", text)              # 인용 마커는 발행본에서 제거
    text = html.escape(text.strip())
    text = BOLD_RE.sub(r"<strong>\1</strong>", text)
    text = LINK_RE.sub(r'<a href="\2">\1</a>', text)
    return text


def _table(rows: list[str]) -> str:
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    # 두 번째 줄이 구분선(---)이면 버린다
    if len(cells) > 1 and all(set(c) <= set("-: ") for c in cells[1]):
        head, body = cells[0], cells[2:]
    else:
        head, body = cells[0], cells[1:]
    out = ["<table><thead><tr>"]
    out += [f"<th>{inline(c)}</th>" for c in head]
    out.append("</tr></thead><tbody>")
    for row in body:
        out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in row) + "</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def convert(md: str) -> str:
    md = COMMENT_RE.sub("", md)      # 사진 지시 제거
    lines = md.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()

        if not line.strip():
            i += 1
            continue

        if line.startswith("|"):                                   # 표
            block = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                block.append(lines[i])
                i += 1
            out.append(_table(block))
            continue

        m = re.match(r"^(#{1,4})\s+(.*)", line)                    # 소제목
        if m:
            level = min(4, max(2, len(m.group(1))))                # ## → h2, ### → h3
            out.append(f"<h{level}>{inline(m.group(2))}</h{level}>")
            i += 1
            continue

        if re.match(r"^\s*[-*]\s+\S", line):                       # 불릿
            items = []
            while i < len(lines) and re.match(r"^\s*[-*]\s+\S", lines[i]):
                items.append(inline(re.sub(r"^\s*[-*]\s+", "", lines[i])))
                i += 1
            out.append("<ul>" + "".join(f"<li>{t}</li>" for t in items) + "</ul>")
            continue

        if re.match(r"^\s*\d+\.\s+\S", line):                      # 번호 목록
            items = []
            while i < len(lines) and re.match(r"^\s*\d+\.\s+\S", lines[i]):
                items.append(inline(re.sub(r"^\s*\d+\.\s+", "", lines[i])))
                i += 1
            out.append("<ol>" + "".join(f"<li>{t}</li>" for t in items) + "</ol>")
            continue

        para = [line]                                              # 본문 문단
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(
                r"^(\s*[-*]\s|\s*\d+\.\s|#{1,4}\s|\|)", lines[i]):
            para.append(lines[i].rstrip())
            i += 1
        out.append(f"<p>{inline(' '.join(para))}</p>")

    return "\n".join(out)
