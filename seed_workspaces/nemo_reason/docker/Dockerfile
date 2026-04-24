# nemo-reasoner: LoRA training + data-parallel inference for Nemotron-H.
# torch 2.10 + transformers + PEFT. No vLLM — its V1 engine segfaults on
# NemotronH in containers (TP=1 and TP=8, with/without LoRA, all configs
# tested). Inference runs transformers `generate()` across all visible GPUs
# via DP fan-out (see docker/inference.py).
#
# Build:   ./docker/build.sh <tag>
# Usage:   docker run --rm --gpus all <image> train
#          docker run --rm --gpus all <image> inference --config eval.yaml

FROM nvidia/cuda:12.8.1-cudnn-devel-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TORCH_CUDA_ARCH_LIST="9.0"

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.11 python3.11-venv python3.11-dev python3-pip \
        git ca-certificates build-essential ninja-build \
    && rm -rf /var/lib/apt/lists/* \
    && update-alternatives --install /usr/bin/python python /usr/bin/python3.11 1 \
    && update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1 \
    && python -m pip install --upgrade pip setuptools wheel

# torch 2.10 cu128
RUN pip install torch==2.10.0 --index-url https://download.pytorch.org/whl/cu128

# Core ML deps
RUN pip install \
        peft==0.19.1 \
        "accelerate>=1.13,<1.14" \
        datasets==4.8.4 \
        sentencepiece==0.2.1 \
        pyyaml==6.0.3 \
        pandas

# flash-attn for torch 2.10
RUN pip install flash-attn --no-build-isolation

# Nemotron-H mamba shim (pure-torch fallback; avoids CUDA kernel segfault with PEFT)
COPY mamba_ssm_shim /usr/local/lib/python3.11/dist-packages/mamba_ssm

# Entrypoint + inference driver
COPY common.py /opt/aevolve/common.py
COPY entrypoint.py /opt/aevolve/entrypoint.py
COPY inference.py /opt/aevolve/inference.py
RUN chmod +x /opt/aevolve/entrypoint.py

WORKDIR /workspace

ENTRYPOINT ["python", "/opt/aevolve/entrypoint.py"]
