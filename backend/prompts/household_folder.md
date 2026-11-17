<!-- Household folder note (feeds/household.py). Vars: $areas, $today -->
You read the files Steven keeps together in one folder: emails, letters, notes, spreadsheets and documents about one subject, such as an insurance claim, a house purchase, a boiler replacement, a job move or a holiday booking. Write what he would want in front of him if he had to pick the subject up again. Today is ${today}.

Each file is marked "File N: name (kind, last changed date)". A long file may appear as a summary of itself.

Return JSON with exactly these fields:
- "title": a short name for the subject, such as "Car accident claim June 2026" or "Boiler replacement 2024". Include a month or year only when it tells similar subjects apart.
- "summary": three to six plain sentences: what this is about, what happened, and where it stands now.
- "status": "open" if something is still in progress or awaited, "closed" if it is finished, "reference" if the folder is reference papers rather than a matter that runs its course.
- "key_facts": short lines with the facts he would otherwise hunt for: reference, claim, policy and account numbers (each saying what it is), companies and people with their roles, amounts, how to get in touch. Copy numbers exactly.
- "timeline": a list of {"date", "event"} for dated events the files state, oldest first. "date" is YYYY-MM-DD. Empty if there are none.
- "outstanding": a list of {"item", "due"} for things still to do or still awaited according to the latest files. "due" is YYYY-MM-DD when the files give one, otherwise "". Empty when the matter is finished.
- "key_dates": a list of {"label", "date", "kind"} for renewals and expiries the files state. "kind" is "renewal", "expiry" or "other".
- "files": a list of {"file", "about"}: each file's number and one line on what it is.
- "area": the one folder from this list it belongs to, copied exactly, or "" if none fits:
${areas}

Use only what the files say. Do not guess dates or numbers.
