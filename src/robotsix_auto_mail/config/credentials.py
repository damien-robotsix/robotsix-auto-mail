"""The canonical Langfuse / OpenRouter credential blocks.

robotsix-standards ``component-standard.md`` fixes one storage shape for
every component's LLM credentials, so the deployment engine can enumerate
the fleet's projects and keys without a per-component special case:

- a top-level ``langfuse`` block holding the instance ``host`` and a
  ``projects`` map keyed by the Langfuse **project name**;
- a parallel ``openrouter`` block whose ``keys`` map uses the **same**
  aliases.  Sharing the alias is the point — reconciliation compares what
  the provider billed for one LLM function against what Langfuse traced for
  that same function, and the shared alias is what makes the two joinable.

The engine reads these blocks and nothing else: there is no fallback to a
component's own historical layout.  Credentials kept anywhere else are
invisible to the fleet — the component's own tracing still works, so the
breakage only shows up as an empty project list in cost-monitor and in the
chat agent's trace proxy.

auto-mail declares one LLM function.  Detection, triage, archiving and draft
generation all run on one provider key and trace to one project, so the
alias is the bare repo name, :data:`MAIN_LLM_ALIAS`.  A second function
would add a second Langfuse project *and* a second OpenRouter key under a
``robotsix-auto-mail-<function>`` alias — never a second use of this one,
because two functions behind a single key produce one usage figure
attributable to neither.

These blocks are component-wide, not per-account: a mailbox is not an LLM
function, and one project per mailbox would split one function's traces
across N projects that reconcile against nothing.
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, ConfigDict, Field, SecretStr

#: Alias for auto-mail's single LLM function, in both canonical blocks.
#: The component standard fixes it as ``<repo>`` for a component's main
#: function.
MAIN_LLM_ALIAS: Final[str] = "robotsix-auto-mail"


class LangfuseProject(BaseModel):
    """Credentials for one Langfuse project."""

    model_config = ConfigDict(frozen=True)

    public_key: str = ""
    secret_key: SecretStr = SecretStr("")
    #: Only needed by consumers that address the project by id rather than
    #: name; the component itself never uses it.
    project_id: str = Field(default="", json_schema_extra={"advanced": True})

    def is_configured(self) -> bool:
        """True when both keys are set — a half-filled project traces nothing."""
        return bool(self.public_key and self.secret_key.get_secret_value())


class LangfuseConfig(BaseModel):
    """The canonical ``langfuse`` block: one instance, N named projects."""

    model_config = ConfigDict(frozen=True)

    host: str = Field(
        default="",
        description="Langfuse instance base URL, e.g. `https://langfuse.example.net`.",
    )
    projects: dict[str, LangfuseProject] = Field(
        default_factory=dict,
        description=(
            "One entry per LLM function, keyed by its Langfuse project name. "
            f"auto-mail has one: `{MAIN_LLM_ALIAS}`."
        ),
    )

    def project(self, alias: str = MAIN_LLM_ALIAS) -> LangfuseProject | None:
        """The project declared under *alias*, or ``None``."""
        return self.projects.get(alias)


class OpenRouterConfig(BaseModel):
    """The canonical ``openrouter`` block: one provider key per alias."""

    model_config = ConfigDict(frozen=True)

    keys: dict[str, SecretStr] = Field(
        default_factory=dict,
        description=(
            "The OpenRouter key funding each LLM function, keyed by the same "
            "alias as `langfuse.projects` — that shared alias is what lets "
            "billed spend be reconciled against traced spend."
        ),
    )

    def key(self, alias: str = MAIN_LLM_ALIAS) -> str:
        """The provider key declared under *alias*, or ``""``."""
        secret = self.keys.get(alias)
        return secret.get_secret_value() if secret else ""
