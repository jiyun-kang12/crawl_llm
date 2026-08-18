from __future__ import annotations

import asyncio
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from crawl4ai import AsyncWebCrawler
from pydantic import BaseModel

from .config import Config
from .fetch import fetch_html
from .llm import classify_json

# 어떤 게시판 CMS든 URL 자체에 "다운로드"류 패턴이 박혀있는 경우가 많다.
# (예: req=download, fn_download, download.do, file_down 등)
STRONG_DOWNLOAD_URL_RE = re.compile(
    r"(req=download|fn_?download|file_?down|down\.php|download\.(do|php|jsp)|attach(File)?Down)",
    re.I,
)
FILE_EXT_RE = re.compile(r"\.(pdf|hwpx?|docx?|xlsx?|pptx?)(?:[?#]|$)", re.I)
# "board"는 넣지 않는다: 이런 류의 한국 공공기관 CMS는 사이트 전체 내비게이션이
# 통째로 /board/... 형태라서, 이 키워드가 있으면 실제 게시글이 아니라 상단
# 메뉴 전체가 "약한 후보"로 잡혀버린다 (실측으로 확인한 버그).
WEAK_HREF_KEYWORDS = ("download", "file", "attach", "down")
WEAK_TEXT_KEYWORDS = (
    "다운로드", "첨부", "PDF", "한글", "HWP", "DOC", "바로보기",
    ".pdf", ".hwp", ".hwpx", ".docx",
)

# "다음 페이지" 링크를 텍스트만으로 찾을 때 쓰는 후보 문자열.
NEXT_LINK_TEXTS = {"다음", "다음페이지", "다음 페이지", "next", "Next", ">", "»"}


class DownloadCandidate(BaseModel):
    href: str
    link_text: str
    context: str
    board_url: str
    strong_match: bool  # URL/확장자만으로 이미 문서 다운로드 링크임이 확실한 경우
    referer: str | None = None  # Referer 검사로 직접 접근을 막는 사이트용 (예: korean.go.kr download.do)


def looks_like_direct_download(url: str) -> bool:
    """메뉴에 게시판이 아니라 파일 하나를 바로 내려받는 링크가 섞여 있는 경우가
    있다 (예: "20년사" 같은 nav 항목이 게시판 목록이 아니라 req=download URL
    자체인 경우). 이런 URL은 HTML 페이지가 아니라 접속하자마자 다운로드가
    시작되므로, 브라우저로 크롤링을 시도하면 안 된다 — crawl4ai가 이런
    즉시-다운로드 URL로 goto()하면 "Download is starting"을 탐색 실패로 처리해
    버리는 버그가 있다 (실측 확인). 호출부에서 이 값이 True면 크롤링 없이 바로
    다운로드 후보로 취급해야 한다."""
    return bool(STRONG_DOWNLOAD_URL_RE.search(url) or FILE_EXT_RE.search(url))


