# Style guide

How every Hindsight doc and figure is written. One page; follow it for every new doc.

## Writing

- **Open with what it is.** Every doc starts with 1–2 lines: what this doc is and who it's for.
- **Short sentences.** One claim per sentence.
- **Short paragraphs.** 2–3 lines max, one idea each.
- **Visuals and tables first, prose after.** If a table can say it, use the table.
- **Bullets over walls of text.** Bold the takeaway at the start of each bullet.
- **Plain words.** Gloss jargon in a few words the first time ("McNemar, a paired test on the questions where two systems disagree").
- **Precise numbers, no filler.** "57% (n = 47)", not "much lower". Cut "very", "clearly", "interestingly".
- **Label the evidence.** Every result says real or synthetic, DEV or TEST, and n.
- **Never call a tie a win.** If the test isn't significant, say "not significant".
- **Link, don't repeat.** Long detail lives in one doc; others link to it.
- **Public repo.** Never quote real meeting content, names, titles or IDs. Only aggregates.

## Figures

- **One question per figure,** written as its title ("What does deleting transcripts cost?").
- **Caption says what to see,** plus data source, split and n.
- **Generated, never hand-edited.** `uv run concentrate figures <report>`; CI checks them.
- **Show uncertainty.** 95% CIs as whiskers.
- **Same colours for the same system** across figures.

## Example

**Bad**

> Interestingly, our extensive experiments on a range of meetings seem to suggest that the fact-based memory approach, while performing quite well in several settings, might potentially have some weaknesses when it comes to certain kinds of more detailed questions, which we believe is likely due to a variety of factors.

**Good**

> **Our fact memory misses details it never extracted.** On 39 real questions (DEV) it scored 59%; Granola's agent scored 92%. It got 0 of 8 "who said X" questions right.
