# Ollama & Spill Project 🚀

本项目包含基于 Ollama 的大模型微调与推理服务部署环境。主要用于 **Qwen3-VL (Vision Language)** 模型的 LoRA 微调以及相关的 Client/Server 测试。

## 📂 项目结构

```text
.
├── docker-compose.yaml   # 容器编排配置
├── Dockerfile            # 环境构建文件
├── beetle_test/          # 测试代码与客户端脚本
│   ├── client_test.py    # 客户端测试脚本
│   ├── server_test.py    # 服务端测试脚本
│   └── promot/           # 提示词配置文件
└── workspace/            # 模型工作区 (微调结果与权重)
    └── spill/            # 存放 LoRA 权重和训练日志
```

## 🛠️ 快速开始 (Quick Start)

### 1. 环境准备
确保你的服务器已安装：
* [Docker](https://www.docker.com/) & [Docker Compose](https://docs.docker.com/compose/)
* NVIDIA Driver & NVIDIA Container Toolkit (用于 GPU 加速)

### 2. 获取代码
```bash
git clone https://github.com/yangguangyuyuanhai/ollama-and-spill.git
cd ollama-and-spill
```

### 3. 📥 下载模型权重 (重要！)
由于模型文件过大，Git 仓库中仅包含代码。请从以下地址下载模型权重并放入对应目录：

* **基础模型 (Base Model)**: 放入 `workspace/model_download/`
* **LoRA 权重 (Fine-tuned)**: 放入 `workspace/spill/lora_finaly/`
* **Ollama 模型**: 放入 `workspace/ollama_models/`

> *[在此处填写你的网盘链接或 HuggingFace 地址，例如: https://drive.google.com/...]*

### 4. 启动服务
使用 Docker Compose 一键启动环境：

```bash
docker-compose up -d --build
```

### 5. 运行测试
进入测试目录并运行客户端脚本：

```bash
# 进入容器或本地环境
python3 beetle_test/client_test.py
```

## 📝 微调说明
本项目使用 **Qwen3-VL-8B-Thinking** 进行微调。
训练产物位于 `workspace/spill/spill_qwen3_thinking_final/`。

## ⚠️ 注意事项
* 所有 `.safetensors` 和 `.bin` 大文件已在 `.gitignore` 中忽略。
* 请确保 `missions.db` 数据库文件已正确配置（如需）。

---
*Created by Fengze*
