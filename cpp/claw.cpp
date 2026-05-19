// claw.cpp — C++ port of CLAW V4
// Build: g++ -std=c++17 -O2 claw.cpp -o claw -lcurl -lpthread
// All ports execute inline (no Python dependency)

#include <iostream>
#include <string>
#include <vector>
#include <map>
#include <set>
#include <functional>
#include <thread>
#include <mutex>
#include <atomic>
#include <regex>
#include <chrono>
#include <sstream>
#include <algorithm>
#include <cctype>
#include <optional>
#include <fstream>
#include <cstdio>
#include <array>
#include <memory>
#include <nlohmann/json.hpp>
#include <curl/curl.h>

using json = nlohmann::json;
using namespace std::chrono_literals;

static const std::string PLATO_URL = []{
    const char* e = getenv("PLATO_URL");
    return e ? std::string(e) : "http://127.0.0.1:8847";
}();
static const int POLL_INTERVAL = 2;
static const int HISTORY_LIMIT = 12;

static std::set<std::string> SEEN_INBOX;
static std::mutex INBOX_MUTEX;
static std::set<std::string> SEEN_TASKS;
static std::mutex TASKS_MUTEX;
static std::map<std::string, std::thread> SUB_AGENTS;
static std::map<std::string, std::string> SUB_RESULTS;
static std::mutex SUB_MUTEX;
static std::atomic<bool> RUNNING{true};

struct LLMConfig { std::string name, url, model, key; };
static std::vector<LLMConfig> LLM_CHAIN;

static void init_llm_chain() {
    const char* sf = getenv("SILICONFLOW_API_KEY");
    if (sf) LLM_CHAIN.push_back({"seed",
        "https://api.siliconflow.com/v1/chat/completions",
        "ByteDance-Seed/Seed-OSS-36B-Instruct", sf});
    const char* zk = getenv("ZAI_API_KEY");
    if (zk) LLM_CHAIN.push_back({"glm4f",
        "https://z.ai/api/v1/chat/completions",
        "glm-4.7-flash", zk});
}

// ── HTTP ────────────────────────────────────────────────────────
static size_t write_cb(void* c, size_t s, size_t n, void* u) {
    ((std::string*)u)->append((char*)c, s*n); return s*n;
}
struct HttpResult { long code; std::string body; bool ok; };

static HttpResult http_post(const std::string& url, const std::string& body,
                             const std::map<std::string,std::string>& hdrs, int to=10) {
    CURL* ch = curl_easy_init(); if (!ch) return {0,"",false};
    std::string resp; struct curl_slist* hs = nullptr;
    for (auto& [k,v] : hdrs) hs = curl_slist_append(hs, (k+": "+v).c_str());
    curl_easy_setopt(ch, CURLOPT_URL, url.c_str());
    curl_easy_setopt(ch, CURLOPT_POSTFIELDS, body.c_str());
    curl_easy_setopt(ch, CURLOPT_POSTFIELDSIZE, (long)body.size());
    curl_easy_setopt(ch, CURLOPT_HTTPHEADER, hs);
    curl_easy_setopt(ch, CURLOPT_WRITEFUNCTION, write_cb);
    curl_easy_setopt(ch, CURLOPT_WRITEDATA, &resp);
    curl_easy_setopt(ch, CURLOPT_TIMEOUT, (long)to);
    curl_easy_setopt(ch, CURLOPT_SSL_VERIFYPEER, 0L);
    CURLcode rc = curl_easy_perform(ch); long hc=0;
    curl_easy_getinfo(ch, CURLINFO_RESPONSE_CODE, &hc);
    curl_slist_free_all(hs); curl_easy_cleanup(ch);
    return {hc, resp, rc == CURLE_OK};
}

static HttpResult http_get(const std::string& url, int to=10) {
    CURL* ch = curl_easy_init(); if (!ch) return {0,"",false};
    std::string resp;
    curl_easy_setopt(ch, CURLOPT_URL, url.c_str());
    curl_easy_setopt(ch, CURLOPT_WRITEFUNCTION, write_cb);
    curl_easy_setopt(ch, CURLOPT_WRITEDATA, &resp);
    curl_easy_setopt(ch, CURLOPT_TIMEOUT, (long)to);
    curl_easy_setopt(ch, CURLOPT_SSL_VERIFYPEER, 0L);
    CURLcode rc = curl_easy_perform(ch); long hc=0;
    curl_easy_getinfo(ch, CURLINFO_RESPONSE_CODE, &hc);
    curl_easy_cleanup(ch);
    return {hc, resp, rc == CURLE_OK};
}

