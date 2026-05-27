"""Generic YAML frontmatter + markdown body parsing for artifacts (skills, prompts, tool defs)."""

from pathlib import Path


def load_artifact(path: Path) -> dict:
    """Load a markdown artifact file and parse its YAML frontmatter + body.

    Returns:
        {
            "path": Path,
            "raw": str,
            "frontmatter": str (YAML between --- markers),
            "body": str (markdown after frontmatter),
            "name": str,
            "description": str,
        }
    """
    raw = path.read_text()

    frontmatter = ""
    body = raw
    if raw.strip().startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            frontmatter = parts[1].strip()
            body = parts[2].strip()

    name = ""
    description = ""
    for line in frontmatter.split("\n"):
        if line.strip().startswith("name:"):
            name = line.split(":", 1)[1].strip().strip("'\"")
        elif line.strip().startswith("description:"):
            description = line.split(":", 1)[1].strip().strip("'\"")

    return {
        "path": path,
        "raw": raw,
        "frontmatter": frontmatter,
        "body": body,
        "name": name,
        "description": description,
    }


def reassemble_artifact(frontmatter: str, body: str) -> str:
    """Reassemble a markdown artifact from frontmatter and body.

    Preserves the original YAML frontmatter and replaces only the body.
    """
    return f"---\n{frontmatter}\n---\n\n{body}\n"
