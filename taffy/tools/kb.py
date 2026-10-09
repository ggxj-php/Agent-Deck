"""知识库工具：让模型自己去 knowledge/ 里查资料。"""
from ..config import KB_TOP_K
from ..kb import search

SPECS = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": (
                "在本地知识库 knowledge/ 目录里检索资料，返回最相关的原文片段（带文件名和页码）。"
                "回答之前先查一下，拿到原文再组织语言，不要凭记忆编。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "检索用的关键词或问题",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": f"返回几段原文，默认 {KB_TOP_K}",
                    },
                },
                "required": ["query"],
            },
        },
    },
]


def search_knowledge(query: str, top_k: int = KB_TOP_K) -> str:
    """在知识库里检索，返回拼好的原文片段"""
    return search(query, top_k)
