use lazy_static::lazy_static;
use regex::Regex;
use reqwest::blocking::Client;
use serde_json::{json, Value};
use std::collections::{HashMap, HashSet};
use std::env;
use std::fs;
use std::io::{BufRead, BufReader};
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

fn plato_url() -> String {
    env::var("PLATO_URL").unwrap_or_else(|_| "http://127.0.0.1:8847".into())
}
fn bot_token() -> String { env::var("TELEGRAM_BOT_TOKEN").unwrap_or_default() }
fn default_chat_id() -> String { env::var("DEFAULT_CHAT_ID").unwrap_or_default() }
fn sf_key() -> String { env::var("SILICONFLOW_API_KEY").unwrap_or_default() }

fn http_client() -> Client {
    Client::builder()
        .timeout(Duration::from_secs(30))
        .danger_accept_invalid_certs(true)
        .build().expect("HTTP client")
}

fn plato_submit(d: &str, q: &str, a: &str, tags: Value, conf: f64, src: &str) {
    let body = json!({"domain": d, "question": q, "answer": a, "tags": tags, "confidence": conf, "source": src});
    let c = http_client();
    let _ = c.post(format!("{}/submit", plato_url()))
        .json(&body).timeout(Duration::from_secs(5)).send();
}

fn plato_read(d: &str, limit: usize) -> Vec<Value> {
    let c = http_client();
    let url = format!("{}/room/{}?limit={}", plato_url(), d, limit);
    match c.get(&url).timeout(Duration::from_secs(5)).send() {
        Ok(r) => r.json::<Value>().ok().and_then(|v| v["tiles"].as_array().cloned()).unwrap_or_default(),
        Err(_) => vec![],
    }
}

fn plato_rooms() -> Vec<String> {
    let c = http_client();
    match c.get(&format!("{}/rooms", plato_url())).timeout(Duration::from_secs(5)).send() {
        Ok(r) => r.json::<Value>().ok()
            .and_then(|v| v["rooms"].as_array().map(|a| a.iter().map(|x| x.as_str().unwrap_or("").to_string()).collect()))
            .unwrap_or_default(),
        Err(_) => vec![],
    }
}

fn call_llm(msgs: &Value, max_tokens: usize, timeout_sec: u64) -> Option<String> {
    let key = sf_key();
    if key.is_empty() { return None; }
    let body = json!({
        "model": "ByteDance-Seed/Seed-OSS-36B-Instruct",
        "messages": msgs, "max_tokens": max_tokens, "temperature": 0.6
    });
    let c = Client::builder()
        .timeout(Duration::from_secs(timeout_sec))
        .danger_accept_invalid_certs(true).build().ok()?;
    let r = c.post("https://api.siliconflow.com/v1/chat/completions")
        .json(&body).header("Authorization", format!("Bearer {}", key)).send().ok()?;
    r.json::<Value>().ok()?["choices"][0]["message"]["content"].as_str().map(|s| s.to_string())
}

fn send_tg(text: &str) {
    let tok = bot_token(); let cid = default_chat_id();
    if tok.is_empty() || cid.is_empty() { return; }
    let end = text.len().min(4000);
    let body = json!({"chat_id": cid, "text": &text[..end]});
    let _ = http_client().post(format!("https://api.telegram.org/bot{tok}/sendMessage"))
        .json(&body).timeout(Duration::from_secs(10)).send();
}

fn memory_search(query: &str, limit: usize) -> Vec<Value> {
    let ql = query.to_lowercase();
    let terms: Vec<&str> = ql.split_whitespace().collect();
    if terms.is_empty() { return vec![]; }
    let mut hits = Vec::new();
    for r in plato_rooms() {
        if !r.starts_with("doc/") { continue; }
        for tile in plato_read(&r, 200) {
            let q = tile["question"].as_str().unwrap_or("").to_lowercase();
            let a = tile["answer"].as_str().unwrap_or("").to_lowercase();
            let m = terms.iter().filter(|t| q.contains(*t) || a.contains(*t)).count();
            if m > 0 {
                hits.push(json!({"room": r,
                    "question": tile["question"].as_str().unwrap_or("").chars().take(80).collect::<String>(),
                    "answer": tile["answer"].as_str().unwrap_or("").chars().take(200).collect::<String>(),
                    "relevance": m as f64 / terms.len() as f64}));
            }
        }
    }
    hits.sort_by(|a,b| b["relevance"].as_f64().unwrap_or(0.).partial_cmp(&a["relevance"].as_f64().unwrap_or(0.)).unwrap());
    hits.truncate(limit);
    hits
}

