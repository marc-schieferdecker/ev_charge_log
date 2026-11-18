"""Tests for the pure charge detection logic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from custom_components.ev_charge_log.detection import (
    DetectionState,
    after_booking,
    booking_defaults,
    on_charging_state,
)

T0 = datetime(2026, 11, 15, 8, 0, tzinfo=UTC)


def _run(steps, state=None):
    """Apply (state, soc) steps one minute apart; return final transition list."""
    state = state or DetectionState()
    results = []
    for i, (new, soc) in enumerate(steps):
        result = on_charging_state(
            state, new, T0 + timedelta(minutes=i), soc, "zone.home"
        )
        results.append(result)
        state = result.state
    return state, results


def test_off_on_off_creates_pending_charge() -> None:
    state, results = _run([("off", 30), ("on", 31), ("off", 80)])
    assert results[1].started
    ended = results[2].ended
    assert ended is not None
    assert ended.soc_start == 31
    assert ended.soc_end == 80
    assert ended.started_at == T0 + timedelta(minutes=1)
    assert ended.ended_at == T0 + timedelta(minutes=2)
    assert ended.zone == "zone.home"
    assert state.pending == ended
    assert not state.charging


def test_unavailable_between_on_states_changes_nothing() -> None:
    state, results = _run([("off", 30), ("on", 31), ("unavailable", None), ("on", 50)])
    assert not any(r.started for r in results[2:])
    assert state.soc_start == 31
    assert state.charging


def test_unavailable_between_on_and_off_ends_charge() -> None:
    _, results = _run([("off", 30), ("on", 31), ("unavailable", None), ("off", 79)])
    assert results[3].ended is not None
    assert results[3].ended.soc_start == 31


def test_unavailable_between_off_and_on_starts_charge() -> None:
    _, results = _run([("off", 30), ("unknown", None), ("on", 32)])
    assert results[2].started


def test_off_unknown_off_changes_nothing() -> None:
    _, results = _run([("off", 30), ("unknown", None), ("off", 30)])
    assert not any(r.started or r.ended for r in results)


def test_first_off_is_no_charge_end() -> None:
    state, results = _run([("off", 30)])
    assert results[0].ended is None
    assert state.last_state == "off"


def test_first_on_counts_as_start() -> None:
    state, results = _run([("on", 40)])
    assert results[0].started
    assert state.soc_start == 40


def test_restart_does_not_start_a_new_charge() -> None:
    state, _ = _run([("off", 30), ("on", 31)])
    restored = DetectionState.from_dict(state.to_dict())
    again = on_charging_state(restored, "on", T0 + timedelta(hours=1), 60)
    assert not again.started
    assert again.state.soc_start == 31


def test_charge_ended_during_restart_is_detected() -> None:
    state, _ = _run([("off", 30), ("on", 31)])
    restored = DetectionState.from_dict(state.to_dict())
    result = on_charging_state(restored, "off", T0 + timedelta(hours=2), 85)
    assert result.ended is not None
    assert result.ended.soc_start == 31


def test_missing_soc_stays_empty() -> None:
    _, results = _run([("off", None), ("on", None), ("off", None)])
    assert results[2].ended.soc_start is None
    assert results[2].ended.soc_end is None


def test_state_roundtrip_with_pending() -> None:
    state, _ = _run([("off", 30), ("on", 31), ("off", 80)])
    assert DetectionState.from_dict(state.to_dict()) == state


# --- booking ------------------------------------------------------------------


def test_booking_takes_pending_values_once() -> None:
    state, _ = _run([("off", 30), ("on", 31), ("off", 80)])
    defaults = booking_defaults(state, soc_now=70)
    assert defaults.from_detection
    assert defaults.timestamp == T0 + timedelta(minutes=2)
    assert (defaults.soc_start, defaults.soc_end) == (31, 80)

    state = after_booking(state)
    assert state.pending is None
    second = booking_defaults(state, soc_now=70)
    assert not second.from_detection
    assert second.timestamp is None
    assert (second.soc_start, second.soc_end) == (None, 70)


def test_booking_while_charging_uses_start_and_current_soc() -> None:
    state, _ = _run([("off", 30), ("on", 31)])
    defaults = booking_defaults(state, soc_now=55)
    assert defaults.from_detection
    assert defaults.timestamp is None
    assert (defaults.soc_start, defaults.soc_end) == (31, 55)


def test_charge_booked_while_charging_creates_no_pending() -> None:
    state, _ = _run([("off", 30), ("on", 31)])
    state = after_booking(state)
    result = on_charging_state(state, "off", T0 + timedelta(hours=1), 80)
    assert result.ended is None
    assert result.state.pending is None
    # The next charge is detected normally again.
    result = on_charging_state(result.state, "on", T0 + timedelta(hours=5), 70)
    assert result.started


def test_no_detection_only_current_soc() -> None:
    defaults = booking_defaults(DetectionState(), soc_now=None)
    assert not defaults.from_detection
    assert (defaults.timestamp, defaults.soc_start, defaults.soc_end) == (
        None,
        None,
        None,
    )


def test_backdated_booking_takes_no_current_soc() -> None:
    defaults = booking_defaults(DetectionState(), soc_now=39, backdated=True)
    assert not defaults.from_detection
    assert (defaults.timestamp, defaults.soc_start, defaults.soc_end) == (
        None,
        None,
        None,
    )


def test_backdated_booking_ignores_running_charge() -> None:
    state, _ = _run([("off", 30), ("on", 31)])
    defaults = booking_defaults(state, soc_now=55, backdated=True)
    assert not defaults.from_detection
    assert (defaults.soc_start, defaults.soc_end) == (None, None)


def test_backdated_booking_still_takes_pending_charge() -> None:
    state, _ = _run([("off", 30), ("on", 31), ("off", 80)])
    defaults = booking_defaults(state, soc_now=70, backdated=True)
    assert defaults.from_detection
    assert (defaults.soc_start, defaults.soc_end) == (31, 80)


def test_booking_before_pending_day_leaves_pending_charge() -> None:
    state, _ = _run([("off", 30), ("on", 31), ("off", 80)])
    defaults = booking_defaults(state, soc_now=70, backdated=True, before_pending=True)
    assert not defaults.from_detection
    assert (defaults.timestamp, defaults.soc_start, defaults.soc_end) == (
        None,
        None,
        None,
    )
