# Student Assistant Website (`web/`)

Full web interface for the G12 Capstone Student Assistant, connected to a **local AI model** and your **AiPi-CAM-D200** prototype.

## How to Run (Step by Step)

1. **Start your local AI model** (for example, [Ollama](https://ollama.com)):
   - For text + camera vision, pull a vision model such as `ollama pull llama3.2-vision`, `ollama pull llava`, or `ollama pull gemma3:4b`.
   - Keep Ollama running (`http://localhost:11434`).
2. **Start the website** in PowerShell:
   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File d:\G12_Capstone\web\start.ps1
   ```
   This automatically uses Python (`server.py`) or Node.js (`server.mjs`) and opens `http://127.0.0.1:8080` in your browser.

## Main Sections

- **Workspace**:
  - **Chat Box**: Type requests anytime instead of using a microphone, or click the mic icon to speak.
  - **Mic Switcher**: Switch between **Laptop Mic** and **Prototype Mic**.
  - **Live Camera**: Switch between **Prototype Camera (AiPi-CAM-D200)** and **Laptop Camera**, freeze a frame, or capture and attach it to your AI message.
- **AI & Rules**:
  - Connect to **Ollama** or any **OpenAI-compatible local server** (like LM Studio).
  - Choose or type your local model, tune creativity/length/language, write custom rules, toggle quick study modes, and save multiple personalized profiles.
- **PDF Library**:
  - Drag and drop PDFs (or `.txt`/`.md` notes).
  - Toggle individual PDFs **ON / OFF** so the AI uses only the notes you want for your current subject.

## Hidden Admin Section (Component Test Lab)

To open the hidden **Admin Lab** and test each component separately (Prototype Camera, Laptop Camera, Laptop Mic, Prototype Mic, Speaker/TTS, Local AI Text & Vision, PDF Search, Local Bridge):

- Press **`Ctrl + Shift + A`** anywhere on the page, **OR**
- Click the **"Student Assistant" logo** in the top-left **5 times quickly**, **OR**
- Open `http://127.0.0.1:8080/#admin`.

Click **Hide Admin** (or press `Ctrl + Shift + A` again) to hide it cleanly during demos.
