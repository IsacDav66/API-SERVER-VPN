from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
from typing import Dict, List
from uuid import uuid4
from fastapi.middleware.cors import CORSMiddleware
import subprocess
import os
import platform
from fastapi.responses import JSONResponse
import shutil
from typing import Optional
import uuid
from tempfile import NamedTemporaryFile
import base64
import jinja2
from ipaddress import ip_address, IPv4Address
import logging
import socket

app = FastAPI()

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Define tus orígenes permitidos
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuración de logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Estructuras en memoria (reemplazables por una base de datos)
users = {}  # Almacena usuarios registrados
rooms = {}  # Almacena salas activas
# Directorio de configuraciones OpenVPN
OPEN_VPN_DIR = os.environ.get("OPEN_VPN_DIR", "/tmp/openvpn_rooms")
SERVER_IP = socket.gethostbyname(socket.gethostname())


# Modelo para los templates
template_loader = jinja2.FileSystemLoader(searchpath="./templates")
template_env = jinja2.Environment(loader=template_loader)

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
    client_ip = request.client.host
    try:
        ip_address(client_ip)
        if not isinstance(ip_address(client_ip), IPv4Address):
            raise ValueError("La IP debe ser una IPv4")
    except ValueError:
        raise HTTPException(status_code=400, detail="La IP del cliente no es válida")
    user_id = str(uuid4())
    users[user_id] = {"username": user.username, "ip": client_ip}
    logging.info(f"User registered: user_id={user_id}, username={user.username}, ip={client_ip}")
    return {"user_id": user_id, "username": user.username, "ip": client_ip}

# Ruta: Crear una sala
class CreateRoomRequest(BaseModel):
    user_id: str

@app.post("/create-room")
async def create_room(create_request: CreateRoomRequest):
    user_id = create_request.user_id
    if user_id not in users:
        raise HTTPException(status_code=404, detail="Usuario no registrado")
    
    room_id = str(uuid4())
    rooms[room_id] = {
        "host_id": user_id,
        "participants": [user_id],
        "process": None
    }
    
    try:
        create_virtual_network(room_id)
        logging.info(f"Room created: room_id={room_id}, host={user_id}")
        return {"room_id": room_id, "host": users[user_id], "participants": rooms[room_id]["participants"]}
    except Exception as e:
       logging.error(f"Error creating room {room_id}: {e}")
       raise HTTPException(status_code=500, detail=f"Error al crear la red virtual: {str(e)}")


# Función para crear una red virtual usando OpenVPN
def create_virtual_network(room_id: str):
    try:
        os.makedirs(OPEN_VPN_DIR, exist_ok=True)
        config_dir = os.path.join(OPEN_VPN_DIR, room_id)
        os.makedirs(config_dir, exist_ok=True)

        config_file = os.path.join(config_dir, "server.conf")

        dh_file = os.path.join(config_dir, "dh.pem")
        subprocess.run([
            "openssl",
            "dhparam",
            "-out",
            dh_file,
            "2048"
        ], check = True)

        server_template = template_env.get_template("server.conf.j2")
        rendered_config = server_template.render(dh_file=dh_file)
        with open(config_file, "w") as f:
            f.write(rendered_config)
        
        # Iniciar OpenVPN en modo daemon
        process = subprocess.Popen(["openvpn", "--config", config_file], 
                                   stdout=subprocess.PIPE, 
                                   stderr=subprocess.PIPE, 
                                   text=True)
        rooms[room_id]["process"] = process
    
    except Exception as e:
        logging.error(f"Error creating virtual network {room_id}: {e}")
        raise Exception(f"Error al crear la red virtual: {e}")

# Ruta: Unirse a una salaa
class JoinRoomRequest(BaseModel):
    room_id: str
    user_id: str

@app.post("/join-room")
async def join_room(request: JoinRoomRequest):
    room_id = request.room_id
    user_id = request.user_id
    if room_id not in rooms:
        raise HTTPException(status_code=404, detail="Sala no encontrada")
    if user_id not in users:
        raise HTTPException(status_code=404, detail="Usuario no registrado")

    # Verificar si el usuario ya está en la sala
    if user_id in rooms[room_id]["participants"]:
        raise HTTPException(status_code=409, detail="Ya estás en esta sala")
    
    rooms[room_id]["participants"].append(user_id)
    # Llamar a OpenVPN para conectar al usuario a la red virtual
    try:
        config = await get_client_config(room_id, user_id)
        logging.info(f"User {user_id} joined room {room_id}")
        return {"room_id": room_id, "participants": rooms[room_id]["participants"], "ovpn_config": config["ovpn_config"]}
    except Exception as e:
        logging.error(f"Error connecting user {user_id} to room {room_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error al conectar a la red virtual: {str(e)}")


