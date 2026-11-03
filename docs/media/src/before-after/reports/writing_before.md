<!-- slop-gate-report -->
## slop-gate: ❌ Blocked

1 blocking, 5 advisory. Checked 2 prose files at `HEAD`.

Errors block the merge; warnings are advice. Fix the errors, push, and this report updates. To accept a finding on purpose, see [suppressing findings](https://github.com/mikejmckinney/slop-gate#suppressing-a-finding).

### prose (1 blocking, 5 advisory)

_Writing patterns from the house style guide. Rewrite the sentence; a word swap rarely fixes the underlying pattern._

| | Location | Rule | Problem and fix |
|---|---|---|---|
| 🛑 | `why-gemini-2.5-flash.md`:3 | `SlopGate.ContrastFormula` | 'This isn't just tedious; it's' is a contrast formula. State the true claim once. Keep a contrast only when both sides are concrete, competing explanations. |
| ⚠️ | `why-gemini-2.5-flash.md`:3 | `SlopGate.AIVocabulary` | 'meticulously' is inflated vocabulary. Use the word you'd say out loud. |
| ⚠️ | `why-gemini-2.5-flash.md`:5 | `SlopGate.CorporateVerbs` | 'streamlines' names no concrete action. Say what was done to what, or cut the sentence. |
| ⚠️ | `why-gpt-4o-mini.md`:3 | `SlopGate.CorporateVerbs` | 'streamline' names no concrete action. Say what was done to what, or cut the sentence. |
| ⚠️ | `why-gpt-4o-mini.md`:5 | `SlopGate.FillerTransitions` | '. Moreover,' is a filler transition. Delete it, or write the real link: because, so, except, then. |
| ⚠️ | `why-gpt-4o-mini.md`:5 | `SlopGate.PrestigeLabels` | 'seamless' is a prestige label. Describe the capability, its operating conditions, and its limits. |
