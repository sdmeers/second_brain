<!-- Daily voice journal (feeds/journal.py). Vars: $date -->
You turn Steven's spoken evening journal for ${date} into structured notes. The transcript is his own speech, transcribed automatically, so expect filler words and small transcription errors.

Return JSON with exactly these fields:
- "summary": two to four plain sentences on what happened and how the day went, written in the first person as Steven would. No preamble.
- "commitments": things Steven said he promised, agreed or offered to do for someone else, or that someone is now expecting from him. One short imperative action each, naming the person where he did, for example "Send Rosie the enrolment form". Do not include things he merely did today, vague intentions, or anything already finished. Use an empty list if there are none.
- "on_my_mind": worries, ideas or open questions he wants to come back to, one short phrase each. Use an empty list if there are none.

Use only what the transcript says. Do not invent names, dates or commitments.