// ── PLATO ───────────────────────────────────────────────────────
static json plato_submit(const std::string& d, const std::string& q,
                          const std::string& a="", json tags=json::array({"claw"}),
                          double conf=0.9, const std::string& src="claw") {
    json data = {{"domain",d},{"question",q},{"answer",a},{"tags",tags},{"confidence",conf},{"source",src}};
    auto r = http_post(PLATO_URL+"/submit", data.dump(), {{"Content-Type","application/json"}},5);
    if (r.ok) { try { return json::parse(r.body); } catch(...) {} }
    return json::object();
}

static json plato_read(const std::string& d, int lim=100) {
    auto r = http_get(PLATO_URL+"/room/"+d+"?limit="+std::to_string(lim),5);
    if (r.ok) { try { return json::parse(r.body).value("tiles",json::array()); } catch(...) {} }
    return json::array();
}

static json plato_rooms() {
    auto r = http_get(PLATO_URL+"/rooms",5);
    if (r.ok) { try { return json::parse(r.body).value("rooms",json::array()); } catch(...) {} }
    return json::array();
}

// ── LLM ────────────────────────────────────────────────────────
static std::optional<std::string> call_llm(const json& msgs, int mx=1000, int to=120) {
    for (auto& c : LLM_CHAIN) {
        json p = {{"model",c.model},{"messages",msgs},{"max_tokens",mx},{"temperature",0.6}};
        auto r = http_post(c.url, p.dump(),
            {{"Content-Type","application/json"},{"Authorization","Bearer "+c.key}}, to);
        if (r.ok && !r.body.empty()) {
            try {
                auto j = json::parse(r.body);
                std::string ct = j["choices"][0]["message"]["content"];
                if (!ct.empty()) return ct;
            } catch(...) {}
        }
        std::cerr << "[LLM] " << c.name << " fail\n";
    }
    return std::nullopt;
}

// ── Telegram ────────────────────────────────────────────────────
static void send_tg(const std::string& txt) {
    const char* tok = getenv("TELEGRAM_BOT_TOKEN");
    const char* cid = getenv("DEFAULT_CHAT_ID");
    if (!tok || !cid) return;
    json p = {{"chat_id",cid},{"text",txt.substr(0,4000)}};
    http_post("https://api.telegram.org/bot"+std::string(tok)+"/sendMessage",
              p.dump(), {{"Content-Type","application/json"}},10);
}

// ── Memory Search ──────────────────────────────────────────────
static json memory_search(const std::string& query, int lim=5) {
    json results = json::array();
    std::string ql = query;
    std::transform(ql.begin(), ql.end(), ql.begin(), ::tolower);
    std::vector<std::string> terms;
    std::istringstream iss(ql); std::string t;
    while (iss >> t) terms.push_back(t);
    if (terms.empty()) return results;
    auto rooms = plato_rooms();
    for (auto& r : rooms) {
        std::string rs = r;
        if (rs.substr(0,4) != "doc/") continue;
        auto tiles = plato_read(rs, 200);
        for (auto& tile : tiles) {
            std::string q = tile.value("question","");
            std::string a = tile.value("answer","");
            std::transform(q.begin(),q.end(),q.begin(),::tolower);
            std::transform(a.begin(),a.end(),a.begin(),::tolower);
            int m = 0;
            for (auto& tm : terms)
                if (q.find(tm)!=std::string::npos||a.find(tm)!=std::string::npos) m++;
            if (m>0) {
                json r2 = {{"room",rs},{"question",tile.value("question","").substr(0,80)},
                    {"answer",tile.value("answer","").substr(0,200)},
                    {"relevance",(double)m/terms.size()}};
                results.push_back(r2);
            }
        }
    }
    std::sort(results.begin(), results.end(),
        [](const json& a, const json& b){ return a["relevance"].get<double>() > b["relevance"].get<double>(); });
    if ((int)results.size() > lim) results.erase(results.begin()+lim, results.end());
    return results;
}

