# LANG-818 root-cause validation

## Pinned source and ground truth

- Buggy revision: `cfab1e4fa19755d65de7b81206d86f9e98622d8e`.
- Fix revision: `04babff3798680e012646988841bcc710a9e3c54`.
- The task label in `benchmarks/commons-lang-defects4j.json` is
  `org.apache.commons.lang3.time.FastDatePrinter.TimeZoneNameRule#appendTo(StringBuffer,Calendar)`.
- The source checkout used for verification is the temporary pinned
  Defects4J checkout recorded in the local evaluation workspace. It is not
  part of the CodeLens repository.

## Causal chain

1. `FastDateFormat.format(Calendar)` delegates formatting to
   `FastDatePrinter` with the caller-provided `Calendar`.
2. The printer applies its parsed formatting rules. The timezone-name rule's
   `appendTo(StringBuffer, Calendar)` receives the caller's calendar.
3. At the buggy revision, `TimeZoneNameRule` stores cached standard/daylight
   names derived from the formatter's configured timezone. Its branch tests
   `mTimeZone.useDaylightTime()` but tests the `DST_OFFSET` on the passed
   calendar. These values can describe different time zones when a caller
   supplies a calendar whose zone differs from the formatter's zone.
4. It then appends the cached name for the formatter zone, so a calendar
   passed to `format(Calendar)` can be rendered with the wrong zone name.
5. The fix removes the stale timezone field from the rule, obtains
   `calendar.getTimeZone()` inside `appendTo`, and selects the localized
   standard/daylight display name for that calendar zone.

The method-level bug is not `FastDateFormat.format(Date)`. `Date` contains
an instant but no caller-selected time zone; the reported mismatch is
specifically on the `Calendar` overload. Earlier references to
`FastDatePrinter.format(Date)` were not the causal location.

## Upstream regression evidence

The fixing commit adds
`FastDatePrinterTest.testCalendarTimezoneRespected()`. It creates a calendar
in a zone other than the JVM default, formats with pattern `h:mma z`, and
compares `FastDateFormat.getInstance(pattern).format(cal)` to a
`SimpleDateFormat` explicitly configured with that calendar's time zone.
This test is source evidence from the upstream fixing commit; it has not
been added to or executed as a CodeLens test.

## CodeLens validation limits

The saved Lang-818 index contains both
`FastDateFormat#format(Calendar)` and
`FastDatePrinter#format(Calendar)`. Its call graph includes the delegation
edge. The internal `TimeZoneNameRule` is represented in parsed methods and
the benchmark gold label. Retrieval rankings are evidence of localization,
not proof that an LLM answer is causally correct. No new provider call was
made for this source validation.

## Reproduced retrieval rank and scoring evidence

The final five-task retrieval-only rerun used the pinned buggy revision and
the exact query in `benchmarks/commons-lang-defects4j.json`. The gold method
was present in the 161-candidate ACOR pool. Its full-candidate rank before
budget selection was **62**; it appeared at position **53** in the final
selected context. The query analyzer extracted only the class anchor
`FastDateFormat`; `_resolve_anchors` therefore returned no method anchors.

The measured gold score was semantic **0.8357**, call-graph **0.0**,
dependency **0.0**, symbol **0.5**, composite **0.459261**. The gold method
has no incoming or outgoing call-graph edge in this parsed index, so the
call-graph score is necessarily zero. The dependency graph has no path from
its `FastDatePrinter.TimeZoneNameRule` class to `FastDateFormat`, so its
dependency score is zero. The symbol score is the existing class-name
overlap with the enclosing `FastDatePrinter` class.

The top ten scored candidates were `FastDateFormat` methods with composite
scores between **0.7484** and **0.7705**; those candidates matched the
class anchor exactly and received dependency and symbol scores of **1.0**.
Thus candidate generation succeeds, but the scoring signals over-prioritize
methods in the explicitly named class relative to the relevant method in a
similarly named neighboring implementation class. The parser's current
call graph does not encode dispatch from the `Rule` interface to this
concrete implementation.

A diagnostic trial propagated one automatically selected similar
same-package class anchor. On the five tasks it changed LANG-818's
full-candidate rank from 62 to 67 and selected-context position from 53 to
55; it was not adopted. This did not provide a justified improvement.
No speculative score boost or LANG-818-specific rule was committed.
The limitation is unresolved pending a general, validated treatment of
class-only anchors and interface dispatch.

The older targeted report records `gold_method_rank: 53` without specifying
whether that value is before or after budget selection. The current run
measures both stages separately, so do not compare those fields as if they
were the same metric. Complete row-level results are in
[`../benchmarks/results/commons-lang-final-completion-retrieval-only-5000.md`](../benchmarks/results/commons-lang-final-completion-retrieval-only-5000.md).

## Reproduction

In the temporary pinned checkout, inspect the two revisions with:

```powershell
git show cfab1e4fa19755d65de7b81206d86f9e98622d8e:src/main/java/org/apache/commons/lang3/time/FastDatePrinter.java
git show 04babff3798680e012646988841bcc710a9e3c54 -- src/main/java/org/apache/commons/lang3/time/FastDatePrinter.java
git show 04babff3798680e012646988841bcc710a9e3c54 -- src/test/java/org/apache/commons/lang3/time/FastDatePrinterTest.java
```

The local pinned checkout is under `%TEMP%\codelens-lang-evaluation-...\checkouts\lang-8-cfab1e4f`; replace the abbreviated temporary directory with the exact path on the machine running the commands.
