"""
Tests for the IMAP client module (src/tldr/imap_client.py).

Uses unittest.mock to patch IMAPClient so no real server is required.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from tldr.imap_client import IMAPError, fetch_emails, move_emails_to_folder

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_CFG = {
    "host": "imap.example.com",
    "port": 993,
    "username": "user@example.com",
    "password": "secret",
    "folder": "INBOX",
}

RAW_EMAIL_1 = b"From: sender@example.com\r\nSubject: Test 1\r\n\r\nBody 1"
RAW_EMAIL_2 = b"From: sender@example.com\r\nSubject: Test 2\r\n\r\nBody 2"


# ---------------------------------------------------------------------------
# Helper: build a configured mock IMAPClient context manager
# ---------------------------------------------------------------------------

def _make_mock_client(message_ids: list[int], raw_map: dict) -> MagicMock:
    """
    Build a mock IMAPClient instance whose context manager behaviour is set up.

    Parameters
    ----------
    message_ids : list[int]
        Message IDs to return from ``search()``.
    raw_map : dict
        Mapping returned by ``fetch()``, keyed by message id.

    Returns
    -------
    MagicMock
        A mock configured to behave like an ``IMAPClient`` context manager.
    """
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.search.return_value = message_ids
    mock_client.fetch.return_value = raw_map
    return mock_client


def _make_move_mock_client(
    existing_folders: list[str],
    capabilities: list[bytes],
) -> MagicMock:
    """
    Build a mock IMAPClient for move_emails_to_folder tests.

    Parameters
    ----------
    existing_folders : list[str]
        Folder names to return from ``list_folders()``.
    capabilities : list[bytes]
        Server capabilities (e.g. ``[b"MOVE"]``).

    Returns
    -------
    MagicMock
        Configured mock.
    """
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    # list_folders returns a list of (flags, delimiter, name) tuples.
    mock_client.list_folders.return_value = [
        ([], b"/", name) for name in existing_folders
    ]
    mock_client.capabilities.return_value = capabilities
    return mock_client


# ---------------------------------------------------------------------------
# Tests — fetch_emails
# ---------------------------------------------------------------------------

class TestFetchEmails:
    """Test suite for fetch_emails."""

    def test_returns_id_and_raw_bytes_for_each_message(self):
        """fetch_emails returns (id, bytes) pairs for each matching message."""
        raw_map = {
            1: {b"RFC822": RAW_EMAIL_1},
            2: {b"RFC822": RAW_EMAIL_2},
        }
        mock_client = _make_mock_client([1, 2], raw_map)

        with patch("tldr.imap_client.IMAPClient", return_value=mock_client):
            result = fetch_emails(SAMPLE_CFG)

        assert isinstance(result, list)
        assert len(result) == 2
        ids = [msg_id for msg_id, _ in result]
        raws = [raw for _, raw in result]
        assert 1 in ids
        assert 2 in ids
        assert RAW_EMAIL_1 in raws
        assert RAW_EMAIL_2 in raws

    def test_returns_empty_list_when_no_messages(self):
        """fetch_emails returns [] when no messages match."""
        mock_client = _make_mock_client([], {})

        with patch("tldr.imap_client.IMAPClient", return_value=mock_client):
            result = fetch_emails(SAMPLE_CFG)

        assert result == []
        mock_client.fetch.assert_not_called()

    def test_raises_imap_error_on_connection_failure(self):
        """fetch_emails raises IMAPError when the connection fails."""
        with patch(
            "tldr.imap_client.IMAPClient",
            side_effect=ConnectionRefusedError("Connection refused"),
        ):
            with pytest.raises(IMAPError):
                fetch_emails(SAMPLE_CFG)

    def test_raises_imap_error_on_login_failure(self):
        """fetch_emails raises IMAPError when authentication fails."""
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.login.side_effect = Exception("Authentication failed")

        with patch("tldr.imap_client.IMAPClient", return_value=mock_client):
            with pytest.raises(IMAPError):
                fetch_emails(SAMPLE_CFG)

    def test_raises_imap_error_on_search_failure(self):
        """fetch_emails raises IMAPError when the SEARCH command fails."""
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.search.side_effect = Exception("SEARCH command failed")

        with patch("tldr.imap_client.IMAPClient", return_value=mock_client):
            with pytest.raises(IMAPError):
                fetch_emails(SAMPLE_CFG)

    def test_raises_value_error_on_invalid_status_filter(self):
        """fetch_emails raises ValueError for an unknown status_filter."""
        with pytest.raises(ValueError, match="Invalid status_filter"):
            fetch_emails(SAMPLE_CFG, status_filter="bogus")


class TestFetchEmailsDateFilter:
    """Tests for the target_date filtering behaviour."""

    def test_uses_sentsince_and_before_when_target_date_given(self):
        """fetch_emails passes SENTSINCE/SENTBEFORE criteria when target_date is set."""
        raw_map = {1: {b"RFC822": RAW_EMAIL_1}}
        mock_client = _make_mock_client([1], raw_map)
        target = date(2026, 2, 20)

        with patch("tldr.imap_client.IMAPClient", return_value=mock_client):
            result = fetch_emails(SAMPLE_CFG, target_date=target)

        assert result == [(1, RAW_EMAIL_1)]
        mock_client.search.assert_called_once_with(
            ["SENTSINCE", "20-Feb-2026", "SENTBEFORE", "21-Feb-2026"]
        )

    def test_defaults_to_today_when_no_target_date(self):
        """fetch_emails defaults target_date to date.today()."""
        raw_map = {1: {b"RFC822": RAW_EMAIL_1}}
        mock_client = _make_mock_client([1], raw_map)
        today = date(2026, 2, 22)

        with patch("tldr.imap_client.IMAPClient", return_value=mock_client):
            with patch("tldr.imap_client.date") as mock_date:
                mock_date.today.return_value = today
                mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
                fetch_emails(SAMPLE_CFG)

        mock_client.search.assert_called_once_with(
            ["SENTSINCE", "22-Feb-2026", "SENTBEFORE", "23-Feb-2026"]
        )


class TestFetchEmailsStatusFilter:
    """Tests for the status_filter parameter."""

    def test_unseen_filter_adds_unseen_criterion(self):
        """fetch_emails includes UNSEEN in search criteria when status_filter='unseen'."""
        raw_map = {1: {b"RFC822": RAW_EMAIL_1}}
        mock_client = _make_mock_client([1], raw_map)
        target = date(2026, 2, 20)

        with patch("tldr.imap_client.IMAPClient", return_value=mock_client):
            fetch_emails(SAMPLE_CFG, target_date=target, status_filter="unseen")

        mock_client.search.assert_called_once_with(
            ["UNSEEN", "SENTSINCE", "20-Feb-2026", "SENTBEFORE", "21-Feb-2026"]
        )

    def test_seen_filter_adds_seen_criterion(self):
        """fetch_emails includes SEEN in search criteria when status_filter='seen'."""
        raw_map = {1: {b"RFC822": RAW_EMAIL_1}}
        mock_client = _make_mock_client([1], raw_map)
        target = date(2026, 2, 20)

        with patch("tldr.imap_client.IMAPClient", return_value=mock_client):
            fetch_emails(SAMPLE_CFG, target_date=target, status_filter="seen")

        mock_client.search.assert_called_once_with(
            ["SEEN", "SENTSINCE", "20-Feb-2026", "SENTBEFORE", "21-Feb-2026"]
        )

    def test_all_filter_uses_no_seen_unseen_criterion(self):
        """fetch_emails omits SEEN/UNSEEN when status_filter='all'."""
        raw_map = {1: {b"RFC822": RAW_EMAIL_1}}
        mock_client = _make_mock_client([1], raw_map)
        target = date(2026, 2, 20)

        with patch("tldr.imap_client.IMAPClient", return_value=mock_client):
            fetch_emails(SAMPLE_CFG, target_date=target, status_filter="all")

        mock_client.search.assert_called_once_with(
            ["SENTSINCE", "20-Feb-2026", "SENTBEFORE", "21-Feb-2026"]
        )


# ---------------------------------------------------------------------------
# Tests — move_emails_to_folder
# ---------------------------------------------------------------------------

class TestMoveEmailsToFolder:
    """Test suite for move_emails_to_folder."""

    def test_uses_move_command_when_server_supports_it(self):
        """move_emails_to_folder uses the MOVE command when the server advertises it."""
        mock_client = _make_move_mock_client(["INBOX", "TLDR/Seen"], [b"MOVE"])

        with patch("tldr.imap_client.IMAPClient", return_value=mock_client):
            move_emails_to_folder(SAMPLE_CFG, [1, 2], "TLDR/Seen")

        mock_client.move.assert_called_once_with([1, 2], "TLDR/Seen")
        mock_client.copy.assert_not_called()
        mock_client.delete_messages.assert_not_called()

    def test_falls_back_to_copy_delete_expunge_without_move_extension(self):
        """move_emails_to_folder falls back to COPY+DELETE+EXPUNGE when no MOVE support."""
        mock_client = _make_move_mock_client(["INBOX", "TLDR/Seen"], [b"IMAP4rev1"])

        with patch("tldr.imap_client.IMAPClient", return_value=mock_client):
            move_emails_to_folder(SAMPLE_CFG, [1, 2], "TLDR/Seen")

        mock_client.copy.assert_called_once_with([1, 2], "TLDR/Seen")
        mock_client.delete_messages.assert_called_once_with([1, 2])
        mock_client.expunge.assert_called_once()
        mock_client.move.assert_not_called()

    def test_creates_target_folder_if_missing(self):
        """move_emails_to_folder creates and subscribes the target folder when absent."""
        mock_client = _make_move_mock_client(["INBOX"], [b"MOVE"])

        with patch("tldr.imap_client.IMAPClient", return_value=mock_client):
            move_emails_to_folder(SAMPLE_CFG, [1], "TLDR/Seen")

        mock_client.create_folder.assert_called_once_with("TLDR/Seen")
        mock_client.subscribe_folder.assert_called_once_with("TLDR/Seen")

    def test_does_not_create_folder_when_it_already_exists(self):
        """move_emails_to_folder skips folder creation when the target already exists."""
        mock_client = _make_move_mock_client(["INBOX", "TLDR/Seen"], [b"MOVE"])

        with patch("tldr.imap_client.IMAPClient", return_value=mock_client):
            move_emails_to_folder(SAMPLE_CFG, [1], "TLDR/Seen")

        mock_client.create_folder.assert_not_called()
        mock_client.subscribe_folder.assert_not_called()

    def test_does_nothing_when_message_ids_is_empty(self):
        """move_emails_to_folder skips all IMAP calls when message_ids is empty."""
        with patch("tldr.imap_client.IMAPClient") as mock_cls:
            move_emails_to_folder(SAMPLE_CFG, [], "TLDR/Seen")

        mock_cls.assert_not_called()

    def test_raises_imap_error_on_connection_failure(self):
        """move_emails_to_folder raises IMAPError when the connection fails."""
        with patch(
            "tldr.imap_client.IMAPClient",
            side_effect=ConnectionRefusedError("refused"),
        ):
            with pytest.raises(IMAPError):
                move_emails_to_folder(SAMPLE_CFG, [1], "TLDR/Seen")

    def test_raises_imap_error_on_move_failure(self):
        """move_emails_to_folder raises IMAPError when the MOVE command fails."""
        mock_client = _make_move_mock_client(["INBOX", "TLDR/Seen"], [b"MOVE"])
        mock_client.move.side_effect = Exception("MOVE failed")

        with patch("tldr.imap_client.IMAPClient", return_value=mock_client):
            with pytest.raises(IMAPError):
                move_emails_to_folder(SAMPLE_CFG, [1], "TLDR/Seen")
