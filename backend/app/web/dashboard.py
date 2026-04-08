from fastapi import APIRouter
from fastapi.responses import FileResponse
from app.core.config import settings

router = APIRouter()


@router.get("/", summary="访问监控大屏", tags=["前端界面"])
async def serve_dashboard():
    """
    当用户在浏览器访问当前后端服务根路径 `/` 时，
    直接返回 `frontend/dashboard.html` 给浏览器解析。
    """
    html_path = settings.FRONTEND_DIR / "dashboard.html"

    if not html_path.exists():
        return {"error": "🚨 未找到前端页面，请检查 splitwise_cloud/frontend/dashboard.html 是否存在！"}

    return FileResponse(html_path)
