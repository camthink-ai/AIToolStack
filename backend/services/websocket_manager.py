"""WebSocket connection manager"""
from typing import Dict, Set
from fastapi import WebSocket
import json


class WebSocketManager:
    """WebSocket connection manager"""
    
    def __init__(self):
        # project_id -> Set[WebSocket]
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        # Global device connections (using special key "_devices")
        self.device_connections: Set[WebSocket] = set()
    
    async def connect(self, websocket: WebSocket, project_id: str):
        """Accept new connection"""
        await websocket.accept()
        
        if project_id not in self.active_connections:
            self.active_connections[project_id] = set()
        
        self.active_connections[project_id].add(websocket)
        print(f"[WebSocket] Client connected to project {project_id}. Total: {len(self.active_connections[project_id])}")
    
    def disconnect(self, websocket: WebSocket, project_id: str):
        """Disconnect"""
        if project_id in self.active_connections:
            self.active_connections[project_id].discard(websocket)
            
            if not self.active_connections[project_id]:
                del self.active_connections[project_id]
            
            print(f"[WebSocket] Client disconnected from project {project_id}")
    
    async def send_personal_message(self, message: dict, websocket: WebSocket):
        """Send personal message"""
        try:
            await websocket.send_json(message)
        except Exception as e:
            print(f"[WebSocket] Error sending message: {e}")
    
    async def broadcast_to_project(self, project_id: str, message: dict):
        """Broadcast to all clients in project"""
        if project_id not in self.active_connections:
            print(f"[WebSocket] No active connections for project {project_id}")
            return
        
        connection_count = len(self.active_connections[project_id])
        print(f"[WebSocket] Broadcasting to {connection_count} client(s) in project {project_id}")
        
        disconnected = set()
        success_count = 0
        
        for connection in self.active_connections[project_id]:
            try:
                await connection.send_json(message)
                success_count += 1
            except Exception as e:
                print(f"[WebSocket] Error broadcasting to client: {e}")
                disconnected.add(connection)
        
        # Clean up disconnected connections
        for conn in disconnected:
            self.disconnect(conn, project_id)
        
        print(f"[WebSocket] Successfully sent message to {success_count} client(s)")
    
    def broadcast_project_update(self, project_id: str, update: dict):
        """Broadcast project update (async call)"""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If event loop is running, create task
                asyncio.create_task(self.broadcast_to_project(project_id, update))
                print(f"[WebSocket] Broadcasting update to project {project_id}: {update.get('type', 'unknown')}")
            else:
                # If event loop is not running, run directly
                loop.run_until_complete(self.broadcast_to_project(project_id, update))
                print(f"[WebSocket] Broadcasting update to project {project_id}: {update.get('type', 'unknown')}")
        except RuntimeError:
            # If no event loop, create a new one
            asyncio.run(self.broadcast_to_project(project_id, update))
            print(f"[WebSocket] Broadcasting update to project {project_id}: {update.get('type', 'unknown')}")
        except Exception as e:
            print(f"[WebSocket] Error broadcasting update: {e}")
    
    async def connect_device_listener(self, websocket: WebSocket):
        """Connect to global device update channel"""
        await websocket.accept()
        self.device_connections.add(websocket)
        print(f"[WebSocket] Device listener connected. Total: {len(self.device_connections)}")
    
    def disconnect_device_listener(self, websocket: WebSocket):
        """Disconnect from global device update channel"""
        self.device_connections.discard(websocket)
        print(f"[WebSocket] Device listener disconnected. Total: {len(self.device_connections)}")
    
    async def broadcast_device_update(self, message: dict):
        """Broadcast device update to all device list listeners"""
        if not self.device_connections:
            return
        
        connection_count = len(self.device_connections)
        print(f"[WebSocket] Broadcasting device update to {connection_count} listener(s)")
        
        disconnected = set()
        success_count = 0
        
        for connection in self.device_connections:
            try:
                await connection.send_json(message)
                success_count += 1
            except Exception as e:
                print(f"[WebSocket] Error broadcasting device update to client: {e}")
                disconnected.add(connection)
        
        # Clean up disconnected connections
        for conn in disconnected:
            self.disconnect_device_listener(conn)
        
        print(f"[WebSocket] Successfully sent device update to {success_count} listener(s)")
    
    def broadcast_device_update_sync(self, message: dict):
        """Broadcast device update (async call)"""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If event loop is running, create task
                asyncio.create_task(self.broadcast_device_update(message))
                print(f"[WebSocket] Broadcasting device update: {message.get('type', 'unknown')}")
            else:
                # If event loop is not running, run directly
                loop.run_until_complete(self.broadcast_device_update(message))
                print(f"[WebSocket] Broadcasting device update: {message.get('type', 'unknown')}")
        except RuntimeError:
            # If no event loop, create a new one
            asyncio.run(self.broadcast_device_update(message))
            print(f"[WebSocket] Broadcasting device update: {message.get('type', 'unknown')}")
        except Exception as e:
            print(f"[WebSocket] Error broadcasting device update: {e}")


# Global WebSocket manager instance
websocket_manager = WebSocketManager()

