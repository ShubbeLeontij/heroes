"""Минимальные офлайн-проверки Claude-игрока: вызов CLI подменяется генератором ответов."""
import json
import random
import shutil
import subprocess
from types import SimpleNamespace
from pathlib import Path

import pytest

from heroes import cli
from heroes.engine.game import Game
from heroes.players.claude_cli import ClaudeError, ClaudePlayer
from heroes.players.random_bot import RandomBot
from heroes.players.workspace import REFERENCE, Workspace
from heroes.protocol import journal, notes, prompts, schemas

from schema_fuzz import random_instance


SCHEMA_LINE = "JSON schema of the answer field: "


class FakeClaude(ClaudePlayer):
    """Отвечает случайным JSON, допустимым по схеме answer из вопроса (план партии — строкой)."""

    def __init__(self, seed):
        super().__init__(transcript_dir=None, tools=False)
        self.rng = random.Random(seed)
        self.prompts = []
        self.kinds = set()
        self.together = []    # виды решений, заданных одним вопросом
        self.schema = {"type": "string"}

    def decide(self, decision, view):
        self.kinds.add(decision.kind)
        return super().decide(decision, view)

    def decide_together(self, decisions, view):
        self.kinds.update(d.kind for d in decisions)
        self.together.append([d.kind for d in decisions])
        return super().decide_together(decisions, view)

    def call(self, prompt, schema, system):
        assert schema == schemas.RESPONSE_SCHEMA      # одна схема на все вызовы — ради кэша
        self.prompts.append((system is not None, prompt))
        self.started = True
        if SCHEMA_LINE in prompt:
            self.schema = json.loads(prompt.rsplit(SCHEMA_LINE, 1)[1].split("\n", 1)[0])
        return {"answer": random_instance(self.schema, self.rng), "analysis": "plan"}


def test_fake_claude_plays_full_game():
    claudes = [FakeClaude(1), FakeClaude(2)]
    game = Game([claudes[0], RandomBot(0), claudes[1]])
    game.run()
    assert game.state.round == 16
    for c in claudes:
        assert c.prompts[0][0] and not any(first for first, _ in c.prompts[1:])   # системный промпт — один раз
        assert "QUESTION (game_plan)" in c.prompts[0][1]                            # с ним — вопрос о плане
        assert all("FORMAT:" in prompt for _, prompt in c.prompts)
        assert c.kinds <= set(notes.DECISION_KINDS)                     # к любому решению можно привязать напоминание


def test_fake_nagas_and_elves_answer_jointly():
    for seed in range(30):   # пока гидра не окажется отдельно от наги в раунде с перемещением
        nagas, elves = FakeClaude(seed), FakeClaude(seed + 50)
        game = Game([nagas, elves, RandomBot(seed)], factions=["nagas", "elves", "humans"])
        game.run()
        assert game.state.round == 16
        assert not any("did not answer" in line for line in game.state.log)
        if nagas.together:
            break
    assert ["destination", "hydra_destination"] in nagas.together


class WithTools(FakeClaude):
    """FakeClaude с рабочей папкой: проверяет подсказку об инструментах, саму папку не готовит."""

    def __init__(self, seed, workdir):
        super().__init__(seed)
        self.tools, self.workspace = True, Workspace(self.variant, self.session_id, workdir)

    def ask(self, game, p, kind, prompt):
        return self.call(prompt, schemas.RESPONSE_SCHEMA, None if self.started else self.system_prompt(game, p))


def test_tools_hint_in_questions(tmp_path):
    from heroes import agent_tools
    assert REFERENCE == tuple(agent_tools.TOOLS)
    assert "view_boards" not in schemas.RESPONSE_SCHEMA["properties"] and "view_boards" not in prompts.PROTOCOL
    on, off = WithTools(5, tmp_path), FakeClaude(6)
    game = Game([on, off, RandomBot(0)], names=["Ада", "Боб", "Вера"])
    game.run()
    assert game.state.round == 16
    asked = {c: [prompt for _, prompt in c.prompts if "QUESTION (" in prompt and "game_plan" not in prompt] for c in (on, off)}
    assert asked[on] and all(q.endswith("[player or argument]; your scripts and simulation: heroes-run <script.py>.")
                             for q in asked[on])
    assert asked[off] and not any("heroes-tool" in q for q in asked[off])
    assert "`heroes-tool board [player]`" in on.system_prompt(game, 0)   # системный промпт: раздел TOOLS


