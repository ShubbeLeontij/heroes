# «Герои» (Hobby World) — rules reference for the computer version

Authors: Yuri Zhuravlev, Egor Nikolaev. Publisher: Hobby World, 2024–2026.
The English edition is *Heroes: Write & Conquer*.
Genre: "draw & write" — each player marks everything with a marker on their own board. 2–4 players (no solo), 16 rounds.

This file is the English translation of `docs/RULES.md`; the game engine sends it to every Claude player as part of the
system prompt. Names of regions, buildings, units and factions stay in the original Russian. Special rules of the computer version follow in the next section of your prompt.

---

## 1. Components and the board

Each faction has its own board. The structure is the same for all:

1. **Building zone** — buildings of four levels (I–IV) with arrows for requirements. For some factions the faction's
   properties block is here too.
2. **Units** — usually 3 kinds of combat units, each with a strength and a hire cost. Values marked with `»` can be
   modified (the old value is crossed out).
3. **Emblem and round counter** 1–16.
4. **Artifacts** — 6 of them. The regions and conditions to gain them are the same for all, the effects differ per faction.
5. **Action zone** — 5 cells: wood, iron, gold, move, build & hire.
6. **Resource stock** — wood, iron, gold. For each resource there are marks: "stockpiled ≥14 → 2 VP, ≥22 → 2 VP,
   ≥30 → 3 VP". The VP are circled permanently, even if the resource is spent later.
7. **Map** — 27 regions (common to all factions, see §6).
8. **Movement cost** by 4 terrain types: fields / forests / mountains / lakes. Each faction has its own.
9. **VP count**.
10. **Monster line** — 8 monsters.

The "chest" symbol (🎁, `a` in the engine) means any one resource of your choice: wood, iron or gold.

## 2. Setup

1. Each player chooses a faction and writes the starting number of units into the lowest unit slot (printed on the
   background of the block).
2. Regions are crossed out by the number of players (dots above the name):
   - 2 players — regions with 3 and 4 dots are unavailable: Вернесс, Гленнфор, Икара, Оррид, Юноро, Яскер;
   - 3 players — regions with 4 dots are unavailable: Вернесс, Гленнфор, Юноро;
   - 4 players — the whole map is open.
3. The first player is chosen arbitrarily.
4. Starting regions are chosen in turn **counter-clockwise**, beginning with the player to the right of the first
   player. Only a region with a portal can be chosen. Two players cannot share a starting region.
5. Each player receives the resources of their starting region.

## 3. The round

- **Choosing actions.** The first player chooses 4 of the 5 actions and writes the numbers 1, 2, 3, 4 into their cells.
  The number is both the **order** and the **level** of the action. The fifth cell stays empty. All players copy these
  numbers to their boards.
- Actions are performed in ascending order. Every action is performed by all players simultaneously; the next one
  begins when everybody has finished the previous one.
- At the end of the round the round number is crossed out and the neighbour to the **left** becomes the first player.
  After round 16 comes scoring.

### Actions of level X

| Action | Effect |
|---|---|
| Gather wood / iron / gold | +X of that resource |
| Move | X movement points (MP), then the steps below |
| Build & hire | One building of level ≤ X **and/or** hiring any number of units |

### Building
Conditions:
- the building's level is not above X;
- you have enough resources;
- at least one of the required buildings (by the arrows) is built.

After it is built the building is marked and its effect works at once. If there is a **foundation** in your current
region, the building you just built can be put on it: its level is written into the foundation, and at the end of the
game that level brings as many VP. The value can later be replaced with a higher one.

### Hiring
Requires the building "Hire [unit]" to be built. Hiring does not depend on the action level. You may hire any number of
units of any kinds while you can pay the gold (or another specified currency).

A building effect under an asterisk is a property of those units: it works even if the building is not built (for
example, blasters obtained by modification for rage are stronger at a fortress even without the Мастерская).

### Move (all at once)
1. Receive X MP plus building bonuses.
2. **Secretly** choose a destination region. Each step to a neighbouring region costs as many MP as the terrain of the
   **region you enter** costs. All regions with portals count as neighbours of each other. You may stay where you are
   (then steps 2–4 are skipped).
3. Circle the destination region — it is now your current region and **visited**. Only the region where your move ended
   counts as visited.
4. Gain the region's resources on the **first** visit. Each player gets them independently of the others.
5. Starting with the first player, everybody announces their regions.
   - **Several players in one region.** Each declares whether they want to fight and/or claim the fortress or artifact.
     If at least one wants to fight, or there are several claimants, a battle between **all** the players in the region
     takes place. Otherwise the single claimant takes the prize (the others may yield it to them).
   - **Players in different regions.** Starting with the first player, each may capture a fortress or gain an artifact.
   - Battles between players happen before everything else: first the one involving the first player, then clockwise.
     An artifact in a region is gained only **after all battles**, including the fight with a monster.
