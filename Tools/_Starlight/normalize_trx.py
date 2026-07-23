#!/usr/bin/env python3
"""Fold NUnit's own result XML into the TRX so one report carries everything.

Two independent defects make a plain TRX report lossy for pooled integration
tests:

1. dorny/test-reporter's dotnet-trx parser discards an error unless BOTH
   <Message> and <StackTrace> are present and non-empty. NUnit3TestAdapter
   omits <Message> for teardown/dispose failures, so those render as a bare
   test name with no diagnostic at all.

2. NUnit3TestAdapter reports a result to VSTest when the test body finishes.
   A failure raised afterwards -- during teardown, or by the pooled server's
   dirty-dispose check -- lands in NUnit's XML but the TRX still says Passed.
   Reporting from the TRX alone therefore hides real assertion failures.

The reverse swap is not an option: the dotnet-nunit parser resolves stack
frames with the JavaScript frame regex and can never produce a source link.
NUnit's <stack-trace> is ordinary .NET text, so once it is inside the TRX the
dotnet-trx parser resolves it to a file and line normally.

Usage:
    normalize_trx.py <trx-dir> [nunit-xml-dir]
"""

import glob
import os
import sys
import xml.etree.ElementTree as ET

TRX_NS = 'http://microsoft.com/schemas/VisualStudio/TeamTest/2010'


def q(tag):
    return f'{{{TRX_NS}}}{tag}'


def collect_nunit_failures(nunit_dir):
    """Return {name_or_fullname: (message, stack_trace)} for every NUnit failure.

    Suite-level failures (OneTimeSetUp/OneTimeTearDown) carry <failure> too and
    are indexed the same way. Both the short and the fully qualified name are
    used as keys because the TRX records only the short display name, which is
    not guaranteed unique.
    """
    failures = {}
    if not nunit_dir or not os.path.isdir(nunit_dir):
        return failures

    for path in glob.glob(os.path.join(nunit_dir, '*.xml')):
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError as exc:
            print(f'skipping {path}: {exc}', file=sys.stderr)
            continue

        for elem in root.iter():
            if elem.tag not in ('test-case', 'test-suite'):
                continue
            failure = elem.find('failure')
            if failure is None:
                continue

            message = failure.find('message')
            stack = failure.find('stack-trace')
            message_text = (message.text or '').strip() if message is not None else ''
            stack_text = (stack.text or '').strip() if stack is not None else ''
            if not message_text and not stack_text:
                continue

            for key in (elem.get('fullname'), elem.get('name')):
                if key:
                    failures.setdefault(key, (message_text, stack_text))

    return failures


def message_from_stack_trace(stack_trace):
    """Recover a headline from a stack trace when nothing better exists.

    NUnit prefixes multiple-failure blocks with `1)`, `2)`, ...; any line that
    is not a frame is the failure text itself.
    """
    for raw in stack_trace.splitlines():
        line = raw.strip()
        if not line:
            continue
        # Strip NUnit's result numbering before testing for a frame.
        body = line.split(')', 1)[1].strip() if line[:1].isdigit() and ')' in line else line
        if body.startswith('at ') or body.startswith('--- End of'):
            continue
        return body
    return ''


def index_results(root):
    """Map both fully qualified and short test names to their UnitTestResult."""
    method_by_id = {}
    for unit_test in root.iter(q('UnitTest')):
        method = unit_test.find(q('TestMethod'))
        if method is None:
            continue
        class_name = method.get('className') or ''
        # className carries an assembly-qualified suffix when the test lives in
        # a referenced assembly; only the type name participates in NUnit's
        # fullname.
        class_name = class_name.split(',', 1)[0].strip()
        method_by_id[unit_test.get('id')] = f'{class_name}.{method.get("name")}'.strip('.')

    index = {}
    for result in root.iter(q('UnitTestResult')):
        for key in (method_by_id.get(result.get('testId')), result.get('testName')):
            if key:
                index.setdefault(key, result)
    return index


