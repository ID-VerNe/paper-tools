"""流水线状态与路径上下文。

把"目录约定 + 断点续跑 state"抽成一个小对象,避免各 step 重复算路径、
重复读写 pipeline_state.json。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field


@dataclass
class PipelineContext:
    """一次 pipeline 运行的目录与状态上下文。

    所有 step 共享同一个 context,共享同一份 state 字典(断点续跑)。
    """
    article_dir: str
    ocr_dir: str = ""
    bib_dir: str = ""
    report_path: str = ""
    verify_report_path: str = ""
    map_path: str = ""
    state_path: str = ""
    state: dict = field(default_factory=lambda: {"steps_completed": {}, "processed_count": 0, "failed": []})
    _prev_state: dict = field(default_factory=dict, repr=False)

    @classmethod
    def create(cls, article_dir: str) -> "PipelineContext":
        article_dir = os.path.abspath(article_dir)
        ctx = cls(
            article_dir=article_dir,
            ocr_dir=os.path.join(article_dir, "ocr_output"),
            bib_dir=os.path.join(article_dir, "bib"),
            report_path=os.path.join(article_dir, "doi_match_report.md"),
            verify_report_path=os.path.join(article_dir, "verify_report.md"),
            map_path=os.path.join(article_dir, "doi_map.json"),
            state_path=os.path.join(article_dir, "pipeline_state.json"),
        )
        state = {"steps_completed": {}, "processed_count": 0, "failed": []}
        if os.path.exists(ctx.state_path):
            try:
                with open(ctx.state_path, "r", encoding="utf-8") as f:
                    state = json.load(f)
            except Exception:
                state = {"steps_completed": {}, "processed_count": 0, "failed": []}
        ctx.state = state
        ctx._prev_state = dict(state)
        return ctx

    def step_done(self, step: str) -> None:
        """标记某 step 完成,并落盘 state(若有变化)。"""
        self.state["steps_completed"][step] = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.save()

    def step_pending(self, step: str) -> bool:
        """该 step 是否尚未完成(可执行)。"""
        return step not in self.state.get("steps_completed", {})

    def record_failure(self, item: dict) -> None:
        self.state.setdefault("failed", []).append(item)

    def save(self) -> None:
        if self.state != self._prev_state:
            with open(self.state_path, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2)
            self._prev_state = dict(self.state)