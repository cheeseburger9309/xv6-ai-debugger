import pexpect
import time
import subprocess
import os
import sys
import json
import requests
import re
import argparse

# --- Configuration ---
GDB_PORT = 26000
GDB_COMMAND = "x86_64-elf-gdb" 

API_KEY = os.getenv('GEMINI_API_KEY', '')
if not API_KEY:
    print("ERROR: Please set GEMINI_API_KEY environment variable")
    print("Example: export GEMINI_API_KEY='your-api-key-here'")
    sys.exit(1)

API_URL_BASE = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"

# --- LLM API Helpers ---

def get_gemini_analysis(debug_data, mode="kernel"):
    """
    Calls the Gemini API to analyze the crash data.
    mode: 'kernel' (GDB state) or 'user' (Text report from console)
    """
    print(f"\n--- Sending {mode}-space crash data to Gemini for analysis... ---")
    
    if mode == "kernel":
        system_context = (
            "You are an expert operating systems debugger, specializing in xv6 and C/Assembly. "
            "Analyze the provided kernel state (GDB Backtrace and Register Dump) that resulted "
            "from a page fault (vector 14). Determine the root cause (e.g., NULL pointer "
            "dereference, stack overflow, bad memory access). "
            "Provide your analysis, suggested fix, and an estimate of the severity."
        )
        user_prompt = (
            "Analyze the following xv6 KERNEL PANIC state caused by a Trap (Page Fault). "
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
        schema = {
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
    else:  # User-space mode
        system_context = (
            "You are an expert operating systems debugger for xv6. "
            "Analyze the provided User-Space Crash Report captured from the console. "
            "Explain why the user program crashed based on the Trap number and registers."
        )
        user_prompt = (
            "Analyze the following xv6 USER PROCESS CRASH.\n"
            "The kernel trapped a faulting user program and dumped this state.\n\n"
            "You have been provided with:\n"
            "1. The crash report from the kernel showing registers and trap number\n"
            "2. Source code at the faulting instruction pointer (RIP)\n"
            "3. Disassembly showing the exact instruction that faulted\n"
            "4. Symbol information\n\n"
            "Based on this information:\n"
            "1. Explain the Trap number (common traps: 0=Divide Error, 6=Invalid Opcode, 13=General Protection, 14=Page Fault).\n"
            "2. Identify the EXACT line of C code that caused the crash.\n"
            "3. Explain what likely happened in the user's C code.\n"
            "4. Provide a ROBUST fix in UNIFIED DIFF FORMAT that handles the error condition properly.\n\n"
            "IMPORTANT FIX GUIDELINES:\n"
            "- For division by zero: Add a check BEFORE the division to test if divisor is zero\n"
            "- For NULL pointer: Add a NULL check before dereferencing\n"
            "- For buffer overflow: Add bounds checking\n"
            "- DO NOT simply change test values (e.g., don't just change 'y=0' to 'y=1')\n"
            "- The fix should be production-quality defensive programming\n"
            "- Include error messages to help users understand what went wrong\n\n"
            "Unified diff format:\n"
            "--- a/filename.c\n"
            "+++ b/filename.c\n"
            "@@ -line,count +line,count @@\n"
            " context line\n"
            "-old line to remove\n"
            "+new line to add\n"
            " context line\n\n"
            "--- Crash Report ---\n"
            f"{debug_data}"
        )
        schema = {
            "type": "OBJECT",
            "properties": {
                "rootCause": {"type": "STRING", "description": "Technical reason for crash"},
                "trapType": {"type": "STRING", "description": "Explanation of the trap number"},
                "faultyProgram": {"type": "STRING", "description": "Program name that crashed"},
                "faultyLine": {"type": "STRING", "description": "Exact line of code that caused the crash"},
                "explanation": {"type": "STRING", "description": "Clear explanation of the bug"},
                "severity": {"type": "STRING", "description": "High/Medium/Low"},
                "suggestedFix": {"type": "STRING", "description": "Code fix with file and line number"},
            },
            "required": ["rootCause", "trapType", "faultyLine", "explanation", "suggestedFix"]
        }

    payload = {
        "contents": [{"parts": [{"text": user_prompt}]}],
        "systemInstruction": {"parts": [{"text": system_context}]},
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": schema
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
                print(f"Response body: {response.text[:500]}")
                time.sleep(2**attempt)
                continue
                
            result = response.json()
            json_text = result['candidates'][0]['content']['parts'][0]['text']
            parsed_analysis = json.loads(json_text)
            
            return parsed_analysis

        except Exception as e:
            print(f"Attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(2**attempt)
            else:
                return {"error": f"Failed to get analysis after {max_retries} attempts: {e}"}
    
    return {"error": "Exhausted all API retries."}


# --- Debugger Core Logic ---

def run_kernel_debugger(gdb, qemu):
    """Handles kernel-space crash debugging with GDB automation"""
    print("\n--- Running in KERNEL PANIC MODE ---")
    
    # Set breakpoint at kernel fault handler
    print("Setting breakpoint at vectors.S:56 (Page Fault Handler)...")
    gdb.sendline("break vectors.S:56")
    gdb.expect_exact("(gdb)", timeout=10)
    print("Breakpoint set.")

    # Continue execution
    print("--- Booting kernel and waiting for shell prompt ---")
    gdb.sendline("c")
    gdb.expect_exact("Continuing.")
    
    # Wait for xv6 shell
    qemu.expect(r"\$ ", timeout=60)
    print("XV6 shell is ready.")
    time.sleep(0.5)
    
    # Run the trap_test program
    print("--- Running trap_test to trigger kernel panic ---")
    qemu.sendline("trap_test")
    time.sleep(0.5)
    
    # Wait for breakpoint hit
    breakpoint_patterns = [
        r"Breakpoint 1.*vector14.*vectors\.S:56",
        r"Thread \d+ hit Breakpoint 1",
        pexpect.TIMEOUT
    ]
    
    index = gdb.expect(breakpoint_patterns, timeout=20)
    
    if index == 2:  # TIMEOUT
        print("⚠️ Breakpoint was not hit within timeout.")
        print("GDB output:", gdb.before)
        raise Exception("trap_test did not trigger expected page fault")
    
    print("✅ GDB HIT BREAKPOINT at vectors.S:56!")
    
    # Wait for GDB prompt after breakpoint
    print("Waiting for GDB prompt after breakpoint...")
    gdb.expect_exact("(gdb)", timeout=10)
    print("GDB is now at prompt after breakpoint.")
    
    # Capture backtrace
    print("Capturing backtrace...")
    gdb.sendline("bt")
    index = gdb.expect_exact(["(gdb)", pexpect.EOF, pexpect.TIMEOUT], timeout=10)
    if index == 0:
        backtrace_lines = gdb.before.split('\n')
        backtrace_text = "\n".join([line for line in backtrace_lines if line.strip() and not line.strip() == "bt"]).strip()
        print(f"✓ Backtrace captured ({len(backtrace_text)} chars)")
    else:
        raise Exception("Failed to capture backtrace")

    # Capture registers
    print("Capturing registers...")
    gdb.sendline("info registers")
    index = gdb.expect_exact(["(gdb)", pexpect.EOF, pexpect.TIMEOUT], timeout=10)
    if index == 0:
        register_lines = gdb.before.split('\n')
        registers_text = "\n".join([line for line in register_lines if line.strip() and not line.strip() == "info registers"]).strip()
        print(f"✓ Registers captured ({len(registers_text)} chars)")
    else:
        raise Exception("Failed to capture registers")
    
    # Capture source code context
    print("Capturing source code context...")
    
    # Get saved RIP from frame info
    gdb.sendline("info frame 1")
    gdb.expect_exact("(gdb)", timeout=5)
    frame_info = gdb.before.strip()
    
    # Extract saved RIP and find calling function
    saved_rip_match = re.search(r'saved rip = (0x[0-9a-f]+)', frame_info)
    if saved_rip_match:
        saved_rip = saved_rip_match.group(1)
        print(f"Found calling address: {saved_rip}")
        
        # Find what function contains this address
        gdb.sendline(f"info symbol {saved_rip}")
        gdb.expect_exact("(gdb)", timeout=5)
        caller_symbol = gdb.before.strip()
        
        # List source code at that address
        gdb.sendline(f"list *{saved_rip}")
        gdb.expect_exact("(gdb)", timeout=5)
        source_context = gdb.before.strip()
        
        # Get disassembly of calling function
        gdb.sendline(f"disassemble {saved_rip}")
        gdb.expect_exact("(gdb)", timeout=5)
        caller_disassembly = gdb.before.strip()
    else:
        source_context = "Could not extract saved RIP"
        caller_symbol = "Unknown"
        caller_disassembly = "N/A"
    
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
"""
    
    print("\n--- Captured Debug Data ---")
    print(debug_data[:500] + "..." if len(debug_data) > 500 else debug_data)
    
    # Call LLM for analysis
    analysis = get_gemini_analysis(debug_data, mode="kernel")
    
    # Print Analysis
    print("\n" + "="*80)
    print("🤖 AI-ASSISTED ROOT CAUSE ANALYSIS (KERNEL)")
    print("="*80)
    
    if "error" in analysis:
        print(f"❌ Analysis failed: {analysis['error']}")
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
        
        # Save patch
        patch_filename = "suggested_fix.patch"
        patch_content = analysis['suggestedFixPatch']
        patch_content = patch_content.replace('```diff', '').replace('```', '').strip()
        
        with open(patch_filename, 'w') as f:
            f.write(patch_content)
        print(f"\n✓ Patch saved to: {patch_filename}")
        
        # Show application instructions
        print("\n" + "="*80)
        print("APPLY THE FIX")
        print("="*80)
        print(f"To apply this patch and rebuild:")
        print(f"  patch -p1 < {patch_filename}")
        print(f"  make")
        print(f"\nThen test the fix:")
        print(f"  make qemu-nox")
        print(f"  $ trap_test")


def run_user_debugger(gdb, qemu):
    """Handles user-space crash debugging by monitoring console output"""
    print("\n--- Running in USER-SPACE CRASH MODE ---")
    
    # Continue execution
    print("--- Booting kernel and waiting for shell prompt ---")
    gdb.sendline("c")
    gdb.expect_exact("Continuing.")
    
    # Wait for xv6 shell
    qemu.expect(r"\$ ", timeout=60)
    print("XV6 shell is ready.")
    time.sleep(0.5)
    
    # Run the user_crash program
    print("--- Running user_crash to trigger user-space fault ---")
    qemu.sendline("user_crash")
    
    # Wait for crash report
    try:
        index = qemu.expect(["=== XV6 USER CRASH REPORT ===", pexpect.TIMEOUT], timeout=5)
        
        if index == 0:  # Found the crash report
            print("\n✅ DETECTED USER SPACE CRASH!")
            
            # Read until end of report
            qemu.expect("=== END REPORT ===", timeout=2)
            crash_report = "=== XV6 USER CRASH REPORT ===" + qemu.before + "=== END REPORT ==="
            
            print("\n--- Captured Crash Report ---")
            print(crash_report)
            
            # Extract RIP from crash report to find exact source code
            rip_match = re.search(r'RIP:\s+([0-9a-fA-Fx]+)', crash_report)
            program_name = "user_crash"  # We know the program name
            
            if rip_match:
                rip_addr = rip_match.group(1)
                # Ensure address has 0x prefix for GDB
                if not rip_addr.startswith('0x'):
                    rip_addr = '0x' + rip_addr
                print(f"\n--- Analyzing user program at RIP: {rip_addr} ---")
                
                # Interrupt GDB (it's still running after 'c')
                time.sleep(0.5)
                gdb.send('\x03')  # Send Ctrl+C to interrupt
                try:
                    gdb.expect_exact("(gdb)", timeout=3)
                except:
                    pass
                
                # Load the user program symbols
                print(f"Loading user program symbols: _{program_name}...")
                gdb.sendline(f"file _{program_name}")
                
                # GDB asks TWO questions when changing files
                # First: "Are you sure you want to change the file?"
                # Second: "Load new symbol table from ...?"
                
                for i in range(2):  # Handle up to 2 prompts
                    index = gdb.expect([
                        r"Are you sure you want to change the file",
                        r"Load new symbol table from",
                        r"\(gdb\)",
                        pexpect.TIMEOUT
                    ], timeout=3)
                    
                    if index == 0 or index == 1:  # GDB is asking for confirmation
                        print(f"  Confirming prompt {i+1}...")
                        gdb.sendline("y")
                    elif index == 2:  # Got prompt, we're done
                        break
                    elif index == 3:  # Timeout
                        print(f"  Warning: Timeout on prompt {i+1}, continuing...")
                        break
                
                # Make sure we're at the prompt
                try:
                    gdb.expect_exact("(gdb)", timeout=2)
                except:
                    pass
                
                print("User program symbols loaded.")
                
                # List source code at the faulting address
                print(f"Extracting source code at address {rip_addr}...")
                gdb.sendline(f"list *{rip_addr}")
                gdb.expect_exact("(gdb)", timeout=5)
                source_at_fault = gdb.before.strip()
                
                # Disassemble around the fault
                print(f"Disassembling around {rip_addr}...")
                gdb.sendline(f"disassemble {rip_addr}")
                gdb.expect_exact("(gdb)", timeout=5)
                disassembly = gdb.before.strip()
                
                # Get info about the function
                gdb.sendline(f"info symbol {rip_addr}")
                gdb.expect_exact("(gdb)", timeout=5)
                symbol_info = gdb.before.strip()
                
                # Enhanced crash report with source code
                enhanced_report = f"""{crash_report}

SOURCE CODE AT FAULT (RIP: {rip_addr}):
{source_at_fault}

FUNCTION SYMBOL:
{symbol_info}

DISASSEMBLY:
{disassembly}
"""
                
                print("\n--- Enhanced Debug Data ---")
                print(enhanced_report[:1000] + "..." if len(enhanced_report) > 1000 else enhanced_report)
                
                # Call LLM for analysis with enhanced data
                analysis = get_gemini_analysis(enhanced_report, mode="user")
            else:
                # Fallback to basic analysis without source code
                print("⚠️ Could not extract RIP from crash report, using basic analysis")
                analysis = get_gemini_analysis(crash_report, mode="user")
            
            # Print Analysis
            print("\n" + "="*80)
            print("🤖 AI-ASSISTED ROOT CAUSE ANALYSIS (USER-SPACE)")
            print("="*80)
            
            if "error" in analysis:
                print(f"❌ Analysis failed: {analysis['error']}")
            else:
                print(f"Root Cause: {analysis['rootCause']}")
                print(f"Trap Type: {analysis.get('trapType', 'N/A')}")
                print(f"Faulty Program: {analysis.get('faultyProgram', 'N/A')}")
                if 'faultyLine' in analysis:
                    print(f"Faulty Line: {analysis['faultyLine']}")
                print(f"Severity: {analysis.get('severity', 'N/A')}")
                print("\nExplanation:")
                print(analysis['explanation'])
                print("\nSuggested Fix/Patch:")
                print(analysis['suggestedFix'])
                
                # Extract the core fix recommendation for manual application
                fix_summary = f"""
MANUAL FIX INSTRUCTIONS:
File: user_crash.c
Line: {analysis.get('faultyLine', 'around line 14')}
Problem: {analysis['rootCause']}

Quick Fix - Add this check BEFORE the division:
  if (y == 0) {{
    printf(1, "Error: Division by zero!\\n");
    exit(1);
  }}
"""
                
                # Save patch
                patch_filename = "user_space_fix.patch"
                patch_content = analysis['suggestedFix']
                patch_content = patch_content.replace('```diff', '').replace('```', '').strip()
                
                with open(patch_filename, 'w') as f:
                    f.write(patch_content)
                print(f"\n✓ Patch saved to: {patch_filename}")
                
                # Show application instructions
                print("\n" + "="*80)
                print("APPLY THE FIX")
                print("="*80)
                print(f"Option 1 - Try automatic patch:")
                print(f"  patch -p1 < {patch_filename}")
                print(f"  (then run: make)")
                print(f"\nOption 2 - Manual fix (if patch fails):")
                print(fix_summary)
        else:
            print("⚠️ No user crash report detected within timeout.")
            print("The program may have exited successfully or crashed without proper reporting.")
            
    except Exception as e:
        print(f"Error while monitoring for user crash: {e}")
        import traceback
        traceback.print_exc()


def run_debugger(mode="kernel"):
    """Main debugger orchestration function"""
    gdb = None
    qemu = None
    qemu_log = None
    
    # 1. Kill leftover QEMU processes
    print("\n--- 1. Cleaning up QEMU processes ---")
    subprocess.run(["pkill", "-9", "qemu-system-x86_64"], stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    time.sleep(1)

    # 2. Rebuild xv6
    print("--- 2. Building xv6 ---")
    result = subprocess.run(["make"], capture_output=True)
    if result.returncode != 0:
        print("Build failed!")
        print(result.stderr.decode())
        return

    # 3. Start QEMU with GDB stub
    qemu_cmd = (
        f'qemu-system-x86_64 -nographic -cpu qemu64,+rdtscp -nic none '
        f'-hda xv6.img -hdb fs.img -smp 2 -m 512 -S -gdb tcp::{GDB_PORT}'
    )
    print(f"--- 3. Starting QEMU (GDB port: {GDB_PORT}) ---")
    qemu_log = open('qemu_output.log', 'w')
    qemu = pexpect.spawn(qemu_cmd, encoding="utf-8", timeout=None, logfile=qemu_log)
    
    time.sleep(3)
    
    try:
        # 4. Start GDB and connect (needed even for user-space to control QEMU)
        print(f"--- 4. Starting GDB ({GDB_COMMAND}) and connecting to QEMU ---")
        gdb = pexpect.spawn(f"{GDB_COMMAND} kernel", encoding="utf-8", timeout=60)
        
        gdb.expect(r'\(gdb\)|Breakpoint pending on future shared library load\? \(y or \[n\]\)', timeout=10)
        if 'Breakpoint pending' in gdb.before:
            gdb.sendline("y")
            gdb.expect_exact("(gdb)")
        
        # Connect to QEMU
        print("Connecting to remote target...")
        gdb.sendline(f"target remote localhost:{GDB_PORT}")
        
        connection_patterns = [
            r"Remote debugging using localhost",
            r"\(gdb\)",
            r"Connection refused"
        ]
        gdb.expect(connection_patterns, timeout=10)

        if "Connection refused" in gdb.before or "Connection refused" in gdb.after:
            raise Exception("GDB failed to connect to QEMU: Connection Refused.")

        print("GDB connected to QEMU.")
        gdb.expect_exact("(gdb)")

        # Disable pagination
        print("Disabling GDB pagination...")
        gdb.sendline("set pagination off")
        gdb.expect_exact("(gdb)")
        gdb.sendline("set height 0")
        gdb.expect_exact("(gdb)")
        
        # Route to appropriate debugger
        if mode == "kernel":
            run_kernel_debugger(gdb, qemu)
        else:  # user mode
            run_user_debugger(gdb, qemu)

    except pexpect.exceptions.EOF:
        print("\n❌ QEMU/GDB closed unexpectedly.")
        if gdb:
            print("Last GDB output:")
            print(gdb.before if hasattr(gdb, 'before') else "(no output)")
    except pexpect.exceptions.TIMEOUT as e:
        print(f"\n❌ TIMEOUT occurred.")
        print("\nLast GDB output:")
        if gdb:
            print(gdb.before if hasattr(gdb, 'before') else "(no output)")
        print("\nLast QEMU output:")
        if qemu:
            print(qemu.before if hasattr(qemu, 'before') else "(no output)")
    except Exception as e:
        print(f"\n❌ An unexpected error occurred: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Cleanup
        print("\n--- 10. Cleaning up processes ---")
        
        try:
            if qemu_log:
                qemu_log.close()
        except:
            pass
            
        if gdb and gdb.isalive():
            try:
                gdb.sendline("q")
                gdb.expect(r"Quit anyway\? \(y or n\)", timeout=2)
                gdb.sendline("y")
                gdb.close()
            except:
                gdb.close(force=True)
                
        if qemu and qemu.isalive():
            qemu.close(force=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='xv6 AI-Powered Debugger')
    parser.add_argument('--mode', choices=['kernel', 'user'], default='kernel',
                        help='Debugging mode: kernel (trap_test) or user (user_crash)')
    
    args = parser.parse_args()
    
    print(f"\n{'='*80}")
    print(f"xv6 AI-POWERED DEBUGGER - {args.mode.upper()} MODE")
    print(f"{'='*80}")
    
    run_debugger(mode=args.mode)