class SchemaProbe(RandomBot):
    """Бот, который на каждом решении проверяет схему Claude: случайный ответ по схеме переводится
    в формат движка, и проверка движка его разбирает (принимает или объясняет ошибку, но не падает)."""

    def __init__(self, seed, kinds):
        super().__init__(seed)
        self.kinds = kinds

    def decide(self, decision, view):
        spec = schemas.spec_for(view.game, decision)
        json.dumps(spec.schema)
        for _ in range(5):
            answer = spec.convert(random_instance(spec.schema, self.rng))
            assert decision.validate(answer) is None or isinstance(decision.validate(answer), str)
        self.kinds.add(decision.kind + ("+extra" if "extra" in decision.data else ""))   # перемещение с Домом вождей/Катафалком
        return super().decide(decision, view)


def test_schemas_cover_every_faction_decision():
    factions = ["humans", "undead", "dwarves", "orcs", "elves", "nagas"]
    wanted = {"destination+extra", "circle_note", "summon_hydra", "hydra_destination", "curse_region",
              "battle_upgrade", "make_lake", "exchange_on_gain"}
    kinds = set()
    for seed in range(40):
        game = Game([SchemaProbe(seed * 3 + i, kinds) for i in range(3)], factions=random.Random(seed).sample(factions, 3))
        game.run()
        if wanted <= kinds:
            break
    assert wanted <= kinds, wanted - kinds


def test_faction_converters():
    from heroes.engine.decisions import Decision
    game = Game([RandomBot(0), RandomBot(1)], factions=["nagas", "undead"])
    spec = schemas.spec_for(game, Decision("avoid_battle", 0, "", {"amount": 3}, lambda a: None, None))
    assert spec.convert({"avoid": False, "pay": {"wood": 3, "iron": 0, "gold": 0}}) is None
    assert spec.convert({"avoid": True, "pay": {"wood": 2, "iron": 0, "gold": 1}}) == {"wood": 2, "gold": 1}
    spec = schemas.spec_for(game, Decision("curse_region", 1, "", {"options": ["Цурт"], "optional": True},
                                           lambda a: None, None))
    assert spec.convert("none") is None and spec.convert("Цурт") == "Цурт"
    assert "Краки" in prompts.system_prompt(game, 0, VARIANT) and "Проклятые регионы" in prompts.system_prompt(game, 0, VARIANT)


def test_converters():
    game = Game([RandomBot(0), RandomBot(1)])
    from heroes.engine.decisions import Decision
    build = Decision("build_recruit", 0, "", {"built": None}, lambda a: None, None)
    spec = schemas.spec_for(game, build)
    assert spec.convert([{"hire": {"soldier": 0, "knight": 0, "archmage": 0}}, {"build": "town_hall"},
                         {"hire": {"soldier": 2, "knight": 0, "archmage": 0}}, "done"]) == \
        [{"build": "town_hall"}, {"hire": {"soldier": 2}}, "done"]
    monster = Decision("fight_monster", 0, "", {"candidates": {"0": {}}, "units": {"soldier": {}}}, lambda a: None, None)
    spec = schemas.spec_for(game, monster)
    assert spec.convert({"fight": False, "monster": 0, "army": {"soldier": 2}}) is None
    assert spec.convert({"fight": True, "monster": 0, "army": {"soldier": 2}}) == {"monster": 0, "army": {"soldier": 2}}


def test_unwrap_double_encoded_answers():
    from heroes.players.claude_cli import unwrap
    assert unwrap('"Чарил"') == "Чарил"
    assert unwrap('{"take": true}') == {"take": True}
    assert unwrap("Чарил") == "Чарил" and unwrap(["wood"]) == ["wood"]


