import asyncio
import json
import os
import sqlite3
import urllib.error
import urllib.request
from http import HTTPStatus

import websockets


connected_clients = {}

ACK_TYPES = {
    "chat_ack", "audio_request_ack", "audio_reply_ack", "read_receipt_ack",
    "status_ack", "group_ack", "friend_ack",
}


def pending_key(data):
    msg_type = data.get("type")
    sender = str(data.get("sender") or "")
    msg_id = str(data.get("msg_id") or "")
    group_id = str(data.get("group_id") or "")
    if msg_type == "add_friend":
        return f"add_friend:{sender}" if sender else None
    if msg_type == "friend_ack":
        return f"friend_ack:{sender}:{msg_id}" if sender else None
    if not msg_id:
        return None
    if msg_type == "chat_msg":
        return msg_id
    if msg_type == "chat_ack":
        return f"chat_ack:{sender}:{msg_id}"
    if msg_type == "audio_request":
        return f"audio_request:{msg_id}"
    if msg_type == "audio_request_ack":
        return f"audio_request_ack:{sender}:{msg_id}"
    if msg_type == "audio_reply":
        return f"audio_reply:{msg_id}"
    if msg_type == "audio_reply_ack":
        return f"audio_reply_ack:{sender}:{msg_id}"
    if msg_type == "read_receipt":
        return f"read_receipt:{msg_id}"
    if msg_type == "read_receipt_ack":
        return f"read_receipt_ack:{sender}:{msg_id}"
    if msg_type in ("friend_removed", "account_deleted"):
        return f"status:{msg_id}"
    if msg_type == "status_ack":
        return f"status_ack:{sender}:{msg_id}"
    if msg_type == "group_ack":
        return f"group_ack:{sender}:{data.get('ack_type', '')}:{group_id}:{msg_id}"
    if msg_type in (
        "group_chat_msg", "group_update", "group_deleted", "group_read_receipt",
        "group_api_sync", "group_translation", "group_audio_reply"
    ):
        return f"{msg_type}:{group_id}:{msg_id}"
    return None


