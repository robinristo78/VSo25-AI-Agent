"""Project-aware context builder for audit findings.
Attaches relevant source snippets and related files so the agent can give concrete, file-level advice.
"""
from pathlib import Path
from typing import Dict, List, Any
import re


def list_project_files(root: Path, page_relpath: str) -> List[str]:
    page_dir = (root / page_relpath).parent
    files = []
    for p in page_dir.rglob("*"):
        if p.is_file() and ".git" not in p.parts and p.suffix.lower() in {".html", ".css", ".js", ".svg", ".webp", ".png", ".jpg", ".jpeg"}:
            files.append(p.relative_to(root).as_posix())
    return sorted(files)[:40]


def extract_relevant_snippet(html: str, finding: Dict) -> str:
    """Pull a short, relevant HTML snippet for a given finding."""
    issue = finding.get("issue", "").lower()
    category = finding.get("category", "").lower()

    if "alt" in issue or "image" in issue:
        imgs = re.findall(r"<img[^>]*>", html, re.I)
        return "\n".join(imgs[:4]) or "(no <img> tags found)"
    if "h1" in issue or "heading" in issue:
        heads = re.findall(r"<h[1-6][^>]*>.*?</h[1-6]>", html, re.I | re.S)
        return "\n".join(heads[:5]) or "(no headings found)"
    if "link" in issue or "anchor" in issue or "href" in issue:
        links = re.findall(r"<a[^>]*href=[\"'][^\"']*[\"'][^>]*>", html, re.I)
        return "\n".join(links[:5]) or "(no anchors found)"
    if "button" in issue:
        btns = re.findall(r"<button[^>]*>.*?</button>", html, re.I | re.S)
        return "\n".join(btns[:3]) or "(no buttons found)"
    if "viewport" in issue:
        metas = re.findall(r"<meta[^>]*>", html, re.I)
        return "\n".join(metas[:6]) or "(no meta tags)"
    # default: first 600 chars of body-ish content
    return html[:600]


def build_finding_context(audit: Dict[str, Any], page_relpath: str, workspace: Path) -> str:
    if not audit or not audit.get("findings"):
        return "No findings to expand."

    page_path = workspace / page_relpath
    html = page_path.read_text(encoding="utf-8", errors="ignore") if page_path.exists() else ""
    project_files = list_project_files(workspace, page_relpath)

    lines = [f"**Project files near the page** ({len(project_files)} shown):"]
    lines.append(", ".join(project_files[:12]) or "(none)")
    lines.append("")

    for i, f in enumerate(audit["findings"], 1):
        lines.append(f"### Finding {i}: [{f['severity'].upper()}] {f['category']} — {f['issue']}")
        if f.get("details"):
            lines.append(f"**Details:** {f['details']}")
        lines.append(f"**Suggested direction:** {f['fix']}")
        snippet = extract_relevant_snippet(html, f)
        lines.append("**Relevant code:**")
        lines.append(f"```html\n{snippet[:500]}\n```")
        lines.append("")
    return "\n".join(lines)
