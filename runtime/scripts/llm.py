#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模型端点解析：本地优先，云端兜底。

优先级：
1. SPACEAGENTS_AUTO_API_URL（显式指定，最高优先）
2. KB_LOCAL_LLM_URL（显式指定本地）
3. 自动探测本机 Ollama（127.0.0.1:11434）且存在对话模型 → 用它，零成本、不出本机
4. 回退 SPACEAGENTS_AUTO_API_URL 默认值（算力中心）

为什么要本地优先：敏感件正文不该离开本机；本地模型零费用、可离线。
视觉（看图）本地一般没有，parse_vision 仍走云端，除非 KB_LOCAL_VISION_URL。
"""
import json
import os
import urllib.request

CLOUD = "https://token.spaceagents.cn/v1/chat/completions"
OLLAMA = "http://127.0.0.1:11434"


def _ollama_models():
    try:
        with urllib.request.urlopen(OLLAMA + "/api/tags", timeout=2) as r:
            return [m.get("name", "") for m in json.loads(r.read()).get("models", [])]
    except Exception:
        return []


def _is_chat_model(name):
    low = name.lower()
    if "embed" in low or "bge" in low or "minilm" in low or "rerank" in low:
        return False
    return True


def local_chat_model():
    """本机可用的对话模型（Ollama）；没有就返回空。"""
    explicit = os.environ.get("KB_LOCAL_LLM_MODEL", "").strip()
    models = _ollama_models()
    if explicit:
        return explicit if explicit in models or not models else explicit
    for m in models:
        if _is_chat_model(m):
            return m
    return ""


def text_endpoint():
    """返回 (url, key, model, source)。source 用于日志说明走的是哪条通道。"""
    explicit_url = os.environ.get("SPACEAGENTS_AUTO_API_URL", "").strip()
    if explicit_url:
        return (explicit_url, os.environ.get("SPACEAGENTS_AUTO_API_KEY", ""),
                os.environ.get("SPACEAGENTS_AUTO_MODEL", "auto"), "env:SPACEAGENTS_AUTO_API_URL")

    local_url = os.environ.get("KB_LOCAL_LLM_URL", "").strip()
    if local_url:
        return (local_url, os.environ.get("KB_LOCAL_LLM_KEY", "ollama"),
                os.environ.get("KB_LOCAL_LLM_MODEL", "qwen2.5:7b-instruct"), "env:KB_LOCAL_LLM_URL")

    model = local_chat_model()
    if model:
        return (OLLAMA + "/v1/chat/completions", "ollama", model, f"ollama:{model}")

    return (CLOUD, os.environ.get("SPACEAGENTS_AUTO_API_KEY", ""), "auto", "cloud:spaceagents")


def vision_endpoint():
    """视觉端点：显式本地 > 显式云端 > 默认云端（本机 Ollama 一般没有 VLM）。"""
    explicit = (os.environ.get("KB_LOCAL_VISION_URL", "").strip()
                or os.environ.get("SPACEAGENTS_AUTO_VISION_URL", "").strip())
    if explicit:
        return (explicit, os.environ.get("KB_LOCAL_VISION_KEY", "") or
                os.environ.get("SPACEAGENTS_AUTO_API_KEY", ""),
                os.environ.get("SPACEAGENTS_AUTO_VISION_MODEL", "auto"), "env:vision-url")
    return (os.environ.get("SPACEAGENTS_AUTO_API_URL", "").strip() or CLOUD,
            os.environ.get("SPACEAGENTS_AUTO_API_KEY", ""),
            os.environ.get("SPACEAGENTS_AUTO_VISION_MODEL", "auto"), "cloud:spaceagents")


def describe(source):
    return {"cloud:spaceagents": "算力中心（云端）",
            "env:SPACEAGENTS_AUTO_API_URL": "自定端点",
            "env:KB_LOCAL_LLM_URL": "自定本地端点",
            "env:vision-url": "自定视觉端点"}.get(source, source)
