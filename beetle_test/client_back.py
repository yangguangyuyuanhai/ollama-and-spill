import asyncio
import logging
import os
import io
import base64
import json
from typing import List, Optional

import redis.asyncio as redis
import httpx
import requests
import aiofiles
from pydantic import BaseModel
from PIL import Image

from Prompt_loader import PromptLoader

# --- 配置 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - [WORKER] - %(message)s')
logger = logging.getLogger(__name__)

# Ollama 配置
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen3-vl:latest"
IMAGE_SAVE_DIR = "./workspace/images"
os.makedirs(IMAGE_SAVE_DIR, exist_ok=True)
SYSTEM_INSTRUCTION = PromptLoader("./promot/spill_promot.yaml")

# Redis 配置 (连接宿主机 6380)
REDIS_URL = "redis://localhost:6380"
TASK_QUEUE = "queue:missions"
RESULT_QUEUE = "queue:results"

# 资源锁
GLOBAL_DOWNLOAD_SEM = asyncio.Semaphore(10)
GLOBAL_OLLAMA_LOCK = asyncio.Lock()


# --- 数据结构 (需与服务端一致) ---

class PictureItem(BaseModel):
    picId: str
    dowmloadUrl: Optional[str] = None
    downloadUrl: Optional[str] = None

    def get_url(self):
        return self.dowmloadUrl or self.downloadUrl


class MissionRequest(BaseModel):
    taskSerial: str
    type: str
    callbackurl: str
    pictureList: List[PictureItem]


class CallbackItem(BaseModel):
    picId: str
    result: bool
    reason: str  # 理由字段


class CallbackPayload(BaseModel):
    taskSerial: str
    type: str
    data: List[CallbackItem]


class QueueItem:
    def __init__(self, pic_id: str, file_path: str, success: bool):
        self.pic_id = pic_id
        self.file_path = file_path
        self.success = success


# --- 图像处理与模型调用 ---

def process_image_sync(file_path: str) -> str:
    try:
        img = Image.open(file_path)
        if img.mode != 'RGB':
            img = img.convert('RGB')
        # 保持比例缩放，限制最大边长 640，防止显存溢出
        img.thumbnail((640, 640), Image.Resampling.LANCZOS)
        buffered = io.BytesIO()
        img.save(buffered, format="JPEG", quality=85)
        return base64.b64encode(buffered.getvalue()).decode('utf-8')
    except Exception as e:
        logger.error(f"Img Error: {e}")
        return ""


def call_ollama_sync(image_base64: str, current_prompt: str):
    if not image_base64:
        logger.error("❌ ABORTING: Image data is empty!")
        return False, "Image Error: No base64 data"

    # 强制要求格式
    user_task = "请分析图像。必须使用中文且严格使用以下格式回答：\n理由：[你的理由]\n结果：[TRUE或FALSE]"
    payload = {
        "model": OLLAMA_MODEL,
        "system": current_prompt,
        "prompt": user_task,
        "images": [image_base64],
        "stream": False,
        "options": {
            "temperature": 0.0,
            "num_ctx": 6140,
            "top_p": 0.01
        }
    }

    # --- 恢复你的“变态”检查机制 ---
    try:
        # 1. 发起请求
        response = requests.post(OLLAMA_URL, json=payload, timeout=120)

        # 2. 状态码检查 (原汁原味)
        if response.status_code != 200:
            logger.critical(f"❌ OLLAMA API ERROR: Status Code {response.status_code}")
            logger.error(f"❌ Response Body: {response.text}")
            return False, f"HTTP Error {response.status_code}"

        # 3. 解析响应
        response_json = response.json()
        if "response" not in response_json:
            logger.error(f"❌ MALFORMED RESPONSE: Field 'response' missing. Keys: {response_json.keys()}")
            return False, "Protocol Error: Missing 'response' field"

        raw_text = response_json.get("response", "").strip()
        logger.info(f"🤖 Model Output: {raw_text}")

        # --- 解析逻辑 (保持新功能) ---
        result_bool = False
        if "TRUE" in raw_text.upper():
            result_bool = True
        elif "FALSE" in raw_text.upper():
            result_bool = False
        else:
            logger.warning(f"⚠️ AMBIGUOUS OUTPUT: Could not find TRUE/FALSE in: {raw_text[:50]}...")
            return False, f"Parse Error: {raw_text[:50]}"

        # 提取理由
        reason_text = raw_text
        if "结果：" in reason_text:
            reason_text = reason_text.split("结果：")[0]
        elif "Result:" in reason_text:
            reason_text = reason_text.split("Result:")[0]

        clean_reason = reason_text.replace("理由：", "").replace("Reason:", "").strip()
        if not clean_reason:
            clean_reason = "Model did not provide details."
        return result_bool, clean_reason

    # 4. 专门捕获连接错误 (恢复你的 Log 风格)
    except requests.exceptions.ConnectionError:
        logger.critical(f"❌ CONNECTION DEAD: Could not connect to {OLLAMA_URL}.")
        logger.critical("❌ CHECK: Is Ollama running? Is the port correct? Is Docker networking ok?")
        return False, "Connection Refused: Ollama Down"

    # 5. 捕获超时
    except requests.exceptions.Timeout:
        logger.error(f"❌ TIMEOUT: Ollama took longer than 120s to respond.")
        return False, "Timeout: Model too slow"

    # 6. 捕获其他未知错误
    except Exception as e:
        logger.error(f"❌ UNKNOWN CRASH in Inference: {str(e)}")
        return False, f"Exception: {str(e)}"
    # --- 检查机制结束 ---


