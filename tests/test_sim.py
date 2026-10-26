"""Инструменты Claude: симуляция партии, рабочая папка со скриптами, песочница heroes-run."""
import subprocess
import sys

import pytest

from heroes import sim
from heroes.engine.game import Game
from heroes.players import workspace
from heroes.players.random_bot import RandomBot


class Exporter(RandomBot):
    """Сохраняет момент партии на первом решении в 5-м раунде — как Claude перед вопросом."""

    def __init__(self, seed, path):
        super().__init__(seed)
        self.path = path
        self.snapshot = None

    def decide(self, decision, view):
        if view.game.state.round == 5 and self.snapshot is None:
            sim.export(view.game, view.player, decision.kind, self.path)
            self.snapshot = view.game.state.to_dict()
        return super().decide(decision, view)


@pytest.fixture
def saved(tmp_path):
    exporter = Exporter(1, tmp_path / "game.json")
    Game([exporter, RandomBot(2), RandomBot(3)], factions=["orcs", "humans", "nagas"]).run()
    return exporter


def test_sim_restores_the_moment_and_plays_on(saved):
    s = sim.load(saved.path)
    assert (s.me, s.round) == (0, 5)
    assert s.game.state.to_dict() == saved.snapshot
    r = s.play({s.me: "random"}, rounds=2, seed=1)
    assert r.game.state.round == 6 and r.asked and r.log and len(r.scores) == 3
    first = next(a for a in r.asked if a.player == s.me)
    assert first.kind == s.kind
    again = s.play({s.me: "random"}, rounds=2, seed=1)
    assert [x["total"] for x in again.scores] == [x["total"] for x in r.scores]   # с того же момента, детерминированно
    assert s.play(rounds=99).game.state.round == 16


def test_sim_scripted_answers_and_rejections(saved):
    s = sim.load(saved.path)
    seen = []

    def policy(q, game):
        seen.append(q.kind)
        return sim.DEFAULT
    s.play({s.me: policy}, rounds=1)
    assert seen and seen[0] == s.kind
    r = s.play({s.me: ["Нет такого региона"]}, rounds=1) if s.kind == "destination" else None
    if r:
        assert r.errors


def test_workspace_sync_back(tmp_path, strategy_dir):
    a = workspace.Workspace("m/high", "s1", tmp_path / "work")
    b = workspace.Workspace("m/high", "s2", tmp_path / "work")
    a.prepare()
    (a.shared / "old.py").write_text("old")
    b.prepare()
    a.prepare()
    assert (a.dir / "README.md").exists()
    (a.dir / "mine.py").write_text("a")
    (b.dir / "old.py").write_text("b's version")
    (a.dir / "README.md").write_text("a's readme")
    (b.dir / "README.md").write_text("b's readme")
    assert sorted(a.sync_back("orcs")) == ["README.md", "mine.py"]
    assert sorted(b.sync_back("humans")) == ["README.humans.md", "old.py"]   # README уже изменил a
    assert (a.shared / "README.md").read_text() == "a's readme"
    assert (a.shared / "old.py").read_text() == "b's version"
    a.cleanup()
    assert not a.dir.exists()


@pytest.mark.skipif(not workspace.available(), reason="bwrap is not installed")
def test_heroes_run_sandbox(tmp_path, saved, strategy_dir):
    ws = workspace.Workspace("m/high", "s1", tmp_path / "work")
    ws.prepare()
    ws.game_file.write_bytes(saved.path.read_bytes())
    (ws.dir / "probe.py").write_text(
        "import os, urllib.request\n"
        "from heroes import sim\n"
        "print('round', sim.load().round)\n"
        "open('out.txt', 'w').write('ok')\n"
        f"print('logs visible', os.path.exists({str(workspace.ROOT / 'logs')!r}))\n"
        f"print('notes visible', os.path.exists({str(strategy_dir)!r}))\n"
        "try:\n    urllib.request.urlopen('http://example.com', timeout=3); print('network')\n"
        "except Exception:\n    print('no network')\n")
    out = subprocess.run([str(ws.bin / "heroes-run"), "probe.py"], capture_output=True, text=True, timeout=60).stdout
    assert "round 5" in out and "logs visible False" in out and "notes visible False" in out and "no network" in out
    assert (ws.dir / "out.txt").read_text() == "ok"


def test_agent_tools(saved, capsys):
    from heroes import agent_tools
    s = sim.load(saved.path)
    out = {name: agent_tools.TOOLS[name](s.game, s.me, "5" if name == "reach" else None) for name in agent_tools.TOOLS}
    assert "army strength now" in out["max_strength"]
    assert "army strength now" in agent_tools.max_strength(s.game, s.me, "1") and "(you)" not in agent_tools.max_strength(s.game, s.me, "1")
    assert "You choose the action order" in out["schedule"] and "round 5" in out["schedule"].lower()
    assert "L1" in out["tree"] and "opens:" in out["tree"]
    assert out["reach"].startswith("From ") and "MP" in out["reach"]
    assert "→" in out["artifacts"] and "Fortresses" in out["fortresses"] and "[NEXT]" in out["monsters"]
    assert "TOTAL" in out["scores"] and "gold" in out["stockpile"]
    with pytest.raises(agent_tools.ToolError):
        agent_tools.player_arg(s.game, s.me, "nobody")
    assert "rounds: 7, 10, 13, 16" in out["schedule"]   # игрок 0, первый игрок 5-го раунда — игрок 1


@pytest.mark.skipif(not workspace.available(), reason="bwrap is not installed")
def test_heroes_tool_sandbox(tmp_path, saved, strategy_dir):
    ws = workspace.Workspace("m/high", "s1", tmp_path / "work")
    ws.prepare()
    ws.game_file.write_bytes(saved.path.read_bytes())
    out = subprocess.run([str(ws.bin / "heroes-tool"), "schedule"], capture_output=True, text=True, timeout=60).stdout
    assert "You choose the action order" in out
    out = subprocess.run([str(ws.bin / "heroes-tool"), "tree", "nobody"], capture_output=True, text=True, timeout=60).stdout
    assert out.startswith("error: unknown player")
