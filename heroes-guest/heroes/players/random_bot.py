"""Случайный бот: делает случайные, но в основном допустимые ходы — и в особых решениях фракций.
Нужен для тестов движка и как простой соперник."""
from __future__ import annotations

import random
from typing import Any

from .base import Player


class RandomBot(Player):
    def __init__(self, seed: int | None = None):
        self.rng = random.Random(seed)
        self.invalid: list[tuple[str, Any, str]] = []

    def on_invalid(self, decision, answer, error):
        self.invalid.append((decision.kind, answer, error))

    def decide(self, decision, view):
        handler = getattr(self, f"_{decision.kind}", None)
        return handler(decision.data) if handler else decision.default

    # --- по типам решений ---------------------------------------------------------------

    def _choose_start(self, data):
        return self.rng.choice(data["options"])

    def _action_order(self, data):
        return self.rng.sample(data["actions"], 4)

    def _distribute_resources(self, data):
        split = dict.fromkeys(data["allowed"], 0)
        for _ in range(data["amount"]):
            split[self.rng.choice(data["allowed"])] += 1
        return split

    def _build_recruit(self, data):
        res = dict(data["resources"])
        build = None
        if data["options"] and self.rng.random() < 0.8:
            build = self.rng.choice(sorted(data["options"]))
            for k, v in data["options"][build]["cost"].items():
                res[k] -= v
        hire: dict[str, int] = {}
        units = list(data["hire"])
        self.rng.shuffle(units)
        for unit in units:
            cost = data["hire"][unit]["cost"]
            while all(res.get(k, 0) >= v for k, v in cost.items()) and self.rng.random() < 0.7:
                for k, v in cost.items():
                    res[k] -= v
                hire[unit] = hire.get(unit, 0) + 1
        return {"build": build, "hire": hire}

    def _reserve_object(self, data):
        return self.rng.choice(sorted(data["options"])) if self.rng.random() < 0.3 else None

    def _destination(self, data):
        return self.rng.choice(sorted(data["options"]))

    def _region_stance(self, data):
        return self.rng.choice(data["options"])

    def _random_army(self, units):
        return {u: self.rng.randint(0, info["count"]) for u, info in units.items()}

    def _commit_army(self, data):
        return self._random_army(data["units"])

    def _choose_reward(self, data):
        kind = self.rng.choice(sorted(data["options"]))
        if kind == "monster":
            return {"type": "monster"}
        if kind == "artifact":
            q = self.rng.choice(sorted(data["options"]["artifact"]))
            return {"type": "artifact", "from": q, "artifact": self.rng.choice(data["options"]["artifact"][q])}
        amount = data["options"]["resources"]["amount"]
        pool = {q: dict(r) for q, r in data["options"]["resources"]["from"].items()}
        take: dict[str, dict[str, int]] = {}
        for _ in range(amount):
            choices = [(q, k) for q, r in pool.items() for k, v in r.items() if v > 0]
            q, k = self.rng.choice(choices)
            pool[q][k] -= 1
            take.setdefault(q, {})
            take[q][k] = take[q].get(k, 0) + 1
        return {"type": "resources", "take": take}

    def _strongest_army(self, units):
        return {u: info["count"] for u, info in units.items()}

    def _capture_fortress(self, data):
        army = self._strongest_army(data["units"])
        strength = sum(info["strength"] * info["count"] for info in data["units"].values())
        return army if strength > data["strength"] and self.rng.random() < 0.7 else None

    def _fight_monster(self, data):
        total = sum(info["strength"] * info["count"] for info in data["units"].values())
        beatable = [i for i, m in data["candidates"].items() if m["strength"] < total]
        if not beatable or self.rng.random() < 0.3:
            return None
        target = self.rng.choice(beatable)
        need = data["candidates"][target]["strength"] + 1
        army: dict[str, int] = {}
        # Берём сначала сильные единицы, пока не наберём нужную силу.
        for unit, info in sorted(data["units"].items(), key=lambda kv: -kv[1]["strength"]):
            while need > 0 and army.get(unit, 0) < info["count"]:
                army[unit] = army.get(unit, 0) + 1
                need -= info["strength"]
        return {"monster": int(target), "army": army}

    def _take_artifact(self, data):
        return True

    # --- особые решения фракций ---------------------------------------------------------

    def _yes_no(self, data):
        return self.rng.random() < 0.6

    _exchange_on_gain = _buy_science = _make_lake = _yes_no

    def _count(self, data):
        return self.rng.randint(0, data["max"])

    _buy_move_points = _battle_upgrade = _count

    def _curse_region(self, data):
        if data["optional"] and self.rng.random() < 0.3:
            return None
        return self.rng.choice(data["options"])

    def _circle_note(self, data):
        return self.rng.choice(data["options"])

    def _summon_hydra(self, data):
        return self.rng.choice(data["options"])

    def _hydra_destination(self, data):
        return self.rng.choice(sorted(data["options"]))

    def _raise_action(self, data):
        left, answer = data["max"], {}
        for action in data["options"]:
            x = self.rng.randint(0, left)
            if x:
                answer[action] = x
                left -= x
        return answer

    def _upgrade_units(self, data):
        units, stock, answer = dict(data["units"]), dict(data["stock"]), {}
        for key, path in data["paths"].items():
            most = min([units.get(path["from"], 0)] + [stock.get(k, 0) // v for k, v in path["cost"].items()])
            n = self.rng.randint(0, max(0, most))
            if n:
                answer[key] = n
                units[path["from"]] -= n
                units[path["to"]] = units.get(path["to"], 0) + n
                for k, v in path["cost"].items():
                    stock[k] -= v * n
        return answer

    def _avoid_battle(self, data):
        if self.rng.random() < 0.5:
            return None
        have, split = dict(data["resources"]), {"wood": 0, "iron": 0, "gold": 0}
        for _ in range(data["amount"]):
            k = self.rng.choice([k for k, v in have.items() if v])
            have[k] -= 1
            split[k] += 1
        return split
