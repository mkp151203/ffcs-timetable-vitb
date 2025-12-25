/**
 * FFCS Timetable Builder - Main Application JavaScript
 */

// Current state
let selectedCourse = null;
let selectedSlotId = null;
let selectedCells = [];  // For manual cell selection

// DOM Ready
document.addEventListener('DOMContentLoaded', function () {
    initializeApp();
});

function initializeApp() {
    // Set up event listeners
    const searchInput = document.getElementById('courseSearch');
    const searchBtn = document.getElementById('searchBtn');

    if (searchInput) {
        searchInput.addEventListener('keypress', function (e) {
            if (e.key === 'Enter') {
                searchCourses();
            }
        });
    }

    if (searchBtn) {
        searchBtn.addEventListener('click', searchCourses);
    }

    // Load registered courses list
    loadRegisteredCoursesList();

    // Check for tab parameter (e.g. from View Saved button)
    const urlParams = new URLSearchParams(window.location.search);
    if (urlParams.get('tab') === 'saved') {
        if (typeof switchGenerateTab === 'function' && document.getElementById('savedView')) {
            switchGenerateTab('saved');
        }
    }
}

// ==================== Course Search ====================

async function searchCourses() {
    const query = document.getElementById('courseSearch').value.trim();
    const resultsDiv = document.getElementById('searchResults');

    if (!query) {
        resultsDiv.innerHTML = '<p class="empty-message">Enter a course code to search.</p>';
        return;
    }

    resultsDiv.innerHTML = '<div class="loading-spinner"></div>';

    try {
        const response = await fetch(`/api/courses/search?q=${encodeURIComponent(query)}`);
        const data = await response.json();

        if (data.courses.length === 0) {
            resultsDiv.innerHTML = '<p class="empty-message">No courses found.</p>';
            return;
        }

        resultsDiv.innerHTML = data.courses.map(course => `
            <div class="search-result-item" onclick="selectCourse('${course.id}')">
                <div class="course-code">${course.code}</div>
                <div class="course-name">${course.name}</div>
                <div class="course-credits">Credits: ${course.c} (${course.ltpjc})</div>
            </div>
        `).join('');

    } catch (error) {
        console.error('Search error:', error);
        resultsDiv.innerHTML = '<p class="empty-message">Error searching courses.</p>';
    }
}

// ==================== Faculty Selection Modal ====================

async function selectCourse(courseId) {
    try {
        const response = await fetch(`/api/courses/${courseId}/slots`);
        const data = await response.json();

        selectedCourse = data.course;
        showFacultyModal(data.course, data.slots);

    } catch (error) {
        console.error('Error loading slots:', error);
        alert('Error loading course slots.');
    }
}

function showFacultyModal(course, slots) {
    const modal = document.getElementById('facultyModal');
    const courseInfoBody = document.getElementById('courseInfoBody');
    const slotsTableBody = document.getElementById('slotsTableBody');

    // Populate course info
    courseInfoBody.innerHTML = `
        <tr>
            <td>${course.code} - ${course.name}</td>
            <td>${course.ltpjc}</td>
            <td>${course.course_type}</td>
            <td>${course.category}</td>
        </tr>
    `;

    // Populate slots table
    if (slots.length === 0) {
        slotsTableBody.innerHTML = `
            <tr>
                <td colspan="5" class="no-results">
                    <i class="fas fa-inbox"></i>
                    <p>No slots available for this course.</p>
                </td>
            </tr>
        `;
    } else {
        slotsTableBody.innerHTML = `
            <tr>
                <td colspan="5" class="slots-section-header">Slots</td>
            </tr>
        ` + slots.map(slot => {
            const isFull = slot.is_full;
            const seatsClass = slot.available_seats < 20 ? 'low' : '';

            const isCurrentSlot = currentEditingRegistrationId && String(slot.id) === String(currentEditingSlotId);
            const rowClass = isFull ? 'clash-row' : (isCurrentSlot ? 'current-slot-row' : '');

            // Inline style for current slot if no CSS class yet, or we can add one. 
            // Let's rely on adding a class 'current-slot-row' and maybe a small inline style for safety if allowed,
            // or better yet, inject the style logic.
            // Let's stick to adding a class and I'll add the CSS next. 

            return `
                <tr class="${rowClass}" data-slot-id="${slot.id}" style="${isCurrentSlot ? 'background-color: #d1e7dd;' : ''}">
                    <td class="slot-code">
                        ${slot.slot_code}
                        ${isCurrentSlot ? '<span class="badge bg-success" style="font-size: 0.7em; margin-left: 5px;">Current</span>' : ''}
                    </td>
                    <td class="venue">${slot.venue}</td>
                    <td class="faculty-name">${slot.faculty_name || 'TBA'}</td>
                    <td class="clash-status" id="clash-${slot.id}"></td>
                    <td>
                        ${isFull ?
                    '<span class="full-label">Full</span>' :
                    (isCurrentSlot ?
                        '<span class="text-success"><i class="fas fa-check-circle"></i> Registered</span>' :
                        `<input type="radio" name="slotSelection" value="${slot.id}" onchange="selectSlot('${slot.id}')">
                         <span class="available-seats ${seatsClass}">${slot.available_seats}</span>`
                    )
                }
                    </td>
                </tr>
            `;
        }).join('');

        // Check for clashes
        checkClashesForSlots(slots);
    }

    modal.classList.add('active');
    selectedSlotId = null;
}

async function checkClashesForSlots(slots) {
    if (slots.length === 0) return;

    try {
        const slotIds = slots.map(s => s.id);
        const response = await fetch('/api/registration/check-clash-batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                slot_ids: slotIds,
                exclude_reg_id: currentEditingRegistrationId
            })
        });

        const data = await response.json();
        const results = data.results || {};

        // Update UI for each slot
        slots.forEach(slot => {
            const slotResult = results[slot.id];

            const clashCell = document.getElementById(`clash-${slot.id}`);
            const row = clashCell ? clashCell.closest('tr') : null;

            // Clear previous state first just in case
            if (clashCell) clashCell.textContent = '';
            // Don't remove class yet, might interfere with other logic? 
            // Actually usually we just append, but here we process all.
            // If we re-open modal, it rebuilds HTML anyway.

            if (slotResult && slotResult.has_clash) {
                if (clashCell) {
                    clashCell.textContent = slotResult.clashing_slots.map(c => c.course_code).join(', ');
                }
                if (row) row.classList.add('clash-row');
            } else if (currentEditingRegistrationId && row) {
                // Logic for current editing slot visual is handled in HTML generation mostly,
                // but we can ensure it's not marked as clash
                row.classList.remove('clash-row');
            }
        });

    } catch (error) {
        console.error('Error checking clashes batch:', error);
    }
}

function selectSlot(slotId) {
    selectedSlotId = slotId;

    // Highlight selected row
    document.querySelectorAll('#slotsTableBody tr').forEach(row => {
        row.classList.remove('selected');
    });
    document.querySelector(`tr[data-slot-id="${slotId}"]`)?.classList.add('selected');
}

function closeFacultyModal() {
    document.getElementById('facultyModal').classList.remove('active');
    selectedCourse = null;
    selectedSlotId = null;
    // Note: currentEditingRegistrationId is global and might need reset here too if not shared with others?
    // Actually closeFacultyModal is called after registration success or just closing the modal.
    // Let's reset the edit states here too to be safe.
    currentEditingRegistrationId = null;
    currentEditingSlotId = null;
}

async function registerSlot(slotId) {
    // If called from button click (which passes event or nothing), we might need to get selectedSlotId
    // But duplicate logic for 'selectSlot' global selection vs 'slotId' param needs unification.
    // The previous API used 'registerSelectedSlot' processing global 'selectedSlotId'.
    // The new API uses 'registerSlot(slotId)'.

    // Let's unify: use the passed slotId if available, else use global selectedSlotId.
    const finalSlotId = slotId || selectedSlotId;

    if (!finalSlotId) {
        alert('Please select a slot first.');
        return;
    }

    setRegisterButtonLoading(true);

    try {
        if (currentEditingRegistrationId) {
            await updateRegistration(finalSlotId, currentEditingRegistrationId);
        } else {
            await createNewRegistration(finalSlotId);
        }
    } catch (error) {
        // Only reset if error, success reloads page
        setRegisterButtonLoading(false);
        console.error("Registration flow error:", error);
    }
}

function setRegisterButtonLoading(isLoading) {
    // The Register button in the modal footer
    const btn = document.querySelector('#facultyModal .modal-footer .btn-primary');
    if (!btn) return;

    if (isLoading) {
        btn.disabled = true;
        btn.dataset.originalText = btn.innerHTML;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Processing...';
    } else {
        btn.disabled = false;
        btn.innerHTML = btn.dataset.originalText || 'Register';
    }
}

async function createNewRegistration(slotId) {
    // try-catch handled by wrapper registerSlot
    const response = await fetch('/api/registration/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ slot_id: slotId })
    });

    const data = await response.json();

    if (response.ok) {
        closeFacultyModal();
        // Reload to show updated timetable grid
        location.reload();
    } else {
        alert(data.error || 'Registration failed.');
        throw new Error(data.error || 'Registration failed.'); // Propagate error
    }
}

