from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol

from pydantic import BaseModel, Field

from aifpl.artifacts import complete_artifact_paths, json_bytes, verify_artifact, write_immutable, write_manifest
from aifpl.game_state import GameState, RankSnapshot


ChipName = Literal["wildcard", "free_hit", "bench_boost", "triple_captain"]


class AccountSnapshot(BaseModel):
    """Read-only account state imported from the public FPL account endpoints."""

    entry_id: int = Field(gt=0)
    season_id: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{2,31}$")
    gameweek: int = Field(ge=1, le=38)
    overall_rank: int = Field(gt=0)
    target_rank: int = Field(gt=0)
    rank_history: list[RankSnapshot] = Field(default_factory=list)
    free_transfers: int = Field(ge=0, le=5)
    bank: int = Field(ge=0)
    chips_remaining: dict[str, int] = Field(default_factory=dict)
    squad_ids: list[int] = Field(min_length=15, max_length=15)
    starting_xi_ids: list[int] = Field(min_length=11, max_length=11)
    bench_ids: list[int] = Field(min_length=4, max_length=4)
    captain_id: int = Field(gt=0)
    vice_captain_id: int = Field(gt=0)
    active_chip: ChipName | None = None
    source: str = "fpl_public_account"
    fetched_at: datetime
    output_path: str = ""


class AccountClient(Protocol):
    async def fetch_entry_history(self, entry_id: int) -> dict[str, Any]: ...

    async def fetch_entry_picks(self, entry_id: int, event: int) -> dict[str, Any]: ...


async def fetch_and_build_account_state(
    client: AccountClient,
    *,
    entry_id: int,
    season_id: str,
    target_rank: int,
    free_transfers: int | None = None,
    initial_free_transfers: int = 1,
    chips_remaining: Mapping[str, int] | None = None,
    gameweek: int | None = None,
) -> tuple[AccountSnapshot, GameState]:
    history_payload = await client.fetch_entry_history(entry_id)
    selected_gameweek = gameweek or _latest_gameweek(history_payload)
    picks_payload = await client.fetch_entry_picks(entry_id, selected_gameweek)
    return build_account_snapshot(
        history_payload,
        picks_payload,
        entry_id=entry_id,
        season_id=season_id,
        target_rank=target_rank,
        free_transfers=free_transfers,
        initial_free_transfers=initial_free_transfers,
        chips_remaining=chips_remaining,
        gameweek=selected_gameweek,
    )


