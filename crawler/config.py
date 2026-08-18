from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

ALLOWED_EXTENSIONS = {"pdf", "hwp", "hwpx", "docx", "doc", "xlsx", "pptx"}
CONVERTIBLE_EXTENSIONS = {"pdf", "docx", "doc", "xlsx", "pptx"}  # hwp/hwpx: 다운로드만, 변환은 생략
DOWNLOAD_METHODS = ("attachment", "crawl", "api")
PROFILE_SAMPLE_SIZE = 5


@dataclass
class Config:
    base_url: str
    start_path: str = "/main"
    output_dir: Path = field(default_factory=lambda: Path("./output"))

    max_pages_per_board: int = 5
    concurrency: int = 8
    request_timeout: int = 30
    limit_downloads_per_board: int | None = None  # 확정된 게시판별 다운로드 개수 상한 (테스트용)

    use_llm: bool = True
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "anthropic"))
    min_menu_relevance: float = 0.5
    min_board_confidence: float = 0.6

    # site별 config.yaml에서 읽어오는 대신, 이 실행에 포함된 모든 게시판에
    # 일괄 적용하는 값. board_search 단계(게시판별 config.yaml)는 아직 없음.
    download_method: str = "attachment"

    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 gdi-doc-crawler/1.0"
    )

    ALLOWED_EXTENSIONS = ALLOWED_EXTENSIONS
    CONVERTIBLE_EXTENSIONS = CONVERTIBLE_EXTENSIONS

    def __post_init__(self) -> None:
        self.output_dir = Path(self.output_dir)

    @property
    def raw_dir(self) -> Path:
        return self.output_dir / "raw"

    @property
    def manifest_path(self) -> Path:
        return self.output_dir / "manifest.json"

    @property
    def profile_csv_path(self) -> Path:
        return self.output_dir / "board_profile.csv"

    @property
    def site_name(self) -> str:
        host = urlparse(self.base_url).netloc.lower()
        host = re.sub(r"^www\.", "", host)
        return host or "site"

    def samples_dir(self, board_name: str) -> Path:
        return self.output_dir / "sites" / self.site_name / "samples" / board_name