async function updateRegistration(slotId, regId) {
    // try-catch handled by wrapper registerSlot
    const response = await fetch(`/api/registration/${regId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ slot_id: slotId })
    });

    const data = await response.json();

    if (response.ok) {
        closeFacultyModal();
        // Reload to show updated timetable grid
        location.reload();
    } else {
        alert(data.error || 'Update failed');
        throw new Error(data.error || 'Update failed');
    }
}

// Alias for button click
function registerSelectedSlot() {
    registerSlot(selectedSlotId);
}

// ==================== Slot Click Handler ====================

function handleSlotClick(cell) {
    const isRegistered = cell.classList.contains('registered');
    const slotCode = cell.dataset.slot;

    if (isRegistered) {
        // Get registration ID and confirm deletion
        const regId = cell.dataset.registrationId;
        if (regId && confirm('Remove this course from your timetable?')) {
            deleteRegistration(regId);
        }
    } else if (slotCode) {
        // Toggle cell selection for manual entry
        toggleCellSelection(cell, slotCode);
    }
}

function toggleCellSelection(cell, slotCode) {
    const index = selectedCells.indexOf(slotCode);

    if (index > -1) {
        // Deselect
        selectedCells.splice(index, 1);
        cell.classList.remove('selected');
    } else {
        // Select
        selectedCells.push(slotCode);
        cell.classList.add('selected');
    }

    // Update the selected cells count display
    updateSelectedCellsDisplay();
}

function updateSelectedCellsDisplay() {
    // Update sidebar button
    const btn = document.querySelector('.manual-entry-panel .btn-primary');
    if (btn) {
        if (selectedCells.length > 0) {
            btn.innerHTML = `<i class="fas fa-edit"></i> Add Course (${selectedCells.length} slots)`;
        } else {
            btn.innerHTML = `<i class="fas fa-edit"></i> Add Course Manually`;
        }
    }

    // Update timetable selection bar
    const selectionBar = document.getElementById('cellSelectionBar');
    const countSpan = document.getElementById('selectedCellsCount');

    if (selectionBar && countSpan) {
        if (selectedCells.length > 0) {
            selectionBar.style.display = 'flex';
            countSpan.textContent = `${selectedCells.length} slot${selectedCells.length > 1 ? 's' : ''} selected`;
        } else {
            selectionBar.style.display = 'none';
        }
    }
}

function clearCellSelection() {
    selectedCells.forEach(slotCode => {
        const cell = document.querySelector(`[data-slot="${slotCode}"]`);
        if (cell) {
            cell.classList.remove('selected');
        }
    });
    selectedCells = [];
    updateSelectedCellsDisplay();
}

async function deleteRegistration(regId) {
    try {
        const response = await fetch(`/api/registration/${regId}`, {
            method: 'DELETE'
        });

        if (response.ok) {
            location.reload();
        } else {
            alert('Error removing course.');
        }
    } catch (error) {
        console.error('Delete error:', error);
        alert('Error removing course.');
    }
}

// ==================== Registered Courses ====================

async function loadRegisteredCoursesList() {
    const listDiv = document.getElementById('registeredCoursesList');

    // Skip if element doesn't exist (e.g., on generate page)
    if (!listDiv) return;

    try {
        const response = await fetch('/api/registration/');
        const data = await response.json();

        if (data.registrations.length === 0) {
            listDiv.innerHTML = '<p class="empty-message">No courses registered yet.</p>';
            return;
        }

        // Build color legend from OCCUPIED_SLOTS
        const courseColors = {};
        if (window.OCCUPIED_SLOTS) {
            Object.values(window.OCCUPIED_SLOTS).forEach(slot => {
                if (slot.course_code && slot.color && !courseColors[slot.course_code]) {
                    courseColors[slot.course_code] = slot.color;
                }
            });
        }

        const colorLegend = Object.keys(courseColors).length > 0 ? `
            <div class="color-legend">
                <span class="legend-label">Color Legend:</span>
                ${Object.entries(courseColors).map(([code, color]) => `
                    <span class="legend-item">
                        <span class="legend-color" style="background-color: ${color};"></span>
                        ${code}
                    </span>
                `).join('')}
            </div>
        ` : '';

        listDiv.innerHTML = `
            <div class="bulk-actions-bar">
                <label class="select-all-reg-label">
                    <input type="checkbox" id="selectAllRegistrations" onchange="toggleAllRegistrations()">
                    Select All
                </label>
                <button type="button" class="btn btn-danger btn-sm" id="bulkDeleteBtn" onclick="bulkDeleteRegistrations()" disabled>
                    <i class="fas fa-trash"></i>
                </button>
            </div>
            <table class="registered-courses-table">
                <thead>
                    <tr>
                        <th></th>
                        <th></th>
                        <th>Course Code</th>
                        <th>Course Name</th>
                        <th>Slot</th>
                        <th>Venue</th>
                        <th>Faculty</th>
                        <th>Credits</th>
                        <th></th>
                    </tr>
                </thead>
                <tbody>
                    ${data.registrations.map(reg => {
            const courseCode = reg.slot?.course?.code || 'N/A';
            const color = courseColors[courseCode] || '#90EE90';
            return `
                        <tr>
                            <td><input type="checkbox" class="reg-checkbox" value="${reg.id}" onchange="updateBulkDeleteState()"></td>
                            <td><span class="row-color-indicator" style="background-color: ${color};"></span></td>
                            <td><strong>${courseCode}</strong></td>
                            <td>${reg.slot?.course?.name || 'N/A'}</td>
                            <td>${reg.slot?.slot_code || 'N/A'}</td>
                            <td>${reg.slot?.venue || 'N/A'}</td>
                            <td>${reg.slot?.faculty_name || 'TBA'}</td>
                            <td>${reg.slot?.course?.c || 0}</td>
                            <td>
                                <button class="edit-btn" onclick="openEditRegistrationModal('${reg.slot?.course?.id}', '${reg.id}', '${reg.slot?.id}')" title="Edit Slot/Faculty">
                                    <i class="fas fa-edit"></i>
                                </button>
                                <button class="delete-btn" onclick="deleteRegistration('${reg.id}')" title="Remove">
                                    <i class="fas fa-trash"></i>
                                </button>
                            </td>
                        </tr>
                    `}).join('')}
                </tbody>
            </table>
            <div class="registered-summary">
                <strong>Total: ${data.count} course(s) | ${data.total_credits} credits</strong>
            </div>
        `;

    } catch (error) {
        console.error('Error loading registrations:', error);
    }
}

function toggleAllRegistrations() {
    const checked = document.getElementById('selectAllRegistrations').checked;
    document.querySelectorAll('.reg-checkbox').forEach(cb => cb.checked = checked);
    updateBulkDeleteState();
}

function updateBulkDeleteState() {
    const selectedCount = document.querySelectorAll('.reg-checkbox:checked').length;
    const countSpan = document.getElementById('selectedRegCount');
    const bulkBtn = document.getElementById('bulkDeleteBtn');

    if (countSpan) countSpan.textContent = selectedCount;
    if (bulkBtn) bulkBtn.disabled = selectedCount === 0;
}

async function bulkDeleteRegistrations() {
    const selectedIds = Array.from(
        document.querySelectorAll('.reg-checkbox:checked')
    ).map(cb => cb.value);

    if (selectedIds.length === 0) return;

    if (!confirm(`Remove ${selectedIds.length} registration(s)? This cannot be undone.`)) {
        return;
    }

    try {
        const response = await fetch('/api/registration/bulk-delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ registration_ids: selectedIds })
        });

        const data = await response.json();

        if (response.ok) {
            location.reload();
        } else {
            alert('Error: ' + (data.error || 'Failed to delete registrations'));
        }
    } catch (error) {
        console.error('Bulk delete error:', error);
        alert('Error removing registrations.');
    }
}

// Global variable to track if we are editing a registration
let currentEditingRegistrationId = null;
let currentEditingSlotId = null;

function openEditRegistrationModal(courseId, registrationId, currentSlotId) {
    currentEditingRegistrationId = registrationId;
    currentEditingSlotId = currentSlotId;

    // Change modal title temporarily (optional UI tweak)
    // For now re-use existing modal logic
    selectCourse(courseId);
}

// Functions moved to line 184 and following
// Removed duplicates to fix conflicts

function closeCourseModal() {
    document.getElementById('courseModal').classList.remove('active');
    currentEditingRegistrationId = null; // Reset edit mode on close
    currentEditingSlotId = null;
}

function viewRegisteredCourses() {
    const modal = document.getElementById('registeredModal');
    const content = document.getElementById('registeredModalContent');

    content.innerHTML = '<div class="loading-spinner"></div>';
    modal.classList.add('active');

    fetch('/api/registration/')
        .then(response => response.json())
        .then(data => {
            if (data.registrations.length === 0) {
                content.innerHTML = `
                    <div class="no-results">
                        <i class="fas fa-inbox"></i>
                        <p>No courses registered yet.</p>
                    </div>
                `;
                return;
            }

            content.innerHTML = `
                <table class="registered-table">
                    <thead>
                        <tr>
                            <th>Course Code</th>
                            <th>Course Name</th>
                            <th>Slot</th>
                            <th>Venue</th>
                            <th>Credits</th>
                            <th>Action</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${data.registrations.map(reg => `
                            <tr>
                                <td>${reg.slot?.course?.code || 'N/A'}</td>
                                <td>${reg.slot?.course?.name || 'N/A'}</td>
                                <td>${reg.slot?.slot_code || 'N/A'}</td>
                                <td>${reg.slot?.venue || 'N/A'}</td>
                                <td>${reg.slot?.course?.c || 0}</td>
                                <td>
                                    <button class="delete-btn" onclick="deleteRegistration('${reg.id}'); closeRegisteredModal();">
                                        <i class="fas fa-trash"></i> Remove
                                    </button>
                                </td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
                <div style="margin-top: 16px; text-align: right;">
                    <strong>Total Credits: ${data.total_credits}</strong>
                </div>
            `;
        })
        .catch(error => {
            console.error('Error:', error);
            content.innerHTML = '<p class="empty-message">Error loading registered courses.</p>';
        });
}

function closeRegisteredModal() {
    document.getElementById('registeredModal').classList.remove('active');
}

// ==================== View All Courses ====================

function viewAllCourses() {
    const modal = document.getElementById('allCoursesModal');
    const content = document.getElementById('allCoursesModalContent');

    content.innerHTML = '<div class="loading-spinner"></div>';
    modal.classList.add('active');

    Promise.all([
        fetch('/api/courses/all').then(r => r.json()),
        fetch('/api/registration/').then(r => r.json())
    ])
        .then(([coursesData, regData]) => {
            const registeredCourseIds = new Set();
            if (regData.registrations) {
                regData.registrations.forEach(reg => {
                    if (reg.slot && reg.slot.course) {
                        registeredCourseIds.add(reg.slot.course.id);
                    }
                });
            }

            if (coursesData.courses.length === 0) {
                content.innerHTML = `
                    <div class="no-results">
                        <i class="fas fa-inbox"></i>
                        <p>No courses imported yet. Upload HTML files to import course data.</p>
                    </div>
                `;
                return;
            }

            content.innerHTML = `
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
                    <p style="margin: 0;">Total courses: <strong>${coursesData.courses.length}</strong></p>
                    <button id="bulkDeleteBtn" class="btn btn-danger btn-sm" disabled onclick="deleteSelectedCourses()">
                        <i class="fas fa-trash"></i> Delete Selected
                    </button>
                </div>
                <div style="max-height: 60vh; overflow-y: auto;">
                <table class="registered-table">
                    <thead style="position: sticky; top: 0; background: var(--bg-secondary); z-index: 1;">
                        <tr>
                            <th style="width: 40px; text-align: center;">
                                <input type="checkbox" id="selectAllCourses" onclick="toggleAllCourses(this)">
                            </th>
                            <th>Code</th>
                            <th>Name</th>
                            <th>L-T-P-J-C</th>
                            <th>Type</th>
                            <th>Action</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${coursesData.courses.map(course => {
                const isRegistered = registeredCourseIds.has(course.id);
                const rowClass = isRegistered ? 'registered-row' : '';
                return `
                            <tr class="${rowClass}">
                                <td style="text-align: center;">
                                    <input type="checkbox" class="course-checkbox" value="${course.id}" onclick="toggleCourseSelection('${course.id}')">
                                </td>
                                <td><strong>${course.code}</strong></td>
                                <td>${course.name}</td>
                                <td>${course.ltpjc}</td>
                                <td>${course.course_type || '-'}</td>
                                <td>
                                    <div style="display: flex; gap: 5px; align-items: center;">
                                        ${isRegistered ?
                        `<button class="btn btn-success btn-sm btn-added" disabled style="opacity: 0.7; cursor: default;">
                                                <i class="fas fa-check"></i> Added
                                            </button>` :
                        `<button class="btn btn-primary btn-sm" onclick="selectCourse('${course.id}'); closeAllCoursesModal();">
                                                <i class="fas fa-plus"></i> Add
                                            </button>`
                    }
                                    </div>
                                </td>
                            </tr>
                        `}).join('')}
                    </tbody>
                </table>
                </div>
            `;

            // Reset selection state
            selectedCoursesToDelete.clear();
            updateDeleteButtonState();
        })
        .catch(error => {
            console.error('Error:', error);
            content.innerHTML = '<p class="empty-message">Error loading courses.</p>';
        });
}



let selectedCoursesToDelete = new Set();

function toggleAllCourses(source) {
    const checkboxes = document.querySelectorAll('.course-checkbox');
    checkboxes.forEach(cb => {
        cb.checked = source.checked;
        const id = cb.value;
        if (source.checked) {
            selectedCoursesToDelete.add(id);
        } else {
            selectedCoursesToDelete.delete(id);
        }
    });
    updateDeleteButtonState();
}

function toggleCourseSelection(id) {
    const checkbox = document.querySelector(`.course-checkbox[value="${id}"]`);
    if (checkbox.checked) {
        selectedCoursesToDelete.add(id);
    } else {
        selectedCoursesToDelete.delete(id);
    }
    updateDeleteButtonState();

    // Update header checkbox
    const allChecked = document.querySelectorAll('.course-checkbox:not(:checked)').length === 0;
    const headerCheckbox = document.getElementById('selectAllCourses');
    if (headerCheckbox) headerCheckbox.checked = allChecked;
}

function updateDeleteButtonState() {
    const btn = document.getElementById('bulkDeleteBtn');
    const count = selectedCoursesToDelete.size;

    if (count > 0) {
        btn.disabled = false;
        btn.innerHTML = `<i class="fas fa-trash"></i> Delete Selected (${count})`;
    } else {
        btn.disabled = true;
        btn.innerHTML = `<i class="fas fa-trash"></i> Delete Selected`;
    }
}

async function deleteSelectedCourses() {
    const count = selectedCoursesToDelete.size;
    if (count === 0) return;

    if (!confirm(`Are you sure you want to delete ${count} selected courses? This cannot be undone.`)) {
        return;
    }

    const btn = document.getElementById('bulkDeleteBtn');
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Deleting...';

    try {
        const response = await fetch('/api/courses/bulk', {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                course_ids: Array.from(selectedCoursesToDelete)
            })
        });

        const data = await response.json();

        if (response.ok) {
            // Success
            alert(data.message);
            // viewAllCourses(); // Reload modal
            // loadRegisteredCoursesList(); // Reload registered list (background)
            location.reload(); // Simplest to sync everything
        } else {
            alert('Error: ' + data.error);
            btn.disabled = false;
            updateDeleteButtonState();
        }

    } catch (error) {
        console.error('Bulk delete error:', error);
        alert('Error deleting courses');
        btn.disabled = false;
        updateDeleteButtonState();
    }
}