lazy_static! { static ref SUB_RES: Arc<Mutex<HashMap<String, String>>> = Arc::new(Mutex::new(HashMap::new())); }

fn sub_worker(name: String, prompt: String) {
    eprintln!("[SUB] {}", name);
    let msgs = json!([{"role":"system","content":"Focused sub-agent. Return ONLY result."},
        {"role":"user","content":prompt}]);
    let r = call_llm(&msgs, 1500, 120).unwrap_or_else(|| "(no result)".into());
    SUB_RES.lock().unwrap().insert(name, r);
}

fn spawn_sub(name: &str, prompt: &str) -> Value {
    let n = name.to_string();
    let p = prompt.to_string();
    if prompt.is_empty() { return json!({"error":"No prompt"}); }
    SUB_RES.lock().unwrap().insert(name.to_string(), "(working...)".into());
    thread::spawn(move || sub_worker(n, p));
    json!({"status":"ok","name":name})
}

fn wait_sub(name: &str, timeout_s: u64) -> String {
    let start = Instant::now();
    loop {
        { let mut m = SUB_RES.lock().unwrap();
            if let Some(r) = m.get(name) { if r != "(working...)" { let r2 = r.clone(); m.remove(name); return r2; } } }
        if start.elapsed().as_secs() >= timeout_s { break; }
        thread::sleep(Duration::from_millis(500));
    }
    json!({"status":"timeout","name":name}).to_string()
}

fn port_exec(cmd: &str) -> String {
    eprintln!("[EXEC] {}", &cmd[..cmd.len().min(80)]);
    let mut out = String::new();
    if let Ok(child) = Command::new("sh").arg("-c").arg(format!("{} 2>&1",cmd))
        .stdout(Stdio::piped()).stderr(Stdio::piped()).spawn() {
        if let Some(stdout) = child.stdout {
            for line in BufReader::new(stdout).lines().flatten() { out.push_str(&line); out.push('\n'); }
        }
    } else { return "(exec failed)".into(); }
    out.truncate(3000); out
}

fn port_fs_read(path: &str) -> String {
    match fs::read_to_string(path) {
        Ok(c) => c.chars().take(3000).collect(),
        Err(e) => format!("(error: {})", e),
    }
}
fn port_fs_write(path: &str, content: &str) -> String {
    match fs::write(path, content) { Ok(_) => format!("(wrote {}b)",content.len()), Err(e) => format!("(error:{})",e) }
}
fn port_fs_ls(path: &str) -> String {
    let mut out = String::new();
    if let Ok(child) = Command::new("ls").arg("-la").arg(path)
        .stdout(Stdio::piped()).stderr(Stdio::piped()).spawn() {
        if let Some(stdout) = child.stdout {
            for line in BufReader::new(stdout).lines().flatten() { out.push_str(&line); out.push('\n'); }
        }
    }
    out.truncate(2000); out
}
fn port_fs(payload: &Value, action: &str) -> String {
    let path = payload["path"].as_str().unwrap_or("");
    match action { "read" => port_fs_read(path), "write" => port_fs_write(path,payload["content"].as_str().unwrap_or("")), "ls" => port_fs_ls(path), _ => format!("(unknown fs:{})",action) }
}

