import asyncio
import json
import os
import websockets

# Guardará la conexión activa de cada usuario: { "nombre_usuario": websocket }
connected_clients = {}
pending_messages = {}


def pending_key(data):
    msg_id = data.get("msg_id")
    if not msg_id:
        return None
    msg_type = data.get("type")
    if msg_type == "chat_msg":
        return msg_id
    if msg_type == "audio_request":
        return f"audio_request:{msg_id}"
    if msg_type in ("friend_removed", "account_deleted"):
        return f"status:{msg_id}"
    if msg_type in ("group_chat_msg", "group_update", "group_deleted"):
        return f"{msg_type}:{data.get('group_id', '')}:{msg_id}"
    return None


def queue_pending(recipient, data):
    key = pending_key(data)
    if recipient and key:
        pending_messages.setdefault(recipient, {})[key] = data


async def deliver_pending(username, websocket):
    queued = pending_messages.get(username, {})
    for data in list(queued.values()):
        await websocket.send(json.dumps(data))

async def handler(websocket):
    current_user = None
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                msg_type = data.get("type")

                # Registro de usuario al abrir sesión
                if msg_type == "register":
                    username = data.get("username")
                    if username:
                        current_user = username
                        connected_clients[username] = websocket
                        print(f"[+] Usuario en línea: {username}")
                        await websocket.send(json.dumps({"type": "registered", "status": "ok"}))
                        await deliver_pending(username, websocket)

                elif msg_type == "connection_ping":
                    await websocket.send(json.dumps({"type": "connection_pong"}))

                # Reenvío de mensajes (chat, ping, pong, add_friend, etc.) al destinatario
                else:
                    recipient = data.get("recipient")
                    if msg_type in ("chat_ack", "audio_request_ack", "status_ack", "group_ack"):
                        msg_id = data.get("msg_id")
                        original_recipient = data.get("sender")
                        if msg_id and original_recipient in pending_messages:
                            if msg_type == "chat_ack":
                                key = msg_id
                            elif msg_type == "audio_request_ack":
                                key = f"audio_request:{msg_id}"
                            elif msg_type == "status_ack":
                                key = f"status:{msg_id}"
                            else:
                                key = (
                                    f"{data.get('ack_type', '')}:"
                                    f"{data.get('group_id', '')}:{msg_id}"
                                )
                            pending_messages[original_recipient].pop(key, None)
                            if not pending_messages[original_recipient]:
                                del pending_messages[original_recipient]

                    if recipient and recipient in connected_clients:
                        target_ws = connected_clients[recipient]
                        try:
                            await target_ws.send(json.dumps(data))
                        except websockets.exceptions.ConnectionClosed:
                            if connected_clients.get(recipient) is target_ws:
                                del connected_clients[recipient]
                            queue_pending(recipient, data)
                    else:
                        queue_pending(recipient, data)

            except json.JSONDecodeError:
                pass

    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        # Al desconectarse, lo quitamos de la lista de usuarios activos
        if current_user and connected_clients.get(current_user) is websocket:
            del connected_clients[current_user]
            print(f"[-] Usuario desconectado: {current_user}")

async def main():
    # Render asigna automáticamente el puerto a través de la variable PORT
    port = int(os.environ.get("PORT", 8080))
    async with websockets.serve(handler, "0.0.0.0", port):
        print(f"Servidor corriendo en el puerto {port}")
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
