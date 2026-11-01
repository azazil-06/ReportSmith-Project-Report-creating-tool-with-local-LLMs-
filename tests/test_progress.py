"""Unit tests for ProgressBar utility."""

from reportsmith.progress import ProgressBar


def test_progress_bar_context_and_updates():
    with ProgressBar(total=5, desc="Test Task", unit="item") as pbar:
        assert pbar.total == 5
        assert pbar.current == 0
        pbar.update(1, status="Item 1")
        assert pbar.current == 1
        pbar.set_status("Working on Item 2")
        pbar.update(2, status="Item 3")
        assert pbar.current == 3
        pbar.update(5, status="Done")
        assert pbar.current == 5  # capped at total


def test_progress_bar_zero_total():
    pbar = ProgressBar(total=0, desc="Zero Task")
    assert pbar.total == 1  # max(1, 0)
    pbar.update(1)
    pbar.close()

