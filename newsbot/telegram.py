"""Envoi des messages via l'API Bot de Telegram."""
import html
import logging
import time

import requests

log = logging.getLogger(__name__)
API = "https://api.telegram.org/bot{token}/{method}"
LIMIT = 4000  # Telegram coupe à 4096 caractères


class Telegram:
    def __init__(self, token, chat_id, dry_run=False):
        self.token = token
        self.chat_id = chat_id
        self.dry_run = dry_run or not token

    def call(self, method, **params):
        if self.dry_run:
            print(f"--- [{method}] ---\n{params.get('text', params)}\n")
            return {}
        for attempt in range(4):
            resp = requests.post(API.format(token=self.token, method=method), json=params, timeout=30)
            data = resp.json()
            if data.get("ok"):
                return data["result"]
            retry = data.get("parameters", {}).get("retry_after")
            if retry and attempt < 3:
                time.sleep(retry + 1)
                continue
            raise RuntimeError(f"Telegram {method} : {data.get('description')}")

    def send(self, text, topic=None, silent=False):
        for chunk in split(text):
            params = dict(chat_id=self.chat_id, text=chunk, parse_mode="HTML",
                          disable_notification=silent,
                          link_preview_options={"is_disabled": True})
            if topic:
                params["message_thread_id"] = topic
            self.call("sendMessage", **params)

    def is_forum(self):
        if self.dry_run:
            return False
        return bool(self.call("getChat", chat_id=self.chat_id).get("is_forum"))

    def create_topic(self, name, color):
        return self.call("createForumTopic", chat_id=self.chat_id, name=name, icon_color=color)["message_thread_id"]


def split(text):
    """Découpe sur les sauts de ligne pour rester sous la limite de Telegram."""
    chunks, current = [], ""
    for block in text.split("\n\n"):
        if current and len(current) + len(block) + 2 > LIMIT:
            chunks.append(current)
            current = ""
        current = f"{current}\n\n{block}" if current else block
    if current:
        chunks.append(current)
    return [c[:LIMIT] for c in chunks]


def esc(text):
    return html.escape(text or "", quote=False)
