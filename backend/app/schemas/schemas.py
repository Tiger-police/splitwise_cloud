from pydantic import BaseModel

class EdgeStateRequest(BaseModel):
    edge_id: str
    available_vram: float
    ping_latency: float
    model_name: str

class StrategyResponse(BaseModel):
    task_id: str
    edge_layers: int
    cloud_layers: int
    status: str
    message: str

class ModelRegisterRequest(BaseModel):
    """节点上线注册请求体"""
    model_name: str
    ip_address: str
    port: int

class ModelUnregisterRequest(BaseModel):
    """节点主动下线请求体"""
    ip_address: str
    port: int

class LoginRequest(BaseModel):
    username: str
    password: str

class UserCreate(BaseModel):
    username: str
    password: str
    role: str = "user"
    allowed_devices: str = "cloud"

class DeviceCreate(BaseModel):
    id: str
    name: str
    value: str
    device_type: str  # 👇 新增：用于接收前端传来的 'cloud' 或 'edge'


class EdgeTriggerRequest(BaseModel):
    """边缘端发送给云端中枢的触发请求"""
    model_type: str
    username: str
    edge_device: str = "cuda"
    edge_storage_limit_gb: float = 16.0
