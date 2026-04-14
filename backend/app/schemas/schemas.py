from typing import List, Optional
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
    model_key: str
    ip_address: str
    port: int

class ModelUnregisterRequest(BaseModel):
    """节点主动下线请求体"""
    ip_address: str
    port: int

class LoginRequest(BaseModel):
    username: str
    password: str


class TokenExchangeRequest(BaseModel):
    openwebui_token: str


class AuthTokenResponse(BaseModel):
    access_token: str
    token_type: str
    username: str
    role: str

class UserCreate(BaseModel):
    username: str
    password: str
    role: str = "user"
    allowed_devices: str = "cloud"
    openwebui_user_id: Optional[str] = None


class UserOpenWebUIBindingUpdate(BaseModel):
    openwebui_user_id: str

class DeviceCreate(BaseModel):
    id: str
    name: str
    value: str
    device_type: str  # 👇 新增：用于接收前端传来的 'cloud' 或 'edge'


class EdgeTriggerRequest(BaseModel):
    """边缘端发送给云端中枢的触发请求"""
    model_type: str

# 👇 新增：对应截图中的每一层切分配置
class LayerPartition(BaseModel):
    layer_id: int
    head_assignments: List[int]  # 0为边端，1为云端
    ffn_assignment: int          # 0为边端，1为云端，2为拆分


class StrategyDisplayLayerPartition(BaseModel):
    layer_id: int
    head_assignments: List[int]
    ffn_assignment: int
    edge_head_count: int
    cloud_head_count: int

# 👇 新增：算法组回调我们接口时发送的总数据包
class StrategyCallbackRequest(BaseModel):
    task_id: str                 # 极其重要：用于匹配是哪次触发请求
    model_type: str
    layer_partitions: List[LayerPartition]


class RuntimeDecisionPayload(BaseModel):
    layer_partitions: List[LayerPartition]


class StrategyDisplayDecisionPayload(BaseModel):
    layer_partitions: List[StrategyDisplayLayerPartition]
    edge_head_count_total: int
    cloud_head_count_total: int


class RuntimeDispatchRequest(BaseModel):
    task_id: str
    model_type: str
    decision: RuntimeDecisionPayload


class RuntimeProgressCallbackRequest(BaseModel):
    task_id: str
    status: str
    progress: int
    message: str
    node_role: Optional[str] = None


class ScheduleTaskAcceptedResponse(BaseModel):
    status: str
    task_id: str
    phase: str
    phase_progress: int
    overall_progress: int
    message: str


class ScheduleTaskStatusResponse(BaseModel):
    task_id: str
    status: str
    phase: str
    phase_progress: int
    overall_progress: int
    message: str
    edge_progress: int
    cloud_progress: int
    edge_status: str
    cloud_status: str
    edge_message: str
    cloud_message: str
    error_detail: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ScheduleTaskStrategyResponse(BaseModel):
    task_id: str
    model_type: str
    decision: StrategyDisplayDecisionPayload
