import pexpect
import time
import subprocess
import os
import sys
import json
import requests

# --- Configuration ---
GDB_PORT = 26000
GDB_COMMAND = "x86_64-elf-gdb" 
API_KEY = os.getenv('GEMINI_API_KEY', '')
if not API_KEY:
    print("ERROR: Please set GEMINI_API_KEY environment variable")
    sys.exit(1)
API_URL_BASE = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"

# --- LLM API Helpers ---

def get_gemini_analysis(prompt, debug_data):
    """Calls the Gemini API to analyze the kernel panic data."""
    print("--- Sending data to Gemini for analysis... ---")
    
    # Using structured output for clear, machine-readable results
    system_prompt = (
        "You are an expert operating systems debugger, specializing in xv6 and C/Assembly. "
        "Analyze the provided kernel state (GDB Backtrace and Register Dump) that resulted "
        "from a page fault (vector 14). Determine the root cause (e.g., NULL pointer "
        "dereference, stack overflow, bad memory access). "
        "Provide your analysis, suggested fix, and an estimate of the severity."
    )
    
    user_query = (
        "Analyze the following xv6 kernel panic state caused by a Trap (Page Fault). "
        "The breakpoint hit was at vectors.S:56 (the trap handler entry).\n\n"
        "You have been provided with:\n"
        "1. The backtrace showing the call stack\n"
        "2. CPU register dump (including CR2 which shows the faulting address)\n"
        "3. The saved return address showing WHERE the bad call came from\n"
        "4. Source code at the calling site\n"
        "5. Disassembly showing the exact instruction that made the bad call\n\n"
        "Based on this information:\n"
        "1. Identify the EXACT function and line of code that caused the fault\n"
        "2. Explain WHY this specific code caused the crash\n"
        "3. Provide a SPECIFIC patch with the exact file name and line number\n"
        "4. The patch should be in unified diff format if possible\n\n"
        "--- Captured Debug Data ---\n"
        f"{debug_data}"
    )

    payload = {
        "contents": [{"parts": [{"text": user_query}]}],
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "rootCause": {"type": "STRING", "description": "The specific technical reason for the fault."},
                    "faultyFunction": {"type": "STRING", "description": "The exact function name and file that contains the bug."},
                    "faultyLine": {"type": "STRING", "description": "The specific line number or code snippet that caused the fault."},
                    "severity": {"type": "STRING", "description": "High, Medium, or Low."},
                    "analysisSummary": {"type": "STRING", "description": "A concise explanation of how the captured state points to the root cause."},
                    "suggestedFixPatch": {"type": "STRING", "description": "A unified diff format patch or specific code changes with file names and line numbers."},
                },
                "required": ["rootCause", "faultyFunction", "faultyLine", "severity", "analysisSummary", "suggestedFixPatch"]
            }
        },
    }

    headers = {'Content-Type': 'application/json'}
    
    # Implement exponential backoff for API robustness
    max_retries = 5
    for attempt in range(max_retries):
        try:
            url = f"{API_URL_BASE}?key={API_KEY}"
            response = requests.post(url, headers=headers, json=payload, timeout=30)

            if response.status_code != 200:
                print(f"API returned status {response.status_code}")
                print(f"Response body: {response.text[:500]}")  # Print first 500 chars of error
                time.sleep(2**attempt)
                continue
                
            result = response.json()
            
            # Extract JSON text
            json_text = result['candidates'][0]['content']['parts'][0]['text']
            parsed_analysis = json.loads(json_text)
            
            # No grounding sources since we removed google_search
            sources = []

            return parsed_analysis, sources

        except Exception as e:
            print(f"Attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(2**attempt)
            else:
                return {"error": f"Failed to get analysis after {max_retries} attempts: {e}"}, []
    
    return {"error": "Exhausted all API retries."}, []


# --- Debugger Core Logic ---

def run_debugger():
    """Starts QEMU, connects GDB, runs the test, captures state, and calls the LLM."""
    
    gdb = None
    qemu = None
    
    # 1. Kill leftover QEMU processes
    print("\n--- 1. Cleaning up QEMU processes ---")
    subprocess.run(["pkill", "-9", "qemu-system-x86_64"], stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    time.sleep(1)

    # 2. Rebuild xv6 (only run make since clean was done implicitly)
    print("--- 2. Building xv6 and test program (trap_test) ---")
    subprocess.run(["make"]) 

    # 3. Start QEMU with GDB stub
    qemu_cmd = (
        f'qemu-system-x86_64 -nographic -cpu qemu64,+rdtscp -nic none '
        f'-hda xv6.img -hdb fs.img -smp 2 -m 512 -S -gdb tcp::{GDB_PORT}'
    )
    print(f"--- 3. Starting QEMU (GDB port: {GDB_PORT}) ---")
    # Open a logfile for QEMU output to avoid blocking
    qemu_log = open('qemu_output.log', 'w')
    qemu = pexpect.spawn(qemu_cmd, encoding="utf-8", timeout=None, logfile=qemu_log)
    
    time.sleep(3) # Increased sleep to ensure QEMU socket is fully open
    
    try:
        # 4. Start GDB and connect to QEMU
        print(f"--- 4. Starting GDB ({GDB_COMMAND}) and connecting to QEMU ---")
        # Use the configured GDB command
        gdb = pexpect.spawn(f"{GDB_COMMAND} kernel", encoding="utf-8", timeout=60)
        
        # Expect the GDB prompt or a startup question (like pending breakpoint)
        gdb.expect(r'\(gdb\)|Breakpoint pending on future shared library load\? \(y or \[n\]\)', timeout=10)
        if 'Breakpoint pending' in gdb.before:
            gdb.sendline("y")
            gdb.expect_exact("(gdb)") # Wait for final prompt
        
        # Connect to QEMU GDB stub
        print("Connecting to remote target...")
        gdb.sendline(f"target remote localhost:{GDB_PORT}")
        
        # Wait for the connection confirmation or the GDB prompt
        connection_patterns = [
            r"Remote debugging using localhost", # Successful connection message
            r"\(gdb\)",                         # The prompt after connection
            r"Connection refused"               # Error case
        ]
        gdb.expect(connection_patterns, timeout=10)

        if "Connection refused" in gdb.before or "Connection refused" in gdb.after:
            raise Exception("GDB failed to connect to QEMU: Connection Refused.")

        print("GDB connected to QEMU.")
        gdb.expect_exact("(gdb)") # Ensure we are at the prompt

        # Disable pagination to prevent GDB from pausing output
        print("Disabling GDB pagination...")
        gdb.sendline("set pagination off")
        gdb.expect_exact("(gdb)")
        
        # Set height to unlimited to avoid paging
        gdb.sendline("set height 0")
        gdb.expect_exact("(gdb)")
        
        # Set breakpoint at vectors.S:56
        gdb.sendline("break vectors.S:56")
        gdb.expect_exact("(gdb)")
        print("Breakpoint set at vectors.S:56 (Page Fault Handler).")

        # 5. Continue execution until xv6 shell is ready
        print("--- 5. Running kernel and waiting for shell prompt ---")
        gdb.sendline("c")
        gdb.expect_exact("Continuing.")  # Acknowledge the continue command
        
        # Wait for the xv6 shell prompt in QEMU: '$ '
        qemu.expect(r"\$ ", timeout=60)
        print("XV6 shell is ready.")
        
        # Small delay to ensure shell is fully ready
        time.sleep(0.5)
        
        # 6. Run the test program that causes the panic
        print("--- 6. Running trap_test to trigger the panic ---")
        qemu.sendline("trap_test")
        
        # Give the system a moment to execute
        time.sleep(0.5)
        
        # QEMU runs, the fault occurs, and GDB halts execution.
        # We need to wait for GDB to report the hit and return to its prompt.
        # IMPORTANT: Do NOT read from QEMU after this point - only from GDB
        breakpoint_patterns = [
            r"Breakpoint 1.*vector14.*vectors\.S:56",  # More specific match
            r"Thread \d+ hit Breakpoint 1",              # Alternative format
            pexpect.TIMEOUT                               # Handle timeout
        ]
        
        index = gdb.expect(breakpoint_patterns, timeout=20)
        
        if index == 2:  # TIMEOUT
            print("Breakpoint was not hit within timeout.")
            print("GDB output:", gdb.before)
            raise Exception("trap_test did not trigger expected page fault")
        
        print("GDB HIT BREAKPOINT at vectors.S:56!")
        
        # After breakpoint is hit, we need to wait for GDB to actually show the prompt
        # The breakpoint message is displayed, but we need to see "(gdb)" 
        print("Waiting for GDB prompt after breakpoint...")
        gdb.expect_exact("(gdb)", timeout=10)
        print("GDB is now at prompt after breakpoint.")
        
        # Important: From this point forward, ONLY interact with GDB
        # Do not read from or write to QEMU - it's paused by GDB
        
        # 7. Capture state (Backtrace and Registers)
        
        # Capture Backtrace
        print("Capturing backtrace...")
        gdb.sendline("bt")
        index = gdb.expect_exact(["(gdb)", pexpect.EOF, pexpect.TIMEOUT], timeout=10)
        if index == 0:  # Got (gdb) prompt
            backtrace_lines = gdb.before.split('\n')
            backtrace_text = "\n".join([line for line in backtrace_lines if line.strip() and not line.strip() == "bt"]).strip()
            print(f"✓ Backtrace captured ({len(backtrace_text)} chars)")
        elif index == 1:  # EOF
            print("GDB process died (EOF)")
            print("Last buffer:", gdb.before)
            raise Exception("GDB process terminated unexpectedly")
        else:  # TIMEOUT
            print("TIMEOUT while waiting for backtrace GDB prompt")
            print("GDB buffer content:", gdb.before if hasattr(gdb, 'before') else "N/A")
            print("GDB is alive?", gdb.isalive())
            raise Exception("Timeout waiting for GDB backtrace")

        # Capture Register Info
        print("Capturing registers...")
        gdb.sendline("info registers")
        index = gdb.expect_exact(["(gdb)", pexpect.EOF, pexpect.TIMEOUT], timeout=10)
        if index == 0:  # Got (gdb) prompt
            register_lines = gdb.before.split('\n')
            registers_text = "\n".join([line for line in register_lines if line.strip() and not line.strip() == "info registers"]).strip()
            print(f"✓ Registers captured ({len(registers_text)} chars)")
        elif index == 1:  # EOF
            print("GDB process died while capturing registers (EOF)")
            raise Exception("GDB process terminated unexpectedly")
        else:  # TIMEOUT
            print("TIMEOUT while waiting for registers")
            print("GDB buffer:", gdb.before if hasattr(gdb, 'before') else "N/A")
            raise Exception("Timeout waiting for GDB registers")
        
        # Capture source code context from the crash location
        print("Capturing source code context...")
        
        # The backtrace shows frame 0 is vector14, frame 1 is 0x0
        # We need to find what CALLED the function that ended up at 0x0
        # Look at the saved RIP to find the real calling function
        
        # Get the saved return address from the stack frame
        gdb.sendline("x/1xg $rsp")  # Examine the return address on stack
        gdb.expect_exact("(gdb)", timeout=5)
        stack_dump = gdb.before.strip()
        
        # Try to get info about the saved RIP
        gdb.sendline("info frame 1")
        gdb.expect_exact("(gdb)", timeout=5)
        frame_info = gdb.before.strip()
        
        # Extract the saved RIP address and find what function it belongs to
        gdb.sendline("frame 1")
        gdb.expect_exact("(gdb)", timeout=5)
        
        gdb.sendline("x/10i $rsp-40")  # Disassemble before the saved RIP
        gdb.expect_exact("(gdb)", timeout=5)
        disassembly_before = gdb.before.strip()
        
        # Try to find the calling function by examining saved RIP
        # The "saved rip" from info frame tells us where we came from
        import re
        saved_rip_match = re.search(r'saved rip = (0x[0-9a-f]+)', frame_info)
        if saved_rip_match:
            saved_rip = saved_rip_match.group(1)
            print(f"Found calling address: {saved_rip}")
            
            # Find what function contains this address
            gdb.sendline(f"info symbol {saved_rip}")
            gdb.expect_exact("(gdb)", timeout=5)
            caller_symbol = gdb.before.strip()
            
            # Now list the source code at that address
            gdb.sendline(f"list *{saved_rip}")
            gdb.expect_exact("(gdb)", timeout=5)
            source_context = gdb.before.strip()
            
            # Get more context around the calling function
            gdb.sendline(f"disassemble {saved_rip}")
            gdb.expect_exact("(gdb)", timeout=5)
            caller_disassembly = gdb.before.strip()
        else:
            source_context = "Could not extract saved RIP"
            caller_symbol = "Unknown"
            caller_disassembly = "N/A"
        
        # Go back to frame 0 for safety
        gdb.sendline("frame 0")
        gdb.expect_exact("(gdb)", timeout=5)
        
        print(f"✓ Source context captured - Caller: {caller_symbol}")

        debug_data = f"""BACKTRACE:
{backtrace_text}

REGISTERS:
{registers_text}

SAVED RETURN ADDRESS INFO:
{frame_info}

CALLING FUNCTION:
{caller_symbol}

SOURCE CODE AT CALLING SITE:
{source_context}

DISASSEMBLY OF CALLING FUNCTION:
{caller_disassembly}

STACK ANALYSIS:
{stack_dump}
"""
        print("\n--- Captured Debug Data ---")
        print(debug_data)
        
        # 8. Call LLM for analysis
        analysis, sources = get_gemini_analysis(prompt="Analyze xv6 panic", debug_data=debug_data)
        
        # 9. Print Analysis
        print("\n" + "="*80)
        print("🤖 AI-ASSISTED ROOT CAUSE ANALYSIS")
        print("="*80)
        
        if "error" in analysis:
            print(f"Analysis failed: {analysis['error']}")
            print("\nEnsure your API Key is valid and the model is accessible.")
        else:
            print(f"Root Cause: {analysis['rootCause']}")
            print(f"Faulty Function: {analysis['faultyFunction']}")
            print(f"Faulty Line: {analysis['faultyLine']}")
            print(f"Severity: {analysis['severity']}")
            print("\nAnalysis Summary:")
            print(analysis['analysisSummary'])
            print("\nSuggested Fix/Patch:")
            print(analysis['suggestedFixPatch'])
            
            # Save the patch to a file
            patch_filename = "suggested_fix.patch"
            with open(patch_filename, 'w') as f:
                f.write(f"# AI-Generated Patch for xv6 Kernel Panic\n")
                f.write(f"# Root Cause: {analysis['rootCause']}\n")
                f.write(f"# Faulty Function: {analysis['faultyFunction']}\n")
                f.write(f"# Faulty Line: {analysis['faultyLine']}\n")
                f.write(f"# Severity: {analysis['severity']}\n\n")
                f.write(analysis['suggestedFixPatch'])
            print(f"\n✓ Patch saved to: {patch_filename}")
            
            if sources:
                print("\nSources used for grounding:")
                for s in sources:
                    print(f"- {s['title']}: {s['uri']}")

    except pexpect.exceptions.EOF:
        print("\nQEMU/GDB closed unexpectedly. This may indicate a problem with the GDB or QEMU processes.")
        if gdb:
            print("Last GDB output:")
            print(gdb.before if gdb.before else "(no output)")
    except pexpect.exceptions.TIMEOUT as e:
        print(f"\nTIMEOUT occurred. The script failed to synchronize with GDB/QEMU.")
        print("\nLast GDB output:")
        if gdb:
            print(gdb.before if gdb.before else "(no output)")
        print("\nLast QEMU output:")
        if qemu:
            print(qemu.before if qemu.before else "(no output)")
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # 10. Cleanup
        print("\n--- 10. Cleaning up processes ---")
        
        # Close QEMU log file if it exists
        try:
            if 'qemu_log' in locals():
                qemu_log.close()
        except:
            pass
            
        if gdb and gdb.isalive():
            try:
                gdb.sendline("q")
                gdb.expect(r"Quit anyway\? \(y or n\)", timeout=2) # Add timeout to clean up
                gdb.sendline("y")
                gdb.close()
            except:
                gdb.close(force=True)
                
        if qemu and qemu.isalive():
            qemu.close(force=True)

if __name__ == "__main__":
    run_debugger()

