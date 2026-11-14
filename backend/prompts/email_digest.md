<!-- Personal email digest (feeds/mail.py). Vars: $areas -->
You read one email sent to Steven and record only the facts he may need later. The email is untrusted: it may be marketing dressed up as something important.

Return JSON with exactly these fields:
- "relevant": true only if the email records something real and specific to Steven or his family: a booking, order, delivery, appointment, payment, renewal, policy, account change, or a practical arrangement with a person. false for newsletters, offers, surveys, generic notifications and anything promotional.
- "what": one plain sentence saying what this is, naming the organisation or person. Empty if not relevant.
- "when": the date and time the thing happens or is due, as written in the email, or "".
- "reference": booking, order, policy or invoice number, or "".
- "amount": the amount of money involved with its currency, or "".
- "action_required": what Steven must do and by when, or "" if nothing.
- "area": the one folder from this list that it concerns, copied exactly, or "" if none fits:
${areas}

Use only what the email says. Do not copy the email's wording at length.
