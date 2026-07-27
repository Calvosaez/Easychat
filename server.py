import asyncio
import json
import os
import websockets

# Guardará la conexión activa de cada usuario: { "nombre_usuario": websocket }
connected_clients = {}

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

                # Reenvío de mensajes (chat, ping, pong, add_friend, etc.) al destinatario
                else:
                    recipient = data.get("recipient")
                    if recipient and recipient in connected_clients:
                        target_ws = connected_clients[recipient]
                        await target_ws.send(json.dumps(data))

            except json.JSONDecodeError:
                pass

    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        # Al desconectarse, lo quitamos de la lista de usuarios activos
        if current_user and current_user in connected_clients:
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