class Reflecting(FakeClaude):
    """После партии отвечает заранее заданными ответами на вопрос о заметках."""

    def __init__(self, seed, answers):
        super().__init__(seed)
        self.answers = list(answers)

    def call(self, prompt, schema, system):
        if "QUESTION (strategy_notes)" in prompt or "YOUR ANSWER WAS REJECTED" in prompt:
            self.prompts.append((system is not None, prompt))
            answer = self.answers.pop(0)
            return {"answer": {"summary": "lessons", **answer} if isinstance(answer, dict) else answer, "analysis": ""}
        return super().call(prompt, schema, system)


VARIANT = notes.variant("claude-opus-5-5", "max")


def test_strategy_notes_round_trip(strategy_dir):
    claude = Reflecting(1, [
        {"update": False},                                                     # нет разбора — повтор
        {"review": "ok", "update": True, "sections": {"humans": "too long " * 200}},   # превышен лимит — повтор
        {"review": "- lost the race for Юноро", "update": True,
         "sections": {"general": "- rush monsters", "humans": "- block portals"}},
    ])
    assert claude.variant == "claude-opus-5-5/max"
    game = Game([claude, FakeClaude(2)], factions=["orcs", "humans"])
    assert "none yet" in prompts.system_prompt(game, 0, VARIANT)
    game.run()
    review, outcome = claude.reflect(game, 0)
    assert review == "- lost the race for Юноро" and outcome.startswith("strategy notes updated (general, humans)")
    assert outcome.endswith("committed to git")
    assert "is 1799 characters" in claude.prompts[-1][1]
    saved = notes.load(VARIANT, "orcs")
    assert saved["general"] == "- rush monsters" and saved["humans"] == "- block portals" and saved["elves"] == ""
    assert (strategy_dir / VARIANT / "orcs.md").exists()
    assert "- block portals" in prompts.system_prompt(game, 0, VARIANT)

    claude.answers = [{"review": "fine", "update": False}]
    assert claude.reflect(game, 0)[1].startswith("no changes to the strategy notes")
    assert notes.load(VARIANT, "orcs") == saved
    history = claude.prompts[-1][1]
    assert "Орки: lessons" in history and "+- block portals" in history   # прошлый коммит с диффом


def test_analysis_goes_to_the_story(strategy_dir, tmp_path):
    assert "comment" not in schemas.RESPONSE_SCHEMA["properties"] and "comment" not in prompts.PROTOCOL
    claude = Reflecting(1, [{"review": "- lost the race", "update": True, "sections": {"general": "- x"}}])
    claude.story_file = tmp_path / "story.md"
    thoughts = []
    claude.on_comment = lambda name, kind, text: thoughts.append(text)
    game = Game([claude, RandomBot(0)], names=["Лео", "Ева"], factions=["orcs", "humans"])
    game.run()
    asked = [prompt for _, prompt in claude.prompts if "QUESTION (" in prompt and "game_plan" not in prompt]
    assert asked and all(prompts.MATTERS in q and "FORMAT: first your analysis as text, then the JSON" in q for q in asked)
    claude.on_invalid(SimpleNamespace(player=0, kind="destination", text="q"), "Улин", "too far")
    claude.reflect(game, 0)
    story = claude.story_file.read_text(encoding="utf-8")
    assert story.startswith("# Лео (Орки): the story of the game") and "## Plan for the game" in story
    assert "## Round 1 · " in story and "plan\n\n**Decision:** `" in story and "## Round 16 · " in story
    assert "*The engine rejected this answer: too far*" in story
    assert story.rstrip().endswith("## Review after the game\n\n- lost the race")
    assert thoughts and set(thoughts[1:]) == {"plan"}                    # зрителю — тот же анализ
    assert "Орки: lessons" in journal.git("log", "-1", "--format=%s").stdout   # summary — сообщение коммита


def test_strategy_notes_skip_random_bot_sections(strategy_dir):
    claude = Reflecting(1, [{"review": "r", "update": True, "sections": {"humans": "- x"}}] * 3)
    game = Game([claude, RandomBot(0)], factions=["orcs", "humans"])
    game.run()
    assert claude.reflect(game, 0)[1].startswith("strategy notes left unchanged")
    assert not (strategy_dir / VARIANT / "orcs.md").exists()


