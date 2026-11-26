"""Telegram Business автоответчик Лады. Python 3.10+."""
import json
import os
import sqlite3
import time
import urllib.request
import urllib.error
from pathlib import Path

TOKEN = os.environ.get("BOT_TOKEN", "")
ANSWER = "Сейчас не можем ответить на ваше сообщение. Ответим позже"
DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).parent / "data"))


class APIError(Exception):
    def __init__(self, code, retry_after=5):
        self.code, self.retry_after = code, retry_after
        super().__init__(f"Telegram API: код {code}")


def api(method, **payload):
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{TOKEN}/{method}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        try:
            result = json.loads(error.read())
        except (ValueError, OSError):
            raise APIError(error.code) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise APIError(0) from None
    if not result.get("ok"):
        raise APIError(result.get("error_code", 0), result.get("parameters", {}).get("retry_after", 5))
    return result["result"]


class Bot:
    def __init__(self, db, call=api):
        self.db, self.call = db, call
        db.executescript('''
            CREATE TABLE IF NOT EXISTS settings (connection TEXT PRIMARY KEY, enabled INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, offset INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS pauses (
                connection TEXT NOT NULL, chat INTEGER NOT NULL, until REAL NOT NULL,
                PRIMARY KEY (connection, chat));
            INSERT OR IGNORE INTO state VALUES (1, 0);
        ''')
        db.commit()

    def enabled(self, connection):
        row = self.db.execute("SELECT enabled FROM settings WHERE connection=?", (connection,)).fetchone()
        return row is None or bool(row[0])

    def handle(self, update):
        msg = update.get("business_message")
        if not msg or msg.get("chat", {}).get("type") != "private":
            return
        cid = msg["business_connection_id"]
        connection = self.call("getBusinessConnection", business_connection_id=cid)
        if not connection.get("is_enabled"):
            return
        sender = msg.get("from", {})
        if sender.get("is_bot") or msg.get("sender_business_bot"):
            return
        if sender.get("id") == connection["user"]["id"]:
            command = msg.get("text", "").strip().lower()
            if command in ("/on", "/off"):
                self.db.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (cid, int(command == "/on")))
                self.db.commit()
            if command in ("/on", "/off", "/status"):
                status = "включён" if self.enabled(cid) else "выключен"
                self.call("sendMessage", chat_id=connection["user_chat_id"], text=f"Автоответчик {status}.")
            else:
                # Срок от времени сообщения: повторная доставка не продлевает паузу.
                until = msg.get("date", 0) + 86400
                self.db.execute('''INSERT INTO pauses VALUES (?, ?, ?)
                    ON CONFLICT(connection, chat) DO UPDATE SET until=MAX(until, excluded.until)''',
                    (cid, msg["chat"]["id"], until))
                self.db.commit()
            return
        pause = self.db.execute("SELECT until FROM pauses WHERE connection=? AND chat=?",
                                (cid, msg["chat"]["id"])).fetchone()
        if pause and time.time() < pause[0]:
            return
        if not connection.get("rights", {}).get("can_reply") or not self.enabled(cid):
            return
        # Не отвечать на сообщения старше суток при первом запуске.
        if time.time() - msg.get("date", 0) >= 86400:
            return
        self.call("sendMessage", business_connection_id=cid, chat_id=msg["chat"]["id"], text=ANSWER)

    def run(self):
        while True:
            try:
                offset = self.db.execute("SELECT offset FROM state WHERE id=1").fetchone()[0]
                updates = self.call("getUpdates", offset=offset, timeout=30,
                                    allowed_updates=["business_message", "business_connection"])
                for update in updates:
                    try:
                        self.handle(update)
                    except APIError as error:
                        if error.code not in (400, 403):
                            raise
                        print(f"Обновление пропущено: код {error.code}", flush=True)
                    self.db.execute("UPDATE state SET offset=? WHERE id=1", (update["update_id"] + 1,))
                    self.db.commit()
            except APIError as error:
                print(str(error), flush=True)
                if error.code in (401, 409):
                    print("Проверьте токен, webhook и отсутствие второй копии бота.", flush=True)
                    raise SystemExit(1)
                time.sleep(max(1, min(error.retry_after, 300)))


def main():
    if not TOKEN:
        raise SystemExit("Добавьте BOT_TOKEN в Variables хостинга.")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    api("getMe")
    webhook = api("getWebhookInfo")
    if webhook.get("url"):
        raise SystemExit("У бота настроен webhook. Отключите его перед запуском polling.")
    with sqlite3.connect(DATA_DIR / "settings.db") as db:
        print("Подключение к Telegram проверено. Ожидание сообщений.", flush=True)
        Bot(db).run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Бот остановлен.")
    except APIError as error:
        print(str(error))
        raise SystemExit(1)