function closeAllCoursesModal() {
    document.getElementById('allCoursesModal').classList.remove('active');
}

// ==================== Clear All ====================

async function clearAllRegistrations() {
    if (!confirm('Are you sure you want to remove ALL registered courses?')) {
        return;
    }

    try {
        const response = await fetch('/api/registration/');
        const data = await response.json();

        for (const reg of data.registrations) {
            await fetch(`/api/registration/${reg.id}`, { method: 'DELETE' });
        }

        location.reload();

    } catch (error) {
        console.error('Error clearing registrations:', error);
        alert('Error clearing registrations.');
    }
}

// ==================== HTML File Upload (Multiple Files) ====================

document.addEventListener('DOMContentLoaded', function () {
    const fileInput = document.getElementById('htmlFileInput');
    if (fileInput) {
        fileInput.addEventListener('change', handleFilesSelect);
    }
});

let selectedFiles = [];

function handleFilesSelect(event) {
    const files = Array.from(event.target.files);
    const fileNameSpan = document.getElementById('selectedFileName');
    const importBtn = document.getElementById('importBtn');
    const statusDiv = document.getElementById('uploadStatus');

    if (files.length > 0) {
        selectedFiles = files;
        fileNameSpan.textContent = files.length === 1
            ? files[0].name
            : `${files.length} files selected`;
        importBtn.disabled = false;
        statusDiv.innerHTML = '';

        // Preview all files
        previewHtmlFiles(files);
    } else {
        selectedFiles = [];
        fileNameSpan.textContent = '';
        importBtn.disabled = true;
    }
}

async function previewHtmlFiles(files) {
    const statusDiv = document.getElementById('uploadStatus');
    statusDiv.innerHTML = '<div class="loading-spinner"></div> Parsing files...';

    const previews = [];
    let errors = 0;

    for (const file of files) {
        const formData = new FormData();
        formData.append('file', file);

        try {
            const response = await fetch('/api/upload/parse', {
                method: 'POST',
                body: formData
            });

            const data = await response.json();

            if (response.ok) {
                previews.push(`<div class="preview-item"><strong>${data.course.code}</strong> - ${data.course.name} (${data.slot_count} slots)</div>`);
            } else {
                previews.push(`<div class="preview-error-item">${file.name}: ${data.error}</div>`);
                errors++;
            }

        } catch (error) {
            previews.push(`<div class="preview-error-item">${file.name}: Error parsing</div>`);
            errors++;
        }
    }

    statusDiv.innerHTML = `
        <div class="preview-list">
            ${previews.join('')}
        </div>
    `;

    if (errors === files.length) {
        document.getElementById('importBtn').disabled = true;
    }
}

async function importHtmlFiles() {
    if (selectedFiles.length === 0) {
        alert('Please select files first.');
        return;
    }

    const statusDiv = document.getElementById('uploadStatus');
    const importBtn = document.getElementById('importBtn');

    importBtn.disabled = true;
    statusDiv.innerHTML = '<div class="loading-spinner"></div> Importing data...';

    const formData = new FormData();
    selectedFiles.forEach(file => {
        formData.append('files[]', file);
    });

    try {
        const response = await fetch('/api/upload/import', {
            method: 'POST',
            body: formData
        });

        const data = await response.json();

        if (response.ok && data.success) {
            // Render results
            const resultItems = data.results.map(res => {
                if (res.status === 'success') {
                    return `<div class="import-success-item"><i class="fas fa-check"></i> ${res.filename}: Imported ${res.course_code} (${res.slots_added} slots)</div>`;
                } else {
                    return `<div class="import-error-item"><i class="fas fa-times"></i> ${res.filename}: ${res.message}</div>`;
                }
            }).join('');

            statusDiv.innerHTML = `
                <div class="import-results">
                    <div class="import-summary">${data.summary}</div>
                    ${resultItems}
                </div>
            `;

            // Clear input
            document.getElementById('htmlFileInput').value = '';
            document.getElementById('selectedFileName').textContent = '';
            selectedFiles = [];

            if (data.success_count > 0) {
                setTimeout(() => {
                    location.reload();
                }, 2000);
            } else {
                importBtn.disabled = false;
            }

        } else {
            statusDiv.innerHTML = `<div class="import-error-item">Error: ${data.error || 'Upload failed'}</div>`;
            importBtn.disabled = false;
        }

    } catch (error) {
        console.error('Import error:', error);
        statusDiv.innerHTML = `<div class="import-error-item">Error importing files.</div>`;
        importBtn.disabled = false;
    }
}

// ==================== Manual Entry ====================

function openManualEntryModal() {
    document.getElementById('manualEntryModal').classList.add('active');
    document.getElementById('manualEntryForm').reset();

    // Pre-fill slot code with selected cells
    if (selectedCells.length > 0) {
        document.getElementById('manualSlotCode').value = selectedCells.join('+');
    }
}

function closeManualEntryModal() {
    document.getElementById('manualEntryModal').classList.remove('active');
    clearCellSelection();
}

async function submitManualEntry(event) {
    event.preventDefault();

    const formData = {
        course_code: document.getElementById('manualCourseCode').value.trim(),
        course_name: document.getElementById('manualCourseName').value.trim(),
        credits: parseInt(document.getElementById('manualCredits').value) || 0,
        slot_code: document.getElementById('manualSlotCode').value.trim(),
        venue: document.getElementById('manualVenue').value.trim() || 'N/A',
        faculty: document.getElementById('manualFaculty').value.trim()
    };

    try {
        const response = await fetch('/api/courses/manual', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(formData)
        });

        const data = await response.json();

        if (response.ok) {
            alert(`Success! ${data.message}`);
            closeManualEntryModal();
            location.reload();
        } else {
            alert(`Error: ${data.error}`);
        }
    } catch (error) {
        console.error('Error:', error);
        alert('Error adding course. Please try again.');
    }
}

// ==================== Import Help ====================

function showImportHelp() {
    document.getElementById('importHelpModal').classList.add('active');
}

function closeImportHelp() {
    document.getElementById('importHelpModal').classList.remove('active');
}

// ==================== PDF Download ====================

async function downloadTimetablePDF() {
    const { jsPDF } = window.jspdf;
    const element = document.querySelector('.timetable-section');
    const btn = document.querySelector('.print-btn');

    // Temporarily hide the button for the screenshot
    if (btn) btn.style.display = 'none';

    try {
        const canvas = await html2canvas(element, {
            scale: 2, // High quality
            useCORS: true,
            backgroundColor: '#ffffff'
        });

        const imgData = canvas.toDataURL('image/png');

        // A4 Landscape dimensions in mm
        const pdf = new jsPDF('l', 'mm', 'a4');
        const pdfWidth = 297;
        const pdfHeight = 210;

        const imgProps = pdf.getImageProperties(imgData);
        const imgWidth = imgProps.width;
        const imgHeight = imgProps.height;

        // Calculate scale to fit width (with margin)
        const margin = 10;
        const maxWidth = pdfWidth - (margin * 2);
        const ratio = maxWidth / imgWidth;

        const finalWidth = imgWidth * ratio;
        const finalHeight = imgHeight * ratio;

        const x = (pdfWidth - finalWidth) / 2;
        const y = 15; // Top padding

        pdf.addImage(imgData, 'PNG', x, y, finalWidth, finalHeight);
        pdf.save('My_Timetable.pdf');

    } catch (err) {
        console.error('PDF Generation Error:', err);
        alert('Error generating PDF.');
    } finally {
        if (btn) btn.style.display = 'inline-flex';
    }
}

// ==================== Auto Generate Timetable ====================

// switchGenerateTab function moved to Saved Timetables Features section

async function populateTeacherPreferences() {
    const list = document.getElementById('teacherPreferencesList');
    // Only populate if empty or dirty check needed? 
    // Always repopulate based on currently selected courses

    // Get selected courses
    const selectedIds = Array.from(
        document.querySelectorAll('#courseSelectionList input[type="checkbox"]:checked')
    ).map(cb => cb.value);

    if (selectedIds.length === 0) {
        list.innerHTML = '<div class="loading-text">Please select courses in Step 1 first.</div>';
        return;
    }

    const selectedCourses = generateAvailableCourses.filter(c => selectedIds.includes(String(c.id)));

    if (selectedCourses.length === 0) {
        list.innerHTML = '<div class="loading-text">No courses selected.</div>';
        return;
    }

    let html = '';

    selectedCourses.forEach(course => {
        // Find faculties for this course
        // We need to know which faculties teach THIS course. 
        // generateAvailableCourses data structure already has 'faculties' list!

        let facultyOptions = '<option value="">-- No Preference --</option>';
        if (course.faculties && course.faculties.length > 0) {
            course.faculties.forEach(f => {
                facultyOptions += `<option value="${f}">${f}</option>`;
            });
        }

        html += `
            <div class="teacher-pref-item" data-course-id="${course.id}">
                <div class="teacher-course-header">
                    <span>${course.code} - ${course.name}</span>
                    <span style="font-size:12px; font-weight:normal; color:#666;">${course.faculties.length} Faculty Options</span>
                </div>
                <div class="teacher-rank-row">
                    <div class="rank-select-group">
                        <label>1st Choice (+50)</label>
                        <select class="rank-select rank-1">${facultyOptions}</select>
                    </div>
                    <div class="rank-select-group">
                        <label>2nd Choice (+30)</label>
                        <select class="rank-select rank-2">${facultyOptions}</select>
                    </div>
                    <div class="rank-select-group">
                        <label>3rd Choice (+15)</label>
                        <select class="rank-select rank-3">${facultyOptions}</select>
                    </div>
                </div>
            </div>
        `;
    });

    list.innerHTML = html;
}

let generateCurrentStep = 1;
let generateAvailableCourses = [];
let generateAllFaculties = [];
let generateSelectedCourseIds = [];
let generateSuggestions = [];
let generateCurrentOffset = 0;
let generatePreferences = {};
let currentPreviewIndex = -1;  // Track currently previewed suggestion (-1 = none)
let generateActiveTab = 'time'; // 'time', 'teacher', 'random'

function openGenerateModal() {
    document.getElementById('generateModal').classList.add('active');
    generateCurrentStep = 1;
    generateSuggestions = [];
    generateCurrentOffset = 0;
    updateGenerateStepUI();
    loadAvailableCoursesForGeneration();
    // Default to Custom tab (Unified)
    switchGenerateTab('custom');
    // Clear teacher prefs list to force reload when needed
    document.getElementById('teacherPreferencesList').innerHTML = '<div class="loading-text"><i class="fas fa-spinner fa-spin"></i> Preparing faculty list...</div>';
}

function closeGenerateModal() {
    document.getElementById('generateModal').classList.remove('active');
    generateCurrentStep = 1;
    generateSelectedCourseIds = [];
    generateSuggestions = [];
}

function updateGenerateStepUI() {
    // Update step indicators
    document.querySelectorAll('.generate-steps .step').forEach((el, i) => {
        el.classList.remove('active', 'completed');
        if (i + 1 < generateCurrentStep) el.classList.add('completed');
        if (i + 1 === generateCurrentStep) el.classList.add('active');
    });

    // Show/hide step content
    document.querySelectorAll('.generate-step-content').forEach((el, i) => {
        el.classList.toggle('active', i + 1 === generateCurrentStep);
    });

    // Update navigation buttons
    const prevBtn = document.getElementById('generatePrevBtn');
    const nextBtn = document.getElementById('generateNextBtn');
    const genBtn = document.getElementById('generateBtn');

    prevBtn.style.display = generateCurrentStep > 1 ? 'inline-flex' : 'none';
    nextBtn.style.display = generateCurrentStep < 2 ? 'inline-flex' : 'none';
    genBtn.style.display = generateCurrentStep === 2 ? 'inline-flex' : 'none';
}

function prevGenerateStep() {
    if (generateCurrentStep > 1) {
        generateCurrentStep--;
        updateGenerateStepUI();
    }
}

