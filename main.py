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
import base64


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
        return {"room_id": room_id, "participants": rooms[room_id]["participants"], "ovpn_config": config["ovpn_config"], "cert_content": config["cert_content"], "key_content": config["key_content"]}

    except Exception as e:
        return {"error": f"Error al conectar a la red virtual: {str(e)}"}


#  Genera los certificados para un usuario dado.
def generate_client_certs(room_id, user_id):
        user_config_dir = os.path.join(OPEN_VPN_DIR, room_id, user_id)
        os.makedirs(user_config_dir, exist_ok=True)
        cert_file = os.path.join(user_config_dir, f"{user_id}-cert.crt")
        key_file = os.path.join(user_config_dir, f"{user_id}-key.key")
        #  Obtenemos la ruta del ca.crt
        ca_path = os.path.join(OPEN_VPN_DIR,"ca.crt")
        ca_key_path = os.path.join(OPEN_VPN_DIR,"./demoCA/private/cakey.pem")
        #  Creamos un archivo de configuracion temporal de openssl.
        with NamedTemporaryFile(mode="w", delete=False) as conf_file:
            conf_file.write(f"""
[ ca ]

[ CA_default ]
dir               = .
certs             = $dir
crl_dir           = $dir
database          = $dir/index.txt
new_certs_dir     = $dir
unique_subject    = no
certificate       = {ca_path}
serial            = {user_config_dir}/serial
crlnumber         = $dir/crlnumber
default_days      = 3650
default_md        = sha256
policy            = policy_anything

[ policy_anything ]
countryName             = optional
stateOrProvinceName     = optional
localityName            = optional
organizationName        = optional
organizationalUnitName  = optional
commonName              = supplied
emailAddress            = optional
""")
            conf_file_path = conf_file.name
            try:
                # Ejecutar comandos OpenSSL para generar certificado y clave del cliente
                subprocess.run(
                    [
                        "openssl",
                        "req",
                        "-new",
                        "-newkey",
                        "rsa:2048",
                        "-nodes",
                        "-keyout",
                        key_file,
                        "-out",
                        f"{user_id}.csr", #  Se genera un .csr, pero lo borramos justo despues
                        "-subj",
                        f"/CN={user_id}",
                    ],
                    cwd = user_config_dir,
                    check=True,
                )
                subprocess.run(
                    [
                        "openssl",
                        "ca",
                        "-config",
                        conf_file_path,
                        "-keyfile",
                        ca_key_path,  #  Especificamos la ruta de la clave de la CA
                        "-cert",
                        ca_path, #  Especificamos la ruta del certificado de la CA
                        "-in",
                        f"{user_id}.csr",
                        "-out",
                        cert_file,
                        "-days",
                        "3650",
                        "-batch" # para no tener que darle a "Y" todo el rato.
                    ],
                    cwd = user_config_dir,
                    check=True,
                )
            except subprocess.CalledProcessError as e:
                raise Exception(f"Error al generar certificados: {e}")
            finally:
                #  Borrar el csr porque no nos hace falta.
                csr_file = os.path.join(user_config_dir, f"{user_id}.csr")
                if os.path.exists(csr_file):
                        os.remove(csr_file)
                # Eliminar el archivo de configuración temporal.
                os.remove(conf_file_path)

        with open(cert_file, 'r') as f:
            cert_content = f.read()
        with open(key_file, 'r') as f:
            key_content = f.read()

        return {"cert_content":cert_content, "key_content":key_content}

# Función para obtener la configuración del cliente OpenVPN
async def get_client_config(room_id: str, user_id: str):
    # ruta del archivo de configuración en el servidor
    user_config_dir = os.path.join(OPEN_VPN_DIR, room_id, user_id)
    os.makedirs(user_config_dir, exist_ok=True)
    config_file = os.path.join(user_config_dir, "client.ovpn")
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
remote-cert-tls server
comp-lzo
verb 3
    """)
    certs = generate_client_certs(room_id,user_id)
    with open(config_file, 'r') as f:
        config_content = f.read()

    return {"ovpn_config":config_content,"cert_content":certs["cert_content"], "key_content":certs["key_content"]}


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