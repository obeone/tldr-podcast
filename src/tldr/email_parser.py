"""
Parser for TLDR newsletter emails.

Parses raw MIME bytes into a list of :class:`Article` dataclasses, one per
article item found in the newsletter.  Sponsor entries are automatically
filtered out.  The raw text is extracted from the ``text/plain`` MIME part
and decoded by Python's standard :mod:`email` module (quoted-printable
encoding is handled transparently).

Article headers span one or two physical lines and follow the pattern::

    TITLE IN ALL CAPS (N MINUTE READ) [link_num]

Sections (theme headers such as "BIG TECH & STARTUPS") are short, all-caps
lines that appear before groups of articles.

The "Links:" block at the end of the email maps ``[link_num]`` to a real URL.
"""

from __future__ import annotations

import email
import logging
import re
from dataclasses import dataclass, field
from email.message import Message

import coloredlogs

logger = logging.getLogger(__name__)
coloredlogs.install(
    level="DEBUG",
    logger=logger,
    fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


# ---------------------------------------------------------------------------
# Public exceptions
# ---------------------------------------------------------------------------


class ParseError(Exception):
    """Raised when the raw bytes cannot be interpreted as an email."""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Article:
    """
    Represents a single article extracted from a TLDR newsletter.

    Attributes
    ----------
    title : str
        The article title (all-caps as written in the newsletter).
    summary : str
        The short summary paragraph below the article title.
    url : str
        The resolved URL for the article.
    section : str
        The theme section the article belongs to (e.g. "BIG TECH & STARTUPS").
    full_text : str
        Full article body fetched by the scraper (populated later, default "").
    """

    title: str
    summary: str
    url: str
    section: str
    full_text: str = field(default="")


# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------

# Patterns that identify sponsor / advertisement content to skip.
_SPONSOR_PATTERNS: tuple[re.Pattern, ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"TOGETHER\s+WITH",
        r"\bSPONSOR\b",
        r"\bADVERTISEMENT\b",
        r"\bPRESENTED\s+BY\b",
    )
)

# Matches an article header after line-joining / normalisation.
# The title is all-caps (with punctuation), followed by "(N MINUTE READ) [link]".
# Also handles "(GITHUB REPO)", "(RESOURCE)" etc. as non-minute variants that
# should still be captured.
_ARTICLE_HEADER_RE = re.compile(
    r"^((?:[A-Z0-9\u2191-\u2199][A-Z0-9 ,'\u2019\u2018&!:?/.\-\+\(\)\u2013\u2014=↗️]+?))"
    r"\s+\((\d+) MINUTE READ\)\s+\[(\d+)\]\s*$",
)

# Matches a non-minute article variant: (GITHUB REPO), (RESOURCE), etc.
_ARTICLE_HEADER_ALT_RE = re.compile(
    r"^((?:[A-Z0-9][A-Z0-9 ,'\u2019\u2018&!:?/.\-\+\(\)\u2013\u2014=↗️]+?))"
    r"\s+\((?:GITHUB REPO|RESOURCE|VIDEO|TOOL|BOOK)\)\s+\[(\d+)\]\s*$",
)

# Detects a section / theme line: short, all-uppercase, no brackets.
_SECTION_RE = re.compile(
    r"^[A-Z][A-Z0-9 &/,'\u2013\u2014\-!]{2,58}$",
)

# Links section marker.
_LINKS_SECTION_RE = re.compile(r"^Links:\s*$", re.MULTILINE)

# Individual link line: "[N] url"
_LINK_LINE_RE = re.compile(r"^\[(\d+)\]\s+(https?://\S+)", re.MULTILINE)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _get_plain_text(msg: Message) -> str:
    """
    Extract and decode the ``text/plain`` MIME part from a parsed email.

    Parameters
    ----------
    msg : email.message.Message
        A parsed email message.

    Returns
    -------
    str
        The decoded plain-text body, with line endings normalised to ``\\n``.

    Raises
    ------
    ParseError
        If no ``text/plain`` part is found in the message.
    """
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            payload = part.get_payload(decode=True)
            charset = part.get_content_charset("utf-8") or "utf-8"
            text = payload.decode(charset, errors="replace")
            return text.replace("\r\n", "\n").replace("\r", "\n")
    raise ParseError("No text/plain part found in the email.")