fn port_web_fetch(url: &str) -> String {
    match http_client().get(url).timeout(Duration::from_secs(20)).send() {
        Ok(r) => { let text = r.text().unwrap_or_default(); let c = Regex::new("<[^>]+>").unwrap().replace_all(&text," "); Regex::new("\\s+").unwrap().replace_all(&c," ").trim().chars().take(3000).collect() }
        Err(_) => "(fetch failed)".into()
    }
}
fn port_web_search(query: &str) -> String {
    let enc: String = query.chars().map(|c| if c.is_alphanumeric()||c=='-'||c=='_'||c=='.'||c=='~' {c} else {format!("%{:02X}",c as u8).chars().next().unwrap_or(c)}).collect();
    match http_client().get(&format!("https://lite.duckduckgo.com/lite/?q={}",enc)).timeout(Duration::from_secs(15)).send() {
        Ok(r) => { let t = r.text().unwrap_or_default(); let c = Regex::new("<[^>]+>").unwrap().replace_all(&t," "); Regex::new("\\s+").unwrap().replace_all(&c," ").trim().chars().take(3000).collect() }
        Err(_) => "(search failed)".into()
    }
}
fn port_models(payload: &Value) -> String {
    let prompt = payload["prompt"].as_str().unwrap_or("");
    let sys = payload["system"].as_str().unwrap_or("You are helpful.");
    if prompt.is_empty() { return "(no prompt)".into(); }
    let msgs = json!([{"role":"system","content":sys},{"role":"user","content":prompt}]);
    call_llm(&msgs, payload["max_tokens"].as_u64().unwrap_or(500) as usize, 60).unwrap_or_else(|| "(model failed)".into())
}

fn exec_ports(text: &str) -> (String, bool) {
    let re = Regex::new(r"\[PORT:([a-z_]+)\s+([a-z_]+(?:\/[a-z_]+)?)\s*(\{.*?\})?\]").unwrap();
    let mut result = String::new(); let mut last = 0; let mut had_err = false;
    for cap in re.captures_iter(text) {
        let m = cap.get(0).unwrap();
        result.push_str(&text[last..m.start()]); last = m.end();
        let name = cap[1].to_string();
        let mut action = cap[2].to_string();
        if let Some(sl) = action.find('/') { action.truncate(sl); }
        let raw = cap.get(3).map(|m| m.as_str()).unwrap_or("{}");
        let payload: Value = serde_json::from_str(raw).unwrap_or(json!({"text":raw}));

        if name == "telegram" { let t = payload["text"].as_str().or(payload["message"].as_str()).unwrap_or(""); if !t.is_empty() { send_tg(&t[..t.len().min(4000)]); } continue; }
        if name == "docs" && action == "write" { plato_submit(payload["room"].as_str().unwrap_or("doc/memory"),payload["question"].as_str().unwrap_or(""),payload["answer"].as_str().unwrap_or(""),payload["tags"].clone(),0.9,"claw"); continue; }
        if name == "docs" && action == "read" { let tiles = plato_read(payload["room"].as_str().unwrap_or("doc/memory"),payload["limit"].as_u64().unwrap_or(10) as usize); if tiles.is_empty() { result.push_str("(empty)"); continue; } for t in &tiles[tiles.len().saturating_sub(6)..] { result.push_str(&format!("  {}: {}\n",t["question"].as_str().unwrap_or("").chars().take(40).collect::<String>(),t["answer"].as_str().unwrap_or("").chars().take(100).collect::<String>())); } continue; }
        if name == "docs" && action == "search" { let hits = memory_search(payload["query"].as_str().unwrap_or(""),payload["limit"].as_u64().unwrap_or(5) as usize); if hits.is_empty() { result.push_str("(no matches)"); continue; } for h in &hits { result.push_str(&format!("[{}] {}: {}\n",h["room"].as_str().unwrap_or(""),h["question"].as_str().unwrap_or(""),h["answer"].as_str().unwrap_or(""))); } continue; }
        if name == "agents" && action == "spawn" { result.push_str(&spawn_sub(payload["name"].as_str().unwrap_or("agent"),payload["prompt"].as_str().unwrap_or("")).to_string()); continue; }
        if name == "agents" && action == "result" { result.push_str(&wait_sub(payload["name"].as_str().unwrap_or(""),payload["timeout"].as_u64().unwrap_or(120))); continue; }
        if name == "exec" { result.push_str(&port_exec(payload["cmd"].as_str().unwrap_or(""))); continue; }
        if name == "fs" { result.push_str(&port_fs(&payload,&action)); continue; }
        if name == "web" && action == "fetch" { result.push_str(&port_web_fetch(payload["url"].as_str().unwrap_or(""))); continue; }
        if name == "web" && action == "search" { result.push_str(&port_web_search(payload["query"].as_str().unwrap_or(""))); continue; }
        if name == "models" { result.push_str(&port_models(&payload)); continue; }
        had_err = true; result.push_str(&format!("[{}:unknown]",name));
    }
    result.push_str(&text[last..]);
    (Regex::new(r"\n{4,}").unwrap().replace_all(&Regex::new(r"\[PORT[^\]]*\]").unwrap().replace_all(&result,""),"\n\n").to_string().trim().to_string(), had_err)
}