def test_plan_review_and_results(strategy_dir):
    thoughts = []
    claude = Reflecting(1, [{"review": "- too slow", "update": False}])
    claude.on_comment = lambda name, kind, text: thoughts.append((kind, text))
    game = Game([claude, RandomBot(0)], factions=["orcs", "humans"])
    game.run()
    cli.record_lessons(game, out=lambda line: None, on_thought=lambda name, kind, text: thoughts.append((kind, text)))
    kinds = [kind for kind, _ in thoughts]
    assert kinds[0] == "game_plan" and kinds[-1] == "game_review" and thoughts[-1][1] == "- too slow"
    rows = (strategy_dir / "results.csv").read_text(encoding="utf-8").splitlines()
    assert rows[0].startswith("time,game_id,model,effort") and ",orcs," in rows[1] and "humans: random bot" in rows[1]


def test_reminders(strategy_dir):
    text = ("- [rounds 14-16, build_recruit] Spend everything on VP.\n"
            "- [destination, choose_start] Count MP first.\n"
            "- [always] Watch the Undead curses.")
    notes.update(VARIANT, "orcs", {notes.REMINDERS: text})
    assert notes.reminders_for(VARIANT, "orcs", 15, "build_recruit") == ["Spend everything on VP.", "Watch the Undead curses."]
    assert notes.reminders_for(VARIANT, "orcs", 3, "build_recruit") == ["Watch the Undead curses."]
    assert notes.reminders_for(VARIANT, "orcs", 3, "destination")[0] == "Count MP first."
    for bad, error in [("Spend everything", "format"), ("- [round 17] x", "within"), ("- [sometimes] x", "unknown condition"),
                       ("\n".join(["- [always] x"] * 13), "at most 12")]:
        try:
            notes.validate({"update": True, "sections": {notes.REMINDERS: bad}}, [notes.REMINDERS])
        except ValueError as e:
            assert error in str(e)
        else:
            raise AssertionError(bad)

    claude = FakeClaude(3)
    game = Game([claude, RandomBot(0)], factions=["orcs", "humans"])
    game.run()
    asked = [prompt for _, prompt in claude.prompts if "QUESTION (destination" in prompt and "REJECTED" not in prompt]
    assert asked and all("YOUR REMINDERS:\n- Count MP first.\n- Watch the Undead curses." in prompt for prompt in asked)
    notes.update(VARIANT, "orcs", {"reminders": "- [upgrade_units] Keep 12 rage."})
    assert notes.reminders_for(VARIANT, "orcs", 3, "build_recruit") == ["Keep 12 rage."]   # модификация — шаг строительства


def test_rules_confusion_log(strategy_dir):
    claude = FakeClaude(4)       # случайные ответы по схеме — движок часть из них отклоняет
    claude.call_orig = claude.call

    def call(prompt, schema, system):
        result = claude.call_orig(prompt, schema, system)
        if "QUESTION (action_order" in prompt:
            result["rules_unclear"] = "Can I start in a crossed-out portal region?"
        return result
    claude.call = call
    game = Game([claude, RandomBot(0)], factions=["orcs", "humans"])
    game.run()
    log = (strategy_dir / "RULES_CONFUSION.md").read_text(encoding="utf-8")
    assert "answer rejected by the engine" in log and "**Engine:**" in log
    assert "question from Claude" in log and "crossed-out portal region" in log
    assert "claude-opus-5-5/max · Player 1 (Орки)" in log


def test_strategy_branch_worktree(tmp_path, strategy_dir):
    def git(*args):
        return subprocess.run(["git", "-C", str(tmp_path), *journal.AUTHOR, *args], capture_output=True,
                              text=True, check=True).stdout
    git("init", "-q", "-b", "master")
    git("commit", "-q", "--allow-empty", "-m", "code")
    notes.update(VARIANT, "orcs", {"general": "Build the Свалка first."})   # strategy/ занята без git — не трогаем
    assert not journal.ensure_repo()
    shutil.rmtree(strategy_dir)
    assert journal.ensure_repo() and (strategy_dir / ".git").is_file()
    assert journal.commit([notes.update(VARIANT, "orcs", {"general": "Build the Свалка first."})], "Орки: first notes")
    assert git("log", "--format=%s", journal.BRANCH, "--").split("\n")[0] == "Орки: first notes"
    assert git("log", "--format=%s", "master", "--").strip() == "code"
    assert f"{VARIANT}/orcs.md" in git("ls-tree", "-r", "--name-only", journal.BRANCH)


