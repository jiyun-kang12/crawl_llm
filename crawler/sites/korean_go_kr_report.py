"""국립국어원 보고서·연구자료 게시판(korean.go.kr/front/reportData, mn_id=207) 전용
목록/상세 페이지 파서.

파일 다운로드 자체는 재구현하지 않고 downloader.py의 download_candidate()를 그대로
쓴다 — 재시도, Content-Disposition 기반 파일명/확장자 판별, 허용 확장자 필터링이
generic 파이프라인과 동일해야 하기 때문이다. 이 모듈이 site-specific하게 하는 일은
딱 두 가지뿐이다: (1) 이 게시판의 표 컬럼(번호/서명/참여자/펴낸곳/펴낸때/등록일/
자료조회) 구조를 읽어 메타데이터를 뽑는 것, (2) 게시물당 첨부가 여러 개여도 대표
파일 1개만 골라 DownloadCandidate 하나로 넘기는 것.

그 위에 이 스크립트가 추가하는 원칙 두 가지:
  - file_url 중복 체크를 이 보드 하나가 아니라 같은 site_id의 모든 보드 기준으로
    한다 (A번호 자체는 기존과 동일하게 보드별로 유지).
  - 게시물 하나에 첨부파일이 여러 개 있어도 행을 늘리지 않고 대표 파일 1개만 받는다.

사용법 (crawl_llm 폴더에서):
  python -m crawler.sites.korean_go_kr_report --tier T2 --site-id S45
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import random
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup

from ..board import DownloadCandidate
from ..config import Config
from ..downloader import build_client_session, download_candidate
from .common import board_attachment_start, sanitize_csv_field, sanitize_filename, site_wide_file_urls

BASE_URL = "https://www.korean.go.kr"
LIST_URL = f"{BASE_URL}/front/reportData/reportDataList.do"
MN_ID = "207"

METADATA_FIELDNAMES = [
    "site_id", "folder_name", "attachment_folder", "local_file_name",
    "report_seq", "title", "participants", "publisher", "pub_year",
    "written_date", "view_count", "view_url", "file_name", "file_url", "file_path",
]

REQUEST_DELAY_RANGE = (1.0, 2.0)
MAX_EMPTY_PAGES_BEFORE_STOP = 1


async def polite_delay() -> None:
    await asyncio.sleep(random.uniform(*REQUEST_DELAY_RANGE))


async def get_html(session: aiohttp.ClientSession, url: str, params: dict | None = None) -> str:
    async with session.get(url, params=params) as resp:
        resp.raise_for_status()
        return await resp.text()


def parse_list_rows(html: str) -> list[dict]:
    """목록 표 열 순서: 번호, 서명(제목), 참여자, 펴낸 곳, 펴낸 때, 등록일, 자료조회."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", attrs={"data-brl-tbltype": "1"}) or soup.find("table")
    if not table:
        return []
    tbody = table.find("tbody") or table

    rows = []
    for tr in tbody.find_all("tr"):
        link = tr.select_one("td.subject a[href]")
        if not link:
            continue
        href = link["href"]
        qs = parse_qs(urlparse(href).query)
        report_seq = (qs.get("report_seq") or [""])[0]
        if not report_seq:
            continue
        title = link.get_text(strip=True)

        tds = tr.find_all("td")
        participants = tds[2].get_text(strip=True) if len(tds) > 2 else ""
        publisher = tds[3].get_text(strip=True) if len(tds) > 3 else ""
        pub_year = tds[4].get_text(strip=True) if len(tds) > 4 else ""
        written_date = tds[5].get_text(strip=True) if len(tds) > 5 else ""
        view_count = tds[6].get_text(strip=True) if len(tds) > 6 else ""

        rows.append({
            "report_seq": report_seq,
            "title": title,
            "participants": participants,
            "publisher": publisher,
            "pub_year": pub_year,
            "written_date": written_date,
            "view_count": view_count,
            "view_url": urljoin(BASE_URL, href),
        })
    return rows


async def parse_representative_attachment(session: aiohttp.ClientSession, view_url: str) -> dict | None:
    """상세 페이지 첨부 목록에서 대표 파일 1개만 고른다 (게시물당 파일 1개 원칙 —
    첨부가 여러 개여도 목록에 나온 순서상 첫 번째를 쓴다)."""
    html = await get_html(session, view_url)
    soup = BeautifulSoup(html, "html.parser")

    file_list = soup.select_one("div.file ul.fileList")
    if not file_list:
        return None
    a_tag = file_list.find("a", href=True)
    if not a_tag:
        return None

    file_url = urljoin(BASE_URL, a_tag["href"])
    file_name = sanitize_filename(a_tag.get_text(strip=True))
    return {"file_name": file_name, "file_url": file_url}


