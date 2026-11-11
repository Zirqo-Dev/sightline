"""Per-wallet Jupiter caps. No borrowing. Every lite-api call is charged here."""


class Budget:
    def __init__(self, cfg):
        jup = cfg["jupiter"]
        self.caps = {k: int(v) for k, v in jup["caps"].items()}
        self.total_cap = int(jup["total_cap"])
        if sum(self.caps.values()) != self.total_cap:
            raise SystemExit("jupiter caps do not sum to total_cap")
        self.used = {k: 0 for k in self.caps}

    def charge(self, wallet):
        if wallet not in self.caps:
            return False
        if self.used[wallet] >= self.caps[wallet]:
            return False
        if sum(self.used.values()) >= self.total_cap:
            return False
        self.used[wallet] += 1
        return True

    def snapshot(self):
        out = {k: self.used[k] for k in self.caps}
        out["total"] = sum(self.used.values())
        out["cap"] = self.total_cap
        return out
