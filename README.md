# xv6 AI-Powered Debugger

An automated kernel debugging tool that uses GDB, QEMU, and Gemini AI to analyze xv6 kernel panics and suggest fixes.

## Features

- 🤖 AI-powered root cause analysis using Google Gemini
- 🔍 Automatic backtrace and register capture
- 🛠️ Suggested code fixes for kernel panics
- 📊 Severity assessment of crashes

## Prerequisites

- Python 3.8+
- QEMU (`qemu-system-x86_64`)
- Cross-compiler GDB (`x86_64-elf-gdb`)
- xv6 operating system source code
- Google Gemini API key

### macOS Installation
```bash
brew install qemu
brew install x86_64-elf-gdb
```

## Setup

1. Clone this repository
2. Create a virtual environment:
```bash
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Get a Gemini API key from [Google AI Studio](https://aistudio.google.com/)

5. Add your API key to `debugger.py`:
```python
API_KEY = "your-api-key-here"
```

## Usage
```bash
python3 debugger.py
```

The debugger will:
1. Build xv6 and the test program
2. Start QEMU with GDB
3. Run `trap_test` to trigger a kernel panic
4. Capture debug data
5. Analyze with AI and suggest fixes

## Project Structure
```
xv6-ai-debugger/
├── debugger.py          # Main debugger script
├── requirements.txt     # Python dependencies
├── README.md           # This file
└── .gitignore          # Git ignore rules
```

## Example Output
```
================================================================================
🤖 AI-ASSISTED ROOT CAUSE ANALYSIS
================================================================================
Root Cause: NULL function pointer dereference at address 0x0
Severity: High

Analysis Summary:
The system experienced a page fault (vector 14) while attempting to execute 
code at virtual address 0x0...

Suggested Fix/Patch:
// Validate function pointers before calling them
if (my_handler == NULL) {
    panic("NULL function pointer called!");
}
```

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

MIT License
