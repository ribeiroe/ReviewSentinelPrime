const {test} = require('node:test');
const assert = require('node:assert/strict');
const {missingItems, evidenceContext, evidenceContextFromRun, screeningEvidence, reviewerEvidence, consistencyWarnings} = require('../../static/js/prisma_guidance.js');

function base() {
    return {
        runId: 'run-1',
        identification: {
            total_found: 100, retrieved: 60, removed_date_at_database: 30, not_retrieved: 10,
            databases: {springer: {status: 'partial', warning: 'Retrieval cap', removedDateAtDatabase: 30}}
        },
        screening: {duplicates: 10, year_range: 2},
        eligibility: {exclude_terms: 3},
        included: 45,
        taScreening: {runId: 'run-1', total: 45, screened: 40, included: 29, excluded: 9, unsure: 2,
            reasonCounts: {'Not relevant': 7, 'No reason given': 2}}
    };
}

test('guided questions require explicit confirmation even for automatic evidence', () => {
    const items = [{section: 'METHODS'}, {id: '3', mode: 'auto'}, {id: '4', mode: 'auto'},
        {id: '5', mode: 'auto'}, {id: '18', mode: 'manual'}, {id: '19', mode: 'manual'}];
    const confirmed = {checked: true, confirmedRevision: 'current'};
    assert.deepEqual(missingItems(items, {'4': confirmed, '19': confirmed}, 'current')
        .map(item => item.id), ['3', '5', '18']);
    assert.deepEqual(missingItems(items, {}, 'current').map(item => item.id), ['3', '4', '5', '18', '19']);
    assert.deepEqual(missingItems(items, {'3': confirmed, '4': confirmed,
        '5': confirmed, '18': confirmed, '19': confirmed}, 'current'), []);
    assert.equal(missingItems(items, {'3': confirmed}, 'changed').length, 5);
    assert.equal(missingItems(items, {'3': confirmed}, null).length, 5);
});

test('evidence context excludes derived screening snapshots but tracks review data', () => {
    const p = {runId: 'run-1', included: 1, taScreening: {screened: 1}, fullTextScreening: {screened: 1}};
    const ctx = evidenceContext(p, {keywords: 'test'}, [{title: 'A'}], {}, [], null);
    assert.deepEqual(ctx.prisma, {runId: 'run-1', included: 1});
    assert.deepEqual(p.taScreening, {screened: 1});
    assert.deepEqual(p.fullTextScreening, {screened: 1});
    assert.equal(ctx.docs[0].title, 'A');
});

test('full-text warnings distinguish pending, deferred and no-longer-eligible decisions', () => {
    const p = base();
    p.fullTextScreening = {screened: 2, included: 1, pending: 3, unsure: 1, inactive: 1, ambiguous: 1};
    const warnings = consistencyWarnings(p, null, []);
    assert.ok(warnings.some(w => w.includes('3 title and abstract inclusions await')));
    assert.ok(warnings.some(w => w.includes('1 full-text decision is unsure')));
    assert.ok(warnings.some(w => w.includes('1 full-text decision no longer matches')));
    assert.ok(warnings.some(w => w.includes('1 full-text candidate has a missing')));
});

test('warning text uses display names, singular grammar and formatted counts', () => {
    const p = base();
    p.identification.not_retrieved = 1203;
    p.identification.databases.springer.warning = 'Retrieval cap reached.';
    p.taScreening = {runId: p.runId, total: 45, screened: 44, included: 43, excluded: 1, unsure: 0,
        reasonCounts: {'No reason given': 1}};
    p.fullTextScreening = {screened: 0, pending: 1, unsure: 0, inactive: 0, ambiguous: 0};
    const warnings = consistencyWarnings(p, null, [], {springer: 'Springer Nature'});
    assert.ok(warnings.includes('Springer Nature: search was partial — Retrieval cap reached. Review retrieval before reporting results.'));
    assert.ok(warnings.some(w => w.startsWith(`${(1203).toLocaleString()} identified records were not retrieved.`)));
    assert.ok(warnings.includes('1 record is awaiting title and abstract screening.'));
    assert.ok(warnings.includes('1 title and abstract exclusion has no recorded reason.'));
    assert.ok(warnings.includes('1 title and abstract inclusion awaits a full-text decision.'));
    p.identification.databases.springer = {status: 'failed', error: 'API unavailable'};
    p.identification.not_retrieved = 1;
    const failed = consistencyWarnings(p, null, [], {springer: 'Springer Nature'});
    assert.ok(failed.includes('Springer Nature: search failed — API unavailable. Review retrieval before reporting results.'));
    assert.ok(failed.includes('1 identified record was not retrieved. Review database coverage before reporting results.'));
});

