from __future__ import annotations

import asyncio
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from crawl4ai import AsyncWebCrawler
from pydantic import BaseModel

from .config import Config
from .fetch import fetch_html
from .llm import classify_json

# 특정 사이트의 클래스명에 의존하지 않도록, 여러 공공기관/기업 사이트에서 공통적으로
# 쓰이는 내비게이션 컨테이너 셀렉터를 폭넓게 잡는다.
NAV_SELECTORS = (
    "nav a, header a, .gnb a, .lnb a, .menu a, .nav a, "
    "ul.nav a, #gnb a, #lnb a, #menu a, .navbar a"
)

# LLM을 쓰지 않는 모두에서만 -> 메뉴 텍스트만으로 문서 게시판을 짐작할 때 쓰는 키워드.
DOCUMENT_MENU_KEYWORDS = (
    "보고서", "발간", "간행물", "자료실", "논문", "리포트", "출판물",
    "공지", "게시판", "소식", "동향", "연구", "브리핑",
)

MENU_SCHEMA = {
    "type": "object",
    "properties": {
        "selections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "href": {"type": "string"},
                    "relevance_score": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["text", "href", "relevance_score", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["selections"],
    "additionalProperties": False,
}


class MenuLink(BaseModel):
    text: str
    href: str


class MenuSelection(BaseModel):
    text: str
    href: str
    relevance_score: float
    reason: str


def extract_menu_links(html: str, base_url: str) -> list[MenuLink]:
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    links: list[MenuLink] = []
    for a in soup.select(NAV_SELECTORS):
        href = a.get("href")
        text = a.get_text(strip=True)
        if not href or not text or href.startswith(("javascript:", "#", "mailto:", "tel:")):
            continue
        full = urljoin(base_url, href)
        if full in seen:
            continue
        seen.add(full)
        links.append(MenuLink(text=text, href=full))
    return links


def _heuristic_select(links: list[MenuLink]) -> list[MenuSelection]:
    out = []
    for link in links:
        if any(kw in link.text for kw in DOCUMENT_MENU_KEYWORDS):
            out.append(
                MenuSelection(
                    text=link.text,
                    href=link.href,
                    relevance_score=1.0,
                    reason="키워드 휴리스틱 매칭 (LLM 미사용)",
                )
            )
    return out


async def discover_document_boards(
    cfg: Config, crawler: AsyncWebCrawler
) -> list[MenuSelection]:
    """메인 페이지의 내비게이션에서 문서(보고서/자료) 게시판으로 보이는 메뉴만 골라낸다.

    사이트마다 메뉴 구조와 URL 패턴이 다르므로, href 패턴에는 의존하지 않고
    (텍스트, href) 쌍만 보고 판단한다.
    """
    start_url = urljoin(cfg.base_url, cfg.start_path)
    html = await fetch_html(crawler, start_url, cfg)
    links = extract_menu_links(html, cfg.base_url)

    if not links:
        return []

    if not cfg.use_llm:
        return _heuristic_select(links)

    listing = "\n".join(f"- text: {l.text!r}, href: {l.href}" for l in links)
    system = (
        "당신은 공공기관/기업 웹사이트의 메뉴 목록을 보고, 연구보고서·발간자료·논문· "
        "정기간행물·보도자료 등 '문서를 다운로드할 수 있는 게시판'으로 이어지는 메뉴를 "
        "가려내는 분류기입니다."
    )
    user = (
        "아래는 웹사이트 내비게이션 메뉴에서 추출한 (텍스트, href) 목록입니다. "
        "이 중 보고서/발간자료/논문/간행물/동향자료 등 문서 게시판으로 연결되는 메뉴만 "
        "골라 관련도 점수(0~1)를 매기세요. 로그인, 사이트맵, 채용정보, 입찰정보, "
        "인사말/조직도 같은 정적 소개 페이지, 민원/신고센터 등 문서 게시판이 아닌 메뉴는 "
        "제외하세요. 확신이 없으면 낮은 점수를 주세요.\n\n" + listing
    )
    result = await asyncio.to_thread(
        classify_json, system, user, MENU_SCHEMA, "menu_selection", cfg.llm_provider
    )
    selections = [MenuSelection.model_validate(s) for s in result.get("selections", [])]
    return [s for s in selections if s.relevance_score >= cfg.min_menu_relevance]
