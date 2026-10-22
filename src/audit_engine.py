"""Deterministic UX audit engine using Playwright + BeautifulSoup.
Improved accuracy for real multi-file projects (Bootstrap, local assets, CSS).
"""
import re
from pathlib import Path
from typing import Dict, List, Any, Optional
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup


def _is_decorative(img) -> bool:
    return img.get("aria-hidden") == "true" or img.get("role") == "presentation"


def _missing_alt(img) -> bool:
    if _is_decorative(img):
        return False
    alt = img.get("alt")
    return alt is None or str(alt).strip() == ""


def run_audit(url: str = "http://127.0.0.1:8000/index.html", workspace: Optional[Path] = None, page_relpath: str = "index.html") -> Dict[str, Any]:
    findings: List[Dict] = []
    score = 100
    metrics = {}

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.goto(url, wait_until="networkidle", timeout=12000)
            except Exception:
                page.goto(url, wait_until="domcontentloaded", timeout=8000)

            console_errors = []
            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
            try:
                page.reload(wait_until="domcontentloaded", timeout=8000)
            except Exception:
                pass

            if console_errors:
                findings.append({
                    "category": "JavaScript",
                    "severity": "high",
                    "issue": f"{len(console_errors)} console error(s) detected",
                    "details": "; ".join(console_errors[:3]),
                    "fix": "Check browser console and fix runtime errors.",
                    "diff_hint": "Open devtools and resolve the listed errors."
                })
                score -= 15

            html = page.content()
            soup = BeautifulSoup(html, "html.parser")
            text_content = soup.get_text(separator=" ", strip=True).lower()

            # Basic document checks
            if not soup.find("html", attrs={"lang": True}):
                findings.append({
                    "category": "Accessibility",
                    "severity": "medium",
                    "issue": "Missing lang attribute on <html>",
                    "fix": "Add lang=\"en\" (or the correct language) to the <html> tag.",
                    "diff_hint": "<html lang=\"en\">"
                })
                score -= 4

            if not soup.title or not soup.title.string or not soup.title.string.strip():
                findings.append({
                    "category": "Accessibility",
                    "severity": "medium",
                    "issue": "Missing or empty <title>",
                    "fix": "Add a descriptive <title>.",
                    "diff_hint": "<title>Page title</title>"
                })
                score -= 4

            if not soup.find("main"):
                findings.append({
                    "category": "Accessibility",
                    "severity": "low",
                    "issue": "No <main> landmark",
                    "fix": "Wrap primary content in <main>.",
                    "diff_hint": "<main>...</main>"
                })
                score -= 3

            # Viewport
            if not soup.find("meta", attrs={"name": "viewport"}):
                findings.append({
                    "category": "Responsive",
                    "severity": "high",
                    "issue": "Missing viewport meta tag",
                    "fix": "Add <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
                    "diff_hint": "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
                })
                score -= 10

            # Images – smarter alt + local asset existence
            images = soup.find_all("img")
            missing_alt = []
            broken_srcs = []
            missing_local = []
            placeholder_domains = ("placeholder.com", "via.placeholder", "placehold.it", "dummyimage.com", "placekitten")

            page_dir = None
            if workspace and page_relpath:
                page_dir = (workspace / page_relpath).parent

            for img in images:
                src = (img.get("src") or "").strip()
                if _missing_alt(img):
                    missing_alt.append(src or "(no src)")
                if not src or src in ("#", "/", "javascript:void(0)"):
                    broken_srcs.append(src or "(empty)")
                elif any(d in src.lower() for d in placeholder_domains):
                    broken_srcs.append(src)
                elif page_dir and not src.startswith(("http://", "https://", "data:")):
                    local = (page_dir / src).resolve()
                    if not local.exists():
                        missing_local.append(src)

            if missing_alt:
                findings.append({
                    "category": "Accessibility",
                    "severity": "medium",
                    "issue": f"{len(missing_alt)} image(s) missing meaningful alt text",
                    "details": ", ".join(missing_alt[:4]),
                    "fix": "Add descriptive alt text, or alt=\"\" + aria-hidden=\"true\" for decorative images.",
                    "diff_hint": "<img src=\"...\" alt=\"descriptive text\">"
                })
                score -= min(12, len(missing_alt) * 2)

            if broken_srcs:
                findings.append({
                    "category": "Content / Assets",
                    "severity": "high",
                    "issue": f"{len(broken_srcs)} image(s) have broken or placeholder src",
                    "details": ", ".join(broken_srcs[:4]),
                    "fix": "Replace with real image URLs or local assets.",
                    "diff_hint": "Replace src with a stable URL or local path."
                })
                score -= min(18, len(broken_srcs) * 5)

            if missing_local:
                findings.append({
                    "category": "Content / Assets",
                    "severity": "high",
                    "issue": f"{len(missing_local)} local asset(s) not found on disk",
                    "details": ", ".join(missing_local[:4]),
                    "fix": "Fix relative paths or add the missing files.",
                    "diff_hint": "Ensure the file exists relative to the HTML."
                })
                score -= min(15, len(missing_local) * 4)

            # Links – ignore pure in-page anchors
            anchors = soup.find_all("a")
            dead_links = []
            for a in anchors:
                href = (a.get("href") or "").strip()
                if href in ("", "javascript:void(0)") or href == "#":
                    dead_links.append(href or "(empty)")
            if dead_links:
                findings.append({
                    "category": "Interaction",
                    "severity": "low",
                    "issue": f"{len(dead_links)} link(s) are empty or only '#'",
                    "details": f"{len(dead_links)} anchor(s)",
                    "fix": "Point brand/logo links somewhere useful or remove href.",
                    "diff_hint": "Change href=\"#\" to a real section or remove it."
                })
                score -= min(6, len(dead_links) * 2)

            # Buttons – ignore Bootstrap data-api controls
            buttons = soup.find_all("button")
            dead_buttons = []
            for b in buttons:
                if b.get("onclick") or b.get("type") == "submit":
                    continue
                if b.get("data-bs-toggle") or b.get("data-bs-dismiss") or b.get("data-toggle"):
                    continue
                dead_buttons.append(b.get_text(strip=True) or "(unnamed)")
            if dead_buttons:
                findings.append({
                    "category": "Interaction",
                    "severity": "low",
                    "issue": f"{len(dead_buttons)} button(s) appear non-functional",
                    "details": ", ".join(dead_buttons[:3]),
                    "fix": "Add an action or use a link.",
                    "diff_hint": "Add onclick, type=\"submit\", or data-bs-toggle."
                })
                score -= 4

            # Headings
            headings = soup.find_all(re.compile(r"^h[1-6]$"))
            levels = [int(h.name[1]) for h in headings]
            h1_count = levels.count(1)
            if h1_count == 0:
                findings.append({
                    "category": "Accessibility",
                    "severity": "medium",
                    "issue": "No <h1> found on the page",
                    "fix": "Add a single main <h1>.",
                    "diff_hint": "Change the primary <h2> to <h1> or add one."
                })
                score -= 8
            elif h1_count > 1:
                findings.append({
                    "category": "Accessibility",
                    "severity": "medium",
                    "issue": f"Multiple <h1> elements ({h1_count})",
                    "fix": "Use only one <h1>.",
                    "diff_hint": "Demote extra <h1> to <h2>."
                })
                score -= 5
            if levels and levels[0] != 1:
                findings.append({
                    "category": "Accessibility",
                    "severity": "medium",
                    "issue": "Page does not start with an <h1>",
                    "fix": "Make the first heading an <h1>.",
                    "diff_hint": "Change the first heading to <h1>."
                })
                score -= 4
            skips = any(levels[i] > levels[i-1] + 1 for i in range(1, len(levels)))
            if skips:
                findings.append({
                    "category": "Accessibility",
                    "severity": "low",
                    "issue": "Heading level skips detected",
                    "fix": "Use sequential heading levels.",
                    "diff_hint": "Avoid jumping more than one level."
                })
                score -= 3

            # Overflow
            page.set_viewport_size({"width": 375, "height": 812})
            try:
                overflow = page.evaluate("() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 5")
            except Exception:
                overflow = False
            if overflow:
                findings.append({
                    "category": "Responsive",
                    "severity": "high",
                    "issue": "Horizontal overflow on mobile (375px)",
                    "fix": "Remove fixed widths or add overflow-x handling.",
                    "diff_hint": "Use max-width: 100% or flex-wrap."
                })
                score -= 12

            # Content density
            word_count = len(text_content.split())
            title = (soup.title.string or "").strip() if soup.title else ""
            if word_count < 8 and not title:
                findings.append({
                    "category": "System",
                    "severity": "high",
                    "issue": f"Empty or near-empty page read ({word_count} words)",
                    "details": "Wrong file may have been loaded.",
                    "fix": "Confirm the correct entry HTML is being served.",
                    "diff_hint": "Check page_relpath / ZIP structure."
                })
                score -= 25
            elif word_count < 40:
                findings.append({
                    "category": "Content Quality",
                    "severity": "high",
                    "issue": f"Very sparse content ({word_count} words)",
                    "fix": "Add real copy to major sections.",
                    "diff_hint": "Expand hero and feature text."
                })
                score -= 12
            elif word_count < 80:
                findings.append({
                    "category": "Content Quality",
                    "severity": "medium",
                    "issue": f"Thin content ({word_count} words)",
                    "fix": "Add more specific details.",
                    "diff_hint": "Expand section descriptions."
                })
                score -= 6

            # Color variety from custom CSS (ignore minified vendor)
            css_text = ""
            if page_dir:
                for css in page_dir.rglob("*.css"):
                    if ".git" in css.parts or "min.css" in css.name or "bootstrap" in css.name.lower():
                        continue
                    try:
                        css_text += css.read_text(encoding="utf-8", errors="ignore")[:5000]
                    except Exception:
                        pass
            color_source = html + css_text
            unique_colors = set(re.findall(r"#[0-9a-fA-F]{3,8}", color_source))
            if len(unique_colors) < 3 and "bootstrap" not in html.lower():
                findings.append({
                    "category": "Design / Visual",
                    "severity": "low",
                    "issue": "Very limited explicit color palette",
                    "details": f"{len(unique_colors)} unique hex colors in HTML + custom CSS",
                    "fix": "Add accent colors in custom CSS.",
                    "diff_hint": "Define a few theme colors in styles.css."
                })
                score -= 4

            # Unstable hosts
            all_urls = re.findall(r"https?://[^\s\"')]+", html)
            unstable_hosts = ("wallpaperflare.com", "alamy.com", "pinterest.com", "leonardo.ai", "dreamstime.com")
            unstable = [u for u in all_urls if any(h in u.lower() for h in unstable_hosts)]
            if unstable:
                findings.append({
                    "category": "Content / Assets",
                    "severity": "medium",
                    "issue": f"{len(unstable)} URL(s) from unstable or hotlink-protected hosts",
                    "details": ", ".join(unstable[:2]),
                    "fix": "Prefer images.unsplash.com or local files.",
                    "diff_hint": "Replace with a stable public image URL."
                })
                score -= min(8, len(unstable) * 3)

            metrics = {
                "images": len(images),
                "missing_alt": len(missing_alt),
                "word_count": word_count,
                "h1_count": h1_count,
                "unique_colors": len(unique_colors),
                "console_errors": len(console_errors),
            }
            browser.close()

    except Exception as e:
        findings.append({
            "category": "System",
            "severity": "high",
            "issue": f"Audit failed: {str(e)}",
            "fix": "Ensure the page is reachable.",
            "diff_hint": "Check the server URL and file path."
        })
        score = 0

    score = max(0, min(100, score))
    return {"score": score, "findings": findings, "metrics": metrics, "url": url}


def format_audit_for_llm(audit: Dict[str, Any]) -> str:
    lines = [f"UX Audit Score: {audit['score']}/100\n"]
    if not audit["findings"]:
        lines.append("No issues found by deterministic checks.")
    else:
        for f in audit["findings"]:
            lines.append(f"- [{f['severity'].upper()}] {f['category']}: {f['issue']}")
            if f.get("details"):
                lines.append(f"  Details: {f['details']}")
            lines.append(f"  Suggested fix: {f['fix']}")
    return "\n".join(lines)
