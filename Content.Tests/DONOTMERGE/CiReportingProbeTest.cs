using System;
using NUnit.Framework;

namespace Content.Tests._Starlight;

// ===================================================================
//  TEMPORARY -- DELETE BEFORE MERGING.
//
//  Deliberately failing tests used to exercise the CI reporting
//  pipeline end to end: dotnet test -> trx + NUnit XML ->
//  Tools/_Starlight/normalize_trx.py -> dorny/test-reporter.
//
//  Each case covers a different path through that pipeline; see the
//  comment on each test. Remove the whole file when finished:
//      rm Content.Tests/_Starlight/CiReportingProbeTest.cs
// ===================================================================
[TestFixture]
public sealed class CiReportingProbeTest
{
    /// <summary>
    ///     Baseline. NUnit writes both a message and a stack trace, so the
    ///     trx carries a complete ErrorInfo and dorny renders it without
    ///     any help. Confirms the reporter is wired up and that the
    ///     annotation resolves to this file and line.
    /// </summary>
    [Test]
    public void ProbeAssertionFailure()
    {
        Assert.That(2 + 2, Is.EqualTo(5), "Deliberate CI probe: arithmetic assertion.");
    }

    /// <summary>
    ///     An unhandled exception, which reaches the trx by a different
    ///     route to an assertion failure. Its stack trace is the deepest
    ///     of the three, so it also checks that path resolution picks the
    ///     first frame inside this repository.
    /// </summary>
    [Test]
    public void ProbeUnhandledException()
    {
        throw new InvalidOperationException("Deliberate CI probe: unhandled exception.");
    }

    /// <summary>
    ///     The interesting one. A warning is recorded as result="Warning"
    ///     with its text in &lt;reason&gt;, not &lt;failure&gt;.
    ///
    ///     With MapWarningTo=Failed the adapter reports it to VSTest as a
    ///     failure, but TestConverter only reads Failure?.Message for a
    ///     failed outcome -- so the trx gets an ErrorInfo with a
    ///     StackTrace and no Message, and dorny discards the whole error.
    ///     normalize_trx.py is what recovers the reason text.
    ///
    ///     Without MapWarningTo=Failed this renders as a *skip*, which is
    ///     exactly how a dirty-disposed pooled test hides locally.
    /// </summary>
    [Test]
    public void ProbeWarning()
    {
        Assert.Warn("Deliberate CI probe: warning, mapped to a failure by MapWarningTo.");
    }
}
