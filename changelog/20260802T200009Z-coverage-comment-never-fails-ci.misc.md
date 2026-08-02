Stop a failed coverage PR comment from failing the whole CI workflow: the retry
step now carries `continue-on-error` like the first attempt. Posting a comment
is reporting, not a merge gate, and a persistent failure was leaving every PR's
CI red — which mill's ci_fix answered with an endless stream of no-op commits.