BOARD_CONFIRM_SCHEMA = {
    "type": "object",
    "properties": {
        "is_relevant_board": {"type": "boolean"},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["is_relevant_board", "confidence", "reason"],
    "additionalProperties": False,
}
BOARD_SAMPLE_SIZE = 20


def extract_download_candidates(html: str, page_url: str, board_url: str) -> list[DownloadCandidate]:
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    out: list[DownloadCandidate] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith(("javascript:", "#", "mailto:", "tel:")):
            continue
        if a.find_parent(["nav", "header", "footer"]):
            # 게시판 목록 페이지에도 사이트 전체 내비게이션/푸터가 반복해서 실려
            # 있는데, 이건 그 게시판만의 게시글이 아니라 전 페이지 공통 요소라
            # 후보에서 제외한다.
            continue
        full = urljoin(page_url, href)
        text = a.get_text(strip=True)

        strong = bool(STRONG_DOWNLOAD_URL_RE.search(full)) or bool(FILE_EXT_RE.search(full))
        weak = (
            any(k in full.lower() for k in WEAK_HREF_KEYWORDS)
            or any(k in text for k in WEAK_TEXT_KEYWORDS)
        )
        if not (strong or weak):
            continue
        if full in seen:
            continue
        seen.add(full)

        # li/tr(게시글 한 건 전체)을 우선 찾는다. find_parent(["li","tr","div"])처럼
        # 한 번에 찾으면 가장 가까운 조상이 우선이라 버튼을 감싼 좁은 div에서
        # 멈춰버려 제목이 잘려나가는 문제가 있었다 (실측으로 확인).
        parent = a.find_parent(["li", "tr"]) or a.find_parent("div")
        context = parent.get_text(" ", strip=True)[:200] if parent else text
        out.append(
            DownloadCandidate(
                href=full, link_text=text, context=context,
                board_url=board_url, strong_match=strong,
            )
        )
    return out


def _find_next_page_url(html: str, page_url: str) -> str | None:
    """사이트마다 페이지네이션 파라미터명이 다르므로, URL을 직접 구성하지 않고
    HTML 안에서 '다음 페이지' 링크를 찾아 그대로 따라간다."""
    soup = BeautifulSoup(html, "html.parser")

    next_a = soup.select_one('a[rel="next"]')
    if next_a and next_a.get("href") and not next_a["href"].startswith(("javascript:", "#")):
        return urljoin(page_url, next_a["href"])

    for sel in (
        ".pagination a", ".paging a", ".page_navi a", ".board_paging a",
        ".pager a", ".page-navigation a",
    ):
        for a in soup.select(sel):
            text = a.get_text(strip=True)
            classes = " ".join(a.get("class", []))
            if text in NEXT_LINK_TEXTS or "next" in classes.lower():
                href = a.get("href")
                if href and not href.startswith(("javascript:", "#")):
                    return urljoin(page_url, href)

    return None


async def crawl_board(
    cfg: Config, crawler: AsyncWebCrawler, board_url: str
) -> list[DownloadCandidate]:
    all_candidates: list[DownloadCandidate] = []
    seen_hrefs: set[str] = set()
    seen_pages: set[str] = {board_url}

    current_url = board_url
    for page_num in range(1, cfg.max_pages_per_board + 1):
        try:
            html = await fetch_html(crawler, current_url, cfg)
        except Exception:
            break

        candidates = extract_download_candidates(html, current_url, board_url)
        new = [c for c in candidates if c.href not in seen_hrefs]
        for c in new:
            seen_hrefs.add(c.href)
        all_candidates.extend(new)

        if page_num >= cfg.max_pages_per_board:
            break
        next_url = _find_next_page_url(html, current_url)
        if not next_url or next_url in seen_pages:
            break
        seen_pages.add(next_url)
        current_url = next_url

    return all_candidates


async def confirm_board_relevance(
    cfg: Config, board_url: str, candidates: list[DownloadCandidate]
) -> tuple[bool, float, str]:
    """게시판을 통째로 신뢰할지 판단한다.

    링크 하나하나를 심사하는 대신, 게시판에서 뽑힌 게시글/첨부 샘플(제목+문맥)을
    보여주고 "이 게시판 자체가 문서 자료를 다루는가"만 LLM 1회 호출로 판단한다.
    확정되면 이 게시판에서 추출된 후보는 (강한/약한 매칭 구분 없이) 전부 다운로드
    대상이 된다 — 게시판이 이미 관련 게시판이면 그 안의 첨부파일은 거의 다 진짜
    문서이므로, 링크 단위 개별 심사보다 비용도 훨씬 싸고 사용자 기대(게시판 단위
    확정)에도 맞는다.
    """
    if not candidates:
        return False, 0.0, "추출된 후보 없음"
    if not cfg.use_llm:
        # LLM 없이는 URL 패턴이 확실한 후보가 하나라도 있으면 신뢰한다.
        has_strong = any(c.strong_match for c in candidates)
        return has_strong, 1.0 if has_strong else 0.0, "LLM 미사용: strong_match 존재 여부로 판단"

    sample = candidates[:BOARD_SAMPLE_SIZE]
    listing = "\n".join(f"- {c.link_text!r}: {c.context!r}" for c in sample)
    system = (
        "당신은 게시판에서 뽑은 게시글/첨부파일 샘플을 보고, 그 게시판 자체가 "
        "연구보고서·발간자료·논문·정기간행물 등 문서 자료를 다루는 게시판인지 "
        "판단하는 분류기입니다."
    )
    user = (
        f"게시판 URL: {board_url}\n\n"
        "아래는 이 게시판에서 추출한 게시글/첨부 링크 샘플(텍스트, 주변 문맥)입니다. "
        "이 게시판이 전반적으로 문서(PDF/HWP/DOCX 등 보고서·자료) 다운로드를 제공하는 "
        "게시판인지 판단하세요. 공지사항이라도 첨부파일이 실제 문서 자료면 "
        "관련 게시판으로 볼 수 있습니다. 로그인/사이트맵 같은 비문서성 페이지라면 "
        "false로 판단하세요.\n\n" + listing
    )
    result = await asyncio.to_thread(
        classify_json, system, user, BOARD_CONFIRM_SCHEMA, "board_confirmation", cfg.llm_provider
    )
    return (
        bool(result.get("is_relevant_board")),
        float(result.get("confidence", 0.0)),
        str(result.get("reason", "")),
    )
