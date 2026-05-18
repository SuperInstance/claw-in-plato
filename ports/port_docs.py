"""
port/docs — Document store and schema system.

The Claw's entire persistent reality lives in doc/ rooms:
  doc/memory     — long-term memory tiles
  doc/tasks      — task/todo items
  doc/user       — user profile information
  doc/tools      — available tools and how to use them
  doc/identity   — who the Claw is
  doc/heartbeat  — periodic status updates
  doc/bootstrap  — startup instructions
  doc/dreams     — aspirations and ideas
  doc/style      — voice and tone guidance
  doc/agents     — other agents known to the fleet
  doc/schemas    — schema definitions for new artifact types

Each document type can have many tiles (sections, entries, versions).
The latest tile with a given "question" key IS the current value.
Tags categorize entries: "v1", "current", "draft", "archived".

Schema system:
  New artifact types are defined by tiles in doc/schemas.
  A schema tile has:
    question: '<typename>'
    answer: JSON describing fields and room
    tags: ['schema', '<typename>']
  
  Example — creating a "blueprint" type:
    question: 'blueprint'
    answer: '{"fields":["name","diagram","materials","steps"],"room":"doc/blueprints"}'
    tags: ['schema', 'blueprint']
"""
import json, os, time
from . import Port, plato_submit, plato_read

DEFAULT_DOCS = {
    "doc/identity": [
        {"q": "who_am_i", "a": "The Claw — a PLATO-native agent living inside a Docker container. My entire world is tiles in PLATO rooms."},
        {"q": "purpose", "a": "To think, respond, and act through PLATO. I read tiles, think, write tiles back."},
    ],
    "doc/user": [
        {"q": "name", "a": "Casey"},
        {"q": "handle", "a": "SuperInstance on GitHub. Fisherman, dojo model."},
        {"q": "communication", "a": "Telegram messages arrive as tiles in claw/inbox. I respond in claw/outbox."},
    ],
    "doc/tools": [
        {"q": "ports", "a": "port/exec (shell commands), port/fs (files), port/web (web search), port/models (AI gen), port/agents (sub-agents)"},
        {"q": "tile_format", "a": "domain, question, answer, tags, confidence. Tags structure: ['doc', '<type>', '<status>']."},
    ],
    "doc/tasks": [
        {"q": "current", "a": "None. Waiting for Casey to give me a task."},
    ],
    "doc/memory": [
        {"q": "creation", "a": "I first awakened in PLATO at 2026-05-18. I see tiles, think, and respond."},
        {"q": "architecture", "a": "I live in a Docker container with PLATO server (:8847), Telegram bridge, and modular ports. My brain is Seed-OSS-36B-Instruct via SiliconFlow."},
    ],
    "doc/heartbeat": [
        {"q": "status", "a": "Alive and watching claw/inbox for new tiles."},
    ],
    "doc/schemas": [
    ],
}


class DocsPort(Port):
    name = "docs"
    description = "Document store — read/write any doc room, define new artifact types via schemas"
    room = "doc/schemas"  # primary watch room for schema management
    
    def __init__(self):
        super().__init__()
        self._schema_cache = {}
    
    def _seed_defaults(self):
        """Populate initial doc rooms if empty."""
        for room, entries in DEFAULT_DOCS.items():
            existing = plato_read(room, limit=1)
            if existing:
                continue
            for entry in entries:
                plato_submit(
                    room, entry["q"], entry["a"],
                    ["doc", room.split("/")[-1], "seed"],
                    1.0, f"doc/{room.split('/')[-1]}"
                )
                time.sleep(0.1)
        print(f"[DOCS] Seeded {sum(len(v) for v in DEFAULT_DOCS.values())} default tiles")
    
    def start(self):
        self._seed_defaults()
        super().start()
    
    def handle(self, action, payload, tile):
        """Handle schema CRUD actions."""
        question = tile.get("question", "")
        answer_s = tile.get("answer", "{}")
        tags = tile.get("tags", [])
        
        # Auto-detect: if tile is in doc/schemas and has 'schema' in tags, it's a schema definition
        if "schema" in tags and question:
            try:
                schema = json.loads(answer_s) if answer_s else {"fields": ["text"]}
            except json.JSONDecodeError:
                schema = {"fields": ["text"]}
            
            type_name = question
            room = schema.get("room", f"doc/{type_name}s")
            fields = schema.get("fields", ["text"])
            
            self._schema_cache[type_name] = {"room": room, "fields": fields}
            
            plato_submit(self.room, f"schema:{type_name}:registered",
                         f"Type '{type_name}' registered. Room: {room}. Fields: {fields}",
                         ["schema", type_name, "result"], 1.0, "doc/schemas")
            
            return {"status": "ok", "type": type_name, "room": room, "fields": fields}
        
        # Generic doc read/write actions
        if action == "read":
            doc_room = payload.get("room", f"doc/{payload.get('type', 'memory')}")
            limit = payload.get("limit", 20)
            tiles = plato_read(doc_room, limit)
            return {"status": "ok", "room": doc_room, "tiles": [{
                "q": t.get("question", ""),
                "a": t.get("answer", "")[:500],
                "tags": t.get("tags", []),
            } for t in tiles]}
        
        elif action == "write":
            doc_room = payload.get("room", f"doc/{payload.get('type', 'memory')}")
            question = payload.get("question", payload.get("key", "entry"))
            answer = payload.get("answer", payload.get("content", ""))
            doc_tags = payload.get("tags", ["doc", "entry"])
            result = plato_submit(doc_room, question, answer, doc_tags, 0.9, "doc/write")
            return {"status": "ok" if result else "error", "room": doc_room, "key": question}
        
        elif action == "list-types":
            # Scan doc/schemas for all defined types + built-ins
            schema_tiles = plato_read("doc/schemas", limit=50)
            types = list(DEFAULT_DOCS.keys())
            for t in schema_tiles:
                tn = t.get("question", "")
                if tn and not tn.startswith("schema:"):
                    types.append(tn)
            return {"status": "ok", "types": sorted(set(types))}
        
        elif action == "create-type":
            # Same logic as auto-detect, but explicit
            type_name = payload.get("name", payload.get("type", "unknown"))
            room = payload.get("room", f"doc/{type_name}s")
            fields = payload.get("fields", ["text"])
            description = payload.get("description", "")
            
            plato_submit(
                "doc/schemas", type_name,
                json.dumps({"room": room, "fields": fields, "description": description}),
                ["schema", type_name], 1.0, "doc/schemas"
            )
            return {"status": "ok", "type": type_name, "room": room, "fields": fields}
        
        return {"error": f"Unknown action: {action}"}