function nextGenerateStep() {
    if (generateCurrentStep === 1) {
        // Validate course selection - keep IDs as strings to avoid precision loss
        generateSelectedCourseIds = Array.from(
            document.querySelectorAll('#courseSelectionList input[type="checkbox"]:checked')
        ).map(cb => cb.value);  // Keep as string, don't parseInt

        if (generateSelectedCourseIds.length === 0) {
            alert('Please select at least one course.');
            return;
        }

        generateCurrentStep = 2;
        updateGenerateStepUI();

        // Populate teacher preferences for the selected courses
        populateTeacherPreferences();
    }
}

async function loadAvailableCoursesForGeneration() {
    const listDiv = document.getElementById('courseSelectionList');
    listDiv.innerHTML = '<p class="loading-text"><i class="fas fa-spinner fa-spin"></i> Loading courses...</p>';

    try {
        const response = await fetch('/api/generate/available');
        const data = await response.json();

        if (!response.ok) {
            listDiv.innerHTML = `<p class="loading-text">Error: ${data.error}</p>`;
            return;
        }

        generateAvailableCourses = data.courses;
        generateAllFaculties = data.all_faculties;

        if (generateAvailableCourses.length === 0) {
            listDiv.innerHTML = '<p class="loading-text">No courses imported yet. Please upload HTML files first.</p>';
            return;
        }

        // Render course list
        listDiv.innerHTML = generateAvailableCourses.map(course => `
            <div class="course-select-item ${course.is_registered ? 'registered' : ''}">
                <input type="checkbox" value="${course.id}" 
                    ${course.is_registered ? 'disabled' : ''} 
                    onchange="updateGenerateCredits()">
                <div class="course-select-info">
                    <span class="course-select-code">${course.code}</span>
                    <span class="course-select-name">${course.name}</span>
                    ${course.is_registered ? '<span class="badge-registered">(Already registered)</span>' : ''}
                </div>
                <span class="course-select-credits">${course.credits} cr</span>
            </div>
        `).join('');

        // Populate faculty selects - Obsolete in new design
        // populateFacultySelects();

        updateGenerateCredits();

    } catch (error) {
        console.error('Error loading courses:', error);
        listDiv.innerHTML = '<p class="loading-text">Error loading courses.</p>';
    }
}

// Obsolete function removed
/*
function populateFacultySelects() {
    const prefSelect = document.getElementById('preferredFaculties');
    const avoidSelect = document.getElementById('avoidedFaculties');

    const options = generateAllFaculties.map(f => `<option value="${f}">${f}</option>`).join('');

    prefSelect.innerHTML = options;
    avoidSelect.innerHTML = options;
}
*/

function toggleAllCoursesGenerate() {
    const checked = document.getElementById('selectAllCoursesGenerate').checked;
    document.querySelectorAll('#courseSelectionList input[type="checkbox"]:not(:disabled)')
        .forEach(cb => cb.checked = checked);
    updateGenerateCredits();
}

function updateGenerateCredits() {
    const selectedIds = Array.from(
        document.querySelectorAll('#courseSelectionList input[type="checkbox"]:checked')
    ).map(cb => cb.value);  // Keep as string

    let totalCredits = 0;
    selectedIds.forEach(id => {
        // Compare as strings since course.id is now a string
        const course = generateAvailableCourses.find(c => c.id === id || String(c.id) === id);
        if (course) totalCredits += course.credits;
    });

    const display = document.getElementById('selectedCreditsDisplay');
    if (display) {
        display.textContent = `${totalCredits} credits selected`;
        display.style.color = totalCredits > 27 ? '#e74c3c' : (totalCredits < 16 ? '#f39c12' : '#27ae60');
    }
}

async function generateTimetable() {
    // Collect preferences based on Active Tab
    const avoidEarly = document.getElementById('prefAvoidEarlyMorning').checked;
    const avoidLate = document.getElementById('prefAvoidLateEvening').checked;

    generatePreferences = {
        avoid_early_morning: avoidEarly,
        avoid_late_evening: avoidLate,
        time_mode: 'none',
        course_faculty_preferences: {}
    };

    // Collect preferences (Random tab removed - always collect from single preferences tab)

    // 1. Collect Time Mode
    const modeEls = document.getElementsByName('timeMode');
    let selectedMode = 'none';
    modeEls.forEach(el => { if (el.checked) selectedMode = el.value; });
    generatePreferences.time_mode = selectedMode;

    // 2. Collect Teacher Ranks
    const prefs = {};
    document.querySelectorAll('.teacher-pref-item').forEach(item => {
        const courseId = item.dataset.courseId;
        const rank1 = item.querySelector('.rank-1').value;
        const rank2 = item.querySelector('.rank-2').value;
        const rank3 = item.querySelector('.rank-3').value;

        const list = [];
        if (rank1) list.push(rank1);
        if (rank2 && !list.includes(rank2)) list.push(rank2);
        if (rank3 && !list.includes(rank3)) list.push(rank3);

        if (list.length > 0) {
            prefs[courseId] = list; // { course_id: ['FacA', 'FacB'] }
        }
    });
    generatePreferences.course_faculty_preferences = prefs;

    generateCurrentStep = 3;
    generateCurrentOffset = 0;
    generateSuggestions = [];
    updateGenerateStepUI();

    const statusDiv = document.getElementById('generationStatus');
    statusDiv.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Generating timetables...';
    document.getElementById('suggestionsList').innerHTML = '';

    console.log('Sending preferences:', generatePreferences);  // DEBUG

    try {
        const response = await fetch('/api/generate/suggest', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                course_ids: generateSelectedCourseIds,
                preferences: generatePreferences,
                limit: 100  // User requested "first 100 results"
            })
        });

        const data = await response.json();
        console.log('Generate suggest response:', JSON.stringify(data, null, 2));  // DEBUG

        if (!response.ok) {
            console.error('Generate error debug:', JSON.stringify(data.debug, null, 2));  // DEBUG
            statusDiv.innerHTML = `<i class="fas fa-exclamation-circle"></i> Error: ${data.error}`;
            return;
        }

        generateSuggestions = data.suggestions;
        generateCurrentOffset = generateSuggestions.length;

        if (generateSuggestions.length === 0) {
            statusDiv.innerHTML = '<i class="fas fa-info-circle"></i> No valid combinations found. Try removing some courses.';
        } else {
            let msg = `<i class="fas fa-check-circle"></i> Found ${generateSuggestions.length} timetable option(s).`;
            if (data.relaxed_constraints) {
                msg += ' <span style="color: #e67e22; font-size: 0.9em;"><br><i class="fas fa-exclamation-triangle"></i> Strict time constraints relaxed to find matches.</span>';
            }
            statusDiv.innerHTML = msg;

            renderSuggestions();
            // Similar endpoint doesn't support offset loading
        }

    } catch (error) {
        console.error('Generation error:', error);
        statusDiv.innerHTML = '<i class="fas fa-exclamation-circle"></i> Error generating timetables.';
    }
}

async function countTimetables() {
    const countBox = document.getElementById('timetableCountBox');
    const countText = document.getElementById('timetableCountText');
    const countMode = document.getElementById('countMode') ? document.getElementById('countMode').value : 'distinct';

    // Must update selected IDs first because button is in Step 1
    generateSelectedCourseIds = Array.from(
        document.querySelectorAll('#courseSelectionList input[type="checkbox"]:checked')
    ).map(cb => cb.value);

    if (generateSelectedCourseIds.length === 0) {
        alert('Please select at least one course first.');
        return;
    }

    countBox.classList.remove('zero');
    countBox.classList.add('loading');
    countText.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Counting...';

    // Get current preferences (minimal for count)
    const preferences = {
        avoid_early_morning: document.getElementById('prefAvoidEarlyMorning') ? document.getElementById('prefAvoidEarlyMorning').checked : false,
        avoid_late_evening: document.getElementById('prefAvoidLateEvening') ? document.getElementById('prefAvoidLateEvening').checked : false,
        // Other prefs might affect count if hard constraints, but for now mostly basic
    };

    try {
        const response = await fetch('/api/generate/count', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                course_ids: generateSelectedCourseIds,
                preferences: preferences,
                mode: countMode
            })
        });

        const data = await response.json();
        console.log('Count response debug:', data.debug);  // DEBUG
        countBox.classList.remove('loading');

        if (response.ok) {
            const count = data.count;
            const capped = data.capped;

            if (count === 0) {
                countBox.classList.add('zero');
                countText.innerHTML = '<span class="count-number">0</span> valid patterns. Try removing courses.';
            } else if (capped) {
                countText.innerHTML = `<span class="count-number">${count}+</span> valid patterns available!`;
            } else {
                countText.innerHTML = `<span class="count-number">${count}</span> valid patterns available`;
            }
        } else {
            countText.innerHTML = 'Error: ' + (data.error || 'Unknown error');
        }

    } catch (error) {
        console.error('Count error:', error);
        countBox.classList.remove('loading');
        countText.innerHTML = 'Error counting timetables';
    }
}

async function loadMoreSuggestions() {
    const btn = document.querySelector('#loadMoreContainer button');
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Loading...';

    try {
        const response = await fetch('/api/generate/more', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                course_ids: generateSelectedCourseIds,
                preferences: generatePreferences,
                offset: generateCurrentOffset
            })
        });

        const data = await response.json();

        if (response.ok && data.suggestions.length > 0) {
            generateSuggestions = generateSuggestions.concat(data.suggestions);
            generateCurrentOffset += data.suggestions.length;
            renderSuggestions();

            if (!data.has_more) {
                document.getElementById('loadMoreContainer').style.display = 'none';
            }

            document.getElementById('generationStatus').innerHTML =
                `<i class="fas fa-check-circle"></i> Found ${generateSuggestions.length} timetable option(s)`;
        } else {
            document.getElementById('loadMoreContainer').style.display = 'none';
        }

    } catch (error) {
        console.error('Load more error:', error);
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-plus"></i> Load More Suggestions';
    }
}

function renderSuggestions() {
    if (generateSuggestions.length === 0) {
        document.getElementById('suggestionsList').innerHTML = '<p class="loading-text">No timetables found.</p>';
        document.getElementById('cardCounter').innerHTML = '';
        return;
    }

    // Start with first card
    currentPreviewIndex = 0;
    renderCurrentCard();
}

// Helper to render a suggestion card into a container
function renderSuggestionCard(suggestion, index, targetId, options = {}) {
    const listDiv = document.getElementById(targetId);
    if (!listDiv) return;

    // Color palette matching main.py
    const COURSE_COLORS = [
        '#90EE90', '#87CEEB', '#FFB6C1', '#DDA0DD', '#F0E68C',
        '#FF7F50', '#00CED1', '#FFE4B5', '#E6E6FA', '#FFDAB9',
        '#DA70D6', '#FFA07A'
    ];

    const courseColors = {};
    let colorIndex = 0;

    // Assign colors to unique courses in this suggestion
    suggestion.slots.forEach(slot => {
        if (!courseColors[slot.course_code]) {
            courseColors[slot.course_code] = COURSE_COLORS[colorIndex % COURSE_COLORS.length];
            colorIndex++;
        }
    });

    // Build slot lines
    const slotLines = suggestion.slots.map(s => {
        const teacherName = s.faculty_name || 'TBA';
        const color = courseColors[s.course_code] || '#eee';
        return `<div class="suggestion-slot-line" style="display: flex; align-items: center;">
            <span style="display: inline-block; width: 12px; height: 12px; background-color: ${color}; border: 1px solid #999; margin-right: 8px; flex-shrink: 0;"></span>
            <span>${s.course_code}: ${teacherName} (${s.slot_code})</span>
        </div>`;
    }).join('');

    // Get preferred teacher count
    const prefCount = suggestion.details?.teacher_match_count ?? suggestion.details?.preferred_faculty_matches ?? 0;

    // Actions
    let actionsHtml = '';
    if (options.showActions) {
        actionsHtml = `
            <div class="suggestion-actions">
                <button class="btn btn-secondary" style="margin-right: 5px;" onclick="saveSuggestion(${index})">
                    <i class="fas fa-bookmark"></i> Save
                </button>
                <button class="btn btn-success" onclick="applySuggestion(${index})">
                    <i class="fas fa-check"></i> Apply This Timetable
                </button>
            </div>
         `;
    }

    listDiv.innerHTML = `
        <div class="suggestion-card ${options.active ? 'active-preview' : ''}" style="${options.style || ''}">
            <div class="suggestion-header">
                <span class="suggestion-rank">${options.title || '#' + (index + 1)}</span>
                <span class="suggestion-score">${suggestion.total_credits} cr</span>
            </div>
            <div class="suggestion-slots-list">
                ${slotLines}
            </div>

            <div class="suggestion-pref-count">
                <i class="fas fa-star"></i> ${prefCount} preferred teachers
            </div>
            ${actionsHtml}
        </div>
    `;

    return courseColors;
}

