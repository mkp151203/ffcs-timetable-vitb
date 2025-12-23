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

            return `
                <tr class="${isFull ? 'clash-row' : ''}" data-slot-id="${slot.id}">
                    <td class="slot-code">${slot.slot_code}</td>
                    <td class="venue">${slot.venue}</td>
                    <td class="faculty-name">${slot.faculty_name || 'TBA'}</td>
                    <td class="clash-status" id="clash-${slot.id}"></td>
                    <td>
                        ${isFull ?
                    '<span class="full-label">Full</span>' :
                    `<input type="radio" name="slotSelection" value="${slot.id}" onchange="selectSlot('${slot.id}')">
                             <span class="available-seats ${seatsClass}">${slot.available_seats}</span>`
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
    const checkPromises = slots.map(async (slot) => {
        try {
            const response = await fetch('/api/registration/check-clash', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ slot_id: slot.id })
            });
            const data = await response.json();

            const clashCell = document.getElementById(`clash-${slot.id}`);
            if (clashCell && data.has_clash) {
                clashCell.textContent = data.clashing_slots.map(c => c.course_code).join(', ');
                clashCell.closest('tr').classList.add('clash-row');
            }
        } catch (error) {
            console.error('Error checking clash:', error);
        }
    });

    await Promise.all(checkPromises);
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

    if (currentEditingRegistrationId) {
        await updateRegistration(finalSlotId, currentEditingRegistrationId);
    } else {
        await createNewRegistration(finalSlotId);
    }
}

async function createNewRegistration(slotId) {
    try {
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
        }

    } catch (error) {
        console.error('Registration error details:', error);
        alert('Error registering course: ' + error.message);
    }
}

async function updateRegistration(slotId, regId) {
    try {
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
        }

    } catch (error) {
        console.error('Update error details:', error);
        alert('Error updating registration: ' + error.message);
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
            <table class="registered-courses-table">
                <thead>
                    <tr>
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
                            <td><span class="row-color-indicator" style="background-color: ${color};"></span></td>
                            <td><strong>${courseCode}</strong></td>
                            <td>${reg.slot?.course?.name || 'N/A'}</td>
                            <td>${reg.slot?.slot_code || 'N/A'}</td>
                            <td>${reg.slot?.venue || 'N/A'}</td>
                            <td>${reg.slot?.faculty_name || 'TBA'}</td>
                            <td>${reg.slot?.course?.c || 0}</td>
                            <td>
                                <button class="edit-btn" onclick="openEditRegistrationModal('${reg.slot?.course?.id}', '${reg.id}')" title="Edit Slot/Faculty">
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

// Global variable to track if we are editing a registration
let currentEditingRegistrationId = null;

function openEditRegistrationModal(courseId, registrationId) {
    currentEditingRegistrationId = registrationId;

    // Change modal title temporarily (optional UI tweak)
    // For now re-use existing modal logic
    selectCourse(courseId);
}

// Functions moved to line 184 and following
// Removed duplicates to fix conflicts

function closeCourseModal() {
    document.getElementById('courseModal').classList.remove('active');
    currentEditingRegistrationId = null; // Reset edit mode on close
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
                <p style="margin-bottom: 16px;">Total courses: <strong>${coursesData.courses.length}</strong></p>
                <table class="registered-table">
                    <thead>
                        <tr>
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
                                        <button class="btn btn-danger btn-sm" onclick="deleteCourse('${course.id}', '${course.code}');">
                                            <i class="fas fa-trash"></i>
                                        </button>
                                    </div>
                                </td>
                            </tr>
                        `}).join('')}
                    </tbody>
                </table>
            `;
        })
        .catch(error => {
            console.error('Error:', error);
            content.innerHTML = '<p class="empty-message">Error loading courses.</p>';
        });
}

async function deleteCourse(courseId, courseCode) {
    if (!confirm(`Are you sure you want to delete course ${courseCode}? This will remove it from the list AND your timetable.`)) {
        return;
    }

    try {
        const response = await fetch(`/api/courses/${courseId}`, {
            method: 'DELETE'
        });
        const data = await response.json();

        if (response.ok) {
            alert(data.message);
            // Refresh list and timetable
            viewAllCourses();
            loadRegisteredCoursesList();
            loadRegisteredCoursesList();
            location.reload();
        } else {
            alert(`Error: ${data.error}`);
        }
    } catch (error) {
        console.error('Delete error:', error);
        alert('Error deleting course.');
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

    const uploadPromises = selectedFiles.map(async (file) => {
        const formData = new FormData();
        formData.append('file', file);
        return fetch('/api/upload/import', {
            method: 'POST',
            body: formData
        }).then(async (response) => {
            const data = await response.json();
            if (response.ok) {
                return `<div class="import-success-item"><i class="fas fa-check"></i> ${data.message}</div>`;
            } else {
                return `<div class="import-error-item"><i class="fas fa-times"></i> ${file.name}: ${data.error}</div>`;
            }
        }).catch(error => {
            return `<div class="import-error-item"><i class="fas fa-times"></i> ${file.name}: Error importing</div>`;
        });
    });

    const results = await Promise.all(uploadPromises);
    const successCount = results.filter(r => r.includes('import-success-item')).length;

    statusDiv.innerHTML = `
        <div class="import-results">
            <div class="import-summary">${successCount}/${selectedFiles.length} courses imported</div>
            ${results.join('')}
        </div>
    `;

    // Clear the file input
    document.getElementById('htmlFileInput').value = '';
    document.getElementById('selectedFileName').textContent = '';
    selectedFiles = [];

    // Reload after a moment if any imports succeeded
    if (successCount > 0) {
        setTimeout(() => {
            location.reload();
        }, 2000);
    } else {
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
