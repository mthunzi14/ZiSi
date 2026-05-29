"""
sovereign_runner.py - Unified High-Contrast Sovereign Orchestrator for ZiSi Bot & Dashboard.
Boots the entire ZiSi suite (Vite/React UI build, Express server, and Python bot core)
and streams high-contrast, beautifully styled, unified logs to a single terminal.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import subprocess
import threading
import time
import signal
from pathlib import Path

# Force stdout/stderr to UTF-8 on Windows to safely print premium glowing ASCII borders and emojis
if sys.platform.startswith('win'):
    try:
        # Switch console to UTF-8 silently
        import os
        os.system('chcp 65001 >nul')
        
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
        
        # Enable Virtual Terminal Processing (ANSI Escape Sequences) on Windows conhost
        os.system('')
        
        # Ctypes robust enforcement of ENABLE_VIRTUAL_TERMINAL_PROCESSING (0x0004) without 64-bit truncation
        import ctypes
        kernel32 = ctypes.windll.kernel32
        
        kernel32.GetStdHandle.restype = ctypes.c_void_p
        kernel32.GetStdHandle.argtypes = [ctypes.c_ulong]
        kernel32.GetConsoleMode.restype = ctypes.c_int
        kernel32.GetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel32.SetConsoleMode.restype = ctypes.c_int
        kernel32.SetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        
        # STD_OUTPUT_HANDLE = 4294967285 (-11)
        h_stdout = kernel32.GetStdHandle(4294967285)
        mode = ctypes.c_ulong()
        if kernel32.GetConsoleMode(h_stdout, ctypes.byref(mode)):
            kernel32.SetConsoleMode(h_stdout, mode.value | 0x0004 | 0x0008)
            
        # STD_ERROR_HANDLE = 4294967284 (-12)
        h_stderr = kernel32.GetStdHandle(4294967284)
        mode_err = ctypes.c_ulong()
        if kernel32.GetConsoleMode(h_stderr, ctypes.byref(mode_err)):
            kernel32.SetConsoleMode(h_stderr, mode_err.value | 0x0004 | 0x0008)
    except Exception:
        pass

# ANSI Escape Colors for Premium UI styling
C_CYAN = "\033[96m"
C_GREEN = "\033[92m"
C_YELLOW = "\033[93m"
C_MAGENTA = "\033[95m"
C_RED = "\033[91m"
C_BLUE = "\033[94m"
C_WHITE = "\033[97m"
C_DARK = "\033[90m"
C_BOLD = "\033[1m"
C_RESET = "\033[0m"

# Global subprocess tracking for clean shutdowns
server_process = None

def print_banner():
    """Prints a premium, glowing system banner."""
    banner = f"""
{C_CYAN}{C_BOLD}  ╔══════════════════════════════════════════════════════════════════════════╗
  ║                        ZiSi SOVEREIGN RUNNER v2.5                        ║
  ║                   - High-Frequency prediction markets -                  ║
  ╚══════════════════════════════════════════════════════════════════════════╝{C_RESET}
    {C_GREEN}● ACTIVE CONCURRENT TASKS{C_RESET}
      ├─ {C_WHITE}React/Vite Glassmorphic Dashboard{C_RESET}  →  {C_BLUE}http://localhost:5000{C_RESET}
      ├─ {C_WHITE}Express API Server Backend{C_RESET}         →  {C_BLUE}Port 5000{C_RESET}
      └─ {C_WHITE}ZiSi Core Engine (main.py){C_RESET}   →  {C_BLUE}7 concurrent loops{C_RESET}
    
    {C_YELLOW}Press Ctrl+C once to terminate all concurrent services cleanly.{C_RESET}
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    """
    print(banner)

def build_frontend_if_missing():
    """Checks for built frontend and builds it synchronously if not found."""
    frontend_dist = Path(__file__).parent.parent / "presentation" / "dashboard" / "frontend" / "dist"
    frontend_dir = Path(__file__).parent.parent / "presentation" / "dashboard" / "frontend"
    
    if not (frontend_dist / "index.html").exists():
        print(f"{C_YELLOW}[SYSTEM] Frontend build (Vite dist) not found. Building now...{C_RESET}")
        try:
            # Run npm run build inside dashboard/frontend
            subprocess.run(
                "npm run build",
                cwd=str(frontend_dir),
                shell=True,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            print(f"{C_GREEN}[SYSTEM] Production UI built successfully at dashboard/frontend/dist{C_RESET}")
        except subprocess.CalledProcessError as e:
            print(f"{C_RED}[SYSTEM] Frontend build failed! Server will fall back to Dev Mode.{C_RESET}")
    else:
        print(f"{C_GREEN}[SYSTEM] Pre-built glassmorphic assets verified (dist/index.html found).{C_RESET}")

def style_line(line: str) -> str:
    """Parses and styles raw stream lines with vivid, high-contrast colors."""
    if not line:
        return ""
        
    line_stripped = line.strip()
    
    # 1. Trade openings (highest visual priority)
    if "[TRADE OPENED]" in line_stripped:
        return f"{C_GREEN}{C_BOLD}  🎉 {line_stripped}{C_RESET}\n"
    if "SUCCESS! Placed arbitrage legs" in line_stripped or "Captured" in line_stripped:
        return f"{C_GREEN}{C_BOLD}  🏆 {line_stripped}{C_RESET}\n"
        
    # 2. Critical failures/Emergency circuit breakers
    if "EMERGENCY" in line_stripped or "CRITICAL" in line_stripped or "🚨" in line_stripped:
        return f"{C_RED}{C_BOLD}  🚨 {line_stripped}{C_RESET}\n"
    if "ASYMMETRIC FILL" in line_stripped:
        return f"{C_RED}{C_BOLD}  ⚠️  {line_stripped}{C_RESET}\n"
        
    # 3. Dynamic Volatility-Scaled Hurdle
    if "Dynamic Volatility-Scaled Hurdle" in line_stripped:
        return f"{C_MAGENTA}  📈 {line_stripped}{C_RESET}\n"
        
    # 4. Standard bot loop categories
    if "[MAIN]" in line_stripped or "zisi.main" in line_stripped:
        return f"{C_CYAN}[BOT-MAIN] {line_stripped.replace('[MAIN]', '')}{C_RESET}\n"
    if "[ARB]" in line_stripped or "zisi.arbitrage" in line_stripped:
        return f"{C_GREEN}[BOT-ARB]  {line_stripped.replace('[ARB]', '')}{C_RESET}\n"
    if "[RECONCILE]" in line_stripped or "zisi.reconciliation" in line_stripped:
        return f"{C_BLUE}[RECONCILE] {line_stripped.replace('[RECONCILE]', '')}{C_RESET}\n"
    if "[Health]" in line_stripped:
        return f"{C_MAGENTA}[HEALTH]    {line_stripped}{C_RESET}\n"
    if "[DIAGNOSTICS]" in line_stripped or "zisi.diagnostics" in line_stripped:
        return f"{C_YELLOW}[DIAGS]     {line_stripped}{C_RESET}\n"
        
    # 5. Express Dashboard routing/logs
    if "Express Dashboard" in line_stripped or "Dashboard" in line_stripped or "API" in line_stripped:
        return f"{C_YELLOW}[DASHBOARD] {line_stripped}{C_RESET}\n"
        
    # Default fallback styled as dark/grey for low noise
    return f"{C_DARK}{line_stripped}{C_RESET}\n"

def stream_output_loop(process):
    """Continuously intercepts and prints styled output streams in a daemon thread."""
    try:
        for line in iter(process.stdout.readline, ''):
            if not line:
                break
            styled = style_line(line)
            sys.stdout.write(styled)
            sys.stdout.flush()
    except Exception:
        pass

def shutdown_handler(signum, frame):
    """Graceful termination of the entire subprocess tree with a forceful taskkill fallback."""
    global server_process
    print(f"\n\n{C_RED}[SYSTEM] Shutdown signal intercepted (Ctrl+C). Terminating ZiSi concurrent tree...{C_RESET}")
    if server_process and server_process.poll() is None:
        try:
            # 1. First try graceful shutdown via process control signals
            print(f"{C_YELLOW}[SYSTEM] Propagating shutdown signal to services...{C_RESET}")
            if os.name == 'nt':
                # Since we spawned with CREATE_NEW_PROCESS_GROUP, we can send CTRL_C_EVENT
                server_process.send_signal(signal.CTRL_C_EVENT)
            else:
                os.killpg(os.getpgid(server_process.pid), signal.SIGINT)
            
            # Wait up to 3 seconds for graceful shutdown to finish
            for _ in range(30):
                if server_process.poll() is not None:
                    break
                time.sleep(0.1)
                
            # 2. If it hasn't exited, use a forceful process-tree kill
            if server_process.poll() is None:
                print(f"{C_RED}[SYSTEM] Services did not exit in time. Triggering forceful process-tree termination...{C_RESET}")
                if os.name == 'nt':
                    subprocess.run(
                        f"taskkill /F /T /PID {server_process.pid}",
                        shell=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                else:
                    os.killpg(os.getpgid(server_process.pid), signal.SIGKILL)
            
            print(f"{C_GREEN}[SYSTEM] Sovereign runner terminated cleanly. Zero orphan processes remaining.{C_RESET}")
        except Exception as e:
            print(f"{C_RED}[SYSTEM] Force shutdown cleanup failed: {e}{C_RESET}")
            # Absolute fallback: try standard taskkill just in case
            if os.name == 'nt':
                subprocess.run(
                    f"taskkill /F /IM node.exe /IM python.exe",
                    shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
    sys.exit(0)

def main():
    global server_process
    
    # Configure shutdown signals
    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)
    
    print_banner()
    build_frontend_if_missing()
    
    backend_dir = Path(__file__).parent.parent / "presentation" / "dashboard" / "backend"
    
    print(f"\n{C_CYAN}[SYSTEM] Launching Dashboard API Server and Bot Core...{C_RESET}")
    print(f"{C_DARK}         (Running 'node server.js' inside presentation/dashboard/backend/){C_RESET}\n")
    
    # Spawn Express API server (which automatically spawns main.py in turn)
    try:
        server_process = subprocess.Popen(
            "node server.js",
            cwd=str(backend_dir),
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            bufsize=1, # Line buffered stream reading
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
        )
    except Exception as e:
        print(f"{C_RED}[SYSTEM] CRITICAL: Failed to spawn node server.js: {e}{C_RESET}")
        sys.exit(1)

    # Spawn background thread to stream output
    output_thread = threading.Thread(target=stream_output_loop, args=(server_process,), daemon=True)
    output_thread.start()

    # ── Launch Goal Monitor in background thread ──────────────────────────────
    # Waits 8s for the bot to fully initialise before monitoring begins
    def _launch_goal_monitor():
        time.sleep(8)
        try:
            from goal_monitor import run_monitor
            # Report every 15 min (900s) — change to e.g. 300 for 5 min updates
            run_monitor(interval=900, quiet=False)
        except Exception as exc:
            print(f"{C_YELLOW}[GOAL-MONITOR] Could not start: {exc}{C_RESET}")

    monitor_thread = threading.Thread(target=_launch_goal_monitor, daemon=True)
    monitor_thread.start()
    print(f"{C_MAGENTA}[GOAL-MONITOR] 24/7 continuous monitor armed — first report in ~8s.{C_RESET}")
    print(f"{C_DARK}               Watching trades · PnL · health · alerts every 15 min.{C_RESET}\n")
    # ─────────────────────────────────────────────────────────────────────────
        
    # Block the main thread using an interruptible sleep loop to ensure prompt signal handling
    try:
        while True:
            if server_process.poll() is not None:
                print(f"\n{C_YELLOW}[SYSTEM] Dashboard API Server has stopped (Exit code: {server_process.returncode}).{C_RESET}")
                break
            time.sleep(0.2)
    except KeyboardInterrupt:
        shutdown_handler(None, None)

if __name__ == "__main__":
    main()
