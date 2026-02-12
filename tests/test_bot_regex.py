import pytest

pytest.importorskip("telegram")

from app.bot import REPO_RE


def test_repo_regex() -> None:
    m = REPO_RE.search("please add https://github.com/Owner-1/repo_2 now")
    assert m
    assert m.group(1) == "Owner-1/repo_2"
