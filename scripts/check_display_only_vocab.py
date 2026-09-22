#!/usr/bin/env python3
"""CI check 9h -- presentation-group containment.

Two assertions, from 09-design-system.md §4.2 properties 1 and 3:

  1. TOTAL PARTITION. Every domain family in taxonomy/domains.yaml belongs to exactly one
     group in taxonomy/domain_groups.yaml, and no group member is anything other than a
     family. Adding a 20th family without assigning it a group fails in the same commit.

  3. NOT IN ANALYTICS. No presentation-group id appears in any shipped artifact.
     build/atlas.json is the single permitted consumer, because banding is a layout
     property. Every coverage percentage, density figure, gap score and curation-confidence
     value is computed over families and subdomains, never over groups.

A display-only grouping that leaks into a published gap claim is a data-integrity failure
invisible in the diff: a specialist who disputes the grouping has then invalidated the
finding without touching the data.

    python scripts/check_display_only_vocab.py
    python scripts/check_display_only_vocab.py --artifacts <dir>   # scan a fixture instead

Exit 0 if both hold, 1 otherwise. If there are no artifacts to scan -- which is the normal
state until the first build -- the containment assertion reports NOT RUN and says so loudly.
It is never reported as a pass.
"""
import argparse
import os
import re
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TAXONOMY = os.path.join(ROOT, 'taxonomy')
BUILD = os.path.join(ROOT, 'build')

# The one artifact permitted to carry a group id, per 09 §4.2 property 3.
PERMITTED = {'atlas.json'}

# Artifacts the grouping is explicitly forbidden from, named so a missing one is visible
# rather than silently unscanned. 08 §4.2 owns the shipped-artifact list; these are the
# subset 09 §4.2 and 05 §9h name by hand.
NAMED_FORBIDDEN = [
    'facets.json',
    'corpus.json',
    'derived/coverage.json',
    'derived/gaps.json',
    'derived/ecosystem.json',
    'suite.yaml',
    'croissant.jsonld',
]

SCAN_SUFFIXES = ('.json', '.jsonld', '.yaml', '.yml')


def display_path(path):
    """relpath raises across Windows drive letters, which a fixture on another drive hits."""
    try:
        return os.path.relpath(path, ROOT).replace(os.sep, '/')
    except ValueError:
        return path.replace(os.sep, '/')


def load(path):
    with open(path, encoding='utf-8') as fh:
        return yaml.safe_load(fh)


def token_re(ids):
    """Match a group id only as a whole token.

    A bare substring search is wrong in both directions here: `reasoning-general-ability`
    contains the family slug `reasoning-general`, and a future group id could sit inside a
    longer identifier. Bounding on the id characters keeps the check from firing on a
    family slug that merely shares a prefix with a group id.
    """
    alt = '|'.join(re.escape(i) for i in sorted(ids, key=len, reverse=True))
    return re.compile(r'(?<![A-Za-z0-9_-])(%s)(?![A-Za-z0-9_-])' % alt)


def check_partition(groups, families):
    problems = []
    seen = {}
    for g in groups:
        gid = g.get('id')
        for m in g.get('members') or []:
            if m in seen:
                problems.append('%s is in both %s and %s' % (m, seen[m], gid))
            seen[m] = gid
            if m not in families:
                problems.append('%s is a member of %s but is not a domain family' % (m, gid))
    unassigned = sorted(families - set(seen))
    if unassigned:
        problems.append('families in no group: %s' % unassigned)
    gids = [g.get('id') for g in groups]
    if len(set(gids)) != len(gids):
        problems.append('duplicate group id')
    collide = set(gids) & families
    if collide:
        problems.append('group id equals a family slug: %s' % sorted(collide))
    return problems


def scan_artifacts(root, pattern):
    """Return (hits, files_scanned). A hit is (relpath, group_id, line_no)."""
    hits, scanned = [], []
    if not os.path.isdir(root):
        return hits, scanned
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in sorted(filenames):
            if not fn.endswith(SCAN_SUFFIXES):
                continue
            if fn in PERMITTED:
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace(os.sep, '/')
            scanned.append(rel)
            with open(full, encoding='utf-8', errors='replace') as fh:
                for n, line in enumerate(fh, 1):
                    for m in pattern.finditer(line):
                        hits.append((rel, m.group(1), n))
    return hits, scanned


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--artifacts', default=BUILD,
                    help='directory of built artifacts to scan (default: build/)')
    args = ap.parse_args()

    gf = load(os.path.join(TAXONOMY, 'domain_groups.yaml'))
    df = load(os.path.join(TAXONOMY, 'domains.yaml'))
    families = set(t['id'] for t in (df.get('terms') or []) if t.get('parent') is None)
    groups = gf.get('groups') or []

    failures = []
    print('check_display_only_vocab (CI 9h)')
    print('  taxonomy/domain_groups.yaml: %d groups over %d families\n' % (len(groups), len(families)))

    # --- the flag itself
    if gf.get('display_only') is not True:
        failures.append('domain_groups.yaml does not carry display_only: true')
        print('  FAIL    display_only flag')
    else:
        print('  PASS    display_only flag                  true')

    # --- property 1
    problems = check_partition(groups, families)
    if problems:
        failures.extend(problems)
        print('  FAIL    total partition                    %s' % '; '.join(problems))
    else:
        print('  PASS    total partition                    %d families, each in exactly one group'
              % len(families))

    # --- property 3
    gids = set(g.get('id') for g in groups if g.get('id'))
    hits, scanned = scan_artifacts(args.artifacts, token_re(gids))
    where = display_path(args.artifacts)
    if not scanned:
        print('  NOT RUN containment in artifacts           nothing to scan under %s/' % where)
        print('          The build has not run yet, so property 3 is UNTESTED here. This is')
        print('          not a pass. It becomes enforceable once bench build writes artifacts.')
        missing = [p for p in NAMED_FORBIDDEN
                   if not os.path.exists(os.path.join(args.artifacts, p))]
        print('          Named artifacts still absent: %d of %d' % (len(missing), len(NAMED_FORBIDDEN)))
    elif hits:
        for rel, gid, n in hits[:20]:
            failures.append('%s:%d carries the group id %s' % (rel, n, gid))
            print('  FAIL    containment in artifacts           %s:%d -> %s' % (rel, n, gid))
        if len(hits) > 20:
            print('          ... and %d more' % (len(hits) - 20))
    else:
        print('  PASS    containment in artifacts           %d artifacts scanned, no group id'
              % len(scanned))
        print('          permitted and skipped: %s' % ', '.join(sorted(PERMITTED)))

    if failures:
        print('\nFAILED (%d):' % len(failures))
        for f in failures:
            print('  %s' % f)
        return 1
    print('\n  OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
