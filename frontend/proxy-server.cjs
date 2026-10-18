/**
 * RippleBack MCP Proxy Shim
 * ─────────────────────────
 * Bridges the browser's REST calls to the Python MCP server's
 * Streamable HTTP transport. Runs on port 3001.
 *
 * Why a shim? The MCP Streamable HTTP protocol uses multipart/SSE framing
 * that browsers cannot consume directly without the full MCP SDK. This shim
 * speaks plain JSON on the browser side and speaks MCP on the backend side.
 *
 * Run:  node proxy-server.cjs
 *
 * Endpoints exposed:
 *   GET  /api/mcp/health   → server info + list of tools
 *   POST /api/mcp/tool     → { tool, arguments } → tool result
 */

const express = require('express');
const cors    = require('cors');
const http    = require('http');

const MCP_HOST = process.env.RIPPLEBACK_HOST ?? '127.0.0.1';
const MCP_PORT = Number(process.env.RIPPLEBACK_PORT ?? 8000);
const SHIM_PORT = 3001;

const app = express();
app.use(cors());
app.use(express.json());

// ── MCP JSON-RPC helper ───────────────────────────────────────────────────────

/**
 * Send one JSON-RPC 2.0 request to the MCP server.
 * MCP Streamable HTTP uses POST /mcp with Accept: application/json.
 */
function mcpRequest(method, params) {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify({
      jsonrpc: '2.0',
      id: Date.now(),
      method,
      params,
    });

    const options = {
      hostname: MCP_HOST,
      port: MCP_PORT,
      path: '/mcp',
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json, text/event-stream',
        'Content-Length': Buffer.byteLength(body),
      },
    };

    const req = http.request(options, (res) => {
      let raw = '';
      res.on('data', (chunk) => { raw += chunk; });
      res.on('end', () => {
        // MCP may return SSE (text/event-stream) or plain JSON.
        // SSE lines look like: data: {...}\n\n
        const contentType = res.headers['content-type'] ?? '';
        let parsed;
        if (contentType.includes('text/event-stream')) {
          // Parse SSE: collect the last non-empty data: line.
          const lines = raw.split('\n');
          let lastData = null;
          for (const line of lines) {
            if (line.startsWith('data:')) {
              lastData = line.slice(5).trim();
            }
          }
          if (!lastData) return reject(new Error('Empty SSE stream from MCP server'));
          try { parsed = JSON.parse(lastData); } catch (e) { return reject(e); }
        } else {
          try { parsed = JSON.parse(raw); } catch (e) { return reject(e); }
        }

        if (parsed.error) return reject(new Error(parsed.error.message ?? JSON.stringify(parsed.error)));
        resolve(parsed.result ?? parsed);
      });
    });

    req.on('error', reject);
    req.setTimeout(30_000, () => {
      req.destroy();
      reject(new Error('MCP request timed out after 30s'));
    });
    req.write(body);
    req.end();
  });
}

// ── Endpoints ─────────────────────────────────────────────────────────────────

// Health check — calls tools/list to discover the server's tools
app.get('/api/mcp/health', async (_req, res) => {
  try {
    // MCP initialize handshake
    const initResult = await mcpRequest('initialize', {
      protocolVersion: '2024-11-05',
      capabilities: {},
      clientInfo: { name: 'rippleback-proxy', version: '1.0.0' },
    });

    // List tools
    let tools = [];
    try {
      const toolsResult = await mcpRequest('tools/list', {});
      tools = (toolsResult.tools ?? []).map((t) => t.name);
    } catch {
      // non-fatal
    }

    res.json({
      name: initResult?.serverInfo?.name ?? 'RippleBack',
      version: initResult?.serverInfo?.version ?? 'unknown',
      tools,
    });
  } catch (err) {
    res.status(503).json({ error: err.message });
  }
});

// Tool call — wraps tools/call JSON-RPC method
app.post('/api/mcp/tool', async (req, res) => {
  const { tool, arguments: args } = req.body ?? {};
  if (!tool) {
    return res.status(400).json({ error: 'Missing "tool" field', isError: true });
  }

  try {
    const result = await mcpRequest('tools/call', {
      name: tool,
      arguments: args ?? {},
    });

    // MCP tools/call returns { content: [ { type, text } ], isError? }
    if (result?.isError) {
      const msg = result.content?.[0]?.text ?? 'Tool returned an error';
      return res.status(200).json({ error: msg, isError: true });
    }

    // Parse the text content back to JSON (MCP wraps results as text)
    let parsed;
    try {
      const text = result?.content?.[0]?.text ?? result;
      parsed = typeof text === 'string' ? JSON.parse(text) : text;
    } catch {
      parsed = result;
    }

    res.json({ result: parsed, isError: false });
  } catch (err) {
    res.status(200).json({ error: err.message, isError: true });
  }
});

app.listen(SHIM_PORT, '127.0.0.1', () => {
  console.log(`RippleBack proxy shim → http://127.0.0.1:${SHIM_PORT}`);
  console.log(`  Forwarding to MCP server → http://${MCP_HOST}:${MCP_PORT}/mcp`);
});
