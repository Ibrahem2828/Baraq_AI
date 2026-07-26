from __future__ import annotations

import ast
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main() -> int:
    errors: list[str] = []
    python_files = list(ROOT.rglob("*.py"))
    for path in python_files:
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            errors.append(f"Syntax error in {path.relative_to(ROOT)}: {exc}")

    required = [
        ".env.example",
        "Dockerfile",
        "docker-compose.yml",
        "app/main.py",
        "app/prompts/templates/fahes_generate_quiz.yaml",
        "docs/BACKEND_INTEGRATION_AR.md",
    ]
    for name in required:
        if not (ROOT / name).exists():
            errors.append(f"Missing required file: {name}")

    secret_pattern = re.compile(r"sk-[A-Za-z0-9_-]{20,}")
    for path in ROOT.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".py", ".md", ".yaml", ".yml", ".toml", ".example"}:
            text = path.read_text(encoding="utf-8", errors="ignore")
            if secret_pattern.search(text):
                errors.append(f"Possible OpenAI secret found in {path.relative_to(ROOT)}")

    result = {
        "python_files": len(python_files),
        "errors": errors,
        "status": "failed" if errors else "passed",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
