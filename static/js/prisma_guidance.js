(function (root) {
    'use strict';

    function isConfirmed(saved, id, revision) {
        return !!(revision && saved[id] && saved[id].checked &&
            saved[id].confirmedRevision === revision);
    }

    function missingItems(items, saved, revision) {
        return items.filter(function (item) {
            return item.id && !isConfirmed(saved, item.id, revision);
        });
    }

    function evidenceContext(prisma, formState, docs, excludedDocs, manualSteps, snowballing) {
        const {taScreening, fullTextScreening, ...searchPrisma} = prisma;
        const relevantState = {keywords: String(formState.keywords || '').trim()};
        ['dbHal', 'dbArxiv', 'dbIeee', 'dbScopus', 'dbInspec', 'dbSciencedirect', 'dbSpringer',
            'dbPubmed', 'dbAcm', 'dbOpenalex', 'dbEbsco', 'dbWos'].forEach(key => {
            relevantState[key] = formState[key] === true ||
                (formState[key] == null && ['dbHal', 'dbArxiv', 'dbOpenalex'].includes(key));
        });
        ['yearFrom', 'yearTo'].forEach(key => { relevantState[key] = String(formState[key] || '').trim(); });
        ['includeTerms', 'excludeTerms', 'includeVenues', 'excludeVenues'].forEach(key => {
            const value = formState[key];
            const terms = key === 'includeTerms' || key === 'excludeTerms';
            const parts = Array.isArray(value) ? value : terms
                ? (String(value || '').match(/"[^"]*"|[^,\n\r]+/g) || [])
                : String(value || '').split(',');
            relevantState[key] = parts.map(part => String(part).trim())
                .filter(part => terms ? part.replace(/["']/g, '').trim() : part);
        });
        ['docTypes', 'languages'].forEach(key => {
            relevantState[key] = Array.isArray(formState[key]) ? [...formState[key]].sort() : [];
        });
        relevantState.includeUnknownLanguage = formState.includeUnknownLanguage !== false;
        relevantState.includeTermsMode = formState.includeTermsMode || 'all';
        relevantState.excludeTermsMode = formState.excludeTermsMode || 'any';
        relevantState.includeVenuesMode = formState.includeVenuesMode || 'any';
        relevantState.excludeVenuesMode = formState.excludeVenuesMode || 'any';
        return {prisma: searchPrisma, formState: relevantState, docs, excludedDocs, manualSteps, snowballing};
    }

    function formStateFromRun(run) {
        const filters = run.filters || {};
        const state = {keywords: run.query || ''};
        const dbKeys = {hal:'dbHal', arxiv:'dbArxiv', ieee:'dbIeee', scopus:'dbScopus',
            inspec:'dbInspec', sciencedirect:'dbSciencedirect', springer:'dbSpringer',
            pubmed:'dbPubmed', acm:'dbAcm', openalex:'dbOpenalex', ebsco:'dbEbsco', wos:'dbWos'};
        Object.entries(dbKeys).forEach(([db, key]) => { state[key] = (run.databases || []).includes(db); });
        ['yearFrom', 'yearTo', 'docTypes', 'languages', 'includeUnknownLanguage', 'includeTermsMode',
            'excludeTermsMode', 'includeVenuesMode', 'excludeVenuesMode'].forEach(key => {
            if (filters[key] != null) state[key] = filters[key];
        });
        ['includeTerms', 'excludeTerms', 'includeVenues', 'excludeVenues'].forEach(key => {
            const value = filters[key];
            state[key] = Array.isArray(value) ? value : (value || '');
        });
        return state;
    }

    function runStorageKey(name, runId, searchId) {
        return name + ':' + (searchId || 'adhoc') + ':' + runId;
    }

    function evidenceContextFromRun(run) {
        const prisma = {...(run.prisma || {}), runId: run.runId};
        const evidence = run.prismaManualEvidence || {};
        const steps = evidence.manualSteps || [];
        const snowballing = evidence.snowballing || null;
        return evidenceContext(prisma, formStateFromRun(run), run.docs || [], run.excluded_docs || {}, steps, snowballing);
    }

    function screeningEvidence(ta) {
        if (!ta || !(ta.total > 0)) {
            return 'No title and abstract screening decisions are recorded here; confirm whether screening was performed elsewhere.';
        }
        const pct = Math.round((ta.screened / ta.total) * 100);
        return `RSP records ${ta.screened.toLocaleString()}/${ta.total.toLocaleString()} title and abstract screening decisions (${pct}%) — ${ta.included} included, ${ta.excluded} excluded, ${ta.unsure} unsure/deferred.`;
    }

    function reviewerEvidence(proto) {
        if (!proto.reviewers) return 'Confirm the number of reviewers and whether they screened independently.';
        return `The Review Protocol lists ${proto.reviewers} reviewer${proto.reviewers === 1 ? '' : 's'}; confirm who screened each record and whether they worked independently.`;
    }

    function consistencyWarnings(prisma, snowballing, manualSteps, dbLabels = {}) {
        const warnings = [];
        const ident = prisma.identification || {};
        const dbs = ident.databases || {};
        const count = value => Number.isInteger(value) && value >= 0;
        const has = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key);

        Object.entries(dbs).forEach(function ([name, db]) {
            if (!db) return;
            const label = dbLabels[name] || name;
            if (db.status === 'partial' || db.status === 'failed' || db.error || db.warning) {
                const status = db.status === 'partial' ? 'search was partial' : db.status === 'failed' ? 'search failed'
                    : db.error ? 'search reported an error' : 'search reported a warning';
                const detail = String(db.error || db.warning || db.message || '').trim();
                warnings.push(`${label}: ${status}${detail ? ' — ' + detail : ''}${detail && /[.!?]$/.test(detail) ? '' : '.'} Review retrieval before reporting results.`);
            }
            if (db.dateLimitWarning) warnings.push(`${label}: ${db.dateLimitWarning}`);
            if (db.dateLimitNote) warnings.push(`${label}: ${db.dateLimitNote}`);
        });
        if (count(ident.not_retrieved) && ident.not_retrieved > 0) {
            const n = ident.not_retrieved;
            warnings.push(`${n.toLocaleString()} identified record${n === 1 ? ' was' : 's were'} not retrieved. Review database coverage before reporting results.`);
        }

        const breakdown = ['total_found', 'retrieved', 'removed_date_at_database', 'not_retrieved'];
        const dateCountsKnown = Object.values(dbs).every(db =>
            !db || !has(db, 'removedDateAtDatabase') || count(db.removedDateAtDatabase));
        if (breakdown.every(key => count(ident[key])) && dateCountsKnown &&
            ident.total_found - ident.removed_date_at_database - ident.not_retrieved !== ident.retrieved) {
            warnings.push('Identification counts do not balance: identified minus date-limited and not retrieved should equal retrieved.');
        }

        const screening = prisma.screening;
        const eligibility = prisma.eligibility;
        if (screening && eligibility && count(ident.retrieved) && count(prisma.included)) {
            const values = Object.values(screening).concat(Object.values(eligibility));
            if (values.every(count)) {
                const excluded = values.reduce((sum, value) => sum + value, 0);
                if (ident.retrieved - excluded !== prisma.included) {
                    warnings.push('Automated flow counts do not balance: retrieved minus duplicate and filter exclusions should equal the records remaining after filters.');
                }
            } else {
                warnings.push('Some automated exclusion counts are invalid; check the flow data.');
            }
        }

        const ta = prisma.taScreening;
        const differentRun = ta && prisma.runId && ta.runId && prisma.runId !== ta.runId;
        if (differentRun) {
            warnings.push('Title and abstract screening data belongs to a different run. Refresh the screening data before reporting it.');
        } else if (ta) {
            const keys = ['total', 'screened', 'included', 'excluded', 'unsure'];
            if (keys.every(key => count(ta[key]))) {
                if (count(prisma.included) && ta.total !== prisma.included) {
                    warnings.push('Title and abstract screening total does not match the records remaining after automated filters; check that the same run is being used.');
                }
                if (ta.included + ta.excluded + ta.unsure !== ta.screened || ta.screened > ta.total) {
                    warnings.push('Title and abstract screening counts do not balance; check the recorded decisions.');
                } else {
                    if (ta.total > ta.screened) {
                        const pending = ta.total - ta.screened;
                        warnings.push(`${pending.toLocaleString()} record${pending === 1 ? ' is' : 's are'} awaiting title and abstract screening.`);
                    }
                    if (ta.unsure > 0) warnings.push(`${ta.unsure.toLocaleString()} title and abstract screening decision${ta.unsure === 1 ? ' is' : 's are'} unsure or deferred.`);
                }
                if (ta.excluded > 0) {
                    if (!ta.reasonCounts || typeof ta.reasonCounts !== 'object') {
                        warnings.push('Title and abstract exclusion reasons are unavailable; check the screening decisions.');
                    } else {
                        const reasons = Object.entries(ta.reasonCounts);
                        if (!reasons.every(([, n]) => count(n)) ||
                            reasons.reduce((sum, [, n]) => sum + (count(n) ? n : 0), 0) !== ta.excluded) {
                            warnings.push('Title and abstract exclusion reason counts do not match the excluded total.');
                        }
                        const missing = reasons.reduce((sum, [reason, n]) =>
                            sum + ((!reason.trim() || reason.trim().toLowerCase() === 'no reason given') && count(n) ? n : 0), 0);
                        if (missing) warnings.push(`${missing.toLocaleString()} title and abstract exclusion${missing === 1 ? ' has' : 's have'} no recorded reason.`);
                    }
                }
            } else {
                warnings.push('Title and abstract screening counts are missing or invalid; check the screening decisions.');
            }
        }

        const ft = prisma.fullTextScreening;
        if (ft) {
            if (ft.pending > 0) warnings.push(`${ft.pending.toLocaleString()} title and abstract inclusion${ft.pending === 1 ? ' awaits' : 's await'} a full-text decision.`);
            if (ft.unsure > 0) warnings.push(`${ft.unsure.toLocaleString()} full-text decision${ft.unsure === 1 ? ' is' : 's are'} unsure or deferred.`);
            if (ft.inactive > 0) warnings.push(`${ft.inactive.toLocaleString()} full-text decision${ft.inactive === 1 ? ' no longer matches' : 's no longer match'} a uniquely included title and abstract record and ${ft.inactive === 1 ? 'is' : 'are'} not counted.`);
            if (ft.ambiguous > 0) warnings.push(`${ft.ambiguous.toLocaleString()} full-text candidate${ft.ambiguous === 1 ? ' has' : 's have'} a missing or duplicate identity and cannot be screened.`);
            if (snowballing && (ft.screened > 0 || ft.dualReview)) warnings.push('Other-source counts are not assessed by the full-text screening workflow.');
        }

        let otherIncluded = 0;
        if (snowballing) {
            const other = ['backward', 'forward', 'other', 'excluded'].map(key => snowballing[key] || 0);
            if (!other.every(count) || other[3] > other[0] + other[1] + other[2]) {
                warnings.push('Other-source exclusions exceed the records identified or contain invalid counts.');
                otherIncluded = null;
            } else {
                otherIncluded = other[0] + other[1] + other[2] - other[3];
            }
        }
        let remaining = ft && (ft.screened > 0 || ft.dualReview) ? ft.included
            : !differentRun && ta && count(ta.total) && ta.total > 0 && count(ta.included) ? ta.included : prisma.included;
        if (count(remaining) && otherIncluded !== null && !differentRun) {
            remaining += otherIncluded;
            (manualSteps || []).forEach(function (step, index) {
                if (remaining === null) return;
                const added = step.added || 0, removed = step.removed || 0;
                if (!count(added) || !count(removed) || removed > remaining + added) {
                    warnings.push(`Manual review step ${index + 1} has invalid counts or removes more records than available.`);
                    remaining = null;
                } else {
                    remaining += added - removed;
                }
            });
        }
        return warnings;
    }

    const api = {isConfirmed, missingItems, evidenceContext, formStateFromRun, runStorageKey,
        evidenceContextFromRun, screeningEvidence, reviewerEvidence, consistencyWarnings};
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    if (root) root.PrismaGuidance = api;
})(typeof window !== 'undefined' ? window : null);
