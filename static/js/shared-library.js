/**
 * Shared course library: search what other students uploaded, import it, verify it.
 * Everything is built with DOM nodes and textContent, never innerHTML, so shared data
 * can't inject markup.
 */
(function () {
    let searchTimer = null;
    let lastQuery = '';
    let loggedIn = false;

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = String(text);
        return node;
    }

    function badge(text, extraClass) {
        return el('span', 'shared-badge' + (extraClass ? ' ' + extraClass : ''), text);
    }

    function setStatus(text, isError) {
        const status = document.getElementById('sharedSearchStatus');
        status.textContent = text || '';
        status.classList.toggle('error', !!isError);
    }

    function timeAgo(iso) {
        const then = new Date(iso);
        if (isNaN(then)) return '';
        const days = Math.floor((Date.now() - then.getTime()) / 86400000);
        if (days <= 0) return 'Updated today';
        if (days === 1) return 'Updated yesterday';
        return `Updated ${days} days ago`;
    }

    function plural(n, word) {
        if (word === 'faculty') return `${n} faculty`;
        return `${n} ${word}${n === 1 ? '' : 's'}`;
    }

    function slotsTable(slots) {
        const table = el('table', 'shared-slots-table');
        const head = el('tr');
        ['Slot', 'Venue', 'Faculty', 'Seats'].forEach(h => head.appendChild(el('th', null, h)));
        const thead = el('thead');
        thead.appendChild(head);
        table.appendChild(thead);
        const body = el('tbody');
        (slots || []).forEach(s => {
            const row = el('tr');
            row.appendChild(el('td', null, s.slot_code));
            row.appendChild(el('td', null, s.venue));
            row.appendChild(el('td', null, s.faculty));
            row.appendChild(el('td', null, s.available_seats));
            body.appendChild(row);
        });
        table.appendChild(body);
        return table;
    }

    function renderVersion(version, isTop, hasOthers) {
        const box = el('div', 'shared-version');

        const meta = el('div', 'shared-version-meta');
        if (isTop && hasOthers) meta.appendChild(badge('Best match', 'shared-badge-top'));
        if (version.verifications > 0) {
            meta.appendChild(badge(`Verified by ${plural(version.verifications, 'student')}`, 'shared-badge-verified'));
        }
        if (!version.confirmed) meta.appendChild(badge('Not yet confirmed', 'shared-badge-warn'));
        meta.appendChild(el('span', null,
            `${plural(version.faculty_count, 'faculty')} · ${plural(version.slot_count, 'slot')} · ` +
            `${plural(version.uploads, 'upload')} · ${timeAgo(version.updated_at)}`));
        box.appendChild(meta);

        const details = el('div', 'shared-version-meta');
        details.appendChild(el('span', null, `LTPJC ${version.ltpjc}`));
        if (version.course_type) details.appendChild(el('span', null, `· ${version.course_type}`));
        if (version.category) details.appendChild(el('span', null, `· ${version.category}`));
        box.appendChild(details);

        const actions = el('div', 'shared-version-actions');
        const importBtn = el('button', 'btn btn-primary btn-sm');
        importBtn.type = 'button';
        importBtn.appendChild(el('i', 'fas fa-download'));
        importBtn.appendChild(document.createTextNode(' Import to my timetable'));
        importBtn.addEventListener('click', () => importVersion(version, importBtn));
        actions.appendChild(importBtn);

        const verifyBtn = el('button', 'btn btn-secondary btn-sm');
        verifyBtn.type = 'button';
        if (!loggedIn) {
            verifyBtn.textContent = 'Log in to verify';
            verifyBtn.disabled = true;
            verifyBtn.title = 'Only logged-in VIT students can verify course data';
        } else if (version.viewer_uploaded) {
            verifyBtn.textContent = 'You uploaded this';
            verifyBtn.disabled = true;
            verifyBtn.title = 'Your upload already counts. Other students can verify it.';
        } else {
            verifyBtn.textContent = version.viewer_verified ? 'Verified ✓ (undo)' : 'Verify this is correct';
            verifyBtn.addEventListener('click', () => toggleVerify(version, verifyBtn));
        }
        actions.appendChild(verifyBtn);
        box.appendChild(actions);

        const slotsWrap = el('details', 'shared-slots-toggle');
        slotsWrap.appendChild(el('summary', null, 'Show faculty and slots'));
        slotsWrap.appendChild(slotsTable(version.slots));
        box.appendChild(slotsWrap);
        return box;
    }

    function renderResults(data) {
        const results = document.getElementById('sharedSearchResults');
        results.replaceChildren();
        loggedIn = !!data.logged_in;

        if (!data.courses || data.courses.length === 0) {
            setStatus(`No shared data for "${data.query}" yet. Import it yourself and it will show up here for others.`);
            return;
        }
        setStatus(`${plural(data.courses.length, 'course')} found`);

        data.courses.forEach(course => {
            const card = el('div', 'shared-course');
            const title = el('h4', 'shared-course-title');
            title.appendChild(el('strong', null, course.code));
            title.appendChild(document.createTextNode(` ${course.name}`));
            card.appendChild(title);

            const versions = course.versions || [];
            if (versions.length) card.appendChild(renderVersion(versions[0], true, versions.length > 1));
            if (versions.length > 1) {
                const others = el('details', 'shared-other-versions');
                others.appendChild(el('summary', null, `Other versions (${versions.length - 1})`));
                versions.slice(1).forEach(v => others.appendChild(renderVersion(v, false, true)));
                card.appendChild(others);
            }
            results.appendChild(card);
        });
    }

    async function runSearch(query) {
        lastQuery = query;
        if (query.length < 2) {
            document.getElementById('sharedSearchResults').replaceChildren();
            setStatus(query.length ? 'Type at least 2 characters.' : '');
            return;
        }
        setStatus('Searching...');
        try {
            const resp = await fetch(`/api/shared/search?q=${encodeURIComponent(query)}`);
            const data = await resp.json();
            if (query !== lastQuery) return; // A newer search is on its way
            if (!resp.ok) {
                setStatus(data.error || 'Search failed.', true);
                return;
            }
            renderResults(data);
        } catch (e) {
            setStatus('Search failed. Check your connection.', true);
        }
    }

    async function importVersion(version, button) {
        button.disabled = true;
        setStatus(`Importing ${version.code}...`);
        try {
            const resp = await fetch(`/api/shared/${encodeURIComponent(version.id)}/import`, { method: 'POST' });
            const data = await resp.json();
            if (!resp.ok) {
                setStatus(data.error || 'Import failed.', true);
                button.disabled = false;
                return;
            }
            setStatus(`Imported ${data.course_code}: ${plural(data.slots_added, 'faculty/slot option')} added.`);
            if (typeof refreshTimetableGrid === 'function') await refreshTimetableGrid();
        } catch (e) {
            setStatus('Import failed. Check your connection.', true);
        }
        button.disabled = false;
    }

    async function toggleVerify(version, button) {
        button.disabled = true;
        const method = version.viewer_verified ? 'DELETE' : 'POST';
        try {
            const resp = await fetch(`/api/shared/${encodeURIComponent(version.id)}/verify`, { method });
            const data = await resp.json();
            if (!resp.ok) {
                setStatus(data.error || 'Could not save your verification.', true);
                button.disabled = false;
                return;
            }
            await runSearch(lastQuery);
            setStatus(data.viewer_verified ? 'Thanks! Your verification helps other students.' : 'Verification removed.');
        } catch (e) {
            setStatus('Could not save your verification.', true);
            button.disabled = false;
        }
    }

    window.openSharedLibraryModal = function () {
        const modal = document.getElementById('sharedLibraryModal');
        if (!modal) return;
        modal.classList.add('active');
        const input = document.getElementById('sharedSearchInput');
        input.focus();
        if (input.value.trim()) runSearch(input.value.trim());
    };

    window.closeSharedLibraryModal = function () {
        const modal = document.getElementById('sharedLibraryModal');
        if (modal) modal.classList.remove('active');
    };

    document.addEventListener('DOMContentLoaded', () => {
        const input = document.getElementById('sharedSearchInput');
        if (!input) return;
        input.addEventListener('input', () => {
            clearTimeout(searchTimer);
            searchTimer = setTimeout(() => runSearch(input.value.trim()), 300);
        });
    });
})();
