from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ALLOWED_EXTENSIONS = {"pdf", "hwp", "hwpx", "docx", "doc", "xlsx", "pptx"}
CONVERTIBLE_EXTENSIONS = {"pdf", "docx", "doc", "xlsx", "pptx"}  # hwp/hwpx: 다운로드만, 변환은 생략


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
    def markdown_dir(self) -> Path:
        return self.output_dir / "markdown"

    @property
    def manifest_path(self) -> Path:
        return self.output_dir / "manifest.json"
