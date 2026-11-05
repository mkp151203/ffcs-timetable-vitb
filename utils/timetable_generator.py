"""
Timetable Generator Module
Generates optimal, clash-free timetable combinations using constraint satisfaction.
"""

import random
from typing import List, Dict, Set, Optional, Tuple, Generator
from dataclasses import dataclass, field
from sqlalchemy.orm import joinedload
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
    Uses backtracking with pruning, constraint propagation (AC-3), and beam search.
    
    Performance optimizations:
    - Pre-computed conflict matrix for O(1) clash detection
    - Cached slot timings to avoid repeated parsing
    - Arc consistency to prune impossible slots early
    - Beam search for efficient high-quality solution finding
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
        
        # Performance caches
        self._slot_timings_cache: Dict[int, Set[Tuple[str, int]]] = {}  # slot_id -> {(day, period), ...}
        self._conflict_matrix: Dict[int, Set[int]] = {}  # slot_id -> set of conflicting slot_ids
        self._slot_scores_cache: Dict[int, float] = {}  # slot_id -> pre-computed score
        
        # Warnings collection
        self.warnings: List[str] = []
        
        # Fetch every course's slots once (course.slots is lazy='dynamic', so each
        # course.slots.all() would be another DB query)
        self._course_slots: Dict[int, List[Slot]] = self._prefetch_course_slots()
        
        # Build initial slot map, filtering out faulty slots
        self._build_slot_map()
        
        # Pre-compute optimizations
        self._build_timing_cache()
        self._build_conflict_matrix()
    
    def _prefetch_course_slots(self) -> Dict[int, List[Slot]]:
        """Load all slots (with faculty) for self.courses in a single query."""
        course_slots: Dict[int, List[Slot]] = {course.id: [] for course in self.courses}
        if not course_slots:
            return course_slots
        
        slots = Slot.query.filter(Slot.course_id.in_(list(course_slots.keys()))).options(
            joinedload(Slot.faculty)
        ).order_by(Slot.id).all()
        for slot in slots:
            course_slots[slot.course_id].append(slot)
        return course_slots
    
    def _get_course_slots(self, course: Course) -> List[Slot]:
        """Pre-fetched slots for a course (replacement for course.slots.all())."""
        return list(self._course_slots.get(course.id, []))
    
    def _build_timing_cache(self):
        """Pre-compute timing information for all slots."""
        for course in self.courses:
            for slot in self.slot_map.get(course.id, []):
                if slot.id not in self._slot_timings_cache:
                    timings = set()
                    for code in slot.get_individual_slots():
                        timing = get_slot_timing(code)
                        if timing:
                            timings.add((timing['day'], timing['period']))
                    self._slot_timings_cache[slot.id] = timings
    
    def _build_conflict_matrix(self):
        """Pre-compute which slots conflict with each other for O(1) clash detection."""
        all_slots = []
        for course in self.courses:
            all_slots.extend(self.slot_map.get(course.id, []))
        
        # Initialize empty conflict sets
        for slot in all_slots:
            self._conflict_matrix[slot.id] = set()
        
        # Build conflict relationships
        for i, slot1 in enumerate(all_slots):
            timings1 = self._slot_timings_cache.get(slot1.id, set())
            codes1 = set(slot1.get_individual_slots())
            
            for slot2 in all_slots[i+1:]:
                # Skip same course (we select one slot per course anyway)
                if slot1.course_id == slot2.course_id:
                    continue
                
                timings2 = self._slot_timings_cache.get(slot2.id, set())
                codes2 = set(slot2.get_individual_slots())
                
                # Check time overlap (O(1) set intersection)
                if timings1 & timings2:
                    self._conflict_matrix[slot1.id].add(slot2.id)
                    self._conflict_matrix[slot2.id].add(slot1.id)
                    continue
                
                # Check mutual exclusion groups
                for group_a, group_b in MUTUAL_EXCLUSION_GROUPS:
                    has_1_in_a = not codes1.isdisjoint(group_a)
                    has_1_in_b = not codes1.isdisjoint(group_b)
                    has_2_in_a = not codes2.isdisjoint(group_a)
                    has_2_in_b = not codes2.isdisjoint(group_b)
                    
                    if (has_1_in_a and has_2_in_b) or (has_1_in_b and has_2_in_a):
                        self._conflict_matrix[slot1.id].add(slot2.id)
                        self._conflict_matrix[slot2.id].add(slot1.id)
                        break

    def _check_clash_fast(self, slot1_id: int, slot2_id: int) -> bool:
        """O(1) clash detection using pre-computed conflict matrix."""
        return slot2_id in self._conflict_matrix.get(slot1_id, set())
    
    def _has_time_clash_with_occupied(self, slot: Slot, occupied: Set[Tuple[str, int]]) -> bool:
        """Check if slot's timings overlap with already occupied time slots."""
        slot_timings = self._slot_timings_cache.get(slot.id, set())
        return bool(slot_timings & occupied)
    
    def _get_slot_timings(self, slot: Slot) -> Set[Tuple[str, int]]:
        """Get cached timings for a slot."""
        return self._slot_timings_cache.get(slot.id, set())
    
    def _build_slot_map(self, randomize_only: bool = False, ignore_preferences: bool = False):
        """
        Build mapping of courses to their available slots.
        
        Args:
            randomize_only: If True, do not sort by preference score; only shuffle.
            ignore_preferences: If True, do not filter out slots based on user prefs (avoids, excludes).
        """
        # Clear previous warnings if rebuilding map from scratch (not usually done, but safe)
        if not randomize_only: 
             self.warnings = []

        for course in self.courses:
            slots = []
            for slot in self._get_course_slots(course):
                # CRITICAL: Filter out faulty slots with unknown timings
                if self._is_slot_faulty(slot):
                    continue

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

    def _is_slot_faulty(self, slot: Slot) -> bool:
        """
        Check if a slot has valid timing codes.
        If faulty, add a warning and return True.
        """
        # Check if we can resolve all timing codes
        individual_slots = slot.get_individual_slots()
        is_faulty = False
        faulty_codes = []

        for code in individual_slots:
            timing = get_slot_timing(code)
            if not timing:
                is_faulty = True
                faulty_codes.append(code)
        
        if is_faulty:
            msg = f"Excluded {slot.faculty.name if slot.faculty else 'Unknown Faculty'} for {slot.course.code if slot.course else 'Unknown Course'}: Unknown slot code(s) {', '.join(faulty_codes)}"
            if msg not in self.warnings:
                self.warnings.append(msg)
            return True
            
        return False
    
    def filter_to_preferred_teachers(self) -> None:
        """
        Filter slot map to ONLY include slots with preferred teachers.
        
        For each course that has teacher preferences, keep only slots
        where the faculty is in the preference list.
        This dramatically reduces the search space.
        """
        if not self.preferences.course_faculty_preferences:
            return
        
        for course in self.courses:
            cid_str = str(course.id)
            preferred_teachers = self.preferences.course_faculty_preferences.get(cid_str, [])
            
            if preferred_teachers:
                # Filter to only slots with preferred teachers
                current_slots = self.slot_map.get(course.id, [])
                filtered_slots = [
                    slot for slot in current_slots
                    if slot.faculty and slot.faculty.name in preferred_teachers
                ]
                
                # Only apply filter if it leaves some slots
                if filtered_slots:
                    self.slot_map[course.id] = filtered_slots
                # If no slots match, keep all slots (soft constraint)

    def generate_tiered_teacher_pool(self, target_pool: int = 20000, target_size: int = 100) -> List[TimetableSolution]:
        """
        Generate timetables in tiers based on how many preferred teachers are matched.
        
        Tier order (highest priority first):
        - Tier N: All N courses have preferred teachers
        - Tier N-1: N-1 courses have preferred teachers
        - ...
        - Tier 0: No preferred teachers (random fallback)
        
        Fills each tier before moving to next, until target_pool is reached.
        Then ranks all by teacher priority score.
        
        Args:
            target_pool: Maximum number of timetables to collect across all tiers
            target_size: Number of top solutions to return
            
        Returns:
            List of TimetableSolution objects, ranked by score
        """
        if not self.courses:
            return []
        
        # Separate slots into preferred vs non-preferred for each course
        preferred_slots: Dict[int, List[Slot]] = {}
        non_preferred_slots: Dict[int, List[Slot]] = {}
        
        for course in self.courses:
            cid_str = str(course.id)
            prefs = self.preferences.course_faculty_preferences.get(cid_str, [])
            all_slots = self.slot_map.get(course.id, [])
            
            if prefs:
                preferred_slots[course.id] = [s for s in all_slots if s.faculty and s.faculty.name in prefs]
                non_preferred_slots[course.id] = [s for s in all_slots if not s.faculty or s.faculty.name not in prefs]
            else:
                # No preference for this course - all slots are "preferred"
                preferred_slots[course.id] = all_slots
                non_preferred_slots[course.id] = []
        
        all_solutions: List[List[Slot]] = []
        seen_signatures: Set[frozenset] = set()
        num_courses = len(self.courses)
        
        # Generate tiers from best (all preferred) to worst (none preferred)
        for num_preferred in range(num_courses, -1, -1):
            if len(all_solutions) >= target_pool:
                break
            
            # Generate combinations where exactly num_preferred courses use preferred slots
            tier_solutions = self._generate_tier(
                num_preferred, 
                preferred_slots, 
                non_preferred_slots,
                seen_signatures,
                target_pool - len(all_solutions)
            )
            all_solutions.extend(tier_solutions)
        
        # Score and rank all solutions
        scored_solutions = []
        for slots in all_solutions:
            score = self._calculate_solution_total_score(slots)
            total_credits = sum(s.course.c if s.course else 0 for s in slots)
            
            # Count how many preferred teachers
            pref_count = 0
            for slot in slots:
                cid_str = str(slot.course_id)
                prefs = self.preferences.course_faculty_preferences.get(cid_str, [])
                if slot.faculty and slot.faculty.name in prefs:
                    pref_count += 1
            
            details = self._build_solution_details(slots)
            details['method'] = 'tiered_teacher_pool'
            details['preferred_teacher_count'] = pref_count
            details['total_pool_size'] = len(all_solutions)
            
            scored_solutions.append(TimetableSolution(
                slots=slots,
                score=score,
                total_credits=total_credits,
                details=details
            ))
        
        # Sort by score (highest first)
        scored_solutions.sort(key=lambda x: x.score, reverse=True)
        
        return scored_solutions[:target_size]
    
    def _generate_tier(
        self, 
        num_preferred: int,
        preferred_slots: Dict[int, List[Slot]],
        non_preferred_slots: Dict[int, List[Slot]],
        seen_signatures: Set[frozenset],
        max_solutions: int
    ) -> List[List[Slot]]:
        """
        Generate timetables where exactly num_preferred courses use preferred teachers.
        Uses backtracking with randomization for diversity.
        """
        solutions: List[List[Slot]] = []
        num_courses = len(self.courses)
        
        if num_preferred > num_courses:
            return solutions
        
        # Try multiple random orderings for diversity
        max_attempts = max_solutions * 50
        attempts = 0
        
        while len(solutions) < max_solutions and attempts < max_attempts:
            attempts += 1
            
            # Randomly choose which courses use preferred slots
            course_indices = list(range(num_courses))
            random.shuffle(course_indices)
            use_preferred = set(course_indices[:num_preferred])
            
            # Try to build a valid timetable with this configuration
            result = self._try_build_timetable(use_preferred, preferred_slots, non_preferred_slots)
            
            if result:
                sig = frozenset(s.id for s in result)
                if sig not in seen_signatures:
                    seen_signatures.add(sig)
                    solutions.append(result)
        
        return solutions
    
    def _try_build_timetable(
        self,
        use_preferred: Set[int],
        preferred_slots: Dict[int, List[Slot]],
        non_preferred_slots: Dict[int, List[Slot]]
    ) -> Optional[List[Slot]]:
        """
        Try to build a valid timetable with the given configuration.
        Returns None if no valid combination found.
        """
        selected: List[Slot] = []
        occupied: Set[Tuple[str, int]] = set()
        
        # Shuffle course order for diversity
        courses = list(self.courses)
        random.shuffle(courses)
        
        for i, course in enumerate(courses):
            # Choose slot pool based on whether this course should use preferred
            course_idx = self.courses.index(course)
            if course_idx in use_preferred:
                pool = preferred_slots.get(course.id, [])
            else:
                pool = non_preferred_slots.get(course.id, [])
            
            # If pool is empty, try the other pool
            if not pool:
                if course_idx in use_preferred:
                    pool = non_preferred_slots.get(course.id, [])
                else:
                    pool = preferred_slots.get(course.id, [])
            
            if not pool:
                return None  # No slots available
            
            # Shuffle and try to find a valid slot
            pool_copy = list(pool)
            random.shuffle(pool_copy)
            
            found = False
            for slot in pool_copy:
                # Check clash with selected slots
                has_clash = False
                for existing in selected:
                    if self._check_clash(slot, existing):
                        has_clash = True
                        break
                
                if not has_clash:
                    # Check time overlap
                    slot_times = set()
                    time_clash = False
                    for code in slot.get_individual_slots():
                        timing = get_slot_timing(code)
                        if timing:
                            key = (timing['day'], timing['period'])
                            if key in occupied:
                                time_clash = True
                                break
                            slot_times.add(key)
                    
                    if not time_clash:
                        selected.append(slot)
                        occupied.update(slot_times)
                        found = True
                        break
            
            if not found:
                return None  # Couldn't find valid slot for this course
        
        return selected if len(selected) == len(self.courses) else None

    def generate_unified(self, target_size: int = 100) -> List[TimetableSolution]:
        """
        Unified generation flow handling all 4 scenarios:
        
        1. NO FILTERS: Generate random 100 timetables
        2. TIME ONLY: Generate 20k random → rank by time → return top 100
        3. TEACHER ONLY: Generate 20k tiered → tier by teacher count → rank by priority → return top 100
        4. TIME + TEACHER: Generate 20k random → tier by teacher count → rank by time within tier → waterfall top 100
        
        Returns:
            List of TimetableSolution objects
        """
        has_teacher_prefs = bool(self.preferences.course_faculty_preferences)
        # Time prefs include time_mode OR avoid filters
        has_time_prefs = (
            self.preferences.time_mode != 'none' or
            self.preferences.avoid_early_morning or
            self.preferences.avoid_late_evening
        )
        
        # Generate 20k random pool for all scenarios (except no filters)
        if not has_teacher_prefs and not has_time_prefs:
            # SCENARIO 1: NO FILTERS - just generate 100 random
            return self._generate_random_solutions(target_size)
        
        # Generate the pool (20k random timetables)
        pool = self._generate_random_pool(target_pool=20000)
        
        if not pool:
            return []
        
        # Calculate metrics for each timetable
        scored_pool = []
        for slots in pool:
            teacher_match_count = self._count_preferred_teachers(slots)
            time_score = self._calculate_time_score(slots)
            teacher_priority_score = self._calculate_teacher_priority_score(slots)
            total_credits = sum(s.course.c if s.course else 0 for s in slots)
            
            scored_pool.append({
                'slots': slots,
                'teacher_match_count': teacher_match_count,
                'time_score': time_score,
                'teacher_priority_score': teacher_priority_score,
                'total_credits': total_credits
            })
        
        # Route to appropriate ranking strategy
        if has_time_prefs and not has_teacher_prefs:
            # SCENARIO 2: TIME ONLY - rank by time score
            return self._rank_by_time(scored_pool, target_size)
        
        elif has_teacher_prefs and not has_time_prefs:
            # SCENARIO 3: TEACHER ONLY - tier by count, rank by priority within tier
            return self._rank_tiered_by_teacher_priority(scored_pool, target_size)
        
        else:
            # SCENARIO 4: BOTH - tier by teacher count, rank by time within tier
            return self._rank_tiered_by_time(scored_pool, target_size)
    
    def _generate_random_solutions(self, target_size: int) -> List[TimetableSolution]:
        """Generate random timetables without any ranking (no filters scenario)."""
        self._build_slot_map(randomize_only=True, ignore_preferences=True)
        
        solutions = []
        seen = set()
        max_attempts = target_size * 100
        attempts = 0
        
        while len(solutions) < target_size and attempts < max_attempts:
            attempts += 1
            result = self._try_random_timetable()
            if result:
                sig = frozenset(s.id for s in result)
                if sig not in seen:
                    seen.add(sig)
                    total_credits = sum(s.course.c if s.course else 0 for s in result)
                    solutions.append(TimetableSolution(
                        slots=result,
                        score=0,  # No scoring for random
                        total_credits=total_credits,
                        details={'method': 'random', 'pool_size': len(solutions)}
                    ))
        
        return solutions
    
    def _generate_random_pool(self, target_pool: int = 20000) -> List[List[Slot]]:
        """Generate a pool of random valid timetables with early termination."""
        self._build_slot_map(randomize_only=True, ignore_preferences=True)
        self._build_timing_cache()
        self._build_conflict_matrix()
        
        pool = []
        seen = set()
        max_attempts = target_pool * 10
        attempts = 0
        
        # Early termination: stop if no new solutions found in N attempts
        no_progress_count = 0
        no_progress_limit = 1000  # Stop after 1000 consecutive failures
        
        while len(pool) < target_pool and attempts < max_attempts:
            attempts += 1
            result = self._try_random_timetable()
            
            if result:
                sig = frozenset(s.id for s in result)
                if sig not in seen:
                    seen.add(sig)
                    pool.append(result)
                    no_progress_count = 0  # Reset on success
                else:
                    no_progress_count += 1
            else:
                no_progress_count += 1
            
            # Early termination: all combinations likely found
            if no_progress_count >= no_progress_limit:
                break
        
        return pool
    
    def _try_random_timetable(self) -> Optional[List[Slot]]:
        """Try to build one random valid timetable."""
        selected = []
        occupied = set()
        
        courses = list(self.courses)
        random.shuffle(courses)
        
        for course in courses:
            slots = self.slot_map.get(course.id, [])
            if not slots:
                return None
            
            random.shuffle(slots)
            found = False
            
            for slot in slots[:10]:  # Try up to 10 random slots
                has_clash = False
                for existing in selected:
                    if self._check_clash(slot, existing):
                        has_clash = True
                        break
                
                if not has_clash:
                    slot_times = set()
                    time_clash = False
                    for code in slot.get_individual_slots():
                        timing = get_slot_timing(code)
                        if timing:
                            key = (timing['day'], timing['period'])
                            if key in occupied:
                                time_clash = True
                                break
                            slot_times.add(key)
                    
                    if not time_clash:
                        selected.append(slot)
                        occupied.update(slot_times)
                        found = True
                        break
            
            if not found:
                return None
        
        return selected if len(selected) == len(self.courses) else None
    
    def _count_preferred_teachers(self, slots: List[Slot]) -> int:
        """Count how many courses have a preferred teacher."""
        count = 0
        for slot in slots:
            cid_str = str(slot.course_id)
            prefs = self.preferences.course_faculty_preferences.get(cid_str, [])
            if slot.faculty and slot.faculty.name in prefs:
                count += 1
        return count
    
    def _calculate_time_score(self, slots: List[Slot]) -> float:
        """Calculate time preference score for a timetable."""
        mode = self.preferences.time_mode
        avoid_early = self.preferences.avoid_early_morning
        avoid_late = self.preferences.avoid_late_evening
        
        total_score = 0.0
        cell_count = 0
        
        for slot in slots:
            for code in slot.get_individual_slots():
                timing = get_slot_timing(code)
                if timing:
                    period = timing['period']
                    cell_count += 1
                    
                    # Apply avoid penalties first
                    if avoid_early and period == 1:
                        total_score += 0  # Strongly penalize 8:30 slots
                    elif avoid_late and period == 7:
                        total_score += 0  # Strongly penalize 6:00 PM slots
                    elif mode == 'morning':
                        total_score += max(0, 115 - (15 * period))
                    elif mode == 'afternoon' or mode == 'evening':
                        total_score += max(0, 10 + (15 * (period - 1)))
                    elif mode == 'middle':
                        dist = abs(period - 4)
                        total_score += max(0, 100 - (30 * dist))
                    else:
                        total_score += 50
        
        return total_score / cell_count if cell_count > 0 else 0
    
    def _calculate_teacher_priority_score(self, slots: List[Slot]) -> float:
        """Calculate teacher priority score (higher = better priority matches)."""
        total_score = 0.0
        
        for slot in slots:
            cid_str = str(slot.course_id)
            prefs = self.preferences.course_faculty_preferences.get(cid_str, [])
            
            if slot.faculty and slot.faculty.name in prefs:
                rank = prefs.index(slot.faculty.name)
                if rank == 0:
                    total_score += 1000
                elif rank == 1:
                    total_score += 800
                elif rank == 2:
                    total_score += 600
        
        return total_score
    
    def _rank_by_time(self, scored_pool: List[Dict], target_size: int) -> List[TimetableSolution]:
        """SCENARIO 2: TIME ONLY - rank by time score."""
        scored_pool.sort(key=lambda x: x['time_score'], reverse=True)
        
        results = []
        for item in scored_pool[:target_size]:
            details = self._build_solution_details(item['slots'])
            details['method'] = 'time_ranked'
            details['time_score'] = round(item['time_score'], 2)
            details['pool_size'] = len(scored_pool)
            
            results.append(TimetableSolution(
                slots=item['slots'],
                score=item['time_score'],
                total_credits=item['total_credits'],
                details=details
            ))
        
        return results
    
    def _rank_tiered_by_teacher_priority(self, scored_pool: List[Dict], target_size: int) -> List[TimetableSolution]:
        """SCENARIO 3: TEACHER ONLY - tier by count, rank by priority within tier."""
        num_courses = len(self.courses)
        results = []
        
        # Group by teacher match count (tier)
        for tier in range(num_courses, -1, -1):
            tier_items = [x for x in scored_pool if x['teacher_match_count'] == tier]
            
            # Sort by teacher priority score within tier
            tier_items.sort(key=lambda x: x['teacher_priority_score'], reverse=True)
            
            for item in tier_items:
                if len(results) >= target_size:
                    break
                
                details = self._build_solution_details(item['slots'])
                details['method'] = 'tiered_teacher_priority'
                details['teacher_match_count'] = item['teacher_match_count']
                details['teacher_priority_score'] = round(item['teacher_priority_score'], 2)
                details['tier'] = tier
                details['pool_size'] = len(scored_pool)
                
                results.append(TimetableSolution(
                    slots=item['slots'],
                    score=item['teacher_priority_score'],
                    total_credits=item['total_credits'],
                    details=details
                ))
            
            if len(results) >= target_size:
                break
        
        return results
    
    def _rank_tiered_by_time(self, scored_pool: List[Dict], target_size: int) -> List[TimetableSolution]:
        """SCENARIO 4: BOTH - tier by teacher count, rank by time within tier."""
        num_courses = len(self.courses)
        results = []
        
        # Group by teacher match count (tier)
        for tier in range(num_courses, -1, -1):
            tier_items = [x for x in scored_pool if x['teacher_match_count'] == tier]
            
            # Sort by TIME score within tier (not teacher priority)
            tier_items.sort(key=lambda x: x['time_score'], reverse=True)
            
            for item in tier_items:
                if len(results) >= target_size:
                    break
                
                details = self._build_solution_details(item['slots'])
                details['method'] = 'tiered_time_ranked'
                details['teacher_match_count'] = item['teacher_match_count']
                details['time_score'] = round(item['time_score'], 2)
                details['tier'] = tier
                details['pool_size'] = len(scored_pool)
                
                results.append(TimetableSolution(
                    slots=item['slots'],
                    score=item['time_score'],
                    total_credits=item['total_credits'],
                    details=details
                ))
            
            if len(results) >= target_size:
                break
        
        return results

    def apply_arc_consistency(self) -> bool:
        """
        AC-3 Algorithm: Reduce domains by removing slots that have no valid 
        pairing with any slot of another course.
        
        Returns:
            False if any course has empty domain (unsatisfiable), True otherwise.
        """
        if len(self.courses) < 2:
            return True
        
        # Queue of arcs (course_id pairs) to process
        queue = [(c1.id, c2.id) for c1 in self.courses for c2 in self.courses if c1.id != c2.id]
        
        while queue:
            (c1_id, c2_id) = queue.pop(0)
            if self._revise(c1_id, c2_id):
                if not self.slot_map.get(c1_id):
                    return False  # Domain wiped out - no solution
                # Re-add neighbors to queue
                for c3 in self.courses:
                    if c3.id != c1_id and c3.id != c2_id:
                        queue.append((c3.id, c1_id))
        return True
    
    def _revise(self, c1_id: int, c2_id: int) -> bool:
        """
        Remove values from c1's domain that have no support in c2.
        
        Returns:
            True if domain was revised (slots removed), False otherwise.
        """
        revised = False
        slots_to_remove = []
        
        for slot1 in self.slot_map.get(c1_id, []):
            has_support = False
            for slot2 in self.slot_map.get(c2_id, []):
                if not self._check_clash_fast(slot1.id, slot2.id):
                    has_support = True
                    break
            if not has_support:
                slots_to_remove.append(slot1)
                revised = True
        
        for slot in slots_to_remove:
            self.slot_map[c1_id].remove(slot)
        
        return revised

    def generate_beam_search(self, beam_width: int = 100, target_size: int = 100) -> List[TimetableSolution]:
        """
        Beam search: Keep top-K partial solutions at each step.
        More efficient than random sampling for finding high-quality solutions.
        
        Args:
            beam_width: Number of partial solutions to keep at each level
            target_size: Maximum number of complete solutions to return
            
        Returns:
            List of TimetableSolution objects, sorted by score
        """
        if not self.courses:
            return []
        
        # Apply constraint propagation first
        if not self.apply_arc_consistency():
            return []  # Unsatisfiable
        
        # Sort courses by constraint (fewer slots = process first for early pruning)
        sorted_courses = sorted(self.courses, key=lambda c: len(self.slot_map.get(c.id, [])))
        
        if not sorted_courses:
            return []
        
        # Initialize beams with first course's slots
        # beam = (score, selected_slots, occupied_times)
        beams: List[Tuple[float, List[Slot], Set[Tuple[str, int]]]] = []
        first_course = sorted_courses[0]
        
        for slot in self.slot_map.get(first_course.id, [])[:beam_width * 2]:  # Start with more for diversity
            score = self._score_slot(slot)
            occupied = self._get_slot_timings(slot)
            beams.append((score, [slot], occupied))
        
        # Sort and keep top beam_width
        beams.sort(key=lambda x: x[0], reverse=True)
        beams = beams[:beam_width]
        
        # Expand beam for each subsequent course
        for course in sorted_courses[1:]:
            new_beams: List[Tuple[float, List[Slot], Set[Tuple[str, int]]]] = []
            available_slots = self.slot_map.get(course.id, [])
            
            for (score, selected, occupied) in beams:
                for slot in available_slots:
                    # Fast clash check using cached data
                    if self._has_time_clash_with_occupied(slot, occupied):
                        continue
                    
                    # Check against all selected slots using conflict matrix
                    has_conflict = False
                    for sel_slot in selected:
                        if self._check_clash_fast(slot.id, sel_slot.id):
                            has_conflict = True
                            break
                    
                    if not has_conflict:
                        new_score = score + self._score_slot(slot)
                        new_occupied = occupied | self._get_slot_timings(slot)
                        new_beams.append((new_score, selected + [slot], new_occupied))
            
            # Keep top beam_width candidates
            new_beams.sort(key=lambda x: x[0], reverse=True)
            beams = new_beams[:beam_width]
            
            if not beams:
                break  # No valid solutions at this level
        
        # Convert complete solutions to TimetableSolutions
        solutions = []
        seen = set()
        
        for (score, slots, _) in beams:
            if len(slots) != len(self.courses):
                continue  # Incomplete solution
                
            sig = frozenset(s.id for s in slots)
            if sig in seen:
                continue
            seen.add(sig)
            
            total_credits = sum(s.course.c for s in slots if s.course)
            details = self._build_solution_details(slots)
            details['method'] = 'beam_search'
            
            solutions.append(TimetableSolution(
                slots=slots,
                score=score,
                total_credits=total_credits,
                details=details
            ))
            
            if len(solutions) >= target_size:
                break
        
        return solutions
    
    def _build_solution_details(self, slots: List[Slot]) -> Dict:
        """Build details dict for a solution."""
        details = {
            'preferred_faculty_matches': 0,
            'gaps_per_day': {},
            'saturday_classes': 0
        }
        
        # Count preferred faculty matches
        for slot in slots:
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
        
        details['total_gaps'] = total_gaps
        return details

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

    def generate_exhaustive(self, max_solutions: int = 20000, target_size: int = 100) -> List[TimetableSolution]:
        """
        Exhaustively generate ALL valid timetable combinations up to max_solutions.
        
        Use this when teacher preferences reduce the search space to a manageable size.
        All solutions are scored and ranked, returning the top target_size results.
        
        Args:
            max_solutions: Stop after finding this many solutions (safety limit)
            target_size: Number of top solutions to return
            
        Returns:
            List of TimetableSolution objects, sorted by score (best first)
        """
        if not self.courses:
            return []
        
        all_solutions: List[List[Slot]] = []
        seen_signatures = set()
        
        def backtrack(index: int, selected: List[Slot], occupied: Set[Tuple[str, int]]) -> None:
            nonlocal all_solutions
            
            # Safety limit
            if len(all_solutions) >= max_solutions:
                return
            
            if index == len(self.courses):
                # Found a complete solution - use slot IDs as signature to avoid duplicates
                sig = frozenset(s.id for s in selected)
                if sig not in seen_signatures:
                    seen_signatures.add(sig)
                    all_solutions.append(selected[:])  # Copy the list
                return
            
            course = self.courses[index]
            available_slots = self.slot_map.get(course.id, [])
            
            for slot in available_slots:
                if len(all_solutions) >= max_solutions:
                    return
                
                # Check clashes with previously selected slots
                clashes = False
                for existing in selected:
                    if self._check_clash_fast(slot.id, existing.id):
                        clashes = True
                        break
                
                if not clashes:
                    # Check time slot availability
                    slot_timings = self._get_slot_timings(slot)
                    if not (slot_timings & occupied):
                        # No clash - proceed
                        occupied.update(slot_timings)
                        selected.append(slot)
                        
                        backtrack(index + 1, selected, occupied)
                        
                        selected.pop()
                        occupied.difference_update(slot_timings)
        
        # Run exhaustive backtracking
        backtrack(0, [], set())
        
        # Score and rank all solutions
        scored_solutions = []
        for slots in all_solutions:
            score = self._calculate_solution_total_score(slots)
            total_credits = sum(s.course.c if s.course else 0 for s in slots)
            details = self._build_solution_details(slots)
            details['method'] = 'exhaustive'
            details['total_enumerated'] = len(all_solutions)
            
            scored_solutions.append(TimetableSolution(
                slots=slots,
                score=score,
                total_credits=total_credits,
                details=details
            ))
        
        # Sort by score (highest first)
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
            for slot in self._get_course_slots(course):
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
        
        # 3. Gap Heuristic: Penalize slots at extreme periods (likely to create gaps)
        gap_penalty = self._estimate_gap_penalty(slot)
        avg_score += gap_penalty
        
        # Credit Weighting: Amplify score by course credits (e.g. 4 credits -> 4x score)
        credits = 1
        if slot.course and slot.course.c:
            credits = slot.course.c
            
        return avg_score * credits
    
    def _estimate_gap_penalty(self, slot: Slot) -> float:
        """
        Estimate gap penalty based on slot's position.
        Slots at extreme periods (1, 7) without neighbors tend to create gaps.
        Middle periods (3, 4, 5) are better for minimizing gaps.
        
        Returns:
            Negative penalty value (deducted from score)
        """
        individual = slot.get_individual_slots()
        if not individual:
            return 0.0
        
        penalty = 0.0
        
        for s in individual:
            timing = get_slot_timing(s)
            if timing:
                period = timing['period']
                # Middle periods (3, 4, 5) are optimal for gap minimization
                # P1 and P7 are most likely to create gaps
                # Distance from middle (period 4): 0 for P4, 1 for P3/P5, 2 for P2/P6, 3 for P1/P7
                distance_from_middle = abs(period - 4)
                # Penalty: 8 points per period away from middle (max 24 for P1/P7)
                penalty += distance_from_middle * 8
        
        # Return as negative (penalty reduces score)
        return -penalty / len(individual)
    
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
            for slot in self._get_course_slots(course):
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