class PendingStore:
    """Persistent inbox via the PHP API hosted alongside x10hosting MySQL."""

    def __init__(self):
        self.api_url = os.environ.get("X10_QUEUE_URL", "").strip()
        self.api_token = os.environ.get("X10_QUEUE_TOKEN", "").strip()
        self.sqlite_path = os.environ.get("PENDING_DB_PATH", "pending_messages.sqlite3")
        self.sqlite = None
        self.lock = asyncio.Lock()

    @property
    def remote(self):
        return bool(self.api_url and self.api_token)

    def _api_request_sync(self, body):
        request = urllib.request.Request(
            self.api_url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-EasyChat-Queue-Token": self.api_token,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as error:
            raise RuntimeError(f"No se pudo acceder a la cola de x10hosting: {error}") from error
        if not isinstance(data, dict) or not data.get("success"):
            raise RuntimeError(f"La cola de x10hosting devolvio un error: {data}")
        return data

    async def _api(self, body):
        return await asyncio.to_thread(self._api_request_sync, body)

    async def open(self):
        if self.remote:
            await self._api({"action": "init"})
            print("[store] Cola persistente conectada a MySQL de x10hosting")
            return
        self.sqlite = sqlite3.connect(self.sqlite_path, check_same_thread=False)
        self.sqlite.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_messages (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                recipient TEXT NOT NULL,
                message_key TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (recipient, message_key)
            )
            """
        )
        self.sqlite.commit()
        print("[store] AVISO: configura X10_QUEUE_URL y X10_QUEUE_TOKEN para persistencia real en Render.")

    async def close(self):
        if self.sqlite is not None:
            self.sqlite.close()
            self.sqlite = None

    async def put(self, recipient, data):
        key = pending_key(data)
        if not recipient or not key:
            return
        async with self.lock:
            if self.remote:
                await self._api({"action": "put", "recipient": recipient, "message_key": key, "payload": data})
                return
            self.sqlite.execute(
                """
                INSERT INTO pending_messages (recipient, message_key, payload)
                VALUES (?, ?, ?)
                ON CONFLICT(recipient, message_key) DO UPDATE SET payload = excluded.payload
                """,
                (recipient, key, json.dumps(data, ensure_ascii=False)),
            )
            self.sqlite.commit()

    async def list_for(self, recipient):
        async with self.lock:
            if self.remote:
                result = await self._api({"action": "list", "recipient": recipient})
                return [(str(row["message_key"]), row["payload"]) for row in result.get("messages", [])]
            rows = self.sqlite.execute(
                "SELECT message_key, payload FROM pending_messages WHERE recipient = ? ORDER BY sequence",
                (recipient,),
            ).fetchall()
            return [(key, json.loads(payload)) for key, payload in rows]

    async def delete(self, recipient, key):
        if not recipient or not key:
            return
        async with self.lock:
            if self.remote:
                await self._api({"action": "delete", "recipient": recipient, "message_key": key})
                return
            self.sqlite.execute(
                "DELETE FROM pending_messages WHERE recipient = ? AND message_key = ?",
                (recipient, key),
            )
            self.sqlite.commit()


pending_store = PendingStore()


async def deliver_pending(username, websocket):
    for key, data in await pending_store.list_for(username):
        await websocket.send(json.dumps(data, ensure_ascii=False))
        if data.get("type") in ACK_TYPES:
            await pending_store.delete(username, key)


async def remove_original_pending(data):
    msg_type = data.get("type")
    original_recipient = data.get("sender")
    original_sender = data.get("recipient")
    msg_id = data.get("msg_id")
    if msg_type == "friend_ack":
        if original_recipient and original_sender:
            await pending_store.delete(original_recipient, f"add_friend:{original_sender}")
        return
    if not msg_id or not original_recipient:
        return
    if msg_type == "chat_ack": key = str(msg_id)
    elif msg_type == "audio_request_ack": key = f"audio_request:{msg_id}"
    elif msg_type == "audio_reply_ack": key = f"audio_reply:{msg_id}"
    elif msg_type == "read_receipt_ack": key = f"read_receipt:{msg_id}"
    elif msg_type == "status_ack": key = f"status:{msg_id}"
    elif msg_type == "group_ack": key = f"{data.get('ack_type', '')}:{data.get('group_id', '')}:{msg_id}"
    else: return
    await pending_store.delete(original_recipient, key)


async def relay_or_queue(recipient, data):
    if not recipient:
        return
    key = pending_key(data)
    if key:
        await pending_store.put(recipient, data)
    target_ws = connected_clients.get(recipient)
    if target_ws is not None:
        try:
            await target_ws.send(json.dumps(data, ensure_ascii=False))
            if data.get("type") in ACK_TYPES and key:
                await pending_store.delete(recipient, key)
        except websockets.exceptions.ConnectionClosed:
            if connected_clients.get(recipient) is target_ws:
                del connected_clients[recipient]


async def handler(websocket):
    current_user = None
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                continue
            msg_type = data.get("type")
            if msg_type == "register":
                username = data.get("username")
                if username:
                    current_user = username
                    connected_clients[username] = websocket
                    await websocket.send(json.dumps({"type": "registered", "status": "ok"}))
                    await deliver_pending(username, websocket)
                continue
            if msg_type == "connection_ping":
                await websocket.send(json.dumps({"type": "connection_pong"}))
                continue
            if msg_type in ACK_TYPES:
                await remove_original_pending(data)
            await relay_or_queue(data.get("recipient"), data)
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        if current_user and connected_clients.get(current_user) is websocket:
            del connected_clients[current_user]


def process_request(path, _request_headers):
    filename = {"/Easychat_version.txt": "Easychat_version.txt", "/Easychat.apk": "Easychat.apk"}.get(path)
    if not filename:
        return None
    server_dir = os.path.dirname(__file__)
    candidates = (os.path.join(server_dir, filename), os.path.join(os.path.dirname(server_dir), filename))
    file_path = next((candidate for candidate in candidates if os.path.isfile(candidate)), None)
    if not file_path:
        return HTTPStatus.NOT_FOUND, [("Content-Type", "text/plain")], b"Not found"
    content_type = "text/plain; charset=utf-8" if filename.endswith(".txt") else "application/vnd.android.package-archive"
    with open(file_path, "rb") as file:
        return HTTPStatus.OK, [("Content-Type", content_type), ("Cache-Control", "no-store")], file.read()


async def main():
    await pending_store.open()
    try:
        async with websockets.serve(handler, "0.0.0.0", int(os.environ.get("PORT", 8080)), process_request=process_request):
            await asyncio.Future()
    finally:
        await pending_store.close()


if __name__ == "__main__":
    asyncio.run(main())
