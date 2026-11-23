# Student assistant project: notes for the coding agent

One workspace, three parts. Work only in the folder the user names.

| Folder | What it is |
| --- | --- |
| `firmware/` | Code for the AiPi-CAM-D200 board (Bouffalo BL616, GC2145 camera). **Read `firmware/AGENTS.md` before touching anything here.** |
| `ai/` | The AI part (planned: a program that runs on the user's PC or in the cloud). Empty so far. |
| `web/` | The website the student sees. Empty so far. |

## Status
- The board part works: it builds, flashes, joins Wi-Fi and streams video to a browser (tested by the user).
- The overall design is **not decided yet**. A proposal was made: the board sends pictures over Wi-Fi, a program in `ai/` asks an AI model, and the website in `web/` shows the answer. The board is too small to run a real AI model. Do not treat this as final; ask the user before building on it.
- The user has not yet said what the student assistant should do. Do not invent features.

## Rules
- Paths must not contain spaces or non-English characters (a space once broke the firmware build). Use names like `G12_Capstone`.
- Build and flash the board with the tasks in `.vscode/tasks.json` (Ctrl+Shift+B builds). Flashing needs the user to put the board in download mode by hand; never flash without asking.
- Keep each part inside its own folder. Do not move files between `firmware/`, `ai/` and `web/` without asking.
- Never write Wi-Fi names, passwords or API keys into source files. Use a local file that is not committed.
- The user prefers short, simple, step-by-step explanations. Explain what you changed in plain words.
