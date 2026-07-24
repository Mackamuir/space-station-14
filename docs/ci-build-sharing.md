# CI Build Sharing

## Summary
`build-test-debug.yml` compiles the solution once and every consumer job reuses
that output through a workspace tarball, rather than each workflow building for
itself. The NuGet package cache is shared alongside it, since it lives outside
the workspace and the tarball cannot carry it.

## Prerequisites
- All consumers must be jobs **inside the same workflow run**. Artifacts and job
  outputs are scoped to a run, so a separate workflow — including a reusable
  `workflow_call` one — cannot reach them.
- The project must be in `SpaceStation14.slnx`, so the root `dotnet build`
  already produces its binaries.

## Steps

1. The `build` job compiles the whole solution once and archives the workspace:

   ```bash
   dotnet build --configuration DebugOpt /m
   tar --use-compress-program='zstd -T0' -cf /tmp/build.tar.zst --exclude='.git' .
   ```

   It uploads that as the `build-output` artifact with `retention-days: 1` and
   `compression-level: 0` (zstd has already compressed it).

2. It also populates the NuGet cache, read-write:

   ```yaml
   - name: Cache NuGet packages
     uses: actions/cache@v4
     with:
       path: ~/.nuget/packages
       key: nuget-${{ hashFiles('Directory.Packages.props') }}
       restore-keys: nuget-
   ```

3. Each consumer job declares `needs: build` and reassembles the workspace in
   this order — checkout first, tarball laid over the top:

   ```yaml
   - uses: actions/checkout@v7
   - uses: actions/setup-dotnet@v6
   - uses: actions/cache/restore@v4      # read-only; build already saved it
     with:
       path: ~/.nuget/packages
       key: nuget-${{ hashFiles('Directory.Packages.props') }}
       restore-keys: nuget-
   - uses: actions/download-artifact@v8
     with:
       name: build-output
   - run: tar --use-compress-program='zstd -d -T0' -xf build.tar.zst && rm build.tar.zst
   - run: dotnet restore <TheProject>.csproj
   - run: dotnet test --no-build --configuration DebugOpt ...
   ```

4. Add the new job to **both** aggregator jobs at the bottom of the file:
   `ci-success` (its `needs:` list *and* the `if:` expression) and `cleanup`
   (its `needs:`, or the shared artifact is deleted mid-run).

## Notes

- **Reusable workflows do not share compute.** `workflow_call` deduplicates YAML
  only; each caller still gets its own runner and rebuilds from scratch. Sharing
  compiled output requires `needs:`-dependent jobs in one run.
- **`dotnet restore` before a `--no-build` step is not redundant.** The tarball
  is `tar -cf … .` from the workspace root, and `~/.nuget/packages` is outside
  it, so packages do not travel with the build.
- **Cache key uses the root `Directory.Packages.props` only.** Consumer jobs have
  no submodules until the tarball is extracted, so `RobustToolbox`'s copy cannot
  be hashed at `setup-dotnet` time — and the key must be byte-identical across
  jobs to hit. `restore-keys: nuget-` absorbs the drift: a partial NuGet cache is
  still correct, `dotnet restore` just fetches what is missing.
- **Only the `build` job saves the cache** (`actions/cache`); consumers use
  `actions/cache/restore`. Eight jobs racing to upload the same ~GB of packages
  would waste most of the saving.
- **Pass `--configuration` to `dotnet run --no-build`.** It defaults to `Debug`
  and will look in the wrong intermediate directory.
  `Content.YAMLLinter.csproj` happens to hardcode a configuration-agnostic
  `<OutputPath>`, which masks the problem — do not rely on that.
- **Caching build output is not viable at this size.** `bin/` plus
  `RobustToolbox/bin/` plus `obj/` is roughly 4.2 GB against GitHub's 10 GB
  per-repository cache limit, so entries would thrash under LRU eviction and the
  compressed transfer could cost more than the compile. A stale restore also
  produces a green run that does not reflect the commit — a worse failure than a
  slow one.
- **Do not add `paths-ignore` to skip this workflow.** `ci-success` ("Debug CI
  Required") is a required status check; a workflow skipped by a path filter
  never reports, and the PR blocks forever. Skipping needs a companion workflow
  with an identical job name and the inverse filter.
- **Prototype-only changes still need the full suite.** `EntityTest` spawns every
  non-abstract `EntityPrototype`, and ~57 integration test files enumerate
  prototypes. The YAML linter validates *serialization* (unknown datafields, type
  mismatches, dangling `ProtoId<T>`); it cannot catch unreachable construction
  graph nodes, missing RSI states, unbalanced reactions, or entities that throw
  during startup. That is what `*PrototypeTest.cs` exists for.
