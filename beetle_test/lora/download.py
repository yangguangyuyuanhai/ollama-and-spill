import sqlite3
import os

# === ⚙️ 配置区域 ===
DB_PATH = 'missions.db'           # 数据库路径
OUTPUT_FILE = 'valid_task_serials.txt' # 结果保存的文件名

def extract_keys():
    """
    只提取符合条件的主键 task_serial
    条件: missions表 (is_spill + COMPLETED) 且在 pictures 表中有记录
    """
    
    # 检查数据库是否存在
    if not os.path.exists(DB_PATH):
        print(f"❌ 错误: 找不到数据库文件 '{DB_PATH}'")
        return

    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            
            # === SQL 查询 ===
            # 使用 DISTINCT 去重，防止同一个任务对应多张图片导致 ID 重复
            query = """
            SELECT DISTINCT m.task_serial
            FROM missions m
            INNER JOIN pictures p ON m.task_serial = p.task_serial
            WHERE m.type = 'is_spill' 
              AND m.status = 'COMPLETED';
            """
            
            print(f"🔍 正在查询数据库: {DB_PATH} ...")
            cursor.execute(query)
            rows = cursor.fetchall()
            
            if rows:
                count = len(rows)
                print(f"✅ 找到 {count} 个符合条件的任务 ID。")
                
                # === 保存结果到 TXT ===
                with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
                    for row in rows:
                        # row 是一个元组 ('TASK_xxx', )，取第一个元素
                        f.write(f"{row[0]}\n")
                
                print(f"📂 ID 列表已保存至: {os.path.abspath(OUTPUT_FILE)}")
                
                # 打印前 5 个示例
                print("-" * 30)
                print("示例 ID:")
                for i, row in enumerate(rows[:5]):
                    print(f"{i+1}. {row[0]}")
                if count > 5:
                    print("...")
            else:
                print("⚠️ 未找到符合条件的记录。")

    except sqlite3.Error as e:
        print(f"❌ 数据库错误: {e}")

if __name__ == "__main__":
    extract_keys()
