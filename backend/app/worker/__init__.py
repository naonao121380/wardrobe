"""批次 3 的 AI 流水线 Worker（DECISIONS D5 / D6 / D7）。

Worker 是**独立进程**，与 API 共享同一份数据库与媒体目录：

- API 只负责把衣物标成 ``pending``，从不跑模型；
- Worker 轮询 ``pending``，做完抠图 / 打标后把候选结果写回 ``ai_suggestion``，
  状态落到 ``needs_review``（有东西要人看）或 ``ready``（没啥可看的）；
- 抠图用的 ONNX 模型只装在 Worker 镜像里，API 镜像保持轻（D5）。

启动::

    python -m app.worker            # 常驻轮询
    python -m app.worker --once     # 处理一批就退出（测试 / 手工补跑）

**降级优先**：任何 provider 装不上、没配 key、推理报错，都不让流水线崩，
而是把原因写进 ``ai_suggestion.error``，衣物该 ready 就 ready、该 failed 就 failed（D7）。
"""

from app.worker.pipeline import process_garment
from app.worker.providers import (
    CandidateTag,
    ClipTagger,
    MattingResult,
    NoOpMatting,
    NoOpTagger,
    RembgMatting,
    TagSuggestion,
    VlmTagger,
    build_matting,
    build_tagger,
)
from app.worker.runner import claim_garment, requeue_stuck, run_once, run_forever

__all__ = [
    "CandidateTag",
    "ClipTagger",
    "MattingResult",
    "NoOpMatting",
    "NoOpTagger",
    "RembgMatting",
    "TagSuggestion",
    "VlmTagger",
    "build_matting",
    "build_tagger",
    "claim_garment",
    "process_garment",
    "requeue_stuck",
    "run_forever",
    "run_once",
]