class Answering(ClaudePlayer):
    """Отвечает по тексту вопроса: answer(prompt) — поле answer; None — сбой вызова."""

    def __init__(self, answer):
        super().__init__(transcript_dir=None, tools=False)
        self.answer = answer
        self.started = True   # без плана партии и системного промпта
        self.prompts = []

    def call(self, prompt, schema, system):
        self.prompts.append(prompt)
        answer = self.answer(prompt)
        if answer is None:
            raise ClaudeError("no answer in this test")
        return {"answer": answer, "analysis": "c"}


def hydra_game(hydra_answer):
    from heroes.engine import movement
    claude = Answering(lambda prompt: {"destination": "Асандер", "hydra_destination": hydra_answer}
                       if "QUESTION (destination" in prompt
                       else "Улин" if "QUESTION (hydra_destination" in prompt else None)
    game = Game([claude, RandomBot(0)], factions=["nagas", "humans"])
    nagas = game.state.players[0]
    nagas.region, nagas.hydra, nagas.units["hydra"] = "Асандер", "Чарил", 1
    game.state.players[1].region = "Есмор"
    movement.move_action(game, 2)
    return claude, game


def test_hydra_moves_in_the_same_call_as_nagas():
    claude, game = hydra_game("Эльри")
    asked = [prompt for prompt in claude.prompts if "QUESTION (destination" in prompt]
    assert len(asked) == 1 and "2 SECRET QUESTIONS AT ONCE" in asked[0] and "QUESTION (hydra_destination" in asked[0]
    assert "answer.destination — " in asked[0] and "answer.hydra_destination — " in asked[0]
    assert not any("QUESTION (hydra_destination" in prompt for prompt in claude.prompts if prompt not in asked)
    assert game.state.players[0].hydra == "Эльри"
    assert [(e["kind"], e["answer"]) for e in game.history if e["player"] == 0][:2] == [
        ("destination", "Асандер"), ("hydra_destination", "Эльри")]


def test_rejected_part_of_a_joint_answer_is_asked_again_alone():
    claude, game = hydra_game("Нокс")   # гидре туда не дойти
    again = claude.prompts[1]
    assert "YOUR PREVIOUS ANSWER WAS REJECTED" in again and "QUESTION (hydra_destination" in again
    assert "QUESTION (destination" not in again
    assert game.state.players[0].region == "Асандер" and game.state.players[0].hydra == "Улин"


def test_armorer_upgrade_is_answered_with_the_commitment():
    from heroes.engine import combat
    claude = Answering(lambda prompt: {"commit": {"pixie": 1, "griffin": 0, "ent": 1}, "upgrade": 5}
                       if "QUESTION (commit_army" in prompt else None)
    game = Game([claude, RandomBot(0)], factions=["elves", "humans"])
    elves = game.state.players[0]
    elves.built.append("armorer")
    elves.units = {"pixie": 1, "griffin": 0, "ent": 1}
    elves.resources["wood"] = 3
    for pl in game.state.players:
        pl.region = "Икара"
    game.state.players[1].units = {"soldier": 0, "knight": 0, "archmage": 0}
    combat.player_battle(game, "Икара", [0, 1])
    assert len(claude.prompts) == 1 and "answer.upgrade — Лавка бронника" in claude.prompts[0]
    assert "pixie 1→2, ent 6→10" in claude.prompts[0]
    assert elves.resources["wood"] == 1                          # модифицированы обе заявленные единицы
    assert "Battle in Икара: Player 1 — strength 12, Player 2 — strength 0" in game.state.log
    # модификация — часть заявки (сверх того, на что хватает дерева, не просят), отдельного вопроса нет
    assert [(e["kind"], e["answer"]) for e in game.history if e["player"] == 0] == [
        ("commit_army", {"army": {"pixie": 1, "ent": 1}, "upgrade": 3})]


