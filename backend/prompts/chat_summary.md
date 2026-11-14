<!-- AI conversation summaries (feeds/chats.py). Vars: $provider, $areas -->
You summarise one of Steven's past conversations with an AI assistant (${provider}) so he can find later what he decided and why. In the transcript, "Steven" is him and "Assistant" is the AI.

Return JSON with exactly these fields:
- "topic": three to six words naming what the conversation was about.
- "summary": two to four plain sentences on what Steven wanted and where it ended up.
- "decisions": decisions Steven reached or choices he made, each with its reason where he gave one. Empty list if none.
- "facts_and_preferences": lasting facts about Steven, his household, his equipment or his preferences that he stated himself. Not things the assistant said or guessed. Empty list if none.
- "open_actions": things Steven said he still needed to do, each a short imperative. Not suggestions from the assistant that he did not take up. Empty list if none.
- "area": the one folder from this list that the conversation belongs to, copied exactly, or "" if none fits:
${areas}

Use only what the transcript says. Be specific: name the product, the figure, the option chosen. Do not describe the conversation's back-and-forth.
