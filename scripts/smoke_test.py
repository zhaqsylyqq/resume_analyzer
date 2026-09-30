"""One real Gemini call to verify GEMINI_API_KEY and GEMINI_MODEL.

Usage:
    python scripts/smoke_test.py               # structured JSON round-trip
    python scripts/smoke_test.py --list-models # list models available to the key

The key is read from the environment (or .env) and is never printed.
"""
from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types
from pydantic import BaseModel


class Ping(BaseModel):
    ok: bool
    language: str


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list-models", action="store_true")
    args = parser.parse_args()

    load_dotenv()
    api_key = os.environ.get("GEMINI_API_KEY")
    model = os.environ.get("GEMINI_MODEL")
    if not api_key:
        print("FAIL: GEMINI_API_KEY is not set (check .env)")
        return 1

    client = genai.Client(api_key=api_key)

    try:
        if args.list_models:
            for m in client.models.list():
                actions = getattr(m, "supported_actions", None) or []
                if "generateContent" in actions:
                    print(m.name)
            return 0

        if not model:
            print("FAIL: GEMINI_MODEL is not set (check .env)")
            return 1

        response = client.models.generate_content(
            model=model,
            contents='Reply with ok=true and the ISO code of the language of: "Сәлеметсіз бе"',
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=Ping,
                temperature=0,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            ),
        )
        ping = Ping.model_validate_json(response.text)
    except errors.APIError as exc:
        print(f"FAIL: Gemini API error {exc.code}: {exc.message}")
        return 1

    print(f"OK: model={model} response={ping.model_dump()}")
    return 0 if ping.ok else 1


if __name__ == "__main__":
    sys.exit(main())
