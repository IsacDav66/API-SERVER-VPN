from fastapi import FastAPI
from pydantic import BaseModel
from typing import Dict
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# Permitir CORS desde cualquier origen (ajusta según sea necesario)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Cambia "*" por la URL de tu cliente Flutter si quieres permitir solo uno específico
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Base de datos en memoria (puedes usar una base de datos real más adelante)
nodes = {}

# Modelo para la información de los nodos
class Node(BaseModel):
    node_id: str
    ip: str
    port: int

# Ruta para registrar un nodo
# Ruta para registrar un nodo
@app.post("/register")
async def register_node(node: Node):
    nodes[node.node_id] = {"ip": node.ip, "port": node.port}
    return {"message": "Node registered", "nodes": nodes}

# Ruta para obtener los nodos registrados
@app.get("/nodes")
async def get_nodes():
    # Genera la respuesta para que cada nodo tenga un 'node_id'
    return [{"node_id": node_id, "ip": node["ip"], "port": node["port"]} for node_id, node in nodes.items()]
