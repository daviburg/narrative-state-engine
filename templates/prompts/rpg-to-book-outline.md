# Copilot Prompt Template — RPG to Book Outline

**Status: Placeholder — not yet implemented. See issue #9.**

This template will be used with `tools/export_book_skeleton.py` once that tool is implemented.

---

## Intended use
Generate a rough book/fiction outline from accumulated session transcripts and derived state.
This is a secondary future purpose of the framework and should not be attempted before
Phase 4 tooling (see `docs/roadmap.md`) is in place.

---

## Planned prompt (draft — do not use yet)

```
Generate a book outline from sessions/{session_id}.

Using the session transcript, summaries, and catalog data:

1. Premise (1 paragraph):
   - Who is the protagonist?
   - What is the central conflict?
   - What is at stake?

2. Act structure:
   - Act I (setup): what established the world and the inciting incident?
   - Act II (escalation): what complications arose and what choices were made?
   - Act III (resolution/cliffhanger): where did the session end?

3. Major beats (bulleted, in order):
   - Key events with brief description

4. Character arcs:
   - For each major character: how were they introduced, what did the player learn, how did their status change?

5. Unresolved threads:
   - List open plot threads that could continue in a sequel session or chapter

Output to: sessions/{session_id}/exports/book-skeleton.md
```
