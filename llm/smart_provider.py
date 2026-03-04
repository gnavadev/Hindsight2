"""
Smart Gemini Provider

Implements a multi-step reasoning process:
1. Classification & Extraction (using Vision)
2. Solution Generation (using structured context)
Uses TOON format for efficient context representation.
"""

import json
import re
import time
import logging
from typing import List, Dict, Any, Optional
from PIL import Image
from .gemini_provider import GeminiProvider
from .exceptions import LLMExtractionError, LLMGenerationError, LLMFormattingError

logger = logging.getLogger(__name__)

try:
    from .toon_formatter import (
        encode_toon,
        encode_problem_context,
        encode_extraction_hints,
        is_toon_available,
    )
except ImportError:
    logger.warning("toon_formatter missing, falling back to JSON.")
    def encode_toon(data): return json.dumps(data, indent=2)
    def encode_problem_context(problem_info, problem_type): return json.dumps(problem_info, indent=2)
    def encode_extraction_hints(user_prompt): return user_prompt
    def is_toon_available() -> bool: return False


class SmartGeminiProvider(GeminiProvider):
    def __init__(self, api_key: str, model_name: str = "gemini-3.1-flash-lite-preview"):
        super().__init__(api_key, model_name)
        logger.info(f"TOON support: {'active' if is_toon_available() else 'JSON fallback'}")

    def send_message(
        self,
        text: str,
        images: Optional[List[Image.Image]] = None,
        audio_file: Optional[str] = None,
        system_prompt: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Smart/Chain-of-Thought send message:
        1. If images provided, classify and extract problem info.
        2. Generate solution based on classification.
        3. Return formatted response.
        """
        if not images:
            logger.info("No images provided. Bypassing smart flow to standard routing.")
            return super().send_message(text, images, audio_file, system_prompt)

        pipeline_start = time.time()

        try:
            # ── Step 1: Classify & Extract ──────────────────────────
            logger.info(f"[{self._model_name}] Step 1: Extracting problem details...")
            t1 = time.time()
            problem_info = self._step_classify_and_extract(text, images)
            t1_elapsed = time.time() - t1

            if "error" in problem_info:
                raise LLMExtractionError(
                    f"Extraction returned error: {problem_info['error']}",
                )

            ptype = problem_info.get("problem_type", "unknown")
            logger.info(
                f"[{self._model_name}] Step 1 Complete ({t1_elapsed:.1f}s) -> "
                f"Type: {ptype} | Summary: {problem_info.get('problem_summary', '')[:80]}"
            )
            logger.debug(f"Step 1 full result: {json.dumps(problem_info, default=str)[:500]}")

            # ── Step 2: Generate Solution ───────────────────────────
            logger.info(f"[{self._model_name}] Step 2: Generating solution (type={ptype})...")
            t2 = time.time()
            solution_data = self._step_generate_solution(problem_info, images)
            t2_elapsed = time.time() - t2

            if "error" in solution_data:
                raise LLMGenerationError(
                    f"Generation returned error: {solution_data['error']}",
                )

            logger.info(f"[{self._model_name}] Step 2 Complete ({t2_elapsed:.1f}s)")
            logger.debug(f"Step 2 full result: {json.dumps(solution_data, default=str)[:500]}")

            # ── Step 3: Format ──────────────────────────────────────
            try:
                final_markdown = self._format_solution_markdown(solution_data, problem_info)
            except Exception as fmt_err:
                raise LLMFormattingError(
                    f"Formatting failed: {fmt_err}", original_error=fmt_err
                )

            total_elapsed = time.time() - pipeline_start
            logger.info(
                f"[{self._model_name}] Pipeline finished in {total_elapsed:.1f}s "
                f"(extract={t1_elapsed:.1f}s, solve={t2_elapsed:.1f}s)"
            )

            return {
                'response': final_markdown,
                'metadata': {
                    'model': self._model_name,
                    'problem_info': problem_info,
                    'raw_solution': solution_data,
                    'timing': {
                        'extraction_s': round(t1_elapsed, 2),
                        'generation_s': round(t2_elapsed, 2),
                        'total_s': round(total_elapsed, 2),
                    },
                }
            }

        except (LLMExtractionError, LLMGenerationError, LLMFormattingError) as e:
            logger.error(f"Smart pipeline error ({type(e).__name__}): {e}")
            logger.info("Falling back to standard (non-smart) generation...")
            return super().send_message(text, images, audio_file, system_prompt)

        except Exception as e:
            logger.error(f"Unexpected error in SmartProvider: {e}", exc_info=True)
            logger.info("Falling back to standard (non-smart) generation...")
            return super().send_message(text, images, audio_file, system_prompt)

    # ──────────────────────────────────────────────────────────────
    # Step 1: Classify & Extract
    # ──────────────────────────────────────────────────────────────

    def _step_classify_and_extract(self, user_prompt: str, images: List[Image.Image]) -> Dict[str, Any]:
        # Build the instruction using TOON-encoded hints for token efficiency
        hints_toon = encode_extraction_hints(user_prompt)

        system_instruction = f"""\
You are an expert analyst. Analyze the provided image(s) carefully.

CLASSIFICATION & EXTRACTION TASK (context in TOON):
{hints_toon}

STEP 1: CLASSIFY THE PROBLEM TYPE
Determine exactly ONE of: coding, multiple_choice, math, general

STEP 2: EXTRACT KEY DATA
- problem_summary: 1-2 sentence brief summary
- details: key entities (language, options, variables, constraints, etc.)

Return a valid JSON object. Do NOT transcribe the entire image text."""

        extraction_schema = {
            "type": "object",
            "properties": {
                "problem_type": {
                    "type": "string",
                    "enum": ["coding", "multiple_choice", "math", "general"],
                },
                "problem_summary": {
                    "type": "string",
                    "description": "1-2 sentence brief summary",
                },
                "details": {
                    "type": "object",
                    "description": "Extracted key entities relevant to the problem type",
                },
            },
            "required": ["problem_type", "problem_summary", "details"],
        }

        return self.send_with_json_output(
            text=system_instruction,
            json_schema=extraction_schema,
            images=images,
        )

    # ──────────────────────────────────────────────────────────────
    # Step 2: Generate Solution
    # ──────────────────────────────────────────────────────────────

    def _step_generate_solution(self, problem_info: Dict[str, Any], images: List[Image.Image]) -> Dict[str, Any]:
        ptype = problem_info.get("problem_type", "general")

        # Encode full classification context in TOON for token efficiency
        context_str = encode_problem_context(problem_info, ptype)

        role_prompt, output_desc, solution_schema = self._get_type_config(ptype)

        prompt = f"""\
{role_prompt}

CLASSIFICATION CONTEXT (TOON Format):
{context_str}

TASK:
Solve the problem shown in the image. Use the classification context above to focus your answer.
{output_desc}"""

        logger.debug(f"Step 2 prompt length: {len(prompt)} chars, schema keys: {list(solution_schema.get('properties', {}).get('solution', {}).get('properties', {}).keys())}")

        return self.send_with_json_output(
            text=prompt,
            json_schema=solution_schema,
            images=images,
        )

    def _get_type_config(self, ptype: str):
        """Return (role_prompt, output_desc, schema) for each problem type."""

        if ptype == "coding":
            role = "You are a Senior Software Engineer. Provide an optimal, complete code solution for the problem in the image."
            desc = (
                "Return strict JSON. CRITICAL RULES:\n"
                "- algorithm_steps: each step MUST be a SEPARATE array element. Never merge multiple steps into one string.\n"
                "- code: raw code only. Use \\n for newlines. Do NOT include markdown backticks.\n"
                "- edge_cases: each case as a SEPARATE array element."
            )
            schema = {
                "type": "object",
                "properties": {
                    "solution": {
                        "type": "object",
                        "properties": {
                            "algorithm_steps": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Each step as a SEPARATE element. Do NOT merge steps.",
                            },
                            "code": {
                                "type": "string",
                                "description": "Complete solution code. Use \\n for newlines. NO markdown backticks.",
                            },
                            "language": {
                                "type": "string",
                                "description": "Programming language used (e.g. python, java, cpp)",
                            },
                            "time_complexity": {"type": "string"},
                            "space_complexity": {"type": "string"},
                            "edge_cases": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Each edge case as a SEPARATE element.",
                            },
                        },
                        "required": ["algorithm_steps", "code", "language", "time_complexity", "space_complexity", "edge_cases"],
                    }
                },
            }

        elif ptype == "multiple_choice":
            role = "You are an expert tutor. Identify the correct answer from the multiple-choice options shown in the image."
            desc = (
                "Return strict JSON containing:\n"
                "- selected_option: the full text of the correct option\n"
                "- option_letter: the letter/number of the correct option (e.g. 'A', 'B', '1', '2')\n"
                "- all_options: object mapping each letter to its text\n"
                "- explanation: clear reasoning for why this is correct"
            )
            schema = {
                "type": "object",
                "properties": {
                    "solution": {
                        "type": "object",
                        "properties": {
                            "selected_option": {"type": "string"},
                            "option_letter": {"type": "string"},
                            "all_options": {
                                "type": "object",
                                "description": "Map of letter/number -> option text",
                            },
                            "explanation": {"type": "string"},
                        },
                        "required": ["selected_option", "option_letter", "explanation"],
                    }
                },
            }

        elif ptype == "math":
            role = "You are an expert mathematician. Solve the math problem shown in the image step-by-step."
            desc = (
                "Return strict JSON containing:\n"
                "- final_answer: the final numeric or symbolic answer\n"
                "- step_by_step: array where each element is one step of the solution\n"
                "- formula_used: key formula(s) applied"
            )
            schema = {
                "type": "object",
                "properties": {
                    "solution": {
                        "type": "object",
                        "properties": {
                            "final_answer": {"type": "string"},
                            "step_by_step": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Each step of the solution as a SEPARATE element.",
                            },
                            "formula_used": {"type": "string"},
                        },
                        "required": ["final_answer", "step_by_step"],
                    }
                },
            }

        else:  # general
            role = "You are a helpful expert assistant. Answer the question shown in the image clearly and thoroughly."
            desc = "Return strict JSON containing the answer and detailed reasoning."
            schema = {
                "type": "object",
                "properties": {
                    "solution": {
                        "type": "object",
                        "properties": {
                            "answer": {"type": "string"},
                            "reasoning": {"type": "string"},
                        },
                        "required": ["answer", "reasoning"],
                    }
                },
            }

        return role, desc, schema

    # ──────────────────────────────────────────────────────────────
    # Step 3: Format to Markdown
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

    # ── Coding Formatter ────────────────────────────────────────

    def _fmt_coding(self, sol: Dict, info: Dict) -> str:
        md = "**Type:** `CODING`\n\n"

        # Algorithm Steps — prefer the JSON array directly, regex as last resort
        steps = self._safe_list(sol, "algorithm_steps")
        if steps:
            md += "### Algorithm Steps\n\n"
            for i, step in enumerate(steps, 1):
                md += f"**{i}.** {step}<br>\n"
            md += "\n"

        # Code
        code = self._sanitize_code(sol.get("code", "") or sol.get("answer", ""))
        lang = (
            sol.get("language")
            or info.get("details", {}).get("language")
            or "python"
        )
        if code:
            md += f"### Code\n\n```{lang}\n{code}\n```\n\n"

        # Edge Cases
        edge_cases = self._safe_list(sol, "edge_cases")
        if edge_cases:
            md += "### Edge Cases\n\n"
            for ec in edge_cases:
                md += f"- {ec}\n"
            md += "\n"

        # Complexity
        tc = sol.get("time_complexity")
        sc = sol.get("space_complexity")
        if tc or sc:
            md += "### Complexity\n\n"
            if tc:
                md += f"**Time:** {tc}\n\n"
            if sc:
                md += f"**Space:** {sc}\n\n"

        return md.strip()

    # ── Multiple Choice Formatter ───────────────────────────────

    def _fmt_multiple_choice(self, sol: Dict, info: Dict) -> str:
        md = "**Type:** `MULTIPLE CHOICE`\n\n"

        letter = sol.get("option_letter", "?")
        selected = sol.get("selected_option", "")
        md += f"### Answer: **{letter}**\n\n"
        if selected:
            md += f"> {selected}\n\n"

        # Show all options if available
        all_opts = sol.get("all_options", {})
        if all_opts and isinstance(all_opts, dict):
            md += "### Options\n\n"
            for key in sorted(all_opts.keys()):
                marker = "✅" if str(key).upper() == str(letter).upper() else "⬚"
                md += f"{marker} **{key}.** {all_opts[key]}\n\n"

        explanation = sol.get("explanation", "")
        if explanation:
            md += f"### Explanation\n\n{explanation}\n\n"

        return md.strip()

    # ── Math Formatter ──────────────────────────────────────────

    def _fmt_math(self, sol: Dict, info: Dict) -> str:
        md = "**Type:** `MATH`\n\n"

        # Step-by-step
        steps = self._safe_list(sol, "step_by_step")
        if steps:
            md += "### Step-by-Step Solution\n\n"
            for i, step in enumerate(steps, 1):
                md += f"**{i}.** {step}<br>\n"
            md += "\n"

        # Final answer (highlighted)
        answer = sol.get("final_answer", "")
        if answer:
            md += f"### Final Answer\n\n**{answer}**\n\n"

        # Formula
        formula = sol.get("formula_used", "")
        if formula:
            md += f"### Formula Used\n\n{formula}\n\n"

        return md.strip()

    # ── General Formatter ───────────────────────────────────────

    def _fmt_general(self, sol: Dict, info: Dict) -> str:
        ptype = info.get("problem_type", "general").upper()
        md = f"**Type:** `{ptype}`\n\n"

        reasoning = sol.get("reasoning", "")
        answer = sol.get("answer", "")

        if reasoning:
            md += f"### Analysis\n\n{reasoning}\n\n"
        if answer:
            md += f"### Answer\n\n{answer}\n\n"

        return md.strip()

    # ── Raw Fallback ────────────────────────────────────────────

    def _format_raw_fallback(self, data: Dict, ptype: str) -> str:
        """Last-resort renderer — always shows *something* useful."""
        logger.debug(f"Raw fallback triggered for type={ptype}")
        md = f"**Type:** `{ptype.upper()}`\n\n"
        md += "### Response\n\n"

        # Try to extract any text-like value
        if isinstance(data, dict):
            for key in ("solution", "answer", "response", "text", "raw"):
                val = data.get(key)
                if val and isinstance(val, str):
                    md += val + "\n\n"
                    return md.strip()
                elif val and isinstance(val, dict):
                    # Dump the dict in a readable way
                    md += "```json\n" + json.dumps(val, indent=2, default=str) + "\n```\n\n"
                    return md.strip()

        md += "```json\n" + json.dumps(data, indent=2, default=str) + "\n```\n\n"
        return md.strip()

    # ── Helpers ─────────────────────────────────────────────────

    @staticmethod
    def _safe_list(data: Dict, key: str) -> List[str]:
        """
        Extract a list of strings from `data[key]`, handling common LLM
        output issues: merged steps, non-list values, nested numbering.
        """
        raw = data.get(key, [])
        if not raw:
            return []

        # If it's already a well-formed list with multiple elements, use as-is
        if isinstance(raw, list) and len(raw) > 1:
            return [str(s).strip() for s in raw if str(s).strip()]

        # Single-element list or string — the LLM may have merged everything
        combined = " ".join(str(s) for s in raw) if isinstance(raw, list) else str(raw)

        # Try to split on numbered patterns like "1. ", "2) ", "Step 3:"
        parts = re.split(r'(?:^|\s)(?:\d+[\.\)]\s+|Step\s+\d+[:\s])', combined)
        parts = [p.strip() for p in parts if p.strip()]

        # If splitting didn't help, keep the original as a single item
        if len(parts) <= 1:
            parts = [combined.strip()]

        # Clean leading bullets/dashes
        cleaned = []
        for p in parts:
            p = re.sub(r'^[\-\*•]\s*', '', p).strip()
            if p:
                cleaned.append(p)

        return cleaned

    @staticmethod
    def _sanitize_code(code: str) -> str:
        """
        Clean up code output from LLM:
        - Strip markdown backtick fences
        - Normalize escaped newlines to real newlines
        - Remove leading/trailing whitespace
        """
        if not code:
            return ""

        code = code.strip()

        # Remove wrapping ```lang ... ``` blocks
        code = re.sub(r'^```\w*\n?', '', code)
        code = re.sub(r'\n?```$', '', code)

        # Normalize escaped newlines (LLM sometimes returns literal \\n)
        code = code.replace('\\n', '\n')

        return code.strip()