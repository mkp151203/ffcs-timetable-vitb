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
                    <i class="fas fa-trash"></i> Remove Selected (<span id="selectedRegCount">0</span>)
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

function switchGenerateTab(tabName) {
    generateActiveTab = tabName;

    // Update tab styles
    document.querySelectorAll('.pref-tab').forEach(t => t.classList.remove('active'));
    document.querySelector(`.pref-tab[data-tab="${tabName}"]`).classList.add('active');

    // Show content
    document.querySelectorAll('.pref-tab-content').forEach(c => c.classList.remove('active'));
    document.getElementById(`prefTabContent-${tabName}`).classList.add('active');

    // Special handling
    if (tabName === 'custom') {
        populateTeacherPreferences();
    }
}

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

        // If Custom tab is active, we need to populate/refresh the teacher preferences 
        // because they depend on the courses selected in Step 1.
        if (generateActiveTab === 'custom') {
            populateTeacherPreferences();
        }
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

    // If Random tab is active, we ignore specific prefs (except maybe avoids? User said "very random"...)
    // Actually, user said "for random generate 100 very random".
    if (generateActiveTab === 'random') {
        // Keep defaults (none/empty), maybe clear avoids too?
        // "generate 100 very random timetables" usually implies no constraints.
        // But if user explicitly checked 'Avoid' in the other tab, should we respect it?
        // UI hides them in Random tab (removed in step 953). So effectively unchecked defaults?
        // Wait, checkboxes are in Time tab only now.
        // Let's reset avoids for Random mode to be purely random.
        generatePreferences.avoid_early_morning = false;
        generatePreferences.avoid_late_evening = false;
        generatePreferences.time_mode = 'none';

    } else {
        // For Time OR Teacher tab, we collect BOTH to allow "Comprehensive Scoring".

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
    }

    generateCurrentStep = 3;
    generateCurrentOffset = 0;
    generateSuggestions = [];
    updateGenerateStepUI();

    const statusDiv = document.getElementById('generationStatus');
    statusDiv.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Generating timetables...';
    document.getElementById('suggestionsList').innerHTML = '';
    document.getElementById('loadMoreContainer').style.display = 'none';

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
    const listDiv = document.getElementById('suggestionsList');

    listDiv.innerHTML = generateSuggestions.map((suggestion, index) => `
        <div class="suggestion-card" onclick="previewSuggestion(${index})">
            <div class="suggestion-header">
                <span class="suggestion-rank">#${index + 1}</span>
                <span class="suggestion-score">Score: ${suggestion.score}</span>
            </div>
            <div class="suggestion-slots">
                ${suggestion.slots.map(s => `
                    <span class="suggestion-slot-tag">
                        ${s.course_code}: ${s.slot_code}
                    </span>
                `).join('')}
            </div>
            <div class="suggestion-details">
                <strong>${suggestion.total_credits} credits</strong> | 
                ${suggestion.details.preferred_faculty_matches || 0} preferred faculty | 
                ${suggestion.details.saturday_classes || 0} Saturday classes
            </div>
            <div class="suggestion-actions">
                <button class="btn btn-primary btn-sm" onclick="event.stopPropagation(); generateSimilar(${index})" title="Find variations of this timetable">
                    <i class="fas fa-copy"></i> More Like This
                </button>
                <button class="btn btn-success btn-sm" onclick="event.stopPropagation(); applySuggestion(${index})">
                    <i class="fas fa-check"></i> Apply
                </button>
            </div>
        </div>
    `).join('');
}

async function generateSimilar(index) {
    const suggestion = generateSuggestions[index];
    if (!suggestion) return;

    const statusDiv = document.getElementById('generationStatus');
    statusDiv.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Finding similar timetables...';

    const referenceSlotIds = suggestion.slots.map(s => s.slot_id);

    try {
        const response = await fetch('/api/generate/similar', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                course_ids: generateSelectedCourseIds,
                reference_slot_ids: referenceSlotIds,
                preferences: generatePreferences
            })
        });

        const data = await response.json();

        if (!response.ok) {
            statusDiv.innerHTML = `<i class="fas fa-exclamation-circle"></i> Error: ${data.error}`;
            return;
        }

        if (data.suggestions.length === 0) {
            statusDiv.innerHTML = '<i class="fas fa-info-circle"></i> No similar variations found. This timetable might be unique!';
            return;
        }

        // Add new suggestions (avoiding duplicates)
        const existingIds = new Set(generateSuggestions.map(s =>
            s.slots.map(sl => sl.slot_id).sort().join(',')
        ));

        let addedCount = 0;
        data.suggestions.forEach(newSugg => {
            const newId = newSugg.slots.map(sl => sl.slot_id).sort().join(',');
            if (!existingIds.has(newId)) {
                generateSuggestions.push(newSugg);
                existingIds.add(newId);
                addedCount++;
            }
        });

        statusDiv.innerHTML = `<i class="fas fa-check-circle"></i> Found ${addedCount} similar variations! Total: ${generateSuggestions.length} options`;
        renderSuggestions();

        // Preview the first new one
        if (addedCount > 0) {
            previewSuggestion(generateSuggestions.length - addedCount);
        }

    } catch (error) {
        console.error('Similar generation error:', error);
        statusDiv.innerHTML = '<i class="fas fa-exclamation-circle"></i> Error finding similar timetables.';
    }
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

function renderMiniTimetable(suggestion) {
    const container = document.getElementById('miniTimetablePreview');
    if (!container) return;

    // Define the grid structure
    const days = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'];
    const periods = [
        { num: 1, time: '8:30-10:00' },
        { num: 2, time: '10:05-11:35' },
        { num: 3, time: '11:40-13:10' },
        { num: 4, time: '13:15-14:45' },
        { num: 5, time: '14:50-16:20' },
        { num: 6, time: '16:25-17:55' },
        { num: 7, time: '18:00-19:30' }
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
                    venue: slotInfo.venue || ''
                };
            }
        });
    });

    // Generate HTML - Days as rows, Times as columns
    let html = '<table class="mini-timetable"><thead><tr><th class="day-header">Day</th>';
    periods.forEach(p => html += `<th class="time-col">${p.time}</th>`);
    html += '</tr></thead><tbody>';

    days.forEach(d => {
        html += `<tr><td class="day-header">${d}</td>`;
        periods.forEach(p => {
            const cell = grid[d][p.num];
            if (cell) {
                html += `<td class="slot-filled" title="${cell.code} - ${cell.venue}">${cell.code}</td>`;
            } else {
                html += '<td class="slot-empty"></td>';
            }
        });
        html += '</tr>';
    });

    html += '</tbody></table>';
    html += `<div class="preview-info">
        <span class="credits-badge">${suggestion.total_credits} Credits</span>
        <span style="margin-left: 10px;">Score: ${suggestion.score}</span>
    </div>`;

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

    previewSuggestion(newIndex);

    // Scroll the active card into view
    const activeCard = document.querySelector('.suggestion-card.active-preview');
    if (activeCard) {
        activeCard.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
}

// Arrow key and Enter navigation for previews
document.addEventListener('keydown', function (e) {
    // Only handle keys when generate modal is open and we're on step 3 (results)
    const modal = document.getElementById('generateModal');
    if (!modal || !modal.classList.contains('active') || generateCurrentStep !== 3) {
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
            closeGenerateModal();
            location.reload();
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

