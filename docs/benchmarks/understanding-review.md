# Review: how well the AI understands requests

Written 13 October 2026, at the owner's request before phase 4.

**The problem reported:** AI decisions felt hard-coded. Creating a worker, and chatting,
seemed to need particular words, and when the AI could not tell what was meant it failed
instead of asking.

**What the review found:** the cause was mostly not the AI, but rules around it:
- fixed phrase lists;
- character-exact checks on the AI's quotes;
- a form that made the AI guess an operation it already knew;
- no way to answer a question and carry on.

Every fix keeps the same safety: authority still comes only from the person's own words. Compute
is the same or lower.

## What was wrong, and what changed

1. **The Create worker form made the AI guess.**
   - **Before:** the form sent the request through general chat, where a separate AI call guessed
     the operation. "Research competitors every week" could be taken as a one-off research task.
   - **A fixed prefix:** the form also added "Create a worker that will:" unless the text began
     with exactly "create", "hire" or "make".
   - **Now:** the form says what it is (`operation: worker.create`) and sends the request as
     written.
   - **Saved:** one AI call per worker.
2. **Approval steps needed one of 14 set phrases.**
   - **Before:** approval steps the AI proposed were kept only if the request contained a phrase
     such as "ask me" or "require approval". "Check with me before posting" or "let me review it
     first" were silently dropped.
   - **Now:** the AI quotes the person's words that ask for the approval, and the step is kept
     when those words really are in the request. The old phrases still work.
3. **The AI's quotes had to match character for character.**
   - **Where:** proof of open-web research, named websites, standing responsibilities and a
     required site.
   - **What went wrong:** a difference in letter case, spacing, quote marks or punctuation either
     stopped the request ("Please clarify the site") or cost another AI call to correct.
   - **Now:** one shared check ([grounding.py](../../backend/app/application/services/grounding.py))
     compares words in order and ignores formatting. A quote that is not the person's words is
     still refused.
4. **There was no "ask, then continue".**
   - **What existed:** only integration tasks could take an answer and carry on. For everything
     else, an answer was a brand-new request. Worker creation even lost the original request,
     since it used only the latest message.
   - **Asking:** when a request is genuinely unclear or misses something essential, the AI can
     return one short question instead of acting. Nothing runs until the person answers.
   - **Continuing:** the answer, in the chat or in the Create worker form, continues the original
     request, which is read together with the answer.
   - **Another subject:** a message on another subject after a question is treated as a new
     request, and the question stays open.
   - **Context:** questions still waiting for an answer are listed in the chat context, so the AI
     knows what an answer refers to.
5. **Worker creation stopped with errors.**
   - **Before:** requests it could not settle stopped with an error message. Examples are an
     open-web permission it could not ground, or a schedule faster than once an hour.
   - **Now:** these become a question the person can answer, and the request continues.
6. **Schedules needed exact formats.**
   - **Days:** they had to be spelled out in full ("monday"). "Mon", "Tuesdays", "weekdays" and
     "weekends" are now understood.
   - **Times:** they had to be exactly "HH:MM". "9am", "9:30 pm" and "21.15" are now understood.
   - **Still refused:** anything unclear, such as "9" or "noon".
7. **Extra work in worker creation.**
   - **One choice:** choosing capabilities took an AI call per job even when only one capability
     was possible. That case is now decided without a call.
   - **Credentials:** stored credentials were checked one connection at a time and are now read in
     one query.

## What was checked and left alone

- **The reply checker** (`reply_claims.py`) looks only at the AI's own reply wording, for claims
  of work done without evidence, never at what the person typed. It stays.
- **Name matching** for workers, jobs and policies already accepts partial names and lists the
  options when several match.

## Compute

| Change | Effect |
|---|---|
| Create worker form | one AI call fewer per worker |
| Capability choice with one option | one AI call fewer per such job |
| Forgiving quote check | no correction call when only formatting differed |
| Questions awaiting an answer in the chat context | one small extra query per message (21 instead of 20) |
| Routing instructions | about 1,000 more characters per chat message |

## Proof

- **[test_understanding.py](../../backend/tests/test_understanding.py), 32 tests:**
  - word-based quote matching;
  - approval steps in any wording, invented ones refused, and old phrases still working;
  - asking without acting, then continuing with the original request plus the answer;
  - a message on another subject treated as new;
  - worker-creation doubts becoming questions;
  - the form skipping the guess;
  - day and time formats.
- **The context test** now also checks that questions awaiting an answer are listed.
- **The capability test** checks the one-option case makes no AI call.
- **Full suite:** all 1,340 backend tests pass; frontend type check, tests and build pass.
