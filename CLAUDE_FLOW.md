# Claude-игрок: файлы и инструменты

```
 ┌─ ПАРТИЯ N ─────────────────────────────────────────────────────────────────────────────────────────┐
 │                                                                                                    │
 │  НАЧАЛО (game_plan)                ХОД                                КОНЕЦ (strategy_notes)       │
 │                                                                                                    │
 │  claude -p --session-id ─────────► claude -p --resume ──────────────► ответ Claude:                │
 │  + системный промпт:               ├ Game.ask                         ├ review                     │
 │  ├ docs/RULES_ENG.md               │ ├ YOUR REMINDERS: раздел         ├ sections                   │
 │  ├ RULES_SPECIAL_ENG.md ◄─┐        │ │ Reminders из <фракция>.md      ├ rules_add, tools_missing   │
 │  ├ <фракция>.md ◄───────┐ │        │ ├ EVERY DECISION MATTERS         └ summary (текст коммита)    │
 │  ├ data/factions, map   │ │        │ └ в конце: подсказка TOOLS                   │                │
 │  └ раздел TOOLS         │ │        ├ ответ: анализ текстом ─► story.md            │                │
 │  рабочая папка (cwd):   │ │        │   затем JSON {answer}                        │                │
 │  ws/<сессия>/ ◄───────┐ │ │        ├ heroes-run, heroes/sim.py                    │                │
 │  = копия scripts/     │ │ │        └ heroes-tool (10 справок, с board)            │                │
 │                       │ │ │                                                       ▼                │
 └───────────────────────┼─┼─┼───────────────────────────────────────────────────────┼────────────────┘
                         │ │ │                                                       │
                         │ │ │                    strategy/ (git, ветка strategy)    │
                         │ │ │        ┌──────────────────────────────────────────────┘
                         │ │ │        │
                         │ │ └────────┼─ rules_add ─► RULES_SPECIAL_ENG.md ────┐
                         │ └──────────┼─ sections  ─► <вариант>/<фракция>.md ──┤  в начало
                         └────────────┼─ скрипты   ─► scripts/ ────────────────┤  ПАРТИИ N+1
                                      └─ коммит    ─► git log (HISTORY) ───────┘  (HISTORY — в вопрос
                                                                                   strategy_notes)
         не возвращается к Claude: results.csv, RULES_CONFUSION.md, TOOLS_WANTED.md (tools_missing, для разработчика), logs/claude/*.jsonl,
         logs/claude/<сессия>.story.md (пересказ партии для человека: план, анализ и решение на каждом вопросе, разбор),
         logs/claude/<сессия>.tools.txt (отчёт об инструментах: печатается в журнал ДО разбора и входит в вопрос о разборе)
```
