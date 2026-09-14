"""Pure business rules: no app, no store."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app import rules
from app.models import WaitlistStatus as S
from app.models import is_valid_phone

T0 = datetime(2026, 9, 14, 18, 0, tzinfo=UTC)


@dataclass
class Entry:
    id: int
    joined_at: datetime
    status: S = S.WAITING
    notified_at: datetime | None = None
    seated_at: datetime | None = None
    canceled_at: datetime | None = None
    no_show_at: datetime | None = None


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


class TestQueuePosition:
    def test_orders_by_joined_at_not_id(self):
        # ids deliberately disagree with join order, so an id sort would fail.
        entries = [Entry(1, at(20)), Entry(2, at(5)), Entry(3, at(10))]
        assert [e.id for e in rules.active_queue(entries)] == [2, 3, 1]
        assert rules.queue_position(entries, 2) == 1
        assert rules.queue_position(entries, 1) == 3

    def test_counts_waiting_and_notified_only(self):
        entries = [
            Entry(1, at(0), S.SEATED),
            Entry(2, at(1), S.NOTIFIED),
            Entry(3, at(2), S.CANCELED),
            Entry(4, at(3), S.NO_SHOW),
            Entry(5, at(4)),
        ]
        assert rules.queue_position(entries, 2) == 1
        assert rules.queue_position(entries, 5) == 2
        assert rules.queue_position(entries, 1) is None

    def test_ties_break_by_id(self):
        entries = [Entry(9, at(0)), Entry(4, at(0))]
        assert [e.id for e in rules.active_queue(entries)] == [4, 9]


class TestTransitions:
    def test_waiting_can_go_anywhere_forward(self):
        assert rules.TRANSITIONS[S.WAITING] == {S.NOTIFIED, S.SEATED, S.CANCELED, S.NO_SHOW}

    def test_notified_cannot_be_notified_again_or_go_back(self):
        assert rules.TRANSITIONS[S.NOTIFIED] == {S.SEATED, S.CANCELED, S.NO_SHOW}
        assert not rules.can_transition(S.NOTIFIED, S.WAITING)

    @pytest.mark.parametrize("final", [S.SEATED, S.CANCELED, S.NO_SHOW])
    def test_final_statuses_are_terminal(self, final):
        assert rules.TRANSITIONS[final] == frozenset()


class TestNoShow:
    notified = Entry(1, at(0), S.NOTIFIED, notified_at=at(30))

    def test_not_overdue_one_second_before_deadline(self):
        assert not rules.is_overdue_no_show(self.notified, 10, at(40) - timedelta(seconds=1))

    def test_overdue_exactly_at_deadline(self):
        assert rules.is_overdue_no_show(self.notified, 10, at(40))

    def test_waiting_party_never_expires(self):
        assert not rules.is_overdue_no_show(Entry(2, at(0)), 10, at(600))


def test_estimated_ready_is_join_plus_quote():
    assert rules.estimate_ready_at(T0, 30) == datetime(2026, 9, 14, 18, 30, tzinfo=UTC)


class TestTodaysHistory:
    def test_keeps_today_only_most_recent_first(self):
        entries = [
            Entry(1, at(0), S.SEATED, seated_at=at(60)),
            Entry(2, at(0), S.CANCELED, canceled_at=at(120)),
            Entry(3, at(0), S.NO_SHOW, no_show_at=at(-60 * 20)),  # yesterday
            Entry(4, at(0)),
            Entry(5, at(0), S.NOTIFIED, notified_at=at(90)),
        ]
        assert [e.id for e in rules.todays_history(entries, at(180), UTC)] == [2, 1]

    def test_today_is_decided_in_the_local_timezone(self):
        # 23:30 UTC on the 13th is already the 14th in UTC+2.
        late = Entry(1, at(0), S.SEATED, seated_at=datetime(2026, 9, 13, 23, 30, tzinfo=UTC))
        plus_two = timezone(timedelta(hours=2))
        assert rules.todays_history([late], T0, UTC) == []
        assert rules.todays_history([late], T0, plus_two) == [late]


@pytest.mark.parametrize("value", ["555-010-6677", "(555) 201-4455", "+44 20 7946 0958", "5550106677"])
def test_valid_phones(value):
    assert is_valid_phone(value)


@pytest.mark.parametrize("value", ["call me", "12345", "555-CALL-NOW", "+1 555 010 6677 1234 5"])
def test_invalid_phones(value):
    assert not is_valid_phone(value)
