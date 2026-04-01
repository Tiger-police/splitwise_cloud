import os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# __file__ 当前在 backend/app/db/database.py
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent

# 拼接绝对路径：.../splitwise_cloud/data/cloud_edge.db
DB_PATH = BASE_DIR / "data" / "cloud_edge.db"
SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()