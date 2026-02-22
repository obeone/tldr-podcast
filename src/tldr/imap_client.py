"""
IMAP client for fetching unread emails from a configured mailbox.

Connects to an IMAP server via SSL, authenticates, selects a folder,
and retrieves the raw RFC 822 bytes for all UNSEEN messages.
"""

from __future__ import annotations

import logging
from typing import Any

import coloredlogs
from imapclient import IMAPClient

logger = logging.getLogger(__name__)
coloredlogs.install(
    level="DEBUG",
    logger=logger,
    fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


class IMAPError(Exception):
    """Raised when any IMAP operation fails."""


def fetch_unread_emails(imap_cfg: dict[str, Any]) -> list[bytes]:
    """
    Connect to an IMAP server and fetch the raw bytes of all unread messages.

    Establishes an SSL connection using the provided configuration, logs in,
    selects the configured folder, searches for UNSEEN messages, and fetches
    the RFC 822 raw bytes for each one.

    All exceptions are caught and re-raised as :exc:`IMAPError`.

    Parameters
    ----------
    imap_cfg : dict[str, Any]
        A dictionary with the following keys (all required):

        - ``host`` (str): IMAP server hostname.
        - ``port`` (int): IMAP server port (typically 993 for SSL).
        - ``username`` (str): Login username / email address.
        - ``password`` (str): Login password.
        - ``folder`` (str): Mailbox folder to select (e.g. ``"INBOX"``).

    Returns
    -------
    list[bytes]
        A list of raw RFC 822 message bytes, one entry per unread message.
        Returns an empty list when there are no unread messages.

    Raises
    ------
    IMAPError
        If the connection, login, folder selection, search, or fetch
        operations fail for any reason.

    Examples
    --------
    >>> cfg = {
    ...     "host": "imap.example.com",
    ...     "port": 993,
    ...     "username": "user@example.com",
    ...     "password": "secret",
    ...     "folder": "INBOX",
    ... }
    >>> messages = fetch_unread_emails(cfg)  # doctest: +SKIP
    """
    host = imap_cfg["host"]
    port = imap_cfg["port"]
    username = imap_cfg["username"]
    password = imap_cfg["password"]
    folder = imap_cfg["folder"]

    logger.debug("Connecting to IMAP server %s:%s", host, port)

    try:
        with IMAPClient(host=host, port=port, ssl=True) as client:
            logger.debug("Logging in as %s", username)
            client.login(username, password)

            logger.debug("Selecting folder: %s", folder)
            client.select_folder(folder)

            logger.debug("Searching for UNSEEN messages")
            message_ids = client.search(["UNSEEN"])
            logger.info("Found %d unread message(s) in %s", len(message_ids), folder)

            if not message_ids:
                return []

            logger.debug("Fetching RFC 822 data for %d message(s)", len(message_ids))
            raw_messages = client.fetch(message_ids, ["RFC822"])

            result: list[bytes] = []
            for msg_id, data in raw_messages.items():
                raw_bytes = data[b"RFC822"]
                logger.debug("Fetched message id=%s (%d bytes)", msg_id, len(raw_bytes))
                result.append(raw_bytes)

            logger.info("Successfully fetched %d raw email(s)", len(result))
            return result

    except IMAPError:
        raise
    except Exception as exc:
        logger.error("IMAP operation failed: %s", exc)
        raise IMAPError(f"IMAP operation failed: {exc}") from exc
