# Опыт Claude-игроков

Ветка `strategy` репозитория игры, подключённая в папку `strategy/` как git worktree
(в `master` эта папка в `.gitignore`). Каждое изменение, которое Claude-игрок вносит после партии, — коммит
с его объяснением (тема) и разбором партии (тело). Claude этот файл не видит.

```
strategy/
  <модель>/<effort>/          вариант Claude: модель и уровень усилий («default» — без --effort)
    <фракция>.md              заметки: General strategy, Reminders, Against <фракция>…
    scripts/                  скрипты Claude этого варианта и их README.md (ведёт сам Claude)
  results.csv                 по строке на каждого Claude-игрока в каждой партии
  RULES_CONFUSION.md          где Claude путался в правилах — для человека
```

Полезное:

```
git log --oneline -- claude-opus-5-5/high/orcs.md     # как менялись заметки варианта
git log -p -3 -- claude-opus-5-5/high/orcs.md         # то же, что видит Claude перед правкой
git revert <коммит>                                   # откатить неудачный урок
```
