from fastapi import FastAPI, Request
from pydantic import BaseModel
from typing import Dict, List
from uuid import uuid4
from fastapi.middleware.cors import CORSMiddleware
import subprocess
import os
import platform
from fastapi import HTTPException
from fastapi.responses import JSONResponse
import shutil
from typing import Optional
import uuid
from tempfile import NamedTemporaryFile


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
# Directorio de configuraciones OpenVPN
OPEN_VPN_DIR = os.environ.get("OPEN_VPN_DIR", "/tmp/openvpn_rooms")

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


# Ruta: Crear una sala
class CreateRoomRequest(BaseModel):
    user_id: str

@app.post("/create-room")
async def create_room(create_request: CreateRoomRequest):
    user_id = create_request.user_id
    if user_id not in users:
            return {"error": "Usuario no registrado"}
    
    room_id = str(uuid4())
    rooms[room_id] = {
            "host_id": user_id,
            "participants": [user_id],
        }
    
    # Llamar a OpenVPN para crear una red virtual
    try:
        create_virtual_network(room_id)
        return {"room_id": room_id, "host": users[user_id], "participants": rooms[room_id]["participants"]}
    except Exception as e:
        return {"error": f"Error al crear la red virtual: {str(e)}"}


# Función para crear una red virtual usando OpenVPN
def create_virtual_network(room_id: str):
    
    os.makedirs(OPEN_VPN_DIR, exist_ok=True)
    config_dir = os.path.join(OPEN_VPN_DIR, room_id)
    os.makedirs(config_dir, exist_ok=True)
    
    config_file = os.path.join(config_dir, "server.conf")
    with open(config_file, "w") as f:
        f.write(f"""dev tun
proto udp
port 1194
ca ca.crt
cert server.crt
key server.key
dh dh2048.pem
server 10.8.0.0 255.255.255.0
ifconfig-pool-persist ipp.txt
push "redirect-gateway def1 bypass-dhcp"
push "dhcp-option DNS 8.8.8.8"
keepalive 10 120
comp-lzo
user nobody
group nobody
persist-key
persist-tun
status openvpn-status.log
log-append /var/log/openvpn.log
verb 3
        """)


# Ruta: Unirse a una sala
class JoinRoomRequest(BaseModel):
    room_id: str
    user_id: str

@app.post("/join-room")
async def join_room(request: JoinRoomRequest):
    room_id = request.room_id
    user_id = request.user_id
    
    if room_id not in rooms:
        return {"error": "Sala no encontrada"}
    if user_id not in users:
        return {"error": "Usuario no registrado"}
    
    # Verificar si el usuario ya está en la sala
    if user_id in rooms[room_id]["participants"]:
        return {"error": "Ya estás en esta sala"}
    
    rooms[room_id]["participants"].append(user_id)
    
    # Llamar a OpenVPN para conectar al usuario a la red virtual
    try:
        config = await get_client_config(room_id, user_id)
        return {"room_id": room_id, "participants": rooms[room_id]["participants"], "ovpn_config": config["ovpn_config"], "cert_path": config["cert_path"], "key_path": config["key_path"]}

    except Exception as e:
        return {"error": f"Error al conectar a la red virtual: {str(e)}"}

# Función para obtener la configuración del cliente OpenVPN
async def get_client_config(room_id: str, user_id: str):
    # ruta del archivo de configuración en el servidor
    user_config_dir = os.path.join(OPEN_VPN_DIR, room_id, user_id)
    os.makedirs(user_config_dir, exist_ok=True)
    config_file = os.path.join(user_config_dir, "client.ovpn")
    cert_file = os.path.join(user_config_dir, f"{user_id}-cert.crt")
    key_file = os.path.join(user_config_dir, f"{user_id}-key.key")
    with open(config_file, "w") as f:
       f.write(f"""
client
dev tun
proto udp
remote 18.119.122.250 1194  # Cambia por la IP publica de tu EC2
resolv-retry infinite
nobind
persist-key
persist-tun
ca ca.crt
cert {user_id}-cert.crt
key {user_id}-key.key
remote-cert-tls server
comp-lzo
verb 3
    """)
    
    # Genera un certificado ficticio
    with open(cert_file, 'w') as f:
      f.write(f"ESTO ES UN CERTIFICADO DE PRUEBA")
    # Genera una clave ficticia
    with open(key_file, 'w') as f:
      f.write(f"ESTO ES UNA CLAVE PRIVADA DE PRUEBA")
    

    #  Lee el contenido del archivo config, y lo devuelve al frontend.
    with open(config_file, 'r') as f:
        config_content = f.read()
    
    return {"ovpn_config":config_content,"cert_path":cert_file, "key_path":key_file}

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


# Ruta: Salir de una sala
class LeaveRoomRequest(BaseModel):
    room_id: str
    user_id: str

@app.post("/leave-room")
async def leave_room(request: LeaveRoomRequest):
    room_id = request.room_id
    user_id = request.user_id
    
    if room_id not in rooms:
        return {"error": "Sala no encontrada"}
    if user_id not in users:
        return {"error": "Usuario no registrado"}
    if user_id not in rooms[room_id]["participants"]:
        return {"error": "El usuario no está en esta sala"}
    
    rooms[room_id]["participants"].remove(user_id)  # Eliminar al usuario de la sala
    
    # Si la sala se queda sin participantes, podemos eliminar la sala
    if not rooms[room_id]["participants"]:
        del rooms[room_id]
    
    return {"room_id": room_id, "participants": rooms[room_id]["participants"] if room_id in rooms else []}