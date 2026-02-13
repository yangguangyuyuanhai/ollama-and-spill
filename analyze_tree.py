import os

# 设置阈值：超过 50MB 的文件会被建议忽略
LARGE_FILE_THRESHOLD = 50 * 1024 * 1024  # 50MB

def get_size(path):
    total_size = 0
    try:
        for dirpath, dirnames, filenames in os.walk(path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                if not os.path.islink(fp):
                    total_size += os.path.getsize(fp)
    except Exception:
        pass
    return total_size

def format_size(size):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"

def print_tree(startpath, max_depth=3):
    startpath = os.path.abspath(startpath)
    print(f"\n📂 分析目录: {startpath}")
    print("=" * 60)
    
    for root, dirs, files in os.walk(startpath):
        level = root.replace(startpath, '').count(os.sep)
        if level >= max_depth:
            continue
            
        indent = ' ' * 4 * (level)
        folder_name = os.path.basename(root)
        
        # 计算文件夹大小（仅作为参考）
        # folder_size = get_size(root) 
        # print(f"{indent}📁 {folder_name}/ ({format_size(folder_size)})")
        if level == 0:
            print(f"📁 {folder_name}/")
        else:
            print(f"{indent}📁 {folder_name}/")

        subindent = ' ' * 4 * (level + 1)
        
        for f in files:
            fp = os.path.join(root, f)
            try:
                size = os.path.getsize(fp)
                size_str = format_size(size)
                
                # 判断文件类型和大小
                if size > LARGE_FILE_THRESHOLD:
                    mark = "❌ [建议忽略: 太大]"
                elif f.endswith(('.py', '.json', '.yaml', '.txt', '.md', '.jinja')):
                    mark = "✅ [建议保留: 代码/配置]"
                elif f.endswith(('.pyc', '.log', '.out', '.db', '.tar', '.gz')):
                    mark = "🚫 [建议忽略: 临时/日志/压缩包]"
                else:
                    mark = "❓ [需确认]"
                
                print(f"{subindent}📄 {f} ({size_str})  {mark}")
                
            except Exception as e:
                print(f"{subindent}📄 {f} (Error: {e})")

# 分析这两个大目录
print_tree('./beetle_test', max_depth=2)
print_tree('./workspace', max_depth=3)
