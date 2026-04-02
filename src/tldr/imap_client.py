"""
IMAP client for fetching unread emails from a configured mailbox and marking
processed messages by moving them to a "seen" folder.

Connects to an IMAP server via SSL, authenticates, selects a folder,
retrieves the raw RFC 822 bytes for all UNSEEN messages, and optionally
moves them to a destination folder once processing is complete.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from imapclient import IMAPClient

logger = logging.getLogger(__name__)


class IMAPError(Exception):
    """Raised when any IMAP operation fails."""


def fetch_unread_emails(
    imap_cfg: dict[str, Any],
    target_date: date | None = None,
) -> list[tuple[int, bytes]]:
    """
    Connect to an IMAP server and fetch the raw bytes of all unread messages.

    Establishes an SSL connection using the provided configuration, logs in,
    selects the configured folder, searches for UNSEEN messages sent on
    ``target_date``, and fetches the RFC 822 raw bytes for each one.

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

    target_date : date or None, optional
        The calendar day to filter by (send date). Defaults to ``date.today()``.
        Uses IMAP ``SENTSINCE``/``SENTBEFORE`` criteria (RFC 3501 date format).

    Returns
    -------
    list[tuple[int, bytes]]
        A list of ``(message_id, raw_bytes)`` pairs, one entry per unread
        message.  ``message_id`` is the IMAP UID used to reference the message
        later (e.g. for moving it).  Returns an empty list when there are no
        unread messages.

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
    >>> emails = fetch_unread_emails(cfg)  # doctest: +SKIP
    >>> from datetime import date
    >>> emails = fetch_unread_emails(cfg, target_date=date(2026, 2, 20))  # doctest: +SKIP
    """
    if target_date is None:
        target_date = date.today()

    since_str = target_date.strftime("%d-%b-%Y")
    before_str = (target_date + timedelta(days=1)).strftime("%d-%b-%Y")

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

            logger.debug("Searching for UNSEEN messages sent on %s", since_str)
            message_ids = client.search(
                ["UNSEEN", "SENTSINCE", since_str, "SENTBEFORE", before_str]
            )
            logger.info("Found %d unread message(s) in %s", len(message_ids), folder)

            if not message_ids:
                return []

            logger.debug("Fetching RFC 822 data for %d message(s)", len(message_ids))
            raw_messages = client.fetch(message_ids, ["RFC822"])

            result: list[tuple[int, bytes]] = []
            for msg_id, data in raw_messages.items():
                raw_bytes = data[b"RFC822"]
                logger.debug("Fetched message id=%s (%d bytes)", msg_id, len(raw_bytes))
                result.append((msg_id, raw_bytes))

            logger.info("Successfully fetched %d raw email(s)", len(result))
            return result

    except IMAPError:
        raise
    except Exception as exc:
        logger.error("IMAP operation failed: %s", exc)
        raise IMAPError(f"IMAP operation failed: {exc}") from exc


def move_emails_to_folder(
    imap_cfg: dict[str, Any],
    message_ids: list[int],
    target_folder: str,
) -> None:
    """
    Move a set of IMAP messages from the configured source folder to a target
    folder, creating the target folder if it does not exist.

    Connects to the IMAP server using the same credentials as
    :func:`fetch_unread_emails`, selects the source folder, copies the
    messages to ``target_folder``, then deletes and expunges the originals.
    If the server advertises the ``MOVE`` capability the more efficient
    ``MOVE`` command is used instead.

    Parameters
    ----------
    imap_cfg : dict[str, Any]
        Same dictionary accepted by :func:`fetch_unread_emails` (``host``,
        ``port``, ``username``, ``password``, ``folder``).
    message_ids : list[int]
        IMAP message IDs to move (as returned by :func:`fetch_unread_emails`).
    target_folder : str
        Destination folder name (e.g. ``"TLDR/Seen"``).

    Raises
    ------
    IMAPError
        If any IMAP operation fails.

    Examples
    --------
    >>> move_emails_to_folder(cfg, [1, 2, 3], "TLDR/Seen")  # doctest: +SKIP
    """
    if not message_ids:
        logger.debug("No message IDs provided, skipping move.")
        return

    host = imap_cfg["host"]
    port = imap_cfg["port"]
    username = imap_cfg["username"]
    password = imap_cfg["password"]
    folder = imap_cfg["folder"]

    logger.debug("Connecting to IMAP server %s:%s to move messages", host, port)

    try:
        with IMAPClient(host=host, port=port, ssl=True) as client:
            logger.debug("Logging in as %s", username)
            client.login(username, password)

            # Create the target folder if it does not exist yet.
            existing_folders = {f[2] for f in client.list_folders()}
            if target_folder not in existing_folders:
                logger.info("Creating missing IMAP folder: %s", target_folder)
                client.create_folder(target_folder)
                client.subscribe_folder(target_folder)

            logger.debug("Selecting source folder: %s", folder)
            client.select_folder(folder)

            capabilities = client.capabilities()
            if b"MOVE" in capabilities:
                logger.debug(
                    "Server supports MOVE; moving %d message(s) to %s",
                    len(message_ids),
                    target_folder,
                )
                client.move(message_ids, target_folder)
            else:
                logger.debug(
                    "Server does not support MOVE; using COPY + DELETE + EXPUNGE"
                )
                client.copy(message_ids, target_folder)
                client.delete_messages(message_ids)
                client.expunge()

            logger.info(
                "Moved %d message(s) to %s", len(message_ids), target_folder
            )

    except IMAPError:
        raise
    except Exception as exc:
        logger.error("IMAP move operation failed: %s", exc)
        raise IMAPError(f"IMAP move operation failed: {exc}") from exc
