from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup

# 사이트마다 "총 게시물 수" 표기 방식이 달라 특정 태그에 의존하지 않고,
# id/class에 흔히 쓰이는 키워드와 "총 N건"류 텍스트 패턴을 함께 시도한다.
POST_COUNT_ATTR_RE = re.compile(r"(tot(al)?[_-]?cnt|tot(al)?[_-]?count|list[_-]?cnt|pageCurrEIndex)", re.I)
POST_COUNT_TEXT_RE = re.compile(r"(?:총|전체)\s*[:：]?\s*([\d,]+)\s*건")

LICENSE_PUBLIC_RE = re.compile(r"공공누리")
LICENSE_PRIVATE_RE = re.compile(r"저작권")

# Tier 임계값은 실측 샘플이 쌓이기 전까지의 잠정치다 (합리적 기본값으로 시작하기로
# 사용자와 합의). tier_reason에 근거 수치를 남겨 사람이 최종 확인하도록 한다.
TIER1_MAX_VISUAL_PER_PAGE = 0.1   # 페이지당 시각요소(이미지+표) 이 미만이면 "거의 텍스트뿐"
TIER2_MAX_VISUAL_PER_PAGE = 0.5   # 이 미만이면 "텍스트 비중 높음", 이상이면 "시각요소 비중 높음"
LOW_TEXT_PER_PAGE = 200           # 페이지당 평균 텍스트가 이 미만이면 저텍스트로 보고 Tier4


@dataclass
class PdfPageStats:
    text_chars: int
    image_count: int
    table_count: int


@dataclass
class PdfAnalysis:
    page_count: int
    pages: list[PdfPageStats]


@dataclass
class BoardProfile:
    site: str
    board: str
    board_url: str
    download_method: str
    data_format: str
    post_count: str
    avg_length: str
    multimodal: str
    license: str
    tier_candidate: str
    tier_reason: str


PROFILE_FIELDS = [
    "site", "board", "board_url", "download_method", "data_format",
    "post_count", "avg_length", "multimodal", "license",
    "tier_candidate", "tier_reason",
]


def analyze_pdf(path: Path) -> PdfAnalysis | None:
    """PDF 한 개를 페이지별 텍스트 글자수/이미지 개수/표 개수로 분석한다.
    암호화·손상 등으로 열지 못하면 None을 돌려주고, 호출부는 이를 '파싱 실패'로 취급한다."""
    import pdfplumber

    try:
        pages: list[PdfPageStats] = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                image_count = len(page.images)
                try:
                    table_count = len(page.find_tables())
                except Exception:
                    table_count = 0
                pages.append(PdfPageStats(len(text), image_count, table_count))
        return PdfAnalysis(page_count=len(pages), pages=pages)
    except Exception:
        return None


def average_page_count(analyses: list[PdfAnalysis | None]) -> float | None:
    valid = [a for a in analyses if a is not None]
    if not valid:
        return None
    return sum(a.page_count for a in valid) / len(valid)


def detect_multimodal(analyses: list[PdfAnalysis | None]) -> list[str]:
    types = ["text"]
    valid = [a for a in analyses if a is not None]
    if any(p.image_count > 0 for a in valid for p in a.pages):
        types.append("image")
    if any(p.table_count > 0 for a in valid for p in a.pages):
        types.append("table")
    return types


def candidate_tier(analyses: list[PdfAnalysis | None]) -> tuple[str, str]:
    """페이지당 평균 시각요소 개수와 평균 텍스트 글자수로 Tier 1~4 '후보'를 매긴다.
    경계값이 애매할 수 있어 후보일 뿐이며, tier_reason에 근거 수치를 남겨
    사람이 최종 확인하도록 한다."""
    valid = [a for a in analyses if a is not None]
    if not valid:
        return "Tier4", "PDF 샘플 파싱 실패: 분석 가능한 샘플 없음"

    total_pages = sum(a.page_count for a in valid)
    if total_pages == 0:
        return "Tier4", "PDF 샘플에 페이지 없음"

    total_chars = sum(p.text_chars for a in valid for p in a.pages)
    total_visual = sum(p.image_count + p.table_count for a in valid for p in a.pages)
    text_per_page = total_chars / total_pages
    visual_per_page = total_visual / total_pages

    stats = f"(페이지당 평균 텍스트 {text_per_page:.0f}자, 시각요소 {visual_per_page:.2f}개, 샘플 {len(valid)}/{len(analyses)}개 파싱)"

    if text_per_page < LOW_TEXT_PER_PAGE:
        return "Tier4", f"저텍스트 {stats}"
    if visual_per_page < TIER1_MAX_VISUAL_PER_PAGE:
        return "Tier1", f"거의 텍스트뿐 {stats}"
    if visual_per_page < TIER2_MAX_VISUAL_PER_PAGE:
        return "Tier2", f"텍스트 비중 높음 {stats}"
    return "Tier3", f"시각요소 비중 상대적으로 높음 {stats}"


def detect_license(html: str) -> str:
    """게시판 목록 페이지 HTML에서 '공공누리'/'저작권' 문구만 훑는 휴리스틱.
    더 정교화하지 않기로 합의된 부분이라, 문구가 없으면 '미확인'으로 둔다."""
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    if LICENSE_PUBLIC_RE.search(text):
        return "public"
    if LICENSE_PRIVATE_RE.search(text):
        return "private"
    return "미확인"


def extract_post_count(html: str) -> int | None:
    """게시판 목록 페이지에서 총 게시물 수를 찾는다. 사이트마다 마크업이 달라
    특정 태그 하나에 의존하지 않고, id/class 키워드와 텍스트 패턴을 순서대로 시도한다."""
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup.find_all(attrs={"id": POST_COUNT_ATTR_RE}):
        m = re.search(r"[\d,]+", tag.get_text())
        if m:
            return int(m.group().replace(",", ""))
    for tag in soup.find_all(attrs={"class": POST_COUNT_ATTR_RE}):
        m = re.search(r"[\d,]+", tag.get_text())
        if m:
            return int(m.group().replace(",", ""))

    text = soup.get_text(" ", strip=True)
    m = POST_COUNT_TEXT_RE.search(text)
    if m:
        return int(m.group(1).replace(",", ""))
    return None


def write_profile_csv(path: Path, rows: list[BoardProfile]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=PROFILE_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.__dict__)
