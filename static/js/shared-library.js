/**
 * Shared course library: browse or search what other students uploaded, open a course to compare
 * its versions, import one, verify it.
 * Everything is built with DOM nodes and textContent, never innerHTML, so shared data
 * can't inject markup.
 */
(function () {
    let searchTimer = null;
    let lastQuery = '';
    let loggedIn = false;
    let browsePage = 1;
    let requestId = 0;      // Only the newest search or page is drawn
    let openCourse = null;  // The course shown in the course view
    let selectedId = null;  // Its expanded version (null: all collapsed)

    const $ = id => document.getElementById(id);

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = String(text);
        return node;
    }

    function icon(name) {
        return el('i', `fas ${name}`);
    }

    function badge(text, extraClass) {
        return el('span', 'shared-badge' + (extraClass ? ' ' + extraClass : ''), text);
    }

    function setStatus(text, isError) {
        const status = $(openCourse ? 'sharedCourseStatus' : 'sharedSearchStatus');
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

    function scrollToTop() {
        $('sharedListView').closest('.modal-body').scrollTop = 0;
    }

    // ==================== List view ====================

    function showList() {
        openCourse = null;
        $('sharedCourseView').hidden = true;
        $('sharedListView').hidden = false;
    }

    function setPager(data) {
        const pager = $('sharedPager');
        if (!data || data.pages <= 1) {
            pager.hidden = true;
            return;
        }
        pager.hidden = false;
        $('sharedPageInfo').textContent = `Page ${data.page} of ${data.pages}`;
        $('sharedPrevPage').disabled = data.page <= 1;
        $('sharedNextPage').disabled = data.page >= data.pages;
    }

    /** One clickable row. `summary` has code, name, faculty_count, slot_count, version_count, verifications, confirmed. */
    function courseRow(summary, onOpen) {
        const item = el('li');
        const row = el('button', 'shared-browse-row');
        row.type = 'button';

        const title = el('span', 'shared-browse-title');
        title.appendChild(el('span', 'shared-code-chip', summary.code));
        title.appendChild(el('span', 'shared-browse-name', summary.name));
        row.appendChild(title);

        const meta = el('span', 'shared-browse-meta');
        meta.appendChild(el('span', null,
            `${plural(summary.faculty_count, 'faculty')} · ${plural(summary.slot_count, 'slot')}` +
            (summary.version_count > 1 ? ` · ${plural(summary.version_count, 'version')}` : '')));
        if (summary.verifications > 0) {
            meta.appendChild(badge(`Verified by ${plural(summary.verifications, 'student')}`, 'shared-badge-verified'));
        } else if (!summary.confirmed) {
            meta.appendChild(badge('Not yet confirmed', 'shared-badge-warn'));
        }
        row.appendChild(meta);
        row.appendChild(icon('fa-chevron-right shared-browse-arrow'));
        row.addEventListener('click', onOpen);
        item.appendChild(row);
        return item;
    }

    function renderBrowse(data) {
        const results = $('sharedSearchResults');
        results.replaceChildren();
        browsePage = data.page;
        setPager(data);

        if (!data.courses || data.courses.length === 0) {
            setStatus('No shared courses yet this semester. Import yours and it will show up here for others.');
            return;
        }
        setStatus(`${plural(data.total, 'course')} available this semester`);

        const list = el('ul', 'shared-browse-list');
        data.courses.forEach(c => list.appendChild(courseRow(c, () => openByCode(c.code))));
        results.appendChild(list);
        scrollToTop();
    }

    function renderResults(data) {
        const results = $('sharedSearchResults');
        results.replaceChildren();
        setPager(null);
        loggedIn = !!data.logged_in;

        if (!data.courses || data.courses.length === 0) {
            setStatus(`No shared data for "${data.query}" yet. Import it yourself and it will show up here for others.`);
            return;
        }
        setStatus(`${plural(data.courses.length, 'course')} found`);

        const list = el('ul', 'shared-browse-list');
        data.courses.forEach(course => {
            const best = course.versions[0] || {};
            list.appendChild(courseRow({
                code: course.code, name: course.name, faculty_count: best.faculty_count,
                slot_count: best.slot_count, version_count: course.version_count,
                verifications: best.verifications, confirmed: best.confirmed,
            }, () => showCourse(course)));
        });
        results.appendChild(list);
    }

    async function loadBrowse(page) {
        lastQuery = '';
        const id = ++requestId;
        setStatus('Loading courses...');
        try {
            const resp = await fetch(`/api/shared/browse?page=${encodeURIComponent(page)}`);
            const data = await resp.json();
            if (id !== requestId) return; // A newer search or page is on its way
            if (!resp.ok) {
                setStatus(data.error || 'Could not load the course list.', true);
                return;
            }
            renderBrowse(data);
        } catch (e) {
            if (id === requestId) setStatus('Could not load the course list. Check your connection.', true);
        }
    }

    async function fetchSearch(query) {
        const resp = await fetch(`/api/shared/search?q=${encodeURIComponent(query)}`);
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.error || 'Search failed.');
        loggedIn = !!data.logged_in;
        return data;
    }

    async function runSearch(query) {
        if (query.length === 0) return loadBrowse(browsePage);
        lastQuery = query;
        const id = ++requestId;
        if (query.length < 2) {
            $('sharedSearchResults').replaceChildren();
            setPager(null);
            setStatus('Type at least 2 characters.');
            return;
        }
        setStatus('Searching...');
        try {
            const data = await fetchSearch(query);
            if (id !== requestId) return; // A newer search or page is on its way
            renderResults(data);
        } catch (e) {
            if (id === requestId) setStatus(e.message || 'Search failed. Check your connection.', true);
        }
    }

    // ==================== Course view ====================

    async function openByCode(code) {
        setStatus(`Opening ${code}...`);
        try {
            const data = await fetchSearch(code);
            const course = (data.courses || []).find(c => c.code === code);
            if (!course) {
                setStatus('This course is no longer available.', true);
                return;
            }
            showCourse(course);
        } catch (e) {
            setStatus('Could not open this course. Check your connection.', true);
        }
    }

    function showCourse(course, keepVersionId) {
        openCourse = course;
        const versions = course.versions || [];
        // Rows start collapsed (a single version opens straight away); after verifying, the same one stays open
        selectedId = versions.some(v => v.id === keepVersionId) ? keepVersionId
            : (versions.length === 1 ? versions[0].id : null);

        $('sharedListView').hidden = true;
        $('sharedCourseView').hidden = false;
        $('sharedBackLabel').textContent = lastQuery ? 'Back to results' : 'All courses';
        $('sharedCourseCode').textContent = course.code;
        $('sharedCourseName').textContent = course.name;
        const best = versions[0] || {};
        const credits = best.credits ? `${plural(best.credits, 'credit')} · ` : '';
        $('sharedCourseSummary').textContent =
            `${credits}${plural(versions.length, 'version')} shared · up to ${plural(Math.max(0, ...versions.map(v => v.faculty_count)), 'faculty')}`;
        setStatus('');
        renderVersionList();
        scrollToTop();
    }

    function versionLabel(index) {
        return `Version ${index + 1}`;
    }

    function renderVersionList() {
        const list = $('sharedVersionList');
        list.replaceChildren();
        const versions = openCourse.versions || [];
        versions.forEach((v, i) => {
            const open = v.id === selectedId;
            const item = el('li', 'shared-version-item' + (open ? ' open' : ''));
            const row = el('button', 'shared-version-row');
            row.type = 'button';
            row.setAttribute('aria-expanded', String(open));

            const name = el('span', 'shared-version-name');
            name.appendChild(el('strong', null, versionLabel(i)));
            if (i === 0 && versions.length > 1) name.appendChild(badge('Best match', 'shared-badge-top'));
            row.appendChild(name);

            row.appendChild(el('span', 'shared-version-stats',
                `${plural(v.faculty_count, 'faculty')} · ${plural(v.slot_count, 'slot')}`));

            const trust = el('span', 'shared-version-trust');
            trust.appendChild(el('span', null, plural(v.uploads, 'upload')));
            if (v.verifications > 0) {
                trust.appendChild(badge(`Verified by ${v.verifications}`, 'shared-badge-verified'));
            } else if (!v.confirmed) {
                trust.appendChild(badge('Not yet confirmed', 'shared-badge-warn'));
            }
            row.appendChild(trust);
            row.appendChild(el('span', 'shared-version-date', timeAgo(v.updated_at)));
            row.appendChild(icon(open ? 'fa-chevron-up shared-version-arrow' : 'fa-chevron-down shared-version-arrow'));

            row.addEventListener('click', () => {
                selectedId = open ? null : v.id;
                renderVersionList();
            });
            item.appendChild(row);
            if (open) item.appendChild(versionDetail(v));
            list.appendChild(item);
        });
    }

    function verifyButton(version) {
        const btn = el('button', 'btn btn-secondary');
        btn.type = 'button';
        if (!loggedIn) {
            btn.appendChild(icon('fa-lock'));
            btn.appendChild(document.createTextNode(' Log in to verify'));
            btn.disabled = true;
            btn.title = 'Only logged-in VIT students can verify course data';
        } else if (version.viewer_uploaded) {
            btn.appendChild(icon('fa-user-check'));
            btn.appendChild(document.createTextNode(' You uploaded this'));
            btn.disabled = true;
            btn.title = 'Your upload already counts. Other students can verify it.';
        } else {
            btn.appendChild(icon(version.viewer_verified ? 'fa-undo' : 'fa-check'));
            btn.appendChild(document.createTextNode(version.viewer_verified ? ' Undo my verification' : ' Verify this is correct'));
            btn.addEventListener('click', () => toggleVerify(version, btn));
        }
        return btn;
    }

    /** Every row exactly as the student uploaded it, in their order. */
    function facultyTable(slots) {
        const wrap = el('div', 'shared-table-wrap');
        const table = el('table', 'shared-slots-table');
        const head = el('tr');
        ['#', 'Faculty', 'Slot', 'Venue', 'Seats left'].forEach(h => head.appendChild(el('th', null, h)));
        const thead = el('thead');
        thead.appendChild(head);
        table.appendChild(thead);

        const body = el('tbody');
        (slots || []).forEach((s, i) => {
            const tr = el('tr');
            tr.appendChild(el('td', 'shared-row-num', i + 1));
            tr.appendChild(el('td', 'shared-faculty-cell', s.faculty));
            tr.appendChild(el('td', 'shared-slot-code', s.slot_code));
            tr.appendChild(el('td', null, s.venue));
            const seats = Number(s.available_seats);
            tr.appendChild(el('td', seats > 0 ? 'shared-seats' : 'shared-seats shared-seats-full',
                seats > 0 ? seats : 'Full'));
            body.appendChild(tr);
        });
        table.appendChild(body);
        wrap.appendChild(table);
        return wrap;
    }

    function versionDetail(version) {
        const panel = el('div', 'shared-version-detail');

        const head = el('div', 'shared-detail-head');
        const info = el('dl', 'shared-detail-info');
        [['Faculty', version.faculty_count], ['Slots', version.slot_count], ['Uploads', version.uploads],
         ['Verified by', plural(version.verifications, 'student')], ['Last updated', timeAgo(version.updated_at).replace('Updated ', '')]]
            .forEach(([label, value]) => {
                const item = el('div');
                item.appendChild(el('dt', null, label));
                item.appendChild(el('dd', null, value));
                info.appendChild(item);
            });
        head.appendChild(info);

        const actions = el('div', 'shared-detail-actions');
        const importBtn = el('button', 'btn btn-primary');
        importBtn.type = 'button';
        importBtn.appendChild(icon('fa-download'));
        importBtn.appendChild(document.createTextNode(' Import this version'));
        importBtn.addEventListener('click', () => importVersion(version, importBtn));
        actions.appendChild(importBtn);
        actions.appendChild(verifyButton(version));
        head.appendChild(actions);
        panel.appendChild(head);

        panel.appendChild(facultyTable(version.slots));
        return panel;
    }

    /** Success box under the version's buttons, asking the student to verify what they imported. */
    function showImportNotice(version, data, panel) {
        if (!panel) return;
        panel.querySelector('.shared-import-notice')?.remove();
        const notice = el('div', 'shared-import-notice');
        notice.setAttribute('role', 'status');

        const title = el('p', 'shared-import-title');
        title.appendChild(icon('fa-check-circle'));
        title.appendChild(document.createTextNode(` ${data.course_code} imported to your timetable`));
        notice.appendChild(title);
        notice.appendChild(el('p', 'shared-import-text', data.slots_added > 0
            ? `${plural(data.slots_added, 'faculty/slot option')} added. Open Register/View A Course to pick your faculty.`
            : 'You already had all of these faculty and slots, so nothing new was added.'));

        const ask = el('div', 'shared-import-verify');
        if (version.viewer_uploaded) {
            ask.appendChild(el('span', null, 'You uploaded this version, so it already counts for other students.'));
        } else if (version.viewer_verified) {
            ask.appendChild(el('span', null, 'You have already verified this version. Thank you!'));
        } else if (!loggedIn) {
            ask.appendChild(el('span', null,
                'Does this list match what you see on VTOP? Log in with your VIT email to verify it for other students.'));
            const login = el('a', 'btn btn-secondary btn-sm', 'Log in to verify');
            login.href = '/auth/login';
            ask.appendChild(login);
        } else {
            ask.appendChild(el('span', null,
                'Does this list match what you see on VTOP? Verifying it helps other students find correct data.'));
            const verify = el('button', 'btn btn-success btn-sm');
            verify.type = 'button';
            verify.appendChild(icon('fa-check'));
            verify.appendChild(document.createTextNode(' Yes, verify it'));
            verify.addEventListener('click', () => toggleVerify(version, verify));
            ask.appendChild(verify);
        }
        notice.appendChild(ask);

        panel.querySelector('.shared-detail-head').after(notice);
        notice.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
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
            setStatus('');
            showImportNotice(version, data, button.closest('.shared-version-detail'));
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
            // Redraw with fresh counts (the order may change), keeping this version selected
            const fresh = (await fetchSearch(openCourse.code)).courses.find(c => c.code === openCourse.code);
            if (fresh) showCourse(fresh, version.id);
            setStatus(data.viewer_verified ? 'Thanks! Your verification helps other students.' : 'Verification removed.');
        } catch (e) {
            setStatus('Could not save your verification.', true);
            button.disabled = false;
        }
    }

    // ==================== Opening and closing ====================

    window.openSharedLibraryModal = function () {
        const modal = $('sharedLibraryModal');
        if (!modal) return;
        modal.classList.add('active');
        showList();
        const input = $('sharedSearchInput');
        input.focus();
        runSearch(input.value.trim());
    };

    window.closeSharedLibraryModal = function () {
        const modal = $('sharedLibraryModal');
        if (modal) modal.classList.remove('active');
    };

    // "/" opens the search from anywhere, unless the user is already typing somewhere
    document.addEventListener('keydown', (e) => {
        if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey) return;
        const target = e.target;
        if (target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return;
        if (document.querySelector('.modal.active')) return;
        e.preventDefault();
        window.openSharedLibraryModal();
    });

    document.addEventListener('DOMContentLoaded', () => {
        const input = $('sharedSearchInput');
        if (!input) return;
        input.addEventListener('input', () => {
            clearTimeout(searchTimer);
            searchTimer = setTimeout(() => runSearch(input.value.trim()), 300);
        });
        $('sharedPrevPage').addEventListener('click', () => loadBrowse(browsePage - 1));
        $('sharedNextPage').addEventListener('click', () => loadBrowse(browsePage + 1));
        $('sharedBackBtn').addEventListener('click', () => {
            showList();
            input.focus();
        });
    });
})();
