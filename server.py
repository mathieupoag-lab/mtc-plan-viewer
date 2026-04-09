#!/usr/bin/env python3
"""Local server for Estimate Comparison Tool.
Serves static files and proxies LLM requests to Anthropic Claude API."""

import http.server
import json
import urllib.request
import urllib.error
import ssl
import os

PORT = 8080

PARSE_PROMPT = """You are parsing a construction vendor estimate, quote, or bid document. Extract ALL individual line items.

For each line item return:
- description: Specific material description (include size, material, type, class, spec)
- quantity: Numeric quantity (0 if not listed)
- unit: Unit of measure (EA, LF, SY, CY, TON, LS, GAL, etc.)
- unitPrice: Price per unit as a number (0 if not listed)
- total: Extended/total price as a number (0 if not listed)

Rules:
- Include EVERY priced line item in the document
- Do NOT include subtotals, totals, tax, freight/shipping, or header/section labels
- If unit price is missing but total and qty exist, calculate unitPrice = total / qty
- If total is missing but unitPrice and qty exist, calculate total = unitPrice * qty
- Prices and quantities must be plain numbers (no $, commas, or text)
- Be precise with descriptions - "8\" DIP CL350 18' Lengths" not just "Pipe"

Return ONLY a valid JSON object in this exact format, nothing else:
{"items": [{"description": "...", "quantity": 0, "unit": "...", "unitPrice": 0, "total": 0}]}

--- DOCUMENT TEXT ---
"""

MATCH_PROMPT = """You are a construction estimator comparing bids from multiple vendors. Match line items that refer to the SAME product/material across vendors, even if described differently.

For example these all match:
- "8\" DIP CL 350" = "8 IN DUCTILE IRON PIPE CL350" = "8\" DI PIPE CLASS 350"
- "6\" GV OL" = "6 IN GATE VALVE OPEN LEFT" = "6\" GATE VALVE O.L."
- "FH 5-1/4\" SHOE" = "FIRE HYDRANT W/ 5.25\" SHOE"

For each matched group return:
- description: Clean standardized description
- unit: Unit of measure
- vendors: Object with vendor name as key, value = {qty, unitPrice, total}

Rules:
- Match by WHAT THE PRODUCT IS, not just text similarity
- Same product in different sizes = DIFFERENT items (8\" pipe != 6\" pipe)
- Items from only one vendor should still be included as their own group
- Do NOT include subtotals, tax, freight, or non-material items
- Preserve each vendor's original qty, unitPrice, total values exactly

Return ONLY valid JSON:
{"matched": [{"description": "...", "unit": "...", "vendors": {"Vendor Name": {"qty": 0, "unitPrice": 0, "total": 0}}}]}

--- VENDOR DATA ---
"""


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = json.loads(self.rfile.read(length)) if length > 0 else {}

        if self.path == '/api/parse':
            self._call_llm(PARSE_PROMPT, body.get('text', ''), body.get('apiKey', ''))
        elif self.path == '/api/match':
            self._call_llm(MATCH_PROMPT, body.get('text', ''), body.get('apiKey', ''))
        else:
            self.send_response(404)
            self.end_headers()

    def _call_llm(self, prompt, user_text, api_key):
        if not api_key:
            self._send_json(400, {'error': 'API key required. Enter your Anthropic API key in Settings.'})
            return

        payload = json.dumps({
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 16384,
            "messages": [{"role": "user", "content": prompt + user_text}]
        }).encode()

        req = urllib.request.Request(
            'https://api.anthropic.com/v1/messages',
            data=payload,
            headers={
                'Content-Type': 'application/json',
                'x-api-key': api_key,
                'anthropic-version': '2023-06-01'
            },
            method='POST'
        )

        ctx = ssl.create_default_context()

        try:
            resp = urllib.request.urlopen(req, context=ctx, timeout=120)
            result = json.loads(resp.read())
            text = result.get('content', [{}])[0].get('text', '{}')
            self._send_json(200, {'result': text})
        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.fp else str(e)
            self._send_json(e.code, {'error': f'API error: {error_body}'})
        except Exception as e:
            self._send_json(500, {'error': str(e)})

    def _send_json(self, code, obj):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(obj).encode())

    def log_message(self, format, *args):
        msg = args[0] if args else ''
        if '/api/' in str(msg):
            super().log_message(format, *args)


if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)) or '.')
    server = http.server.HTTPServer(('', PORT), Handler)
    print(f"Estimate Comparison Server running at http://localhost:{PORT}")
    print(f"Open http://localhost:{PORT}/estimate-vendors.html")
    print("Press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
