from __future__ import annotations

import ast
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
EXCLUDED_DIRECTORIES = {
    ".baraq_lab",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "venv",
}


def is_project_file(path: pathlib.Path) -> bool:
    return not any(part in EXCLUDED_DIRECTORIES for part in path.relative_to(ROOT).parts)


def main() -> int:
    errors: list[str] = []
    python_files = [path for path in ROOT.rglob("*.py") if is_project_file(path)]
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
    supported_suffixes = {".py", ".md", ".yaml", ".yml", ".toml", ".example"}
    for path in ROOT.rglob("*"):
        if is_project_file(path) and path.is_file() and path.suffix.lower() in supported_suffixes:
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