def _build_link_map(text: str) -> dict[str, str]:
    """
    Parse the "Links:" footer section and return a mapping of link number to URL.

    Parameters
    ----------
    text : str
        The full plain-text body of the newsletter.

    Returns
    -------
    dict[str, str]
        Mapping ``{link_number_str: url}``, e.g. ``{"5": "https://…"}``.
    """
    link_map: dict[str, str] = {}
    match = _LINKS_SECTION_RE.search(text)
    if not match:
        logger.warning("No 'Links:' section found in email body.")
        return link_map

    links_block = text[match.start():]
    for m in _LINK_LINE_RE.finditer(links_block):
        link_map[m.group(1)] = m.group(2)

    logger.debug("Parsed %d link entries from Links: section.", len(link_map))
    return link_map


def _normalise_paragraph(para: str) -> str:
    """
    Normalise a paragraph by joining its lines with a single space.

    Lines are stripped individually; empty lines within the paragraph are
    removed.  This lets us match article headers that are word-wrapped across
    two physical lines.

    Parameters
    ----------
    para : str
        A paragraph block (lines separated by ``\\n``).

    Returns
    -------
    str
        The paragraph with all lines joined by a space.
    """
    return " ".join(line.strip() for line in para.splitlines() if line.strip())


def _is_section_line(text: str) -> bool:
    """
    Return True if *text* (after normalisation) looks like a TLDR section header.

    A section header is a short, all-uppercase line with no square brackets
    and not a TLDR date line (e.g. "TLDR DEVOPS 2026-02-20").

    Parameters
    ----------
    text : str
        A stripped single-line string to test.

    Returns
    -------
    bool
        ``True`` if the line is identified as a section header.
    """
    if not text or len(text) > 60:
        return False
    if text != text.upper():
        return False
    if "[" in text or "]" in text:
        return False
    if text.startswith("TLDR") and re.search(r"\d{4}", text):
        return False
    return bool(_SECTION_RE.match(text))


def _is_sponsor(title: str, section: str) -> bool:
    """
    Return True if the article title or its section matches a sponsor pattern.

    Parameters
    ----------
    title : str
        The article title.
    section : str
        The current theme section name.

    Returns
    -------
    bool
        ``True`` if the article should be excluded as sponsor content.
    """
    combined = f"{title} {section}"
    for pat in _SPONSOR_PATTERNS:
        if pat.search(combined):
            return True
    return False


