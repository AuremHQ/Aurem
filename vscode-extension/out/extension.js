"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || function (mod) {
    if (mod && mod.__esModule) return mod;
    var result = {};
    if (mod != null) for (var k in mod) if (k !== "default" && Object.prototype.hasOwnProperty.call(mod, k)) __createBinding(result, mod, k);
    __setModuleDefault(result, mod);
    return result;
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.deactivate = exports.activate = exports.parseMcpSsePayload = void 0;
/**
 * AUREM CTO — VS Code extension (Iter 212m-174)
 *
 * Uses `sk-aurem-` API key stored in VS Code SecretStorage.  All ORA
 * calls route through the MCP server (JSON-RPC 2.0 at
 * `/api/aurem-dev/mcp`) so this extension shares the same auth story
 * as Cursor / Claude Desktop / Claude Code CLI.
 */
const vscode = __importStar(require("vscode"));
const path = __importStar(require("path"));
const SECRET_KEY = 'aurem.apiKey';
const MCP_PATH = '/api/aurem-dev/mcp';
async function getApiKey(context) {
    return context.secrets.get(SECRET_KEY);
}
async function setApiKey(context, key) {
    if (key) {
        await context.secrets.store(SECRET_KEY, key);
    }
    else {
        await context.secrets.delete(SECRET_KEY);
    }
}
/**
 * Parse a `text/event-stream` body carrying JSON-RPC 2.0 messages
 * (one `data: {...}` line per message, the MCP streamable-HTTP shape).
 * Returns the `result` of the last message that carries one; an
 * `error` message throws immediately. Skips `[DONE]` terminators and
 * non-data lines (`event:`, `:comment`, blank). Exported for unit
 * tests — pure function, no vscode dependency.
 */
function parseMcpSsePayload(text) {
    let lastResult;
    let sawResult = false;
    for (const line of text.split(/\r?\n/)) {
        const trimmed = line.trim();
        if (!trimmed.startsWith('data:'))
            continue;
        const data = trimmed.slice('data:'.length).trim();
        if (!data || data === '[DONE]')
            continue;
        let msg;
        try {
            msg = JSON.parse(data);
        }
        catch {
            throw new Error('MCP server returned an SSE event that is not valid JSON');
        }
        if (msg.error)
            throw new Error(msg.error.message || 'RPC error');
        if ('result' in msg) {
            lastResult = msg.result;
            sawResult = true;
        }
    }
    if (!sawResult) {
        throw new Error('MCP server returned an SSE stream with no result');
    }
    return lastResult;
}
exports.parseMcpSsePayload = parseMcpSsePayload;
/**
 * Call any JSON-RPC 2.0 method on the MCP server.
 * Throws on transport / RPC errors so the caller can render a toast.
 *
 * Part VII fix 3(a): the request advertises
 * `Accept: application/json, text/event-stream`, so the server may
 * answer with an SSE stream — the old code unconditionally called
 * `r.json()`, which throws SyntaxError on an SSE body and failed
 * EVERY such call. Now: `text/event-stream` → parse `data:` lines
 * (above); anything else → plain JSON, with an SSE fallback for a
 * server that streams without labelling the content type.
 */
async function callMcpRpc(serverUrl, apiKey, method, params) {
    const url = `${serverUrl}${MCP_PATH}`;
    const body = {
        jsonrpc: '2.0',
        id: Date.now(),
        method,
        params,
    };
    const r = await fetch(url, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Accept': 'application/json, text/event-stream',
            'Authorization': `Bearer ${apiKey}`,
        },
        body: JSON.stringify(body),
    });
    if (!r.ok)
        throw new Error(`HTTP ${r.status}`);
    const contentType = r.headers.get('Content-Type') || '';
    const text = await r.text();
    if (contentType.includes('text/event-stream')) {
        return parseMcpSsePayload(text);
    }
    try {
        const json = JSON.parse(text);
        if (json.error)
            throw new Error(json.error.message || 'RPC error');
        return json.result;
    }
    catch (e) {
        if (e instanceof Error && e.message === 'RPC error')
            throw e;
        // Not JSON — a server streaming SSE without the content-type
        // label. If this also fails, its error is the one reported.
        return parseMcpSsePayload(text);
    }
}
/**
 * Call an MCP tool via JSON-RPC 2.0 (`tools/call`).
 * Throws on transport / RPC errors so the caller can render a toast.
 */
