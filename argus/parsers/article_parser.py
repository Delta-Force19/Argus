from collections.abc import Callable

import httpx
import trafilatura

from argus.text_quality import is_probably_readable_text


ARTICLE_REQUEST_HEADERS = {
    "Accept-Encoding": "gzip, deflate",
    "User-Agent": "Argus/0.1.1",
}


def extract_article_text(
        url: str,
        *,
        downloader: Callable[[str], str | None] = trafilatura.fetch_url,
        fallback_downloader: Callable[[str], str] | None = None,
) -> str | None:
    """Download and extract an article while rejecting corrupted text."""

    downloaded = downloader(url)
    text = _extract_readable_text(downloaded)
    if text is not None:
        return text

    fallback = fallback_downloader or _download_html_without_brotli
    downloaded = fallback(url)
    return _extract_readable_text(downloaded)


def _download_html_without_brotli(url: str) -> str:
    """Retry with encodings supported by the installed HTTP stack."""

    with httpx.Client(
            follow_redirects=True,
            headers=ARTICLE_REQUEST_HEADERS,
            timeout=30,
    ) as client:
        response = client.get(url)
        response.raise_for_status()
        content_type = response.headers.get(
            "content-type",
            "",
        ).casefold()
        if content_type and not any(
                allowed_type in content_type
                for allowed_type in (
                    "text/html",
                    "application/xhtml+xml",
                )
        ):
            raise ValueError(
                f"Unsupported article content type: {content_type}."
            )
        return response.text


def _extract_readable_text(downloaded: str | None) -> str | None:
    if downloaded is None:
        return None
    text = trafilatura.extract(downloaded)
    if not is_probably_readable_text(text):
        return None
    return text
