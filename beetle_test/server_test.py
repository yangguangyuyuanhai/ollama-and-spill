import uvicorn
from fastapi import FastAPI, Request
from pydantic import BaseModel, Field
from typing import List, Optional, Any
import aiosqlite
import httpx
import logging
#加上出入队列时间，便于追踪import time
import asyncio
import json
import redis.asyncio as redis

# --- 配置 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - [SERVER] - %(message)s')
logger = logging.getLogger(__name__)

DB_NAME = "missions.db"
# 连接宿主机 Redis 6380
REDIS_URL = "redis://localhost:6380"
TASK_QUEUE = "queue:missions"
RESULT_QUEUE = "queue:results"

USER_FORWARDING_LIMIT = asyncio.Semaphore(50)
beetle_server = FastAPI(title="Dispatch Server")
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

# --- 数据模型定义 ---

class PictureItem(BaseModel):
    picId: str
    # 兼容文档可能的拼写差异
    dowmloadUrl: Optional[str] = None 
    downloadUrl: Optional[str] = None

    def get_url(self):
        return self.dowmloadUrl or self.downloadUrl

class MissionRequest(BaseModel):
    taskSerial: str  # 核心字段
    type: str
    callbackurl: str
    pictureList: List[PictureItem]

# 新增：标准API返回结构
class StandardResponse(BaseModel):
    status: int
    error_msg: str
    data: Any

# 修改：回调内部子项PictureResult
class CallbackItem(BaseModel):
    picId: str
    result: bool
    reason: str  # 新增：大模型生成的理由

# 回调给用户的主体结构
class CallbackPayload(BaseModel):
    taskSerial: str
    type: str
    data: List[CallbackItem]

# --- 数据库操作 ---

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        # 任务表
        await db.execute("""
            CREATE TABLE IF NOT EXISTS missions (
                task_serial TEXT PRIMARY KEY,
                type TEXT,
                callbackurl TEXT,
                callback_status TEXT, 
                status TEXT, 
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # 图片表
        await db.execute("""
            CREATE TABLE IF NOT EXISTS pictures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_serial TEXT,
                pic_id TEXT,
                download_url TEXT,
                result BOOLEAN, 
                reason TEXT,
                FOREIGN KEY(task_serial) REFERENCES missions(task_serial)
            )
        """)
        await db.commit()

async def save_mission_initial(mission: MissionRequest):
    async with aiosqlite.connect(DB_NAME) as db:
        # 插入任务
        await db.execute(
            "INSERT OR REPLACE INTO missions (task_serial, type, callbackurl, callback_status, status) VALUES (?, ?, ?, ?, ?)",
            (mission.taskSerial, mission.type, mission.callbackurl, "WAITING", "PENDING")
        )
        # 插入图片
        pic_tuples = [(mission.taskSerial, p.picId, p.get_url()) for p in mission.pictureList]
        await db.executemany(
            "INSERT INTO pictures (task_serial, pic_id, download_url) VALUES (?, ?, ?)",
            pic_tuples
        )
        await db.commit()

async def update_mission_result(payload: CallbackPayload):
    async with aiosqlite.connect(DB_NAME) as db:
        # 更新主任务状态
        await db.execute("UPDATE missions SET status = 'COMPLETED', updated_at = CURRENT_TIMESTAMP WHERE task_serial = ?", (payload.taskSerial,))
        # 更新每张图片的结果和理由
        result_tuples = [(p.result, p.reason, payload.taskSerial, p.picId) for p in payload.data]
        await db.executemany("UPDATE pictures SET result = ?, reason = ? WHERE task_serial = ? AND pic_id = ?", result_tuples)
        await db.commit()

async def get_user_callback_url(task_serial: str):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT callbackurl FROM missions WHERE task_serial=?", (task_serial,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

async def update_callback_status(task_serial: str, status: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE missions SET callback_status = ? WHERE task_serial = ?", (status, task_serial))
        await db.commit()

# --- 后台监听与回调逻辑 ---

async def handle_forwarding(user_url: str, payload: CallbackPayload):
    logger.info(f"Callback posting to {user_url}")
    is_success = await forward_to_user(user_url, payload)
    final_status = "SUCCESS" if is_success else "FAILED"
    await update_callback_status(payload.taskSerial, final_status)

async def forward_to_user(user_url: str, payload: CallbackPayload):
    async with USER_FORWARDING_LIMIT:
        async with httpx.AsyncClient() as client:
            try:
                # 发送符合接口文档的 JSON
                resp = await client.post(user_url, json=payload.dict(), timeout=10.0)
                logger.info(f"User response code: {resp.status_code}")
                # 只要对方回 200 就认为成功
                return resp.status_code == 200
            except Exception as e:
                logger.error(f"Callback failed: {e}")
                return False

async def result_monitor():
    logger.info("Result Monitor started (Listening Redis)...")
    while True:
        try:
            # 阻塞等待结果
            result = await redis_client.brpop(RESULT_QUEUE, timeout=0)
            if result:
                json_data = result[1]
                data_dict = json.loads(json_data)
                payload = CallbackPayload(**data_dict)
                
                logger.info(f"Received Result for: {payload.taskSerial}")
                
                # 1. 存库
                await update_mission_result(payload)
                
                # 2. 触发回调
                user_url = await get_user_callback_url(payload.taskSerial)
                if user_url:
                    asyncio.create_task(handle_forwarding(user_url, payload))
                else:
                    logger.warning(f"No callback URL found for {payload.taskSerial}")
        except Exception as e:
            logger.error(f"Monitor Error: {e}")
            await asyncio.sleep(1)

# --- 启动与API ---

@beetle_server.on_event("startup")
async def startup():
    await init_db()
    # 启动后台监听任务
    asyncio.create_task(result_monitor())

@beetle_server.post("/mission_entry", response_model=StandardResponse)
async def mission_entry(request: MissionRequest):
    try:
        # 1. 存库
        await save_mission_initial(request)
        
        # 2. 推送 Redis 任务队列
        await redis_client.lpush(TASK_QUEUE, request.json())
        
        logger.info(f"📨 Queued: {request.taskSerial}")
        
        # 3. 返回标准结构
        return StandardResponse(
            status=200,
            error_msg="",
            data="请求成功"
        )
    except Exception as e:
        logger.error(f"API Error: {e}")
        return StandardResponse(status=500, error_msg=str(e), data="Server Error")

if __name__ == "__main__":
    uvicorn.run(beetle_server, host="0.0.0.0", port=8000)