#  Genera los certificados para un usuario dado.
def generate_client_certs(room_id, user_id):
    user_config_dir = os.path.join(OPEN_VPN_DIR, room_id, user_id)
    os.makedirs(user_config_dir, exist_ok=True)
    cert_file = os.path.join(user_config_dir, f"{user_id}-cert.crt")
    key_file = os.path.join(user_config_dir, f"{user_id}-key.key")
    #  Obtenemos la ruta del ca.crt
    ca_path = os.path.join(OPEN_VPN_DIR,"ca.crt")
    ca_key_path = os.path.join(OPEN_VPN_DIR,"demoCA/private/ca.key")
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
                f"/CN={user_id}/C=US/ST=ExampleState/O=ExampleOrg",
            ],
            cwd = user_config_dir,
            check=True,
        )
        subprocess.run(
            [
                "openssl",
                "ca",
                "-config",
                 "/usr/lib/ssl/openssl.cnf",
                "-keyfile",
                ca_key_path,
                "-cert",
                ca_path,
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
        logging.error(f"Error al generar certificados: {e}")
        raise Exception(f"Error al generar certificados: {e}")
    finally:
        #  Borrar el csr porque no nos hace falta.
        csr_file = os.path.join(user_config_dir, f"{user_id}.csr")
        if os.path.exists(csr_file):
            os.remove(csr_file)
    
    with open(cert_file, 'r') as f:
        cert_content = f.read()
    with open(key_file, 'r') as f:
        key_content = f.read()
    return {"cert_content":cert_content, "key_content":key_content}

# Función para obtener la configuración del cliente OpenVPN
async def get_client_config(room_id: str, user_id: str):
    user_config_dir = os.path.join(OPEN_VPN_DIR, room_id, user_id)
    os.makedirs(user_config_dir, exist_ok=True)
    config_file = os.path.join(user_config_dir, "client.ovpn")
    certs = generate_client_certs(room_id, user_id)
    
    client_template = template_env.get_template("client.ovpn.j2")
    rendered_config = client_template.render(
        cert_content = certs["cert_content"], 
        key_content = certs["key_content"],
        server_ip=SERVER_IP
        )
    with open(config_file, "w") as f:
        f.write(rendered_config)
    
    with open(config_file, 'r') as f:
            config_content = f.read()
    
    #Eliminar el archivo del servidor
    if os.path.exists(config_file):
         os.remove(config_file)

    return {"ovpn_config": config_content}

# Ruta: Consultar salas activas
@app.get("/rooms")
async def get_rooms():
    return [{"room_id": room_id, "participants": len(data["participants"])} for room_id, data in rooms.items()]


# Ruta: Consultar participantes de una sala
@app.get("/rooms/{room_id}")
async def get_room_details(room_id: str):
    if room_id not in rooms:
        raise HTTPException(status_code=404, detail="Sala no encontrada")
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
        raise HTTPException(status_code=404, detail="Sala no encontrada")
    if user_id not in users:
        raise HTTPException(status_code=404, detail="Usuario no registrado")
    if user_id not in rooms[room_id]["participants"]:
        raise HTTPException(status_code=409, detail="El usuario no está en esta sala")
    
    rooms[room_id]["participants"].remove(user_id)  # Eliminar al usuario de la sala
    
    # Si la sala se queda sin participantes, podemos eliminar la sala
    if not rooms[room_id]["participants"]:
        process = rooms[room_id]["process"]
        if process and process.poll() is None:  # Verify the subprocess is still running
            process.terminate()  # Terminate gracefully
            process.wait(timeout=10) # Wait for it to finish
            if process.poll() is None: # Check that the subprocess was effectively terminated
                logging.error(f"Error terminating openvpn process for room {room_id}")
                # You could use process.kill() if it doesnt terminate after 10 secs
        del rooms[room_id]
        logging.info(f"Room {room_id} removed")
        return {"room_id": room_id, "participants": []}
    logging.info(f"User {user_id} left room {room_id}")
    return {"room_id": room_id, "participants": rooms[room_id]["participants"]}
    
    
@app.get("/test-vpn")
async def test_vpn():
    try:
        test_virtual_network()
        return {"message": "OpenVPN iniciado para pruebas."}
    except Exception as e:
        return {"error": f"Error al iniciar la red virtual de prueba: {str(e)}"}


def test_virtual_network():
  try:
    os.makedirs(OPEN_VPN_DIR, exist_ok=True)
    config_dir = os.path.join(OPEN_VPN_DIR, "test-vpn")
    os.makedirs(config_dir, exist_ok=True)

    config_file = os.path.join(config_dir, "server.conf")

    dh_file = os.path.join(config_dir, "dh.pem")
    subprocess.run([
        "openssl",
        "dhparam",
        "-out",
        dh_file,
        "2048"
    ], check = True)

    server_template = template_env.get_template("test_server.conf.j2")
    rendered_config = server_template.render(dh_file=dh_file)
    with open(config_file, "w") as f:
        f.write(rendered_config)
    
    # Iniciar OpenVPN en modo daemon
    process = subprocess.Popen(["openvpn", "--config", config_file], 
                            stdout=subprocess.PIPE, 
                            stderr=subprocess.PIPE, 
                            text=True)
    rooms["test-vpn"] = {
        "process": process
    }

  except Exception as e:
      logging.error(f"Error creating test virtual network: {e}")
      raise Exception(f"Error al crear la red virtual de prueba: {e}")