"""Deterministic UX audit engine using Playwright + BeautifulSoup.
Returns structured findings + score for the LLM to interpret.
Covers both accessibility/structure AND design/content quality signals.
"""
import re
from typing import Dict, List, Any
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup


def run_audit(url: str = "http://127.0.0.1:8000/index.html") -> Dict[str, Any]:
    findings: List[Dict] = []
    score = 100
    metrics = {}

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="networkidle", timeout=15000)

            # Console errors
            console_errors = []
            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
            page.reload(wait_until="networkidle")
            if console_errors:
                findings.append({
                    "category": "JavaScript",
                    "severity": "high",
                    "issue": f"{len(console_errors)} console error(s) detected",
                    "details": "; ".join(console_errors[:3]),
                    "fix": "Check browser console and fix runtime errors."
                })
                score -= 15

            html = page.content()
            soup = BeautifulSoup(html, "html.parser")
            text_content = soup.get_text(separator=" ", strip=True).lower()

            # Viewport meta
            viewport = soup.find("meta", attrs={"name": "viewport"})
            if not viewport:
                findings.append({
                    "category": "Responsive",
                    "severity": "high",
                    "issue": "Missing viewport meta tag",
                    "fix": "Add <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
                })
                score -= 10

            # Images: missing alt + broken/placeholder sources (including CSS backgrounds)
            images = soup.find_all("img")
            missing_alt = [img.get("src", "unknown") for img in images if not img.get("alt")]
            broken_srcs = []
            placeholder_domains = ("placeholder.com", "via.placeholder", "placehold.it", "dummyimage.com", "placekitten", "picsum.photos/placeholder")

            for img in images:
                src = (img.get("src") or "").strip()
                if not src or src in ("#", "/", "javascript:void(0)") or (not src.startswith("data:") and len(src) < 4):
                    broken_srcs.append(src or "(empty)")
                elif any(d in src.lower() for d in placeholder_domains):
                    broken_srcs.append(src)

            # Also scan inline styles and <style> blocks for placeholder backgrounds
            style_text = ""
            for style_tag in soup.find_all("style"):
                style_text += style_tag.get_text() + " "
            for tag in soup.find_all(True):
                if tag.get("style"):
                    style_text += tag["style"] + " "
            if any(d in style_text.lower() for d in placeholder_domains):
                findings.append({
                    "category": "Content / Assets",
                    "severity": "high",
                    "issue": "Placeholder image service detected in CSS (via.placeholder.com or similar)",
                    "details": "Background images use a known placeholder domain",
                    "fix": "Replace with real thematic image URLs (Unsplash, etc.) or pure CSS graphics."
                })
                score -= 18

            if missing_alt:
                findings.append({
                    "category": "Accessibility",
                    "severity": "medium",
                    "issue": f"{len(missing_alt)} image(s) missing alt attribute",
                    "details": ", ".join(missing_alt[:3]),
                    "fix": "Add descriptive alt text to every <img>."
                })
                score -= min(12, len(missing_alt) * 3)
            if broken_srcs:
                findings.append({
                    "category": "Content / Assets",
                    "severity": "high",
                    "issue": f"{len(broken_srcs)} image(s) have broken or placeholder src",
                    "details": ", ".join(broken_srcs[:4]),
                    "fix": "Replace placeholder srcs with real image URLs or local assets."
                })
                score -= min(20, len(broken_srcs) * 6)

            # Placeholder / Lorem ipsum text
            placeholder_patterns = ["lorem ipsum", "dolor sit amet", "consectetur adipiscing", "placeholder", "your text here"]
            found_placeholders = [p for p in placeholder_patterns if p in text_content]
            if found_placeholders:
                findings.append({
                    "category": "Content Quality",
                    "severity": "medium",
                    "issue": "Placeholder or Lorem ipsum text detected",
                    "details": ", ".join(found_placeholders),
                    "fix": "Replace dummy text with real, theme-appropriate copy."
                })
                score -= 10

            # Non-functional links and buttons
            anchors = soup.find_all("a")
            dead_links = [a.get("href", "") for a in anchors if (a.get("href") or "").strip() in ("#", "", "javascript:void(0)")]
            buttons = soup.find_all("button")
            dead_buttons = [b.get_text(strip=True) or "(unnamed)" for b in buttons if not b.get("onclick") and not b.get("type") == "submit"]
            if dead_links:
                findings.append({
                    "category": "Interaction",
                    "severity": "medium",
                    "issue": f"{len(dead_links)} link(s) point to '#' or are empty",
                    "details": f"{len(dead_links)} dead anchor(s)",
                    "fix": "Give links real destinations or remove them."
                })
                score -= min(12, len(dead_links) * 2)
            if dead_buttons and len(buttons) > 0:
                findings.append({
                    "category": "Interaction",
                    "severity": "low",
                    "issue": "Buttons appear non-functional (no onclick or form action)",
                    "fix": "Wire buttons to actions or make them links."
                })
                score -= 5

            # Form inputs without labels
            inputs = soup.find_all(["input", "textarea", "select"])
            unlabeled = []
            for inp in inputs:
                if inp.get("type") in ("hidden", "submit", "button"):
                    continue
                id_ = inp.get("id")
                if not id_ or not soup.find("label", attrs={"for": id_}):
                    if not inp.get("aria-label") and not inp.get("aria-labelledby"):
                        unlabeled.append(inp.get("name") or inp.get("placeholder") or "unnamed")
            if unlabeled:
                findings.append({
                    "category": "Accessibility",
                    "severity": "high",
                    "issue": f"{len(unlabeled)} form control(s) without associated label",
                    "details": ", ".join(unlabeled[:3]),
                    "fix": "Use <label for=\"id\"> or aria-label."
                })
                score -= min(18, len(unlabeled) * 5)

            # Heading hierarchy + multiple h1
            headings = soup.find_all(re.compile(r"^h[1-6]$"))
            levels = [int(h.name[1]) for h in headings]
            h1_count = levels.count(1)
            if h1_count == 0:
                findings.append({
                    "category": "Accessibility",
                    "severity": "medium",
                    "issue": "No <h1> found on the page",
                    "fix": "Add a single main <h1> that describes the page."
                })
                score -= 8
            elif h1_count > 1:
                findings.append({
                    "category": "Accessibility / Structure",
                    "severity": "medium",
                    "issue": f"Multiple <h1> elements ({h1_count})",
                    "fix": "Use only one <h1> per page; demote others to <h2>."
                })
                score -= 6
            if levels and levels[0] != 1:
                findings.append({
                    "category": "Accessibility",
                    "severity": "medium",
                    "issue": "Page does not start with an <h1>",
                    "fix": "Ensure the main heading is an <h1>."
                })
                score -= 5
            skips = [levels[i] for i in range(1, len(levels)) if levels[i] > levels[i-1] + 1]
            if skips:
                findings.append({
                    "category": "Accessibility",
                    "severity": "low",
                    "issue": "Heading level skips detected",
                    "fix": "Use sequential heading levels (h1 → h2 → h3)."
                })
                score -= 4

            # Horizontal overflow check (mobile)
            page.set_viewport_size({"width": 375, "height": 812})
            overflow = page.evaluate("""() => {
                return document.documentElement.scrollWidth > document.documentElement.clientWidth + 5;
            }""")
            if overflow:
                findings.append({
                    "category": "Responsive",
                    "severity": "high",
                    "issue": "Horizontal overflow on mobile (375px)",
                    "fix": "Check fixed widths, use max-w-full, overflow-x-hidden, or flex-wrap."
                })
                score -= 12

            # Empty / wrong-file detection (only flag truly empty pages)
            word_count = len(text_content.split())
            title = (soup.title.string or "").strip() if soup.title else ""
            if word_count < 8 and not title:
                findings.append({
                    "category": "System",
                    "severity": "high",
                    "issue": f"Empty or near-empty page read ({word_count} words)",
                    "details": "The served page has almost no content. This usually means the wrong file was loaded.",
                    "fix": "Confirm the correct index.html path is being served."
                })
                score -= 25

            # Unstable / hotlink-protected image hosts
            unstable_hosts = ("wallpaperflare.com", "alamy.com", "pinterest.com", "leonardo.ai", "dreamstime.com", "shutterstock.com")
            all_urls = re.findall(r"https?://[^\s\"')]+", html)
            unstable = [u for u in all_urls if any(h in u.lower() for h in unstable_hosts)]
            if unstable:
                findings.append({
                    "category": "Content / Assets",
                    "severity": "medium",
                    "issue": f"{len(unstable)} image URL(s) from unstable or hotlink-protected hosts",
                    "details": ", ".join(unstable[:3]),
                    "fix": "Prefer stable public hosts such as images.unsplash.com or local assets."
                })
                score -= min(10, len(unstable) * 3)

            # Content density
            if word_count < 40:
                findings.append({
                    "category": "Content Quality",
                    "severity": "high",
                    "issue": f"Very sparse content ({word_count} words)",
                    "details": "Page has almost no real text",
                    "fix": "Add meaningful copy for each section (hero subtext, feature descriptions, CTA)."
                })
                score -= 15
            elif word_count < 80:
                findings.append({
                    "category": "Content Quality",
                    "severity": "medium",
                    "issue": f"Thin content ({word_count} words)",
                    "fix": "Expand sections with specific, thematic details."
                })
                score -= 8

            # Suspicious or likely-broken external image URLs (Leonardo private, etc.)
            all_urls = re.findall(r"https?://[^\s\"')]+", html)
            suspicious = [u for u in all_urls if any(x in u.lower() for x in ["leonardo.ai/generation", "app.leonardo", "placeholder", "via.placeholder"])]
            if suspicious:
                findings.append({
                    "category": "Content / Assets",
                    "severity": "high",
                    "issue": "Likely inaccessible or temporary image URL(s) detected",
                    "details": ", ".join(suspicious[:2]),
                    "fix": "Use stable public image URLs (Unsplash, etc.) or pure CSS."
                })
                score -= 12

            # Try to detect failed image loads via Playwright
            try:
                failed_images = page.evaluate("""() => {
                    const imgs = Array.from(document.images);
                    return imgs.filter(img => !img.complete || img.naturalWidth === 0).map(img => img.src);
                }""")
                if failed_images:
                    findings.append({
                        "category": "Content / Assets",
                        "severity": "high",
                        "issue": f"{len(failed_images)} image(s) failed to load",
                        "details": ", ".join(failed_images[:2]),
                        "fix": "Replace broken image sources."
                    })
                    score -= min(15, len(failed_images) * 5)
            except Exception:
                pass

            # Low color contrast approximation (text vs common dark backgrounds)
            # Simple check: if body text is very light on very light or very dark on dark without enough difference
            body_style = soup.find("body")
            if body_style and "color:" in (body_style.get("style") or "") and "#fff" in (body_style.get("style") or "").lower() and "#1" in (body_style.get("style") or ""):
                pass  # acceptable dark theme
            # More useful: flag if almost no visual variety
            unique_colors = len(set(re.findall(r"#[0-9a-fA-F]{3,6}", html)))
            if unique_colors < 4:
                findings.append({
                    "category": "Design / Visual",
                    "severity": "medium",
                    "issue": "Very limited color palette (low visual variety)",
                    "fix": "Introduce accent colors that support the theme."
                })
                score -= 6

            metrics = {
                "images": len(images),
                "broken_images": len(broken_srcs),
                "form_controls": len(inputs),
                "headings": len(headings),
                "h1_count": h1_count,
                "dead_links": len(dead_links),
                "console_errors": len(console_errors),
                "mobile_overflow": bool(overflow),
                "placeholder_text": bool(found_placeholders),
            }

            browser.close()

    except Exception as e:
        findings.append({
            "category": "System",
            "severity": "high",
            "issue": f"Audit failed: {str(e)}",
            "fix": "Ensure the page is served correctly at the URL."
        })
        score = 0

    score = max(0, min(100, score))
    return {
        "score": score,
        "findings": findings,
        "metrics": metrics,
        "url": url
    }


def format_audit_for_llm(audit: Dict[str, Any]) -> str:
    lines = [f"UX Audit Score: {audit['score']}/100\n"]
    if not audit["findings"]:
        lines.append("No structural or content issues found by the deterministic checks.")
    else:
        for f in audit["findings"]:
            lines.append(f"- [{f['severity'].upper()}] {f['category']}: {f['issue']}")
            if f.get("details"):
                lines.append(f"  Details: {f['details']}")
            lines.append(f"  Suggested fix: {f['fix']}")
    lines.append("\nNote: This score reflects structural, accessibility, and obvious content/asset issues. Design taste, atmosphere, and theme strength still need human/LLM judgment.")
    return "\n".join(lines)