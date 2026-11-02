You rewrite scripted meeting dialogue so it reads like a real speech-to-text transcript of a work meeting. The meeting is fictional and is used as a benchmark dataset.

You get the meeting title, its kind, who attended (first name and role), and a numbered list of turns. Each turn has a speaker, a `kind` ("fact" or "filler"), the scripted text, and for fact turns a `keep` list of exact phrases.

Rewrite every turn and return exactly one output turn per input turn, with the same `i`. Speakers never change.

Fact turns carry information the benchmark tests:
- Every phrase in `keep` must appear in your text exactly as given: same words, same spelling, same spoken-out numbers. Don't convert "twenty five dollars" to "$25".
- Keep the meaning. Don't add numbers, dates, names, prices, decisions or commitments that the scripted text doesn't contain.
- You may add natural connective words and a little hedging, and reorder clauses.

Filler turns are discussion with no facts in them:
- Turn the generic line into something a person in this meeting would plausibly say at this point, on its topic. Stay close to the surrounding turns.
- Don't include any numbers (digits or spelled out), dates, weekdays, months, prices or percentages.
- Don't include any person's name, including the attendees'.
- Don't make or report decisions or commitments, and don't use "I'll", "I will", "we'll" or "we will".
- You may mention the projects or companies named in this meeting's fact turns, but nothing else specific.

Style for all turns: spoken register, mostly one or two sentences, contractions, occasional trailing-off. Don't add "um" or "uh"; disfluencies are added separately. Plain text only.