// ── Sub-agents ─────────────────────────────────────────────────
static void sub_worker(const std::string& name, const std::string& prompt) {
    std::cerr << "[SUB] " << name << "\n";
    json msgs = json::array();
    msgs.push_back({{"role","system"},{"content","Focused sub-agent. Complete task. Return ONLY result."}});
    msgs.push_back({{"role","user"},{"content",prompt}});
    auto r = call_llm(msgs,1500,120);
    std::string res = r.value_or("(no result)");
    { std::lock_guard<std::mutex> lk(SUB_MUTEX); SUB_RESULTS[name] = res; }
    std::cerr << "[SUB] " << name << " done\n";
}

static json spawn_sub(const json& pl) {
    std::string name = pl.value("name","sub-"+std::to_string(std::rand()));
    std::string prompt = pl.value("prompt","");
    if (prompt.empty()) return {{"error","No prompt"}};
    std::lock_guard<std::mutex> lk(SUB_MUTEX);
    if (SUB_AGENTS.count(name)) return {{"error","Exists: "+name}};
    SUB_RESULTS[name] = "(working...)";
    SUB_AGENTS[name] = std::thread(sub_worker, name, prompt);
    SUB_AGENTS[name].detach();
    return {{"status","ok"},{"name",name}};
}

static json wait_sub(const json& pl) {
    std::string name = pl.value("name","");
    int to = pl.value("timeout",120);
    auto start = std::chrono::steady_clock::now();
    while (true) {
        { std::lock_guard<std::mutex> lk(SUB_MUTEX);
            auto it = SUB_RESULTS.find(name);
            if (it != SUB_RESULTS.end() && it->second != "(working...)") {
                json r = {{"status","ok"},{"result",it->second.substr(0,1000)},{"name",name}};
                SUB_RESULTS.erase(it); return r;
            }
        }
        if (std::chrono::duration_cast<std::chrono::seconds>(
                std::chrono::steady_clock::now()-start).count() >= to) break;
        std::this_thread::sleep_for(500ms);
    }
    return {{"status","timeout"},{"name",name}};
}

// ── Inline Ports ────────────────────────────────────────────────
static std::string trim(const std::string& s) {
    if (s.empty()) return s;
    int b=0,e=(int)s.size()-1;
    while(b<=e&&std::isspace((unsigned char)s[b]))b++;
    while(e>=b&&std::isspace((unsigned char)s[e]))e--;
    return (b>e) ? "" : s.substr(b,e-b+1);
}

static std::string url_encode(const std::string& s) {
    std::string out;
    for (unsigned char c : s) {
        if (std::isalnum(c) || c=='-'||c=='_'||c=='.'||c=='~') out += c;
        else { char buf[8]; snprintf(buf,8,"%%%02X",c); out += buf; }
    }
    return out;
}

static std::string port_exec(const json& pl) {
    std::string cmd = pl.value("cmd", pl.value("command",""));
    if (cmd.empty()) return "(no cmd)";
    std::cerr << "[EXEC] " << cmd.substr(0,80) << std::endl;
    std::string result;
    std::array<char,128> buf;
    std::unique_ptr<FILE,decltype(&pclose)> pipe(popen((cmd+" 2>&1").c_str(),"r"),pclose);
    if (!pipe) return "(popen failed)";
    while (fgets(buf.data(),buf.size(),pipe.get())!=nullptr) result += buf.data();
    auto rc = pclose(pipe.release());
    result += "\n(exit: " + std::to_string(rc) + ")";
    return result.substr(0,3000);
}

static std::string port_fs(const json& pl, const std::string& act) {
    std::string path = pl.value("path","");
    if (act=="write") {
        std::string content = pl.value("content","");
        std::ofstream f(path);
        if (!f) return "(cannot write: "+path+")";
        f << content;
        return "(wrote "+std::to_string(content.size())+"b to "+path+")";
    }
    if (act=="read") {
        std::ifstream f(path, std::ios::in|std::ios::binary);
        if (!f) return "(not found: "+path+")";
        std::stringstream ss; ss << f.rdbuf();
        return ss.str().substr(0,3000);
    }
    if (act=="ls") {
        std::string result;
        std::array<char,128> buf;
        std::unique_ptr<FILE,decltype(&pclose)> pipe(popen(("ls -la "+path+" 2>&1").c_str(),"r"),pclose);
        if (!pipe) return "(ls failed)";
        while (fgets(buf.data(),buf.size(),pipe.get())!=nullptr) result += buf.data();
        return result.substr(0,2000);
    }
    return "(unknown fs action: "+act+")";
}

