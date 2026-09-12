#!/usr/bin/env python3
"""Разовая миграция данных в приватный репозиторий (data repo).

Загружает файлы из указанной папки в DATA_REPO через GitHub Contents API.
Выполняется один раз: после успешной миграции создаёт маркер `migration.done`,
и повторные запуски ничего не делают.

Использование:
  DATA_REPO=owner/repo DATA_PAT=<токен> python3 migrate_data.py /path/to/dir

Зависимости: data_store.py, стандартная библиотека.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import data_store as ds

FILES = [
    "users_state.json",
    "sends.jsonl",
    "tg_bot_state.json",
    "chat_state.json",
    "history.jsonl",
]
MARKER = "migration.done"


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "."
    repo = os.environ.get("DATA_REPO")
    token = os.environ.get("DATA_PAT")
    if not repo or not token:
        print("Нужны DATA_REPO и DATA_PAT в окружении", file=sys.stderr)
        return 2

    done, _ = ds.load_text(repo, MARKER, token, default=None)
    if done is not None:
        print(f"Миграция уже выполнялась (есть {MARKER}) — выход.")
        return 0

    migrated = 0
    for name in FILES:
        path = os.path.join(src, name)
        if not os.path.exists(path):
            print(f"{name}: нет файла — пропуск")
            continue
        with open(path, encoding="utf-8") as f:
            text = f.read()
        if not text.strip():
            print(f"{name}: пусто — пропуск")
            continue
        try:
            ds.save_text(repo, name, token, text, None, message=f"миграция {name}")
            print(f"{name}: загружен ({len(text)} байт)")
            migrated += 1
        except Exception as e:
            print(f"{name}: ОШИБКА {e}")

    try:
        ds.save_text(repo, MARKER, token, "ok", None, message="миграция завершена")
        print(f"Готово. Перенесено файлов: {migrated}. Создан {MARKER}.")
    except Exception as e:
        print(f"Не удалось создать маркер: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