async def collect_list_rows(
    session: aiohttp.ClientSession, start_page: int, max_pages: int | None
) -> list[dict]:
    print("Fetching list pages...")
    all_rows: list[dict] = []
    page = start_page
    empty_streak = 0
    while True:
        if max_pages is not None and page - start_page >= max_pages:
            break
        html = await get_html(
            session, LIST_URL, params={"mn_id": MN_ID, "searchOrder": "years", "pageIndex": str(page)}
        )
        rows = parse_list_rows(html)
        if not rows:
            empty_streak += 1
            if empty_streak > MAX_EMPTY_PAGES_BEFORE_STOP:
                break
        else:
            empty_streak = 0
            all_rows.extend(rows)
            print(f"  page {page}: {len(rows)} row(s) (running total {len(all_rows)})")
        page += 1
        await polite_delay()

    return all_rows


async def run(
    tier: str, site_id: str, board_label: str, output_dir: Path,
    start_page: int, max_pages: int | None,
) -> None:
    output_root = output_dir / tier / site_id
    board_dir = output_root / board_label
    metadata_csv = board_dir / "metadata.csv"
    board_dir.mkdir(parents=True, exist_ok=True)

    cfg = Config(base_url=BASE_URL, output_dir=output_dir)

    async with build_client_session(cfg) as session:
        rows = await collect_list_rows(session, start_page, max_pages)
        print(f"Found report rows: {len(rows)}")

        # A번호는 보드별로 유지하지만, file_url 중복 체크는 site_id 전체를 본다.
        seen_urls = site_wide_file_urls(output_root)
        att_counter = board_attachment_start(metadata_csv)
        print(f"Existing file_url across site {site_id}: {len(seen_urls)} (this board starts at A{att_counter + 1})")

        sem = asyncio.Semaphore(cfg.concurrency)
        total = len(rows)
        write_header = not metadata_csv.exists()
        with open(metadata_csv, "a", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=METADATA_FIELDNAMES)
            if write_header:
                writer.writeheader()

            for idx, row in enumerate(rows, start=1):
                pct = idx / total * 100 if total else 100
                attachment = await parse_representative_attachment(session, row["view_url"])
                if attachment is None:
                    print(f"[{idx}/{total} {pct:.1f}%] {row['report_seq']}: 첨부 없음 - 스킵 - {row['title']}")
                    continue

                attachment_folder = f"A{att_counter + 1}"
                dest_dir = board_dir / attachment_folder / "Raw"
                candidate = DownloadCandidate(
                    href=attachment["file_url"], link_text=attachment["file_name"],
                    context=row["title"], board_url=row["view_url"], strong_match=True,
                    referer=row["view_url"],  # korean.go.kr download.do는 Referer 없으면 403
                )
                result = await download_candidate(session, candidate, cfg, sem, dest_dir, seen_urls)

                if result.status == "cached":
                    print(f"[{idx}/{total} {pct:.1f}%] {row['report_seq']}: 이미 site 내 다른 게시물에서 받음 - 스킵")
                    continue
                if result.status != "ok":
                    print(f"[{idx}/{total} {pct:.1f}%] {row['report_seq']}: 실패({result.status}/{result.error}) - 스킵")
                    continue

                att_counter += 1
                ext = f".{result.extension}" if result.extension else Path(result.saved_path).suffix
                local_file_name = f"{site_id}.{board_label}.{attachment_folder}{ext}".lower()
                final_path = dest_dir / local_file_name
                Path(result.saved_path).rename(final_path)

                writer.writerow({
                    "site_id": site_id,
                    "folder_name": board_label,
                    "attachment_folder": attachment_folder,
                    "local_file_name": local_file_name,
                    "report_seq": row["report_seq"],
                    "title": sanitize_csv_field(row["title"]),
                    "participants": sanitize_csv_field(row["participants"]),
                    "publisher": sanitize_csv_field(row["publisher"]),
                    "pub_year": row["pub_year"],
                    "written_date": row["written_date"],
                    "view_count": row["view_count"],
                    "view_url": row["view_url"],
                    "file_name": attachment["file_name"],
                    "file_url": attachment["file_url"],
                    "file_path": str(final_path),
                })
                csv_file.flush()
                print(f"[{idx}/{total} {pct:.1f}%] {row['report_seq']}: downloaded -> {board_label}/{attachment_folder}/Raw/{local_file_name}")
                await polite_delay()

    print(f"Done. Metadata saved to: {metadata_csv}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tier", required=True, help="예: T2")
    parser.add_argument("--site-id", required=True, help="다른 사이트와 겹치지 않는 site 번호, 예: S45")
    parser.add_argument("--board-label", default="B1")
    parser.add_argument("--output-dir", default=str(Path(__file__).resolve().parents[2] / "output"))
    parser.add_argument("--start-page", type=int, default=1)
    parser.add_argument("--max-pages", type=int, default=None, help="테스트용 페이지 수 제한")
    args = parser.parse_args()

    asyncio.run(run(
        tier=args.tier,
        site_id=args.site_id,
        board_label=args.board_label,
        output_dir=Path(args.output_dir),
        start_page=args.start_page,
        max_pages=args.max_pages,
    ))


if __name__ == "__main__":
    main()
