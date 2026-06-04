from __future__ import annotations

import argparse
import sys
import time

from .cache import make_search_cache
from .config import load_settings
from .engine_factory import create_search_engine
from .sync import SyncService, make_product_source


class SyncBaselineMissing(RuntimeError):
    pass


def run_once(
    service: SyncService,
    mode: str,
    since: str | None = None,
    auto_since: bool = True,
    allow_full_bootstrap_sync: bool = False,
) -> int:
    if mode == "reindex":
        result = service.reindex_all()
    else:
        result = service.sync_changed(
            resolve_sync_since(
                service,
                since,
                auto_since=auto_since,
                require_baseline=not allow_full_bootstrap_sync,
            )
        )
    print(result.model_dump_json(indent=2), flush=True)
    return 1 if result.failed else 0


def resolve_sync_since(
    service: SyncService,
    explicit_since: str | None = None,
    auto_since: bool = True,
    require_baseline: bool = False,
) -> str | None:
    if explicit_since:
        return explicit_since
    if not auto_since:
        return None
    latest = service.logger.latest_successful_result({"sync", "reindex"})
    if latest is None:
        if require_baseline:
            raise SyncBaselineMissing(
                "sync mode needs a successful sync/reindex baseline or an explicit --since value; "
                "use --allow-full-bootstrap-sync only for an intentional full bootstrap sync"
            )
        return None
    return latest.status.last_started_at


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Haeorum AI product index synchronization.")
    parser.add_argument("--mode", choices=["sync", "reindex"], default="sync")
    parser.add_argument("--since", default=None, help="Optional updated_at lower bound for sync mode.")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit.")
    parser.add_argument("--interval-seconds", type=int, default=None, help="Repeat interval. Defaults to settings.")
    parser.add_argument(
        "--allow-full-bootstrap-sync",
        action="store_true",
        help="Allow sync mode to run without a previous successful sync/reindex baseline.",
    )
    args = parser.parse_args()

    settings = load_settings()
    interval = args.interval_seconds or settings.sync_interval_seconds
    engine = create_search_engine(settings, preload_local_products=False)
    service = SyncService(engine, make_product_source(settings), settings, search_cache=make_search_cache(settings))

    if args.once:
        try:
            return run_once(
                service,
                args.mode,
                args.since,
                allow_full_bootstrap_sync=args.allow_full_bootstrap_sync,
            )
        except SyncBaselineMissing as exc:
            print(str(exc), file=sys.stderr, flush=True)
            return 2

    while True:
        try:
            exit_code = run_once(
                service,
                args.mode,
                args.since,
                allow_full_bootstrap_sync=args.allow_full_bootstrap_sync,
            )
        except SyncBaselineMissing as exc:
            print(str(exc), file=sys.stderr, flush=True)
            exit_code = 2
        if exit_code:
            print("sync cycle failed; next cycle will retry", file=sys.stderr, flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
