import os
import threading
import http.server
import socketserver
from playwright.sync_api import sync_playwright
from crewai import Agent, Task, Crew, LLM
from crewai.tools import tool

# 1. Seadistame tasuta LOKAALSE mudeli (Ollama kaudu)
local_llm = LLM(
    model="ollama/llama3.2",
    base_url="http://localhost:11434"
)

# 2. Lokaalne server (et Playwright saaks faili lugeda)
def start_server():
    port = 8000
    handler = http.server.SimpleHTTPRequestHandler
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("", port), handler)
    print(f"Server käivitatud aadressil http://localhost:{port}")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

# 3. UUS Playwright tööriist: Loeb koodi struktuuri, mitte pilti
@tool("Analüüsi HTML Struktuuri")
def analyze_dom_tool() -> str:
    """Käivitab lokaalse lehe http://localhost:8000/index.html ja tagastab selle HTML struktuuri analüüsiks."""
    print("Playwright: Loeb lehe struktuuri...")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto("http://localhost:8000/index.html", wait_until="networkidle")
            html_content = page.inner_html("body")
            browser.close()
        return f"Lehe renderdatud sisu on järgmine:\n{html_content}\nPalun hinda, kas kasutatud on õigeid Tailwind klasse ja kas kood on ligipääsetav (ARIA märgendid jms)."
    except Exception as e:
        return f"Viga lehe lugemisel: {e}. Kontrolli, kas arendaja lõi index.html faili."

start_server()

# 4. Agendid (kasutavad lokaalset LLM-i)
frontend_developer = Agent(
    role='Frontend Developer',
    goal='Write a modern HTML/CSS login form and save it to index.html.',
    backstory='You are an expert developer. You use Tailwind CSS via CDN. You ALWAYS save the code to "index.html". Write only code, no extra text.',
    llm=local_llm,
    allow_delegation=False,
    verbose=True
)

ux_evaluator = Agent(
    role='UX and Code Evaluator',
    goal='Review the generated HTML and Tailwind classes and provide feedback.',
    backstory='You are a technical UX designer. Use the "Analüüsi HTML Struktuuri" tool to read the DOM. Check for logical layouts (flex, grid), padding, and accessibility (aria-labels).',
    tools=[analyze_dom_tool],
    llm=local_llm,
    allow_delegation=True,
    verbose=True
)

# 5. Ülesanded
task1 = Task(
    description='Create a clean, modern dark mode login form. Use Tailwind CSS CDN. It must have email, password, and submit button. IMPORTANT: Return ONLY the raw HTML code. Do not wrap it in ```html markdown blocks. Do not add any conversational text.',
    expected_output='Raw HTML code for the login form without markdown formatting.',
    agent=frontend_developer,
    output_file='index.html' # See rida sunnib CrewAI-d tulemuse automaatselt faili salvestama!
)

task2 = Task(
    description='Evaluate the generated login form. You must write a short text report about its UI/UX and accessibility. IMPORTANT: Write a natural English text paragraph. DO NOT output JSON or function calls as your final answer.',
    expected_output='A professional text report in English evaluating the UI/UX and Tailwind structure.',
    agent=ux_evaluator
)

design_crew = Crew(
    agents=[frontend_developer, ux_evaluator],
    tasks=[task1, task2],
    verbose=True
)

print("Käivitan lokaalsed agendid...")
result = design_crew.kickoff()

print("\n\n=== LÕPPTULEMUS ===")
print(result)