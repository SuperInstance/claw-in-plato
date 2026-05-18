"""
port/web — Web search and page fetch on the internet.
Actions: search (web search), fetch (get page content)
"""
import json, urllib.request, urllib.parse
from ports import Port

class WebPort(Port):
    name = "web"
    description = "Search the web and fetch page content"
    room = "port/web"
    
    def _search(self, query, count=5):
        """Fallback: use DuckDuckGo's lite search via plain HTTP."""
        try:
            url = "https://lite.duckduckgo.com/lite/"
            data = urllib.parse.urlencode({"q": query}).encode()
            req = urllib.request.Request(url, data=data)
            with urllib.request.urlopen(req, timeout=10) as r:
                html = r.read().decode()
                # Extract result snippets
                results = []
                for line in html.split("\n"):
                    if 'class="result-snippet"' in line or 'class="result__snippet"' in line:
                        results.append(line.strip()[:300])
                    if len(results) >= count:
                        break
                return results if results else ["No results found"]
        except Exception as e:
            return [f"Search error: {e}"]
    
    def _fetch(self, url, max_chars=5000):
        """Fetch a URL and return text content."""
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Claw/1.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                content = r.read().decode("utf-8", errors="replace")
                return content[:max_chars]
        except Exception as e:
            return f"Fetch error: {e}"
    
    def handle(self, action, payload, tile):
        if action == "search":
            query = payload.get("query") or payload.get("text", "")
            count = payload.get("count", 5)
            results = self._search(query, count)
            return {"status": "ok", "query": query, "results": results}
        
        elif action == "fetch":
            url = payload.get("url") or payload.get("text", "")
            max_chars = payload.get("max_chars", 5000)
            content = self._fetch(url, max_chars)
            return {"status": "ok", "url": url, "content": content[:max_chars]}
        
        return {"error": f"Unknown action: {action}"}