function renderCurrentCard() {
    const counterDiv = document.getElementById('cardCounter');

    if (currentPreviewIndex < 0 || currentPreviewIndex >= generateSuggestions.length) {
        return;
    }

    const index = currentPreviewIndex;
    const suggestion = generateSuggestions[index];

    // Render using helper
    const courseColors = renderSuggestionCard(suggestion, index, 'suggestionsList', {
        showActions: true
    });

    // Update counter
    if (counterDiv) {
        counterDiv.innerHTML = `<strong>${index + 1}</strong> of <strong>${generateSuggestions.length}</strong> timetables &nbsp;|&nbsp; Use ← → arrow keys to navigate`;
    }

    // Update timetable preview
    renderMiniTimetable(suggestion, courseColors);
}



function previewSuggestion(index) {
    const suggestion = generateSuggestions[index];
    if (!suggestion) return;

    currentPreviewIndex = index;

    // Highlight the active card in the suggestions list
    document.querySelectorAll('.suggestion-card').forEach((card, i) => {
        if (i === index) {
            card.classList.add('active-preview');
        } else {
            card.classList.remove('active-preview');
        }
    });

    // Render mini timetable preview
    renderMiniTimetable(suggestion);

    // Show preview indicator
    const statusDiv = document.getElementById('generationStatus');
    if (statusDiv) {
        statusDiv.innerHTML = `<i class="fas fa-eye"></i> Previewing Option #${index + 1} of ${generateSuggestions.length} | Use ← → to navigate | Press Enter to apply`;
    }
}

function renderMiniTimetable(suggestion, courseColors = {}, targetId = 'miniTimetablePreview') {
    const container = document.getElementById(targetId);
    if (!container) return;

    // Define the grid structure with lunch
    const days = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'];
    const periods = [
        { num: 1, time: '8:30-10:00', label: 'P1' },
        { num: 2, time: '10:05-11:35', label: 'P2' },
        { num: 3, time: '11:40-13:10', label: 'P3' },
        { num: 'lunch', time: 'LUNCH', label: '🍽️' },
        { num: 4, time: '13:15-14:45', label: 'P4' },
        { num: 5, time: '14:50-16:20', label: 'P5' },
        { num: 6, time: '16:25-17:55', label: 'P6' },
        { num: 7, time: '18:00-19:30', label: 'P7' }
    ];

    // Map slot codes to their day/period
    const slotTimings = {
        'A11': { day: 'MON', period: 1 }, 'B11': { day: 'MON', period: 2 }, 'C11': { day: 'MON', period: 3 },
        'A21': { day: 'MON', period: 4 }, 'A14': { day: 'MON', period: 5 }, 'B21': { day: 'MON', period: 6 }, 'C21': { day: 'MON', period: 7 },
        'D11': { day: 'TUE', period: 1 }, 'E11': { day: 'TUE', period: 2 }, 'F11': { day: 'TUE', period: 3 },
        'D21': { day: 'TUE', period: 4 }, 'E14': { day: 'TUE', period: 5 }, 'E21': { day: 'TUE', period: 6 }, 'F21': { day: 'TUE', period: 7 },
        'A12': { day: 'WED', period: 1 }, 'B12': { day: 'WED', period: 2 }, 'C12': { day: 'WED', period: 3 },
        'A22': { day: 'WED', period: 4 }, 'B14': { day: 'WED', period: 5 }, 'B22': { day: 'WED', period: 6 }, 'A24': { day: 'WED', period: 7 },
        'D12': { day: 'THU', period: 1 }, 'E12': { day: 'THU', period: 2 }, 'F12': { day: 'THU', period: 3 },
        'D22': { day: 'THU', period: 4 }, 'F14': { day: 'THU', period: 5 }, 'E22': { day: 'THU', period: 6 }, 'F22': { day: 'THU', period: 7 },
        'A13': { day: 'FRI', period: 1 }, 'B13': { day: 'FRI', period: 2 }, 'C13': { day: 'FRI', period: 3 },
        'A23': { day: 'FRI', period: 4 }, 'C14': { day: 'FRI', period: 5 }, 'B23': { day: 'FRI', period: 6 }, 'B24': { day: 'FRI', period: 7 },
        'D13': { day: 'SAT', period: 1 }, 'E13': { day: 'SAT', period: 2 }, 'F13': { day: 'SAT', period: 3 },
        'D23': { day: 'SAT', period: 4 }, 'D14': { day: 'SAT', period: 5 }, 'D24': { day: 'SAT', period: 6 }, 'E23': { day: 'SAT', period: 7 }
    };

    // Build grid data from suggestion slots
    const grid = {};
    days.forEach(d => {
        grid[d] = {};
        periods.forEach(p => grid[d][p.num] = null);
    });

    suggestion.slots.forEach(slotInfo => {
        const slotCodes = slotInfo.slot_code.replace(/\//g, '+').split('+');
        slotCodes.forEach(code => {
            const timing = slotTimings[code];
            if (timing) {
                grid[timing.day][timing.period] = {
                    code: slotInfo.course_code,
                    venue: slotInfo.venue || '',
                    faculty: slotInfo.faculty_name || ''
                };
            }
        });
    });

    // Calculate stats
    const prefCount = suggestion.details?.teacher_match_count ?? suggestion.details?.preferred_faculty_matches ?? 0;
    const satClasses = suggestion.details?.saturday_classes ?? 0;

    // Color palette logic moved to renderCurrentCard

    // Generate HTML - Days as rows, Times as columns
    let html = '<div class="preview-timetable-wrapper">';
    html += '<table class="mini-timetable"><thead><tr><th class="day-header">Day</th>';
    periods.forEach(p => {
        if (p.num === 'lunch') {
            html += '<th class="time-col lunch-col">Lunch</th>';
        } else {
            html += `<th class="time-col"><div>${p.label}</div><small>${p.time}</small></th>`;
        }
    });
    html += '</tr></thead><tbody>';

    days.forEach(d => {
        html += `<tr><td class="day-header">${d}</td>`;
        periods.forEach(p => {
            if (p.num === 'lunch') {
                html += '<td class="lunch-cell"><span>LUNCH</span></td>';
            } else {
                const cell = grid[d][p.num];
                if (cell) {
                    const bgColor = courseColors[cell.code] || '#90EE90';
                    html += `<td class="slot-filled" style="background-color: ${bgColor};" title="${cell.code} - ${cell.faculty || 'TBA'} @ ${cell.venue || 'TBA'}">
                        <div class="slot-code">${cell.code}</div>
                        <div class="slot-venue">${cell.venue || ''}</div>
                    </td>`;
                } else {
                    html += '<td class="slot-empty"></td>';
                }
            }
        });
        html += '</tr>';
    });

    html += '</tbody></table></div>';

    container.innerHTML = html;
}

function clearPreview() {
    currentPreviewIndex = -1;
    document.querySelectorAll('.slot-cell.preview-highlight').forEach(el => {
        el.classList.remove('preview-highlight');
        el.innerHTML = '';
    });
    document.querySelectorAll('.suggestion-card.active-preview').forEach(el => {
        el.classList.remove('active-preview');
    });
}

function navigatePreview(direction) {
    if (generateSuggestions.length === 0) return;

    let newIndex = currentPreviewIndex + direction;

    // Wrap around
    if (newIndex < 0) newIndex = generateSuggestions.length - 1;
    if (newIndex >= generateSuggestions.length) newIndex = 0;

    currentPreviewIndex = newIndex;
    renderCurrentCard();
}

// Arrow key and Enter navigation for previews
document.addEventListener('keydown', function (e) {
    // Check if we're on the generate page or modal is open
    const onGeneratePage = document.querySelector('.generate-page-main') !== null;
    const modal = document.getElementById('generateModal');
    const modalOpen = modal && modal.classList.contains('active');

    // Check if saved view is active
    const savedView = document.getElementById('savedView');
    const isSavedTabActive = savedView && savedView.style.display !== 'none';

    // Handle saved configurations tab navigation
    if ((onGeneratePage || modalOpen) && isSavedTabActive && savedTimetablesList.length > 0) {
        if (e.key === 'ArrowLeft') {
            e.preventDefault();
            prevSavedTimetable();
        } else if (e.key === 'ArrowRight') {
            e.preventDefault();
            nextSavedTimetable();
        }
        return;
    }

    // Must be on generate page OR modal open, and on step 3 for generator tab
    if (!onGeneratePage && !modalOpen) {
        return;
    }
    if (generateCurrentStep !== 3) {
        return;
    }

    if (e.key === 'ArrowLeft') {
        e.preventDefault();
        navigatePreview(-1);
    } else if (e.key === 'ArrowRight') {
        e.preventDefault();
        navigatePreview(1);
    } else if (e.key === 'Enter' && currentPreviewIndex >= 0) {
        e.preventDefault();
        applySuggestion(currentPreviewIndex);
    } else if (e.key === 'Escape') {
        clearPreview();
    }
});

async function applySuggestion(index) {
    const suggestion = generateSuggestions[index];
    if (!suggestion) return;

    if (!confirm('Apply this timetable? This will replace your current registrations.')) {
        return;
    }

    const slotIds = suggestion.slots.map(s => s.slot_id);

    try {
        const response = await fetch('/api/generate/apply', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ slot_ids: slotIds })
        });

        const data = await response.json();

        if (response.ok) {
            alert(`Success! Registered ${data.registration_count} courses.`);
            // Redirect to main page (works for both modal and page)
            window.location.href = '/';
        } else {
            alert('Error: ' + data.error);
        }

    } catch (error) {
        console.error('Apply error:', error);
        alert('Error applying timetable.');
    }
}

// Add CSS for preview highlight
const previewStyle = document.createElement('style');
previewStyle.textContent = `
    .slot-cell.preview-highlight {
        background-color: rgba(46, 204, 113, 0.4) !important;
        border: 2px dashed #27ae60 !important;
        animation: pulse-preview 1s infinite;
    }
    @keyframes pulse-preview {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.8; }
    }
    .preview-slot-content {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        height: 100%;
        padding: 2px;
        font-size: 11px;
        color: #1a5f3c;
    }
    .preview-slot-content strong {
        font-size: 12px;
        font-weight: 600;
    }
    .preview-slot-content small {
        font-size: 9px;
        opacity: 0.8;
    }
    .suggestion-card.active-preview {
        border: 3px solid #27ae60 !important;
        background-color: rgba(46, 204, 113, 0.15) !important;
        transform: scale(1.02);
        box-shadow: 0 4px 12px rgba(39, 174, 96, 0.3);
    }
    .badge-registered {
        font-size: 11px;
        color: #27ae60;
        font-style: italic;
    }
`;
document.head.appendChild(previewStyle);


// --- Saved Timetables Features ---

function switchGenerateTab(tabName) {
    // Update tabs
    document.querySelectorAll('.nav-tab').forEach(tab => {
        if (tab.dataset.tab === tabName) {
            tab.classList.add('active');
        } else {
            tab.classList.remove('active');
        }
    });

    // Update views
    document.getElementById('generatorView').style.display = (tabName === 'generator') ? 'block' : 'none';
    document.getElementById('savedView').style.display = (tabName === 'saved') ? 'block' : 'none';

    if (tabName === 'saved') {
        loadSavedTimetables();
    }
}

function saveSuggestion(index) {
    const suggestion = generateSuggestions[index];
    if (!suggestion) return;

    // Prompt for name (optional)
    const name = prompt("Enter a name for this configuration:", `Option #${index + 1} (${suggestion.total_credits} cr)`);
    if (!name) return; // User cancelled

    const slotIds = suggestion.slots.map(s => s.slot_id);

    fetch('/api/generate/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            name: name,
            slot_ids: slotIds,
            total_credits: suggestion.total_credits,
            course_count: suggestion.slots.length
        })
    })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                alert('Timetable configuration saved successfully!');
            } else {
                alert('Error saving timetable: ' + data.error);
            }
        })
        .catch(err => {
            console.error('Save error:', err);
            alert('Failed to save timetable.');
        });
}