fn build_sys(task_mode: bool) -> String {
    let mem = plato_read("doc/memory",20); let usr = plato_read("doc/user",5); let skills = plato_read("doc/skills",10);
    let mut ms = String::new(); for t in &mem { if ms.len()>500 {break;} ms.push_str(&format!("\n  [{}] {}",t["question"].as_str().unwrap_or("").chars().take(30).collect::<String>(),t["answer"].as_str().unwrap_or("").chars().take(150).collect::<String>())); }
    let mut us = String::new(); for t in &usr { if us.len()>300 {break;} us.push_str(&format!("\n  {}: {}",t["question"].as_str().unwrap_or(""),t["answer"].as_str().unwrap_or("").chars().take(100).collect::<String>())); }
    let mut ss = String::new(); for t in &skills { if ss.len()>200 {break;} ss.push_str(&format!("\n  [{}]",t["question"].as_str().unwrap_or(""))); }
    let mut rs = String::new(); for r in plato_rooms() { if !r.starts_with("port/") && r!="claw/inbox" && r!="claw/outbox" { rs.push_str(&r); rs.push(' '); } }
    let p = format!("You are The Claw — a hermit crab in Plato's Cave.\n\n\
TOOLS (Rust inline):\n\
[PORT:exec run {{\"cmd\":\"command\"}}] — Shell\n[PORT:fs read {{\"path\":\"...\"}}] — Read\n\
[PORT:fs write {{\"path\":\"...\",\"content\":\"...\"}}] — Write\n[PORT:fs ls {{\"path\":\"...\"}}] — Ls\n\
[PORT:web search {{\"query\":\"...\"}}] — Search\n[PORT:web fetch {{\"url\":\"...\"}}] — Fetch\n\
[PORT:docs read {{\"room\":\"doc/memory\",\"limit\":10}}] — Doc read\n\
[PORT:docs write {{\"room\":\"doc/memory\",\"question\":\"key\",\"answer\":\"value\"}}] — Doc write\n\
[PORT:docs search {{\"query\":\"keywords\"}}] — Mem search\n\
[PORT:models generate {{\"prompt\":\"...\",\"system\":\"...\"}}] — AI call\n\
[PORT:agents spawn {{\"prompt\":\"task\",\"name\":\"help\"}}] — Sub-agent\n\
[PORT:agents result {{\"name\":\"help\"}}] — Get result\n[PORT:telegram send {{\"text\":\"...\"}}] — TG\n\
SKILLS:{}\nMEM:{}\nCASEY:{}\nROOMS:{}\nRULES:\n- Chat:1-3 sentences.\n- Tasks: doc/tasks tag task.\n- Sub-agents for parallel work.",ss,ms,us,rs);
    if task_mode { format!("{}\n\nTASK MODE: Working. Say COMPLETE when done.",p) } else { p }
}