test('the latest and saved-run views normalize equivalent search evidence', () => {
    const run = {runId: 'run-1', query: 'test', databases: ['hal'],
        filters: {yearFrom: 2020, includeTerms: ['"alpha,beta"', 'gamma'], includeVenues: ['a', 'b']},
        prisma: {included: 1}, docs: [{title: 'A'}], excluded_docs: {}};
    const fromRun = evidenceContextFromRun(run);
    const formState = {keywords: 'test', dbHal: true, dbArxiv: false, dbOpenalex: false,
        yearFrom: '2020', includeTerms: '"alpha,beta",gamma', includeVenues: 'a, b'};
    const fromLatest = evidenceContext({...run.prisma, runId: run.runId},
        formState,
        run.docs, {}, [], null);
    assert.deepEqual(fromLatest, fromRun);
    assert.notDeepEqual(evidenceContext({...run.prisma, runId: run.runId}, {...formState, yearFrom: '2021'},
        run.docs, {}, [], null), fromRun);
    const withManual = {...run, prismaManualEvidence: {manualSteps: [{name: 'Full text', removed: 1}],
        snowballing: {backward: 2}}};
    assert.deepEqual(evidenceContextFromRun(withManual).manualSteps, withManual.prismaManualEvidence.manualSteps);
    assert.deepEqual(evidenceContextFromRun(withManual).snowballing, withManual.prismaManualEvidence.snowballing);
});

test('screening and reviewer drafts describe recorded facts without inventing methods', () => {
    assert.match(screeningEvidence(null), /No title and abstract screening decisions are recorded/);
    assert.match(screeningEvidence(null), /confirm whether screening was performed elsewhere/);
    assert.match(screeningEvidence(base().taScreening), /40\/45 title and abstract screening decisions/);
    assert.match(reviewerEvidence({reviewers: 2}), /lists 2 reviewers/);
    assert.match(reviewerEvidence({reviewers: 1}), /lists 1 reviewer;/);
    assert.match(reviewerEvidence({}), /Confirm the number of reviewers/);
    for (const draft of [reviewerEvidence({}), reviewerEvidence({reviewers: 2}), screeningEvidence(null)]) {
        assert.doesNotMatch(draft, /single reviewer|no manual screening|without independent/i);
    }
});

test('reports incomplete retrieval, pending and unsure decisions, and missing reasons', () => {
    const warnings = consistencyWarnings(base(), null, []);
    assert(warnings.some(w => w.includes('springer') && w.includes('Retrieval cap')));
    assert(warnings.some(w => w.includes('10 identified records were not retrieved')));
    assert(warnings.some(w => w.includes('5 records are awaiting')));
    assert(warnings.some(w => w.includes('2 title and abstract screening decisions are unsure')));
    assert(warnings.some(w => w.includes('2 title and abstract exclusions have no recorded reason')));
    assert(!warnings.some(w => w.includes('do not balance')));
});

test('database date exclusions are not mistaken for incomplete retrieval', () => {
    const p = base();
    p.identification.retrieved = 70;
    p.identification.not_retrieved = 0;
    p.identification.databases.springer = {status: 'complete', removedDateAtDatabase: 30};
    p.included = 55;
    delete p.taScreening;
    assert.deepEqual(consistencyWarnings(p, null, []), []);
});

test('flags inconsistent flow, screening and exclusion reason counts', () => {
    const p = base();
    p.identification.total_found = 101;
    p.included = 44;
    p.taScreening.screened = 41;
    p.taScreening.reasonCounts = {'Relevant': 3};
    const warnings = consistencyWarnings(p, null, []);
    assert(warnings.some(w => w.includes('Identification counts do not balance')));
    assert(warnings.some(w => w.includes('Automated flow counts do not balance')));
    assert(warnings.some(w => w.includes('screening counts do not balance')));
    assert(warnings.some(w => w.includes('reason counts do not match')));
});

test('flags screening totals that differ from records after automated filters', () => {
    const p = base();
    p.taScreening.total = 44;
    const warnings = consistencyWarnings(p, null, []);
    assert(warnings.some(w => w.includes('screening total does not match')));
});

test('does not assume unknown all-years counts are zero', () => {
    const p = base();
    p.identification.databases.springer.removedDateAtDatabase = null;
    p.identification.databases.springer.dateLimitNote = 'All-years count unavailable';
    p.identification.total_found = 999;
    const warnings = consistencyWarnings(p, null, []);
    assert(warnings.some(w => w.includes('All-years count unavailable')));
    assert(!warnings.some(w => w.includes('Identification counts do not balance')));
});

test('checks run identity and invalid other-source and manual-step counts', () => {
    const p = base();
    p.taScreening.runId = 'different-run';
    let warnings = consistencyWarnings(p, null, [{added: 0, removed: 99}]);
    assert(warnings.some(w => w.includes('different run')));
    assert(!warnings.some(w => w.includes('Manual review step')));
    p.taScreening.runId = p.runId;
    warnings = consistencyWarnings(p, {backward: 1, forward: 0, other: 0, excluded: 2}, []);
    assert(warnings.some(w => w.includes('Other-source exclusions exceed')));
    warnings = consistencyWarnings(p, null, [{added: 0, removed: 30}, {added: 0, removed: 99}]);
    assert.equal(warnings.filter(w => w.includes('Manual review step')).length, 1);
    assert(warnings.some(w => w.includes('Manual review step 1')));
});