def test_engine_error_goes_only_to_its_question():
    from heroes.engine.decisions import Decision, check_choice
    from heroes.engine.game import GameView
    claude = Answering(lambda prompt: "Асандер")
    game = Game([claude, RandomBot(0)])
    start = [Decision("choose_start", 0, "Choose", {"options": ["Асандер"]}, lambda a: check_choice(a, ["Асандер"]),
                      "Асандер") for _ in range(2)]
    claude.on_invalid(start[0], "Нокс", "Not a valid option")   # по нему взят ответ по умолчанию
    claude.decide(start[1], GameView(game, 0))
    assert "REJECTED" not in claude.prompts[-1]
    claude.on_invalid(start[1], "Нокс", "Not a valid option")
    claude.decide(start[1], GameView(game, 0))
    assert "YOUR PREVIOUS ANSWER WAS REJECTED: Not a valid option" in claude.prompts[-1]


def test_claude_is_not_restarted_after_ctrl_c():
    """Ctrl+C убивает и процесс claude; поток Claude-игрока не запускает его снова, а сразу сдаётся."""
    game = Game([RandomBot(0), RandomBot(1)])
    claude = ClaudePlayer(transcript_dir=None, tools=False)
    claude.binary = "/bin/false"
    claude.game = game
    game.stopping.set()
    with pytest.raises(ClaudeError, match="stopping"):
        claude.call("prompt", schemas.RESPONSE_SCHEMA, None)


def test_rules_clarifications(strategy_dir):
    claude = Reflecting(1, [
        {"review": "r", "update": False, "rules_add": ["x" * 400]},                 # пункт длиннее лимита — повтор
        {"review": "r", "update": False, "rules_add": ["Гидра dies after any battle.", "Храм counts earlier summons."]},
    ])
    game = Game([claude, RandomBot(0)], factions=["nagas", "humans"])
    assert "SPECIAL RULES OF THIS COMPUTER VERSION" in prompts.system_prompt(game, 0, VARIANT)
    assert not (strategy_dir / "RULES_SPECIAL_ENG.md").exists() and len(notes.load_rules()) == len(notes.SEED_RULES)
    game.run()
    review, outcome = claude.reflect(game, 0)
    assert "is 400 characters" in claude.prompts[-1][1] and "special rules added" in outcome
    assert notes.load_rules() == [*notes.SEED_RULES, "Гидра dies after any battle.", "Храм counts earlier summons."]
    system = prompts.system_prompt(game, 0, VARIANT)
    assert "- Гидра dies after any battle." in system and "the engine decides" in system
    assert "«Герои» (Hobby World) — rules reference" in system
    claude.answers = [{"review": "r", "update": False, "rules_add": ["Храм counts earlier summons.", "New fact."]}]
    claude.reflect(game, 0)
    assert notes.load_rules()[-2:] == ["Храм counts earlier summons.", "New fact."]   # повтор не добавлен
    assert len(notes.load_rules()) == len(notes.SEED_RULES) + 3
    for bad in ("text", ["a"] * 6, [""]):
        try:
            notes.validate_rules_add({"rules_add": bad})
        except ValueError:
            continue
        raise AssertionError(bad)


STREAM = "\n".join(json.dumps(e, ensure_ascii=False) for e in [
    {"type": "system", "subtype": "init"},
    {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Чарил is two moves away."},
        {"type": "tool_use", "name": "Bash", "input": {"command": "heroes-tool tree 2"}},
        {"type": "tool_use", "name": "Read", "input": {"file_path": "/x/heroes/sim.py"}}]}},
    {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": "heroes-run -c 'print(1)'"}},
        {"type": "text", "text": "  So I go there now.  "},
        {"type": "tool_use", "name": "StructuredOutput", "input": {"answer": "Чарил"}}]}},
    {"type": "result", "is_error": False, "session_id": "s", "total_cost_usd": 0.5,
     "usage": {"input_tokens": 2, "cache_creation_input_tokens": 300, "cache_read_input_tokens": 700, "output_tokens": 900,
               "output_tokens_details": {"thinking_tokens": 800}},
     "structured_output": {"answer": "Чарил"}},
])


