import asyncio
import os
import sys
import argparse

# --- 关键修改：导入 sync 函数 ---
try:
    # 你的 client_test.py 里只有 call_ollama_sync
    from client_test import process_image_sync, call_ollama_sync, OLLAMA_MODEL
    from Prompt_loader import PromptLoader
except ImportError as e:
    print(f"❌ 导入错误: {e}")
    sys.exit(1)

# --- 配置 ---
TEST_IMAGE_DIR = "./workspace/images"
PROMPT_YAML_PATH = "./promot/spill_promot.yaml"
CURRENT_TEST_TYPE = "is_spill"

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    RESET = '\033[0m'

async def run_prompt_test(filter_keyword):
    # 检查图片目录
    if not os.path.exists(TEST_IMAGE_DIR):
        print(f"❌ 找不到图片文件夹: {TEST_IMAGE_DIR}")
        return

    # 加载提示词
    try:
        loader = PromptLoader(PROMPT_YAML_PATH)
        system_prompt = loader.system_prompt_get(CURRENT_TEST_TYPE)
    except Exception as e:
        print(f"❌ 提示词加载失败: {e}")
        return

    # 获取文件列表
    all_files = os.listdir(TEST_IMAGE_DIR)
    image_files = [
        f for f in all_files 
        if filter_keyword in f 
        and f.lower().endswith(('.png', '.jpg', '.jpeg'))
    ]
    image_files.sort()

    print(f"🚀 开始测试 | 模型: {OLLAMA_MODEL} | 图片数: {len(image_files)} | 关键词: '{filter_keyword}'")
    print("=" * 60)

    stats = {"TRUE": 0, "FALSE": 0, "ERROR": 0}

    # --- 循环测试 ---
    for idx, img_name in enumerate(image_files):
        img_path = os.path.join(TEST_IMAGE_DIR, img_name)
        
        print(f"[{idx+1}/{len(image_files)}] 分析中: {img_name} ...", end="\r")

        # 1. 图片转码 (放入线程池)
        b64_data = await asyncio.to_thread(process_image_sync, img_path)
        
        if not b64_data:
            print(f"❌ 读取失败: {img_name}" + " " * 40)
            stats["ERROR"] += 1
            continue

        # 2. 调用模型 (关键修改：使用 to_thread 调用同步函数)
        # 注意：call_ollama_sync 不需要 client 参数
        try:
            result_bool, reason = await asyncio.to_thread(call_ollama_sync, b64_data, system_prompt)
        except Exception as e:
            print(f"❌ 调用出错: {e}")
            stats["ERROR"] += 1
            continue

        # 3. 打印结果
        print(" " * 80, end="\r") 
        
        if result_bool:
            color = Colors.GREEN
            res_str = "TRUE "
            stats["TRUE"] += 1
        else:
            color = Colors.RED
            res_str = "FALSE"
            stats["FALSE"] += 1
        
        # 格式化输出
        print(f"🖼️  {img_name[:30]:<30} -> {color}{res_str}{Colors.RESET} | 💡 {reason}")

    print("=" * 60)
    print(f"✅ 统计: TRUE={stats['TRUE']} | FALSE={stats['FALSE']} | ERRORS={stats['ERROR']}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-m", type=str, default="", help="图片名关键词")
    args = parser.parse_args()
    
    asyncio.run(run_prompt_test(args.m))