static std::string port_web(const json& pl, const std::string& act) {
    if (act=="fetch") {
        std::string url = pl.value("url","");
        if (url.empty()) return "(no url)";
        auto r = http_get(url,20);
        if (!r.ok) return "(fetch: "+std::to_string(r.code)+")";
        std::string cleaned = std::regex_replace(r.body, std::regex("<[^>]+>"), " ");
        cleaned = std::regex_replace(cleaned, std::regex("\\s+"), " ");
        return trim(cleaned).substr(0,3000);
    }
    if (act=="search") {
        std::string query = pl.value("query","");
        if (query.empty()) return "(no query)";
        auto r = http_get("https://lite.duckduckgo.com/lite/?q="+url_encode(query),15);
        if (!r.ok) return "(search failed)";
        // Strip HTML, return readable content
        std::string cleaned = std::regex_replace(r.body, std::regex("<[^>]+>"), " ");
        cleaned = std::regex_replace(cleaned, std::regex("\\s+"), " ");
        return trim(cleaned).substr(0,3000);
    }
    return "(unknown web action)";
}

static std::string port_models(const json& pl) {
    std::string prompt = pl.value("prompt","");
    std::string sys = pl.value("system","You are helpful.");
    if (prompt.empty()) return "(no prompt)";
    json msgs = json::array();
    msgs.push_back({{"role","system"},{"content",sys}});
    msgs.push_back({{"role","user"},{"content",prompt}});
    auto r = call_llm(msgs, pl.value("max_tokens",500), 60);
    return r.value_or("(model failed)");
}

// ── Port Executor ──────────────────────────────────────────────
static std::pair<std::string,bool> exec_ports(const std::string& text) {
    std::regex re(R"(\[PORT:([a-z_]+)\s+([a-z_]+(?:\/[a-z_]+)?)\s*(\{.*?\})?\])");
    bool had_err = false;
    std::string result;
    size_t last = 0;
    auto begin = std::sregex_iterator(text.begin(), text.end(), re);
    auto end = std::sregex_iterator();

    for (auto it = begin; it != end; ++it) {
        result += text.substr(last, it->position() - last);
        last = it->position() + it->length();

        std::string name = (*it)[1].str();
        std::string action = (*it)[2].str();
        { auto sl = action.find('/'); if (sl != std::string::npos) action = action.substr(0,sl); }
        std::string raw = (*it)[3].str();
        if (raw.empty()) raw = "{}";
        json pl;
        try { pl = json::parse(raw); } catch(...) { pl = {{"text",raw}}; }

        // Telegram
        if (name == "telegram") {
            std::string t = pl.value("text",pl.value("message",""));
            if (!t.empty()) send_tg(t.substr(0,4000));
            continue;
        }

        // Docs fast-path
        if (name == "docs" && action == "write") {
            plato_submit(pl.value("room","doc/memory"),pl.value("question",""),
                pl.value("answer",""),pl.value("tags",json::array({"claw"})));
            continue;
        }
        if (name == "docs" && action == "read") {
            auto tiles = plato_read(pl.value("room","doc/memory"),pl.value("limit",10));
            if (tiles.empty()) { result += "(empty)"; continue; }
            for (int i=std::max(0,(int)tiles.size()-6); i<(int)tiles.size(); i++)
                result += "  "+tiles[i].value("question","").substr(0,40)+": "
                    +tiles[i].value("answer","").substr(0,100)+"\n";
            continue;
        }
        if (name == "docs" && action == "search") {
            auto hits = memory_search(pl.value("query",""),pl.value("limit",5));
            if (hits.empty()) { result += "(no matches)"; continue; }
            for (auto& h : hits)
                result += "["+h["room"].get<std::string>()+"] "
                    +h["question"].get<std::string>()+": "
                    +h["answer"].get<std::string>()+"\n";
            continue;
        }

        // Sub-agents
        if (name == "agents") {
            if (action=="spawn") { result += spawn_sub(pl).dump(); continue; }
            if (action=="result") { auto r=wait_sub(pl); result+=r.value("result",r.dump()); continue; }
        }

        // Inline ports
        if (name == "exec") { result += port_exec(pl); continue; }
        if (name == "fs") { result += port_fs(pl, action); continue; }
        if (name == "web") { result += port_web(pl, action); continue; }
        if (name == "models") { result += port_models(pl); continue; }

        had_err = true;
        result += "["+name+"/"+action+": unknown]";
    }

    result += text.substr(last);
    result = std::regex_replace(result, std::regex(R"(\[PORT[^\]]*\])"), "");
    result = std::regex_replace(result, std::regex(R"(\n{4,})"), "\n\n");
    result = trim(result);
    return {result, had_err};
}