// Saved Timetables State
let savedTimetablesList = [];
let currentSavedIndex = 0;

function loadSavedTimetables() {
    const statusDiv = document.getElementById('savedGenerationStatus');
    statusDiv.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Loading saved configurations...';

    fetch('/api/generate/saved')
        .then(r => r.json())
        .then(data => {
            if (data.saved && data.saved.length > 0) {
                savedTimetablesList = data.saved;
                currentSavedIndex = 0;
                statusDiv.innerHTML = `<i class="fas fa-check"></i> ${savedTimetablesList.length} saved timetable(s) found`;
                renderCurrentSavedCard();
            } else {
                savedTimetablesList = [];
                statusDiv.innerHTML = '<i class="fas fa-info-circle"></i> No saved timetables yet. Save one from the Generator tab!';
                document.getElementById('savedMiniTimetable').innerHTML = `
                    <div class="preview-placeholder">
                        <i class="fas fa-bookmark"></i>
                        <p>No saved timetables yet</p>
                    </div>`;
                document.getElementById('savedDetailsCard').innerHTML = '';
                document.getElementById('savedCardCounter').innerHTML = '';
            }
        })
        .catch(err => {
            console.error('Load saved error:', err);
            statusDiv.innerHTML = '<i class="fas fa-exclamation-triangle"></i> Error loading saved timetables';
        });
}

function renderCurrentSavedCard() {
    if (savedTimetablesList.length === 0) return;

    const item = savedTimetablesList[currentSavedIndex];
    const counterDiv = document.getElementById('savedCardCounter');
    const detailsDiv = document.getElementById('savedDetailsCard');
    const previewDiv = document.getElementById('savedMiniTimetable');

    // Update counter
    counterDiv.innerHTML = `<strong>${currentSavedIndex + 1}</strong> of <strong>${savedTimetablesList.length}</strong> saved timetables &nbsp;|&nbsp; Use ← → to navigate`;

    // Show loading in preview
    previewDiv.innerHTML = '<div class="loading-text"><i class="fas fa-spinner fa-spin"></i> Loading preview...</div>';

    // Parse slot IDs
    let slotIds = [];
    try {
        slotIds = typeof item.slot_ids === 'string' ? JSON.parse(item.slot_ids) : item.slot_ids;
    } catch (e) {
        console.error('Parse error:', e);
        previewDiv.innerHTML = '<div class="error-text">Error loading saved data</div>';
        return;
    }

    // Fetch slot details for preview
    fetch('/api/generate/preview-details', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ slot_ids: slotIds })
    })
        .then(r => r.json())
        .then(data => {
            if (data.error) {
                previewDiv.innerHTML = `<div class="error-text">Error: ${data.error}</div>`;
                return;
            }

            const suggestion = data.suggestion;

            // Generate colors
            const COURSE_COLORS = [
                '#90EE90', '#87CEEB', '#FFB6C1', '#DDA0DD', '#F0E68C',
                '#FF7F50', '#00CED1', '#FFE4B5', '#E6E6FA', '#FFDAB9',
                '#DA70D6', '#FFA07A'
            ];
            const courseColors = {};
            let colorIndex = 0;
            suggestion.slots.forEach(slot => {
                if (!courseColors[slot.course_code]) {
                    courseColors[slot.course_code] = COURSE_COLORS[colorIndex % COURSE_COLORS.length];
                    colorIndex++;
                }
            });

            // Render preview grid
            renderMiniTimetable(suggestion, courseColors, 'savedMiniTimetable');

            // Render details card with actions
            const slotLines = suggestion.slots.map(s => {
                const color = courseColors[s.course_code] || '#eee';
                return `<div class="suggestion-slot-line" style="display: flex; align-items: center;">
                    <span style="display: inline-block; width: 12px; height: 12px; background-color: ${color}; border: 1px solid #999; margin-right: 8px;"></span>
                    <span>${s.course_code}: ${s.faculty_name || 'TBA'} (${s.slot_code})</span>
                </div>`;
            }).join('');

            detailsDiv.innerHTML = `
                <div class="suggestion-card" style="border-color: #3498db; background: #f8ffff;">
                    <div class="suggestion-header">
                        <span class="suggestion-rank">${item.name}</span>
                        <span class="suggestion-score">${suggestion.total_credits} cr</span>
                    </div>
                    <div class="suggestion-slots-list">${slotLines}</div>
                    <div class="saved-meta" style="margin-top: 10px; font-size: 11px; color: #888;">
                        Saved on ${new Date(item.created_at).toLocaleDateString()}
                    </div>
                    <div class="suggestion-actions" style="margin-top: 10px;">
                        <button class="btn btn-danger btn-sm" onclick="deleteSavedTimetable('${item.id}')">
                            <i class="fas fa-trash"></i> Delete
                        </button>
                        <button class="btn btn-success" onclick="applySavedTimetable(${JSON.stringify(slotIds)})">
                            <i class="fas fa-check"></i> Apply Timetable
                        </button>
                    </div>
                </div>
            `;
        })
        .catch(err => {
            console.error(err);
            previewDiv.innerHTML = '<div class="error-text">Failed to load preview</div>';
        });
}

function prevSavedTimetable() {
    if (savedTimetablesList.length === 0) return;
    currentSavedIndex = (currentSavedIndex - 1 + savedTimetablesList.length) % savedTimetablesList.length;
    renderCurrentSavedCard();
}

function nextSavedTimetable() {
    if (savedTimetablesList.length === 0) return;
    currentSavedIndex = (currentSavedIndex + 1) % savedTimetablesList.length;
    renderCurrentSavedCard();
}

async function deleteSavedTimetable(id) {
    if (!confirm('Are you sure you want to delete this saved timetable?')) return;

    try {
        const resp = await fetch(`/api/generate/saved/${id}`, { method: 'DELETE' });
        const data = await resp.json();

        if (resp.ok && (data.success || !data.error)) {
            // Updated Logic: Handle both Generator Page (Carousel) and Modal (Index Page)

            const savedViewActive = document.getElementById('savedView') && document.getElementById('savedView').style.display !== 'none';
            const modalActive = document.getElementById('savedTimetablesModal') && document.getElementById('savedTimetablesModal').classList.contains('active');

            // 1. If we are managing a local list (Generator Page Context)
            if (typeof savedTimetablesList !== 'undefined' && Array.isArray(savedTimetablesList)) {
                // Remove from local array
                savedTimetablesList = savedTimetablesList.filter(item => String(item.id) !== String(id));

                // If we are currently viewing the generator saved tab, refresh the view
                if (savedViewActive || document.getElementById('savedView')) {
                    if (savedTimetablesList.length === 0) {
                        // Reload fully if empty to show placeholder states
                        if (typeof loadSavedTimetables === 'function') loadSavedTimetables();
                    } else {
                        // Adjust index if needed
                        if (currentSavedIndex >= savedTimetablesList.length) {
                            currentSavedIndex = savedTimetablesList.length - 1;
                        }
                        // Re-render
                        if (typeof renderCurrentSavedCard === 'function') renderCurrentSavedCard();

                        const statusDiv = document.getElementById('savedGenerationStatus');
                        if (statusDiv) {
                            statusDiv.innerHTML = `<i class="fas fa-check"></i> ${savedTimetablesList.length} saved timetable(s) remaining`;
                        }
                    }
                }
            }

            // 2. If the Modal is open (or we are in a context where the modal list exists) - Index Page Context
            // The modal uses `loadSavedTimetablesList()` which fetches freshly from server.
            // We only need to call it if the function exists and the container exists.
            if (typeof loadSavedTimetablesList === 'function' && document.getElementById('savedTimetablesModalContent')) {
                loadSavedTimetablesList();
            }

        } else {
            alert('Error deleting: ' + (data.error || 'Unknown error'));
        }
    } catch (err) {
        console.error(err);
        alert('Error deleting timetable');
    }
}

function applySavedTimetable(slotIds) {
    if (!confirm('Apply this timetable? This will replace your current registration.')) return;

    // Handle potential string slots or already parsed array
    let slots = slotIds;
    if (typeof slotIds === 'string') {
        try {
            slots = JSON.parse(slotIds);
        } catch (e) {
            console.error("Error parsing slots for apply:", e);
            alert("Error applying: Invalid slot data");
            return;
        }
    }

    fetch('/api/generate/apply', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ slot_ids: slots })
    })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                alert('Timetable applied successfully!');
                window.location.href = '/'; // Go to home to see it
            } else {
                alert('Error applying timetable: ' + data.error);
            }
        })
        .catch(err => alert('Apply failed: ' + err));
}



function previewSavedTimetable(item) {
    const container = document.getElementById('savedMiniTimetable');

    // Highlight active card
    document.querySelectorAll('.saved-card').forEach(c => c.classList.remove('active-preview'));
    const card = document.getElementById(`saved-${item.id}`);
    if (card) card.classList.add('active-preview');

    container.innerHTML = '<div class="loading-text"><i class="fas fa-spinner fa-spin"></i> Loading details...</div>';

    // Parse slot IDs if string
    let slotIds = [];
    try {
        if (typeof item.slot_ids === 'string') {
            slotIds = JSON.parse(item.slot_ids);
        } else if (Array.isArray(item.slot_ids)) {
            slotIds = item.slot_ids;
        }
    } catch (e) {
        console.error("Error parsing slot_ids:", e);
        container.innerHTML = '<div class="error-text">Error processing saved data.</div>';
        return;
    }

    // Scroll to top to see preview
    container.scrollIntoView({ behavior: 'smooth', block: 'center' });

    // Fetch details
    fetch('/api/generate/preview-details', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ slot_ids: slotIds })
    })
        .then(r => r.json())
        .then(data => {
            if (data.error) {
                container.innerHTML = `<div class="error-text">Error: ${data.error}</div>`;
                return;
            }

            const suggestion = data.suggestion;

            // Generate colors locally
            const COURSE_COLORS = [
                '#90EE90', '#87CEEB', '#FFB6C1', '#DDA0DD', '#F0E68C',
                '#FF7F50', '#00CED1', '#FFE4B5', '#E6E6FA', '#FFDAB9',
                '#DA70D6', '#FFA07A'
            ];
            const courseColors = {};
            let colorIndex = 0;

            suggestion.slots.forEach(slot => {
                if (!courseColors[slot.course_code]) {
                    courseColors[slot.course_code] = COURSE_COLORS[colorIndex % COURSE_COLORS.length];
                    colorIndex++;
                }
            });

            // Render into the new container
            const returnedColors = renderMiniTimetable(suggestion, courseColors, 'savedMiniTimetable');

            // Render details card
            renderSuggestionCard(suggestion, -1, 'savedDetailsCard', {
                title: item.name || 'Saved Timetable',
                showActions: false, // Actions are already on the card below
                style: 'border-color: #3498db; background: #f8ffff;'
            });

            // Add Apply button below preview? 
            // Or just let the user use the Apply button on the card.
            // The user said "open same as preview page". Preview page has apply buttons on cards.
            // So no need for extra apply button here.
        })
        .catch(err => {
            console.error(err);
            container.innerHTML = '<div class="error-text">Failed to load preview details.</div>';
        });
}

// ==================== HTML Import Modal ====================

function openHtmlImportModal() {
    document.getElementById('htmlImportModal').classList.add('active');
    // Reset state
    document.getElementById('htmlFileInput').value = '';
    document.getElementById('selectedFileName').textContent = '';
    document.getElementById('uploadStatus').innerHTML = '';
    document.getElementById('importBtn').disabled = true;
}

function closeHtmlImportModal() {
    document.getElementById('htmlImportModal').classList.remove('active');
}

let selectedCsvFile = null;

function openCsvUploadModal() {
    document.getElementById('csvUploadModal').classList.add('active');
    // Reset state
    selectedCsvFile = null;
    document.getElementById('csvFileInput').value = '';
    document.getElementById('csvFileName').textContent = '';
    document.getElementById('csvUploadStatus').innerHTML = '';
    document.getElementById('csvImportBtn').disabled = true;
}

function closeCsvUploadModal() {
    document.getElementById('csvUploadModal').classList.remove('active');
    selectedCsvFile = null;
}

