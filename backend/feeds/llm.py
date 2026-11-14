"""The only way a feed calls a model.

Every call: masks identifiers, checks the monthly cap against the worst case, calls the
configured provider with no tools, records the real token counts in the ledger, then
restores the identifiers locally. Ingested text is untrusted data, so nothing here can
give the model a tool or an action.
"""

from pydantic import BaseModel

from backend import config
from backend.feeds import ledger, masking
from backend.feeds.dryrun import RunReport
from backend.prompts import load_prompt

# Wraps ingested text in the prompt. prompts/feed_untrusted_rule.md tells the model that
# anything between these markers is data to summarise, never instructions to follow.
UNTRUSTED_OPEN, UNTRUSTED_CLOSE = "<untrusted_content>", "</untrusted_content>"


def wrap_untrusted(text: str) -> str:
    return f"{UNTRUSTED_OPEN}\n{text}\n{UNTRUSTED_CLOSE}"


def worst_case_pence(prompt: str, system_instruction: str, max_output_tokens: int) -> float:
    tokens_in = ledger.estimate_tokens(prompt) + ledger.estimate_tokens(system_instruction)
    return ledger.cost_pence(config.LLM_MODEL, tokens_in, max_output_tokens)


async def generate(feed: str, prompt: str, system_instruction: str = "", *,
                   response_schema: type[BaseModel] | None = None, max_output_tokens: int = 1024,
                   report: RunReport | None = None, dry_run: bool = False) -> str | None:
    """Run one text generation for a feed and return the response text.

    In dry-run mode no call is made and None is returned; the worst-case cost is added to
    ``report``. Raises ``ledger.BudgetExceeded`` when the call might breach the monthly cap.
    """
    from google.genai import types

    from backend.utils.gemini import call_gemini_with_retry

    system_instruction = f"{system_instruction or ''}\n\n{load_prompt('feed_untrusted_rule')}".strip()
    masked_prompt, mapping = masking.mask(prompt)
    estimate = worst_case_pence(masked_prompt, system_instruction, max_output_tokens)

    if dry_run:
        if report:
            report.model_calls += 1
            report.cost_pence += estimate
        return None

    ledger.check_budget(feed, estimate)

    kwargs = {"system_instruction": system_instruction, "temperature": 0.1,
              "max_output_tokens": max_output_tokens}
    if response_schema is not None:
        kwargs["response_mime_type"] = "application/json"
        kwargs["response_schema"] = response_schema
    response = await call_gemini_with_retry(
        model=config.LLM_MODEL, contents=masked_prompt, config=types.GenerateContentConfig(**kwargs))

    text = response.text or ""
    usage = getattr(response, "usage_metadata", None)
    if usage is not None:
        tokens_in = getattr(usage, "prompt_token_count", None) or 0
        tokens_out = ((getattr(usage, "candidates_token_count", None) or 0)
                      + (getattr(usage, "thoughts_token_count", None) or 0))
    else:  # non-Gemini providers return no usage; charge the estimate
        tokens_in = ledger.estimate_tokens(masked_prompt) + ledger.estimate_tokens(system_instruction)
        tokens_out = ledger.estimate_tokens(text)
    pence = ledger.record(feed, config.LLM_MODEL, "generate", tokens_in, tokens_out)
    if report:
        report.model_calls += 1
        report.cost_pence += pence

    return masking.unmask(text, mapping)