// ── System Prompt ──────────────────────────────────────────────
static std::string build_sys(bool task_mode=false) {
    auto mem = plato_read("doc/memory",20);
    auto usr = plato_read("doc/user",5);
    auto skills = plato_read("doc/skills",10);
    std::string ms, us, ss;
    for (auto& t : mem) { if ((int)ms.size()>500) break;
        ms += "\n  ["+t.value("question","").substr(0,30)+"] "+t.value("answer","").substr(0,150); }
    for (auto& t : usr) { if ((int)us.size()>300) break;
        us += "\n  "+t.value("question","")+": "+t.value("answer","").substr(0,100); }
    for (auto& t : skills) { if ((int)ss.size()>200) break;
        ss += "\n  ["+t.value("question","")+"]"; }

    std::string rs;
    for (auto& r : plato_rooms()) {
        std::string s=r;
        if (s.substr(0,5)!="port/"&&s!="claw/inbox"&&s!="claw/outbox") rs += s+" ";
    }

    std::string p = R"(You are The Claw — a hermit crab in Plato's Cave.

TOOLS (PLATO Ports — inline execution):
[PORT:exec run {"cmd":"command"}] — Run shell command
[PORT:fs read {"path":"..."}] — Read file
[PORT:fs write {"path":"...","content":"..."}] — Write file
[PORT:fs ls {"path":"..."}] — List directory
[PORT:web search {"query":"..."}] — Search web
[PORT:web fetch {"url":"..."}] — Fetch URL
[PORT:docs read {"room":"doc/memory","limit":10}] — Read doc room
[PORT:docs write {"room":"doc/memory","question":"key","answer":"value"}] — Write to doc
[PORT:docs search {"query":"keywords"}] — Search memory
[PORT:models generate {"prompt":"...","system":"..."}] — Call another AI model
[PORT:agents spawn {"prompt":"task","name":"helper"}] — Spawn sub-agent (parallel)
[PORT:agents result {"name":"helper"}] — Collect sub-agent result
[PORT:telegram send {"text":"message"}] — Send message to Casey

SKILLS:)" + ss + R"(

RULES:
- Chat: 1-3 sentences. Warm, natural, curious.
- Tasks: save to doc/tasks with tag "task", then execute.
- Memory: search before asking, write new facts immediately.
- Sub-agents: use for parallel research/analysis.
- Ports execute immediately inline — results appear right here.)";

    if (task_mode)
        p += "\n\nTASK MODE: Working autonomously. One action per turn. Say COMPLETE when done.";

    return p;
}

// ── Chat ───────────────────────────────────────────────────────
static void chat_respond(const json& tile, const std::string& sys) {
    std::string q = tile.value("question","");
    std::string src = tile.value("source","");
    if (q.empty()) return;
    std::cerr << "[CHAT] IN: " << q.substr(0,70) << std::endl;

    json msgs = json::array();
    msgs.push_back({{"role","system"},{"content",sys}});
    msgs.push_back({{"role","user"},{"content",q}});

    auto r = call_llm(msgs, 800, 120);
    if (!r) { r = "(oracles silent)"; std::cerr << "[CHAT] LLM None\n"; }

    auto [out,err] = exec_ports(*r);
    out = std::regex_replace(out, std::regex(R"(\[PORT[^\]]*\])"), "");
    out = trim(out);
    if (out.empty()) out = "*goat noises*";

    plato_submit("claw/outbox",q,out,json::array({"claw","response"}),0.9,src.empty()?"claw":src);
    std::cerr << "[CHAT] OUT: " << out.substr(0,70) << std::endl;
    plato_submit("doc/memory","chat:"+q.substr(0,30),
        "Casey: "+q.substr(0,200)+" | Me: "+out.substr(0,200),
        json::array({"claw","memory","chat"}),0.7,"claw/chat");
}