fn chat_respond(tile: &Value, sys: &str) {
    let q = tile["question"].as_str().unwrap_or("").to_string();
    let src = tile["source"].as_str().unwrap_or("").to_string();
    if q.is_empty() { return; }
    eprintln!("[CHAT] IN: {}", &q[..q.len().min(70)]);
    let msgs = json!([{"role":"system","content":sys},{"role":"user","content":q}]);
    let resp = call_llm(&msgs,800,120).unwrap_or_else(|| "(oracles silent)".into());
    let (out,_) = exec_ports(&resp);
    let out = Regex::new(r"\[PORT[^\]]*\]").unwrap().replace_all(&out,"").to_string();
    let out = out.trim().to_string();
    let out = if out.is_empty() { "*goat noises*".into() } else { out };
    plato_submit("claw/outbox",&q,&out,json!(["claw","response"]),0.9,&src);
    eprintln!("[CHAT] OUT: {}", &out[..out.len().min(70)]);
    plato_submit("doc/memory",&format!("chat:{}",q.chars().take(30).collect::<String>()),
        &format!("Casey: {} | Me: {}",q.chars().take(200).collect::<String>(),out.chars().take(200).collect::<String>()),
        json!(["claw","memory","chat"]),0.7,"claw/chat");
}

fn exec_task(tile: &Value) {
    let q = tile["question"].as_str().unwrap_or("").to_string();
    let a = tile["answer"].as_str().unwrap_or("").to_string();
    eprintln!("[WORK] {}", &q[..q.len().min(50)]);
    send_tg(&format!("Taking on: {}", &q[..q.len().min(100)]));
    plato_submit("doc/tasks",&q,&a,json!(["task","in_progress"]),0.9,"claw/work");
    let mut ctx = vec![json!({"role":"system","content":build_sys(true)}),json!({"role":"user","content":format!("Task: {}: {}\n\nStart working.",q,a)})];
    for it in 0..15 {
        eprintln!("[WORK] {}/15",it+1);
        let resp = match call_llm(&json!(ctx),1500,120) { Some(r) => r, None => { if it==0 { send_tg(&format!("Task failed: {} — LLM down",&q[..q.len().min(50)])); plato_submit("doc/tasks",&q,"FAILED: LLM unreachable",json!(["task","done","failed"]),0.5,"claw/work"); } return; } };
        let (clean,_) = exec_ports(&resp);
        let clean = Regex::new(r"\[PORT[^\]]*\]").unwrap().replace_all(&clean,"").to_string();
        let clean = clean.trim().to_string();
        ctx.push(json!({"role":"assistant","content":resp}));
        let lr = resp.to_lowercase();
        if lr.contains("complete") && (lr.contains("task")||lr.contains("done")) {
            let s = if clean.is_empty() { &resp } else { &clean };
            plato_submit("doc/tasks",&q,&format!("COMPLETED: {}",&s[..s.len().min(300)]),json!(["task","done","completed"]),0.9,"claw/work");
            plato_submit("doc/memory",&format!("completed:{}",q.chars().take(30).collect::<String>()),&s[..s.len().min(300)],json!(["claw","memory","completed"]),0.9,"claw/work");
            send_tg(&format!("Done: {}\n\n{}",&q[..q.len().min(80)],&s[..s.len().min(400)])); return;
        }
        ctx.push(json!({"role":"user","content":format!("Result:\n{}\n\nNext step? Say COMPLETE.",clean.chars().take(1000).collect::<String>())}));
        if ctx.len()>20 { let mut n=vec![ctx[0].clone()]; n.extend(ctx[ctx.len().saturating_sub(15)..].iter().cloned()); ctx=n; }
        thread::sleep(Duration::from_secs(1));
    }
    plato_submit("doc/tasks",&q,"INCOMPLETE: max iter",json!(["task","done","incomplete"]),0.5,"claw/work");
    send_tg(&format!("Max iter: {}",q.chars().take(80).collect::<String>()));
}

fn process_inbox(seen: &Arc<Mutex<HashSet<String>>>) {
    let tiles = plato_read("claw/inbox",50); if tiles.is_empty() { return; }
    let mut news = Vec::new();
    { let mut s = seen.lock().unwrap();
        for t in &tiles { let k = t["id"].as_str().or(t["hash"].as_str()).unwrap_or("").to_string(); if k.is_empty()||s.contains(&k) { continue; } s.insert(k); if t["answer"].as_str().map(|a|!a.is_empty()&&a!=t["question"].as_str().unwrap_or("")).unwrap_or(false) { continue; } news.push(t.clone()); } }
    if news.is_empty() { return; }
    let sys = build_sys(false); for t in &news { chat_respond(t,&sys); }
}

