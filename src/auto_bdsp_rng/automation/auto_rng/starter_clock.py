"""Clock-driven adaptation of Project_Xs player_blink_gui.py, no GUI or keypresses.

Keep the original RNG call order (including uncounted transition/queue calls),
1.018s tracking, 1.017s Timeline, and second delay on Timeline event eleven.
"""
import heapq


def seed_payload(rng):
    words = [f'{value:08X}' for value in rng.get_state()]
    return {'words': words, 'pair': [words[0] + words[1], words[2] + words[3]]}


class TimelineClock:
    def __init__(self, rng, config, advances, now):
        self.rng, self.config, self.advances = rng, config, advances
        self.starts_at = now + config.get('timeDelay', 0.)
        self.started = False
        self.queue = []
        self.delay2_count = 10
        self.delay2_zero_at = None
        self.delay2_at = None
        self.rng.next()
        if config.get('menuClose', True):
            self.rng.next()

    def update(self, now):
        if not self.started and now + 1e-9 >= self.starts_at:
            self.started = True
            delay = self.config.get('advanceDelay', 0)
            self.rng.advance(delay)
            self.advances += delay
            for _ in range(self.config.get('timelineNpc', 0) + 1):
                heapq.heappush(self.queue, (self.starts_at + 1.017, 0))
            for _ in range(self.config.get('pokemonNpc', 0)):
                heapq.heappush(self.queue, (self.starts_at + self.rng.rangefloat(3, 12) + .285, 1))
        while self.queue and self.queue[0][0] <= now + 1e-9:
            timestamp, kind = heapq.heappop(self.queue)
            self.advances += 1
            delay = self.config.get('advanceDelay2', 0)
            if delay:
                if self.delay2_count > 0:
                    self.delay2_count -= 1
                    if self.delay2_count == 0:
                        self.delay2_zero_at = timestamp
                elif self.delay2_count == 0:
                    self.delay2_count = -1
                    self.delay2_at = timestamp
                    self.rng.advance(delay)
                    self.advances += delay
            if kind == 0:
                self.rng.next()
                heapq.heappush(self.queue, (timestamp + 1.017, 0))
            else:
                heapq.heappush(self.queue, (timestamp + self.rng.rangefloat(3, 12) + .285, 1))
        next_at = self.queue[0][0] if self.queue else now if self.started else self.starts_at
        return {**seed_payload(self.rng), 'advances': self.advances,
                'phase': 'timeline' if self.started else 'delay', 'countdown': None,
                'nextIn': max(0, next_at - now)}


class BlinkTracking:
    def __init__(self, rng, config, matched_advance, offset, now):
        self.rng, self.config = rng, config
        self.pokemon = config['mode'] == 'munchlax'
        self.npc = config.get('npc', 0)
        diff = 0 if self.pokemon else max(0, round(now - offset))
        rng.advance(diff * (self.npc + 1))
        self.advances = 0 if self.pokemon else (matched_advance + diff * (self.npc + 1) if matched_advance is not None else 0) + int(config.get('menuClose', True))
        self.initial = {**seed_payload(rng), 'baselineAdvances': self.advances}
        self.next_at = now
        self.pending = False
        self.countdown = None
        self.countdown_zero_at = None
        self.timeline = None
        if config['mode'] == 'reidentify' and config.get('noisy'):
            self.timeline = TimelineClock(rng, {**config, 'pokemonNpc': 1}, self.advances, now)

    def request_timeline(self):
        if self.pokemon or self.timeline or self.pending:
            return False
        self.pending = True
        return True

    def update(self, now):
        while not self.timeline and self.next_at <= now + 1e-9:
            if self.pokemon:
                self.advances += 1
                self.next_at += self.rng.rangefloat(3, 12) + .285
                continue
            if self.pending:
                if self.countdown is None:
                    self.countdown = 10
                elif self.countdown > 0:
                    self.countdown -= 1
                    if self.countdown == 0:
                        self.countdown_zero_at = self.next_at
                else:
                    self.timeline = TimelineClock(self.rng, self.config, self.advances, self.next_at)
                    break
            self.advances += self.npc + 1
            for _ in range(self.npc + 1):
                self.rng.next()
            self.next_at += 1.018
        if self.timeline:
            return self.timeline.update(now)
        return {**seed_payload(self.rng), 'advances': self.advances,
                'phase': 'munchlax' if self.pokemon else 'countdown' if self.pending else 'tracking',
                'countdown': self.countdown, 'nextIn': max(0, self.next_at - now)}
