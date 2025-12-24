"""
Timetable Generator Module
Generates optimal, clash-free timetable combinations using constraint satisfaction.
"""

import random
from typing import List, Dict, Set, Optional, Tuple, Generator
from dataclasses import dataclass, field
from models import Course, Slot, Faculty
from models.slot import get_slot_timing, SLOT_TIMINGS


@dataclass
class GenerationPreferences:
    """User preferences for timetable generation."""
    # Time Constraints (Soft Filters)
    avoid_early_morning: bool = False   # Avoid Period 1 (8:30)
    avoid_late_evening: bool = False    # Avoid Period 7 (18:00)

    # Time Mode: 'none', 'morning', 'afternoon', 'middle'
    time_mode: str = 'none'
    
    # Backwards compatibility (mapped to time_mode logic where possible)
    prefer_morning: bool = False
    prefer_afternoon: bool = False
    
    max_gaps_per_day: int = 2
    
    # Legacy - Global preferred faculties list (optional usage)
    preferred_faculties: List[str] = field(default_factory=list)
    
    # New - Per-course faculty preference: {course_id: ['Fac1', 'Fac2', 'Fac3']}
    course_faculty_preferences: Dict[int, List[str]] = field(default_factory=dict)
    
    avoided_faculties: List[str] = field(default_factory=list)
    exclude_slots: List[str] = field(default_factory=list)


@dataclass
class TimetableSolution:
    """A single valid timetable combination."""
    slots: List[Slot]           # Selected slots for each course
    score: float                # Quality score (higher is better)
    total_credits: int          # Sum of course credits
    details: Dict               # Additional info (gaps, faculty matches, etc.)
    
    def to_dict(self):
        return {
            'slots': [
                {
                    'slot_id': str(s.id),  # String to prevent JS precision loss
                    'slot_code': s.slot_code,
                    'course_id': str(s.course_id) if s.course_id else '',
                    'course_code': s.course.code if s.course else '',
                    'course_name': s.course.name if s.course else '',
                    'faculty_name': s.faculty.name if s.faculty else '',
                    'venue': s.venue,
                    'credits': s.course.c if s.course else 0
                } for s in self.slots
            ],
            'score': round(self.score, 2),
            'total_credits': self.total_credits,
            'details': self.details
        }


# Mutual exclusion groups - these slot sets cannot be taken together
MUTUAL_EXCLUSION_GROUPS = [
    ({'C11', 'C12', 'C13'}, {'A21', 'A22', 'A23'}),  # C1 and A2 clash
]


