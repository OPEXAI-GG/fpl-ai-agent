# Usability script: Weekly team check (round 1)

Five participants, about 20 minutes each, one facilitator plus one note-taker. Not yet run; no participants recruited.
Contacting participants needs god's approval (Layla owns recruitment).

## Who

Adults who play the fantasy football game most weeks and spend under 30 minutes on their weekly transfer.
Mix: at least 2 who mainly use a phone; at least 1 who has never paid for a fantasy tool.
Exclude employees and close friends of the team.

## Before any session (blocking)

Sessions cannot start until all of these are closed:
- god approves recruitment and participant contact.
- The consent and data items in the approved discovery draft (section 7) are settled: who the data controller is,
  recording consent wording, and how long notes and recordings are kept and when they are deleted.
- Each participant has read and agreed to that consent before recording starts.

## Setup

- Open `index.html`. Facilitator controls closed. Use a 390 px phone (or browser emulation) for 3 sessions and a laptop for 2.
- Record screen and voice only with written consent. Do not collect the participant's real team ID.
- Say: "This is a prototype with made-up players. We are testing the design, not you. Please think aloud."

## Tasks

1. **Find your team.** "Use team ID 1234567 to check this team." Note: hesitations, error messages seen.
2. **The 30-second read.** On "Your move this week", start a timer. Ask, without letting them scroll back once answered:
   - "What does it suggest you do this week?"
   - "What is the biggest risk with that?"
   - "How recent is the information it used?"
   Stop the timer at the third correct answer, or at 30 seconds.
3. **Compare.** "Compare this with not making a transfer. Which would you choose, and why?" No help unless stuck for 60 s.
4. **Uncertainty.** Point at "How sure is this?". "What does this tell you?" Then turn on *Show a sample range* and ask again.
5. **Stale data.** Turn on *Data is stale*. "Has anything changed? What would you do now?"
6. **Squad.** "Find who is on your bench, in order." Then: "Is there another way to view the squad?"
   The second question points to the List view, so score finding it as *prompted*, not unaided.
7. **Ideas we are testing.** "Would you use either of these? Would either be worth paying for, compared with what you use now, or not?"
   Do not mention any price. "I would not pay" is a valid result: record it as given, without probing for a reason to pay.

## Scoring sheet (one row per participant)

| P | Task 2: action ✓/✗ | risk ✓/✗ | timestamp ✓/✗ | time (s) | Task 3 unaided ✓/✗ | Task 4 understood "unavailable" ✓/✗ | Task 5 noticed stale ✓/✗ | Task 6 list view: unaided / prompted / not found | Task 7: would pay / would not pay / unsure | notes |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | | | | | | | | | | |
| 2 | | | | | | | | | | |
| 3 | | | | | | | | | | |
| 4 | | | | | | | | | | |
| 5 | | | | | | | | | | |

**Round target (not proof):** at least 4 of 5 answer all three Task 2 questions within 30 s and complete Task 3 unaided.
Five sessions find usability problems; they do not estimate how the wider population would do.

## Debrief questions

- "What, if anything, would make you trust this advice less?"
- "Was any word unclear?" (probe: gameweek, expected gain, uncertainty, bench order)
- "What did you expect to see that wasn't there?"

## After the round

Log each problem with severity (blocks task / slows task / cosmetic), how many participants hit it, and a screenshot.
Send findings to god; design changes go back through Wegz review.