6. At the end of the move each player may fight a monster. Unspent MP let you skip monsters in the line (§4).

Capturing a fortress, an artifact and a monster fight are possible **only** in a round in which move was chosen.

## 4. Battles

**General rule.** Before a battle the player decides which units to **commit**: all, some or none. The strength of the
army is the sum of the strengths of the committed units plus the effects of buildings and artifacts. A bonus can also be
added to zero strength. To win you must exceed the opponent **by at least 1**. All committed units die whatever the
outcome, the winner's too.

### Battle between players
1. Before the battle everybody sees the other players' boards.
2. Each player **secretly** writes down the committed units (no peeking).
3. The strengths are revealed. The greatest strength wins. **On a tie everybody loses.**
4. The winner circles the leftmost uncircled monster in their own line (this fixes a victory and gives its VP at the end
   of the game) and chooses **one** reward:
   - that monster's resources;
   - the losers' resources, in the amount (the winner's strength − the strength of the weakest loser), taken from the
     losers in any combination of wood, iron and gold (factions' unique resources cannot be taken);
   - one artifact of one of the losers. Its permanent effect disappears for the loser, a one-time effect stays.
5. Additionally the winner may capture the fortress or gain the artifact in this region. If the reward was a taken
   artifact, the region's artifact cannot be taken this round.

### Fight with a monster
- You may fight the leftmost uncircled monster of your own line. You do not have to move to do this.
- Each unspent MP lets you skip one monster; you can come back to the skipped ones later.
- At most one monster per round. Each player has their own line.
- Victory: the monster is circled, its resources go to your stock, its VP are counted at the end of the game.

| Monster strength | 2 | 3 | 5 | 8 | 13 | 19 | 25 | 32 |
|---|---|---|---|---|---|---|---|---|
| VP | 1 | 1 | 1 | 2 | 3 | 4 | 5 | 7 |
| Resources | — | 2🎁 | 1w 1i 1g | 2w 2i 2g | 5🎁 | 2w 2i 2g | 3w 3i 3g | 4w 4i 4g |

(Some factions have their own monster line instead of this one; it is shown on the faction board.)

### Capturing a fortress
You must exceed the strength of the fortress. The one who captures it gets its resources, and the other players cross
the fortress out: every fortress has only **one** owner per game. The VP are counted at the end of the game.

## 5. Map objects

- **Portals** — all connected to each other. A move between portals costs MP by the terrain of the destination region.
- **Fortresses** — strength, VP and resources; captured once per game.
- **Foundations** — each player has their own. The value = the building's level = VP.
- **Artifacts** — each artifact has one owner; the others cross it out on their boards but may take it away in battle.
  Conditions to gain them:

| Region | Таскар | Цурт | Дорталь | Золен | Содмур | Шог Ид |
|---|---|---|---|---|---|---|
| Condition (all except nagas) | 12 wood | 12 iron | 12 gold | 4 foundations with buildings put on them | 5 battle victories (captured fortresses + circled monsters) | 4 mountain regions visited (Шог Ид counts) |
| Nagas | 3 kraks | 3 kraks | 3 kraks | as for everybody | as for everybody | as for everybody |
| Elves with the Гнездовье, alternative | 5 wood | 4 wood | 6 wood | 2 wood | 3 wood | 2 wood |

## 6. The map (27 regions)

The full list of regions (terrain, resources on the first visit, objects, neighbours) is in the MAP section of your
prompt (also `docs/MAP.md`; the machine-readable version is `data/map.json`).

## 7. Scoring (after round 16)

1. VP for built buildings (printed in the blocks) and for their effects.
2. VP for foundations (the sum of the written levels).
3. Circled VP for stockpiled resources.
4. VP for captured fortresses and circled monsters.
5. VP from artifact effects.

The player with the greatest total wins. On a tie, whoever has more resources in stock wins; if the tie remains, all
tied players win. For some factions the scoring cells differ: for example, the undead get VP for cursed regions instead
of artifacts.

## 8. Errata and FAQ (important for the engine)

- An artifact cannot be gained before the fight with a monster: only after all battles.
- A fortress, an artifact and a monster need a round in which move was chosen.
- A winner who takes an artifact from a loser does not have to meet its condition.
- Orcs: the monster of strength 13 gives 5🎁 (the first print had a typo).
- Dwarves: a unit can be strengthened by only one mechanic.

## 9. Factions

| Box | Factions |
|---|---|
| Heroes | Люди (humans), Нежить (undead), Гномы (dwarves), Орки (orcs), Эльфы (elves), Наги (nagas) |
| Heroes II | Гремлины, Механизмы, Элементали, Демоны, Полурослики, Культисты |
| Heroes III | Гриболюды, Оборотни, Архонты, Ящеры, Тёмные эльфы, Циклопы |

The rules of each faction are on its board in your prompt.
