from __future__ import annotations

import csv
import re
from pathlib import Path

ILLEGAL_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*]')


def sanitize_filename(name: str) -> str:
    name = ILLEGAL_FILENAME_CHARS.sub("_", name).strip()
    return name or "attachment"


def sanitize_csv_field(value: str) -> str:
    """엑셀 등에서 열었을 때 수식으로 해석되지 않도록 방어 (CSV/formula injection)."""
    if value and value[0] in ("=", "+", "-", "@"):
        return "'" + value
    return value


def site_wide_file_urls(site_root: Path) -> set[str]:
    """같은 site_id 아래 모든 게시판(B*)의 metadata.csv를 훑어 file_url 집합을 만든다.

    보드 하나의 metadata.csv만 보면, 같은 첨부파일이 다른 보드 목록에도 걸려 있는
    경우(예: 여러 게시판에 같은 자료가 중복 게시) 중복 다운로드를 놓친다. A번호
    (attachment_folder)는 보드별로 그대로 유지하되, file_url 중복 체크만 site_id
    전체 기준으로 한다.
    """
    urls: set[str] = set()
    if not site_root.exists():
        return urls
    for csv_path in site_root.glob("B*/metadata.csv"):
        with csv_path.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                url = row.get("file_url")
                if url:
                    urls.add(url)
    return urls


def board_attachment_start(board_metadata_csv: Path) -> int:
    """A번호(attachment_folder) 채번 시작값. 이 보드 자신의 metadata.csv에 이미
    쓰인 행 수부터 이어서 채번한다 (A번호는 사이트 전체가 아니라 보드별로 유지)."""
    if not board_metadata_csv.exists():
        return 0
    with board_metadata_csv.open(encoding="utf-8") as f:
        return sum(1 for _ in csv.DictReader(f))