# --- 生产消费流程 ---

async def producer(queue: asyncio.Queue, picture_list: List[PictureItem], taskSerial: str):
    async def download_one(client, pic):
        url = pic.get_url()
        if not url:
            await queue.put(QueueItem(pic.picId, "", False))
            return

        async with GLOBAL_DOWNLOAD_SEM:
            file_path = os.path.join(IMAGE_SAVE_DIR, f"{taskSerial}_{pic.picId}.jpg")
            # 简单的防重下载逻辑，可根据需要移除
            if os.path.exists(file_path):
                await queue.put(QueueItem(pic.picId, file_path, True))
                return

            try:
                resp = await client.get(url, timeout=30.0)
                if resp.status_code == 200:
                    async with aiofiles.open(file_path, 'wb') as f:
                        await f.write(resp.content)
                    await queue.put(QueueItem(pic.picId, file_path, True))
                else:
                    await queue.put(QueueItem(pic.picId, "", False))
            except Exception as e:
                logger.error(f"Download error: {e}")
                await queue.put(QueueItem(pic.picId, "", False))

    async with httpx.AsyncClient(verify=False) as client:
        tasks = [download_one(client, pic) for pic in picture_list]
        await asyncio.gather(*tasks)
    await queue.put(None)


async def consumer(queue: asyncio.Queue, total_count: int, current_prompt: str) -> List[CallbackItem]:
    results = []
    processed_count = 0
    while processed_count < total_count:
        item = await queue.get()
        if item is None:
            break
        
        res_bool = False
        res_reason = "Download Failed"
        
        if item.success:
            b64 = await asyncio.to_thread(process_image_sync, item.file_path)
            if b64:
                async with GLOBAL_OLLAMA_LOCK:
                    logger.info(f"Inference: {item.pic_id}")
                    # 调用模型，获取 bool 和 string
                    res_bool, res_reason = await asyncio.to_thread(call_ollama_sync, b64, current_prompt)
            
            # 删图
            try:
                os.remove(item.file_path)
            except:
                pass

        results.append(CallbackItem(picId=item.pic_id, result=res_bool, reason=res_reason))
        processed_count += 1
        queue.task_done()
    return results


async def process_mission(mission_data: str, redis_client):
    try:
        data = json.loads(mission_data)
        mission = MissionRequest(**data)

        logger.info(f"🚀 Processing: {mission.taskSerial}")

        current_prompt = SYSTEM_INSTRUCTION.system_prompt_get(mission.type)
        queue = asyncio.Queue(maxsize=100)

        # 启动消费者
        consumer_task = asyncio.create_task(consumer(queue, len(mission.pictureList), current_prompt))
        # 启动生产者
        await producer(queue, mission.pictureList, mission.taskSerial)

        # 等待结果
        final_data = await consumer_task

        # 构造回调 Payload
        callback_payload = CallbackPayload(
            taskSerial=mission.taskSerial,
            type=mission.type,
            data=final_data
        )

        await redis_client.lpush(RESULT_QUEUE, callback_payload.json())
        logger.info(f"✅ Done: {mission.taskSerial}")

    except Exception as e:
        logger.error(f"Mission Error: {e}")


async def main():
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    logger.info("🔥 Worker Node Started...")
    while True:
        try:
            result = await redis_client.brpop(TASK_QUEUE, timeout=0)
            if result:
                await process_mission(result[1], redis_client)
        except Exception as e:
            logger.error(f"Loop Error: {e}")
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
