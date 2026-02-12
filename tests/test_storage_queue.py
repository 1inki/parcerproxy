import pytest

pytest.importorskip("sqlalchemy")

from app.storage import Storage


def test_repo_queue_dedup_and_status() -> None:
    st = Storage("sqlite:///:memory:")
    st.init_db()

    ok, reason = st.enqueue_repo("Owner/Repo")
    assert ok is True and reason == "queued"

    ok2, reason2 = st.enqueue_repo("owner/repo")
    assert ok2 is False and reason2 == "already_queued"

    st.mark_repo_status("owner/repo", "done")
    ok3, reason3 = st.enqueue_repo("owner/repo")
    assert ok3 is False and reason3 == "already_analyzed"
