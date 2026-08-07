Dropped `--randomly-seed=last` from the CI pytest arguments. The seed was
resolved from `.pytest_cache`, which CI never persists, so each xdist worker
resolved it independently and could disagree on collection order — tripping
xdist's consistency guard and turning main red intermittently.