def _try_match_article_header(normalised: str) -> tuple[str, str] | None:
    """
    Attempt to match a normalised paragraph string as an article header.

    Tries both the standard "(N MINUTE READ)" pattern and the alternative
    "(GITHUB REPO|RESOURCE|…)" pattern.

    Parameters
    ----------
    normalised : str
        A single-line string produced by :func:`_normalise_paragraph`.

    Returns
    -------
    tuple[str, str] or None
        ``(title, link_num)`` if the string is a valid article header,
        otherwise ``None``.
    """
    m = _ARTICLE_HEADER_RE.match(normalised)
    if m:
        return m.group(1).strip(), m.group(3)

    m = _ARTICLE_HEADER_ALT_RE.match(normalised)
    if m:
        return m.group(1).strip(), m.group(2)

    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_emails(raw: bytes) -> list[Article]:
    """
    Parse a raw MIME email and return a list of non-sponsor :class:`Article` objects.

    The function:

    1. Parses the raw bytes with :func:`email.message_from_bytes`.
    2. Extracts the ``text/plain`` MIME part.
    3. Builds a link map from the "Links:" footer.
    4. Splits the body on blank lines into paragraphs.
    5. Identifies section headers (short all-caps lines).
    6. Identifies article headers and the summary paragraph that follows.
    7. Resolves URLs via the link map.
    8. Filters out any article whose title or section matches a sponsor pattern.

    Parameters
    ----------
    raw : bytes
        Raw RFC 822 bytes for a single email message.

    Returns
    -------
    list[Article]
        Ordered list of extracted articles, sponsors excluded.

    Raises
    ------
    ParseError
        If *raw* is empty or contains no plain-text body.

    Examples
    --------
    >>> articles = parse_emails(raw_email_bytes)  # doctest: +SKIP
    >>> articles[0].title
    'GOOGLE ANNOUNCES GEMINI 3.1 PRO ...'
    """
    if not raw:
        raise ParseError("Empty input: cannot parse an empty byte string.")

    logger.debug("Parsing email (%d bytes)", len(raw))

    try:
        msg = email.message_from_bytes(raw)
    except Exception as exc:
        raise ParseError(f"Failed to parse email bytes: {exc}") from exc

    text = _get_plain_text(msg)
    link_map = _build_link_map(text)

    # Truncate at Links: section so we don't accidentally parse link URLs as
    # summaries.
    links_marker = _LINKS_SECTION_RE.search(text)
    body = text[: links_marker.start()] if links_marker else text

    # Split body into blank-line-separated paragraphs.
    paragraphs = re.split(r"\n{2,}", body)

    articles: list[Article] = []
    current_section = ""
    pending_title: str | None = None
    pending_link_num: str | None = None

    for para in paragraphs:
        normalised = _normalise_paragraph(para)
        if not normalised:
            continue

        # Check for section header (must be a single-line paragraph after norm).
        if _is_section_line(normalised):
            # Flush any pending article with no summary (edge case)
            if pending_title is not None:
                url = link_map.get(pending_link_num or "", "")
                if url and not _is_sponsor(pending_title, current_section):
                    articles.append(
                        Article(
                            title=pending_title,
                            summary="",
                            url=url,
                            section=current_section,
                        )
                    )
                    logger.debug("Extracted article (no summary): %s", pending_title[:60])
                pending_title = None
                pending_link_num = None
            current_section = normalised
            logger.debug("Entered section: %s", current_section)
            continue

        # Try to match as article header.
        header_match = _try_match_article_header(normalised)
        if header_match is not None:
            # Flush previous pending article (it had no following summary para)
            if pending_title is not None:
                url = link_map.get(pending_link_num or "", "")
                if url and not _is_sponsor(pending_title, current_section):
                    articles.append(
                        Article(
                            title=pending_title,
                            summary="",
                            url=url,
                            section=current_section,
                        )
                    )
                    logger.debug("Extracted article (no summary): %s", pending_title[:60])

            title, link_num = header_match
            if _is_sponsor(title, current_section):
                logger.debug("Skipping sponsor header: %s", title[:60])
                pending_title = None
                pending_link_num = None
            else:
                pending_title = title
                pending_link_num = link_num
            continue

        # If there is a pending article header, this paragraph is the summary.
        if pending_title is not None:
            summary = normalised
            url = link_map.get(pending_link_num or "", "")
            if url:
                articles.append(
                    Article(
                        title=pending_title,
                        summary=summary,
                        url=url,
                        section=current_section,
                    )
                )
                logger.debug("Extracted article: %s", pending_title[:60])
            else:
                logger.debug("Skipping article with no URL: %s", pending_title[:60])
            pending_title = None
            pending_link_num = None
            continue

        # Otherwise it's unrelated content (intro blurb, footer, etc.) — skip.
        logger.debug("Skipping unrelated paragraph: %s", normalised[:60])

    # Flush any final pending article
    if pending_title is not None:
        url = link_map.get(pending_link_num or "", "")
        if url and not _is_sponsor(pending_title, current_section):
            articles.append(
                Article(
                    title=pending_title,
                    summary="",
                    url=url,
                    section=current_section,
                )
            )
            logger.debug("Extracted final article (no summary): %s", pending_title[:60])

    logger.info("Parsed %d article(s) from email.", len(articles))
    return articles
