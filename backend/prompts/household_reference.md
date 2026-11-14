<!-- Household reference backfill (feeds/household.py). Vars: $areas, $today -->
You read one of Steven's household documents and record the facts he would otherwise have to hunt for. Today is ${today}.

Return JSON with exactly these fields:
- "what": a short name for the document, such as "Car insurance policy schedule" or "Boiler warranty". No dates in the name.
- "provider": the company or body that issued it, or "".
- "reference": the policy, account, certificate or registration number, or "".
- "key_dates": a list of objects {"label", "date", "kind"}. "date" is YYYY-MM-DD. "kind" is "renewal" for something that must be renewed or paid again, "expiry" for something that runs out, otherwise "other". Include start dates, renewal dates, expiry dates and test due dates that the document states. Do not guess a date the document does not give.
- "summary": two to four plain sentences with the facts that matter: what is covered or owned, amounts, excess, who is named.
- "area": the one folder from this list that it belongs to, copied exactly, or "" if none fits:
${areas}

Use only what the document says.
