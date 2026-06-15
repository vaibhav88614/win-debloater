"""Tests for the rotating application log."""

from __future__ import annotations

import logging

import pytest

from app.core import applog


@pytest.fixture(autouse=True)
def _reset_logger(monkeypatch, tmp_path):
    monkeypatch.setattr(applog, "user_data_dir", lambda: tmp_path)
    monkeypatch.setattr("app.core.paths.user_data_dir", lambda: tmp_path)
    logger = logging.getLogger(applog.LOGGER_NAME)
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()
    yield
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()


def test_setup_is_idempotent(tmp_path):
    a = applog.setup(console=False)
    b = applog.setup(console=False)
    assert a is b
    # Only one rotating handler should ever be attached.
    from logging.handlers import RotatingFileHandler

    rh = [h for h in a.handlers if isinstance(h, RotatingFileHandler)]
    assert len(rh) == 1


def test_log_file_path_under_user_data_dir(tmp_path):
    assert applog.log_file_path() == tmp_path / applog.LOG_FILENAME


def test_messages_are_written_to_file(tmp_path):
    logger = applog.setup(console=False)
    logger.info("hello world")
    for h in logger.handlers:
        h.flush()
    contents = (tmp_path / applog.LOG_FILENAME).read_text(encoding="utf-8")
    assert "hello world" in contents


def test_get_logger_auto_initializes():
    logger = applog.get_logger()
    assert logger.handlers, "get_logger must attach a handler when none exist"
