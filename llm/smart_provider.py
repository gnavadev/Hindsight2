"""
Smart LLM Provider

Single-pass classify + solve on top of any backend that implements
send_with_json_output. One request, one image upload.

Uses TOON for compact prompt context.
"""

import json
import re
import time
import logging
from typing import List, Dict, Any, Optional, Generator
from PIL import Image

from llm.base_provider import BaseLLMProvider
from llm.exceptions import LLMGenerationError, LLMFormattingError
from llm.image_prep import annotate_pages, prepare_image_for_llm

logger = logging.getLogger(__name__)

try:
    from .toon_formatter import (
        encode_toon,
        is_toon_available,
    )
except ImportError:
    logger.warning("toon_formatter missing, falling back to JSON.")

    def encode_toon(data):
        return json.dumps(data, separators=(",", ":"))

    def is_toon_available() -> bool:
        return False


# One schema for classify+solve. Sending the screenshot twice (old 2-step
# pipeline) was the main token sink; this keeps the same output shape.
UNIFIED_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "problem_type": {
            "type": "string",
            "enum": ["coding", "multiple_choice", "math", "general"],
        },
        "problem_summary": {"type": "string"},
        "details": {"type": "object"},
        "solution": {
            "type": "object",
            "properties": {
                "understanding": {"type": "string"},
                "brute_force": {"type": "string"},
                "key_insight": {"type": "string"},
                "algorithm_steps": {"type": "array", "items": {"type": "string"}},
                "code": {"type": "string"},
                "dry_run": {"type": "string"},
                "follow_ups": {"type": "array", "items": {"type": "string"}},
                "language": {"type": "string"},
                "time_complexity": {"type": "string"},
                "space_complexity": {"type": "string"},
                "edge_cases": {"type": "array", "items": {"type": "string"}},
                "selected_option": {"type": "string"},
                "option_letter": {"type": "string"},
                "all_options": {"type": "object"},
                "explanation": {"type": "string"},
                "final_answer": {"type": "string"},
                "step_by_step": {"type": "array", "items": {"type": "string"}},
                "formula_used": {"type": "string"},
                "answer": {"type": "string"},
                "reasoning": {"type": "string"},
            },
        },
    },
    "required": ["problem_type", "problem_summary", "solution"],
}


def _single_pass_prompt(user_text: str, image_count: int = 1) -> str:
    hints = encode_toon({
        "task": "classify_and_solve",
        "user_note": user_text,
        "image_count": image_count,
        "types": {
            "coding": "code, IDE, terminal, algorithm",
            "multiple_choice": "lettered/numbered options",
            "math": "equations, numeric/symbolic answer",
            "general": "open-ended / other",
        },
        "solution_fields": {
            "coding": [
                "understanding: restate the problem, inputs/outputs, constraints and assumptions (1-3 sentences)",
                "brute_force: the naive approach, its complexity, and why it is not good enough (1-2 sentences)",
                "key_insight: the core idea behind the optimal solution and WHY it is correct (2-4 sentences)",
                "algorithm_steps[]: short imperative steps",
                "code",
                "language",
                "dry_run: trace the code on one small example, step by step (2-4 short sentences)",
                "time_complexity: Big-O plus a short justification, e.g. 'O(n): one pass, O(1) hash lookups'",
                "space_complexity: Big-O plus a short justification",
                "edge_cases[]: each case AND how the code handles it",
                "follow_ups[]: up to 3 likely interviewer follow-ups or trade-offs, each with a one-line answer",
            ],
            "multiple_choice": ["selected_option", "option_letter", "all_options{}", "explanation"],
            "math": ["final_answer", "step_by_step[]", "formula_used"],
            "general": ["answer", "reasoning"],
        },
        "rules": [
            "Fill only fields for the chosen type",
            "coding: explain the way a strong candidate talks an interviewer through a "
            "technical interview, showing they grasp every concept: clear, first-person, "
            "concise sentences, no filler",
            "code: raw, no markdown fences, \\n for newlines",
            "arrays: one item per step/case, never merge",
            "Do not transcribe the whole image",
            "If multiple images, they are sequential pages of ONE problem — use all of them",
        ],
    })
    if image_count > 1:
        lead = (
            f"You are given {image_count} images, labeled Page 1 of {image_count} "
            f"through Page {image_count} of {image_count}. They are sequential pages "
            "of the SAME problem (for example a function that spans two screens). "
            "Read every page before solving. Do not ignore earlier pages.\n"
        )
    else:
        lead = "Analyze the image. Classify the problem, then solve it.\n"
    return lead + hints


