import logging
from sqlalchemy import Column, Integer, String, DateTime
from datetime import datetime
from app.db.database import Base, SessionLocal
from app.core.security import get_password_hash

logger = logging.getLogger("InitDB")


class Device(Base):
    __tablename__ = "devices"
    id = Column(String, primary_key=True, index=True)
    name = Column(String)
    value = Column(String)
    device_type = Column(String, default="edge")  # 👇 新增：记录设备类型


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    role = Column(String)
    allowed_devices = Column(String)


class ModelNode(Base):
    """
    模型切分节点服务注册表
    """
    __tablename__ = "model_nodes"

    id = Column(Integer, primary_key=True, index=True)
    model_name = Column(String)  # 例如: "meta-llama/Llama-3.2-3B"
    ip_address = Column(String, index=True)  # 节点IP
    port = Column(Integer, index=True)  # 节点端口 (如 8001, 8002)
    status = Column(String, default="online")  # "online" 或 "offline"
    last_heartbeat = Column(DateTime, default=datetime.utcnow)  # 最后活跃时间


def init_db_data():
    """
    系统首次启动时，检查并注入默认的物理设备和管理员账号
    """
    db = SessionLocal()

    if not db.query(Device).first():
        logger.info("🛠️ 首次启动：正在向数据库注入默认物理设备资产...")
        db.add_all([
            Device(id="cloud", name="☁️ 云端总枢纽 (RTX 5090)", value="10.144.144.2:9400|10.144.144.2:9100",
                   device_type="cloud"),
            Device(id="edge_A", name="📱 边缘节点 A (RTX 4090)", value="10.144.144.3:9100|10.144.144.3:9400",
                   device_type="edge"),
            Device(id="edge_B", name="📱 边缘节点 B (RTX 3080)", value="10.144.144.4:9100|10.144.144.4:9400",
                   device_type="edge")
        ])
        db.commit()

    if not db.query(User).filter(User.username == "admin").first():
        logger.info("🛠️ 首次启动：正在向数据库注入初始管理员和普通用户账号...")
        admin = User(
            username="admin",
            hashed_password=get_password_hash("admin123"),
            role="admin",
            allowed_devices="cloud,edge_A,edge_B"
        )
        userA = User(
            username="userA",
            hashed_password=get_password_hash("user123"),
            role="user",
            allowed_devices="cloud,edge_A"
        )
        db.add(admin)
        db.add(userA)
        db.commit()

    db.close()