def ensure_error_info(result):
    output = result.find(q('Output'))
    if output is None:
        output = ET.SubElement(result, q('Output'))
    error = output.find(q('ErrorInfo'))
    if error is None:
        error = ET.SubElement(output, q('ErrorInfo'))
    return error


def set_child_text(error, tag, text, overwrite=False):
    elem = error.find(q(tag))
    if elem is None:
        elem = ET.Element(q(tag))
        # ErrorInfo's schema order is Message then StackTrace.
        error.insert(0 if tag == 'Message' else len(error), elem)
    elif (elem.text or '').strip() and not overwrite:
        return False
    elem.text = text
    return True


def update_counters(root):
    """Keep <Counters> consistent with the results we just rewrote."""
    counters = root.find(f'{q("ResultSummary")}/{q("Counters")}')
    if counters is None:
        return
    outcomes = [r.get('outcome') for r in root.iter(q('UnitTestResult'))]
    failed = sum(1 for o in outcomes if o == 'Failed')
    passed = sum(1 for o in outcomes if o == 'Passed')
    counters.set('total', str(len(outcomes)))
    counters.set('executed', str(passed + failed))
    counters.set('passed', str(passed))
    counters.set('failed', str(failed))

    summary = root.find(q('ResultSummary'))
    if summary is not None and failed:
        summary.set('outcome', 'Failed')


def normalize(trx_path, nunit_failures):
    ET.register_namespace('', TRX_NS)
    tree = ET.parse(trx_path)
    root = tree.getroot()

    index = index_results(root)
    promoted = 0
    filled = 0

    # 1. Failures NUnit saw but the TRX recorded as passing.
    for name, (message, stack) in nunit_failures.items():
        result = index.get(name)
        if result is None:
            continue
        error = ensure_error_info(result)
        if result.get('outcome') != 'Failed':
            result.set('outcome', 'Failed')
            promoted += 1
        if message:
            set_child_text(error, 'Message', message)
        if stack:
            set_child_text(error, 'StackTrace', stack)

    # 2. Failures the TRX has but with no <Message>, which dorny would discard.
    for result in root.iter(q('UnitTestResult')):
        if result.get('outcome') != 'Failed':
            continue
        error = ensure_error_info(result)
        message = error.find(q('Message'))
        if message is not None and (message.text or '').strip():
            continue
        stack_elem = error.find(q('StackTrace'))
        stack_text = (stack_elem.text or '') if stack_elem is not None else ''
        text = (
            message_from_stack_trace(stack_text)
            or 'Test failed; NUnit recorded no message. See the stack trace below.'
        )
        set_child_text(error, 'Message', text, overwrite=True)
        # dorny drops the error unless StackTrace is non-empty too.
        if not stack_text.strip():
            set_child_text(error, 'StackTrace', '(no stack trace recorded)', overwrite=True)
        filled += 1

    if promoted or filled:
        update_counters(root)
        tree.write(trx_path, encoding='utf-8', xml_declaration=True)

    return promoted, filled


def main(argv):
    if not 2 <= len(argv) <= 3:
        print(__doc__, file=sys.stderr)
        return 2

    nunit_failures = collect_nunit_failures(argv[2] if len(argv) > 2 else None)

    files = glob.glob(os.path.join(argv[1], '**', '*.trx'), recursive=True)
    promoted = filled = 0
    for trx_path in files:
        try:
            p, f = normalize(trx_path, nunit_failures)
        except ET.ParseError as exc:
            # A malformed trx is not worth failing the job over; the reporter
            # step will surface it.
            print(f'skipping {trx_path}: {exc}', file=sys.stderr)
            continue
        promoted += p
        filled += f

    print(
        f'normalize_trx: {len(files)} file(s); '
        f'{promoted} failure(s) recovered from NUnit, {filled} message(s) filled in'
    )
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