def test_tool_log_parse_and_report():
    from heroes.protocol import tool_log
    out, calls, text = tool_log.parse_stream(STREAM + "\nnot json")
    assert text == "Чарил is two moves away.\n\nSo I go there now."
    assert out["structured_output"]["answer"] == "Чарил"
    assert calls == [{"tool": "heroes-tool", "detail": "tree 2"}, {"tool": "Read", "detail": "/x/heroes/sim.py"},
                     {"tool": "heroes-run", "detail": "-c 'print(1)'"}]
    assert tool_log.parse_stream("garbage") == (None, [], "")
    entries = [{"round": 3, "kind": "build_recruit", **c} for c in calls] + [
        {"round": 5, "kind": "destination", "tool": "heroes-tool", "detail": "board Ева"}]
    text = tool_log.report("Лео", entries, 40, True)
    assert "4 calls in 2 of 40 questions" in text and "round  3 · build_recruit: heroes-tool tree 2" in text
    assert "round  5 · destination: heroes-tool board Ева" in text and "by tool: heroes-tool 2, Read 1, heroes-run 1" in text
    assert "none in 40 calls" in tool_log.report("Лео", [], 40, True) and "none were available" in tool_log.report("Лео", [], 40, False)


def test_call_records_tools(monkeypatch, tmp_path):
    claude = ClaudePlayer(transcript_dir=tmp_path, workdir=tmp_path / "w", tools=False)
    claude.binary = "claude"
    seen = {}

    def run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, STREAM, "")
    monkeypatch.setattr(subprocess, "run", run)
    claude.where = (4, "destination")
    result = claude.call("question", schemas.RESPONSE_SCHEMA, "system")
    assert result == {"answer": "Чарил", "analysis": "Чарил is two moves away.\n\nSo I go there now."} and "stream-json" in seen["cmd"] and "--verbose" in seen["cmd"]
    assert [(e["round"], e["kind"], e["tool"]) for e in claude.tool_log] == [(4, "destination", "heroes-tool"),
                                                                           (4, "destination", "Read"), (4, "destination", "heroes-run")]
    record = json.loads((tmp_path / f"{claude.session_id}.jsonl").read_text(encoding="utf-8"))
    assert record["tools"][0] == "heroes-tool tree 2" and record["tools_enabled"] is False
    assert record["tokens"] == {"input": 1002, "output": 900, "thinking": 800} and record["analysis"].startswith("Чарил is")
    game = Game([claude, RandomBot(0)], factions=["orcs", "humans"])
    assert "3 calls in 1 of 1 questions" not in claude.tools_report(game, 0)   # инструменты выключены (tools=False)
    claude.tools = True
    assert "3 calls in 1 of 1 questions" in claude.tools_report(game, 0)
    assert (tmp_path / f"{claude.session_id}.tools.txt").exists()
    claude.call("question", schemas.RESPONSE_SCHEMA, None)
    assert claude.tokens == {"input": 2004, "output": 1800, "thinking": 1600}   # usage — за вызов, складываем
    usage = []
    cli.report_claude_usage(game, usage.append)
    assert "2 Claude calls" in usage[0] and "tokens: 2,004 in, 1,800 out (thinking 1,600; ≈ 900 out per call)" in usage[0]


def test_default_effort_and_time_budget():
    from heroes.players.claude_cli import DEFAULT_EFFORT, tokens_of
    claude = ClaudePlayer(transcript_dir=None, tools=False)
    assert DEFAULT_EFFORT == "max" and claude.effort == "max" and claude.variant.endswith("/max")
    assert ClaudePlayer(transcript_dir=None, tools=False, effort=None).variant.endswith("/default")   # старые сохранения
    game = Game([claude, RandomBot(0)], factions=["orcs", "humans"])
    assert "hard limit of about 10 minutes" in claude.system_prompt(game, 0)
    assert "time limit" not in Workspace("v", "s", Path("/nonexistent")).prompt_text()
    assert tokens_of({}) == {"input": 0, "output": 0, "thinking": 0}


class Recorder(RandomBot):
    def __init__(self, seed):
        super().__init__(seed)
        self.forced = []

    def on_forced(self, decision, answer, view):
        self.forced.append((decision.kind, decision.reason, view.player == decision.player))


