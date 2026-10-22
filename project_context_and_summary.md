# Project Context: Autonomous AI Web Design Agent

## 1. Project Purpose & Goal
The goal of this project is to build an autonomous, multi-agent AI system capable of designing, writing, and evaluating web interfaces (HTML/Tailwind CSS). This was developed as a school project to demonstrate the orchestration of AI agents, prompt engineering, and local LLM deployment. 

The system operates 100% locally and free of charge, ensuring absolute data privacy and independence from paid APIs. It is wrapped in an interactive web UI for demonstration purposes.

## 2. Technology Stack
*   **Framework:** CrewAI (for agent orchestration and task management)
*   **LLM Provider:** Ollama running Llama 3.2 (Local, 3B parameter model)
*   **Web Inspection:** Playwright (for headless DOM inspection)
*   **User Interface:** Streamlit (Python-based web app framework)
*   **Styling:** Tailwind CSS (via CDN in generated HTML)
*   **Language:** Python

## 3. System Architecture & Workflow
The system features a two-agent setup working in a loop:

1.  **User Input:** The user provides a natural language prompt via the Streamlit UI (e.g., "Create a dark mode login form") or uploads an existing `index.html` file to be modified.
2.  **Frontend Developer Agent:** 
    *   Takes the prompt (and existing code context, if any) and generates raw HTML/CSS.
    *   Crucial constraint: It is prompted to output *only* raw HTML without Markdown formatting (no ```html tags) so the output is immediately executable.
    *   Saves the output directly to a local `index.html` file.
3.  **Local HTTP Server:** A background Python HTTP server temporarily hosts the generated `index.html`.
4.  **UX Evaluator Agent:** 
    *   Instead of using unreliable vision models to "look" at the page, this agent uses a custom Playwright tool to visit the local HTTP server, scrape the rendered DOM, and read the actual HTML structure.
    *   Evaluates the code for UI/UX best practices, proper use of Tailwind classes, and accessibility (ARIA tags).
    *   Outputs a final text-based evaluation report.
5.  **Streamlit Output:** The UI displays the live rendered HTML preview, the generated code, and the UX report in separate tabs.

## 4. Key Challenges & Solutions
During development, several critical challenges were solved regarding small local LLMs:
*   **Tool Calling / JSON Leaks:** Smaller local models (like Llama 3.2) often struggle with tool calling, sometimes outputting internal JSON reasoning (e.g., `{"name":"analyze_dom_tool"...}`) as their final answer. **Solution:** Strict prompt engineering was applied, explicitly instructing the evaluator agent to output plain English text and prohibiting JSON output.
*   **Hallucinations & Formatting:** The developer agent kept wrapping code in Markdown blocks, which broke the live preview. **Solution:** Enforced strict output constraints in the CrewAI task descriptions and utilized CrewAI's `output_file` parameter to force the raw output safely into `index.html`.
*   **Evaluating Visuals with Text Models:** Vision models were too slow or inaccurate. **Solution:** Pivoted to a text-based evaluation strategy using Playwright to feed the exact HTML structure back to the LLM, which proved highly effective for checking Tailwind layouts and accessibility.

## 5. Codebase (To be inserted)

### app.py (Main Streamlit & CrewAI Logic)
[USER: PASTE YOUR APP.PY CODE HERE]

### requirements.txt (Dependencies)
[USER: PASTE YOUR REQUIREMENTS.TXT HERE]