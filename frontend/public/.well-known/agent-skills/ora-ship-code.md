# ora-ship-code
 
Ship code to GitHub with ORA, the autonomous AI software engineer.
 
## When to use
 
Use this skill when you need to change code in a connected GitHub repository: add a feature, fix a bug, or refactor, and have the change committed to a branch.
 
## How it works
 
1. Authenticate: send a Bearer token - either a JWT from an auremcto.com login, or a long-lived API key starting with sk-aurem- (issue one with POST /api/aurem-dev/mcp/keys).
2. List projects with the list_projects MCP tool to get the project_id.
3. Submit the task with the ship_code MCP tool: a concrete brief, at least 10 characters, naming the files to change.
4. Poll get_task_status with the returned task_id until the task is done. ORA runs security scans before committing.
5. Verify with get_recent_commits.
 
## Endpoint
 
- MCP (Streamable HTTP, JSON-RPC 2.0): https://auremcto.com/api/aurem-dev/mcp
- Discovery: https://auremcto.com/.well-known/mcp.json
- API reference: https://auremcto.com/openapi.json
 
## Rules
 
- Tasks must be concrete: name the files and the change. Vague tasks such as fix it or make it better are rejected and need clarification.
- ORA commits only after the user approves the plan. Never claim a ship is done until get_task_status reports it done with a commit SHA.
