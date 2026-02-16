"""
Smart Gemini Provider

Implements a multi-step reasoning process:
1. Classification & Extraction (using Vision)
2. Solution Generation (using structured context)
Uses TOON format for efficient context representation.
"""

import json
from typing import List, Dict, Any, Optional
from PIL import Image
from .gemini_provider import GeminiProvider
try:
    from .toon_formatter import encode_toon
except ImportError:
    # Fallback if toon_formatter is missing or fails
    def encode_toon(data): return json.dumps(data, indent=2)

class SmartGeminiProvider(GeminiProvider):
    def __init__(self, api_key: str, model_name: str = "gemini-2.5-flash-lite"):
        super().__init__(api_key, model_name)
    
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
        # If no images, fall back to standard behavior for now
        if not images:
            return super().send_message(text, images, audio_file, system_prompt)

        try:
            # Step 1: Classification & Extraction
            problem_info = self._step_classify_and_extract(text, images)
            
            # Step 2: Generate Solution
            solution_data = self._step_generate_solution(problem_info)
            
            # Step 3: Format Response to Markdown
            final_markdown = self._format_solution_markdown(solution_data, problem_info)
            
            return {
                'response': final_markdown,
                'metadata': {
                    'model': self._model_name,
                    'problem_info': problem_info,
                    'raw_solution': solution_data
                }
            }
            
        except Exception as e:
            print(f"SmartProvider Error: {e}")
            # Fallback to standard simple prompt if smart flow fails
            return super().send_message(text, images, audio_file, system_prompt)

    def _step_classify_and_extract(self, user_prompt: str, images: List[Image.Image]) -> Dict[str, Any]:
        """Classify image content and extract details into JSON"""
        
        system_instruction = """
        You are an expert analyst. Analyze the provided image(s).
        
        STEP 1: CLASSIFY THE PROBLEM TYPE
        - 'coding': Code snippets, IDEs, programming errors.
        - 'multiple_choice': Quizzes with options.
        - 'math': Equations, calculus, geometry.
        - 'general': Text questions, logic, etc.
        
        STEP 2: EXTRACT DATA
        Return a valid JSON object with:
        {
          "problem_type": "...",
          "problem_statement": "Full extracted text of the problem",
          "details": {
             "language": "python/js/etc (if coding)",
             "code_snippet": "extracted code (if coding)",
             "options": [] (if multiple choice),
             "context": "..." 
          }
        }
        """
        
        # We use a specialized schema for the extraction to ensure valid JSON
        extraction_schema = {
            "type": "object",
            "properties": {
                "problem_type": {"type": "string", "enum": ["coding", "multiple_choice", "math", "general"]},
                "problem_statement": {"type": "string"},
                "details": {"type": "object"}
            },
            "required": ["problem_type", "problem_statement", "details"]
        }

        # Use the base provider's JSON capability
        response = self.send_with_json_output(
            text=f"{system_instruction}\n\nUser Note: {user_prompt}",
            json_schema=extraction_schema,
            images=images
        )
        
        return response

    def _step_generate_solution(self, problem_info: Dict[str, Any]) -> Dict[str, Any]:
        """Generate solution based on problem info"""
        
        ptype = problem_info.get("problem_type", "general")
        
        # Encode context using TOON for efficiency
        context_str = encode_toon(problem_info)
        
        if ptype == "coding":
            role_prompt = "You are a Senior Software Engineer. Provide an optimal code solution."
            schema_keys = ["algorithm_steps", "code", "time_complexity", "space_complexity", "edge_cases"]
            output_desc = (
                "Return JSON with these exact keys:\n"
                "- 'algorithm_steps': Clear, numbered steps of the algorithm.\n"
                "- 'code': The full solution code (no markdown fences in JSON).\n"
                "- 'time_complexity': Big O notation with brief explanation.\n"
                "- 'space_complexity': Big O notation with brief explanation.\n"
                "- 'edge_cases': A list of edge cases considered.\n"
            )
            
            solution_schema = {
                "type": "object",
                "properties": {
                    "solution": {
                        "type": "object",
                        "properties": {
                            "algorithm_steps": {"type": "string"},
                            "code": {"type": "string"},
                            "time_complexity": {"type": "string"},
                            "space_complexity": {"type": "string"},
                            "edge_cases": {
                                "type": "array",
                                "items": {"type": "string"}
                            }
                        },
                        "required": ["algorithm_steps", "code", "time_complexity", "space_complexity", "edge_cases"]
                    }
                }
            }
        else:
            # Fallback for non-coding types (math, general)
            role_prompt = "You are a helpful expert assistant."
            output_desc = "Return JSON: { 'solution': { 'answer': '...', 'reasoning': '...' } }"
            solution_schema = {
                "type": "object",
                "properties": {
                    "solution": {
                        "type": "object",
                        "properties": {
                            "answer": {"type": "string"},
                            "reasoning": {"type": "string"}
                        },
                        "required": ["answer", "reasoning"]
                    }
                }
            }

        prompt = f"""
        {role_prompt}
        
        PROBLEM CONTEXT (TOON Format):
        {context_str}
        
        TASK:
        Solve the problem above.
        {output_desc}
        """
        
        response = self.send_with_json_output(
            text=prompt,
            json_schema=solution_schema
        )
        
        return response

    def _format_solution_markdown(self, solution_data: Dict[str, Any], problem_info: Dict[str, Any]) -> str:
        """Convert structured solution to Markdown"""
        
        sol = solution_data.get("solution", {})
        ptype = problem_info.get("problem_type", "general")
        
        markdown_out = ""
        # Badge
        markdown_out += f"**Type:** `{ptype.upper()}`\n\n"

        if ptype == "coding":
            # 1. Algorithm Steps
            steps = sol.get("algorithm_steps", "")
            if steps:
                markdown_out += f"### Algorithm Steps\n{steps}\n\n"
            elif sol.get("reasoning"): # Fallback
                markdown_out += f"### Algorithm Steps\n{sol.get('reasoning')}\n\n"
            
            # 2. Edge Cases (Swapped with Complexity)
            edge_cases = sol.get("edge_cases", [])
            if edge_cases:
                markdown_out += "### Edge Cases\n"
                for ec in edge_cases:
                    markdown_out += f"- {ec}\n"
                markdown_out += "\n"

            # 3. Code
            code = sol.get("code", "")
            if not code: code = sol.get("answer", "") # Fallback
            
            if code:
                # Ensure wrapping
                lang = problem_info.get("details", {}).get("language", "python")
                if not code.strip().startswith("```"):
                    code = f"``` {lang}\n{code}\n```"
                markdown_out += f"### Code Section\n{code}\n\n"
            
            # 4. Complexity (Swapped with Edge Cases)
            tc = sol.get("time_complexity")
            sc = sol.get("space_complexity")
            if tc or sc:
                markdown_out += "### Complexity\n"
                if tc: markdown_out += f"- **Time**: {tc}\n"
                if sc: markdown_out += f"- **Space**: {sc}\n"
                markdown_out += "\n"
        
        else:
            # Standard formatting for other types
            answer = sol.get("answer", "")
            reasoning = sol.get("reasoning", "")
            
            if reasoning:
                markdown_out += f"### Analysis\n{reasoning}\n\n"
            if answer:
                markdown_out += f"### Solution\n{answer}"
        
        return markdown_out
