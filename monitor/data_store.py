#!/usr/bin/env python3
"""Хранение данных в приватном GitHub-репозитории (GitHub Contents API).

Позволяет читать/писать JSON-файл в отдельном приватном репозитории по PAT
(без сторонних БД). Используется для users.json — чтобы координаты и ntfy-темы
пользователей не попадали в публичный репозиторий.

Использование:
  import data_store as ds
  data, sha = ds.load_json(repo, "users.json", token)
  new_sha = ds.save_json(repo, "users.json", token, data, sha, message="update")

Зависимости: только стандартная библиотека Python 3.
"""

import base64
import json
import urllib.error
import urllib.request

GH_API = "https://api.github.com"


def _gh(method, url, token, body=None, timeout=30):
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8")[:400]
        except Exception:
            pass
        raise RuntimeError(f"GitHub API {e.code} {e.reason}: {detail}") from None


def load_json(repo, path, token, default=None):
    """Возвращает (data, sha). Если файла нет — (default, None)."""
    url = f"{GH_API}/repos/{repo}/contents/{path}"
    try:
        resp = _gh("GET", url, token)
    except RuntimeError as e:
        msg = str(e)
        if "404" in msg:
            return (default if default is not None else {}), None
        raise
    raw = resp.get("content", "")
    text = base64.b64decode(raw.replace("\n", "")).decode("utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = default if default is not None else {}
    return data, resp.get("sha")


def save_json(repo, path, token, data, sha=None, message="update"):
    """Создаёт или обновляет файл. Возвращает новый sha."""
    text = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    content = base64.b64encode(text).decode("ascii")
    body = {"message": message, "content": content}
    if sha:
        body["sha"] = sha
    url = f"{GH_API}/repos/{repo}/contents/{path}"
    resp = _gh("PUT", url, token, body)
    return resp.get("content", {}).get("sha")
