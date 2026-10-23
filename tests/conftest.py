import pytest

from heroes.protocol import notes


@pytest.fixture(autouse=True)
def strategy_dir(tmp_path, monkeypatch):
    """Заметки, скрипты, git и журналы Claude в тестах — во временной папке, не в strategy/."""
    folder = tmp_path / "strategy"
    monkeypatch.setattr(notes, "NOTES_DIR", folder)
    return folder