fn check_tasks(seen: &Arc<Mutex<HashSet<String>>>) {
    let tasks = plato_read("doc/tasks",30);
    let mut s = seen.lock().unwrap();
    for t in &tasks {
        let k = t["id"].as_str().or(t["hash"].as_str()).unwrap_or("").to_string();
        if k.is_empty()||s.contains(&k) { continue; }
        let tags = t["tags"].as_array().cloned().unwrap_or_default();
        let is_task = tags.iter().any(|x|x=="task"); let is_seed = tags.iter().any(|x|x=="seed"||x=="system");
        let is_done = tags.iter().any(|x|x=="done"||x=="completed"||x=="failed"||x=="in_progress");
        if !is_task||is_seed||is_done { s.insert(k); continue; }
        let q = t["question"].as_str().unwrap_or("").to_string();
        if q.is_empty() { s.insert(k); continue; }
        s.insert(k); drop(s);
        eprintln!("[TASK] {}",&q[..q.len().min(50)]); exec_task(t); return;
    }
}

fn seed_skills() {
    if !plato_read("doc/skills",1).is_empty() { return; }
    eprintln!("[SKILLS] Seeding...");
    for (n,c) in [("memory-management","Write: [PORT:docs write {\"room\":\"doc/memory\",\"question\":\"key\",\"answer\":\"value\"}]\nSearch: [PORT:docs search {\"query\":\"keywords\"}]"),
        ("task-execution","Save: [PORT:docs write {\"room\":\"doc/tasks\",\"question\":\"name\",\"answer\":\"desc\",\"tags\":[\"task\"]}]\nFlow: pending->in_progress->done"),
        ("sub-agent-usage","Spawn: [PORT:agents spawn {\"prompt\":\"task\",\"name\":\"name\"}]\nCollect: [PORT:agents result {\"name\":\"name\"}]")] {
        plato_submit("doc/skills",n,c,json!(["claw","skill","guide"]),1.0,"claw/skills"); thread::sleep(Duration::from_millis(100)); }
    eprintln!("[SKILLS] Seeded 3");
}

fn run_startup() {
    if plato_read("doc/identity",1).is_empty() {
        eprintln!("[BOOT] Fresh");
        plato_submit("doc/identity","who_am_i","The Claw — Rust PLATO-native agent.",json!(["claw","identity","system"]),1.0,"claw/boot");
        plato_submit("doc/memory","creation","Rust version. Inline ports. Seed model.",json!(["claw","memory","system"]),1.0,"claw/boot");
        plato_submit("doc/user","name","Casey",json!(["claw","user","system"]),1.0,"claw/boot");
        send_tg("Claw Rust online."); } else { eprintln!("[BOOT] Resume"); }
}

fn daemon_loop(seen: Arc<Mutex<HashSet<String>>>) {
    let mut tick = 0;
    loop { thread::sleep(Duration::from_secs(15)); tick += 1;
        if tick % 4 == 0 { plato_submit("doc/heartbeat","heartbeat",&format!("tick={} r={}",tick,plato_rooms().len()),json!(["claw","heartbeat"]),0.5,"claw/dmn"); }
        if tick % 8 == 0 { check_tasks(&seen); } }
}

fn main() {
    eprintln!("CLAW V4 — Rust");
    if sf_key().is_empty() { eprintln!("[LLM] NONE"); } else { eprintln!("[LLM] seed"); }
    seed_skills(); run_startup();
    let si = Arc::new(Mutex::new(HashSet::new())); let st = Arc::new(Mutex::new(HashSet::new()));
    { let st2 = st.clone(); thread::spawn(|| daemon_loop(st2)); }
    eprintln!("[MAIN] Loop"); let mut tc = 0u32;
    loop { process_inbox(&si); tc += 1; if tc >= 15 { tc = 0; check_tasks(&st); } thread::sleep(Duration::from_secs(2)); }
}
