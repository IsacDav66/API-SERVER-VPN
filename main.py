from fastapi import FastAPI, Request
from pydantic import BaseModel
from typing import Dict, List
from uuid import uuid4
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Cambiar según sea necesario
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Estructuras en memoria (reemplazables por una base de datos)
users = {}  # Almacena usuarios registrados
rooms = {}  # Almacena salas activas


# Modelos
class User(BaseModel):
    username: str


class Room(BaseModel):
    room_id: str
    host_id: str
    participants: List[str]


# Ruta: Registrar usuario (detectando IP automáticamente)
@app.post("/register")
async def register_user(user: User, request: Request):
    client_ip = request.client.host  # Detectar IP automáticamente
    user_id = str(uuid4())  # Generar un ID único para el usuario
    users[user_id] = {"username": user.username, "ip": client_ip}
    return {"user_id": user_id, "username": user.username, "ip": client_ip}


# Ruta: Crear sala
@app.post("/create-room")
async def create_room(user_id: str):
    if user_id not in users:
        return {"error": "Usuario no registrado"}
    
    # Verificar si el usuario ya tiene una sala activa
    for room_id, room in rooms.items():
        if room["host_id"] == user_id:
            return {"error": "El usuario ya tiene una sala activa", "room_id": room_id}
    
    room_id = str(uuid4())
    rooms[room_id] = {
        "host_id": user_id,
        "participants": [user_id],
    }
    return {"room_id": room_id, "host": users[user_id], "participants": rooms[room_id]["participants"]}


# Ruta: Unirse a sala
@app.post("/join-room")
async def join_room(room_id: str, user_id: str):
    if room_id not in rooms:
        return {"error": "Sala no encontrada"}
    if user_id not in users:
        return {"error": "Usuario no registrado"}
    
    rooms[room_id]["participants"].append(user_id)
    return {"room_id": room_id, "participants": rooms[room_id]["participants"]}


# Ruta: Consultar salas activas
@app.get("/rooms")
async def get_rooms():
    return [{"room_id": room_id, "participants": len(data["participants"])} for room_id, data in rooms.items()]


# Ruta: Consultar participantes de una sala
@app.get("/rooms/{room_id}")
async def get_room_details(room_id: str):
    if room_id not in rooms:
        return {"error": "Sala no encontrada"}
    return {"participants": [users[uid] for uid in rooms[room_id]["participants"]]}