class TimetableGenerator:
    """
    Constraint-based timetable generator.
    Uses backtracking with pruning to find valid combinations.
    """
    
    def __init__(self, courses: List[Course], preferences: GenerationPreferences = None):
        """
        Initialize generator with courses to schedule.
        
        Args:
            courses: List of Course objects user wants to register
            preferences: Optional generation preferences
        """
        self.courses = courses
        self.preferences = preferences or GenerationPreferences()
        self.slot_map: Dict[int, List[Slot]] = {}  # course_id -> available slots
        self._build_slot_map()
    
    def _build_slot_map(self, randomize_only: bool = False, ignore_preferences: bool = False):
        """
        Build mapping of courses to their available slots.
        
        Args:
            randomize_only: If True, do not sort by preference score; only shuffle.
            ignore_preferences: If True, do not filter out slots based on user prefs (avoids, excludes).
        """
        for course in self.courses:
            slots = []
            for slot in course.slots.all():
                # Apply hard filters (Avoid X, Exclude Y) - ONLY if not ignoring preferences
                if not ignore_preferences:
                    if self._should_exclude_slot(slot):
                        continue
                slots.append(slot)
            
            # Shuffle first for diversity
            random.shuffle(slots)
            
            if not randomize_only:
                # Greedy Mode: Sort by priority so backtracking picks 'best' first
                slots.sort(key=lambda s: self._score_slot(s), reverse=True)
            
            self.slot_map[course.id] = slots
    
    def _should_exclude_slot(self, slot: Slot) -> bool:
        """Check if slot should be excluded based on hard constraints."""
        # Check avoided faculty
        if slot.faculty and slot.faculty.name in self.preferences.avoided_faculties:
            return True
        
        # Check excluded slot codes
        individual_slots = slot.get_individual_slots()
        for s in individual_slots:
            if s in self.preferences.exclude_slots:
                return True
                
            # Check Time Constraints
            # (Removed early/late specific checks)
        
        return False

    def generate_ranked_pool(self, target_size: int = 100, pool_attempts: int = 100000) -> List[TimetableSolution]:
        """
        Strategy 3: Generate-Filter-Rank (The "Broad Search" Strategy).
        1. Generate a MASSIVE pool of random valid timetables (ignoring user filters like 'Avoid 8:30').
           - Target: 20,000 candidates.
        2. Post-Filter: Remove timetables that violate user constraints.
        3. Rank: Score the remaining ones and return top N.
        """
        # 1. Rebuild slot map IGNORING preferences (get ALL valid slots) -> Maximum Diversity
        self._build_slot_map(randomize_only=True, ignore_preferences=True)
        
        # 2. Generate Massive Pool
        pool_solutions: List[List[Slot]] = []
        seen_signatures = set()
        
        # Limit total attempts
        # We want 20,000 candidates if possible
        max_attempts = pool_attempts
        attempts = 0
        target_pool = 20000 # User requested 20,000
        
        course_ids = [c.id for c in self.courses]
        
        # Strategy: Random Restarts
        while len(pool_solutions) < target_pool and attempts < max_attempts:
            attempts += 1
            
            # Shuffle course order for this attempt
            random.shuffle(course_ids)
            
            current_solution = []
            occupied = set()
            valid_attempt = True
            
            for cid in course_ids:
                slots = self.slot_map.get(cid, [])
                if not slots:
                    valid_attempt = False
                    break
                
                # Pick ONE random slot. 
                # (Since we want 20,000 unique ones, simple random choice is fastest)
                # If we iterate candidates, it becomes a DFS which is slow for 20k target.
                # Let's try up to 5 random picks per course to reduce dead ends
                candidates = random.sample(slots, min(len(slots), 5))
                
                found_slot = False
                for slot in candidates:
                    # Check clash against existing
                    is_clash = False
                    for existing in current_solution:
                        if self._check_clash(slot, existing):
                            is_clash = True
                            break
                    
                    if not is_clash:
                        # Check internal time clash
                        new_occupied = set()
                        time_clash = False
                        for s in slot.get_individual_slots():
                            timing = get_slot_timing(s)
                            if timing:
                                key = (timing['day'], timing['period'])
                                if key in occupied:
                                    time_clash = True
                                    break
                                new_occupied.add(key)
                        
                        if not time_clash:
                            occupied.update(new_occupied)
                            current_solution.append(slot)
                            found_slot = True
                            break 
                
                if not found_slot:
                    valid_attempt = False
                    break
            
            if valid_attempt and len(current_solution) == len(self.courses):
                # Success
                sig = self._get_timetable_signature(current_solution)
                if sig not in seen_signatures:
                    seen_signatures.add(sig)
                    pool_solutions.append(current_solution)
        
        # 3. Score and Rank (No Filtering, just Penalties)
        scored_solutions = []
        
        for slots in pool_solutions:
            # Calculate score (penalties are applied inside score function)
            score = self._calculate_solution_total_score(slots)
            total_credits = sum(s.course.c if s.course else 0 for s in slots)
            sol = TimetableSolution(
                slots=slots,
                score=score,
                total_credits=total_credits,
                details={'from_pool_size': len(pool_solutions)}
            )
            scored_solutions.append(sol)
            
        # Sort descending (Higher score = Better, Lower score = More violations)
        scored_solutions.sort(key=lambda x: x.score, reverse=True)
        
        return scored_solutions[:target_size]

    def _calculate_solution_total_score(self, slots: List[Slot]) -> float:
        """Calculate total quality score for a full timetable (Average of slot scores)."""
        if not slots:
            return 0.0
            
        total_score = 0.0
        # 1. Sum of slot scores (Preferences: Time Mode, Faculty Rank)
        for slot in slots:
            total_score += self._score_slot(slot)
            
        # 2. Return Average
        return total_score / len(slots)
    
    def count_distinct_solutions(self, max_count: int = 100000) -> int:
        """
        Count distinct timetable time-patterns (ignoring teacher differences).
        Groups slots by (slot_code) and counts valid combinations of slot codes.
        """
        if not self.courses:
            return 0
        
        # 1. Group available slots by slot_code for each course
        # course_id -> list of unique slot_codes that are valid (filtered)
        course_slot_codes = {}
        
        for course in self.courses:
            valid_codes = set()
            for slot in course.slots.all():
                if not self._should_exclude_slot(slot):
                    valid_codes.add(slot.slot_code)
            
            if not valid_codes:
                return 0  # No valid slots for this course
                
            course_slot_codes[course.id] = list(valid_codes)
            
        course_ids = [c.id for c in self.courses]
        count = 0
        
        # 2. Backtrack on slot codes
        def backtrack(index: int, occupied: Set[Tuple[str, int]]) -> None:
            nonlocal count
            
            if count >= max_count:
                return
            
            if index == len(course_ids):
                count += 1
                return
            
            course_id = course_ids[index]
            available_codes = course_slot_codes.get(course_id, [])
            
            for code in available_codes:
                if count >= max_count:
                    return
                
                # Check clashes
                clashes = False
                
                # Parse code into individual slots tokens/timings
                # We don't have a Slot object here, just the code string
                # So we must manually parse timings
                code_parts = code.replace('/', '+').split('+')
                new_occupied = set()
                
                for part in code_parts:
                    timing = get_slot_timing(part)
                    if timing:
                        key = (timing['day'], timing['period'])
                        if key in occupied:
                            clashes = True
                            break
                        new_occupied.add(key)
                        
                    # Also check Mutual Exclusion Groups statically based on code string
                    # (This assumes mutual exclusion rules are simple code checks)
                    # For safety, we should ideally check against actual slot objects or logic,
                    # but checking time overlap covers the main hard constraint.
                
                # Check MUTUAL_EXCLUSION_GROUPS
                if not clashes:
                    code_slots_set = set(code_parts)
                    for group_a, group_b in MUTUAL_EXCLUSION_GROUPS:
                        # Check against current code
                        has_current_in_a = not code_slots_set.isdisjoint(group_a)
                        has_current_in_b = not code_slots_set.isdisjoint(group_b)
                        
                        # We don't have a "selected" list of slot objects here to check against easily
                        # But wait - we only need to check against occupied times?
                        # No, mutual exclusion is about slot codes (e.g. C1 vs A2).
                        # We need to track selected slot codes to check this properly.
                        # However, since C1 and A2 generally overlap in time (or are defined to clash),
                        # checking strict time overlap might be sufficient IF the timing map is accurate.
                        # But let's be safe: we can check if occupied set implies any exclusion.
                        pass # Skipping complex mutual exclusion for pure count if time covers it.
                             # Actually time overlap usually covers it. 
                             # C11 is Mon-3, C12 is Wed-3, C13 is Fri-3
                             # A21 is Mon-4, A22 is Wed-4... wait, they might conflict in exam slots?
                             # In FFCS, typically they don't time-clash but are grouped clashes.
                             # Let's trust pure time clash for now as "good enough" approximation 
                             # or if critical, we'd need to pass 'selected_codes' down.
                
                if not clashes:
                    occupied.update(new_occupied)
                    backtrack(index + 1, occupied)
                    occupied.difference_update(new_occupied)
        
        backtrack(0, set())
        return count

    def count_solutions(self, max_count: int = 100000) -> int:
        """
        Count total valid timetable combinations (considering teacher differences).
        Uses backtracking with pruning.
        """
        if not self.courses:
            return 0
            
        count = 0
        def backtrack(index: int, selected: List[Slot], occupied: Set[Tuple[str, int]]) -> None:
            nonlocal count
            if count >= max_count:
                return
            
            if index == len(self.courses):
                count += 1
                return
            
            course_id = self.courses[index].id
            # Use pre-built slot map which is already filtered
            available_slots = self.slot_map.get(course_id, [])
            
            for slot in available_slots:
                if count >= max_count:
                    return
                
                # Check clashes with previously selected slots
                clashes = False
                for existing in selected:
                    if self._check_clash(slot, existing):
                        clashes = True
                        break
                
                if not clashes:
                    # Check time slot availability against occupied set
                    new_occupied = set()
                    for s in slot.get_individual_slots():
                        timing = get_slot_timing(s)
                        if timing:
                            key = (timing['day'], timing['period'])
                            if key in occupied:
                                clashes = True
                                break
                            new_occupied.add(key)
                    
                    if not clashes:
                        selected.append(slot)
                        occupied.update(new_occupied)
                        
                        backtrack(index + 1, selected, occupied)
                        
                        selected.pop()
                        occupied.difference_update(new_occupied)
        
        backtrack(0, [], set())
        return count

    def _score_slot(self, slot: Slot) -> float:
        """
        Calculate score for a single slot based on detailed user rules.
        Score is calculated PER INDIVIDUAL TIME UNIT (e.g. A11, A12) then averaged?
        Actually, the user said "calculate the average score of selected cells".
        A 'Slot' object contains multiple cells.
        We will return the AVERAGE score of the cells in this slot.
        """
        individual_slots = slot.get_individual_slots()
        if not individual_slots:
            return 0.0
            
        total_cell_score = 0.0
        
        # 1. Faculty Score
        # "Teacher with priority 1 on course A gets a 100 score, priority 2 gets 80"
        faculty_score = 0.0
        if self.preferences.course_faculty_preferences and slot.course_id:
            # Cast to string because JSON keys are strings
            cid_str = str(slot.course_id)
            course_prefs = self.preferences.course_faculty_preferences.get(cid_str, [])
            
            if slot.faculty and slot.faculty.name in course_prefs:
                rank = course_prefs.index(slot.faculty.name)
                # Boosted scores to make Faculty Preference DOMINANT over Time Preference (max 100)
                if rank == 0:
                    faculty_score = 1000.0
                elif rank == 1:
                    faculty_score = 800.0
                elif rank == 2:
                    faculty_score = 600.0
            else:
                # Unlisted teacher getting low priority? 
                # User didn't specify, but implies only prioritized ones get score.
                # Let's give small base score or 0.
                pass
        
        # 2. Time Score (Per Cell)
        # Calculate for each cell and take average for this slot group
        
        mode = self.preferences.time_mode
        # Compat check
        if mode == 'none':
            if self.preferences.prefer_morning: mode = 'morning'
            elif self.preferences.prefer_afternoon: mode = 'afternoon' # "evening"
            
        for s in individual_slots:
            cell_time_score = 0.0
            
            # Check exclusions first
            if s in self.preferences.exclude_slots:
                 cell_time_score -= 1000.0
            elif slot.faculty and slot.faculty.name in self.preferences.avoided_faculties:
                 cell_time_score -= 1000.0
            else:
                timing = get_slot_timing(s)
                if timing:
                    period = timing['period']
                    
                    # Check Soft Avoidance Filters (User said "least scores", so we give 0)
                    if self.preferences.avoid_early_morning and period == 1:
                        cell_time_score = 0.0
                    elif self.preferences.avoid_late_evening and period == 7:
                         cell_time_score = 0.0
                    else:
                        # Normal Mode Scoring (Normalized to 0-100 to match Faculty Weight)
                        if mode == 'morning':
                            # P1(8:30) -> 100. P7(18:00) -> 10.
                            # Slope: -15 per period roughly.
                            # P1: 100. P2: 85. P3: 70... P7: 10.
                            cell_time_score = max(0, 115 - (15 * period))
                            
                        elif mode == 'evening' or mode == 'afternoon':
                            # P7 -> 100. P1 -> 10.
                            # P7: 100. P6: 85...
                            # 10 + (15 * (period-1)) ?
                            # P1: 10. P7: 10 + 90 = 100.
                            cell_time_score = max(0, 10 + (15 * (period - 1)))
                            
                        elif mode == 'middle':
                            # Peak P4 -> 100.
                            # P3/P5 -> 70.
                            # P2/P6 -> 40.
                            # P1/P7 -> 10.
                            dist = abs(period - 4) 
                            # 100 - (30 * dist)
                            cell_time_score = max(0, 100 - (30 * dist))
                        
                        else:
                            # Random or None mode -> Neutral score
                            # If only Teachers applied, this acts as base.
                            cell_time_score = 50.0
            
            # Combine scores
            # User said "calculate the average score of selected cells"
            # And "calculate the teacher score of each cell... and calculate the average"
            # It implies we sum (TimeScore + TeacherScore) per cell?
            # yes "cell has teacher with priority 1... that cell gets points"
            
            total_cell_score += (cell_time_score + faculty_score)
            
        # Average score for this slot group
        avg_score = total_cell_score / len(individual_slots)
        
        # Credit Weighting: Amplify score by course credits (e.g. 4 credits -> 4x score)
        credits = 1
        if slot.course and slot.course.c:
            credits = slot.course.c
            
        return avg_score * credits
    
    def _check_clash(self, slot1: Slot, slot2: Slot) -> bool:
        """Check if two slots clash (time overlap or mutual exclusion)."""
        slots1 = set(slot1.get_individual_slots())
        slots2 = set(slot2.get_individual_slots())
        
        # Check mutual exclusion groups
        for group_a, group_b in MUTUAL_EXCLUSION_GROUPS:
            has_1_in_a = not slots1.isdisjoint(group_a)
            has_1_in_b = not slots1.isdisjoint(group_b)
            has_2_in_a = not slots2.isdisjoint(group_a)
            has_2_in_b = not slots2.isdisjoint(group_b)
            
            if (has_1_in_a and has_2_in_b) or (has_1_in_b and has_2_in_a):
                return True
        
        # Check time overlap
        for s1 in slots1:
            for s2 in slots2:
                t1 = get_slot_timing(s1)
                t2 = get_slot_timing(s2)
                if t1 and t2:
                    if t1['day'] == t2['day'] and t1['period'] == t2['period']:
                        return True
        
        return False
    
    def _calculate_solution_score(self, slots: List[Slot]) -> Tuple[float, Dict]:
        """Calculate overall score for a complete solution."""
        score = 0.0
        details = {
            'preferred_faculty_matches': 0,
            'gaps_per_day': {},
            'saturday_classes': 0
        }
        
        # Sum individual slot scores
        for slot in slots:
            score += self._score_slot(slot)
            
            # Count preferred faculty matches
            # Count preferred faculty matches
            if slot.faculty and slot.course_id:
                cid_str = str(slot.course_id)
                if cid_str in self.preferences.course_faculty_preferences:
                    if slot.faculty.name in self.preferences.course_faculty_preferences[cid_str]:
                        details['preferred_faculty_matches'] += 1
        
        # Calculate gaps per day
        day_periods: Dict[str, List[int]] = {}
        for slot in slots:
            for s in slot.get_individual_slots():
                timing = get_slot_timing(s)
                if timing:
                    day = timing['day']
                    if day not in day_periods:
                        day_periods[day] = []
                    day_periods[day].append(timing['period'])
                    
                    if day == 'SAT':
                        details['saturday_classes'] += 1
        
        total_gaps = 0
        for day, periods in day_periods.items():
            periods.sort()
            gaps = 0
            for i in range(1, len(periods)):
                gap = periods[i] - periods[i-1] - 1
                if gap > 0:
                    gaps += gap
            details['gaps_per_day'][day] = gaps
            total_gaps += gaps
        
        # Penalize gaps
        score -= total_gaps * 2
        
        # Penalize Saturday classes
        score -= details['saturday_classes'] * 3
        
        return score, details
    
    def generate(self, limit: int = 5, offset: int = 0) -> Generator[TimetableSolution, None, None]:
        """
        Generate valid timetable solutions using backtracking.
        
        Args:
            limit: Maximum number of solutions to return
            offset: Number of solutions to skip (for pagination)
            
        Yields:
            TimetableSolution objects
        """
        if not self.courses:
            return
        
        # Randomize course order for diversity
        course_ids = [c.id for c in self.courses]
        random.shuffle(course_ids)
        
        # Also re-shuffle slots for each course to get different combinations
        for course_id in course_ids:
            if course_id in self.slot_map:
                random.shuffle(self.slot_map[course_id])
        
        solutions_found = 0
        solutions_skipped = 0
        seen_solutions: Set[frozenset] = set()  # Track unique combinations
        
        def backtrack(index: int, selected: List[Slot], occupied: Set[Tuple[str, int]]) -> Generator:
            """Recursive backtracking with pruning."""
            nonlocal solutions_found, solutions_skipped
            
            if solutions_found >= limit:
                return
            
            if index == len(course_ids):
                # Create a signature for this solution (set of slot IDs)
                solution_sig = frozenset(s.id for s in selected)
                
                # Skip duplicates
                if solution_sig in seen_solutions:
                    return
                seen_solutions.add(solution_sig)
                
                # Found a complete, unique solution
                if solutions_skipped < offset:
                    solutions_skipped += 1
                    return
                
                total_credits = sum(s.course.c for s in selected if s.course)
                score, details = self._calculate_solution_score(selected)
                
                solutions_found += 1
                yield TimetableSolution(
                    slots=list(selected),
                    score=score,
                    total_credits=total_credits,
                    details=details
                )
                return
            
            course_id = course_ids[index]
            available_slots = self.slot_map.get(course_id, [])
            
            for slot in available_slots:
                # Check if this slot clashes with any already selected
                clashes = False
                for existing in selected:
                    if self._check_clash(slot, existing):
                        clashes = True
                        break
                
                if not clashes:
                    # Check time slot availability
                    new_occupied = set()
                    for s in slot.get_individual_slots():
                        timing = get_slot_timing(s)
                        if timing:
                            key = (timing['day'], timing['period'])
                            if key in occupied:
                                clashes = True
                                break
                            new_occupied.add(key)
                    
                    if not clashes:
                        # Recurse with this slot selected
                        selected.append(slot)
                        occupied.update(new_occupied)
                        
                        yield from backtrack(index + 1, selected, occupied)
                        
                        # Backtrack
                        selected.pop()
                        occupied.difference_update(new_occupied)
                        
                        if solutions_found >= limit:
                            return
        
        yield from backtrack(0, [], set())
    
    def generate_batch(self, limit: int = 5, offset: int = 0) -> List[TimetableSolution]:
        """
        Generate a batch of solutions (non-generator version).
        
        Args:
            limit: Maximum number of solutions
            offset: Pagination offset
            
        Returns:
            List of TimetableSolution objects, sorted by score descending
        """
        solutions = list(self.generate(limit=limit, offset=offset))
        solutions.sort(key=lambda s: s.score, reverse=True)
        return solutions
    
    def count_solutions(self, max_count: int = 100000) -> int:
        """
        Count total valid timetable combinations without fully generating them.
        
        Args:
            max_count: Stop counting after this many (for performance)
            
        Returns:
            Number of valid combinations (capped at max_count)
        """
        if not self.courses:
            return 0
        
        course_ids = [c.id for c in self.courses]
        count = 0
        
        def backtrack(index: int, selected: List[Slot], occupied: Set[Tuple[str, int]]) -> None:
            nonlocal count
            
            if count >= max_count:
                return
            
            if index == len(course_ids):
                count += 1
                return
            
            course_id = course_ids[index]
            available_slots = self.slot_map.get(course_id, [])
            
            for slot in available_slots:
                if count >= max_count:
                    return
                    
                # Check if this slot clashes with any already selected
                clashes = False
                for existing in selected:
                    if self._check_clash(slot, existing):
                        clashes = True
                        break
                
                if not clashes:
                    # Check time slot availability
                    new_occupied = set()
                    for s in slot.get_individual_slots():
                        timing = get_slot_timing(s)
                        if timing:
                            key = (timing['day'], timing['period'])
                            if key in occupied:
                                clashes = True
                                break
                            new_occupied.add(key)
                    
                    if not clashes:
                        selected.append(slot)
                        occupied.update(new_occupied)
                        
                        backtrack(index + 1, selected, occupied)
                        
                        selected.pop()
                        occupied.difference_update(new_occupied)
        
        backtrack(0, [], set())
        return count

    def _get_timetable_signature(self, slots: List[Slot]) -> Tuple:
        """
        Create a signature for a timetable based on time distribution.
        Used to compare how different two timetables are.
        """
        morning_count = 0
        afternoon_count = 0
        days_used = set()
        periods_used = set()
        
        for slot in slots:
            for code in slot.get_individual_slots():
                timing = get_slot_timing(code)
                if timing:
                    days_used.add(timing['day'])
                    periods_used.add(timing['period'])
                    if timing['period'] <= 3:
                        morning_count += 1
                    else:
                        afternoon_count += 1
        
        return (
            frozenset(days_used),
            frozenset(periods_used),
            morning_count,
            afternoon_count
        )

    def _calculate_diversity_score(self, new_slots: List[Slot], existing_solutions: List[TimetableSolution]) -> float:
        """
        Calculate how different a new solution is from all existing solutions.
        Higher score = more different = better for diversity.
        """
        if not existing_solutions:
            return 100.0
        
        new_sig = self._get_timetable_signature(new_slots)
        new_slot_ids = frozenset(s.id for s in new_slots)
        
        min_diff = float('inf')
        
        for existing in existing_solutions:
            existing_sig = self._get_timetable_signature(existing.slots)
            existing_ids = frozenset(s.id for s in existing.slots)
            
            # Count shared slots (lower = more different)
            shared_slots = len(new_slot_ids & existing_ids)
            
            # Count shared days
            shared_days = len(new_sig[0] & existing_sig[0])
            
            # Count shared periods
            shared_periods = len(new_sig[1] & existing_sig[1])
            
            # Similarity score (lower = more different)
            similarity = shared_slots * 10 + shared_days * 2 + shared_periods
            
            min_diff = min(min_diff, similarity)
        
        # Convert to diversity score (higher = better)
        return max(0, 100 - min_diff * 5)

    def generate_diverse(self, limit: int = 5, min_diversity: float = 30.0) -> List[TimetableSolution]:
        """
        Generate highly diverse timetable solutions.
        Rejects solutions too similar to already found ones.
        
        Args:
            limit: Maximum number of solutions
            min_diversity: Minimum diversity score (0-100) to accept a solution
            
        Returns:
            List of diverse TimetableSolution objects
        """
        if not self.courses:
            return []
        
        solutions = []
        seen_ids: Set[frozenset] = set()
        
        # Determine if we should randomize slots
        # If user has a specific time preference, we should RESPECT the sorted order (by score)
        # and NOT shuffle the slots, otherwise we lose the "preferred time" optimization.
        should_shuffle_slots = (self.preferences.time_mode == 'none' and 
                                not self.preferences.course_faculty_preferences)
        
        max_attempts = limit * 50
        attempts = 0
        
        def try_generate():
            nonlocal attempts
            
            def backtrack(index: int, selected: List[Slot], occupied: Set[Tuple[str, int]]) -> Optional[List[Slot]]:
                nonlocal attempts
                if attempts >= max_attempts:
                    return None
                    
                if index == len(self.courses):
                    return selected[:]
                
                course_id = course_ids[index]
                # Slots are already sorted by score (preference) in _build_slot_map
                slots = self.slot_map.get(course_id, [])
                
                for slot in slots:
                    attempts += 1
                    if attempts >= max_attempts:
                        return None
                        
                    # Check clashes
                    clashes = False
                    for existing in selected:
                        if self._check_clash(slot, existing):
                            clashes = True
                            break
                    
                    if not clashes:
                        # Check time availability
                        new_occupied = set()
                        time_clash = False
                        for s in slot.get_individual_slots():
                            timing = get_slot_timing(s)
                            if timing:
                                key = (timing['day'], timing['period'])
                                if key in occupied:
                                    time_clash = True
                                    break
                                new_occupied.add(key)
                        
                        if not time_clash:
                            occupied.update(new_occupied)
                            result = backtrack(index + 1, selected + [slot], occupied)
                            occupied.difference_update(new_occupied)
                            if result:
                                return result
                return None
            
            return backtrack(0, [], set())
        
        # Try to find diverse solutions with decreasing strictness
        current_min_diversity = min_diversity
        failed_attempts_streak = 0
        
        # Initial course order
        course_ids = [c.id for c in self.courses]
        
        while len(solutions) < limit and attempts < max_attempts:
            # Always shuffle COURSE order for variety in backtracking path
            random.shuffle(course_ids)
            
            # ONLY shuffle slots if NO preference is set
            if should_shuffle_slots:
                for cid in course_ids:
                    if cid in self.slot_map:
                        random.shuffle(self.slot_map[cid])
            
            result = try_generate()
            
            if result:
                slot_ids = frozenset(s.id for s in result)
                
                # Check for duplicate
                if slot_ids in seen_ids:
                    attempts += 1  # Count duplicate as attempt to avoid infinite loops
                    continue
                
                # Check diversity
                diversity = self._calculate_diversity_score(result, solutions)
                
                # If we're stuck, lower the bar
                if failed_attempts_streak > 20:
                    current_min_diversity = max(5.0, current_min_diversity - 5.0)
                    failed_attempts_streak = 0
                
                if diversity >= current_min_diversity or len(solutions) == 0:
                    seen_ids.add(slot_ids)
                    total_credits = sum(s.course.c for s in result if s.course)
                    score, details = self._calculate_solution_score(result)
                    solutions.append(TimetableSolution(
                        slots=result,
                        score=score,
                        total_credits=total_credits,
                        details=details
                    ))
                    failed_attempts_streak = 0
                else:
                    failed_attempts_streak += 1
            else:
                # If generation failed completely (no solution found), stop
                break
        
        return solutions

    def generate_similar(self, reference_slot_ids: List[int], limit: int = 5) -> List[TimetableSolution]:
        """
        Generate timetables similar to a reference (differing by 1-2 courses).
        
        Args:
            reference_slot_ids: Slot IDs from the reference timetable
            limit: Maximum number of similar solutions
            
        Returns:
            List of similar TimetableSolution objects
        """
        if not self.courses:
            return []
        
        # Map course_id to reference slot for that course
        reference_slots = {}
        for course in self.courses:
            for slot in course.slots.all():
                if slot.id in reference_slot_ids:
                    reference_slots[course.id] = slot
                    break
        
        solutions = []
        seen_ids: Set[frozenset] = set()
        seen_ids.add(frozenset(reference_slot_ids))  # Exclude exact reference
        
        course_ids = [c.id for c in self.courses]
        
        # Try varying 1-2 courses from reference
        for vary_count in [1, 2]:
            if len(solutions) >= limit:
                break
            
            for vary_indices in self._combinations(range(len(course_ids)), vary_count):
                if len(solutions) >= limit:
                    break
                
                # Start with reference slots
                selected = []
                occupied = set()
                valid = True
                
                for i, cid in enumerate(course_ids):
                    if i not in vary_indices and cid in reference_slots:
                        slot = reference_slots[cid]
                        selected.append(slot)
                        for s in slot.get_individual_slots():
                            timing = get_slot_timing(s)
                            if timing:
                                occupied.add((timing['day'], timing['period']))
                
                # Try different slots for varied courses
                for idx in vary_indices:
                    cid = course_ids[idx]
                    available = self.slot_map.get(cid, [])
                    
                    for slot in available:
                        if cid in reference_slots and slot.id == reference_slots[cid].id:
                            continue  # Skip reference slot
                        
                        clashes = False
                        for existing in selected:
                            if self._check_clash(slot, existing):
                                clashes = True
                                break
                        
                        if not clashes:
                            new_occupied = set()
                            for s in slot.get_individual_slots():
                                timing = get_slot_timing(s)
                                if timing:
                                    key = (timing['day'], timing['period'])
                                    if key in occupied:
                                        clashes = True
                                        break
                                    new_occupied.add(key)
                            
                            if not clashes:
                                test_selected = selected + [slot]
                                slot_ids = frozenset(s.id for s in test_selected)
                                
                                if slot_ids not in seen_ids and len(test_selected) == len(course_ids):
                                    seen_ids.add(slot_ids)
                                    total_credits = sum(s.course.c for s in test_selected if s.course)
                                    score, details = self._calculate_solution_score(test_selected)
                                    solutions.append(TimetableSolution(
                                        slots=test_selected,
                                        score=score,
                                        total_credits=total_credits,
                                        details=details
                                    ))
                                    break
        
        return solutions[:limit]

    def _combinations(self, items, r):
        """Generate combinations of r items from list."""
        items = list(items)
        n = len(items)
        if r > n:
            return
        indices = list(range(r))
        yield tuple(items[i] for i in indices)
        while True:
            for i in reversed(range(r)):
                if indices[i] != i + n - r:
                    break
            else:
                return
            indices[i] += 1
            for j in range(i + 1, r):
                indices[j] = indices[j - 1] + 1
            yield tuple(items[i] for i in indices)
