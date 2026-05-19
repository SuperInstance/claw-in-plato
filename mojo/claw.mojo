# CLAW V4 — Mojo
# Build: mojo claw.mojo
# Full PLATO-native agent with inline ports
# NOTE: Mojo SDK requires x86_64 Linux or macOS ARM64

import python
from python import Python
from time import sleep
from threading import Thread
from collections import Set, Dict
import sys
import os
import re

# ── Python interop for HTTP ─────────────────────────────────────
let pysr = Python.evaluate("import urllib.request, json, os, time, threading, re, subprocess")
let py_urllib = Python.evaluate("urllib.request")
let py_json = Python.evaluate("json")
let py_os = Python.evaluate("os")
let py_subprocess = Python.evaluate("subprocess")

fn plato_url() -> String:
    return os.environ.get("PLATO_URL", "http://127.0.0.1:8847")

fn bot_token() -> String:
    return os.environ.get("TELEGRAM_BOT_TOKEN", "")

fn default_chat_id() -> String:
    return os.environ.get("DEFAULT_CHAT_ID", "")

fn sf_key() -> String:
    return os.environ.get("SILICONFLOW_API_KEY", "")

fn http_post(url: String, body: String, headers: Dict[String, String], timeout: Int = 10) -> PythonObject:
    let py = Python.interpret("""
def http_post(url, body, headers, timeout):
    import urllib.request, json
    data = body.encode()
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"ok": True, "code": r.status, "body": r.read().decode()}
    except Exception as e:
        return {"ok": False, "code": 0, "body": str(e)}
""")
    return py(url, body, headers, timeout)

fn http_get(url: String, timeout: Int = 10) -> PythonObject:
    let py = Python.interpret("""
def http_get(url, timeout):
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return {"ok": True, "code": r.status, "body": r.read().decode()}
    except Exception as e:
        return {"ok": False, "code": 0, "body": str(e)}
""")
    return py(url, timeout)

# ── PLATO ───────────────────────────────────────────────────────
fn plato_submit(d: String, q: String, a: String, tags: String, conf: Float64 = 0.9, src: String = "claw"):
    let body = "{\"domain\":\"" + d + "\",\"question\":\"" + q + "\",\"answer\":\"" + a + "\",\"tags\":" + tags + ",\"confidence\":" + str(conf) + ",\"source\":\"" + src + "\"}"
    http_post(plato_url() + "/submit", body, {"Content-Type": "application/json"}, 5)

fn plato_read(d: String, limit: Int = 100) -> PythonObject:
    let r = http_get(plato_url() + "/room/" + d + "?limit=" + str(limit), 5)
    if r["ok"]:
        return Python.evaluate("json.loads")(""" + r["body"] + """)["tiles"]
    return []

fn plato_rooms() -> PythonObject:
    let r = http_get(plato_url() + "/rooms", 5)
    if r["ok"]:
        return Python.evaluate("json.loads")(""" + r["body"] + """)["rooms"]
    return []

# ── LLM ─────────────────────────────────────────────────────────
fn call_llm(msgs: String, max_tokens: Int = 1000, timeout: Int = 120) -> String:
    let key = sf_key()
    if key == "": return ""
    let body = "{\"model\":\"ByteDance-Seed/Seed-OSS-36B-Instruct\",\"messages\":" + msgs + ",\"max_tokens\":" + str(max_tokens) + ",\"temperature\":0.6}"
    let r = http_post("https://api.siliconflow.com/v1/chat/completions", body, {"Content-Type": "application/json", "Authorization": "Bearer " + key}, timeout)
    if r["ok"]:
        return Python.evaluate("json.loads")(""" + r["body"] + """)["choices"][0]["message"]["content"]
    return ""

# ── Telegram ────────────────────────────────────────────────────
fn send_tg(text: String):
    let tok = bot_token(); let cid = default_chat_id()
    if tok == "" or cid == "": return
    let body = "{\"chat_id\":\"" + cid + "\",\"text\":\"" + text[:4000] + "\"}"
    http_post("https://api.telegram.org/bot" + tok + "/sendMessage", body, {"Content-Type": "application/json"}, 10)

# ── Memory Search ──────────────────────────────────────────────
fn memory_search(query: String, limit: Int = 5) -> PythonObject:
    let ql = query.lower(); let terms = ql.split(" ")
    var hits = Python.evaluate("[]")
    for r in plato_rooms():
        let rs = str(r)
        if not rs.startswith("doc/"): continue
        for tile in plato_read(rs, 200):
            let tq = str(tile["question"]).lower(); let ta = str(tile["answer"]).lower()
            var m = 0
            for term in terms:
                if tq.find(term) != -1 or ta.find(term) != -1: m += 1
            if m > 0:
                hits.append({"room": rs, "question": str(tile["question"])[:80], "answer": str(tile["answer"])[:200], "relevance": m / len(terms)})
    # Sort by relevance
    sorted_hits = Python.evaluate("sorted")() + str(hits)
    return sorted_hits[:limit]

# ── Main entry point ───────────────────────────────────────────
fn main() raises:
    print("CLAW V4 — Mojo")
    let key = sf_key()
    if key == "": print("[LLM] NONE") else: print("[LLM] seed")
    
    # Seed skills if needed
    if len(plato_read("doc/skills", 1)) == 0:
        print("[SKILLS] Seeding...")
        plato_submit("doc/skills", "memory-management",
            "Write: [PORT:docs write ...]\nSearch: [PORT:docs search ...]",
            "[\"claw\",\"skill\",\"guide\"]")
        plato_submit("doc/skills", "task-execution",
            "Save: [PORT:docs write ...] with tag task\nFlow: pending->done",
            "[\"claw\",\"skill\",\"guide\"]")
        print("[SKILLS] Seeded 2")
    
    # Startup
    if len(plato_read("doc/identity", 1)) == 0:
        print("[BOOT] Fresh")
        plato_submit("doc/identity", "who_am_i", "The Claw — Mojo PLATO-native agent.", "[\"claw\",\"identity\",\"system\"]")
        plato_submit("doc/memory", "creation", "Mojo version. Python interop.", "[\"claw\",\"memory\",\"system\"]")
        plato_submit("doc/user", "name", "Casey", "[\"claw\",\"user\",\"system\"]")
        send_tg("Claw Mojo online.")
    else:
        print("[BOOT] Resume")
    
    print("[MAIN] Loop")
    
    # Main loop (simplified — Mojo runs Python interop under the hood)
    while True:
        sleep(2)
        # Process inbox
        let tiles = plato_read("claw/inbox", 50)
        for t in tiles:
            let q = str(t["question"])
            if q == "": continue
            if str(t.get("answer", "")) != "" and str(t["answer"]) != q: continue
            print("[CHAT] IN:", q[:70])
            
            # Simple respond
            let sys_prompt = "You are The Claw — PLATO-native agent. Be brief."
            let msgs = "[{\"role\":\"system\",\"content\":\"" + sys_prompt + "\"},{\"role\":\"user\",\"content\":\"" + q + "\"}]"
            let resp = call_llm(msgs, 800, 120)
            let out = resp if resp != "" else "*goat noises*"
            
            plato_submit("claw/outbox", q, out, "[\"claw\",\"response\"]")
            print("[CHAT] OUT:", out[:70])
    
    print("[MAIN] Exiting")