async function callMcpTool(serverUrl, apiKey, toolName, args) {
    return callMcpRpc(serverUrl, apiKey, 'tools/call', { name: toolName, arguments: args });
}
function activate(context) {
    const serverUrl = () => vscode.workspace.getConfiguration('aurem')
        .get('serverUrl', 'https://auremcto.com');
    const bar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    bar.command = 'aurem.openChat';
    bar.show();
    context.subscriptions.push(bar);
    const refreshBar = async () => {
        const key = await getApiKey(context);
        bar.text = key ? '$(rocket) ORA' : '$(plug) AUREM';
        bar.tooltip = key ? 'ORA connected via MCP' : 'Click to connect ORA';
    };
    refreshBar();
    context.subscriptions.push(vscode.commands.registerCommand('aurem.openChat', () => {
        vscode.commands.executeCommand('aurem.chatView.focus');
    }), 
    /*
     * aurem.login — Prompt for an ORA API key.  The key is generated at
     * https://auremcto.com/integrations (or user's own preview URL).  A
     * "Get your key" button opens the browser to that page.
     */
    vscode.commands.registerCommand('aurem.login', async () => {
        const action = await vscode.window.showInformationMessage('Paste your ORA API key (starts with sk-aurem-…). Get one at ' +
            `${serverUrl()}/integrations`, 'Paste API Key', 'Open Integrations Page');
        if (action === 'Open Integrations Page') {
            vscode.env.openExternal(vscode.Uri.parse(`${serverUrl()}/integrations`));
            return;
        }
        if (action !== 'Paste API Key')
            return;
        const key = await vscode.window.showInputBox({
            prompt: 'Paste your ORA API key',
            placeHolder: 'sk-aurem-…',
            ignoreFocusOut: true,
            password: true,
            validateInput: v => (!v || !v.startsWith('sk-aurem-'))
                ? 'Key must start with "sk-aurem-"'
                : undefined,
        });
        if (!key)
            return;
        // Verify the key with a REAL MCP handshake: `initialize` proves
        // the key is accepted by the MCP server (transport + auth).
        // Part VII fix 3(b): the old code called tools/call with an EMPTY
        // tool name (guaranteed to fail) and then fell back to a bare GET
        // that saved the key on ANY HTTP 200 without ever proving the
        // key — both are removed. The key is saved ONLY when initialize
        // succeeds; any failure aborts before setApiKey.
        try {
            await callMcpRpc(serverUrl(), key, 'initialize', {
                protocolVersion: '2024-11-05',
                capabilities: {},
                clientInfo: { name: 'aurem-cto-vscode', version: '0.3.0' },
            });
        }
        catch (e) {
            vscode.window.showErrorMessage(`AUREM: Key rejected — ${e instanceof Error ? e.message : String(e)}`);
            return;
        }
        await setApiKey(context, key);
        await refreshBar();
        vscode.window.showInformationMessage('AUREM: Connected via MCP.');
    }), vscode.commands.registerCommand('aurem.logout', async () => {
        await setApiKey(context, undefined);
        await refreshBar();
        vscode.window.showInformationMessage('AUREM: Disconnected.');
    }), 
    /*
     * aurem.shipSelection — Route through the `ship_code` MCP tool.
     * Requires the user to pick which project to ship to (calls
     * `list_projects` first).
     */
    vscode.commands.registerCommand('aurem.shipSelection', async () => {
        const apiKey = await getApiKey(context);
        if (!apiKey) {
            const a = await vscode.window.showWarningMessage('AUREM: Not connected.', 'Connect Now');
            if (a === 'Connect Now')
                vscode.commands.executeCommand('aurem.login');
            return;
        }
        const editor = vscode.window.activeTextEditor;
        if (!editor)
            return;
        const code = editor.document.getText(editor.selection);
        const file = editor.document.fileName;
        const desc = await vscode.window.showInputBox({
            prompt: 'What should ORA do with this code?',
            placeHolder: 'e.g. add error handling, refactor, add tests',
            ignoreFocusOut: true,
        });
        if (!desc)
            return;
        await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: 'AUREM: Sending to ORA…' }, async () => {
            try {
                // 1) Pick a project via MCP list_projects
                const list = await callMcpTool(serverUrl(), apiKey, 'list_projects', {});
                let projects = [];
                try {
                    const raw = list?.content?.[0]?.text || '{}';
                    const parsed = JSON.parse(raw);
                    projects = parsed.projects || [];
                }
                catch { /* ignore */ }
                if (projects.length === 0) {
                    vscode.window.showErrorMessage('AUREM: No projects found. Add a repo at ' +
                        `${serverUrl()}/projects`);
                    return;
                }
                const pick = await vscode.window.showQuickPick(projects.map(p => ({
                    label: p.name || p.github_repo || p.project_id,
                    description: p.project_id,
                    p,
                })), { placeHolder: 'Which project should ORA ship to?' });
                if (!pick)
                    return;
                // 2) Ship the code via MCP ship_code
                // Part VII fix 3(c): path.win32.basename() — NOT plain
                // basename(). The host path module only splits its own
                // separator, so plain basename() on a POSIX host returns a
                // Windows `C:\Users\…\file.ts` path whole (the leak:
                // file.split('/').pop() had the same flaw). win32.basename
                // splits on BOTH `\` and `/`, so the filename is reduced
                // on every host. Selections over the cap are warned about,
                // not silently cut.
                const MAX_SHIP_CHARS = 3000;
                const truncated = code.length > MAX_SHIP_CHARS;
                if (truncated) {
                    vscode.window.showWarningMessage(`AUREM: Selection is ${code.length} chars — only the first ${MAX_SHIP_CHARS} will be sent to ORA.`);
                }
                const message = `${desc}\n\nContext from VS Code (${path.win32.basename(file)}):\n\`\`\`\n${code.slice(0, MAX_SHIP_CHARS)}${truncated ? `\n… [truncated at ${MAX_SHIP_CHARS} chars]` : ''}\n\`\`\``;
                const result = await callMcpTool(serverUrl(), apiKey, 'ship_code', {
                    project_id: pick.p.project_id,
                    message,
                });
                const summary = JSON.stringify(result).slice(0, 200);
                const action = await vscode.window.showInformationMessage(`ORA queued the change. ${summary}`, 'Open AUREM');
                if (action === 'Open AUREM') {
                    vscode.env.openExternal(vscode.Uri.parse(`${serverUrl()}/dashboard`));
                }
            }
            catch (e) {
                const msg = e instanceof Error ? e.message : String(e);
                vscode.window.showErrorMessage(`AUREM: ${msg}`);
            }
        });
    }));
    const provider = {
        async resolveWebviewView(view) {
            view.webview.options = { enableScripts: true };
            const apiKey = await getApiKey(context);
            view.webview.html = getSidebarHtml(serverUrl(), apiKey);
            view.webview.onDidReceiveMessage(async (msg) => {
                if (msg.type === 'login')
                    vscode.commands.executeCommand('aurem.login');
                if (msg.type === 'logout')
                    vscode.commands.executeCommand('aurem.logout');
            });
        },
    };
    context.subscriptions.push(vscode.window.registerWebviewViewProvider('aurem.chatView', provider));
}
exports.activate = activate;
function getSidebarHtml(serverUrl, apiKey) {
    if (apiKey) {
        // Iframe the dashboard so the user sees the same chat as web.
        return `<!DOCTYPE html><html><head><meta charset="UTF-8">
<style>body{margin:0;padding:0;height:100vh;display:flex}iframe{flex:1;border:none;width:100%}</style>
</head><body><iframe src="${serverUrl}/dashboard?vscode=1" allow="clipboard-write"></iframe></body></html>`;
    }
    return `<!DOCTYPE html><html><head><meta charset="UTF-8">
<style>
body{margin:0;padding:0;font-family:system-ui;background:var(--vscode-sideBar-background);color:var(--vscode-foreground);height:100vh;display:flex;flex-direction:column}
.connect{display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;gap:12px;padding:20px;text-align:center}
button{padding:8px 16px;border-radius:5px;border:none;cursor:pointer;font-size:12px;background:var(--vscode-button-background);color:var(--vscode-button-foreground)}
.note{font-size:10px;opacity:.6;margin-top:14px;line-height:1.4;max-width:220px}
</style></head><body>
<div class="connect">
  <div style="font-size:24px">◈</div>
  <div style="font-size:13px;font-weight:500">AUREM ORA</div>
  <p style="font-size:11px;opacity:.7">Paste your ORA API key to ship code from VS Code via MCP.</p>
  <button onclick="(function(){const v=acquireVsCodeApi();v.postMessage({type:'login'})})()">Paste API Key</button>
  <div class="note">
    Get your key at <br/>
    <code style="font-size:10px">${serverUrl}/integrations</code>
  </div>
</div>
</body></html>`;
}
function deactivate() { }
exports.deactivate = deactivate;
//# sourceMappingURL=extension.js.map