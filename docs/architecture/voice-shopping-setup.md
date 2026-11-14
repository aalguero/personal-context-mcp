# Voice shopping: Home Assistant setup (task 212 phase 4)

Written 2026-11-13 on shadow. **For Andre to follow by hand.** No Claude session can
reach Home Assistant or the Pi. Server side: `639cf6c`, deployed by
`SHOP_VOICE_BRIEF.md`.

## What this builds

> "Okay computer, add coffee to the grocery list."

1. The Pi's microphone, wake word and speech-to-text (voice stages 1–2a, done) hand the
   text to Home Assistant's Assist pipeline.
2. HA's **local** intents get first try, so lights and switches never leave the house.
3. Anything they don't match goes to an **LLM conversation agent** in HA.
4. That agent reaches futileMCP through HA's **Model Context Protocol** integration,
   authenticated with **`PA_SHOP_TOKEN`**. With that token it sees exactly two tools:
   `pa_shop_add` and `pa_shop_list`.

**Why a separate token.** HA sits behind a microphone anyone in the room can talk to,
and an HA backup or compromise exposes whatever credentials HA holds.
`PA_AUTH_TOKEN` reaches all 34 tools, medical and financial tasks included.
`PA_SHOP_TOKEN` reaches your shopping list and nothing else: not tasks, not context,
not even removing items. That's enforced by the server, not by a prompt. The scoped
token gets its own MCP server instance, proven in
`futile-pa/tests/test_server_scopes.py`.

## Step 0: establish three unknowns before configuring anything

These were not checkable from shadow. Each answer changes the next step.

| # | Question | How to check | If no |
|---|---|---|---|
| 1 | Does your HA version's **Model Context Protocol** integration speak **streamable HTTP**? It first shipped SSE-only | Settings → Devices & services → Add integration → "Model Context Protocol". Read the URL field's help text, and the integration's docs page for your HA version | **Stop.** futileMCP serves streamable HTTP at `/mcp` only. Report back; the options are an HA update or a different bridge |
| 2 | Can it send a **custom `Authorization: Bearer` header**, or only take a URL (or OAuth)? | The same config form | Use the **path form** (step 3b). It needs `PA_SHOP_ALLOW_PATH=1` on the server |
| 3 | Which **LLM conversation integration** will you use, and does its options page let you pick the MCP server as an **LLM API**? | Settings → Devices & services → Add integration → "Anthropic" (or another). After setup, its options | The agent can't reach the tools. Check that integration's docs |

## Step 1: get the shop token, on futileassistant, in your own terminal

`SHOP_VOICE_BRIEF.md` generates `PA_SHOP_TOKEN` into `.env` and never prints it. To
read it yourself:

```bash
grep '^PA_SHOP_TOKEN=' /home/dremon/tools/personal-context-mcp/.env
```

Copy it straight into HA in step 3. Don't paste it into a chat, a note or a task.

## Step 2: an LLM conversation agent in HA

1. Add the conversation integration you chose in step 0.3, e.g. **Anthropic**.
2. **Use an API key made for HA alone**, not the one in futileassistant's `.env`. Then
   revoking HA's access never takes down the check-in agent, and the provider's usage
   page shows HA's cost separately.
3. Prompt and model are your choice. One line worth adding to its instructions:
   *"For shopping, use the shopping tools. Category is the kind of shop: grocery,
   hardware, technology, outdoors. Say back exactly what you added."*

## Step 3: connect HA to futileMCP with the shop token

Server URL base: `https://futileassistant.cormorant-barb.ts.net`. That's the Funnel
address the server already serves; nothing about exposure changes.

**3a. If HA can send a header** (step 0.2 yes): URL `…/mcp`, header
`Authorization: Bearer <PA_SHOP_TOKEN>`.

**3b. If it can only take a URL**: URL `…/t/<PA_SHOP_TOKEN>/mcp`. On futileassistant,
add `PA_SHOP_ALLOW_PATH=1` to `.env` and restart `personal-context-mcp.service`. That
enables the path form **for the shop token only**; the full token's path form stays off.
The server redacts `/t/…` paths from its access log, so the token doesn't reach the
journal. But **HA stores the URL in its config and its backups** either way, which is
exactly why it's the shop token there and not the full one.

Then, in the conversation agent's options, select the MCP server as its LLM API (step 0.3).

## Step 4: the voice pipeline

Settings → Voice assistants → your pipeline (the one the Pi uses):
- **Conversation agent:** the agent from step 2.
- **Prefer handling commands locally: ON.** Home commands stay local and only unmatched
  speech reaches the LLM. Without it, "turn off the kitchen lights" would cost an API
  call and depend on the internet.

## Step 5: prove it, in this order

1. **The scope, from HA's side.** HA's integration page should list exactly **two**
   tools. If it shows more, stop: the wrong token is configured.
2. **Typed first.** In HA's Assist dialog, type *"add zqvoice beans to the grocery list"*.
   - Pass: the reply says it was added.
   - Then, from claude.ai or Signal: *"show me my grocery list"*. `zqvoice beans` is on it.
   - `pa.task` must not grow. A Claude session can check that read-only.
3. **Spoken.** *"Okay computer, add zqvoice beans to the grocery list."* Expect a reply
   that it's already there, because shopping lists deduplicate. Then *"what's on my
   grocery list?"*
4. **Clean up from Signal or claude.ai:** *"take zqvoice beans off, it was a test."* The
   voice path deliberately can't remove items.
5. **Home commands still local:** *"Okay computer, turn off the kitchen lights."* It
   should work exactly as before, with no LLM involved (check the pipeline's debug view).

## Revoking

- **HA's access to your list:** change `PA_SHOP_TOKEN` in `.env` (or delete the line)
  and restart `personal-context-mcp.service`. The full token and the check-in agent are
  untouched.
- **HA's LLM spend:** revoke the HA-only API key at the provider.

## What stays deferred

- The custom wake word (voice stage 2b).
- Whisper vocabulary hints (stage 3).
- Barge-in.

None blocks this. The Pi's reliability does: voice capture is only as good as the Pi
being on. That's why voice came last in the phase plan.
