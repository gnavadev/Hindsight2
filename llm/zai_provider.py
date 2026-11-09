"""
Z.AI LLM Provider

OpenAI-compatible client for Z.AI (https://api.z.ai/api/paas/v4/).
Default model: glm-5.3-flash (native multimodal).
"""

import base64
import io
import json
import logging
from typing import Any, Dict, Generator, List, Optional

from PIL import Image

from llm.base_provider import BaseLLMProvider
from llm.image_prep import prepare_image_for_llm

logger = logging.getLogger(__name__)

ZAI_BASE_URL = "https://api.z.ai/api/paas/v4/"
DEFAULT_MODEL = "glm-5.3-flash"
ASR_MODEL = "glm-asr-2512"  # speech-to-text; max 30 s of audio per request


class ZaiProvider(BaseLLMProvider):
    """Z.AI API provider (GLM models)."""

    def __init__(
        self,
        api_key: str,
        model_name: str = DEFAULT_MODEL,
        base_url: str = ZAI_BASE_URL,
        reasoning_effort: str = "high",
    ):
        super().__init__(api_key)
        self._model_name = model_name
        self.base_url = base_url.rstrip("/") + "/"
        self.reasoning_effort = reasoning_effort

        if not api_key or not api_key.strip():
            logger.error("Z.AI API key is empty or None")
            raise ValueError("Z.AI API key is empty or None.")

        try:
            from openai import OpenAI
            self.client = OpenAI(
                api_key=api_key,
                base_url=self.base_url,
                default_headers={"Accept-Language": "en-US,en"},
            )
            logger.info(
                f"Z.AI provider initialized: model={model_name}, "
                f"reasoning_effort={reasoning_effort}"
            )
        except Exception as e:
            logger.critical(f"Failed to initialize Z.AI client: {e}")
            raise

    def _image_to_data_url(self, image: Image.Image) -> str:
        prepared = prepare_image_for_llm(image)
        buf = io.BytesIO()
        prepared.save(buf, format="JPEG", quality=85, optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        return f"data:image/jpeg;base64,{b64}"

    def _build_user_content(
        self,
        text: str,
        images: Optional[List[Image.Image]] = None,
        audio_file: Optional[str] = None,
    ) -> Any:
        if not images and not audio_file:
            return text or ""

        content: List[Dict[str, Any]] = []
        if images:
            logger.info(f"Attaching {len(images)} image(s) to Z.AI request")
            for img in images:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": self._image_to_data_url(img)},
                })
        if audio_file:
            logger.warning(
                f"Audio file '{audio_file}' provided but Z.AI chat path "
                "does not attach audio here"
            )
            content.append({
                "type": "text",
                "text": f"[Audio file provided: {audio_file}]",
            })
        if text:
            content.append({"type": "text", "text": text})
        return content

    def transcribe_audio(self, audio_path: str) -> str:
        """GLM-ASR accepts at most 30 s per request, so transcribe 25 s chunks
        in parallel and join them in order."""
        from concurrent.futures import ThreadPoolExecutor
        from capture.system_audio import split_wav

        chunks = split_wav(audio_path, max_seconds=25.0)
        if not chunks:
            logger.info("Recording is silent; nothing to transcribe")
            return ""
        logger.info(f"Transcribing {len(chunks)} audio chunk(s) with {ASR_MODEL}")

        def one(item):
            i, data = item
            result = self.client.audio.transcriptions.create(
                model=ASR_MODEL, file=(f"chunk{i}.wav", data, "audio/wav"),
            )
            return (getattr(result, "text", "") or "").strip()

        with ThreadPoolExecutor(max_workers=4) as pool:
            texts = list(pool.map(one, enumerate(chunks)))
        return " ".join(t for t in texts if t)

    def _extra_body(self) -> Dict[str, Any]:
        # GLM-5.3-Flash cannot disable thinking; effort is the cost knob.
        # "high" is cheaper than the default "max" without dropping quality
        # on screenshot solve tasks.
        return {
            "thinking": {"type": "enabled", "clear_thinking": True},
            "reasoning_effort": self.reasoning_effort,
        }

    def _usage_meta(self, response) -> Dict[str, Any]:
        usage = getattr(response, "usage", None)
        meta: Dict[str, Any] = {"model": self._model_name}
        if usage is None:
            return meta
        meta["usage"] = {
            "input_tokens": getattr(usage, "prompt_tokens", None),
            "output_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        }
        details = getattr(usage, "prompt_tokens_details", None)
        if details is not None:
            cached = getattr(details, "cached_tokens", None)
            if cached is not None:
                meta["usage"]["cached_tokens"] = cached
        return meta

    def send_message(
        self,
        text: str,
        images: Optional[List[Image.Image]] = None,
        audio_file: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            messages: List[Dict[str, Any]] = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({
                "role": "user",
                "content": self._build_user_content(text, images, audio_file),
            })

            logger.info(f"Sending request to Z.AI ({self._model_name})...")
            response = self.client.chat.completions.create(
                model=self._model_name,
                messages=messages,
                temperature=0.7,
                max_tokens=8192,
                extra_body=self._extra_body(),
            )

            choice = response.choices[0]
            content = choice.message.content or ""
            meta = self._usage_meta(response)
            meta["finish_reason"] = choice.finish_reason
            logger.info(
                f"Z.AI response received — finish={choice.finish_reason}, "
                f"usage={meta.get('usage')}"
            )
            return {"response": content, "metadata": meta}
        except Exception as e:
            return self._error_result(e)

    def stream_response(
        self,
        text: str,
        images: Optional[List[Image.Image]] = None,
        audio_file: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> Generator[str, Any, None]:
        try:
            messages: List[Dict[str, Any]] = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({
                "role": "user",
                "content": self._build_user_content(text, images, audio_file),
            })

            logger.info("Initiating Z.AI stream...")
            stream = self.client.chat.completions.create(
                model=self._model_name,
                messages=messages,
                temperature=0.7,
                max_tokens=8192,
                stream=True,
                extra_body=self._extra_body(),
            )
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                piece = getattr(delta, "content", None)
                if piece:
                    yield piece
        except Exception as e:
            logger.exception(f"Z.AI streaming error: {e}")
            yield f"\n\nError connecting to Z.AI: {str(e)}"

    def send_with_json_output(
        self,
        text: str,
        json_schema: Dict[str, Any],
        images: Optional[List[Image.Image]] = None,
    ) -> Dict[str, Any]:
        """
        Request JSON matching schema. Compact schema in the prompt (no pretty
        indent) plus json_object mode when the API accepts it.
        """
        schema_str = json.dumps(json_schema, separators=(",", ":"))
        prompt = (
            f"{text}\n\n"
            "Respond with ONLY valid JSON matching this schema. "
            "No markdown fences.\n"
            f"{schema_str}"
        )
        messages = [{
            "role": "user",
            "content": self._build_user_content(prompt, images),
        }]

        kwargs: Dict[str, Any] = {
            "model": self._model_name,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 8192,
            "response_format": {"type": "json_object"},
            "extra_body": self._extra_body(),
        }

        try:
            logger.info("Sending JSON structured request to Z.AI...")
            response = self.client.chat.completions.create(**kwargs)
        except Exception as first_err:
            # Vision payloads sometimes reject response_format; retry without it.
            logger.warning(
                f"Z.AI json_object mode failed ({first_err}); retrying without it"
            )
            kwargs.pop("response_format", None)
            try:
                response = self.client.chat.completions.create(**kwargs)
            except Exception as e:
                logger.exception(f"Z.AI JSON output error: {e}")
                return {"error": str(e)}

        try:
            raw_text = response.choices[0].message.content or ""
            usage = self._usage_meta(response).get("usage")
            logger.debug(
                f"Z.AI JSON response length={len(raw_text)} usage={usage}"
            )
            parsed = self.parse_structured_output(raw_text)
            if usage and isinstance(parsed, dict) and "error" not in parsed:
                parsed["_usage"] = usage
            return parsed
        except Exception as e:
            logger.exception(f"Z.AI JSON parse error: {e}")
            return {"error": str(e)}

    def _error_result(self, e: Exception) -> Dict[str, Any]:
        error_type = type(e).__name__
        msg = str(e)
        logger.exception(f"Z.AI API error ({error_type}): {e}")

        user_msg = f"Error connecting to Z.AI: {msg}"
        lowered = msg.lower()
        if "401" in msg or "403" in msg or "api key" in lowered or "unauthorized" in lowered:
            user_msg = "Z.AI authentication failed. Please check your API key."
        elif "429" in msg or "rate" in lowered:
            user_msg = "Z.AI rate limit reached. Please wait a moment and try again."
        elif "timeout" in lowered:
            user_msg = "Z.AI request timed out. Please try again."

        return {
            "response": user_msg,
            "metadata": {"error": msg, "error_type": error_type},
        }
