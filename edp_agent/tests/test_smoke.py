"""Smoke test: confirms pytest + conftest import hooks work."""


def test_smoke_pytest_runs():
    assert 1 + 1 == 2


def test_smoke_can_import_common_events():
    """conftest puts agent-runtime tree on sys.path so common.events imports."""
    from common.events import TodoListItemEvent  # noqa: F401