def test_every_skipped_question_has_a_reason():
    kinds = set()
    for seed in range(12):
        players = [Recorder(seed * 4 + i) for i in range(4)]
        factions = [["orcs", "humans", "nagas", "undead"], ["elves", "dwarves", "nagas", "orcs"],
                    ["undead", "elves", "dwarves", "humans"]][seed % 3]
        Game(players, factions=factions).run()
        for kind, reason, own in (f for pl in players for f in pl.forced):
            assert reason and own, kind
            kinds.add(kind)
    assert {"destination", "build_recruit"} <= kinds


def test_skipped_question_goes_to_events_in_place():
    claude = ClaudePlayer(transcript_dir=None, tools=False)
    game = Game([claude, RandomBot(0)], names=["Лео", "Ева"], factions=["orcs", "humans"])
    view = SimpleNamespace(game=game, player=0)
    claude.seen_log = len(game.state.log)
    game.log("Ева moves to Улин")
    claude.on_forced(SimpleNamespace(kind="destination", reason="your 1 MP are not enough to enter any neighbouring "
                                     "region, so you stay in Чарил"), "Чарил", view)
    game.log("Лео stays in Чарил")
    claude.on_forced(SimpleNamespace(kind="commit_army", reason="you had no units to commit"), {}, view)
    assert claude.new_events(game) == [
        "Ева moves to Улин",
        "(You were not asked destination: your 1 MP are not enough to enter any neighbouring region, so you stay in "
        "Чарил; the engine answered for you: Чарил.)",
        "Лео stays in Чарил",
        "(You were not asked commit_army: you had no units to commit.)"]
    assert claude.new_events(game) == []


def test_tools_status_in_log(monkeypatch):
    monkeypatch.setattr("heroes.players.claude_cli.tools_available", lambda: False)
    off, no_bwrap = ClaudePlayer(transcript_dir=None, tools=False), ClaudePlayer(transcript_dir=None, effort="low")
    game = Game([off, no_bwrap], names=["Лео", "Ева"], factions=["orcs", "humans"])
    lines = []
    cli.report_claude_setup(game, lines.append)
    assert lines[0].startswith("Лео: Claude ") and lines[0].endswith("effort max, tools off (--no-tools)")
    assert lines[1].endswith("effort low, tools off (no bwrap sandbox found)")
    monkeypatch.setattr("heroes.players.claude_cli.tools_available", lambda: True)
    assert ClaudePlayer(transcript_dir=None).tools_status() == "tools on (bwrap sandbox)"


def test_tools_missing_and_report_before_review(strategy_dir):
    claude = Reflecting(1, [
        {"review": "r", "update": False, "tools_missing": ["x" * 400]},                       # длиннее лимита — повтор
        {"review": "r", "update": False, "tools_missing": ["distance to every fortress for N MP", "price of next build"]},
    ])
    claude.tools = True
    claude.tool_log.append({"round": 2, "kind": "destination", "tool": "heroes-tool", "detail": "reach 3"})
    game = Game([claude, RandomBot(0)], factions=["orcs", "humans"])
    game.run()
    lines = []
    cli.record_lessons(game, out=lines.append, on_thought=lambda *a: None)
    asked = next(prompt for _, prompt in claude.prompts if "QUESTION (strategy_notes)" in prompt)
    assert "TOOL USAGE IN THIS GAME" in asked and "heroes-tool reach 3" in asked and "tools_missing" in asked
    assert "is 400 characters" in claude.prompts[-1][1]
    assert any("heroes-tool reach 3" in line for line in lines)
    first_report = next(i for i, line in enumerate(lines) if "Tools used by" in line)
    assert first_report < next(i for i, line in enumerate(lines) if "tools missing:" in line)   # статистика — до разбора
    wanted = (strategy_dir / "TOOLS_WANTED.md").read_text(encoding="utf-8")
    assert "- price of next build" in wanted and "- distance to every fortress for N MP" in wanted
    for bad in ("text", ["a"] * 6, [""]):
        try:
            notes.validate_tools_missing({"tools_missing": bad})
        except ValueError:
            continue
        raise AssertionError(bad)
