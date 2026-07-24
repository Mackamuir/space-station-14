# CI Test Reporting

## Summary
CI publishes test results as GitHub check runs using `dorny/test-reporter`, with
failure annotations linked to the source file and line. Each test job emits a
VSTest `.trx`, which is what the reporter reads. The sharded integration job
emits one `.trx` per shard; the `ci-success` job merges them into a single
combined `.trx` (tagging each result with the shard it ran on) so the whole
suite reports as one check run instead of one per shard. Same-repo runs report
inline, fork PRs report via a second workflow triggered by `workflow_run`.

## Prerequisites
- Tests run through `dotnet test` with `NUnit3TestAdapter` and
  `Microsoft.NET.Test.Sdk` (already referenced by `Content.Tests` and
  `Content.IntegrationTests`).
- A `Debug`-style configuration so PDBs carry source paths. CI uses `DebugOpt`.

## Steps

1. Emit a `.trx` from every test run, with run settings that map NUnit's
   `Warning` state onto `Failed` (see *Warning-state tests* below):

   ```bash
   dotnet test --no-build --configuration DebugOpt Content.Tests/Content.Tests.csproj \
     --logger "trx;LogFileName=results.trx" \
     --results-directory /tmp/test-results \
     --settings .github/ci.runsettings
   ```

   `Content.IntegrationTests` passes its shard's generated runsettings instead
   of `.github/ci.runsettings`, but that file *extends* `.github/ci.runsettings`:
   `partition_tests.py` reads it as the base and injects only the shard's
   `<Where>` filter, so both jobs share one NUnit run config (`MapWarningTo` and
   anything added later). The `dotnet test` invocation is otherwise identical.

   NUnit's own XML records failures the `.trx` drops (see *Known `.trx` blind
   spots*), but CI no longer emits it: the integration job matches the content
   job's run config and passes neither `NUnit.TestOutputXml` nor `--blame-hang`.
   Reproduce those flags locally to diagnose (see below).

2. Report same-repo runs inline, from the local file:

   ```yaml
   - name: Report Content.Tests results
     if: ${{ (success() || failure()) && (github.event_name != 'pull_request' || github.event.pull_request.head.repo.full_name == github.repository) }}
     uses: dorny/test-reporter@v3
     with:
       name: Content Tests
       path: /tmp/test-results/**/*.trx
       reporter: dotnet-trx
       list-suites: all
       list-tests: failed
       fail-on-error: false
   ```

3. Upload the `.trx` as an artifact so the fork path can consume it:

   ```yaml
   path: /tmp/test-results/**/*.trx
   ```

   Pull one down locally when a report looks wrong:

   ```bash
   gh run download <run-id> -R Mackamuir/space-station-14 -n test-trx-shard-5 -D ./out
   ```

4. Merge the shards for the same-repo integration report. Each shard uploads
   `test-trx-shard-<N>`; `ci-success` downloads them all, runs
   `Tools/_Starlight/merge_shard_trx.py <in-dir> <out.trx>` to fold them into one
   combined `.trx`, and points a single `dorny/test-reporter` step at the local
   file (no `artifact:` regex). The merge appends ` [shard N]` to every result's
   `testName` and prefixes each failure `<Message>`, so the combined report still
   shows which shard a result came from. The shard label is taken from the
   `shard_<N>_results.trx` filename (or its `test-trx-shard-<N>` directory).

5. Report fork PRs from `.github/workflows/test-report.yml`, which runs on
   `workflow_run` in the base repo's context and reads the artifact by name (a
   `/regex/` matches one artifact per shard, with `$1` interpolated into `name`).
   The fork path is **not** merged — it still reports one check run per shard,
   because the merge script needs the shard `.trx` on the same runner and the
   `workflow_run` reporter consumes artifacts by name through the API.

## Notes

### Why `dotnet-trx` and not `dotnet-nunit`

