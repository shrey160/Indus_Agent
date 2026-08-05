import http.client
import sys

try:
    conn = http.client.HTTPConnection("localhost", 9000, timeout=3)
    conn.request("GET", "/mcp", headers={"Accept": "text/event-stream"})
    conn.getresponse()
    conn.close()
except Exception:
    sys.exit(1)
sys.exit(0)
