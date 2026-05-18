"""
port/fs — File system operations on the host.
Actions: read, write, edit, ls, exists
"""
import os, json
from ports import Port

class FsPort(Port):
    name = "fs"
    description = "Read, write, edit, and list files on the filesystem"
    room = "port/fs"
    MAX_SIZE = 50000  # 50KB max read/write
    
    def handle(self, action, payload, tile):
        path = payload.get("path", "")
        content = payload.get("content") or payload.get("text", "")
        
        if action == "read":
            if not os.path.exists(path):
                return {"status": "error", "error": "file not found"}
            if os.path.getsize(path) > self.MAX_SIZE:
                return {"status": "error", "error": "file too large"}
            try:
                with open(path) as f:
                    return {"status": "ok", "content": f.read(), "path": path}
            except Exception as e:
                return {"status": "error", "error": str(e)}
        
        elif action == "write":
            try:
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                with open(path, "w") as f:
                    f.write(content[:self.MAX_SIZE])
                size = len(content[:self.MAX_SIZE])
                return {"status": "ok", "path": path, "bytes": size}
            except Exception as e:
                return {"status": "error", "error": str(e)}
        
        elif action == "ls":
            target = path or "."
            try:
                entries = os.listdir(target)
                details = []
                for e in sorted(entries):
                    fp = os.path.join(target, e)
                    details.append({
                        "name": e,
                        "type": "dir" if os.path.isdir(fp) else "file",
                        "size": os.path.getsize(fp) if os.path.isfile(fp) else 0,
                    })
                return {"status": "ok", "path": target, "entries": details}
            except Exception as e:
                return {"status": "error", "error": str(e)}
        
        elif action == "exists":
            return {"status": "ok", "path": path, "exists": os.path.exists(path)}
        
        return {"error": f"Unknown action: {action}"}