`reporter: dotnet-nunit` cannot produce source links. Its parser resolves stack
frames with the *JavaScript* frame regex (`/\((.*):(\d+):\d+\)$/`, imported from
`utils/node-utils.ts`), which never matches a .NET frame
(`... in /path/File.cs:line 42`). Annotations then fall back to `tr.path` — the
report file itself. It also prefixes every suite name with the assembly
filename, because NUnit 3's outermost `<test-suite>` node is `type="Assembly"
name="Content.Tests.dll"` and the parser joins all ancestor suite names.

`dotnet-trx` resolves paths correctly (`/ in (.+):line (\d+)$/`), which is why
the `.trx` is the reported format.

### Known `.trx` blind spots

The `.trx` is not a superset of NUnit's own result XML. Two failure modes are
invisible in a TRX-only report; both are accepted trade-offs of this pipeline:

- `NUnit3TestAdapter` reports a result to VSTest when the test *body* finishes.
  A failure raised afterwards — during teardown, or by the pooled server's
  dirty-dispose check — lands in NUnit's XML while the `.trx` still says
  `Passed`.
- dorny discards an error unless **both** `<Message>` and `<StackTrace>` are
  present and non-empty. `<ErrorInfo>` for a result derived from NUnit's
  `Warning` state never has both, so it renders as a bare test name with no
  diagnostic. See *Warning-state tests* below.

To investigate either case, add `-- NUnit.TestOutputXml=<dir>` to the
`dotnet test` invocation locally and compare NUnit's XML against the `.trx`.
A relative path there resolves against the **assembly work directory**
(`bin/Content.IntegrationTests/…`), not the current directory. CI does **not**
capture this: the integration job shares the content job's run config and passes
no `NUnit.TestOutputXml`, so these blind spots are local-diagnosis-only. The
`harvest-test-timings.yml` baseline still runs with `--blame-hang` if you need a
hang dump from a full run.

### Misattributed assertion failures

Integration test assertions run on the server game loop
(`IntegrationGameLoop.SingleThreadRunUntilEmpty`), not the NUnit test thread. A
failure raised there is delivered asynchronously, and NUnit attaches it to
whichever test is current *at delivery time*. When the suite runs in full, that
is frequently a different, unrelated test.

The symptom is a `<test-case>` whose `name` and whose `<stack-trace>` disagree:

```
name  : MachineBoardTest.TestBladeServerBoardHasValidBladeServer
stack : at PrototypeSaveTest.<UninitializedSaveTest>b__1() in PrototypeSaveTest.cs:line 141
        at Robust.UnitTesting...IntegrationGameLoop.SingleThreadRunUntilEmpty()
```

**Always trust the stack trace over the test name.** The named test is a
bystander; the first repository frame identifies the test that actually failed.

Two consequences worth internalising:

- Such a failure usually does not reach the `.trx` at all — the bystander is
  recorded `Passed` with no `ErrorInfo`, because its result was reported to
  VSTest before the failure arrived. NUnit's XML is the only record.
- The originating test still gets its pair dirty-disposed, so it surfaces as a
  bare `Test was dirty-disposed.` warning with no cause attached. That warning
  is a tombstone, not the bug — go looking for a misattributed failure
  elsewhere in the same run.

Running one test, or a narrow `--filter`, hides all of this: with nothing else
executing, the failure is delivered back to its own test and reports normally.
A green `.trx` from a full run therefore does **not** imply a green NUnit XML.

### Warning-state tests

`Assert.Warn` puts a test into NUnit's `Warning` result state. The pooled test
harness uses it: `TestPair.OnDirtyDispose` calls
`Assert.Warn("Test was dirty-disposed.")` whenever a pair is not clean-returned.

NUnit stores a warning's text under `<reason><message>`, **not** under
`<failure><message>`. The adapter sources a TRX `<Message>` from the *failure*
node and a `<StackTrace>` from the *failure* node, so the two `MapWarningTo`
settings lose opposite halves of the diagnostic:

| `MapWarningTo` | TRX `outcome` | `<Message>` | `<StackTrace>` | Counted as failure |
| --- | --- | --- | --- | --- |
| `Skipped` (adapter default) | `NotExecuted` | reason text | absent | **no** |
| `Failed` | `Failed` | absent | present | yes |

Under `Failed` the reason text is also stripped from `<StdOut>` — the adapter
routes it into the failure record instead of the output stream, then drops it.
The text exists nowhere in the `.trx`. The stack trace still names the origin
(`TestPair.Recycle.cs:line 44` is the dirty-dispose `Assert.Warn`), and a
leading `1)` marks the frame as coming through NUnit's warning formatter.

`Skipped` is the worse half: it does not increment the `failed` counter, so
`dotnet test` exits `0` and a warning-only failure passes CI silently. Every
`dotnet test` invocation must therefore pass run settings containing:

```xml
<RunSettings>
  <NUnit>
    <MapWarningTo>Failed</MapWarningTo>
  </NUnit>
