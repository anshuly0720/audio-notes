from app.audio import MAX_PART_SEC, Silence, plan_chunks


def assert_valid(parts, duration):
    assert parts[0][0] == 0
    assert parts[-1][1] == round(duration, 3)
    for (a_start, a_end), (b_start, _) in zip(parts, parts[1:]):
        assert a_end == b_start                       # contiguous, no gaps or overlaps
    for start, end in parts:
        assert 0 < end - start <= MAX_PART_SEC + 1e-6  # never over the limit


def test_short_file_is_one_part():
    assert plan_chunks(17.1, []) == [(0.0, 17.1)]


def test_no_pauses_falls_back_to_hard_cuts():
    m = MAX_PART_SEC
    parts = plan_chunks(70.0, [])
    assert parts == [(0.0, m), (m, 2 * m), (2 * m, 70.0)]
    assert_valid(parts, 70.0)


def test_cuts_in_a_pause_inside_the_window():
    parts = plan_chunks(40.0, [Silence(24.0, 25.0)])
    assert parts[0] == (0.0, 24.5)       # midpoint of the pause
    assert_valid(parts, 40.0)


def test_prefers_the_longest_pause():
    silences = [Silence(15.0, 15.4), Silence(20.0, 21.0), Silence(26.0, 26.3)]
    parts = plan_chunks(40.0, silences)
    assert parts[0] == (0.0, 20.5)       # the 1.0 s pause beats 0.4 s and 0.3 s
    assert_valid(parts, 40.0)


def test_ignores_pauses_too_early_or_too_late():
    # one pause before MIN_PART_SEC, one after the window -> neither is usable
    silences = [Silence(3.0, 4.0), Silence(MAX_PART_SEC + 1, MAX_PART_SEC + 2)]
    parts = plan_chunks(40.0, silences)
    assert parts[0] == (0.0, MAX_PART_SEC)   # no valid pause -> hard cut
    assert_valid(parts, 40.0)


def test_long_file_stays_valid():
    silences = [Silence(t, t + 0.5) for t in range(5, 3600, 7)]
    parts = plan_chunks(3600.0, silences)
    assert_valid(parts, 3600.0)
    assert len(parts) < 3600 / 10        # not absurdly fragmented