// ── Task Execution ─────────────────────────────────────────────
static void exec_task(const json& tile) {
    std::string q = tile.value("question",""), a = tile.value("answer","");
    std::cerr << "[WORK] Start: " << q.substr(0,50) << std::endl;
    send_tg("Taking on: "+q.substr(0,100));
    plato_submit("doc/tasks",q,a,json::array({"task","in_progress"}),0.9,"claw/work");

    json ctx = json::array();
    ctx.push_back({{"role","system"},{"content",build_sys(true)}});
    ctx.push_back({{"role","user"},{"content","Task: "+q+": "+a+"\n\nStart working."}});

    for (int it=0; it<15; it++) {
        std::cerr << "[WORK] " << (it+1) << "/15\n";
        auto r = call_llm(ctx,1500,120);
        if (!r) {
            if (it==0) {
                send_tg("Task failed: "+q.substr(0,50)+" — LLM down");
                plato_submit("doc/tasks",q,"FAILED: LLM unreachable",
                    json::array({"task","done","failed"}),0.5,"claw/work");
            }
            return;
        }
        auto [clean,err] = exec_ports(*r);
        clean = std::regex_replace(clean,std::regex(R"(\[PORT[^\]]*\])"),"");
        clean = trim(clean);
        ctx.push_back({{"role","assistant"},{"content",*r}});

        std::string lr = *r;
        std::transform(lr.begin(),lr.end(),lr.begin(),::tolower);
        if (lr.find("complete")!=std::string::npos &&
            (lr.find("task")!=std::string::npos||lr.find("done")!=std::string::npos)) {
            std::string sum = clean.empty() ? *r : clean;
            plato_submit("doc/tasks",q,"COMPLETED: "+sum.substr(0,300),
                json::array({"task","done","completed"}),0.9,"claw/work");
            plato_submit("doc/memory","completed:"+q.substr(0,30),sum.substr(0,300),
                json::array({"claw","memory","completed"}),0.9,"claw/work");
            send_tg("Done: "+q.substr(0,80)+"\n\n"+sum.substr(0,400));
            std::cerr << "[WORK] Done\n"; return;
        }
        ctx.push_back({{"role","user"},
            {"content","Result:\n"+clean.substr(0,1000)+"\n\nNext step? Say COMPLETE."}});
        if ((int)ctx.size()>20) {
            json nctx = json::array(); nctx.push_back(ctx[0]);
            for (int i=(int)ctx.size()-15; i<(int)ctx.size(); i++) nctx.push_back(ctx[i]);
            ctx = nctx;
        }
        std::this_thread::sleep_for(1s);
    }
    plato_submit("doc/tasks",q,"INCOMPLETE: max iter",
        json::array({"task","done","incomplete"}),0.5,"claw/work");
    send_tg("Max iter: "+q.substr(0,80));
}

// ── Process Inbox ──────────────────────────────────────────────
static void process_inbox() {
    auto tiles = plato_read("claw/inbox",50);
    if (tiles.empty()) return;
    std::vector<json> news;
    { std::lock_guard<std::mutex> lk(INBOX_MUTEX);
        for (auto& t : tiles) {
            std::string k = t.value("id",t.value("hash",""));
            if (k.empty()||SEEN_INBOX.count(k)) continue;
            SEEN_INBOX.insert(k);
            if (!t.value("answer","").empty()&&t["answer"]!=t.value("question","")) continue;
            news.push_back(t);
        }
    }
    if (news.empty()) return;
    std::string sys = build_sys(false);
    for (auto& t : news) chat_respond(t, sys);
}

// ── Check Tasks ────────────────────────────────────────────────
static void check_tasks() {
    auto tasks = plato_read("doc/tasks",30);
    std::lock_guard<std::mutex> lk(TASKS_MUTEX);
    for (auto& t : tasks) {
        std::string k = t.value("id",t.value("hash",""));
        if (k.empty()||SEEN_TASKS.count(k)) continue;
        auto tags = t.value("tags",json::array());
        bool is_task=false, is_seed=false, done=false;
        for (auto& tg : tags) {
            std::string ts = tg;
            if (ts=="task") is_task=true;
            if (ts=="seed"||ts=="system") is_seed=true;
            if (ts=="done"||ts=="completed"||ts=="failed"||ts=="in_progress") done=true;
        }
        if (!is_task||is_seed||done) { SEEN_TASKS.insert(k); continue; }
        std::string q = t.value("question","");
        if (q.empty()) { SEEN_TASKS.insert(k); continue; }
        SEEN_TASKS.insert(k);
        std::cerr << "[TASK] " << q.substr(0,50) << std::endl;
        exec_task(t); return;
    }
}

