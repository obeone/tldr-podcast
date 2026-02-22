"""
Tests for the IMAP client module (src/tldr/imap_client.py).

Uses unittest.mock to patch IMAPClient so no real server is required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tldr.imap_client import IMAPError, fetch_unread_emails

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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFetchUnreadEmails:
    """Test suite for fetch_unread_emails."""

    def test_returns_raw_bytes_for_each_unread_message(self):
        """fetch_unread_emails returns one bytes entry per unread message."""
        raw_map = {
            1: {b"RFC822": RAW_EMAIL_1},
            2: {b"RFC822": RAW_EMAIL_2},
        }
        mock_client = _make_mock_client([1, 2], raw_map)

        with patch("tldr.imap_client.IMAPClient", return_value=mock_client):
            result = fetch_unread_emails(SAMPLE_CFG)

        assert isinstance(result, list)
        assert len(result) == 2
        assert RAW_EMAIL_1 in result
        assert RAW_EMAIL_2 in result

    def test_returns_empty_list_when_no_unread_messages(self):
        """fetch_unread_emails returns [] when the folder contains no UNSEEN messages."""
        mock_client = _make_mock_client([], {})

        with patch("tldr.imap_client.IMAPClient", return_value=mock_client):
            result = fetch_unread_emails(SAMPLE_CFG)

        assert result == []
        # fetch should not have been called at all
        mock_client.fetch.assert_not_called()

    def test_raises_imap_error_on_connection_failure(self):
        """fetch_unread_emails raises IMAPError when the connection fails."""
        with patch(
            "tldr.imap_client.IMAPClient",
            side_effect=ConnectionRefusedError("Connection refused"),
        ):
            with pytest.raises(IMAPError):
                fetch_unread_emails(SAMPLE_CFG)

    def test_raises_imap_error_on_login_failure(self):
        """fetch_unread_emails raises IMAPError when authentication fails."""
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.login.side_effect = Exception("Authentication failed")

        with patch("tldr.imap_client.IMAPClient", return_value=mock_client):
            with pytest.raises(IMAPError):
                fetch_unread_emails(SAMPLE_CFG)

    def test_raises_imap_error_on_search_failure(self):
        """fetch_unread_emails raises IMAPError when the SEARCH command fails."""
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.search.side_effect = Exception("SEARCH command failed")

        with patch("tldr.imap_client.IMAPClient", return_value=mock_client):
            with pytest.raises(IMAPError):
                fetch_unread_emails(SAMPLE_CFG)
