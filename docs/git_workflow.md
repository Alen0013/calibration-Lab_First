# Git и порядок изменений

## Назначение веток

| Ветка | Назначение |
| --- | --- |
| `main` | Принятая версия лабораторной работы |
| `develop` | Ветка интеграции проверенных изменений |
| `feature/lab1-eda` | Исходный код анализа и демонстрации калибровки |
| `feature/lab1-quality` | Pre-commit, негативные контроли и CI |
| `feature/lab1-app` | Локальное приложение и материалы сдачи |

Имена веток описывают предлагаемый рабочий процесс. Фактически существующие ветки
проверяются командой `git branch --all`, история — командой
`git log --graph --oneline --decorate --all`.

Создавайте отдельный коммит на законченное изменение. После локальных проверок
открывайте pull request в `develop`, затем — из `develop` в `main`. Шаблон PR
находится в `.github/pull_request_template.md`. После ревью и зелёной проверки
`quality` сливайте PR. Не обходите защиту ветки ради демонстрации.

## Локальная установка проверок

Из корня репозитория в активированном окружении:

```bash
python -m pip install -e ".[dev]"
python -m pre_commit install
python -m pre_commit run --all-files
python -m pytest -q
python scripts/negative_controls.py
python -m calibration_lab.cli verify --config configs/lab1.yaml
python -m calibration_lab.cli tamper --config configs/lab1.yaml
python -m calibration_lab.cli reproduce --config configs/lab1.yaml
```

Все хуки объявлены как `repo: local` и `language: system`: после установки
зависимостей им не нужно скачивать отдельные репозитории. Окружение должно быть
активировано, чтобы команда `python` видела `ruff`, `PyYAML` и пакет проекта.

Ruff проверяет стиль и формат Python. `scripts/check_staged.py` читает именно
индекс Git, обнаруживает распространённые форматы токенов и приватных ключей,
запрещает файлы размером больше 1 МиБ. Это учебная проверка известных шаблонов,
а не гарантия обнаружения любого возможного секрета. Быстрый хук проверяет
синтаксис Python, JSON, YAML и оставленные маркеры конфликтов.

`scripts/negative_controls.py` создаёт временный репозиторий, устанавливает в нём
настоящий хук и выполняет три команды `git commit`: с ошибкой стиля, вымышленным
токеном и бинарным файлом 1 МиБ + 1 байт. Контроль считается успешным только при
ненулевом коде команды, неизменном HEAD и сообщении ожидаемого блокировщика.
Исходный репозиторий и его индекс не изменяются. Свидетельства находятся в
`reports/LAB1/precommit_block.log` и `precommit_controls.json`.

Команда `tamper` отдельно изменяет копию сырых данных во временной папке и
проверяет отказ настоящего валидатора хешей. Оригинал остаётся неизменным,
результат записывается в `reports/LAB1/hash_tamper.log` и `hash_tamper.json`.

## Публикация на GitHub

Публикуйте проект только в выбранный вами репозиторий. Пароль или токен не нужно
вставлять в код, README, чат или URL удалённого репозитория. Используйте обычную
авторизацию GitHub CLI или подключение GitHub.

Если удалённый репозиторий пустой, после авторизации выполните следующие команды,
заменив `OWNER/REPOSITORY` своим адресом:

```bash
git remote add origin https://github.com/OWNER/REPOSITORY.git
git push -u origin --all
git push origin --tags
```

Если `origin` уже настроен, сначала проверьте `git remote -v`. Не перезаписывайте
чужую историю: при непустом удалённом репозитории сначала изучите его ветки.

## Защита main

Файл `.github/branch_protection.json` — подготовленная конфигурация, сам по себе
он не включает защиту GitHub. Она настраивается после создания репозитория и
первого выполнения CI. Команды GitHub CLI:

```bash
gh auth status
gh api --method PUT repos/OWNER/REPOSITORY/branches/main/protection --input .github/branch_protection.json
gh api repos/OWNER/REPOSITORY/branches/main/protection
gh run list --workflow ci.yml
```

Настройки требуют PR, успешную проверку `quality`, актуальность ветки относительно
`main`, разрешённые обсуждения и одно одобрение другого участника. Force push и
удаление главной ветки запрещены. Автор не может одобрить собственный PR; нужен
реальный рецензент. Если аккаунт или тип репозитория не поддерживает защиту,
команда завершится ошибкой: такую ситуацию надо явно отразить в отчёте, не
выдавать JSON за действующую защиту.

Эквивалент в интерфейсе: Settings → Branches → Add branch protection rule →
`main`; включить pull request, одно approval, status check `quality`, актуальность
ветки и запрет обхода правил. Конкретный вид страницы зависит от GitHub.

## Проверяемое состояние сдачи

До подключения удалённого репозитория доказаны только локальные проверки.
Факт защиты `main`, ссылка на PR, ревью другим человеком и успешный прогон GitHub
Actions подтверждаются удалёнными результатами отдельно. Сохраните ссылку на
Actions run и результат чтения protection в отчёте после публикации.

Официальная документация:

- https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches
- https://docs.github.com/en/rest/branches/branch-protection#update-branch-protection
- https://pre-commit.com/#repository-local-hooks