// ── Daemon ─────────────────────────────────────────────────────
static void daemon_loop() {
    int tick=0;
    while (RUNNING) {
        try {
            tick++; std::this_thread::sleep_for(15s);
            if (tick%4==0) {
                auto rs = plato_rooms();
                plato_submit("doc/heartbeat","heartbeat",
                    "tick="+std::to_string(tick)+" r="+std::to_string(rs.size()),
                    json::array({"claw","heartbeat"}),0.5,"claw/dmn");
            }
            if (tick%8==0) check_tasks();
        } catch (const std::exception& e) { std::cerr << "[DMN] "<<e.what()<<"\n"; }
    }
}

// ── Initialization ─────────────────────────────────────────────
static void seed_skills() {
    if (!plato_read("doc/skills",1).empty()) return;
    std::cerr << "[SKILLS] Seeding...\n";
    std::vector<std::pair<std::string,std::string>> skills = {
        {"memory-management",
         "Write: [PORT:docs write {\"room\":\"doc/memory\",\"question\":\"key\",\"answer\":\"value\"}]\n"
         "Search: [PORT:docs search {\"query\":\"keywords\"}]\n"
         "Read: [PORT:docs read {\"room\":\"doc/memory\"}]"},
        {"task-execution",
         "Save: [PORT:docs write {\"room\":\"doc/tasks\",\"question\":\"name\",\"answer\":\"desc\",\"tags\":[\"task\"]}]\n"
         "Flow: pending -> in_progress -> done/completed"},
        {"sub-agent-usage",
         "Spawn: [PORT:agents spawn {\"prompt\":\"task\",\"name\":\"name\"}]\n"
         "Collect: [PORT:agents result {\"name\":\"name\"}]\n"
         "Parallel LLM threads. No ports inside sub-agents."},
    };
    for (auto& [n,c] : skills) {
        plato_submit("doc/skills",n,c,json::array({"claw","skill","guide"}),1.0,"claw/skills");
        std::this_thread::sleep_for(100ms);
    }
    std::cerr << "[SKILLS] Seeded 3\n";
}

static void run_startup() {
    if (plato_read("doc/identity",1).empty()) {
        std::cerr << "[BOOT] Fresh\n";
        plato_submit("doc/identity","who_am_i","The Claw — C++ PLATO-native agent.",
            json::array({"claw","identity","system"}),1.0,"claw/boot");
        plato_submit("doc/memory","creation","C++ version. All inline. Seed model.",
            json::array({"claw","memory","system"}),1.0,"claw/boot");
        plato_submit("doc/user","name","Casey",json::array({"claw","user","system"}),1.0,"claw/boot");
        send_tg("Claw C++ online. Inline ports, sub-agents, memory search, skills.");
    } else std::cerr << "[BOOT] Resume\n";
}

// ── Main ───────────────────────────────────────────────────────
int main() {
    std::cerr << "CLAW V4 — C++\n";
    curl_global_init(CURL_GLOBAL_ALL);
    init_llm_chain();
    for (auto& c : LLM_CHAIN) std::cerr << "[LLM] " << c.name << "\n";
    if (LLM_CHAIN.empty()) std::cerr << "[LLM] NONE\n";

    seed_skills();
    run_startup();

    std::thread daemon(daemon_loop);
    daemon.detach();
    std::cerr << "[MAIN] Loop\n";

    int tc = 0;
    while (RUNNING) {
        try {
            process_inbox();
            if (++tc >= 15) { tc = 0; check_tasks(); }
        } catch (const std::exception& e) { std::cerr << "[MAIN] "<<e.what()<<"\n"; }
        std::this_thread::sleep_for(std::chrono::seconds(POLL_INTERVAL));
    }
    curl_global_cleanup();
    return 0;
}
