# Claude-игрок: файлы и инструменты

```
 ┌─ ПАРТИЯ N ───────────────────────────────────────────────────────────────────────────────────┐
 │                                                                                              │
 │  НАЧАЛО                         ХОД                              КОНЕЦ (strategy_notes)      │
 │                                                                                              │
 │  системный промпт ───────►  claude -p --resume  ─────────────►  ответ Claude:               │
 │  ├ docs/RULES_ENG.md        ├ Game.ask                          ├ review                    │
 │  ├ RULES_SPECIAL_ENG.md ◄─┐ ├ view_boards                       ├ sections                  │
 │  ├ <фракция>.md ◄───────┐ │ ├ heroes-run, heroes/sim.py         ├ rules_add, tools_missing  │
 │  ├ data/factions, map   │ │ └ heroes-tool (9 справок)           └ comment                   │
 │  └ ws/…/scripts/ ◄────┐ │ │                                              │                   │
 │                       │ │ │                                              ▼                   │
 └───────────────────────┼─┼─┼──────────────────────────────────────────────┼───────────────────┘
                         │ │ │                                              │
                         │ │ │            strategy/ (git, ветка strategy)   │
                         │ │ │       ┌──────────────────────────────────────┘
                         │ │ │       │
                         │ │ └───────┼─ rules_add ─► RULES_SPECIAL_ENG.md ──┐
                         │ └─────────┼─ sections  ─► <вариант>/<фракция>.md ─┤  в начало
                         └───────────┼─ скрипты   ─► scripts/ ───────────────────┤  ПАРТИИ N+1
                                     └─ коммит    ─► git log (HISTORY) ─────┘  (HISTORY — в вопрос
                                                                               strategy_notes)
         не возвращается к Claude: results.csv, RULES_CONFUSION.md, TOOLS_WANTED.md (tools_missing, для разработчика), logs/claude/*.jsonl,
         logs/claude/<сессия>.tools.txt (отчёт об инструментах: печатается в журнал ДО разбора и входит в вопрос о разборе)
```
