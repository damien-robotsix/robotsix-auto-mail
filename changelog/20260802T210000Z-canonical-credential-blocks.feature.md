Move the LLM provider key and the Langfuse credentials out of every mailbox
and into the two canonical component-wide blocks robotsix-standards fixes:
top-level `langfuse` (instance `host` plus a `projects` map keyed by Langfuse
project name) and `openrouter` (a `keys` map addressed by the same aliases),
alongside a component-wide `llm_provider_model`. auto-mail declares one LLM
function, `robotsix-auto-mail`.

Per-account `llm_api_key`, `llm_provider_model` and `langfuse_public_key` /
`langfuse_secret_key` / `langfuse_base_url` are **removed** from `MailConfig`.
A mailbox is not an LLM function, so N mailboxes meant N copies of one
credential and, at best, one function's traces split across N projects. The
deployment engine reads the canonical blocks and nothing else, so in that
shape auto-mail reported no projects and no keys to the fleet at all —
cost-monitor could not reconcile its spend and the chat agent's trace proxy
had nothing to proxy, while auto-mail's own tracing kept working and hid it.

Existing config files need no migration step: the old per-account keys are
ignored on load, and the new blocks default to unconfigured. Re-enter the
credentials once, in the component's Settings panel or in `config.json`.
