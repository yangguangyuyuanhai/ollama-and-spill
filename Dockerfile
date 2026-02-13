# === 1. 基础镜像：CUDA 12.1 (支持 RTX 4090) ===
FROM nvidia/cuda:12.1.1-devel-ubuntu22.04

# 避免交互式弹窗
ENV DEBIAN_FRONTEND=noninteractive

# === 2. 系统依赖安装 ===
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-dev \
    git \
    wget \
    curl \
    vim \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# === 3. Python 环境配置 ===
RUN ln -s /usr/bin/python3 /usr/bin/python

# 升级 pip
RUN pip3 install --upgrade pip

# === 4. 安装 PyTorch (支持 4090 的 CUDA 12.1 版本) ===
RUN pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121

# === 5. 安装 YOLO (Ultralytics) ===
RUN pip install ultralytics

# === 6. 安装 Ollama (已修改为离线模式) ===
# 复制本地下载好的压缩包到镜像内的临时目录
COPY ollama-linux-amd64.tgz /tmp/ollama-linux-amd64.tgz

# 解压并安装到 /usr 目录，清理压缩包，赋予执行权限
RUN tar -C /usr -xzf /tmp/ollama-linux-amd64.tgz && \
    rm /tmp/ollama-linux-amd64.tgz && \
    chmod +x /usr/bin/ollama

# === 7. 目录结构与权限 (适配 YAML) ===
WORKDIR /ultralytics

# 创建挂载点目录结构
RUN mkdir -p /ultralytics/workspace/ollama_models

# === 8. 环境变量预设 (适配 YAML) ===
ENV OLLAMA_HOST=0.0.0.0
ENV OLLAMA_MODELS=/ultralytics/workspace/ollama_models

# === 9. 添加启动脚本 ===
RUN echo '#!/bin/bash\n\
echo "Starting Ollama in background..."\n\
ollama serve > /var/log/ollama.log 2>&1 &\n\
echo "Ollama started. Logs at /var/log/ollama.log"\n\
exec "$@"' > /usr/local/bin/entrypoint.sh && \
chmod +x /usr/local/bin/entrypoint.sh

# === 10. 启动命令 ===
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["/bin/bash"]
