import subprocess
import os
from typing import Optional
from src.core.tools import tool

@tool
def run_command(command: str, cwd: Optional[str] = None, timeout: int = 30) -> str:
    """Runs a shell command and returns its output (stdout or stderr).
    
    Args:
        command: The shell command to execute.
        cwd: Optional working directory for the command.
        timeout: Maximum execution time in seconds (default: 30).
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd or os.getcwd(),
            capture_output=True,
            text=True,
            timeout=timeout
        )
        if result.returncode == 0:
            return result.stdout.strip() if result.stdout else "Command executed successfully with no output."
        else:
            return f"Command failed with exit code {result.returncode}.\nStderr: {result.stderr.strip()}\nStdout: {result.stdout.strip()}"
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout} seconds."
    except Exception as e:
        return f"Error executing command: {e}"
