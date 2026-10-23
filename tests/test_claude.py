"""Минимальные офлайн-проверки Claude-игрока: вызов CLI подменяется генератором ответов."""
import json
import random
import shutil
import subprocess

from heroes import cli
from heroes.engine.game import Game
from heroes.players.claude_cli import ClaudeError, ClaudePlayer
from heroes.players.random_bot import RandomBot
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
            self.schema = json.loads(prompt.rsplit(SCHEMA_LINE, 1)[1])
        return {"answer": random_instance(self.schema, self.rng), "comment": "plan"}


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


class Peeking(FakeClaude):
    """На каждый вопрос сначала просит планшеты всех игроков (по номеру и по имени), затем снова и снова."""

    def call(self, prompt, schema, system):
        result = super().call(prompt, schema, system)
        if "Now answer the same question" in prompt:
            self.viewed.append(prompt)
        return {**result, "view_boards": [0, "Боб", True, 7, "?"]}


def test_view_boards():
    claude = Peeking(3)
    claude.viewed = []
    game = Game([claude, RandomBot(0)], names=["Ада", "Боб"])
    game.run()
    assert game.state.round == 16
    assert claude.viewed and all(prompt.count("BOARD:") == 2 for prompt in claude.viewed)   # оба, по разу за решение
    shown = claude.viewed[-1]
    assert "BOARD: Боб" in shown and ", player 1" in shown and "QUESTION" not in shown
    # на просьбу — ровно один повтор вопроса, и ответ после него засчитывается (партия доиграна без умолчаний)
    asked = [prompt for _, prompt in claude.prompts if "QUESTION (" in prompt and "game_plan" not in prompt]
    assert len(claude.viewed) == len(asked)
    assert not any("did not answer" in line for line in game.state.log)


def test_requested_boards():
    game = Game([RandomBot(0), RandomBot(1), RandomBot(2)], names=["Ада", "Боб", "Вера"])
    assert ClaudePlayer.requested_boards(game, [2, " боб ", "0", 2, False, -1, 3, None]) == [2, 1, 0]
    assert ClaudePlayer.requested_boards(game, "Боб") == []


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
        self.kinds.add(decision.kind)
        return super().decide(decision, view)


def test_schemas_cover_every_faction_decision():
    factions = ["humans", "undead", "dwarves", "orcs", "elves", "nagas"]
    wanted = {"raise_action", "circle_note", "summon_hydra", "hydra_destination", "curse_region",
              "buy_move_points", "battle_upgrade", "make_lake", "exchange_on_gain"}
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
            return {"answer": self.answers.pop(0), "comment": "lessons"}
        return super().call(prompt, schema, system)


VARIANT = notes.variant("claude-opus-5-5", None)


def test_strategy_notes_round_trip(strategy_dir):
    claude = Reflecting(1, [
        {"update": False},                                                     # нет разбора — повтор
        {"review": "ok", "update": True, "sections": {"humans": "too long " * 200}},   # превышен лимит — повтор
        {"review": "- lost the race for Юноро", "update": True,
         "sections": {"general": "- rush monsters", "humans": "- block portals"}},
    ])
    assert claude.variant == "claude-opus-5-5/default"
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
    assert "claude-opus-5-5/default · Player 1 (Орки)" in log


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
        return {"answer": answer, "comment": "c"}


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
