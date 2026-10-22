"""Project-aware context, file tree, and per-finding fix hints."""
from pathlib import Path
from typing import Dict, List, Any
import re


def list_project_files(root: Path, page_relpath: str) -> List[str]:
    page_dir = (root / page_relpath).parent if page_relpath else root
    files = []
    for p in page_dir.rglob("*"):
        if p.is_file() and ".git" not in p.parts and p.suffix.lower() in {".html", ".css", ".js", ".svg", ".webp", ".png", ".jpg", ".jpeg"}:
            files.append(p.relative_to(root).as_posix())
    return sorted(files)[:50]


def file_tree(root: Path, max_items: int = 30) -> str:
    lines = []
    count = 0
    for p in sorted(root.rglob("*")):
        if ".git" in p.parts or not p.is_file():
            continue
        if p.suffix.lower() not in {".html", ".css", ".js", ".svg", ".webp", ".png", ".jpg"}:
            continue
        lines.append(p.relative_to(root).as_posix())
        count += 1
        if count >= max_items:
            lines.append("…")
            break
    return "\n".join(lines) or "(no project files)"


def extract_relevant_snippet(html: str, finding: Dict) -> str:
    issue = finding.get("issue", "").lower()
    if "alt" in issue or "image" in issue:
        return "\n".join(re.findall(r"<img[^>]*>", html, re.I)[:5]) or "(no img)"
    if "h1" in issue or "heading" in issue:
        return "\n".join(re.findall(r"<h[1-6][^>]*>.*?</h[1-6]>", html, re.I | re.S)[:6]) or "(no headings)"
    if "link" in issue or "anchor" in issue or "href" in issue:
        return "\n".join(re.findall(r"<a[^>]*href=[\"'][^\"']*[\"'][^>]*>", html, re.I)[:6]) or "(no anchors)"
    if "button" in issue:
        return "\n".join(re.findall(r"<button[^>]*>.*?</button>", html, re.I | re.S)[:4]) or "(no buttons)"
    if "viewport" in issue or "title" in issue or "lang" in issue:
        return "\n".join(re.findall(r"<(?:html|meta|title)[^>]*>", html, re.I)[:8]) or html[:400]
    return html[:500]


def build_finding_context(audit: Dict[str, Any], page_relpath: str, workspace: Path) -> str:
    if not audit or not audit.get("findings"):
        return "No findings."
    page_path = workspace / page_relpath
    html = page_path.read_text(encoding="utf-8", errors="ignore") if page_path.exists() else ""
    files = list_project_files(workspace, page_relpath)
    lines = ["**Project files:**", ", ".join(files[:15]) or "(none)", ""]
    for i, f in enumerate(audit["findings"], 1):
        lines.append(f"### {i}. [{f['severity'].upper()}] {f['category']} — {f['issue']}")
        if f.get("details"):
            lines.append(f"Details: {f['details']}")
        lines.append(f"Fix: {f['fix']}")
        if f.get("diff_hint"):
            lines.append(f"Example change: `{f['diff_hint']}`")
        snippet = extract_relevant_snippet(html, f)
        lines.append("```html")
        lines.append(snippet[:450])
        lines.append("```")
        lines.append("")
    return "\n".join(lines)
