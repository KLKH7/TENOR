# UI references (Mobbin) for the Phase 6 web UI

The Phase 6 UI replicates **layout and interaction patterns** from the real products below, collected on Mobbin on 2026-10-29. Granola's own screens are the primary reference, so Concentrate feels like it belongs next to Granola. Per the project brief, we **do not** copy Granola's logo, name, wordmark or exact brand palette: Concentrate gets its own accent colour and typefaces.

We link to Mobbin rather than committing screenshots. They are third-party product images, and this repo is open source.

## Primary reference: Granola (iOS)

Granola's desktop app isn't indexed on Mobbin; its iOS app is.

| Screen | What we take from it |
|---|---|
| [Chat answer with numbered citations](https://mobbin.com/screens/ecb067d0-6928-473e-8728-84a5f033bd8c) | Inline citation chips (`[1]`) in the answer; clicking one opens a **sheet with the source meeting**: title, date chip, attendee chip, then the relevant notes. This is the model for every citation in Concentrate. |
| [Chat with this meeting (thinking state)](https://mobbin.com/screens/ffd0ccf1-3562-4213-8afb-835322ba2e21) | Right-aligned user bubble, "Thought for 1s ›" disclosure, bottom-docked composer ("Chat with this meeting"). |
| [Chat empty state with suggestions](https://mobbin.com/screens/e8a43616-3671-44bf-9a46-c805e98fc9aa) | Suggested-prompt pills above the composer. |
| [Note view](https://mobbin.com/screens/d4d02001-91ed-4f70-b998-4c7b17f9e811) | Large **serif** title; a meta row of rounded **pill chips** (date/time, attendee, folder); body as plain bullets; dark pill primary button. Granola also says plainly when a transcript is too thin to trust. |
| [My notes + new note sheet](https://mobbin.com/screens/e96bd18e-eec7-49cf-87c3-ff38ab46a47e) | Warm off-white/greige surfaces, cards with soft radius, "Coming up" section. |

## Per-view references

### 1. Note view with enhanced transcript (entity hover card)
- [Fireflies: notes left, transcript right](https://mobbin.com/screens/615fc9cf-720a-4615-80cb-3217c6a1244d): two-pane split with summary and notes on the left, transcript in a right pane with tabs (Transcript | Ask).
- [Amie: notes with Private notes / Summary / Transcript tabs](https://mobbin.com/screens/c660e4c0-f122-428b-949d-8980d57b4527): calm single column; the meta rows (Created, Meeting, Share) are the closest to Granola's restraint.
- [Lightfield: transcript with right "Meeting details" rail](https://mobbin.com/screens/e19e1501-342e-4929-b079-2af68df911ff): participants in a right rail, which is where our resolved-entity list goes.
- [Otter: transcript with keywords/speakers header](https://mobbin.com/screens/2e1c11fa-d916-4f3f-8e2e-55ed28bd7287): a "Speakers" summary line above the transcript, which we reuse for Me/Them plus resolved people.

**Concentrate adaptation.** We use Granola's chip meta row with Amie's tab set (Notes · Summary · Enhanced transcript · Raw transcript). Resolved mentions render as an underlined token (`Sam` → Sam Patel). Hovering shows a small card like Granola's citation sheet: name, role, email, link confidence, and "linked because…". Unresolved mentions (`Sam[?]`) get a dashed underline. The Me/Them channel is a small label, not a coloured avatar, because we don't know names.

### 2. Memory timeline per entity (superseded facts struck through)
- [Sana: version history with struck-through old text and highlighted new text](https://mobbin.com/screens/e4f25c59-b045-49ac-9505-86d6f2e8ed3d): the closest match to "superseded facts struck through".
- [Linear: version history modal](https://mobbin.com/screens/6c14e085-9ff5-4e61-9a21-b04f1b0a3433): inline strike plus highlight, with a right-hand list of versions by date.
- [Basecamp: history of changes](https://mobbin.com/screens/07446502-ce66-4714-a13d-fd4d6b9707a9): a vertical dot-and-line timeline with a timestamp above each event.

**Concentrate adaptation.** The page is laid out like Basecamp's vertical timeline, one column per entity. Each fact row looks like Sana and Linear: the superseded value is struck through, the current value is highlighted, and there's a "why it changed" line. Each row has a citation chip that opens the Granola-style source sheet.

### 3. Ask: B0 vs Concentrate side by side
- [Amie: chat answer citing a meeting](https://mobbin.com/screens/62639542-c837-409d-bf0d-f676187a1a42): an answer card with "Thought for 11s", a meeting link inline, and follow-up pills.
- [Sana: answer with structured sources](https://mobbin.com/screens/67047d63-2ae6-44e0-a2a2-8d6989a14819): a source list inside the answer.
- [Voicenotes: Ask panel with context chip](https://mobbin.com/screens/67d70fbc-5b27-4bf4-ad71-57642585c132): a context chip ("based on this note") above the thread.

**Concentrate adaptation.** One composer, two answer columns (Top-k baseline | Concentrate). Each column shows the answer with Granola-style `[n]` citations, then a quiet footer: input tokens, cost and latency. Citations that the grounding check marks as unsupported get a warning tint.

### 4. Eval dashboard
- [Adaline: eval run results](https://mobbin.com/screens/b44c76f4-b4d1-4231-8a3c-eeb5382c1ef3): a big headline score, passed/failed counts, and a row table with status, reason and response.
- [Braintrust: monitor cards](https://mobbin.com/screens/e60ccfc6-357a-4528-8d99-e9b9662b93db): a card grid for cost, tokens and latency p50/p95.
- [Gumloop: score dashboard](https://mobbin.com/screens/60ec82f0-afbf-4a61-b57a-0c56f540f9b6): stat tiles, then charts, then a records table; the simplest layout.

**Concentrate adaptation.** The layout follows Gumloop: stat tiles (accuracy with CI, tokens/query, cost/query, p95 latency) per system, then the compression-vs-accuracy curve and a per-category table, then Adaline's per-question failure table filtered to errors.

## Visual spec (as built in `web/`)

**Decision (2026-10-29):** the user asked for the UI to be "way closer" to Granola. The note view now matches Granola's visual language from the screens above: [transcript sheet](https://mobbin.com/screens/2ab4ec98-0de2-40ee-87b5-a30d7cbcd58b), [note sheet](https://mobbin.com/screens/6f30ae7d-6f1b-4d50-a0b4-58c706b77be1), [chat composer](https://mobbin.com/screens/77c50532-d6fa-4077-958a-d4a33e5e5edf). We still don't use Granola's logo, name or wordmark, and we don't ship their fonts.

| Element | As built |
|---|---|
| Canvas / sheet / card | greige `#E6E5E0`, note sheet `#F1F0EB` with 32px radius, cards `#FAFAF6` |
| Ink | `#1F1F1D`; secondary `#73726D` |
| Accent | green `#4F7F1A` for links and "Me" avatars; orange `#E8833A` for "Them" avatars (Granola's speaker-circle colours) |
| Title | low-contrast serif (Newsreader 400; Granola's typeface is not used) at ~44px |
| Meta row | white pill chips with stroke icons: calendar, person, folder |
| Default view | **Enhanced notes** (the AI summary), like Granola, with a small *Enhanced notes / My notes* toggle. The transcript is secondary. |
| Enhanced notes | bold sans section headings (17px semibold), ink bullets at 15.5px / 1.65 line height, **bold** for key words; topical sections, then "Progress updates" and "Next steps" |
| Transcript | opened with a *Transcript* pill (wave icon) as a right-hand **white** panel, matching [Granola's transcript sheet](https://mobbin.com/screens/2ab4ec98-0de2-40ee-87b5-a30d7cbcd58b): "Transcript" title with a plain text action (*Raw / Linked*) and ✕; one row per speaker turn with a 24px initial circle (orange = first voice/Me, magenta = second/Them, then blue and green for further diarized speakers), bold name, grey text, hairline dividers; no visible timestamps (the start offset is in the row tooltip); the linker explanation sits in a faint footer |
| Entity card | rounded sheet: serif name, pill tags (kind, link confidence), detail line, "why linked" |
| Composer | docked cream bar "Chat with this meeting" with suggestion pills (disabled until Phase 6) |
