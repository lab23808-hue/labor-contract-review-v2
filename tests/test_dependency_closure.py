import ast
from pathlib import Path

DOMAIN = Path(__file__).resolve().parents[1] / "src" / "domain"
FORBIDDEN_ROOTS = {
    "openai",
    "dotenv",
    "fastapi",
    "httpx",
    "requests",
    "mineru",
    "docx",
    "pypdf",
    "api",
    "app",
    "infra",
}


def _import_roots(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.extend(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.append(node.module.split(".")[0])
    return roots


def test_domain_has_no_external_sdk_imports():
    offenders = []
    for path in DOMAIN.rglob("*.py"):
        for root in _import_roots(path):
            if root in FORBIDDEN_ROOTS:
                offenders.append(
                    f"{path.relative_to(DOMAIN)} -> {root}"
                )
    assert offenders == []
