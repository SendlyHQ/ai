# Sendly for AI agents and the developers who point them at things

Sendly is a developer-first API for SMS, MMS, WhatsApp, RCS and phone verification.
This repository is the single front door to building on it.

If you are an AI agent, read [AGENTS.md](./AGENTS.md) first and stop reading this file.
If you are a human deciding how to integrate, start with the table below.

## Which path do I need?

| You want to | Use | Setup cost | Start here |
| --- | --- | --- | --- |
| Give a coding agent or chat client the ability to send and manage messages, right now | **Hosted MCP server** | One config block, no install | [Hosted MCP server](#hosted-mcp-server) |
| Teach an agent *how* to use Sendly well, without giving it tools | **Agent Skills** | One command | [Agent Skills](#agent-skills) |
| Call Sendly from application code | **SDK** | One package install | [SDKs](#sdks) |
| Script it, pipe it, or run it in CI | **CLI** | One install, one login | [CLI](#cli) |
| Anything else, or a language with no SDK | **REST** | None | [Raw REST](#raw-rest) |

All five are clients of the same REST API. A compliance block, an idempotency key
or a rate limit behaves identically whichever you pick, and none of them can do
something the API underneath cannot.

---

## Hosted MCP server

The fastest path. No Node.js, no install, no local process. The server runs at
`https://mcp.sendly.live`, speaks Streamable HTTP, and authenticates with your API
key as a bearer token.

```json
{
  "mcpServers": {
    "sendly": {
      "type": "http",
      "url": "https://mcp.sendly.live",
      "headers": {
        "Authorization": "Bearer sk_test_v1_..."
      }
    }
  }
}
```

`https://mcp.sendly.live/mcp` is accepted as well and behaves identically.
`GET https://mcp.sendly.live/health` needs no auth and is safe to probe, but it is a
liveness check and nothing more. The `version` and `tools` fields in its body are
constants compiled into the worker, not read from the running server, so do not use
either to decide anything. `tools/list` is the only honest answer to what a server
has.

The server rejects anything that is not a `sk_test_v1_` or `sk_live_v1_` key before
it opens a session, so a malformed key fails fast with `401 invalid_key` rather than
failing later on the first tool call.

### Local stdio instead

The same server, running on your machine, reading the key from the environment:

```json
{
  "mcpServers": {
    "sendly": {
      "command": "npx",
      "args": ["-y", "@sendly/mcp"],
      "env": {
        "SENDLY_API_KEY": "sk_test_v1_..."
      }
    }
  }
}
```

`SENDLY_API_KEY` is required and the process exits immediately without it.
`SENDLY_BASE_URL` overrides the API host and must be HTTPS unless it points at
`localhost` or `127.0.0.1`.

Prefer the hosted server unless you specifically need the API traffic to originate
from your own machine.

Both servers register the same tool module, but they do not ship on the same clock.
The hosted server bundles that module out of this source tree when it is built and
deployed; `@sendly/mcp` carries the copy that existed at its last npm publish. The
two can therefore expose different tools at the same moment. Call `tools/list`
against whichever server you connected to; that is the only authoritative list for
the process in front of you.

The full generated tool list, with parameters, is in
[`reference/mcp-tools.md`](./reference/mcp-tools.md). It is generated from the same
module the hosted server bundles, so it tracks the hosted server rather than the
published npm package.

### Claude Code plugin

Installs the hosted MCP server as a plugin, so you never paste a config block:

```
/plugin marketplace add SendlyHQ/ai
/plugin install sendly@sendly
```

The plugin reads `SENDLY_API_KEY` from your environment. The published Agent Skills
are a second plugin in the same marketplace:

```
/plugin install sendly-skills@sendly
```

---

## Agent Skills

Skills are instructions, not tools. They teach an agent the patterns, the compliance
rules and the failure modes, and pair well with either MCP or an SDK.

```
npx skills add SendlyHQ/sendly-skills
```

Published skills: `sending-sms`, `verifying-phones`, `sms-best-practices`.

---

## SDKs

Every SDK reads the same `SENDLY_API_KEY`, targets the same base URL, and returns the
same objects. Pick your language and send one message.

**Node.js** ([`SendlyHQ/sendly-node`](https://github.com/SendlyHQ/sendly-node))

```bash
npm install @sendly/node
```

```typescript
import Sendly from '@sendly/node';

const sendly = new Sendly(process.env.SENDLY_API_KEY!);

const message = await sendly.messages.send({
  to: '+15005550000',
  text: 'Hello from Sendly',
});
```

**Python** ([`SendlyHQ/sendly-python`](https://github.com/SendlyHQ/sendly-python))

```bash
pip install sendly
```

```python
from sendly import Sendly

client = Sendly(os.environ["SENDLY_API_KEY"])

message = client.messages.send(to="+15005550000", text="Hello from Sendly")
```

Keyword arguments are snake_case. The sender parameter is `from_`, because `from` is
a reserved word.

**Go** ([`SendlyHQ/sendly-go`](https://github.com/SendlyHQ/sendly-go))

```bash
go get github.com/SendlyHQ/sendly-go/v3
```

```go
client := sendly.NewClient(os.Getenv("SENDLY_API_KEY"))

message, err := client.Messages.Send(ctx, &sendly.SendMessageRequest{
    To:   "+15005550000",
    Text: "Hello from Sendly",
})
```

**Ruby** ([`SendlyHQ/sendly-ruby`](https://github.com/SendlyHQ/sendly-ruby))

```bash
gem install sendly
```

```ruby
client = Sendly::Client.new(ENV["SENDLY_API_KEY"])

message = client.messages.send(to: "+15005550000", text: "Hello from Sendly")
```

**PHP** ([`SendlyHQ/sendly-php`](https://github.com/SendlyHQ/sendly-php))

```bash
composer require sendly/sendly-php
```

```php
$client = new Sendly\Sendly(getenv('SENDLY_API_KEY'));

$message = $client->messages()->send('+15005550000', 'Hello from Sendly');
```

**Java** ([`SendlyHQ/sendly-java`](https://github.com/SendlyHQ/sendly-java))

Coordinates `live.sendly:sendly-java`.

```java
Sendly client = new Sendly(System.getenv("SENDLY_API_KEY"));

Message message = client.messages().send("+15005550000", "Hello from Sendly");
```

**Rust** ([`SendlyHQ/sendly-rust`](https://github.com/SendlyHQ/sendly-rust))

```bash
cargo add sendly
```

```rust
let client = Sendly::new(std::env::var("SENDLY_API_KEY")?);

let message = client
    .messages()
    .send(SendMessageRequest::new("+15005550000", "Hello from Sendly"))
    .await?;
```

**.NET** ([`SendlyHQ/sendly-dotnet`](https://github.com/SendlyHQ/sendly-dotnet))

Package id `Sendly`.

```csharp
using var client = new SendlyClient(Environment.GetEnvironmentVariable("SENDLY_API_KEY"));

var message = await client.Messages.SendAsync("+15005550000", "Hello from Sendly");
```

Exact package names matter and the near-misses are all taken by other projects. The
npm package is `@sendly/node`, not `sendly-node` and not `sendly`. The PyPI package
is `sendly`, not `sendly-sdk`.

---

## CLI

([`SendlyHQ/sendly-cli`](https://github.com/SendlyHQ/sendly-cli))

```bash
npm install -g @sendly/cli
# or, on macOS and Linux
brew install SendlyHQ/tap/sendly
```

```bash
sendly login
sendly send --to "+15005550000" --text "Hello from Sendly"
```

`sendly login` runs a browser device-approval flow and stores the session for you.
If you already have a key, exporting `SENDLY_API_KEY` is enough and no login is
needed. Add `--json` to any command for machine-readable output, and run
`sendly doctor` when something is misconfigured.

---

## Raw REST

Base URL `https://sendly.live/api/v1`. Bearer auth. JSON in, JSON out.

```bash
curl -X POST https://sendly.live/api/v1/messages \
  -H "Authorization: Bearer $SENDLY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"to": "+15005550000", "text": "Hello from Sendly"}'
```

Machine-readable specs, all served from the API host:

- OpenAPI 3.0: <https://sendly.live/openapi.yaml>
- OpenAI function-calling tool definitions: <https://sendly.live/openai-tools.json>
- MCP discovery: <https://sendly.live/.well-known/mcp.json>
- Everything, indexed for LLMs: <https://sendly.live/llms.txt>

---

## What is generated, and what that buys you

Every file in [`reference/`](./reference) is generated from the Sendly source tree,
not written by a person:

- [`reference/mcp-tools.md`](./reference/mcp-tools.md), every MCP tool with its
  parameters, read out of the tool registry
- [`reference/endpoints.md`](./reference/endpoints.md), every REST route the server
  actually registers
- [`reference/compliance.md`](./reference/compliance.md), the per-country messaging
  rules including each country's own quiet-hours window

A checked-in verifier re-derives all of it and fails the build when a claim stops
being true. It runs five checks: the generated files still match what the source
produces; every tool name mentioned anywhere in this repository is a real tool;
every `/api/v1` path mentioned here is a route the server registers, with a
matching method where one is named; every tool binds to a route that exists; and
the carrier, which is white-label, is never named.

```bash
npm run generate:ai-reference   # regenerate reference/ from source
npm run verify:ai-reference     # prove this repository is still true
```

That is the whole point. This front door exists because the previous hand-written
one drifted: the MCP tool count shipped as five different numbers at once, the
discovery endpoint advertised a count that had not been right for months, and the
US quiet-hours window was documented as though it applied worldwide. A front door
that can drift is worse than no front door.

So the prose here, in [AGENTS.md](./AGENTS.md), in [`guides/`](./guides) and in
[`toolkits/`](./toolkits) deliberately never repeats a number that `reference/`
owns. A stale sentence in a hand-written file cannot contradict the generated
truth, because the hand-written files do not state the facts that rot. When you
need a count, a list or an exact limit, go to `reference/`.

---

## Public repositories

| Repository | Contents |
| --- | --- |
| [`SendlyHQ/ai`](https://github.com/SendlyHQ/ai) | This front door |
| [`SendlyHQ/sendly-node`](https://github.com/SendlyHQ/sendly-node) | Node.js and TypeScript SDK |
| [`SendlyHQ/sendly-python`](https://github.com/SendlyHQ/sendly-python) | Python SDK |
| [`SendlyHQ/sendly-go`](https://github.com/SendlyHQ/sendly-go) | Go SDK |
| [`SendlyHQ/sendly-ruby`](https://github.com/SendlyHQ/sendly-ruby) | Ruby SDK |
| [`SendlyHQ/sendly-php`](https://github.com/SendlyHQ/sendly-php) | PHP SDK |
| [`SendlyHQ/sendly-java`](https://github.com/SendlyHQ/sendly-java) | Java SDK |
| [`SendlyHQ/sendly-rust`](https://github.com/SendlyHQ/sendly-rust) | Rust SDK |
| [`SendlyHQ/sendly-dotnet`](https://github.com/SendlyHQ/sendly-dotnet) | .NET SDK |
| [`SendlyHQ/sendly-cli`](https://github.com/SendlyHQ/sendly-cli) | Command line interface |
| [`SendlyHQ/sendly-skills`](https://github.com/SendlyHQ/sendly-skills) | Agent Skills |
| [`SendlyHQ/sendly-docs`](https://github.com/SendlyHQ/sendly-docs) | Developer documentation |
| [`SendlyHQ/sendly-mastra-template`](https://github.com/SendlyHQ/sendly-mastra-template) | Mastra agent template |
| [`SendlyHQ/sendly-discord`](https://github.com/SendlyHQ/sendly-discord) | Two-way SMS in a Discord server |
| [`SendlyHQ/homebrew-tap`](https://github.com/SendlyHQ/homebrew-tap) | Homebrew tap for the CLI |

Per-language OTP examples live in `SendlyHQ/sendly-<language>-otp-example` for
Next.js, Python, Go, Ruby, PHP, Java, .NET and Rust.

---

## Links

- Dashboard: <https://sendly.live/dashboard>
- API keys: <https://sendly.live/api-keys>
- Documentation: <https://sendly.live/docs>
- Support: support@sendly.live