</RunSettings>
```

`.github/ci.runsettings` holds this for the unsharded projects, and each
generated `shard_N.runsettings` is built by extending that same file with the
shard's `<Where>` filter — so the mapping is defined in exactly one place.
Adding a new test job whose runsettings does not include it reintroduces the
false green.

Spotting it in a `.trx` you have already downloaded: a genuine skip takes
sub-millisecond time, so a `NotExecuted` result with a real duration is a test
that ran and then had its outcome downgraded.

```bash
python3 -c "
import xml.etree.ElementTree as ET,sys
ns='{http://microsoft.com/schemas/VisualStudio/TeamTest/2010}'
for u in ET.parse(sys.argv[1]).getroot().findall('.//'+ns+'UnitTestResult'):
    if u.get('outcome')=='NotExecuted':
        print(u.get('duration'), u.get('testName'))
" results.trx
```

Note that this is invisible when reproducing locally, because a real assertion
failure produces `Failed` regardless of the mapping. Only a test whose *sole*
complaint is a warning is affected.

### Path resolution

Annotations come **only from failure stack traces** — no .NET result format
records file/line for passing tests. `RunConfiguration.CollectSourceInformation`
affects VSTest *discovery* only, never reaches the `.trx`, and costs discovery
time, so it is not set. `/property:GenerateFullPaths=true` is likewise
irrelevant: it formats *compiler* diagnostics, and these steps run `--no-build`.

Paths are resolved by suffix-matching against the repo's tracked files:
`getBasePath` finds the longest tracked file the absolute stack-trace path ends
with and strips the remainder as the work dir. Consequences:

- Inline reporter steps shell out to `git ls-files`, so the job **must**
  `actions/checkout` first. The build tarball excludes `.git`, so checkout runs
  before the tarball is extracted over the top.
- The `workflow_run` reporter uses `artifact:` mode, which lists tracked files
  through the GitHub API instead. It needs no checkout.
- `RobustToolbox` is a submodule, so its files are not tracked at this level.
  The parser walks down the stack until it finds a tracked file, so failures
  thrown inside the engine annotate the first frame in Content code.
- Build and test run in different jobs but share the runner path
  `/home/runner/work/space-station-14/space-station-14`, so PDB paths line up.
  Moving the build into a container or onto a different runner label breaks this
  silently — paths stop resolving, with no error.

### Permissions

Fork PRs get a read-only token regardless of the `permissions:` block, so
`checks: write` is silently dropped there. Guard inline reporter steps on
`github.event.pull_request.head.repo.full_name == github.repository` — **not**
on `head.repo.fork`, since this repository is itself a fork and that flag is
always true.

### Miscellaneous

- Glob `**/*.trx` rather than the results directory, so non-trx result files
  stay out of the reporter's input. (The PR integration job no longer runs
  `--blame-hang`; the `harvest-test-timings.yml` baseline still does, and its
  `Sequence_*.xml` would otherwise leak into a directory glob.)
- `fail-on-error: false` on reporter steps: the test step has already failed the
  job, and failing again just marks the same problem red twice.
- `list-suites: all` keeps passing suites visible with their counts;
  `list-tests: failed` stops the report enumerating thousands of passing names.