def build_account_snapshot(
    history_payload: Mapping[str, Any],
    picks_payload: Mapping[str, Any],
    *,
    entry_id: int,
    season_id: str,
    target_rank: int,
    free_transfers: int | None = None,
    initial_free_transfers: int = 1,
    chips_remaining: Mapping[str, int] | None = None,
    gameweek: int | None = None,
    fetched_at: datetime | None = None,
) -> tuple[AccountSnapshot, GameState]:
    if not isinstance(history_payload, Mapping) or not isinstance(picks_payload, Mapping):
        raise ValueError("FPL account history and picks must be JSON objects")
    if chips_remaining is not None and not isinstance(chips_remaining, Mapping):
        raise ValueError("chips_remaining must be a JSON object")
    if entry_id <= 0:
        raise ValueError("entry_id must be positive")
    if target_rank <= 0:
        raise ValueError("target_rank must be positive")
    if free_transfers is not None and not 0 <= free_transfers <= 5:
        raise ValueError("free_transfers must be within 0..5")
    if not 1 <= initial_free_transfers <= 5:
        raise ValueError("initial_free_transfers must be within 1..5")
    current = history_payload.get("current")
    if not isinstance(current, list) or not current:
        raise ValueError("FPL account history must contain a non-empty current collection")
    records = [record for record in current if isinstance(record, Mapping) and _positive_int(record.get("event"))]
    if not records:
        raise ValueError("FPL account history contains no valid gameweek records")
    selected = _select_record(records, gameweek)
    selected_gameweek = _positive_int(selected.get("event"))
    assert selected_gameweek is not None
    if free_transfers is None:
        free_transfers = derive_free_transfers(
            records, selected_gameweek, initial_free_transfers=initial_free_transfers,
        )
    picks = picks_payload.get("picks")
    if not isinstance(picks, list):
        raise ValueError("FPL account picks must contain a picks collection")
    account_history = picks_payload.get("entry_history")
    if isinstance(account_history, Mapping) and account_history.get("event") is not None:
        picks_gameweek = _positive_int(account_history.get("event"))
        if picks_gameweek != selected_gameweek:
            raise ValueError("Account history and picks gameweeks do not match")
    ordered = sorted(
        (pick for pick in picks if isinstance(pick, Mapping)),
        key=lambda pick: _positive_int(pick.get("position")) or 99,
    )
    if len(ordered) != 15:
        raise ValueError("FPL account picks must contain exactly 15 players")
    squad_ids = [_required_id(pick.get("element"), "picks element") for pick in ordered]
    if len(set(squad_ids)) != 15:
        raise ValueError("FPL account picks contain duplicate players")
    starting = [
        pick for pick in ordered
        if (_positive_int(pick.get("position")) or 99) <= 11
    ]
    bench = [
        pick for pick in ordered
        if (_positive_int(pick.get("position")) or 0) > 11
    ]
    if len(starting) != 11 or len(bench) != 4:
        raise ValueError("FPL account picks must contain 11 starters and 4 bench players")
    captain = [pick for pick in starting if bool(pick.get("is_captain"))]
    vice = [pick for pick in starting if bool(pick.get("is_vice_captain"))]
    if len(captain) != 1 or len(vice) != 1:
        raise ValueError("FPL account picks must identify one captain and one vice-captain")
    chips = _validate_chips(
        derive_chips_remaining(history_payload) if chips_remaining is None else chips_remaining,
    )
    fetched = (fetched_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    overall_rank = _required_id(
        selected.get("overall_rank", selected.get("rank")), "overall rank",
    )
    bank = _required_nonnegative_int(selected.get("bank"), "bank")
    rank_history = [
        RankSnapshot(
            gameweek=_required_id(record.get("event"), "rank history gameweek"),
            overall_rank=_required_id(record.get("overall_rank", record.get("rank")), "rank history rank"),
            target_rank=target_rank,
            captured_at=fetched,
        )
        for record in sorted(records, key=lambda item: int(item["event"]))
        if _positive_int(record.get("overall_rank", record.get("rank")))
    ]
    active_chip = picks_payload.get("active_chip")
    if active_chip is not None and active_chip not in {"wildcard", "free_hit", "bench_boost", "triple_captain"}:
        raise ValueError(f"Unsupported active chip: {active_chip}")
    snapshot = AccountSnapshot(
        entry_id=entry_id,
        season_id=season_id,
        gameweek=selected_gameweek,
        overall_rank=overall_rank,
        target_rank=target_rank,
        rank_history=rank_history,
        free_transfers=free_transfers,
        bank=bank,
        chips_remaining=chips,
        squad_ids=squad_ids,
        starting_xi_ids=[_required_id(pick.get("element"), "starter element") for pick in starting],
        bench_ids=[_required_id(pick.get("element"), "bench element") for pick in bench],
        captain_id=_required_id(captain[0].get("element"), "captain element"),
        vice_captain_id=_required_id(vice[0].get("element"), "vice-captain element"),
        active_chip=active_chip,
        fetched_at=fetched,
    )
    state = GameState(
        season_id=season_id,
        account_id=entry_id,
        gameweek=selected_gameweek,
        overall_rank=overall_rank,
        target_rank=target_rank,
        rank_history=rank_history,
        free_transfers=free_transfers,
        bank=bank,
        chips_remaining=chips,
        objective_mode="RANK_MODE",
        source="fpl_public_account",
        updated_at=fetched,
    )
    return snapshot, state


class AccountSnapshotStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def save(self, snapshot: AccountSnapshot) -> Path:
        fetched = snapshot.fetched_at.astimezone(timezone.utc)
        path = self.root / "account" / snapshot.season_id / str(snapshot.entry_id) / (
            f"gw{snapshot.gameweek}.{fetched.strftime('%Y%m%dT%H%M%S%fZ')}.json"
        )
        persisted = snapshot.model_copy(update={"output_path": str(path)})
        write_immutable(path, json_bytes(persisted.model_dump(mode="json"), pretty=True))
        write_manifest(
            self.root,
            path,
            artifact_type="account_snapshot",
            created_at=fetched.isoformat(),
            record_count=len(snapshot.rank_history),
            sources={},
            parameters={
                "entry_id": snapshot.entry_id,
                "season_id": snapshot.season_id,
                "gameweek": snapshot.gameweek,
            },
        )
        return path

    def latest(self, entry_id: int | None = None, season_id: str | None = None) -> AccountSnapshot:
        directory = self.root / "account"
        paths = list(directory.glob("*/*/*.json")) if directory.exists() else []
        paths = [path for path in paths if not path.name.endswith(".manifest.json")]
        if entry_id is not None:
            paths = [path for path in paths if path.parent.name == str(entry_id)]
        if season_id is not None:
            paths = [path for path in paths if path.parent.parent.name == season_id]
        paths = complete_artifact_paths(sorted(paths))
        if not paths:
            raise FileNotFoundError("No account snapshot exists; import an FPL account first")
        path = paths[-1]
        verify_artifact(self.root, path)
        return AccountSnapshot.model_validate_json(path.read_text(encoding="utf-8"))


def _select_record(records: list[Mapping[str, Any]], gameweek: int | None) -> Mapping[str, Any]:
    if gameweek is None:
        return max(records, key=lambda record: int(record["event"]))
    matching = [record for record in records if int(record["event"]) == gameweek]
    if not matching:
        raise ValueError(f"FPL account history has no gameweek {gameweek}")
    return matching[-1]


def derive_free_transfers(
    records: list[Mapping[str, Any]],
    gameweek: int,
    *,
    initial_free_transfers: int = 1,
) -> int:
    """Calculate the transfers available for the next gameweek from FPL history."""
    if not 1 <= initial_free_transfers <= 5:
        raise ValueError("initial_free_transfers must be within 1..5")
    available = initial_free_transfers
    previous_gameweek = 0
    for record in sorted(records, key=lambda item: int(item["event"])):
        event = int(record["event"])
        if event > gameweek:
            break
        if event > previous_gameweek + 1:
            available = min(5, available + event - previous_gameweek - 1)
        transfers = _nonnegative_int(record.get("event_transfers"), "event transfers")
        available = min(5, max(0, available + 1 - transfers))
        previous_gameweek = event
    return available


def derive_chips_remaining(history_payload: Mapping[str, Any]) -> dict[str, int]:
    """Return remaining two-set chips based on the public account chip history."""
    remaining = {name: 2 for name in ("wildcard", "free_hit", "bench_boost", "triple_captain")}
    used = history_payload.get("chips", [])
    if not isinstance(used, list):
        return remaining
    aliases = {
        "freehit": "free_hit",
        "free_hit": "free_hit",
        "benchboost": "bench_boost",
        "bench_boost": "bench_boost",
        "triplecaptain": "triple_captain",
        "triple_captain": "triple_captain",
    }
    for chip in used:
        raw_name = chip.get("name") if isinstance(chip, Mapping) else chip
        if not isinstance(raw_name, str):
            continue
        name = aliases.get(raw_name.casefold(), raw_name.casefold())
        if name in remaining:
            remaining[name] = max(0, remaining[name] - 1)
    return remaining


def _latest_gameweek(history_payload: Mapping[str, Any]) -> int:
    current = history_payload.get("current")
    if not isinstance(current, list):
        raise ValueError("FPL account history must contain a current collection")
    gameweeks = [
        _positive_int(record.get("event"))
        for record in current
        if isinstance(record, Mapping)
    ]
    latest = max((event for event in gameweeks if event is not None), default=None)
    if latest is None:
        raise ValueError("FPL account history contains no valid gameweek records")
    return latest


def _validate_chips(values: Mapping[str, int]) -> dict[str, int]:
    allowed = {"wildcard", "free_hit", "bench_boost", "triple_captain"}
    result: dict[str, int] = {}
    for name, value in values.items():
        if name not in allowed:
            raise ValueError(f"Unsupported chip in remaining-chip input: {name}")
        try:
            count = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Chip count for {name} must be an integer") from exc
        if not 0 <= count <= 2:
            raise ValueError(f"Chip count for {name} must be within 0..2")
        result[name] = count
    return result


def _nonnegative_int(value: object, label: str) -> int:
    if value is None:
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a non-negative integer") from exc
    if number < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return number


def _positive_int(value: object) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _required_id(value: object, label: str) -> int:
    number = _positive_int(value)
    if number is None:
        raise ValueError(f"{label} must be a positive integer")
    return number


def _required_nonnegative_int(value: object, label: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a non-negative integer") from exc
    if number < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return number
