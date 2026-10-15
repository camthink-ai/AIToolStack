"""TrainingStallMonitor pytest test suite"""
from backend.services.training_service import TrainingStallMonitor


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def make_monitor(clock=None, **overrides):
    messages = []
    stops = []
    config = {
        "log_fn": messages.append,
        "on_stall_stop": lambda: stops.append(True),
    }
    if clock is not None:
        config["time_fn"] = clock
    config.update(overrides)
    monitor = TrainingStallMonitor(**config)
    return monitor, messages, stops


class TestSlowButProgressingTraining:
    def test_slow_steady_batches_never_stall(self):
        clock = FakeClock()
        monitor, messages, stops = make_monitor(clock)
        monitor.record_batch()
        for _ in range(20):
            clock.advance(130)
            monitor.record_batch()
            monitor.check()
        assert stops == []
        assert monitor.stopped_by_stall is False
        assert monitor.stall_count == 0
        assert len(messages) == 20

    def test_slow_batches_reported_with_batch_index(self):
        clock = FakeClock()
        monitor, messages, stops = make_monitor(clock)
        monitor.record_batch()
        clock.advance(130)
        monitor.record_batch()
        assert any("batch 1" in message and "130s" in message for message in messages)

    def test_production_incident_timeline_does_not_stall(self):
        clock = FakeClock()
        monitor, messages, stops = make_monitor(clock)
        clock.advance(18)
        monitor.record_batch()
        for _ in range(3):
            for _ in range(26):
                clock.advance(5)
                monitor.check()
            monitor.record_batch()
        assert stops == []
        assert monitor.stopped_by_stall is False
        assert monitor.stall_count == 0
        assert not any("No batch completed" in message for message in messages)


class TestTrueStallDetection:
    def test_true_stall_stops_after_max_strikes(self):
        clock = FakeClock()
        monitor, messages, stops = make_monitor(clock)
        monitor.record_batch()
        clock.advance(1)
        monitor.record_batch()
        for expected_count in (1, 2, 3):
            clock.advance(120)
            monitor.check()
            assert monitor.stall_count == expected_count
        assert monitor.stopped_by_stall is True
        assert stops == [True]

    def test_no_duplicate_stop_requests_after_stopped(self):
        clock = FakeClock()
        monitor, messages, stops = make_monitor(clock)
        monitor.record_batch()
        clock.advance(1)
        monitor.record_batch()
        for _ in range(3):
            clock.advance(120)
            monitor.check()
        clock.advance(120)
        monitor.check()
        assert stops == [True]

    def test_no_strikes_before_first_batch(self):
        clock = FakeClock()
        monitor, messages, stops = make_monitor(clock)
        clock.advance(10000)
        monitor.check()
        assert monitor.stall_count == 0
        assert stops == []


class TestAdaptiveTimeout:
    def test_timeout_adapts_to_measured_batch_duration(self):
        clock = FakeClock()
        monitor, messages, stops = make_monitor(clock)
        monitor.record_batch()
        clock.advance(130)
        monitor.record_batch()
        clock.advance(300)
        monitor.check()
        assert monitor.stall_count == 0
        clock.advance(350)
        monitor.check()
        assert monitor.stall_count == 1
        assert stops == []

    def test_fast_training_uses_base_timeout(self):
        clock = FakeClock()
        monitor, messages, stops = make_monitor(clock)
        monitor.record_batch()
        clock.advance(130)
        monitor.record_batch()
        clock.advance(0.1)
        monitor.record_batch()
        clock.advance(120)
        monitor.check()
        assert monitor.stall_count == 1


class TestSuspension:
    def test_suspended_training_does_not_stall(self):
        clock = FakeClock()
        monitor, messages, stops = make_monitor(clock)
        monitor.record_batch()
        clock.advance(1000)
        monitor.check(suspended=True)
        assert monitor.stall_count == 0
        clock.advance(650)
        monitor.check()
        assert monitor.stall_count == 1


class TestReset:
    def test_reset_clears_stall_state(self):
        clock = FakeClock()
        monitor, messages, stops = make_monitor(clock)
        monitor.record_batch()
        clock.advance(1)
        monitor.record_batch()
        clock.advance(120)
        monitor.check()
        monitor.reset()
        assert monitor.stopped_by_stall is False
        assert monitor.stall_count == 0
        assert monitor.batches_done == 0
        clock.advance(120)
        monitor.check()
        assert monitor.stall_count == 0
        assert stops == []


class TestWatchdogLifecycle:
    def test_watchdog_starts_and_stops(self):
        clock = FakeClock()
        monitor, messages, stops = make_monitor(clock)
        monitor.start_watchdog(check_interval=0.01)
        assert monitor.watchdog_alive is True
        monitor.stop_watchdog()
        assert monitor.watchdog_alive is False

    def test_watchdog_reports_stall_via_callbacks(self):
        import time as time_module

        monitor, messages, stops = make_monitor(base_timeout=0.05)
        monitor.record_batch()
        monitor.start_watchdog(check_interval=0.02)
        deadline = time_module.monotonic() + 2.0
        while not monitor.stopped_by_stall and time_module.monotonic() < deadline:
            time_module.sleep(0.01)
        monitor.stop_watchdog()
        assert monitor.stopped_by_stall is True
        assert stops == [True]
