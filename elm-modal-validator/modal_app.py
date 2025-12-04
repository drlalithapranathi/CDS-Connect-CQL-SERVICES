"""
ELM Validator using Llama on Modal
"""

import modal
import json

app = modal.App("elm-validator")

image = modal.Image.debian_slim().pip_install(
    "transformers", "torch", "accelerate", "sentencepiece", "fastapi", "requests"
)

MODEL_NAME = "meta-llama/Llama-3.2-1B-Instruct"
volume = modal.Volume.from_name("elm-cache", create_if_missing=True)


@app.function(
    image=image,
    gpu="T4",
    timeout=180,
    volumes={"/cache": volume},
    secrets=[modal.Secret.from_name("huggingface")]
)
@modal.web_endpoint(method="POST")
def validate(data: dict) -> dict:
    """
    Validate ELM JSON

    Input: {"elm_json": {...}, "library_name": "string"}
    Output: {"valid": bool, "errors": [], "warnings": []}
    """
    import torch
    import os
    from transformers import AutoTokenizer, AutoModelForCausalLM

    # Use HuggingFace token for gated models
    os.environ["HUGGING_FACE_HUB_TOKEN"] = os.getenv("HF_TOKEN")

    elm_json = data.get("elm_json")
    library_name = data.get("library_name", "Unknown")

    if not elm_json:
        return {
            "valid": False,
            "errors": ["No ELM JSON provided or fetched"],
            "warnings": [],
            "source": "error"
        }

    # Check for embedded CQL-to-ELM errors
    embedded_errors = extract_embedded_errors(elm_json)
    if embedded_errors:
        return {
            "valid": False,
            "errors": embedded_errors,
            "warnings": [],
            "source": "embedded"
        }

    # Load Llama model
    print(f"Loading {MODEL_NAME}...")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME, cache_dir="/cache")
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_NAME,
        torch_dtype=torch.float16,
        device_map="auto",
        cache_dir="/cache"
    )

    # Build validation prompt
    prompt = build_prompt(elm_json, library_name)

    # Generate response
    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
    outputs = model.generate(
        **inputs,
        max_new_tokens=300,
        temperature=0.1,
        do_sample=True,
        pad_token_id=tokenizer.eos_token_id
    )

    response_text = tokenizer.decode(outputs[0], skip_special_tokens=True)
    answer = response_text[len(prompt):].strip()

    # Parse Llama response
    result = parse_response(answer)

    print(f"Validation result for {library_name}: {result}")

    return result


def extract_embedded_errors(elm_json):
    """Extract errors from ELM annotations"""
    errors = []
    annotations = elm_json.get("library", {}).get("annotation", [])

    for ann in annotations:
        if ann.get("type") == "CqlToElmError" and ann.get("errorSeverity") == "error":
            msg = ann.get("message", "Unknown error")
            line = ann.get("startLine", "?")
            errors.append(f"{msg} (Line {line})")

    return errors


def build_prompt(elm_json, library_name):
    """Build Llama validation prompt"""
    library = elm_json.get("library", {})
    statements = library.get("statements", {}).get("def", [])

    stmt_names = [s.get("name") for s in statements if isinstance(s, dict)]

    summary = {
        "libraryName": library.get("identifier", {}).get("id", library_name),
        "version": library.get("identifier", {}).get("version", "unknown"),
        "statements": stmt_names[:10]
    }

    prompt = f"""You are a Clinical Quality Language (CQL) expert. Analyze this ELM clinical logic.

ELM Library:
{json.dumps(summary, indent=2)}

Check for LOGICAL ISSUES only:
- Contradictory conditions?
- Missing logic steps?
- Inappropriate recommendations?

DO NOT check syntax - already validated.

Respond EXACTLY:
VALID: YES or NO
ERRORS: List or "None"
WARNINGS: List or "None"

Your response:"""

    return prompt


def parse_response(response):
    """Parse Llama's structured response"""
    lines = [l.strip() for l in response.split('\n') if l.strip()]

    valid = True
    errors = []
    warnings = []
    section = None

    for line in lines:
        upper = line.upper()

        if upper.startswith('VALID:'):
            valid = 'YES' in upper
        elif upper.startswith('ERRORS:'):
            section = 'errors'
            content = line.split(':', 1)[1].strip()
            if content.lower() != 'none':
                errors.append(content)
        elif upper.startswith('WARNINGS:'):
            section = 'warnings'
            content = line.split(':', 1)[1].strip()
            if content.lower() != 'none':
                warnings.append(content)
        elif line.startswith('-') or line.startswith('*'):
            item = line[1:].strip()
            if item.lower() != 'none':
                if section == 'warnings':
                    warnings.append(item)
                elif section == 'errors':
                    errors.append(item)

    return {
        "valid": valid and len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "source": "llama"
    }


@app.function(image=image)
@modal.web_endpoint(method="GET")
def health():
    """Health check"""
    return {"status": "healthy", "model": MODEL_NAME}

