/**
 * Client-side timetable generator.
 *
 * Port of TimetableGenerator.generate_unified (utils/timetable_generator.py) so
 * suggestions are computed on the user's device instead of the server. The logic
 * (pool sizes, attempt limits, scoring, tiering, output shape) must stay identical
 * to the Python version - change both together.
 *
 * Loaded two ways:
 *   - as a Web Worker script: answers postMessage({ data, preferences, limit })
 *   - as a classic <script>: exposes window.TimetableEngine (main-thread fallback)
 * Written in plain ES2015 (no modules, no optional chaining) so it runs everywhere.
 */
(function (root) {
    'use strict';

    var hasOwn = Object.prototype.hasOwnProperty;

    // ---------- Python-compat helpers ----------

    function pyGet(obj, key, defaultValue) {
        return obj && hasOwn.call(obj, key) ? obj[key] : defaultValue;
    }

    function pyTruthy(value) {
        if (value === null || value === undefined) return false;
        if (Array.isArray(value)) return value.length > 0;
        if (typeof value === 'object') return Object.keys(value).length > 0;
        return Boolean(value);
    }

    // Python round(x, 2): nearest, ties to even (ties only occur at .xx5 exactly,
    // i.e. when x * 8 is an odd integer).
    function pyRound2(x) {
        var eighths = x * 8;
        if (Number.isInteger(eighths) && Math.abs(eighths % 2) === 1) {
            var lo = Math.floor(x * 100);
            return (lo % 2 === 0 ? lo : lo + 1) / 100;
        }
        return Number(x.toFixed(2));
    }

    // Mirrors the limit parsing/clamping in routes/generate.py suggest_timetable (max 500)
    function parseLimit(limit) {
        var n;
        if (typeof limit === 'number' && isFinite(limit)) {
            n = Math.trunc(limit);
        } else if (typeof limit === 'boolean') {
            n = limit ? 1 : 0;
        } else if (typeof limit === 'string' && /^\s*[+-]?\d+\s*$/.test(limit)) {
            n = parseInt(limit, 10);
        } else if (limit === undefined) {
            n = 5;
        } else {
            return 5;
        }
        return Math.max(1, Math.min(n, 500));
    }

    // random.shuffle (Fisher-Yates, in place)
    function shuffle(arr) {
        for (var i = arr.length - 1; i > 0; i--) {
            var j = Math.floor(Math.random() * (i + 1));
            var tmp = arr[i];
            arr[i] = arr[j];
            arr[j] = tmp;
        }
        return arr;
    }

    // list.sort(key=..., reverse=True): descending and stable
    function sortDescStable(items, keyFn) {
        var decorated = items.map(function (item, index) {
            return { item: item, key: keyFn(item), index: index };
        });
        decorated.sort(function (a, b) {
            if (a.key !== b.key) return a.key > b.key ? -1 : 1;
            return a.index - b.index;
        });
        for (var i = 0; i < items.length; i++) items[i] = decorated[i].item;
        return items;
    }

    // Slot.get_individual_slots
    function getIndividualSlots(slotCode) {
        return String(slotCode).replace(/\//g, '+').split('+');
    }

    // ---------- Generator ----------

    function TimetableGenerator(data, rawPreferences) {
        var p = rawPreferences || {};
        // Same defaults as GenerationPreferences / routes/generate.py
        this.preferences = {
            avoid_early_morning: pyGet(p, 'avoid_early_morning', false),
            avoid_late_evening: pyGet(p, 'avoid_late_evening', false),
            time_mode: pyGet(p, 'time_mode', 'none'),
            course_faculty_preferences: pyGet(p, 'course_faculty_preferences', {}) || {}
        };

        this.timings = new Map();
        var slotTimings = data.slot_timings || {};
        Object.keys(slotTimings).forEach(function (code) {
            this.timings.set(code, slotTimings[code]);
        }, this);

        this.exclusionGroups = (data.mutual_exclusion_groups || []).map(function (pair) {
            return [new Set(pair[0]), new Set(pair[1])];
        });

        this.warnings = [];
        this.courses = [];
        this.slotMap = {};
        this.allSlots = [];

        (data.courses || []).forEach(function (rawCourse) {
            var course = {
                id: String(rawCourse.id),
                code: rawCourse.code,
                name: rawCourse.name,
                credits: rawCourse.credits
            };
            this.courses.push(course);

            // _build_slot_map(randomize_only=True, ignore_preferences=True):
            // drop faulty slots, then shuffle. This is the slot map every
            // generate_unified path ends up using.
            var slots = [];
            (rawCourse.slots || []).forEach(function (rawSlot) {
                var slot = {
                    index: -1,
                    id: String(rawSlot.id),
                    slot_code: rawSlot.slot_code,
                    course: course,
                    faculty_name: rawSlot.faculty_name,
                    has_faculty: rawSlot.faculty_name !== null && rawSlot.faculty_name !== undefined,
                    venue: rawSlot.venue,
                    codes: getIndividualSlots(rawSlot.slot_code),
                    timingKeys: []
                };
                if (this._isSlotFaulty(slot)) return;
                slot.timingKeys = slot.codes.map(function (code) {
                    var t = this.timings.get(code);
                    return t.day + '|' + t.period;
                }, this);
                slot.index = this.allSlots.length;
                this.allSlots.push(slot);
                slots.push(slot);
            }, this);

            shuffle(slots);
            this.slotMap[course.id] = slots;
        }, this);

        // Lazily filled clash cache: 0 = unknown, 1 = no clash, 2 = clash
        var n = this.allSlots.length;
        this._clashCache = new Uint8Array(n * n);
    }

    TimetableGenerator.prototype._getSlotTiming = function (code) {
        return this.timings.has(code) ? this.timings.get(code) : null;
    };

    // _is_slot_faulty
    TimetableGenerator.prototype._isSlotFaulty = function (slot) {
        var faultyCodes = [];
        for (var i = 0; i < slot.codes.length; i++) {
            if (!this._getSlotTiming(slot.codes[i])) faultyCodes.push(slot.codes[i]);
        }
        if (faultyCodes.length) {
            var msg = 'Excluded ' + (slot.has_faculty ? slot.faculty_name : 'Unknown Faculty') +
                ' for ' + (slot.course ? slot.course.code : 'Unknown Course') +
                ': Unknown slot code(s) ' + faultyCodes.join(', ');
            if (this.warnings.indexOf(msg) === -1) this.warnings.push(msg);
            return true;
        }
        return false;
    };

    // _check_clash (time overlap or mutual exclusion), cached per slot pair
    TimetableGenerator.prototype._checkClash = function (slot1, slot2) {
        var n = this.allSlots.length;
        var cacheIdx = slot1.index * n + slot2.index;
        var cached = this._clashCache[cacheIdx];
        if (cached) return cached === 2;

        var result = this._computeClash(slot1, slot2);
        var value = result ? 2 : 1;
        this._clashCache[cacheIdx] = value;
        this._clashCache[slot2.index * n + slot1.index] = value;
        return result;
    };

    TimetableGenerator.prototype._computeClash = function (slot1, slot2) {
        var slots1 = new Set(slot1.codes);
        var slots2 = new Set(slot2.codes);

        function intersects(set, group) {
            var hit = false;
            set.forEach(function (code) { if (group.has(code)) hit = true; });
            return hit;
        }

        for (var g = 0; g < this.exclusionGroups.length; g++) {
            var groupA = this.exclusionGroups[g][0];
            var groupB = this.exclusionGroups[g][1];
            var has1InA = intersects(slots1, groupA);
            var has1InB = intersects(slots1, groupB);
            var has2InA = intersects(slots2, groupA);
            var has2InB = intersects(slots2, groupB);
            if ((has1InA && has2InB) || (has1InB && has2InA)) return true;
        }

        var codes1 = Array.from(slots1);
        var codes2 = Array.from(slots2);
        for (var i = 0; i < codes1.length; i++) {
            for (var j = 0; j < codes2.length; j++) {
                var t1 = this._getSlotTiming(codes1[i]);
                var t2 = this._getSlotTiming(codes2[j]);
                if (t1 && t2 && t1.day === t2.day && t1.period === t2.period) return true;
            }
        }
        return false;
    };

    // _try_random_timetable
    TimetableGenerator.prototype._tryRandomTimetable = function () {
        var selected = [];
        var occupied = new Set();

        var courses = shuffle(this.courses.slice());

        for (var c = 0; c < courses.length; c++) {
            var slots = this.slotMap[courses[c].id] || [];
            if (!slots.length) return null;

            shuffle(slots);
            var found = false;
            var tryCount = Math.min(10, slots.length);  // Try up to 10 random slots

            for (var s = 0; s < tryCount; s++) {
                var slot = slots[s];
                var hasClash = false;
                for (var e = 0; e < selected.length; e++) {
                    if (this._checkClash(slot, selected[e])) {
                        hasClash = true;
                        break;
                    }
                }

                if (!hasClash) {
                    var slotTimes = [];
                    var timeClash = false;
                    for (var k = 0; k < slot.timingKeys.length; k++) {
                        if (occupied.has(slot.timingKeys[k])) {
                            timeClash = true;
                            break;
                        }
                        slotTimes.push(slot.timingKeys[k]);
                    }

                    if (!timeClash) {
                        selected.push(slot);
                        slotTimes.forEach(function (key) { occupied.add(key); });
                        found = true;
                        break;
                    }
                }
            }

            if (!found) return null;
        }

        return selected.length === this.courses.length ? selected : null;
    };

    function signature(slots) {
        return slots.map(function (s) { return s.id; }).sort().join(',');
    }

    function totalCredits(slots) {
        var total = 0;
        for (var i = 0; i < slots.length; i++) total += slots[i].course.credits || 0;
        return total;
    }

    // _generate_random_solutions (no filters scenario)
    TimetableGenerator.prototype._generateRandomSolutions = function (targetSize) {
        var solutions = [];
        var seen = new Set();
        var maxAttempts = targetSize * 100;
        var attempts = 0;

        while (solutions.length < targetSize && attempts < maxAttempts) {
            attempts++;
            var result = this._tryRandomTimetable();
            if (result) {
                var sig = signature(result);
                if (!seen.has(sig)) {
                    seen.add(sig);
                    solutions.push({
                        slots: result,
                        score: 0,  // No scoring for random
                        total_credits: totalCredits(result),
                        details: { method: 'random', pool_size: solutions.length }
                    });
                }
            }
        }
        return solutions;
    };

    // _generate_random_pool
    TimetableGenerator.prototype._generateRandomPool = function (targetPool) {
        var pool = [];
        var seen = new Set();
        var maxAttempts = targetPool * 10;
        var attempts = 0;

        // Early termination: stop if no new solutions found in N attempts
        var noProgressCount = 0;
        var noProgressLimit = 1000;

        while (pool.length < targetPool && attempts < maxAttempts) {
            attempts++;
            var result = this._tryRandomTimetable();

            if (result) {
                var sig = signature(result);
                if (!seen.has(sig)) {
                    seen.add(sig);
                    pool.push(result);
                    noProgressCount = 0;
                } else {
                    noProgressCount++;
                }
            } else {
                noProgressCount++;
            }

            if (noProgressCount >= noProgressLimit) break;
        }
        return pool;
    };

    TimetableGenerator.prototype._coursePrefs = function (slot) {
        var cfp = this.preferences.course_faculty_preferences;
        var prefs = pyGet(cfp, slot.course.id, []);
        return Array.isArray(prefs) ? prefs : [];
    };

    // _count_preferred_teachers
    TimetableGenerator.prototype._countPreferredTeachers = function (slots) {
        var count = 0;
        for (var i = 0; i < slots.length; i++) {
            var prefs = this._coursePrefs(slots[i]);
            if (slots[i].has_faculty && prefs.indexOf(slots[i].faculty_name) !== -1) count++;
        }
        return count;
    };

    // Per-cell part of _calculate_time_score
    TimetableGenerator.prototype._cellTimeScore = function (period) {
        var mode = this.preferences.time_mode;
        if (pyTruthy(this.preferences.avoid_early_morning) && period === 1) return 0;
        if (pyTruthy(this.preferences.avoid_late_evening) && period === 7) return 0;
        if (mode === 'morning') return Math.max(0, 115 - (15 * period));
        if (mode === 'afternoon' || mode === 'evening') return Math.max(0, 10 + (15 * (period - 1)));
        if (mode === 'middle') return Math.max(0, 100 - (30 * Math.abs(period - 4)));
        return 50;
    };

    // Sum of cell scores and number of timed cells for one slot
    TimetableGenerator.prototype._slotTimeParts = function (slot) {
        var sum = 0;
        var cells = 0;
        for (var j = 0; j < slot.codes.length; j++) {
            var timing = this._getSlotTiming(slot.codes[j]);
            if (!timing) continue;
            cells++;
            sum += this._cellTimeScore(timing.period);
        }
        return { sum: sum, cells: cells };
    };

    // _calculate_time_score
    TimetableGenerator.prototype._calculateTimeScore = function (slots) {
        var totalScore = 0;
        var cellCount = 0;
        for (var i = 0; i < slots.length; i++) {
            var parts = this._slotTimeParts(slots[i]);
            totalScore += parts.sum;
            cellCount += parts.cells;
        }
        return cellCount > 0 ? totalScore / cellCount : 0;
    };

    // _calculate_teacher_priority_score
    TimetableGenerator.prototype._calculateTeacherPriorityScore = function (slots) {
        var totalScore = 0;
        for (var i = 0; i < slots.length; i++) {
            var prefs = this._coursePrefs(slots[i]);
            if (slots[i].has_faculty) {
                var rank = prefs.indexOf(slots[i].faculty_name);
                if (rank === 0) totalScore += 1000;
                else if (rank === 1) totalScore += 800;
                else if (rank === 2) totalScore += 600;
            }
        }
        return totalScore;
    };

    // _build_solution_details
    TimetableGenerator.prototype._buildSolutionDetails = function (slots) {
        var details = {
            preferred_faculty_matches: 0,
            gaps_per_day: {},
            saturday_classes: 0
        };
        var cfp = this.preferences.course_faculty_preferences;

        for (var i = 0; i < slots.length; i++) {
            var slot = slots[i];
            if (slot.has_faculty && slot.course.id) {
                if (hasOwn.call(cfp, slot.course.id)) {
                    var prefs = cfp[slot.course.id];
                    if (Array.isArray(prefs) && prefs.indexOf(slot.faculty_name) !== -1) {
                        details.preferred_faculty_matches++;
                    }
                }
            }
        }

        // Insertion-ordered like the Python dict
        var dayPeriods = new Map();
        for (var a = 0; a < slots.length; a++) {
            var codes = slots[a].codes;
            for (var b = 0; b < codes.length; b++) {
                var timing = this._getSlotTiming(codes[b]);
                if (!timing) continue;
                if (!dayPeriods.has(timing.day)) dayPeriods.set(timing.day, []);
                dayPeriods.get(timing.day).push(timing.period);
                if (timing.day === 'SAT') details.saturday_classes++;
            }
        }

        var totalGaps = 0;
        dayPeriods.forEach(function (periods, day) {
            periods.sort(function (x, y) { return x - y; });
            var gaps = 0;
            for (var k = 1; k < periods.length; k++) {
                var gap = periods[k] - periods[k - 1] - 1;
                if (gap > 0) gaps += gap;
            }
            details.gaps_per_day[day] = gaps;
            totalGaps += gaps;
        });

        details.total_gaps = totalGaps;
        return details;
    };

    // _rank_by_time (SCENARIO 2: TIME ONLY)
    TimetableGenerator.prototype._rankByTime = function (scoredPool, targetSize) {
        sortDescStable(scoredPool, function (x) { return x.time_score; });

        var results = [];
        var top = scoredPool.slice(0, targetSize);
        for (var i = 0; i < top.length; i++) {
            var item = top[i];
            var details = this._buildSolutionDetails(item.slots);
            details.method = 'time_ranked';
            details.time_score = pyRound2(item.time_score);
            details.pool_size = scoredPool.length;
            results.push({
                slots: item.slots,
                score: item.time_score,
                total_credits: item.total_credits,
                details: details
            });
        }
        return results;
    };

    // _rank_tiered_by_teacher_priority (SCENARIO 3) and _rank_tiered_by_time (SCENARIO 4)
    TimetableGenerator.prototype._rankTiered = function (scoredPool, targetSize, byTime) {
        var numCourses = this.courses.length;
        var results = [];

        for (var tier = numCourses; tier >= 0; tier--) {
            var tierItems = scoredPool.filter(function (x) { return x.teacher_match_count === tier; });
            sortDescStable(tierItems, byTime
                ? function (x) { return x.time_score; }
                : function (x) { return x.teacher_priority_score; });

            for (var i = 0; i < tierItems.length; i++) {
                if (results.length >= targetSize) break;
                var item = tierItems[i];
                var details = this._buildSolutionDetails(item.slots);
                if (byTime) {
                    details.method = 'tiered_time_ranked';
                    details.teacher_match_count = item.teacher_match_count;
                    details.time_score = pyRound2(item.time_score);
                } else {
                    details.method = 'tiered_teacher_priority';
                    details.teacher_match_count = item.teacher_match_count;
                    details.teacher_priority_score = pyRound2(item.teacher_priority_score);
                }
                details.tier = tier;
                details.pool_size = scoredPool.length;
                results.push({
                    slots: item.slots,
                    score: byTime ? item.time_score : item.teacher_priority_score,
                    total_credits: item.total_credits,
                    details: details
                });
            }

            if (results.length >= targetSize) break;
        }
        return results;
    };

    // generate_unified
    TimetableGenerator.prototype.generateUnified = function (targetSize) {
        var hasTeacherPrefs = pyTruthy(this.preferences.course_faculty_preferences);
        var hasTimePrefs = (
            this.preferences.time_mode !== 'none' ||
            pyTruthy(this.preferences.avoid_early_morning) ||
            pyTruthy(this.preferences.avoid_late_evening)
        );

        if (!hasTeacherPrefs && !hasTimePrefs) {
            // SCENARIO 1: NO FILTERS - just generate random
            return this._generateRandomSolutions(targetSize);
        }

        var pool = this._generateRandomPool(20000);
        if (!pool.length) return [];

        var scoredPool = [];
        for (var i = 0; i < pool.length; i++) {
            var slots = pool[i];
            scoredPool.push({
                slots: slots,
                teacher_match_count: this._countPreferredTeachers(slots),
                time_score: this._calculateTimeScore(slots),
                teacher_priority_score: this._calculateTeacherPriorityScore(slots),
                total_credits: totalCredits(slots)
            });
        }

        if (hasTimePrefs && !hasTeacherPrefs) {
            return this._rankByTime(scoredPool, targetSize);
        } else if (hasTeacherPrefs && !hasTimePrefs) {
            return this._rankTiered(scoredPool, targetSize, false);
        }
        return this._rankTiered(scoredPool, targetSize, true);
    };

    // ---------- Exact search with variety (default, browser only) ----------
    //
    // Same ranking as generate_unified, but instead of sampling a random pool it
    // finds the true best timetables with branch-and-bound:
    //   time only     -> highest time_score
    //   teacher only  -> most preferred teachers, then teacher_priority_score
    //   both          -> most preferred teachers, then time_score
    // Variety: every suggestion must use a different time slot from each earlier
    // suggestion in at least `minDiff` courses. Teachers that share a time slot
    // are collapsed into one option (the best-ranked teacher for that slot).

    function compareKeys(a, b) {
        for (var i = 0; i < a.length; i++) {
            if (a[i] !== b[i]) return a[i] > b[i] ? 1 : -1;
        }
        return 0;
    }

    TimetableGenerator.prototype._buildExactOptions = function () {
        var self = this;
        return this.courses.map(function (course, courseIdx) {
            var byPattern = new Map();
            (self.slotMap[course.id] || []).slice().sort(function (x, y) {
                return x.index - y.index;  // undo the shuffle: deterministic
            }).forEach(function (slot) {
                var prefs = self._coursePrefs(slot);
                var rank = slot.has_faculty ? prefs.indexOf(slot.faculty_name) : -1;
                var parts = self._slotTimeParts(slot);
                var opt = {
                    slot: slot,
                    courseIdx: courseIdx,
                    pattern: slot.slot_code,
                    m: rank !== -1 ? 1 : 0,
                    tp: rank === 0 ? 1000 : (rank === 1 ? 800 : (rank === 2 ? 600 : 0)),
                    cs: parts.sum,
                    cc: parts.cells
                };
                var best = byPattern.get(opt.pattern);
                if (!best || opt.m > best.m || (opt.m === best.m && opt.tp > best.tp)) {
                    byPattern.set(opt.pattern, opt);
                }
            });
            return Array.from(byPattern.values());
        });
    };

    TimetableGenerator.prototype._patternDistance = function (slotsA, slotsB) {
        var codesB = {};
        slotsB.forEach(function (s) { codesB[s.course.id] = s.slot_code; });
        var diff = 0;
        slotsA.forEach(function (s) { if (codesB[s.course.id] !== s.slot_code) diff++; });
        return diff;
    };

    // No preferences: nothing to rank, so keep random picks, but varied
    TimetableGenerator.prototype._generateRandomVaried = function (targetSize, minDiff) {
        var accepted = [];
        var maxAttempts = targetSize * 100;
        for (var attempts = 0; accepted.length < targetSize && attempts < maxAttempts; attempts++) {
            var result = this._tryRandomTimetable();
            if (!result) continue;
            var ok = true;
            for (var i = 0; i < accepted.length && ok; i++) {
                if (this._patternDistance(result, accepted[i].slots) < minDiff) ok = false;
            }
            if (ok) {
                accepted.push({
                    slots: result,
                    score: 0,
                    total_credits: totalCredits(result),
                    details: { method: 'random_varied', pool_size: accepted.length, min_diff: minDiff }
                });
            }
        }
        return { solutions: accepted, complete: true, nodes: 0 };
    };

    TimetableGenerator.prototype.generateExact = function (targetSize, minDiff, nodeBudget, candidatePoolSize) {
        var self = this;
        var hasTeacherPrefs = pyTruthy(this.preferences.course_faculty_preferences);
        var hasTimePrefs = (
            this.preferences.time_mode !== 'none' ||
            pyTruthy(this.preferences.avoid_early_morning) ||
            pyTruthy(this.preferences.avoid_late_evening)
        );
        if (!hasTeacherPrefs && !hasTimePrefs) return this._generateRandomVaried(targetSize, minDiff);

        var scenario = !hasTeacherPrefs ? 'time' : (!hasTimePrefs ? 'teacher' : 'both');
        var n = this.courses.length;
        var optionsByCourse = this._buildExactOptions();
        for (var c = 0; c < n; c++) {
            if (!optionsByCourse[c].length) return { solutions: [], complete: true, nodes: 0 };
        }

        // Search order: fewest options first; within a course, best options first
        var order = optionsByCourse.map(function (opts, idx) { return idx; })
            .sort(function (x, y) { return optionsByCourse[x].length - optionsByCourse[y].length; });
        var levels = order.map(function (courseIdx) {
            return optionsByCourse[courseIdx].slice().sort(function (x, y) {
                return (y.m - x.m) || (y.tp - x.tp) ||
                    ((y.cc ? y.cs / y.cc : 0) - (x.cc ? x.cs / x.cc : 0));
            });
        });

        // Optimistic bounds for the courses not yet chosen (clashes ignored)
        var sufM = new Array(n + 1).fill(0);
        var sufTP = new Array(n + 1).fill(0);
        var sufCS = new Array(n + 1);  // sufCS[d][cells] = best cell-score sum using exactly `cells` cells
        sufCS[n] = [0];
        for (var d = n - 1; d >= 0; d--) {
            var maxM = 0, maxTP = 0, next = sufCS[d + 1], cur = [];
            levels[d].forEach(function (opt) {
                if (opt.m > maxM) maxM = opt.m;
                if (opt.tp > maxTP) maxTP = opt.tp;
                for (var cells = 0; cells < next.length; cells++) {
                    if (next[cells] === undefined) continue;
                    var v = next[cells] + opt.cs, at = cells + opt.cc;
                    if (cur[at] === undefined || v > cur[at]) cur[at] = v;
                }
            });
            sufM[d] = sufM[d + 1] + maxM;
            sufTP[d] = sufTP[d + 1] + maxTP;
            sufCS[d] = cur;
        }

        function ratioBound(depth, cs, cc) {
            var best = -Infinity, table = sufCS[depth];
            for (var cells = 0; cells < table.length; cells++) {
                if (table[cells] === undefined) continue;
                var total = cc + cells;
                var r = total > 0 ? (cs + table[cells]) / total : 0;
                if (r > best) best = r;
            }
            return best;
        }

        function makeKey(m, tp, cs, cc) {
            var ratio = cc > 0 ? cs / cc : 0;
            if (scenario === 'time') return [ratio];
            if (scenario === 'teacher') return [m, tp];
            return [m, ratio];
        }

        function boundKey(depth, m, tp, cs, cc) {
            if (scenario === 'time') return [ratioBound(depth, cs, cc)];
            if (scenario === 'teacher') return [m + sufM[depth], tp + sufTP[depth]];
            return [m + sufM[depth], ratioBound(depth, cs, cc)];
        }

        var nodes = 0;
        var leaves = 0;
        var aborted = false;
        var picked = [];       // accepted results: { opts (by course idx), key }
        var chosen = [];       // options on the current DFS path
        var matches = [];      // per picked result: courses sharing the same time slot so far
        var maxMatches = n - minDiff;

        // Finds up to k best timetables honouring the variety rule against `picked`.
        // Returned best-first; ties keep the order they were found in.
        function search(k) {
            var heap = [];  // min-heap of the k best so far: heap[0] is the worst kept
            var seq = 0;

            function worse(a, b) {
                var c = compareKeys(a.key, b.key);
                return c < 0 || (c === 0 && a.seq > b.seq);
            }

            function siftUp(i) {
                while (i > 0) {
                    var parent = (i - 1) >> 1;
                    if (!worse(heap[i], heap[parent])) break;
                    var t = heap[i]; heap[i] = heap[parent]; heap[parent] = t;
                    i = parent;
                }
            }

            function siftDown(i) {
                for (;;) {
                    var l = 2 * i + 1, r = l + 1, w = i;
                    if (l < heap.length && worse(heap[l], heap[w])) w = l;
                    if (r < heap.length && worse(heap[r], heap[w])) w = r;
                    if (w === i) break;
                    var t = heap[i]; heap[i] = heap[w]; heap[w] = t;
                    i = w;
                }
            }

            function offer(key, opts) {
                if (heap.length === k) {
                    if (compareKeys(key, heap[0].key) <= 0) return;
                    heap[0] = { key: key, opts: opts, seq: seq++ };
                    siftDown(0);
                } else {
                    heap.push({ key: key, opts: opts, seq: seq++ });
                    siftUp(heap.length - 1);
                }
            }

            function dfs(depth, m, tp, cs, cc) {
                if (aborted) return;
                if (++nodes > nodeBudget) { aborted = true; return; }
                if (depth === n) {
                    leaves++;
                    offer(makeKey(m, tp, cs, cc), chosen.slice());
                    return;
                }
                if (heap.length === k && compareKeys(boundKey(depth, m, tp, cs, cc), heap[0].key) <= 0) return;

                var opts = levels[depth];
                for (var i = 0; i < opts.length; i++) {
                    var opt = opts[i];
                    var clash = false;
                    for (var j = 0; j < chosen.length; j++) {
                        if (self._checkClash(opt.slot, chosen[j].slot)) { clash = true; break; }
                    }
                    if (clash) continue;

                    var tooSimilar = false;
                    for (var p = 0; p < picked.length; p++) {
                        if (picked[p].byCourse[opt.courseIdx] === opt.pattern && matches[p] + 1 > maxMatches) {
                            tooSimilar = true;
                            break;
                        }
                    }
                    if (tooSimilar) continue;
                    for (p = 0; p < picked.length; p++) {
                        if (picked[p].byCourse[opt.courseIdx] === opt.pattern) matches[p]++;
                    }

                    chosen.push(opt);
                    dfs(depth + 1, m + opt.m, tp + opt.tp, cs + opt.cs, cc + opt.cc);
                    chosen.pop();

                    for (p = 0; p < picked.length; p++) {
                        if (picked[p].byCourse[opt.courseIdx] === opt.pattern) matches[p]--;
                    }
                    if (aborted) return;
                }
            }

            dfs(0, 0, 0, 0, 0);
            return heap.sort(function (a, b) { return compareKeys(b.key, a.key) || (a.seq - b.seq); });
        }

        function accept(item) {
            var byCourse = [];
            item.opts.forEach(function (opt) { byCourse[opt.courseIdx] = opt.pattern; });
            picked.push({ key: item.key, opts: item.opts, byCourse: byCourse });
            matches.push(0);
        }

        function farEnough(item) {
            for (var p = 0; p < picked.length; p++) {
                var same = 0;
                for (var j = 0; j < item.opts.length; j++) {
                    if (picked[p].byCourse[item.opts[j].courseIdx] === item.opts[j].pattern) same++;
                }
                if (same > maxMatches) return false;
            }
            return true;
        }

        if (minDiff <= 1) {
            // Distinct time patterns only: one search gives the exact top-k
            search(targetSize).forEach(accept);
        } else {
            // Greedy: each pick is the best timetable far enough from all earlier picks.
            // Work in rounds: fetch the exact best batch that is far enough from the
            // picks so far, then scan it best-first. Anything outside the batch ranks
            // below everything in it, so the first far-enough candidate is always the
            // best possible next pick. A short batch means nothing else is left.
            while (picked.length < targetSize && !aborted) {
                var batch = search(candidatePoolSize);
                for (var bi = 0; bi < batch.length && picked.length < targetSize; bi++) {
                    if (farEnough(batch[bi])) accept(batch[bi]);
                }
                if (batch.length < candidatePoolSize) break;
            }
        }

        var methodName = scenario === 'time' ? 'exact_time_ranked'
            : (scenario === 'teacher' ? 'exact_tiered_teacher_priority' : 'exact_tiered_time_ranked');

        var solutions = picked.map(function (item) {
            var slots = item.opts.slice().sort(function (x, y) { return x.courseIdx - y.courseIdx; })
                .map(function (opt) { return opt.slot; });
            var details = self._buildSolutionDetails(slots);
            var timeScore = self._calculateTimeScore(slots);
            var teacherScore = self._calculateTeacherPriorityScore(slots);
            var teacherCount = self._countPreferredTeachers(slots);
            details.method = methodName;
            if (scenario !== 'time') {
                details.teacher_match_count = teacherCount;
                details.tier = teacherCount;
            }
            if (scenario === 'teacher') details.teacher_priority_score = pyRound2(teacherScore);
            else details.time_score = pyRound2(timeScore);
            details.pool_size = leaves;
            details.min_diff = minDiff;
            return {
                slots: slots,
                score: scenario === 'teacher' ? teacherScore : timeScore,
                total_credits: totalCredits(slots),
                details: details
            };
        });

        return { solutions: solutions, complete: !aborted, nodes: nodes };
    };

    // TimetableSolution.to_dict
    function solutionToDict(solution) {
        return {
            slots: solution.slots.map(function (s) {
                return {
                    slot_id: s.id,
                    slot_code: s.slot_code,
                    course_id: s.course.id || '',
                    course_code: s.course.code,
                    course_name: s.course.name,
                    faculty_name: s.has_faculty ? s.faculty_name : '',
                    venue: s.venue,
                    credits: s.course.credits
                };
            }),
            score: pyRound2(solution.score),
            total_credits: solution.total_credits,
            details: solution.details
        };
    }

    /**
     * Same response shape as POST /api/generate/suggest.
     * @param data        response of POST /api/generate/data
     * @param preferences same object the page sends to /suggest
     * @param limit       number of suggestions wanted (clamped to 1..100)
     */
    function generateSuggestions(data, preferences, limit) {
        var generator = new TimetableGenerator(data, preferences);
        var solutions = generator.generateUnified(parseLimit(limit));

        var generationMethod = solutions.length ? (solutions[0].details.method || 'unknown') : 'none';
        var poolSize = solutions.length ? (solutions[0].details.pool_size || 0) : 0;

        return {
            success: true,
            suggestions: solutions.map(solutionToDict),
            count: solutions.length,
            has_more: false,
            generation_method: generationMethod,
            total_combinations: poolSize,
            relaxed_constraints: false,  // Deprecated but kept for frontend compat
            warnings: generator.warnings
        };
    }

    var EXACT_DEFAULT_MIN_DIFF = 2;
    var EXACT_NODE_BUDGET = 5000000;  // safety stop for very large course sets
    var EXACT_CANDIDATE_POOL = 3000;  // timetables fetched per round of the variety search

    /**
     * Exact best-first search with variety. Same response shape as
     * generateSuggestions, plus `search_complete` (false if the node budget ran out,
     * in which case the best timetables found so far are returned).
     * @param options.minDiff courses that must differ in time slot between any two suggestions
     */
    function generateExactSuggestions(data, preferences, limit, options) {
        var opts = options || {};
        var minDiff = parseInt(opts.minDiff, 10);
        if (!(minDiff >= 1)) minDiff = EXACT_DEFAULT_MIN_DIFF;

        var generator = new TimetableGenerator(data, preferences);
        minDiff = Math.min(minDiff, Math.max(1, generator.courses.length));
        var outcome = generator.generateExact(parseLimit(limit), minDiff,
            opts.nodeBudget || EXACT_NODE_BUDGET, opts.candidatePoolSize || EXACT_CANDIDATE_POOL);
        var solutions = outcome.solutions;

        return {
            success: true,
            suggestions: solutions.map(solutionToDict),
            count: solutions.length,
            has_more: false,
            generation_method: solutions.length ? solutions[0].details.method : 'none',
            total_combinations: solutions.length ? (solutions[0].details.pool_size || 0) : 0,
            relaxed_constraints: false,
            warnings: generator.warnings,
            search_complete: outcome.complete,
            search_nodes: outcome.nodes,
            min_diff: minDiff
        };
    }

    function runRequest(msg) {
        if (msg.mode === 'exact') {
            return generateExactSuggestions(msg.data, msg.preferences, msg.limit, msg.options);
        }
        return generateSuggestions(msg.data, msg.preferences, msg.limit);
    }

    root.TimetableEngine = {
        generateSuggestions: generateSuggestions,
        generateExactSuggestions: generateExactSuggestions,
        run: runRequest
    };

    // Web Worker entry point
    if (typeof WorkerGlobalScope !== 'undefined' && root instanceof WorkerGlobalScope) {
        root.onmessage = function (event) {
            var msg = event.data || {};
            try {
                root.postMessage({ ok: true, result: runRequest(msg) });
            } catch (err) {
                root.postMessage({ ok: false, error: String((err && err.message) || err) });
            }
        };
    }
})(typeof self !== 'undefined' ? self : this);