// Set up CSV file input listener
document.addEventListener('DOMContentLoaded', function () {
    const csvInput = document.getElementById('csvFileInput');
    if (csvInput) {
        csvInput.addEventListener('change', handleCsvFileSelect);
    }
});

function handleCsvFileSelect(event) {
    const file = event.target.files[0];
    const fileNameSpan = document.getElementById('csvFileName');
    const importBtn = document.getElementById('csvImportBtn');
    const statusDiv = document.getElementById('csvUploadStatus');

    if (file) {
        if (!file.name.toLowerCase().endsWith('.csv')) {
            statusDiv.innerHTML = '<div class="import-error-item"><i class="fas fa-times"></i> Please select a CSV file.</div>';
            importBtn.disabled = true;
            return;
        }

        selectedCsvFile = file;
        fileNameSpan.textContent = file.name;
        importBtn.disabled = false;
        statusDiv.innerHTML = '<div class="import-success-item"><i class="fas fa-check"></i> File ready for import.</div>';
    } else {
        selectedCsvFile = null;
        fileNameSpan.textContent = '';
        importBtn.disabled = true;
    }
}

async function importCsvFile() {
    if (!selectedCsvFile) {
        alert('Please select a CSV file first.');
        return;
    }

    const statusDiv = document.getElementById('csvUploadStatus');
    const importBtn = document.getElementById('csvImportBtn');

    importBtn.disabled = true;
    statusDiv.innerHTML = '<div class="loading-spinner"></div> Importing course data...';

    const formData = new FormData();
    formData.append('files[]', selectedCsvFile);

    try {
        const response = await fetch('/api/upload/import', {
            method: 'POST',
            body: formData
        });

        const data = await response.json();

        if (response.ok && data.success) {
            const result = data.results[0];

            if (result.status === 'success') {
                statusDiv.innerHTML = `
                    <div class="import-success">
                        <i class="fas fa-check-circle"></i>
                        <div>
                            <strong>Course imported successfully!</strong><br>
                            ${result.course_code} - ${result.slots_added} faculty/slot options added
                        </div>
                    </div>
                `;

                // Clear and reload after a delay
                setTimeout(() => {
                    closeCsvUploadModal();
                    location.reload();
                }, 1500);
            } else {
                statusDiv.innerHTML = `<div class="import-error-item"><i class="fas fa-times"></i> ${result.message}</div>`;
                importBtn.disabled = false;
            }
        } else {
            statusDiv.innerHTML = `<div class="import-error-item"><i class="fas fa-times"></i> ${data.error || 'Import failed'}</div>`;
            importBtn.disabled = false;
        }

    } catch (error) {
        console.error('CSV Import error:', error);
        statusDiv.innerHTML = '<div class="import-error-item"><i class="fas fa-times"></i> Error importing CSV file.</div>';
        importBtn.disabled = false;
    }
}

// ==================== OCR Import Modal ====================

let ocrCurrentStep = 1;
let ocrImages = [];
let ocrExtractedData = [];
let ocrImportMode = 'ai'; // 'ai', 'manual', 'edit'
let currentEditingCourseId = null;


function openOcrImportModal() {
    ocrImportMode = 'ai';
    document.querySelector('#ocrImportModal h2').innerHTML = '<i class="fas fa-robot"></i> AI-Assisted Course Import';

    // Update UI for AI mode
    document.getElementById('ocrStep2Label').textContent = 'AI Extract';
    document.getElementById('ocrStep1NextBtn').innerHTML = 'Next: Upload Images <i class="fas fa-arrow-right"></i>';

    document.getElementById('ocrImportModal').classList.add('active');
    resetOcrModal();
}

function openManualImportModal() {
    ocrImportMode = 'manual';
    document.querySelector('#ocrImportModal h2').innerHTML = '<i class="fas fa-keyboard"></i> Manual Course Entry';

    // Update UI for Manual mode
    document.getElementById('ocrStep2Label').textContent = 'Manual Entry';
    document.getElementById('ocrStep1NextBtn').innerHTML = 'Next: Enter Data <i class="fas fa-arrow-right"></i>';

    document.getElementById('ocrImportModal').classList.add('active');
    resetOcrModal();
}

function closeOcrImportModal() {
    document.getElementById('ocrImportModal').classList.remove('active');
    resetOcrModal();
}

