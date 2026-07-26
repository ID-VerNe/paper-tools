"""Core extraction pipeline orchestrator.

Refactored from the original project's main.py Processor class with:
- Config-driven task enumeration via config objects
- ReaderFactory for multiple input formats
- OutputManager for configurable file naming
- StateManager for per-model state tracking

Lifecycle overview:
  1. The Pipeline is instantiated with a ReviewConfig and model alias.
  2. ``initialize_queue()`` scans the input directory and builds a deque of
     ExtractionTask objects, skipping tasks already marked completed in state.
  3. ``run()`` submits tasks to a ``ThreadPoolExecutor``, manages retries via
     a linear backoff / re-queue rotation strategy, and returns ``PipelineStats``.
  4. Each task goes through: read document -> render prompt -> LLM call ->
     JSON parse/validate -> write result file (or skip in burn mode).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Any, Dict, List, Optional

from tqdm import tqdm

from .config import ConfigLoader
from .llm_handler import LLMHandler
from .models import ExtractionTask, ModelConfig, PromptDef, ReviewConfig, PipelineStats
from .output import OutputManager
from .readers.base import ReaderFactory
from .state import StateManager
from .templates import TemplateManager
from .utils.file_utils import relpath, scan_documents
from .utils.json_utils import clean_json_string, extract_json_block, validate_json_schema

logger = logging.getLogger(__name__)


class NonRetriableError(Exception):
    """A task failure that should NOT be retried under any circumstances.

    Raise this (or return a designated flag) when the error is permanent —
    e.g. auth failure, invalid request, missing document, corrupt file.
    The pipeline catches it separately from generic ``Exception`` and marks
    the task as permanently failed without wasting retry attempts.
    """


class Pipeline:
    """Core extraction pipeline: enumerate tasks, execute with concurrency, track state.

    The Pipeline owns the life cycle of a single extraction run for one model.
    It scans documents, builds tasks from (document, prompt) pairs, dispatches
    them to a thread pool, and manages retries with linear backoff.

    Attributes:
        config: The loaded ``ReviewConfig`` (prompts, models, io paths, etc.).
        model_alias: The alias of the model to use (e.g. ``"opus"``).
        model_config: The resolved ``ModelConfig`` for ``model_alias``.
        max_workers: Max thread-pool parallelism. Falls back to
            ``model_config.max_concurrent`` when not given.
        burn_mode: When ``True``, the LLM is called but output is *not* written
            to disk. Useful for cost estimation or schema validation only.
        state_manager: Tracks completed / failed tasks across runs so the
            pipeline can resume after interruption.
        output_manager: Builds output file paths from task metadata.
        reader_factory: Creates document readers based on file extension.
        template_manager: Loads and renders Jinja2-style prompt templates.
        llm_handler: Rate-limited LLM API client with key rotation.
        queue: A ``deque`` of pending ``ExtractionTask`` objects.
        stats: Accumulated ``PipelineStats`` (completed, failed, retries, ...).
        _lock: A ``threading.Lock`` protecting shared mutable state (queue,
            stats, pbar).
        _pbar: Optional ``tqdm`` progress bar shown during ``run()``.

    Usage:
        config = ConfigLoader.from_file("litrev.yaml")
        pipeline = Pipeline(config, model_alias="opus")
        pipeline.initialize_queue()
        pipeline.run()
    """

    def __init__(
        self,
        config: ReviewConfig,
        model_alias: str,
        max_workers: Optional[int] = None,
        burn_mode: bool = False,
    ) -> None:
        """Initialise pipeline sub-systems and resolve the target model config.

        Args:
            config: Top-level ``ReviewConfig`` loaded from the YAML file.
            model_alias: Which model to use (must match an alias in ``config.models``).
            max_workers: Override for max concurrent workers. When ``None``,
                ``model_config.max_concurrent`` is used.
            burn_mode: When ``True``, output files are not written (dry-run).
        """
        self.config = config
        self.model_alias = model_alias

        # Resolve model config
        self.model_config: ModelConfig = self._resolve_model(model_alias)

        # Override max_workers if provided
        # Note: None = use model_config.max_concurrent; 0 = single-threaded
        self.max_workers = max_workers if max_workers is not None else self.model_config.max_concurrent

        self.burn_mode = burn_mode

        # Initialize sub-systems
        self.state_manager = StateManager(config.state_file)
        self.output_manager = OutputManager(
            config.output, model_alias, self.model_config.model_name
        )
        self.reader_factory = ReaderFactory([f.value for f in config.input_formats])
        self.template_manager = TemplateManager()
        self.llm_handler = LLMHandler(self.model_config)

        # Queue and stats
        self.queue: deque = deque()
        self.stats = PipelineStats()
        self._lock = threading.Lock()
        self._pbar: Optional[tqdm] = None

    def _resolve_model(self, alias: str) -> ModelConfig:
        """Find a model config by alias, or raise if not found.

        Args:
            alias: The model alias to look up (e.g. ``"opus"``).

        Returns:
            The matching ``ModelConfig`` instance.

        Raises:
            ValueError: If no model with the given alias exists in
                ``self.config.models``.
        """
        for m in self.config.models:
            if m.alias == alias:
                return m
        raise ValueError(
            f"Model alias '{alias}' not found in configuration. "
            f"Available: {[m.alias for m in self.config.models]}"
        )

    @staticmethod
    def _build_image_caption_map(md_path: str) -> dict[str, str | None]:
        """Build a mapping from image filename to its caption text.

        Scans the Markdown file sequentially, collecting ``![](images/xxx.jpg)``
        references and ``Figure`` / ``Table`` captions.  Each image is paired
        with the *next* caption that follows it in the file order.
        """
        import re
        with open(md_path, "r", encoding="utf-8") as f:
            text = f.read()

        entries: list[tuple[int, str, str]] = []

        for m in re.finditer(r'!\[.*?\]\((images/[^)]+)\)', text):
            entries.append((m.start(), "image", m.group(1)))

        for m in re.finditer(
            r"<center>\s*(?:Figure|Fig\.?|Scheme)\s+\d+[^<]*?</center>",
            text, re.IGNORECASE,
        ):
            inner = re.sub(r"</?center>", "", m.group(0)).strip()
            inner = re.sub(r"^(?:Fig\.?\s|Scheme\s)", "Figure ", inner, flags=re.IGNORECASE)
            entries.append((m.start(), "caption", inner))

        for m in re.finditer(r"(?:^|\n)\s*(Figure\s+\d+\.[^\n]{0,500})", text):
            entries.append((m.start(), "caption", m.group(1).strip()))

        entries.sort(key=lambda x: x[0])

        image_map: dict[str, str | None] = {}
        current_img: str | None = None
        for pos, etype, data in entries:
            if etype == "image":
                current_img = data
                if current_img not in image_map:
                    image_map[current_img] = None
            elif etype == "caption" and current_img is not None:
                if image_map.get(current_img) is None:
                    image_map[current_img] = data

        return image_map

    def initialize_queue(self) -> None:
        """Scan input directory and build the deque of ``ExtractionTask`` objects.

        For each discovered document and each prompt definition, one task is
        created. Tasks whose ``(model_alias, task_id)`` pair is already recorded
        as completed in the state file are skipped — this is how the pipeline
        achieves resumability across interrupted runs.

        When a prompt has ``multimodal: True``, the pipeline scans the
        document's ``images/`` sub-directory and creates **one task per image**
        instead of one task per document.  The image caption (extracted from
        the Markdown file) is passed as ``{content}`` and the image file is
        sent via the vision API.

        The progress bar is seeded with the number of skipped (already-completed)
        tasks so the total reflects the full count.
        """
        # Discover documents
        extensions = [fmt.value for fmt in self.config.input_formats]
        documents = scan_documents(
            input_dir=self.config.input_dir,
            extensions=extensions,
            recursive=self.config.recursive,
            exclude_patterns=self.config.exclude_patterns,
        )

        # Build queue
        total = 0
        skipped = 0

        seen: set[str] = set()

        for doc_path in documents:
            rel = relpath(doc_path, self.config.input_dir)
            parent_dir = os.path.dirname(doc_path)

            for prompt_def in self.config.prompts:
                if not prompt_def.multimodal:
                    # ── Plain text mode: one task per document ──────────
                    task = ExtractionTask(
                        document_path=doc_path,
                        relative_path=rel,
                        prompt_def=prompt_def,
                        model_alias=self.model_alias,
                    )
                    if task.task_id in seen:
                        continue
                    seen.add(task.task_id)
                    total += 1
                    if not self.burn_mode and self.state_manager.is_completed(
                        self.model_alias, task.task_id
                    ):
                        skipped += 1
                        continue
                    self.queue.append(task)
                else:
                    # ── Multimodal mode: one task per image ─────────────
                    # Build image → caption map from the .md file
                    image_map = self._build_image_caption_map(doc_path)
                    for img_file, caption in image_map.items():
                        img_path = os.path.join(parent_dir, img_file)
                        if not os.path.exists(img_path):
                            continue
                        # Use a relative path that includes the image filename
                        # so each image gets its own state entry.
                        img_rel = os.path.join(os.path.dirname(rel), img_file)
                        task = ExtractionTask(
                            document_path=doc_path,
                            relative_path=img_rel,
                            prompt_def=prompt_def,
                            model_alias=self.model_alias,
                            image_path=img_path,
                            image_caption=caption or "",
                            image_doi_slug=os.path.basename(os.path.dirname(doc_path)),
                        )
                        if task.task_id in seen:
                            continue
                        seen.add(task.task_id)
                        total += 1
                        if not self.burn_mode and self.state_manager.is_completed(
                            self.model_alias, task.task_id
                        ):
                            skipped += 1
                            continue
                        self.queue.append(task)

        self.stats.total = total
        self.stats.skipped = skipped

        logger.info(
            f"Initialized queue with {len(self.queue)} tasks. "
            f"Skipped {skipped}/{total} already completed."
        )
        print(f"[*] Total tasks: {total}")
        print(f"[*] Already completed: {skipped}")
        print(f"[*] Remaining to process: {len(self.queue)}")

    def run(self) -> PipelineStats:
        """Execute the pipeline with concurrent workers and retry management.

        The method uses a ``ThreadPoolExecutor`` to process tasks in parallel,
        bounded by ``self.max_workers``. Completed tasks are popped from the
        active set and new tasks from the queue are submitted to fill available
        slots (back-pressure mechanism). Retries are handled transparently:
        ``_safe_process`` returns ``False`` for tasks that should be re-queued,
        and ``run()`` does *not* re-submit them — the re-queue happens inside
        ``_handle_failure``, which rotates the task back into ``self.queue``
        after a linear-backoff sleep.

        The progress bar description is updated dynamically to show the current
        retry count.

        Returns:
            Final ``PipelineStats`` with completed / failed / retry tallies.

        Raises:
            (none intentionally — all task-level exceptions are caught by
            ``_safe_process``.)
        """
        if not self.queue and self.stats.skipped == self.stats.total:
            print("[*] All tasks already completed. Nothing to do.")
            return self.stats

        desc = f"BURN ({self.model_alias})" if self.burn_mode else f"Extract ({self.model_alias})"
        self._pbar = tqdm(
            total=self.stats.total,
            desc=desc,
            unit="task",
        )
        self._pbar.update(self.stats.skipped)

        max_workers = min(self.max_workers, len(self.queue) or 1)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_task: Dict[Any, ExtractionTask] = {}

            # Initial task submission: fill the pool up to max_workers
            with self._lock:
                while self.queue and len(future_to_task) < max_workers:
                    task = self.queue.popleft()
                    future_to_task[executor.submit(self._safe_process, task)] = task

            # Main event loop: wait for at least one future to complete,
            # then submit replacements to keep the pool saturated.
            while future_to_task:
                done, _ = wait(future_to_task.keys(), return_when=FIRST_COMPLETED)

                for future in done:
                    task = future_to_task.pop(future)
                    is_terminal = future.result()

                    if is_terminal:
                        # Task finished (success or max retries exceeded) —
                        # advance the progress bar by one unit.
                        self._pbar.update(1)

                    # Update desc with retry count
                    with self._lock:
                        self._pbar.set_description(
                            f"{desc} [Retries: {self.stats.retries}]"
                        )

                # Submit new tasks to fill slots vacated by completed futures
                with self._lock:
                    while self.queue and len(future_to_task) < max_workers:
                        task = self.queue.popleft()
                        future_to_task[executor.submit(self._safe_process, task)] = task

            # Ensure LLM connections are released even on exception
            self.llm_handler.close()

        self._pbar.close()
        # Flush any pending state changes to disk before reporting completion
        self.state_manager.flush()
        print(f"\n[*] Done. Completed: {self.stats.completed}, "
              f"Failed: {self.stats.failed}, Retries: {self.stats.retries}")
        return self.stats

    def _safe_process(self, task: ExtractionTask) -> bool:
        """Wrapper that catches all exceptions and handles retry logic.

        This is the entry point submitted to the thread pool. It delegates the
        actual work to ``_process_task`` and, on failure, to ``_handle_failure``.

        The boolean return value tells the caller (``run()``) whether this task
        is **terminal** (True = success OR max retries exhausted) — meaning the
        progress bar should tick — or **non-terminal** (False = re-queued for
        retry), meaning the progress bar should *not* tick yet.

        Args:
            task: The extraction task to process.

        Returns:
            ``True`` if the task reached a terminal state (succeeded or
            permanently failed). ``False`` if it was re-queued for retry.
        """
        try:
            success = self._process_task(task)
            if success:
                # Persist success state (skip in burn mode to avoid corrupting state)
                if not self.burn_mode:
                    self.state_manager.mark_success(
                    self.model_alias, task.task_id, task.retry_count
                )
                self.stats.increment_completed()
                return True
            else:
                # Non-exception failure (schema validation, missing reader, etc.)
                return self._handle_failure(task)
        except NonRetriableError as e:
            # Permanent failure — do NOT retry. Mark as failed immediately.
            tqdm.write(f"[!] Permanent failure for {task.task_id}: {e}")
            if not self.burn_mode:
                self.state_manager.mark_failed(
                    self.model_alias, task.task_id, task.retry_count,
                    error=str(e),
                )
            self.stats.increment_failed()
            self._pbar.update(1)
            return True
        except Exception as e:
            tqdm.write(f"[!] Error processing {task.task_id}: {e}")
            return self._handle_failure(task)

    def _process_task(self, task: ExtractionTask) -> bool:
        """Process a single extraction task end-to-end.

        The processing pipeline consists of five steps:

        1. **Read document** — Obtain a reader from ``ReaderFactory`` for the
           document's extension and read its full text.
        2. **Render prompt** — Load the system/user prompt templates and render
           the user prompt with the document content (with optional truncation).
        3. **Call LLM** — Send the rendered prompts to the API via
           ``self.llm_handler.request()``.
        4. **Parse & validate JSON** — Clean the raw LLM response string,
           parse it as JSON, and validate against the expected schema.
        5. **Write result** — Serialise the validated JSON to disk. Skipped in
           burn mode.

        Args:
            task: The extraction task to process.

        Returns:
            ``True`` if the task completed successfully, ``False`` otherwise.
            Non-API errors (missing reader, JSON parse failure, schema
            validation failure) return ``False`` without raising, so they are
            routed to the retry path.
        """
        # ── Step 1: Read document ───────────────────────────────────────
        if task.prompt_def.multimodal and task.image_path:
            # Multimodal: use the caption text as content, skip document reader
            content = task.image_caption
        else:
            reader = self.reader_factory.get_reader(task.document_path)
            if not reader:
                tqdm.write(f"[!] No reader for {task.document_path}")
                raise NonRetriableError(f"No reader for {task.document_path}")
            try:
                content = reader.read(task.document_path)
            except (FileNotFoundError, PermissionError, UnicodeDecodeError) as e:
                tqdm.write(f"[!] Cannot read {task.document_path}: {e}")
                raise NonRetriableError(str(e)) from e

        # ── Step 2: Load and render prompt template ─────────────────────
        prompts = self.template_manager.load(
            task.prompt_def,
            base_dir=".",
        )

        # Additional variables for template rendering
        render_vars = {}
        if task.prompt_def.multimodal and task.image_path:
            render_vars["image_stem"] = os.path.splitext(os.path.basename(task.image_path))[0]
            render_vars["doi_slug"] = task.image_doi_slug

        user_prompt = self.template_manager.render(
            prompts["user_prompt"],
            content,
            truncation=task.prompt_def.content_truncation if not task.prompt_def.multimodal else None,
            variables=render_vars if render_vars else None,
        )

        # ── Step 2b: For multimodal tasks, load image parts ──────────
        image_parts = None
        if task.prompt_def.multimodal and task.image_path:
            try:
                with open(task.image_path, "rb") as f:
                    import base64
                    img_b64 = base64.b64encode(f.read()).decode()
                image_parts = [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{img_b64}",
                        },
                    }
                ]
            except (FileNotFoundError, PermissionError) as e:
                tqdm.write(f"[!] Cannot read image {task.image_path}: {e}")
                raise NonRetriableError(str(e)) from e

        # ── Step 3: Call LLM ────────────────────────────────────────────
        try:
            response_str = self.llm_handler.request(
                prompts["system_prompt"], user_prompt,
                image_parts=image_parts,
            )
        except Exception as e:
            if self.llm_handler.is_retriable(e):
                raise  # Let retry logic handle it via _safe_process
            tqdm.write(f"[!] Non-retriable API error for {task.task_id}: {e}")
            raise NonRetriableError(str(e)) from e

        # ── Step 4: Parse and validate JSON ─────────────────────────────
        try:
            # Use extract_json_block which handles multi-strategy extraction:
            # 1. Strip markdown fences,
            # 2. Bracket-depth matching for text-wrapped JSON,
            # 3. Fallback (the caller handles json.JSONDecodeError).
            cleaned = extract_json_block(response_str)
            data = json.loads(cleaned)
            if not validate_json_schema(data):
                tqdm.write(f"[!] Schema validation failed for {task.task_id}")
                return False
        except json.JSONDecodeError:
            tqdm.write(f"[!] JSON parse failed for {task.task_id}")
            return False

        # ── Step 5: Write output (skip in burn mode) ────────────────────
        if self.burn_mode:
            return True

        out_path = self.output_manager.get_result_path(task)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        # Write to tmp file first, then atomically rename (prevents truncated files on crash)
        tmp_path = out_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, out_path)

        return True

    def _handle_failure(self, task: ExtractionTask) -> bool:
        """Handle a task failure with linear-backoff retry and queue rotation.

        When a task fails, this method:

        1. Increments the task's retry counter.
        2. Computes a delay = ``retry_count * retry_delay_base`` and sleeps.
        3. If ``retry_count > max_retries``, marks the task as permanently
           failed (unless in burn mode) and returns ``True`` (terminal).
        4. Otherwise, rotates the task back into ``self.queue`` at a position
           near the front (``insert_pos = min(retry_delay_base, queue_len)``)
           so it gets retried sooner than brand-new tasks, while also avoiding
           starvation of the rest of the queue.

        The rotation mechanism works by:
        - Rotating the deque left by ``insert_pos``,
        - Appending the task at the (now rightmost) end,
        - Rotating right by ``insert_pos`` to restore the original order,
          effectively inserting the task at index ``insert_pos``.

        Args:
            task: The failed extraction task.

        Returns:
            ``True`` if the task is terminal (max retries exceeded).
            ``False`` if it was re-queued for another retry attempt.
        """
        task.retry_count += 1
        # Linear backoff with 60-second ceiling
        delay = min(task.retry_count * self.model_config.retry_delay_base, 60)

        tqdm.write(
            f"[!] Task {task.task_id} failed, "
            f"retrying in {delay}s... (Attempt {task.retry_count})"
        )
        time.sleep(delay)

        with self._lock:
            self.stats.increment_retries()

            if task.retry_count >= self.model_config.max_retries:
                # ── Terminal failure ────────────────────────────────────
                if not self.burn_mode:
                    self.state_manager.mark_failed(
                        self.model_alias, task.task_id, task.retry_count,
                        error=f"Failed after {task.retry_count} retries"
                    )
                self.stats.increment_failed()
                return True

            # ── Re-queue with rotation for priority ─────────────────────
            # Insert near the front so the task is retried soon,
            # but behind `retry_delay_base` other tasks to keep the queue fair.
            insert_pos = min(self.model_config.retry_delay_base, len(self.queue))
            self.queue.rotate(-insert_pos)
            self.queue.appendleft(task)
            self.queue.rotate(insert_pos)
            return False


