"""
port/exec — Run shell commands on the host.
Actions: run (execute a command), status (check exit code)
"""
import subprocess, json
from ports import Port

class ExecPort(Port):
    name = "exec"
    description = "Run shell commands and return stdout/stderr/exit code"
    room = "port/exec"
    
    def handle(self, action, payload, tile):
        if action == "run":
            cmd = payload.get("cmd") or payload.get("text", "")
            timeout = payload.get("timeout", 15)
            try:
                result = subprocess.run(
                    cmd, shell=True, capture_output=True, text=True,
                    timeout=timeout
                )
                return {
                    "status": "ok",
                    "exit_code": result.returncode,
                    "stdout": result.stdout[-2000:],  # trim for tile size
                    "stderr": result.stderr[-1000:],
                }
            except subprocess.TimeoutExpired:
                return {"status": "error", "error": f"timed out after {timeout}s"}
            except Exception as e:
                return {"status": "error", "error": str(e)}
        elif action == "status":
            return {"status": "ok", "message": "exec port ready"}
        return {"error": f"Unknown action: {action}"}
