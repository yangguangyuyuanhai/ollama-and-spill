import re
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
OLLAMA_MODEL = "spill-thinking"
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

    # 微调模型不需要太复杂的 Prompt，简单的指令即可触发它的能力
    user_task = "请分析图像。请先在<think>标签中思考，然后严格按格式回答：\n理由：[理由]\n结果：[TRUE或FALSE]"

    payload = {
        "model": OLLAMA_MODEL,  # 确保这里是你 ollama list 里的名字
        "system": current_prompt,   # 传入 yaml 里的提示词
        "prompt": user_task,
        "images": [image_base64],
        "stream": False,
        "options": {
            "temperature": 0.1,  # 稍微给一点温度
            "num_ctx": 8192,     # 【关键】防止长思维链被截断
            "top_p": 0.9
        }
    }

    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=120)

        if response.status_code != 200:
            logger.critical(f"❌ OLLAMA API ERROR: {response.status_code}")
            return False, f"HTTP Error {response.status_code}"

        response_json = response.json()
        raw_text = response_json.get("response", "").strip()
        
        # 记录原始输出以便调试
        logger.info(f"🤖 Raw Output: {raw_text[:200]}...") 

        # --- 核心解析逻辑 Start ---
        
        # 1. 移除 <think> 标签及其内容
        # 这是为了防止模型在思考过程中提到 "TRUE" (比如 "Is this TRUE? No.") 导致误判
        clean_text = re.sub(r'<think>.*?(?:</think>|$)', '', raw_text, flags=re.DOTALL).strip()
        
        # 2. 提取结果 (优先匹配标准格式)
        result_bool = False
        # 匹配 "结果：TRUE" 或 "Result: TRUE"
        if re.search(r'(结果|Result)[:：]\s*TRUE', clean_text, re.IGNORECASE):
            result_bool = True
        elif re.search(r'(结果|Result)[:：]\s*FALSE', clean_text, re.IGNORECASE):
            result_bool = False
        else:
            # 兜底匹配：只在清洗后的文本中找单词
            if "TRUE" in clean_text.upper():
                result_bool = True
            elif "FALSE" in clean_text.upper():
                result_bool = False
            else:
                logger.warning(f"⚠️ 解析失败: {clean_text[:50]}...")
                return False, "Parse Error"

        # 3. 提取理由
        clean_reason = "Model provided no details."
        # 尝试提取 "理由：" 后面的内容
        reason_match = re.search(r'(理由|Reason)[:：](.*?)(?=(结果|Result)|$)', clean_text, re.DOTALL | re.IGNORECASE)
        if reason_match:
            clean_reason = reason_match.group(2).strip()
        else:
            # 如果没找到标准理由格式，就用去掉结果后的剩余文本
            clean_reason = re.sub(r'(结果|Result)[:：]\s*(TRUE|FALSE)', '', clean_text, flags=re.IGNORECASE).strip()

        # --- 核心解析逻辑 End ---

        return result_bool, clean_reason

    except requests.exceptions.ConnectionError:
        logger.critical(f"❌ CONNECTION DEAD: Check Ollama.")
        return False, "Connection Refused"
    except Exception as e:
        logger.error(f"❌ CRASH: {str(e)}")
        return False, f"Exception: {str(e)}"


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