function openModifyCoursesModal() {
    const modal = document.getElementById('allCoursesModal');
    const content = document.getElementById('allCoursesModalContent');

    // Change modal title temporarily (or permanent if generic)
    // We can assume the modal header is static "Select Course", but let's check index.html
    // For now, I'll update the content title inside the list if possible or just use the existing modal

    // Reusing All Courses Modal Logic but with Edit Actions
    content.innerHTML = '<div class="loading-spinner"></div>';
    modal.classList.add('active');

    fetch('/api/courses/all')
        .then(r => r.json())
        .then(data => {
            if (data.courses.length === 0) {
                content.innerHTML = '<div class="no-results"><p>No courses found.</p></div>';
                return;
            }

            content.innerHTML = `
                <div style="margin-bottom: 16px;">
                    <h3>Select a Course to Modify</h3>
                    <p style="color: var(--text-muted); font-size: 0.9rem;">Click 'Edit' to modify faculty and slots.</p>
                </div>
                <div style="max-height: 60vh; overflow-y: auto;">
                <table class="registered-table">
                    <thead>
                        <tr>
                            <th>Code</th>
                            <th>Name</th>
                            <th>Target</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${data.courses.map(course => `
                            <tr>
                                <td><strong>${course.code}</strong></td>
                                <td>${course.name}</td>
                                <td>
                                    <button class="btn btn-sm btn-primary" onclick="loadCourseForEdit('${course.id}', '${course.code}', '${course.name}', ${course.l}, ${course.t}, ${course.p}, ${course.j}, ${course.c}, '${course.course_type}', '${course.category}')">
                                        <i class="fas fa-edit"></i> Edit
                                    </button>
                                </td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
                </div>
            `;
        })
        .catch(err => {
            console.error(err);
            content.innerHTML = '<p>Error loading courses.</p>';
        });
}

async function loadCourseForEdit(id, code, name, l, t, p, j, c, type, cat) {
    // Close selection modal
    document.getElementById('allCoursesModal').classList.remove('active');

    // Set Edit Mode
    ocrImportMode = 'edit';
    currentEditingCourseId = id;

    // Pre-fill Step 1 Form (disabled or readonly? User might want to edit meta too? For now, allowing edit)
    document.getElementById('ocrCourseCode').value = code;
    document.getElementById('ocrCourseName').value = name;
    document.getElementById('ocrL').value = l;
    document.getElementById('ocrT').value = t;
    document.getElementById('ocrP').value = p;
    document.getElementById('ocrJ').value = j;
    document.getElementById('ocrC').value = c;
    document.getElementById('ocrCourseType').value = type;
    document.getElementById('ocrCategory').value = cat;

    // Fetch Slots
    try {
        const response = await fetch(`/api/courses/${id}/slots`);
        const data = await response.json();

        // Populate ocrExtractedData
        ocrExtractedData = data.slots.map(s => ({
            slot_code: s.slot_code,
            venue: s.venue,
            faculty: s.faculty_name || 'N/A',
            available_seats: s.available_seats
        }));

        // Open Editor (Step 3)
        document.getElementById('ocrImportModal').classList.add('active');
        ocrCurrentStep = 3;
        updateOcrStepUI();

        // Update UI Text
        document.querySelector('#ocrImportModal h2').innerHTML = '<i class="fas fa-edit"></i> Modify Course Details';
        document.getElementById('ocrStep2Label').textContent = 'Data Loaded'; // Contextual label

        const impBtn = document.getElementById('ocrImportBtn');
        impBtn.innerHTML = '<i class="fas fa-save"></i> Save Changes';
        impBtn.onclick = null; // Clear old listener to be safe, but we use mode check inside function
        // Actually, better to rely on inside check or simple override
        // logic below handles it

        populateOcrReviewTable();

    } catch (e) {
        alert('Error loading course details');
        console.error(e);
    }
}

function resetOcrModal() {
    ocrCurrentStep = 1;
    ocrImages = [];
    ocrExtractedData = [];

    // Reset form
    document.getElementById('ocrCourseCode').value = '';
    document.getElementById('ocrCourseName').value = '';
    document.getElementById('ocrCourseType').value = 'LTP';
    document.getElementById('ocrCategory').value = 'PC';
    document.getElementById('ocrL').value = '2';
    document.getElementById('ocrT').value = '1';
    document.getElementById('ocrP').value = '1';
    document.getElementById('ocrJ').value = '0';
    document.getElementById('ocrC').value = '4';

    // Reset image previews
    document.getElementById('ocrImagePreviews').innerHTML = '';
    document.getElementById('ocrProcessBtn').disabled = true;
    document.getElementById('ocrProgress').style.display = 'none';

    // Reset review table
    document.getElementById('ocrReviewBody').innerHTML = '';
    document.getElementById('ocrReviewStatus').innerHTML = '';

    updateOcrStepUI();
}

function updateOcrStepUI() {
    // Update step indicators
    document.querySelectorAll('.ocr-steps .ocr-step').forEach(step => {
        const stepNum = parseInt(step.dataset.step);
        step.classList.remove('active', 'completed');
        if (stepNum < ocrCurrentStep) step.classList.add('completed');
        if (stepNum === ocrCurrentStep) step.classList.add('active');
    });

    // Show/hide step content
    document.querySelectorAll('.ocr-step-content').forEach((content, i) => {
        content.classList.toggle('active', i + 1 === ocrCurrentStep);
    });
}

function ocrNextStep(step) {
    // Validate before proceeding
    if (ocrCurrentStep === 1) {
        const code = document.getElementById('ocrCourseCode').value.trim();
        const name = document.getElementById('ocrCourseName').value.trim();
        if (!code || !name) {
            alert('Please enter Course Code and Course Name.');
            return;
        }

        // If manual mode, skip step 2 and go straight to 3
        if (ocrImportMode === 'manual') {
            ocrCurrentStep = 3;
            updateOcrStepUI();

            // Initialize with an empty row if empty
            const reviewBody = document.getElementById('ocrReviewBody');
            if (reviewBody.children.length === 0 || reviewBody.innerText.includes('No data')) {
                reviewBody.innerHTML = '';
                ocrAddRow(); // Add first empty row
            }
            return;
        }
    }

    ocrCurrentStep = step;
    updateOcrStepUI();
}

function ocrPrevStep(step) {
    // If manual mode and coming back from step 3, go to step 1
    if (ocrImportMode === 'manual' && ocrCurrentStep === 3) {
        ocrCurrentStep = 1;
        updateOcrStepUI();
        return;
    }

    ocrCurrentStep = step;
    updateOcrStepUI();
}

// AI-based extraction replaces image upload - no OCR handlers needed

// Copy AI prompt to clipboard
// Copy AI prompt to clipboard
function copyAiPrompt() {
    const promptText = document.getElementById('aiPromptText').textContent;
    const btn = event.target.closest('button');

    // Helper to show success message
    const showSuccess = () => {
        const originalHtml = btn.innerHTML;
        btn.innerHTML = '<i class="fas fa-check"></i> Copied!';
        setTimeout(() => {
            btn.innerHTML = originalHtml;
        }, 2000);
    };

    // Try modern Clipboard API
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(promptText).then(showSuccess).catch(err => {
            console.warn('Clipboard API failed, trying fallback:', err);
            fallbackCopy(promptText, showSuccess);
        });
    } else {
        fallbackCopy(promptText, showSuccess);
    }
}

function fallbackCopy(text, successCallback) {
    const textArea = document.createElement("textarea");
    textArea.value = text;
    textArea.style.position = "fixed";  // Avoid scrolling to bottom
    textArea.style.opacity = "0";
    document.body.appendChild(textArea);
    textArea.focus();
    textArea.select();

    try {
        const successful = document.execCommand('copy');
        if (successful) {
            successCallback();
        } else {
            alert('Failed to copy. Please select and copy manually.');
        }
    } catch (err) {
        console.error('Fallback copy error:', err);
        alert('Failed to copy. Please select and copy manually.');
    }

    document.body.removeChild(textArea);
}

// Parse AI output (CSV format)
function parseAiOutput() {
    const textarea = document.getElementById('aiOutputText');
    const statusDiv = document.getElementById('aiParseStatus');
    const text = textarea.value.trim();

    if (!text) {
        statusDiv.innerHTML = '<div class="import-error-item"><i class="fas fa-exclamation-circle"></i> Please paste the AI output first.</div>';
        return;
    }

    const rows = [];
    const lines = text.split('\n').map(l => l.trim()).filter(l => l.length > 0);

    for (const line of lines) {
        // Skip header line
        if (line.toLowerCase().includes('slot_code') && line.toLowerCase().includes('venue')) continue;
        if (line.toLowerCase().includes('faculty') && line.toLowerCase().includes('seats')) continue;

        // Try to parse as CSV
        const parts = line.split(',').map(p => p.trim());

        if (parts.length >= 4) {
            const slot = parts[0];
            const venue = parts[1];
            const faculty = parts[2];
            const seats = parseInt(parts[3]) || 70;

            // Validate slot format (should contain letter+number pattern)
            if (/[A-Za-z]\d/.test(slot) && faculty.length >= 2) {
                rows.push({
                    slot_code: slot.toUpperCase(),
                    venue: venue.toUpperCase(),
                    faculty: faculty,
                    available_seats: seats
                });
            }
        }
    }

    if (rows.length === 0) {
        statusDiv.innerHTML = '<div class="import-error-item"><i class="fas fa-exclamation-circle"></i> Could not parse any valid rows. Make sure the AI output is in CSV format.</div>';
        return;
    }

    // Store and move to review
    ocrExtractedData = rows;
    statusDiv.innerHTML = `<div class="import-success"><i class="fas fa-check-circle"></i> Parsed ${rows.length} faculty entries!</div>`;

    setTimeout(() => {
        populateOcrReviewTable();
        ocrNextStep(3);
    }, 500);
}



function populateOcrReviewTable() {
    const tbody = document.getElementById('ocrReviewBody');

    if (ocrExtractedData.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: #999;">No data extracted. Please add rows manually or try different images.</td></tr>';
        return;
    }

    tbody.innerHTML = ocrExtractedData.map((row, i) => `
        <tr data-index="${i}">
            <td><input type="checkbox" class="ocr-row-select"></td>
            <td><input type="text" value="${escapeHtml(row.slot_code)}" class="ocr-slot"></td>
            <td><input type="text" value="${escapeHtml(row.venue)}" class="ocr-venue"></td>
            <td><input type="text" value="${escapeHtml(row.faculty)}" class="ocr-faculty"></td>
            <td><input type="number" value="${row.available_seats}" class="ocr-seats" min="0"></td>
            <td><button class="delete-row-btn" onclick="ocrDeleteRow(this)"><i class="fas fa-trash"></i></button></td>
        </tr>
    `).join('');
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function ocrAddRow() {
    const tbody = document.getElementById('ocrReviewBody');
    const newIndex = tbody.querySelectorAll('tr').length;

    const row = document.createElement('tr');
    row.dataset.index = newIndex;
    row.innerHTML = `
        <td><input type="checkbox" class="ocr-row-select"></td>
        <td><input type="text" value="" class="ocr-slot" placeholder="e.g., A11+A12+A13"></td>
        <td><input type="text" value="TBA" class="ocr-venue"></td>
        <td><input type="text" value="" class="ocr-faculty" placeholder="Faculty Name"></td>
        <td><input type="number" value="70" class="ocr-seats" min="0"></td>
        <td><button class="delete-row-btn" onclick="ocrDeleteRow(this)"><i class="fas fa-trash"></i></button></td>
    `;
    tbody.appendChild(row);
}

function ocrDeleteRow(btn) {
    btn.closest('tr').remove();
}

function ocrToggleSelectAll() {
    const checked = document.getElementById('ocrSelectAll').checked;
    document.querySelectorAll('.ocr-row-select').forEach(cb => cb.checked = checked);
}

function ocrDeleteSelectedRows() {
    document.querySelectorAll('.ocr-row-select:checked').forEach(cb => {
        cb.closest('tr').remove();
    });
    document.getElementById('ocrSelectAll').checked = false;
}

async function importOcrData() {
    if (ocrImportMode === 'edit') {
        saveCourseEdits();
        return;
    }

    const statusDiv = document.getElementById('ocrReviewStatus');
    const importBtn = document.getElementById('ocrImportBtn');

    // Collect course data
    const courseData = {
        course_code: document.getElementById('ocrCourseCode').value.trim(),
        course_name: document.getElementById('ocrCourseName').value.trim(),
        l: parseInt(document.getElementById('ocrL').value) || 0,
        t: parseInt(document.getElementById('ocrT').value) || 0,
        p: parseInt(document.getElementById('ocrP').value) || 0,
        j: parseInt(document.getElementById('ocrJ').value) || 0,
        c: parseInt(document.getElementById('ocrC').value) || 0,
        course_type: document.getElementById('ocrCourseType').value,
        category: document.getElementById('ocrCategory').value
    };

    // Collect slot data from table
    const rows = document.querySelectorAll('#ocrReviewBody tr');
    const slots = [];

    rows.forEach(row => {
        const slot = row.querySelector('.ocr-slot')?.value.trim();
        const venue = row.querySelector('.ocr-venue')?.value.trim() || 'TBA';
        const faculty = row.querySelector('.ocr-faculty')?.value.trim();
        const seats = parseInt(row.querySelector('.ocr-seats')?.value) || 70;

        if (slot && faculty) {
            slots.push({
                slot_code: slot,
                venue: venue,
                faculty: faculty,
                available_seats: seats
            });
        }
    });

    if (slots.length === 0) {
        alert('Please add at least one valid slot entry.');
        return;
    }

    importBtn.disabled = true;
    statusDiv.innerHTML = '<div class="loading-spinner"></div> Importing course data...';

    // Build CSV content
    const csvLines = [
        'course_code,course_name,l,t,p,j,c,course_type,category',
        `${courseData.course_code},${courseData.course_name},${courseData.l},${courseData.t},${courseData.p},${courseData.j},${courseData.c},${courseData.course_type},${courseData.category}`,
        'slot_code,faculty,venue,available_seats'
    ];

    slots.forEach(s => {
        csvLines.push(`${s.slot_code},${s.faculty},${s.venue},${s.available_seats}`);
    });

    const csvContent = csvLines.join('\n');

    // Create a blob and send as file
    const blob = new Blob([csvContent], { type: 'text/csv' });
    const formData = new FormData();
    formData.append('files[]', blob, `${courseData.course_code}.csv`);

    try {




        const response = await fetch('/api/upload/import', {
            method: 'POST',
            body: formData
        });

        const data = await response.json();

        if (response.ok && data.success) {
            const result = data.results[0];

            if (result.status === 'success') {
                statusDiv.innerHTML = `
                    <div class="import-success">
                        <i class="fas fa-check-circle"></i>
                        <div>
                            <strong>Course imported successfully!</strong><br>
                            ${result.course_code} - ${result.slots_added} faculty/slot options added
                        </div>
                    </div>
                `;

                setTimeout(() => {
                    closeOcrImportModal();
                    location.reload();
                }, 1500);
            } else {
                statusDiv.innerHTML = `<div class="import-error-item"><i class="fas fa-times"></i> ${result.message}</div>`;
                importBtn.disabled = false;
            }
        } else {
            statusDiv.innerHTML = `<div class="import-error-item"><i class="fas fa-times"></i> ${data.error || 'Import failed'}</div>`;
            importBtn.disabled = false;
        }

    } catch (error) {
        console.error('Import error:', error);
        statusDiv.innerHTML = '<div class="import-error-item"><i class="fas fa-times"></i> Error importing data.</div>';
        importBtn.disabled = false;
    }
}

async function saveCourseEdits() {
    const statusDiv = document.getElementById('ocrReviewStatus');
    const importBtn = document.getElementById('ocrImportBtn');

    // Collect slot data
    const rows = document.querySelectorAll('#ocrReviewBody tr');
    const slots = [];

    rows.forEach(row => {
        const slot = row.querySelector('.ocr-slot')?.value.trim();
        const venue = row.querySelector('.ocr-venue')?.value.trim() || 'TBA';
        const faculty = row.querySelector('.ocr-faculty')?.value.trim();
        const seats = parseInt(row.querySelector('.ocr-seats')?.value) || 70;

        if (slot && faculty) {
            slots.push({
                slot_code: slot,
                venue: venue,
                faculty: faculty,
                available_seats: seats
            });
        }
    });

    if (slots.length === 0 && !confirm('No slots defined. This will clear all slots for the course. Continue?')) {
        return;
    }

    importBtn.disabled = true;
    statusDiv.innerHTML = '<div class="loading-spinner"></div> Saving changes...';

    try {
        const response = await fetch(`/api/courses/${currentEditingCourseId}/sync`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ slots: slots })
        });

        const data = await response.json();

        if (response.ok) {
            statusDiv.innerHTML = `<div class="import-success"><i class="fas fa-check"></i> ${data.message}</div>`;
            setTimeout(() => {
                location.reload();
            }, 1000);
        } else {
            statusDiv.innerHTML = `<div class="import-error-item">Error: ${data.error}</div>`;
            importBtn.disabled = false;
        }
    } catch (e) {
        console.error(e);
        statusDiv.innerHTML = `<div class="import-error-item">Error saving changes.</div>`;
        importBtn.disabled = false;
    }
}

// --- Timetable Saving & Viewing ---

async function saveCurrentTimetable() {
    try {
        // Fetch current registrations
        const regResp = await fetch('/api/registration/');
        const regData = await regResp.json();

        if (!regData.registrations || regData.registrations.length === 0) {
            alert('Your timetable is empty! Register some courses before saving.');
            return;
        }

        const slotIds = regData.registrations.map(r => r.slot.id);
        const count = regData.count;
        const credits = regData.total_credits;

        // Prompt for name
        let name = prompt("Enter a name for this timetable:", `My Timetable (${new Date().toLocaleDateString()})`);
        if (name === null) return; // Cancelled
        if (!name.trim()) name = "Untitled Timetable";

        // Save
        const saveResp = await fetch('/api/generate/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: name,
                slot_ids: slotIds,
                total_credits: credits,
                course_count: count
            })
        });

        const data = await saveResp.json();
        if (saveResp.ok) {
            alert('Timetable saved successfully!');
        } else {
            alert(data.message || 'Error saving timetable');
        }
    } catch (err) {
        console.error('Save error:', err);
        alert('Failed to save timetable.');
    }
}

function openSavedTimetablesModal() {
    document.getElementById('savedTimetablesModal').classList.add('active');
    loadSavedTimetablesList();
}

function closeSavedTimetablesModal() {
    document.getElementById('savedTimetablesModal').classList.remove('active');
}

async function loadSavedTimetablesList() {
    const container = document.getElementById('savedTimetablesModalContent');
    if (!container) return; // Prevent error if modal is not present (e.g. on generator page)

    container.innerHTML = '<div class="loading-spinner"><i class="fas fa-spinner fa-spin"></i> Loading...</div>';

    try {
        const resp = await fetch('/api/generate/saved');
        const data = await resp.json();

        if (!data.saved || data.saved.length === 0) {
            container.innerHTML = '<p class="empty-state">No saved timetables found.</p>';
            return;
        }

        container.innerHTML = data.saved.map(item => `
            <div class="saved-card">
                <div class="saved-card-header">
                    <h3>${item.name}</h3>
                    <span class="saved-date">${new Date(item.created_at).toLocaleDateString()}</span>
                </div>
                <div class="saved-card-body">
                    <p><i class="fas fa-book"></i> ${item.course_count} Courses</p>
                    <p><i class="fas fa-star"></i> ${item.total_credits} Credits</p>
                </div>
                <div class="saved-card-actions">
                    <button class="btn btn-sm btn-success" onclick='applySavedTimetable(${JSON.stringify(item.slot_ids)})'>
                        <i class="fas fa-upload"></i> Load
                    </button>
                    <button class="btn btn-sm btn-danger" onclick="deleteSavedTimetable('${item.id}')">
                        <i class="fas fa-trash"></i> Delete
                    </button>
                </div>
            </div>
        `).join('');

    } catch (err) {
        console.error('List error:', err);
        container.innerHTML = '<p class="error-msg">Error loading saved timetables.</p>';
    }
}

// Duplicate deleteSavedTimetable removed. The unified version is defined earlier.