class SmartProvider(BaseLLMProvider):
    """Single-pass smart pipeline wrapping any vision-capable backend."""

    def __init__(self, backend: BaseLLMProvider):
        api_key = getattr(backend, "api_key", "") or ""
        super().__init__(api_key)
        self.backend = backend
        self._model_name = backend.model_name
        logger.info(
            f"Smart single-pass pipeline on {self._model_name}; "
            f"TOON={'active' if is_toon_available() else 'JSON fallback'}"
        )

    def stream_response(
        self,
        text: str,
        images: Optional[List[Image.Image]] = None,
        audio_file: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> Generator[str, Any, None]:
        return self.backend.stream_response(text, images, audio_file, system_prompt)

    def send_with_json_output(
        self,
        text: str,
        json_schema: Dict[str, Any],
        images: Optional[List[Image.Image]] = None,
    ) -> Dict[str, Any]:
        return self.backend.send_with_json_output(text, json_schema, images)

    def transcribe_audio(self, audio_path: str) -> str:
        return self.backend.transcribe_audio(audio_path)

    def send_message(
        self,
        text: str,
        images: Optional[List[Image.Image]] = None,
        audio_file: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not images:
            logger.info("No images provided. Bypassing smart flow to standard routing.")
            return self.backend.send_message(text, images, audio_file, system_prompt)

        prepared = [prepare_image_for_llm(img) for img in images]
        prepared = annotate_pages(prepared)
        pipeline_start = time.time()

        try:
            logger.info(
                f"[{self._model_name}] Single-pass classify+solve "
                f"({len(prepared)} image(s))..."
            )
            result = self.backend.send_with_json_output(
                text=_single_pass_prompt(text, len(prepared)),
                json_schema=UNIFIED_SCHEMA,
                images=prepared,
            )

            if not result or "error" in result:
                raise LLMGenerationError(
                    f"Single-pass returned error: {(result or {}).get('error', 'empty result')}"
                )

            problem_info = {
                "problem_type": result.get("problem_type", "general"),
                "problem_summary": result.get("problem_summary", ""),
                "details": result.get("details") or {},
            }
            solution_data = {"solution": result.get("solution") or {}}
            ptype = problem_info["problem_type"]
            elapsed = time.time() - pipeline_start
            logger.info(
                f"[{self._model_name}] Single-pass complete ({elapsed:.1f}s) "
                f"-> Type: {ptype} | Summary: {problem_info.get('problem_summary', '')[:80]}"
            )

            try:
                final_markdown = self._format_solution_markdown(solution_data, problem_info)
            except Exception as fmt_err:
                raise LLMFormattingError(
                    f"Formatting failed: {fmt_err}", original_error=fmt_err
                )

            metadata: Dict[str, Any] = {
                "model": self._model_name,
                "problem_info": problem_info,
                "raw_solution": solution_data,
                "timing": {"total_s": round(elapsed, 2), "requests": 1},
            }
            if result.get("_usage"):
                metadata["usage"] = result["_usage"]
                logger.info(f"[{self._model_name}] tokens: {result['_usage']}")

            return {"response": final_markdown, "metadata": metadata}

        except (LLMGenerationError, LLMFormattingError) as e:
            logger.error(f"Smart pipeline error ({type(e).__name__}): {e}")
            logger.info("Falling back to standard (non-smart) generation...")
            return self.backend.send_message(text, prepared, audio_file, system_prompt)

        except Exception as e:
            logger.error(f"Unexpected error in SmartProvider: {e}", exc_info=True)
            logger.info("Falling back to standard (non-smart) generation...")
            return self.backend.send_message(text, prepared, audio_file, system_prompt)

    # ──────────────────────────────────────────────────────────────
    # Format to Markdown
    # ──────────────────────────────────────────────────────────────

    def _format_solution_markdown(self, solution_data: Dict[str, Any], problem_info: Dict[str, Any]) -> str:
        """
        Dispatch to a type-specific formatter.  Falls back to raw-dump if
        the structured data is missing or malformed.
        """
        sol = solution_data.get("solution", {})
        ptype = problem_info.get("problem_type", "general")

        if not sol:
            logger.warning("No 'solution' key in LLM output — rendering raw response")
            return self._format_raw_fallback(solution_data, ptype)

        try:
            formatters = {
                "coding": self._fmt_coding,
                "multiple_choice": self._fmt_multiple_choice,
                "math": self._fmt_math,
                "general": self._fmt_general,
            }
            formatter = formatters.get(ptype, self._fmt_general)
            return formatter(sol, problem_info)
        except Exception as e:
            logger.error(f"Formatter crashed for type={ptype}: {e}", exc_info=True)
            return self._format_raw_fallback(solution_data, ptype)

    @staticmethod
    def _numbered(items: List[str]) -> str:
        return "".join(f"{i}. {item}\n" for i, item in enumerate(items, 1)) + "\n"

    @staticmethod
    def _bulleted(items: List[str]) -> str:
        return "".join(f"- {item}\n" for item in items) + "\n"

    def _fmt_coding(self, sol: Dict, info: Dict) -> str:
        # Interview order: understand -> naive -> insight -> plan -> code -> verify
        md = ""
        for key, title in (
            ("understanding", "Understanding the problem"),
            ("brute_force", "Brute force"),
            ("key_insight", "Key insight"),
        ):
            text = (sol.get(key) or "").strip()
            if text:
                md += f"### {title}\n\n{text}\n\n"

        steps = self._safe_list(sol, "algorithm_steps")
        if steps:
            md += "### Approach\n\n" + self._numbered(steps)

        code = self._sanitize_code(sol.get("code", "") or sol.get("answer", ""))
        lang = (
            sol.get("language")
            or info.get("details", {}).get("language")
            or "python"
        )
        if code:
            md += f"### Code\n\n```{lang}\n{code}\n```\n\n"

        dry_run = (sol.get("dry_run") or "").strip()
        if dry_run:
            md += f"### Dry run\n\n{dry_run}\n\n"

        tc = sol.get("time_complexity")
        sc = sol.get("space_complexity")
        if tc or sc:
            md += "### Complexity\n\n"
            md += self._bulleted(
                ([f"**Time:** {tc}"] if tc else []) + ([f"**Space:** {sc}"] if sc else [])
            )

        edge_cases = self._safe_list(sol, "edge_cases")
        if edge_cases:
            md += "### Edge cases\n\n" + self._bulleted(edge_cases)

        follow_ups = self._safe_list(sol, "follow_ups")
        if follow_ups:
            md += "### Possible follow-ups\n\n" + self._bulleted(follow_ups)

        return md.strip()

    def _fmt_multiple_choice(self, sol: Dict, info: Dict) -> str:
        md = ""

        letter = sol.get("option_letter", "?")
        selected = sol.get("selected_option", "")
        md += f"### Answer: **{letter}**\n\n"
        if selected:
            md += f"> {selected}\n\n"

        all_opts = sol.get("all_options", {})
        if all_opts and isinstance(all_opts, dict):
            md += "### Options\n\n"
            for key in sorted(all_opts.keys()):
                marker = "✅" if str(key).upper() == str(letter).upper() else "⬚"
                md += f"- {marker} **{key}.** {all_opts[key]}\n"
            md += "\n"

        explanation = sol.get("explanation", "")
        if explanation:
            md += f"### Explanation\n\n{explanation}\n\n"

        return md.strip()

    def _fmt_math(self, sol: Dict, info: Dict) -> str:
        md = ""

        steps = self._safe_list(sol, "step_by_step")
        if steps:
            md += "### Step-by-step solution\n\n" + self._numbered(steps)

        answer = sol.get("final_answer", "")
        if answer:
            md += f"### Final Answer\n\n**{answer}**\n\n"

        formula = sol.get("formula_used", "")
        if formula:
            md += f"### Formula Used\n\n{formula}\n\n"

        return md.strip()

    def _fmt_general(self, sol: Dict, info: Dict) -> str:
        md = ""

        reasoning = sol.get("reasoning", "")
        answer = sol.get("answer", "")

        if reasoning:
            md += f"### Analysis\n\n{reasoning}\n\n"
        if answer:
            md += f"### Answer\n\n{answer}\n\n"

        return md.strip()

    def _format_raw_fallback(self, data: Dict, ptype: str) -> str:
        """Last-resort renderer — always shows *something* useful."""
        logger.debug(f"Raw fallback triggered for type={ptype}")
        md = ""

        if isinstance(data, dict):
            for key in ("solution", "answer", "response", "text", "raw"):
                val = data.get(key)
                if val and isinstance(val, str):
                    md += val + "\n\n"
                    return md.strip()
                elif val and isinstance(val, dict):
                    md += "```json\n" + json.dumps(val, indent=2, default=str) + "\n```\n\n"
                    return md.strip()

        md += "```json\n" + json.dumps(data, indent=2, default=str) + "\n```\n\n"
        return md.strip()

    @staticmethod
    def _safe_list(data: Dict, key: str) -> List[str]:
        raw = data.get(key, [])
        if not raw:
            return []

        if isinstance(raw, list) and len(raw) > 1:
            return [str(s).strip() for s in raw if str(s).strip()]

        combined = " ".join(str(s) for s in raw) if isinstance(raw, list) else str(raw)

        parts = re.split(r'(?:^|\s)(?:\d+[\.\)]\s+|Step\s+\d+[:\s])', combined)
        parts = [p.strip() for p in parts if p.strip()]

        if len(parts) <= 1:
            parts = [combined.strip()]

        cleaned = []
        for p in parts:
            p = re.sub(r'^[\-\*•]\s*', '', p).strip()
            if p:
                cleaned.append(p)

        return cleaned

    @staticmethod
    def _sanitize_code(code: str) -> str:
        if not code:
            return ""

        code = code.strip()
        code = re.sub(r'^```\w*\n?', '', code)
        code = re.sub(r'\n?```$', '', code)
        code = code.replace('\\n', '\n')
        return code.strip()


class SmartGeminiProvider(SmartProvider):
    """Back-compat wrapper: Gemini backend + single-pass smart pipeline."""

    def __init__(self, api_key: str, model_name: str = "gemini-3.1-flash-lite-preview"):
        from llm.gemini_provider import GeminiProvider
        super().__init__(GeminiProvider(api_key, model_name))
