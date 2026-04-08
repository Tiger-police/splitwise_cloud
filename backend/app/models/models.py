import logging
from sqlalchemy import Column, Integer, String, DateTime, Text, inspect, text
from datetime import datetime
from app.db.database import Base, SessionLocal, engine
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
    openwebui_user_id = Column(String, unique=True, index=True, nullable=True)
    hashed_password = Column(String)
    role = Column(String)
    allowed_devices = Column(String)


class ModelNode(Base):
    """
    模型切分节点服务注册表
    """
    __tablename__ = "model_nodes"

    id = Column(Integer, primary_key=True, index=True)
    model_key = Column(String, index=True)  # 例如: "gpt2" / "llama-3.2-3b"
    model_name = Column(String)  # 例如: "meta-llama/Llama-3.2-3B"
    device_id = Column(String, index=True)  # 对应 Device.id，例如 cloud / edge_A
    node_role = Column(String, default="edge")  # edge / cloud
    service_type = Column(String, default="runtime")  # runtime / monitor 等
    ip_address = Column(String, index=True)  # 节点IP
    port = Column(Integer, index=True)  # 节点端口 (如 8001, 8002)
    control_path = Column(String, default="/load_strategy")  # 下发策略的控制接口路径
    supported_models = Column(Text, nullable=True)  # 多模型 runtime 支持的模型列表(JSON)
    status = Column(String, default="online")  # "online" 或 "offline"
    last_heartbeat = Column(DateTime, default=datetime.utcnow)  # 最后活跃时间


class ScheduleTask(Base):
    __tablename__ = "schedule_tasks"

    task_id = Column(String, primary_key=True, index=True)
    username = Column(String, index=True)
    model_type = Column(String, index=True)
    status = Column(String, default="accepted")
    phase = Column(String, default="strategy")
    phase_progress = Column(Integer, default=0)
    overall_progress = Column(Integer, default=0)
    message = Column(String, default="任务已受理")
    edge_device_id = Column(String, nullable=True)
    cloud_device_id = Column(String, nullable=True)
    edge_progress = Column(Integer, default=0)
    cloud_progress = Column(Integer, default=0)
    edge_status = Column(String, default="pending")
    cloud_status = Column(String, default="pending")
    strategy_payload = Column(Text, nullable=True)
    error_detail = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


def run_lightweight_migrations():
    """
    SQLite 轻量补丁：为已存在的表补齐新列。
    create_all 不会为已有表自动加列，这里手动兜底。
    """
    with engine.begin() as conn:
        inspector = inspect(conn)
        table_names = set(inspector.get_table_names())

        if "model_nodes" in table_names:
            existing_columns = {col["name"] for col in inspector.get_columns("model_nodes")}
            column_patches = [
                ("model_key", "ALTER TABLE model_nodes ADD COLUMN model_key VARCHAR"),
                ("device_id", "ALTER TABLE model_nodes ADD COLUMN device_id VARCHAR"),
                ("node_role", "ALTER TABLE model_nodes ADD COLUMN node_role VARCHAR DEFAULT 'edge'"),
                ("service_type", "ALTER TABLE model_nodes ADD COLUMN service_type VARCHAR DEFAULT 'runtime'"),
                ("control_path", "ALTER TABLE model_nodes ADD COLUMN control_path VARCHAR DEFAULT '/load_strategy'"),
                ("supported_models", "ALTER TABLE model_nodes ADD COLUMN supported_models TEXT"),
            ]

            for column_name, statement in column_patches:
                if column_name not in existing_columns:
                    logger.info("🧩 正在为 model_nodes 补充新字段: %s", column_name)
                    conn.execute(text(statement))

            conn.execute(text("UPDATE model_nodes SET model_key = lower(model_name) WHERE model_key IS NULL"))
            conn.execute(text("UPDATE model_nodes SET node_role = 'edge' WHERE node_role IS NULL"))
            conn.execute(text("UPDATE model_nodes SET service_type = 'runtime' WHERE service_type IS NULL"))
            conn.execute(text("UPDATE model_nodes SET control_path = '/load_strategy' WHERE control_path IS NULL"))

        if "users" in table_names:
            existing_columns = {col["name"] for col in inspector.get_columns("users")}
            if "openwebui_user_id" not in existing_columns:
                logger.info("🧩 正在为 users 补充新字段: openwebui_user_id")
                conn.execute(text("ALTER TABLE users ADD COLUMN openwebui_user_id VARCHAR"))
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_openwebui_user_id "
                "ON users (openwebui_user_id)"
            ))


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
            openwebui_user_id="ow-admin",
            hashed_password=get_password_hash("admin123"),
            role="admin",
            allowed_devices="cloud,edge_A,edge_B"
        )
        userA = User(
            username="userA",
            openwebui_user_id="ow-userA",
            hashed_password=get_password_hash("user123"),
            role="user",
            allowed_devices="cloud,edge_A"
        )
        db.add(admin)
        db.add(userA)
        db.commit()
    else:
        existing_users = db.query(User).filter(User.username.in_(["admin", "userA"])).all()
        changed = False
        for user in existing_users:
            if user.username == "admin" and not user.openwebui_user_id:
                user.openwebui_user_id = "ow-admin"
                changed = True
            if user.username == "userA" and not user.openwebui_user_id:
                user.openwebui_user_id = "ow-userA"
                changed = True
        if changed:
            db.commit()

    db.close()
