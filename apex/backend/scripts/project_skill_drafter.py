from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SKILLS_ROOT = ROOT / "model_context" / "project-skills"
REGISTRY = SKILLS_ROOT / "registry.json"


def slugify(value: str) -> str:
    return "-".join(part for part in "".join(ch.lower() if ch.isalnum() else "-" for ch in value).split("-") if part)


def ensure_registry() -> dict:
    if REGISTRY.exists():
        return json.loads(REGISTRY.read_text(encoding="utf-8"))
    payload = {"version": 1, "workflow": {"mode": "auto-draft-review-promote"}, "skills": []}
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a repo-local project skill draft.")
    parser.add_argument("--slug", required=True)
    parser.add_argument("--description", required=True)
    parser.add_argument("--summary", default="")
    parser.add_argument("--status", default="draft")
    args = parser.parse_args()

    slug = slugify(args.slug)
    if not slug:
        raise SystemExit("slug resolved to empty")

    skill_dir = SKILLS_ROOT / slug
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.exists():
        skill_file.write_text(
            "\n".join(
                [
                    "---",
                    f"name: {slug}",
                    f"description: {args.description}",
                    "---",
                    "",
                    f"# {slug}",
                    "",
                    "## Purpose",
                    args.summary or args.description,
                    "",
                    "## Draft Notes",
                    "- Replace this section with the stable workflow once the draft has been reused and validated.",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    registry = ensure_registry()
    skills = [item for item in registry.get("skills", []) if item.get("slug") != slug]
    skills.append(
        {
            "slug": slug,
            "status": args.status,
            "summary": args.summary or args.description,
        }
    )
    registry["skills"] = sorted(skills, key=lambda item: str(item.get("slug") or ""))
    REGISTRY.write_text(json.dumps(registry, indent=2), encoding="utf-8")
    print(str(skill_file))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
